"""Walk-forward validation: choose parameters on a training window, judge them ONLY on the
next unseen test window, then roll forward. The stitched out-of-sample (OOS) result is the
only number worth looking at; in-sample numbers are expected to look good."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .backtest import Costs, Metrics, Params, compute_metrics, run_backtest, warmup_bars
from .data import Candles

GRID = {
    "entry_n": [20, 40, 55],
    "exit_n": [10, 20],
    "atr_stop": [2.0, 3.0],
}


def candidates(base: Params) -> List[Params]:
    keys = list(GRID)
    out = []
    for vals in itertools.product(*(GRID[k] for k in keys)):
        kw = dict(zip(keys, vals))
        if kw["exit_n"] >= kw["entry_n"]:
            continue
        out.append(Params(**{**base.__dict__, **kw}))
    return out


def score(m: Metrics, min_trades: int = 8) -> float:
    """Return / drawdown, with a penalty for too few trades (little evidence)."""
    if m.trades < min_trades or m.max_drawdown <= 0:
        return -1e9
    return m.total_return / m.max_drawdown


@dataclass
class Fold:
    train: Tuple[int, int]
    test: Tuple[int, int]
    params: Params
    in_sample: Metrics
    oos: Metrics


@dataclass
class WFResult:
    folds: List[Fold]
    oos_compounded_return: float
    oos_worst_drawdown: float
    oos_trades: int
    buy_hold_return: float


def walk_forward(c: Candles, bars_per_year: float, base: Params = Params(), costs: Costs = Costs(),
                 train_bars: int = 2190, test_bars: int = 540) -> Optional[WFResult]:
    """Defaults assume 4H candles: 2190 bars ~ 365 days train, 540 bars ~ 90 days test."""
    warm = max(warmup_bars(p) for p in candidates(base))
    folds: List[Fold] = []
    t = warm
    n = len(c)
    while t + train_bars + test_bars <= n:
        tr_a, tr_b = t, t + train_bars
        te_a, te_b = tr_b, tr_b + test_bars
        best, best_s, best_m = None, -1e18, None
        for p in candidates(base):
            # train slice includes warmup bars before tr_a so indicators are ready on day one
            seg = c.slice(tr_a - warm, tr_b)
            m = compute_metrics(run_backtest(seg, p, costs, start=warm), bars_per_year)
            s = score(m)
            if s > best_s:
                best, best_s, best_m = p, s, m
        if best is None:
            best, best_m = base, compute_metrics(run_backtest(c.slice(tr_a - warm, tr_b), base, costs, start=warm), bars_per_year)
        seg = c.slice(te_a - warm, te_b)
        oos = compute_metrics(run_backtest(seg, best, costs, start=warm), bars_per_year)
        folds.append(Fold((tr_a, tr_b), (te_a, te_b), best, best_m, oos))
        t += test_bars
    if not folds:
        return None
    comp = 1.0
    for f in folds:
        comp *= 1 + f.oos.total_return
    first_test = folds[0].test[0]
    return WFResult(
        folds=folds,
        oos_compounded_return=comp - 1,
        oos_worst_drawdown=max(f.oos.max_drawdown for f in folds),
        oos_trades=sum(f.oos.trades for f in folds),
        buy_hold_return=c.close[folds[-1].test[1] - 1] / c.close[first_test] - 1,
    )
