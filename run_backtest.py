"""Usage:
    python run_backtest.py --symbol BTCUSDT --granularity 4H --days 1095
    python run_backtest.py --csv data/BTCUSDT_4H.csv
    python run_backtest.py --synthetic            # offline demo, meaningless numbers
"""
import argparse
import json
import os
import sys

from bot.backtest import Costs, Params, buy_and_hold, compute_metrics, run_backtest, warmup_bars
from bot.data import BARS_PER_YEAR, fetch_bitget, load_csv, save_csv, synthetic
from bot.walkforward import walk_forward


def pct(x: float) -> str:
    return f"{x * 100:6.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--granularity", default="4H")
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--csv")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()

    if a.synthetic:
        c, label = synthetic(n=6000, seed=7), "SYNTHETIC"
    elif a.csv:
        c, label = load_csv(a.csv), os.path.basename(a.csv)
    else:
        c = fetch_bitget(a.symbol, a.granularity, a.days)
        label = f"{a.symbol}_{a.granularity}"
        save_csv(c, f"data/{label}.csv")
    bpy = BARS_PER_YEAR.get(a.granularity, 2190)
    print(f"{label}: {len(c)} candles")

    p, costs = Params(), Costs()
    full = compute_metrics(run_backtest(c, p, costs, start=warmup_bars(p)), bpy)
    print("\nFull-period backtest with DEFAULT parameters (not out-of-sample):")
    for k, v in full.as_dict().items():
        print(f"  {k:15s} {v:.4f}")
    print(f"  buy&hold        {buy_and_hold(c, warmup_bars(p)):.4f}")

    wf = walk_forward(c, bpy, p, costs)
    report = {"label": label, "default_params_full_period": full.as_dict()}
    if wf is None:
        print("\nNot enough data for walk-forward (need > ~2800 4H candles).")
    else:
        print(f"\nWalk-forward, {len(wf.folds)} folds (train picks params, test is unseen):")
        for i, f in enumerate(wf.folds, 1):
            print(f"  fold {i}: oos return {pct(f.oos.total_return)}  maxDD {pct(f.oos.max_drawdown)}  "
                  f"trades {f.oos.trades:3d}  params entry={f.params.entry_n} exit={f.params.exit_n} stop={f.params.atr_stop}")
        print(f"\n  OOS compounded return: {pct(wf.oos_compounded_return)}"
              f" | worst fold DD: {pct(wf.oos_worst_drawdown)} | trades: {wf.oos_trades}"
              f" | buy&hold same span: {pct(wf.buy_hold_return)}")
        report["walk_forward"] = {
            "oos_compounded_return": wf.oos_compounded_return,
            "oos_worst_drawdown": wf.oos_worst_drawdown,
            "oos_trades": wf.oos_trades,
            "buy_hold_return": wf.buy_hold_return,
            "folds": [{"oos": f.oos.as_dict(), "params": f.params.__dict__} for f in wf.folds],
        }
    os.makedirs(a.out, exist_ok=True)
    with open(f"{a.out}/{label}.json", "w") as fh:
        json.dump(report, fh, indent=2)
    print("\nReminder: past results do not predict future results; funding rates are not modelled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
