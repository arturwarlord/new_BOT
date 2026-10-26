"""Strategy library. Each strategy turns candles + params into engine Signals.

All strategies are causal (bar i uses data up to i) and share an IndicatorCache, so a
parameter grid recomputes only what actually changes."""
from __future__ import annotations

import itertools
from typing import Callable, Dict, List

from . import indicators as ind
from .engine import Signals

NaN = float("nan")
ok = ind.is_valid


class IndicatorCache:
    def __init__(self, c):
        self.c = c
        self._d: Dict[tuple, object] = {}

    def _get(self, key: tuple, fn: Callable):
        if key not in self._d:
            self._d[key] = fn()
        return self._d[key]

    def atr(self, n):
        return self._get(("atr", n), lambda: ind.atr(self.c.high, self.c.low, self.c.close, n))

    def ema(self, n):
        return self._get(("ema", n), lambda: ind.ema(self.c.close, n))

    def sma(self, n):
        return self._get(("sma", n), lambda: ind.sma(self.c.close, n))

    def std(self, n):
        return self._get(("std", n), lambda: ind.rolling_std(self.c.close, n))

    def adx(self, n=14):
        return self._get(("adx", n), lambda: ind.adx(self.c.high, self.c.low, self.c.close, n))

    def rsi(self, n=14):
        return self._get(("rsi", n), lambda: ind.rsi(self.c.close, n))

    def hi(self, n):
        return self._get(("hi", n), lambda: ind.prior_highest(self.c.high, n))

    def lo(self, n):
        return self._get(("lo", n), lambda: ind.prior_lowest(self.c.low, n))

    def roc(self, n):
        return self._get(("roc", n), lambda: ind.roc(self.c.close, n))

    def supertrend(self, n, mult):
        return self._get(("st", n, mult), lambda: ind.supertrend(self.c.high, self.c.low, self.c.close, n, mult))


def _grid(**axes) -> List[dict]:
    keys = list(axes)
    return [dict(zip(keys, v)) for v in itertools.product(*(axes[k] for k in keys))]


class Strategy:
    name = "base"
    description = ""

    def grid(self) -> List[dict]:
        raise NotImplementedError

    def default(self) -> dict:
        raise NotImplementedError

    def signals(self, cache: IndicatorCache, p: dict) -> Signals:
        raise NotImplementedError


class DonchianBreakout(Strategy):
    name = "donchian"
    description = "Trend: breakout of the N-bar channel in the direction of EMA200, exit on the opposite M-bar channel, ATR trailing stop"

    def grid(self):
        return [p for p in _grid(entry_n=[20, 55, 100], exit_n=[10, 20], atr_stop=[2.5, 3.5],
                                 allow_short=[True, False]) if p["exit_n"] < p["entry_n"]]

    def default(self):
        return dict(entry_n=55, exit_n=20, atr_stop=3.0, allow_short=True)

    def signals(self, k, p):
        c = k.c
        n = len(c)
        a, e = k.atr(14), k.ema(200)
        ehi, elo, xhi, xlo = k.hi(p["entry_n"]), k.lo(p["entry_n"]), k.hi(p["exit_n"]), k.lo(p["exit_n"])
        target, stop, event = [0] * n, [NaN] * n, [0] * n
        state = 0
        for i in range(n):
            if not (ok(a[i]) and ok(e[i]) and ok(ehi[i])):
                continue
            x = c.close[i]
            if state == 1 and x < xlo[i] or state == -1 and x > xhi[i]:
                state = 0
            long_sig = x > ehi[i] and x > e[i]
            short_sig = p["allow_short"] and x < elo[i] and x < e[i]
            event[i] = 1 if long_sig else (-1 if short_sig else 0)
            if state == 0 and event[i]:
                state = event[i]
            target[i] = state
            stop[i] = p["atr_stop"] * a[i]
        return Signals(target, stop, trail_dist=stop, entry_event=event)


class EmaTrend(Strategy):
    name = "ema_trend"
    description = "Trend: fast/slow EMA alignment, entries only when ADX confirms a trend, ATR trailing stop"

    def grid(self):
        return [p for p in _grid(fast=[10, 20, 50], slow=[50, 100, 200], adx_min=[0, 20],
                                 allow_short=[True, False]) if p["fast"] < p["slow"]]

    def default(self):
        return dict(fast=20, slow=100, adx_min=20, allow_short=True)

    def signals(self, k, p):
        c = k.c
        n = len(c)
        f, s, a, dx = k.ema(p["fast"]), k.ema(p["slow"]), k.atr(14), k.adx(14)
        target, stop = [0] * n, [NaN] * n
        state = 0
        for i in range(n):
            if not (ok(f[i]) and ok(s[i]) and ok(a[i]) and ok(dx[i])):
                continue
            bull = f[i] > s[i]
            if state == 1 and not bull or state == -1 and bull:
                state = 0
            if state == 0 and dx[i] >= p["adx_min"]:
                state = 1 if bull else (-1 if p["allow_short"] else 0)
            target[i] = state
            stop[i] = 3.0 * a[i]
        return Signals(target, stop, trail_dist=stop)


class Supertrend(Strategy):
    name = "supertrend"
    description = "Trend: Supertrend direction, the stop rides the Supertrend line"

    def grid(self):
        return _grid(atr_n=[10, 14], mult=[2.0, 3.0, 4.0], allow_short=[True, False])

    def default(self):
        return dict(atr_n=10, mult=3.0, allow_short=True)

    def signals(self, k, p):
        c = k.c
        n = len(c)
        line, d = k.supertrend(p["atr_n"], p["mult"])
        a = k.atr(p["atr_n"])
        target, stop = [0] * n, [NaN] * n
        for i in range(n):
            if d[i] == 0 or not ok(line[i]):
                continue
            target[i] = d[i] if (d[i] == 1 or p["allow_short"]) else 0
            stop[i] = max(abs(c.close[i] - line[i]), 0.5 * a[i])
        return Signals(target, stop, trail_dist=stop)


class TimeSeriesMomentum(Strategy):
    name = "tsmom"
    description = "Momentum: sign of the N-bar return confirmed by price vs its N-bar average, wide ATR stop"

    def grid(self):
        return _grid(lookback=[42, 126, 252, 540], atr_stop=[3.0, 5.0], allow_short=[True, False])

    def default(self):
        return dict(lookback=126, atr_stop=4.0, allow_short=True)

    def signals(self, k, p):
        c = k.c
        n = len(c)
        r, m, a = k.roc(p["lookback"]), k.sma(p["lookback"]), k.atr(14)
        target, stop = [0] * n, [NaN] * n
        state = 0
        for i in range(n):
            if not (ok(r[i]) and ok(m[i]) and ok(a[i])):
                continue
            up, down = r[i] > 0 and c.close[i] > m[i], r[i] < 0 and c.close[i] < m[i]
            if state == 1 and down or state == -1 and up:
                state = 0
            if state == 0:
                state = 1 if up else (-1 if down and p["allow_short"] else 0)
            target[i] = state
            stop[i] = p["atr_stop"] * a[i]
        return Signals(target, stop, trail_dist=stop)


class MeanReversion(Strategy):
    name = "mean_reversion"
    description = "Range: Bollinger band + RSI extremes, only while ADX says there is no trend; exit at the mean, time stop"

    def grid(self):
        return _grid(bb_k=[2.0, 2.5], rsi_lo=[25, 30], adx_max=[20, 25], allow_short=[True, False])

    def default(self):
        return dict(bb_k=2.0, rsi_lo=30, adx_max=25, allow_short=True)

    def signals(self, k, p):
        c = k.c
        n = len(c)
        mid, sd, r, dx, a = k.sma(20), k.std(20), k.rsi(14), k.adx(14), k.atr(14)
        target, stop, event = [0] * n, [NaN] * n, [0] * n
        state = 0
        for i in range(n):
            if not (ok(mid[i]) and ok(sd[i]) and ok(r[i]) and ok(dx[i]) and ok(a[i])):
                continue
            x = c.close[i]
            if state == 1 and x >= mid[i] or state == -1 and x <= mid[i]:
                state = 0
            calm = dx[i] < p["adx_max"]
            if calm and x < mid[i] - p["bb_k"] * sd[i] and r[i] < p["rsi_lo"]:
                event[i] = 1
            elif calm and p["allow_short"] and x > mid[i] + p["bb_k"] * sd[i] and r[i] > 100 - p["rsi_lo"]:
                event[i] = -1
            if state == 0 and event[i]:
                state = event[i]
            target[i] = state
            stop[i] = 2.0 * a[i]
        return Signals(target, stop, entry_event=event, max_hold=30)


STRATEGIES: Dict[str, Strategy] = {s.name: s for s in (
    DonchianBreakout(), EmaTrend(), Supertrend(), TimeSeriesMomentum(), MeanReversion())}


def get(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; available: {', '.join(STRATEGIES)}")
    return STRATEGIES[name]


def warmup_bars() -> int:
    """Bars needed before every strategy/param in the library produces signals."""
    return 600
