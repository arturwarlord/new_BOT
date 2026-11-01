"""Run a portfolio in read-only signal, paper, demo, or explicitly armed live mode."""
import argparse
import json
import logging
import os
import sys
import time

from bot import data as D
from bot.broker import BitgetBroker, PaperBroker
from bot.engine import Costs
from bot.trader import Limits, State, cycle, describe, load_portfolio, report


def sleep_until_next_close(granularity: str, delay_s: int) -> None:
    step = D.GRANULARITY_MS[granularity] / 1000
    now = time.time()
    wake = (now // step + 1) * step + delay_s
    logging.getLogger("bot").info("sleeping %.0f min until the next %s close", (wake - now) / 60, granularity)
    time.sleep(max(wake - now, 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["signal", "paper", "demo", "live"], default="signal")
    ap.add_argument("--portfolio", default="config/portfolio.json")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--delay", type=int, default=30, help="seconds after candle close before acting")
    ap.add_argument("--paper-equity", type=float, default=10_000.0)
    ap.add_argument("--paper-account", default="state/paper_account.json",
                    help="paper account file; allows isolated experiments")
    ap.add_argument("--state-file", help="strategy state file; defaults to state/trader_<mode>.json")
    ap.add_argument("--max-total-leverage", type=float, help="default: portfolio.json limits or 3")
    ap.add_argument("--max-daily-loss", type=float, help="default: portfolio.json limits or 0.05")
    ap.add_argument("--i-understand-the-risk", action="store_true")
    a = ap.parse_args()

    os.makedirs("logs", exist_ok=True)
    log = logging.getLogger("bot")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(f"logs/trade_{a.mode}.log")])
    pf = load_portfolio(a.portfolio)
    gran = pf.get("granularity", "4H")

    if a.mode == "signal":
        print(json.dumps(describe(pf), indent=2))
        return 0
    if a.mode == "live" and not a.i_understand_the_risk:
        log.error("live trading needs --i-understand-the-risk (start with --mode paper or --mode demo)")
        return 2

    costs = Costs(**pf.get("costs", {}))
    if a.mode == "paper":
        broker = PaperBroker(a.paper_account, a.paper_equity, costs)
    else:
        broker = BitgetBroker(demo=(a.mode == "demo"))
    state = State(a.state_file or f"state/trader_{a.mode}.json")
    pl = pf.get("limits", {})
    limits = Limits(a.max_total_leverage or pl.get("max_total_leverage", 3.0),
                    a.max_daily_loss or pl.get("max_daily_loss", 0.05))
    log.info("mode=%s broker=%s portfolio=%s sleeves=%s", a.mode, broker.name, a.portfolio if hasattr(a, "portfolio") else "configured",
             ", ".join(f"{s['symbol']}:{s['strategy']}" for s in pf["sleeves"]))

    while True:
        try:
            if isinstance(broker, BitgetBroker):
                broker._positions = None
            events = cycle(pf, broker, state, limits)
            report(events, broker)
            log.info("equity %.2f", broker.equity())
        except Exception as e:
            log.exception("cycle failed: %s", e)
            if not a.loop:
                return 1
        if not a.loop:
            return 0
        sleep_until_next_close(gran, a.delay)


if __name__ == "__main__":
    sys.exit(main())
