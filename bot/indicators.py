"""Pure-Python indicators. Every value at index i uses only data up to index i
(or up to i-1 where noted), so there is no look-ahead."""
from __future__ import annotations

from typing import List, Optional

NaN = float("nan")


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


def atr(highs: List[float], lows: List[float], closes: List[float], period: int) -> List[float]:
    """Wilder's ATR."""
    n = len(closes)
    out = [NaN] * n
    if n <= period:
        return out
    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    prev = sum(tr[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def prior_highest(values: List[float], n: int) -> List[float]:
    """Highest of the n bars BEFORE index i (current bar excluded)."""
    out = [NaN] * len(values)
    for i in range(n, len(values)):
        out[i] = max(values[i - n : i])
    return out


def prior_lowest(values: List[float], n: int) -> List[float]:
    out = [NaN] * len(values)
    for i in range(n, len(values)):
        out[i] = min(values[i - n : i])
    return out


def is_valid(x: Optional[float]) -> bool:
    return x is not None and x == x  # NaN != NaN
