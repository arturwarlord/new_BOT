"""Backtest every strategy on one symbol: full-period run with default params (orientation only)
plus walk-forward out-of-sample results (the number that matters).

    python run_backtest.py --symbol BTCUSDT
    python run_backtest.py --symbol ETHUSDT --strategies donchian tsmom --days 1460
    python run_backtest.py --csv data/BTCUSDT_4H.csv
    python run_backtest.py --synthetic            # offline demo, meaningless numbers
"""
import argparse
import json
import os
import sys

from bot import data as D
from bot import strategies as S
from bot.engine import Costs, Risk, buy_and_hold, compute_metrics, run
from bot.walkforward import walk_forward


def pct(x: float) -> str:
    return f"{x * 100:7.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--granularity", default="4H")
    ap.add_argument("--days", type=int, default=1460)
    ap.add_argument("--strategies", nargs="*", default=list(S.STRATEGIES))
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--max-leverage", type=float, default=2.0)
    ap.add_argument("--csv")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()

    funding = None
    if a.synthetic:
        c, label = D.synthetic(n=8000, seed=7), "SYNTHETIC"
    elif a.csv:
        c, label = D.load_csv(a.csv), os.path.splitext(os.path.basename(a.csv))[0]
    else:
        c = D.load_or_fetch(a.symbol, a.granularity, a.days)
        label = f"{a.symbol}_{a.granularity}"
        try:
            funding = D.funding_per_bar(c, D.fetch_funding_history(a.symbol), D.GRANULARITY_MS[a.granularity])
        except Exception as e:
            print(f"funding history unavailable ({e}); funding not charged")
    bpy = D.BARS_PER_YEAR.get(a.granularity, 2190)
    costs, risk = Costs(), Risk(a.risk, a.max_leverage)
    warm = S.warmup_bars()
    print(f"{label}: {len(c)} candles, buy&hold after warm-up {pct(buy_and_hold(c, warm))}")

    print("\nFull period, DEFAULT params (in-sample orientation, not a test):")
    print(f"  {'strategy':15s} {'return':>8s} {'maxDD':>8s} {'sharpe':>7s} {'trades':>6s} {'win%':>6s} {'PF':>5s}")
    cache = S.IndicatorCache(c)
    full = {}
    for name in a.strategies:
        st = S.get(name)
        m = compute_metrics(run(c, st.signals(cache, st.default()), costs, risk, warm, funding=funding), bpy)
        full[name] = m.as_dict()
        print(f"  {name:15s} {pct(m.total_return)} {pct(m.max_drawdown)} {m.sharpe:7.2f} {m.trades:6d} "
              f"{m.win_rate * 100:5.0f}% {min(m.profit_factor, 99):5.2f}")

    wf = walk_forward(c, bpy, funding=funding, names=a.strategies, costs=costs, risk=risk)
    print("\nWalk-forward OUT-OF-SAMPLE (train 1y -> test next 3m, rolling):")
    print(f"  {'strategy':15s} {'return':>8s} {'CAGR':>8s} {'maxDD':>8s} {'sharpe':>7s} {'trades':>6s} {'+folds':>7s}  params now")
    report = {"label": label, "default_params_full_period": full, "walk_forward": {}}
    for name, r in wf.items():
        s = r.stats(bpy)
        report["walk_forward"][name] = {**s, "latest": r.latest}
        print(f"  {name:15s} {pct(s['oos_return'])} {pct(s['oos_cagr'])} {pct(s['oos_max_drawdown'])} "
              f"{s['oos_sharpe']:7.2f} {s['oos_trades']:6d} {s['positive_folds'] * 100:6.0f}%  {r.latest}")
    os.makedirs(a.out, exist_ok=True)
    with open(f"{a.out}/backtest_{label}.json", "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print("\nPast results do not predict future results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
