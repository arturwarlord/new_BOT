"""Market data: Bitget public candles (no API keys needed), CSV cache, synthetic data for tests."""
from __future__ import annotations

import csv
import json
import math
import os
import random
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List

BASE_URL = "https://api.bitget.com"
# Bitget v2 granularity names for USDT-FUTURES candles
GRANULARITY_MS = {
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1H": 3_600_000,
    "4H": 4 * 3_600_000,
    "6H": 6 * 3_600_000,
    "12H": 12 * 3_600_000,
    "1D": 24 * 3_600_000,
}
BARS_PER_YEAR = {k: (365 * 24 * 3_600_000) / v for k, v in GRANULARITY_MS.items()}


@dataclass
class Candles:
    ts: List[int]
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    volume: List[float]

    def __len__(self) -> int:
        return len(self.ts)

    def slice(self, a: int, b: int) -> "Candles":
        return Candles(self.ts[a:b], self.open[a:b], self.high[a:b], self.low[a:b], self.close[a:b], self.volume[a:b])


def from_rows(rows) -> Candles:
    rows = sorted({int(r[0]): r for r in rows}.values(), key=lambda r: int(r[0]))  # dedupe + sort
    return Candles(
        [int(r[0]) for r in rows],
        [float(r[1]) for r in rows],
        [float(r[2]) for r in rows],
        [float(r[3]) for r in rows],
        [float(r[4]) for r in rows],
        [float(r[5]) for r in rows],
    )


def _get(path: str, params: dict) -> dict:
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "new_BOT/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("code") != "00000":
        raise RuntimeError(f"Bitget API error: {payload}")
    return payload


def fetch_bitget(symbol: str, granularity: str, days: int, product_type: str = "USDT-FUTURES") -> Candles:
    """Download `days` of CLOSED candles, paging backwards with history-candles.

    NOTE: written from the public v2 docs and not testable offline; if Bitget changes
    the endpoint or limits, only this function needs adjusting.
    """
    step = GRANULARITY_MS[granularity]
    now = int(time.time() * 1000)
    end = now - (now % step)  # start of the current (still open) candle
    first_open = end
    start_limit = end - days * 24 * 3_600_000
    rows: list = []
    page = 0
    while end > start_limit and page < 200:
        page += 1
        payload = _get(
            "/api/v2/mix/market/history-candles",
            {
                "symbol": symbol,
                "productType": product_type,
                "granularity": granularity,
                "endTime": end,
                "limit": 200,
            },
        )
        data = payload.get("data") or []
        if not data:
            break
        rows.extend(data)
        oldest = min(int(r[0]) for r in data)
        if oldest >= end:
            break
        end = oldest
        time.sleep(0.12)  # stay well below rate limits
    c = from_rows(rows)
    keep = [i for i, t in enumerate(c.ts) if start_limit <= t < first_open]
    return c.slice(keep[0], keep[-1] + 1) if keep else c


def save_csv(c: Candles, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for i in range(len(c)):
            w.writerow([c.ts[i], c.open[i], c.high[i], c.low[i], c.close[i], c.volume[i]])


def load_csv(path: str) -> Candles:
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r)
        return from_rows(list(r))


def synthetic(n: int = 3000, seed: int = 1, drift: float = 0.0, vol: float = 0.012,
              start_price: float = 100.0, step_ms: int = 4 * 3_600_000) -> Candles:
    """Deterministic random-walk OHLC for tests/demos. NOT evidence of any performance."""
    rng = random.Random(seed)
    price = start_price
    ts, o, h, l, c, v = [], [], [], [], [], []
    t0 = 1_600_000_000_000
    for i in range(n):
        op = price
        ret = drift + vol * rng.gauss(0, 1)
        cl = max(op * math.exp(ret), 0.01)
        wick = abs(rng.gauss(0, vol / 2))
        hi = max(op, cl) * (1 + wick)
        lo = min(op, cl) * (1 - wick)
        ts.append(t0 + i * step_ms)
        o.append(op); h.append(hi); l.append(lo); c.append(cl); v.append(1000.0)
        price = cl
    return Candles(ts, o, h, l, c, v)
