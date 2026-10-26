"""Market data from Bitget public REST v2 (no API keys needed), CSV cache, synthetic data for tests."""
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
from typing import Dict, List, Optional

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "USDT-FUTURES"
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
DEFAULT_FUNDING_PER_8H = 0.0001  # 0.01%: Bitget's typical base rate, used when history is missing


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


def _get(path: str, params: dict, retries: int = 4) -> dict:
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "new_BOT/0.2"})
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode())
            if payload.get("code") != "00000":
                raise RuntimeError(f"Bitget API error: {payload}")
            return payload
        except Exception as e:  # network hiccups / 429: back off and retry
            last = e
            time.sleep(0.5 * 2 ** attempt)
    raise RuntimeError(f"GET {path} failed after {retries} attempts: {last}")


def fetch_bitget(symbol: str, granularity: str, days: int, product_type: str = PRODUCT_TYPE) -> Candles:
    """Download `days` of CLOSED candles, paging backwards with history-candles."""
    step = GRANULARITY_MS[granularity]
    now = int(time.time() * 1000)
    end = now - (now % step)  # open time of the current (still forming) candle
    first_open = end
    start_limit = end - days * 24 * 3_600_000
    rows: list = []
    page = 0
    while end > start_limit and page < 400:
        page += 1
        payload = _get("/api/v2/mix/market/history-candles", {
            "symbol": symbol, "productType": product_type, "granularity": granularity,
            "endTime": end, "limit": 200,
        })
        data = payload.get("data") or []
        if not data:
            break
        rows.extend(data)
        oldest = min(int(r[0]) for r in data)
        if oldest >= end:
            break
        end = oldest
        time.sleep(0.06)
    if not rows:
        raise RuntimeError(f"no candles for {symbol}")
    c = from_rows(rows)
    keep = [i for i, t in enumerate(c.ts) if start_limit <= t < first_open]
    return c.slice(keep[0], keep[-1] + 1) if keep else c


def load_or_fetch(symbol: str, granularity: str, days: int, cache_dir: str = "data",
                  max_age_s: float = 6 * 3600) -> Candles:
    """Cached download: reuses data/<symbol>_<granularity>.csv when it is fresh and long enough."""
    path = os.path.join(cache_dir, f"{symbol}_{granularity}.csv")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_s:
        c = load_csv(path)
        need = days * 24 * 3_600_000 * 0.97
        if len(c) and c.ts[-1] - c.ts[0] >= need:
            return c
    c = fetch_bitget(symbol, granularity, days)
    save_csv(c, path)
    return c


def fetch_tickers(product_type: str = PRODUCT_TYPE) -> List[dict]:
    return _get("/api/v2/mix/market/tickers", {"productType": product_type}).get("data") or []


def fetch_contracts(product_type: str = PRODUCT_TYPE, symbol: Optional[str] = None) -> List[dict]:
    params = {"productType": product_type}
    if symbol:
        params["symbol"] = symbol
    return _get("/api/v2/mix/market/contracts", params).get("data") or []


def fetch_funding_history(symbol: str, product_type: str = PRODUCT_TYPE, max_pages: int = 20) -> Dict[int, float]:
    """{funding_time_ms: rate}. Bitget only serves the last few months of history."""
    out: Dict[int, float] = {}
    for page in range(1, max_pages + 1):
        data = _get("/api/v2/mix/market/history-fund-rate", {
            "symbol": symbol, "productType": product_type, "pageSize": 100, "pageNo": page,
        }).get("data") or []
        if not data:
            break
        for r in data:
            out[int(r["fundingTime"])] = float(r["fundingRate"])
        time.sleep(0.06)
    return out


def funding_per_bar(c: Candles, history: Dict[int, float], step_ms: int,
                    interval_h: float = 8.0) -> List[float]:
    """Funding rate charged during each bar: sum of rates whose time falls in [ts, ts+step).
    Before the first known funding event, the average historical rate is assumed (or the
    Bitget default 0.01%/8h when nothing is known), so long-history backtests still pay funding."""
    known = sorted(history.items())
    mean = sum(r for _, r in known) / len(known) if known else DEFAULT_FUNDING_PER_8H
    first_known = known[0][0] if known else None
    per_bar_assumed = mean * step_ms / (interval_h * 3_600_000)
    out = [0.0] * len(c)
    j = 0
    for i, t in enumerate(c.ts):
        if first_known is None or t + step_ms <= first_known:
            out[i] = per_bar_assumed
            continue
        while j < len(known) and known[j][0] < t:
            j += 1
        k = j
        s = 0.0
        while k < len(known) and known[k][0] < t + step_ms:
            s += known[k][1]
            k += 1
        out[i] = s
    return out


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
