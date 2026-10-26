"""Walk-forward evaluation: parameters (and, for "auto", the strategy itself) are chosen on a
training window and judged on the following unseen test window. Test windows tile the future."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import strategies as S
from .engine import Costs, Risk, Signals, compute_metrics, equity_stats, run

AUTO = "auto"


@dataclass
class Candidate:
    strategy: str
    params: dict
    signals: Signals


@dataclass
class StrategyWF:
    strategy: str
    oos_ts: List[int]
    oos_equity: List[float]          # stitched out-of-sample equity, starts at 1.0
    fold_returns: List[float]
    fold_choices: List[Optional[Tuple[str, dict]]]
    trades: int
    latest: Optional[Tuple[str, dict]]  # choice on the most recent training window (for live trading)
    latest_train_score: float

    def stats(self, bars_per_year: float, cutoff_ts: Optional[int] = None) -> dict:
        ts, eq = self.oos_ts, self.oos_equity
        if cutoff_ts is not None:
            k = next((i for i, t in enumerate(ts) if t >= cutoff_ts), len(ts))
            ts, eq = ts[:k], eq[:k]
        total, cagr, mdd, sharpe = equity_stats(eq, bars_per_year)
        folds = self.fold_returns
        return {
            "oos_return": total, "oos_cagr": cagr, "oos_max_drawdown": mdd, "oos_sharpe": sharpe,
            "oos_trades": self.trades, "folds": len(folds),
            "positive_folds": sum(1 for r in folds if r > 0) / len(folds) if folds else 0.0,
        }


def score(m, min_trades: int) -> float:
    """Training objective: CAGR / max drawdown (floored at 5%), ignoring windows with too few trades."""
    if m.trades < min_trades:
        return -math.inf
    return m.cagr / max(m.max_drawdown, 0.05)


def build_candidates(c, names: Optional[Sequence[str]] = None) -> List[Candidate]:
    cache = S.IndicatorCache(c)
    out = []
    for name in names or list(S.STRATEGIES):
        strat = S.get(name)
        for p in strat.grid():
            out.append(Candidate(name, p, strat.signals(cache, p)))
    return out


def fold_bounds(n: int, warmup: int, train_bars: int, test_bars: int,
                min_last_test: Optional[int] = None) -> List[Tuple[int, int, int]]:
    """(train_start, test_start, test_end) triples; a final shorter test window reaching the last
    bar is kept when it has at least `min_last_test` bars (default: a third of a full window)."""
    min_last_test = test_bars // 3 if min_last_test is None else min_last_test
    out, t = [], warmup
    while t + train_bars + test_bars <= n:
        out.append((t, t + train_bars, t + train_bars + test_bars))
        t += test_bars
    if t + train_bars + min_last_test <= n:
        out.append((t, t + train_bars, n))
    return out


def walk_forward(c, bars_per_year: float, funding: Optional[Sequence[float]] = None,
                 names: Optional[Sequence[str]] = None, costs: Costs = Costs(), risk: Risk = Risk(),
                 train_bars: int = 2190, test_bars: int = 540, min_trades: int = 5,
                 warmup: Optional[int] = None) -> Dict[str, StrategyWF]:
    """Defaults assume 4H candles: train ~1 year, test ~3 months. Returns one entry per strategy plus AUTO."""
    n = len(c)
    warmup = S.warmup_bars() if warmup is None else warmup
    cands = build_candidates(c, names)
    folds = fold_bounds(n, warmup, train_bars, test_bars)
    groups: Dict[str, List[int]] = {}
    for idx, cd in enumerate(cands):
        groups.setdefault(cd.strategy, []).append(idx)
    groups[AUTO] = list(range(len(cands)))

    def best_of(idx_list, scores):
        best = max(idx_list, key=lambda j: scores[j])
        return (best, scores[best]) if scores[best] > 0 else (None, scores[best])

    acc = {g: {"ts": [], "eq": [1.0], "rets": [], "choices": [], "trades": 0} for g in groups}
    for a, b, e in folds:
        scores = [score(compute_metrics(run(c, cd.signals, costs, risk, a, b, funding=funding), bars_per_year),
                        min_trades) for cd in cands]
        oos_cache: Dict[int, object] = {}
        for g, idx_list in groups.items():
            st = acc[g]
            best, _ = best_of(idx_list, scores)
            if best is None:  # nothing had an edge in training: stay flat
                curve, trades, choice = [1.0] * (e - b), 0, None
            else:
                if best not in oos_cache:
                    oos_cache[best] = run(c, cands[best].signals, costs, risk, b, e, funding=funding)
                res = oos_cache[best]
                curve = [v / res.equity[0] for v in res.equity]
                trades, choice = len(res.trades), (cands[best].strategy, cands[best].params)
            base = st["eq"][-1]
            st["eq"].extend(base * v for v in curve)
            st["ts"].extend(c.ts[b:e])
            st["rets"].append(curve[-1] / curve[0] - 1)
            st["choices"].append(choice)
            st["trades"] += trades

    # latest training window -> what to trade from now on
    a = max(warmup, n - train_bars)
    latest_scores = [score(compute_metrics(run(c, cd.signals, costs, risk, a, n, funding=funding), bars_per_year),
                           min_trades) for cd in cands]
    out: Dict[str, StrategyWF] = {}
    for g, idx_list in groups.items():
        st = acc[g]
        best, best_score = best_of(idx_list, latest_scores)
        latest = None if best is None else (cands[best].strategy, cands[best].params)
        ts = ([c.ts[folds[0][1] - 1]] if folds else []) + st["ts"]  # equity starts at 1.0 one bar before OOS
        out[g] = StrategyWF(g, ts, st["eq"], st["rets"], st["choices"], st["trades"], latest, best_score)
    return out
