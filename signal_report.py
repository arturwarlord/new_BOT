"""Read-only signal monitor. Prints the current state of the strategy on the latest closed
candles (open position, stop, and what would be done at the next open). Places NO orders."""
import argparse
import json
import sys

from bot.backtest import Costs, Params, run_backtest, warmup_bars
from bot.data import fetch_bitget


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--granularity", default="4H")
    a = ap.parse_args()
    p = Params()
    c = fetch_bitget(a.symbol, a.granularity, days=120)
    res = run_backtest(c, p, Costs(), start=warmup_bars(p), close_at_end=False)
    pos = res.open_position
    out = {
        "symbol": a.symbol,
        "granularity": a.granularity,
        "last_candle_ts": c.ts[-1],
        "last_close": res.last_close,
        "position": None if pos is None else {"side": "long" if pos.side == 1 else "short",
                                              "entry": pos.entry_price, "stop": pos.stop},
        "next_action": res.pending,
        "note": "Informational only. No orders are placed.",
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
