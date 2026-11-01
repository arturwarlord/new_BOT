"""Compare the exact deployed sleeve set at 1.0% vs 0.5% risk on identical Bitget data.

This is a fixed-parameter historical replay, not a walk-forward or an out-of-sample claim.
The portfolio aggregation intentionally reuses bot.scanner.portfolio_curve so leverage and
daily-loss limits are treated the same way as the scanner's portfolio-level curve.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from types import SimpleNamespace

from bot import data as D
from bot import strategies as S
from bot.engine import Costs, Risk, run, equity_stats
from bot.scanner import portfolio_curve


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_configs(control: dict, experiment: dict) -> None:
    for key in ("granularity", "costs", "limits", "sleeves"):
        if control.get(key) != experiment.get(key):
            raise ValueError(f"Control and V2 configs differ in {key}; refusing a non-like-for-like comparison")
    r0, r1 = control["risk"], experiment["risk"]
    if r0.get("max_leverage") != r1.get("max_leverage"):
        raise ValueError("max_leverage differs between configs")
    if not (0 < r1.get("risk_pct", 0) < r0.get("risk_pct", 0)):
        raise ValueError("V2 risk_pct must be lower than control risk_pct")


def replay(config: dict, market: dict, days: int, initial: float) -> dict:
    gran = config.get("granularity", "4H")
    bpy = D.BARS_PER_YEAR[gran]
    warm = S.warmup_bars()
    risk = Risk(config["risk"]["risk_pct"], config["risk"].get("max_leverage", 2.0))
    costs = Costs(**config.get("costs", {}))
    simulated = []
    total_trades = wins = gross_profit = gross_loss = 0.0

    for sleeve in config["sleeves"]:
        sym = sleeve["symbol"]
        c, funding = market[sym]
        sig = S.get(sleeve["strategy"]).signals(S.IndicatorCache(c), sleeve["params"])
        res = run(c, sig, costs, risk, start=warm, initial_equity=initial, funding=funding)
        # Include a baseline point immediately before the first simulated bar.
        wf = SimpleNamespace(
            oos_ts=[c.ts[warm - 1]] + c.ts[warm:],
            oos_equity=[1.0] + [v / initial for v in res.equity],
            oos_exposure=[0.0] + res.exposure,
        )
        simulated.append({"symbol": sym, "_wf": wf})
        total_trades += len(res.trades)
        wins += sum(t.pnl > 0 for t in res.trades)
        gross_profit += sum(t.pnl for t in res.trades if t.pnl > 0)
        gross_loss += -sum(t.pnl for t in res.trades if t.pnl <= 0)

    ts, eq = portfolio_curve(
        simulated,
        max_total_leverage=config.get("limits", {}).get("max_total_leverage"),
        max_daily_loss=config.get("limits", {}).get("max_daily_loss"),
    )
    total, cagr, mdd, sharpe = equity_stats(eq, bpy)
    step = D.GRANULARITY_MS[gran]
    series = [[(ts[0] - step) if ts else 0, eq[0]]] + [[t, eq[i + 1]] for i, t in enumerate(ts)]
    return {
        "risk_pct": risk.risk_pct,
        "return": total,
        "cagr": cagr,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "trades": int(total_trades),
        "win_rate": wins / total_trades if total_trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (99.0 if gross_profit else 0.0),
        "series": series,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--control", default="config/portfolio.json")
    ap.add_argument("--experiment", default="config/portfolio_v2_experiment.json")
    ap.add_argument("--days", type=int, default=1460)
    ap.add_argument("--initial", type=float, default=10_000.0)
    ap.add_argument("--out", default="reports/v2_comparison.json")
    a = ap.parse_args()

    control, experiment = load_json(a.control), load_json(a.experiment)
    validate_configs(control, experiment)
    gran = control.get("granularity", "4H")
    market = {}
    for sleeve in control["sleeves"]:
        sym = sleeve["symbol"]
        c = D.load_or_fetch(sym, gran, a.days)
        try:
            funding_history = D.fetch_funding_history(sym)
        except Exception as exc:
            print(f"WARNING: funding history unavailable for {sym}: {exc}; using documented fallback", file=sys.stderr)
            funding_history = {}
        funding = D.funding_per_bar(c, funding_history, D.GRANULARITY_MS[gran])
        market[sym] = (c, funding)

    control_result = replay(control, market, a.days, a.initial)
    v2_result = replay(experiment, market, a.days, a.initial)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "granularity": gran,
        "days_requested": a.days,
        "initial_equity_usdt": a.initial,
        "symbols": [s["symbol"] for s in control["sleeves"]],
        "same_market_data": True,
        "same_costs": True,
        "method": "Fixed deployed sleeve parameters; same OHLCV and funding per symbol; portfolio-level exposure, leverage and daily-loss aggregation reused from bot.scanner.portfolio_curve.",
        "caveat": "Historical replay only, not walk-forward/OOS. The existing sleeve aggregation is a portfolio approximation; do not interpret it as a guarantee or live performance.",
        "control": control_result,
        "v2": v2_result,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(json.dumps({k: v for k, v in payload.items() if k not in ("control", "v2")}, indent=2))
    for label, r in (("CONTROL", control_result), ("V2", v2_result)):
        print(f"{label}: risk={r['risk_pct']:.2%} return={r['return']:.2%} "
              f"maxDD={r['max_drawdown']:.2%} Sharpe={r['sharpe']:.2f} "
              f"trades={r['trades']} PF={r['profit_factor']:.2f}")
    print(f"Saved {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
