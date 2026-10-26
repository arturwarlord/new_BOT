"""Pure-Python indicators. The value at index i uses only data up to index i
(or up to i-1 where noted), so there is no look-ahead. Warm-up values are NaN."""
from __future__ import annotations

import math
from collections import deque
from typing import List, Optional, Tuple

NaN = float("nan")


def is_valid(x: Optional[float]) -> bool:
    return x is not None and x == x  # NaN != NaN


def sma(values: List[float], period: int) -> List[float]:
    out = [NaN] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def ema(values: List[float], period: int) -> List[float]:
    out = [NaN] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rolling_std(values: List[float], period: int) -> List[float]:
    out = [NaN] * len(values)
    s = sq = 0.0
    for i, v in enumerate(values):
        s += v
        sq += v * v
        if i >= period:
            old = values[i - period]
            s -= old
            sq -= old * old
        if i >= period - 1:
            mean = s / period
            out[i] = math.sqrt(max(sq / period - mean * mean, 0.0))
    return out


def true_range(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    tr = [highs[0] - lows[0]] if closes else []
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return tr


def _wilder(values: List[float], period: int, first: int) -> List[float]:
    """Wilder smoothing of values[first:], seeded with the simple mean of the first `period` values."""
    n = len(values)
    out = [NaN] * n
    if n < first + period:
        return out
    prev = sum(values[first : first + period]) / period
    out[first + period - 1] = prev
    for i in range(first + period, n):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def atr(highs: List[float], lows: List[float], closes: List[float], period: int) -> List[float]:
    """Wilder's ATR."""
    return _wilder(true_range(highs, lows, closes), period, 1)


def rsi(closes: List[float], period: int) -> List[float]:
    n = len(closes)
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    ag = _wilder(gains, period, 1)
    al = _wilder(losses, period, 1)
    out = [NaN] * n
    for i in range(n):
        if is_valid(ag[i]) and is_valid(al[i]):
            out[i] = 100.0 if al[i] == 0 else 100.0 - 100.0 / (1.0 + ag[i] / al[i])
    return out


def adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Wilder's ADX (trend strength, 0..100, direction-agnostic)."""
    n = len(closes)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr = true_range(highs, lows, closes)
    s_tr = _wilder(tr, period, 1)
    s_p = _wilder(plus_dm, period, 1)
    s_m = _wilder(minus_dm, period, 1)
    dx = [NaN] * n
    for i in range(n):
        if is_valid(s_tr[i]) and s_tr[i] > 0:
            pdi = 100 * s_p[i] / s_tr[i]
            mdi = 100 * s_m[i] / s_tr[i]
            dx[i] = 0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi)
    first = next((i for i, v in enumerate(dx) if is_valid(v)), n)
    return _wilder(dx, period, first) if first < n else [NaN] * n


def _prior_extreme(values: List[float], n: int, better) -> List[float]:
    """Extreme of the n bars BEFORE index i (current bar excluded), O(len) with a monotonic deque."""
    out = [NaN] * len(values)
    dq: deque = deque()
    for i in range(len(values)):
        if i >= n:
            while dq and dq[0] < i - n:
                dq.popleft()
            out[i] = values[dq[0]]
        while dq and not better(values[dq[-1]], values[i]):
            dq.pop()
        dq.append(i)
    return out


def prior_highest(values: List[float], n: int) -> List[float]:
    """Highest of the n bars BEFORE index i (current bar excluded)."""
    return _prior_extreme(values, n, lambda kept, new: kept > new)


def prior_lowest(values: List[float], n: int) -> List[float]:
    return _prior_extreme(values, n, lambda kept, new: kept < new)


def roc(values: List[float], n: int) -> List[float]:
    """Rate of change over n bars: values[i] / values[i-n] - 1."""
    out = [NaN] * len(values)
    for i in range(n, len(values)):
        if values[i - n] > 0:
            out[i] = values[i] / values[i - n] - 1
    return out


def supertrend(highs: List[float], lows: List[float], closes: List[float], period: int,
               mult: float) -> Tuple[List[float], List[int]]:
    """Classic Supertrend. Returns (line, direction) where direction is +1 (up) / -1 (down) / 0 (warm-up)."""
    n = len(closes)
    a = atr(highs, lows, closes, period)
    line = [NaN] * n
    direction = [0] * n
    fu = fl = NaN
    d = 0
    for i in range(n):
        if not is_valid(a[i]):
            continue
        mid = (highs[i] + lows[i]) / 2
        bu, bl = mid + mult * a[i], mid - mult * a[i]
        if d == 0:
            fu, fl, d = bu, bl, 1 if closes[i] >= mid else -1
        else:
            fu = bu if (bu < fu or closes[i - 1] > fu) else fu
            fl = bl if (bl > fl or closes[i - 1] < fl) else fl
            if d == 1 and closes[i] < fl:
                d = -1
            elif d == -1 and closes[i] > fu:
                d = 1
        direction[i] = d
        line[i] = fl if d == 1 else fu
    return line, direction


def efficiency_ratio(values: List[float], n: int) -> List[float]:
    """Kaufman efficiency ratio: |net move| / sum(|moves|) over n bars. 1 = perfect trend, 0 = noise."""
    out = [NaN] * len(values)
    moves = [0.0] + [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    s = 0.0
    for i in range(len(values)):
        s += moves[i]
        if i > n:
            s -= moves[i - n]
        if i >= n and s > 0:
            out[i] = abs(values[i] - values[i - n]) / s
    return out
