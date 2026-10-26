"""Market scanner: picks the liquid Bitget USDT-M perpetuals, measures their character
(volatility, trendiness, correlation to BTC), runs walk-forward for every strategy on every
symbol and selects a diversified portfolio of (symbol, strategy) sleeves."""
from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import data as D
from . import indicators as ind
from .engine import Costs, Risk, equity_stats
from .walkforward import StrategyWF, walk_forward

STABLE_BASES = {"USDC", "USDT", "FDUSD", "DAI", "TUSD", "USDE", "USD1", "PYUSD", "BUSD", "USDD", "EUR", "USDBRL"}


@dataclass
class Filters:
    min_trades: int = 10
    min_sharpe: float = 0.5
    max_drawdown: float = 0.35
    min_positive_folds: float = 0.5
    min_folds: int = 4
    max_correlation: float = 0.6


def universe(top_n: int = 25, min_volume_usdt: float = 20e6, include_rwa: bool = False) -> List[dict]:
    """Most liquid normal-status perpetuals by 24h USDT volume (stablecoin and, by default,
    tokenised stock/commodity contracts excluded)."""
    contracts = {x["symbol"]: x for x in D.fetch_contracts()}
    rows = []
    for t in D.fetch_tickers():
        info = contracts.get(t["symbol"])
        if not info or info.get("symbolType") != "perpetual" or info.get("symbolStatus") != "normal":
            continue
        if info.get("baseCoin", "").upper() in STABLE_BASES:
            continue
        if not include_rwa and info.get("isRwa") == "YES":
            continue
        vol = float(t.get("usdtVolume") or 0)
        if vol < min_volume_usdt:
            continue
        rows.append({"symbol": t["symbol"], "volume_24h_usdt": vol, "funding_rate": float(t.get("fundingRate") or 0),
                     "last": float(t.get("lastPr") or 0), "max_lever": int(float(info.get("maxLever") or 0))})
    rows.sort(key=lambda r: -r["volume_24h_usdt"])
    return rows[:top_n]


def market_profile(c, bars_per_year: float) -> dict:
    """Character of the market: is it trending or choppy, how volatile, where is it now."""
    n = len(c)
    rets = [math.log(c.close[i] / c.close[i - 1]) for i in range(1, n) if c.close[i - 1] > 0]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)) * math.sqrt(bars_per_year)
    dx = ind.adx(c.high, c.low, c.close, 14)
    er = ind.efficiency_ratio(c.close, 42)
    e200 = ind.ema(c.close, 200)
    valid = lambda xs: [x for x in xs if ind.is_valid(x)]
    last_year = int(bars_per_year)
    return {
        "bars": n,
        "years": round(n / bars_per_year, 2),
        "annual_volatility": vol,
        "avg_adx": sum(valid(dx)) / max(len(valid(dx)), 1),
        "avg_efficiency_ratio": sum(valid(er)) / max(len(valid(er)), 1),
        "adx_now": dx[-1] if ind.is_valid(dx[-1]) else None,
        "trend_now": "up" if c.close[-1] > e200[-1] else "down",
        "return_1y": c.close[-1] / c.close[max(0, n - last_year)] - 1,
    }


def _evaluate(args) -> dict:
    symbol, granularity, days, costs, risk = args
    bpy = D.BARS_PER_YEAR[granularity]
    try:
        c = D.load_or_fetch(symbol, granularity, days)
        try:
            fh = D.fetch_funding_history(symbol)
        except Exception:
            fh = {}
        funding = D.funding_per_bar(c, fh, D.GRANULARITY_MS[granularity])
        prof = market_profile(c, bpy)
        wf = walk_forward(c, bpy, funding=funding, costs=costs, risk=risk)
        return {"symbol": symbol, "profile": prof, "wf": wf, "close_ts": c.ts, "close": c.close,
                "avg_funding_8h": (sum(fh.values()) / len(fh)) if fh else None}
    except Exception as e:  # one broken symbol must not kill the scan
        return {"symbol": symbol, "error": f"{type(e).__name__}: {e}"}


def scan(symbols: Sequence[str], granularity: str = "4H", days: int = 1460, costs: Costs = Costs(),
         risk: Risk = Risk(), workers: int = 4) -> List[dict]:
    jobs = [(s, granularity, days, costs, risk) for s in symbols]
    if workers <= 1:
        return [_evaluate(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_evaluate, jobs))


def _returns_by_ts(wf: StrategyWF, cutoff: Optional[int] = None) -> Dict[int, float]:
    out = {}
    for i in range(1, len(wf.oos_ts)):
        t = wf.oos_ts[i]
        if cutoff is not None and t >= cutoff:
            break
        out[t] = wf.oos_equity[i] / wf.oos_equity[i - 1] - 1
    return out


def _corr(a: Dict[int, float], b: Dict[int, float]) -> float:
    common = [t for t in a if t in b]
    if len(common) < 100:
        return 0.0
    xa, xb = [a[t] for t in common], [b[t] for t in common]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    va = math.sqrt(sum((x - ma) ** 2 for x in xa))
    vb = math.sqrt(sum((y - mb) ** 2 for y in xb))
    return cov / (va * vb) if va > 0 and vb > 0 else 0.0


def rank(results: List[dict], bars_per_year: float, filters: Filters = Filters(),
         cutoff_ts: Optional[int] = None) -> List[dict]:
    """Every (symbol, strategy) pair with its out-of-sample stats, best first, with an eligibility flag."""
    rows = []
    for r in results:
        if "error" in r:
            continue
        for name, wf in r["wf"].items():
            st = wf.stats(bars_per_year, cutoff_ts)
            reasons = []
            if st["oos_trades"] < filters.min_trades:
                reasons.append("few trades")
            if st["oos_return"] <= 0:
                reasons.append("OOS loss")
            if st["oos_sharpe"] < filters.min_sharpe:
                reasons.append("low Sharpe")
            if st["oos_max_drawdown"] > filters.max_drawdown:
                reasons.append("deep drawdown")
            if st["folds"] < filters.min_folds:
                reasons.append("short history")
            if st["positive_folds"] < filters.min_positive_folds:
                reasons.append("unstable folds")
            rows.append({"symbol": r["symbol"], "strategy": name, **st, "eligible": not reasons,
                         "rejected_because": ", ".join(reasons), "latest": wf.latest, "_wf": wf})
    rows.sort(key=lambda x: -x["oos_sharpe"])
    return rows


def select(ranked: List[dict], max_sleeves: int = 5, filters: Filters = Filters(),
           cutoff_ts: Optional[int] = None) -> List[dict]:
    """Greedy: best Sharpe first, one sleeve per symbol, skip anything too correlated with what is
    already chosen, and skip sleeves whose latest training window found no edge."""
    chosen: List[dict] = []
    rets: List[Dict[int, float]] = []
    for row in ranked:
        if len(chosen) >= max_sleeves:
            break
        if not row["eligible"] or row["latest"] is None:
            continue
        if any(c["symbol"] == row["symbol"] for c in chosen):
            continue
        r = _returns_by_ts(row["_wf"], cutoff_ts)
        if any(_corr(r, x) > filters.max_correlation for x in rets):
            continue
        chosen.append(row)
        rets.append(r)
    return chosen


def portfolio_curve(sleeves: List[dict], start_ts: Optional[int] = None,
                    end_ts: Optional[int] = None):
    """Each sleeve risks the same fraction of TOTAL equity, so portfolio bar returns are the sum of
    sleeve bar returns (a sleeve without out-of-sample history yet contributes 0). Returns (ts, equity)."""
    series = [_returns_by_ts(s["_wf"]) for s in sleeves]
    if not series:
        return [], []
    common = sorted(set.union(*(set(x) for x in series)))
    common = [t for t in common if (start_ts is None or t >= start_ts) and (end_ts is None or t < end_ts)]
    eq = [1.0]
    for t in common:
        eq.append(eq[-1] * (1 + sum(x.get(t, 0.0) for x in series)))
    return common, eq


def btc_buy_hold(results: List[dict], ts: List[int]) -> Optional[float]:
    btc = next((r for r in results if r.get("symbol") == "BTCUSDT" and "error" not in r), None)
    if not btc or not ts:
        return None
    idx = {t: i for i, t in enumerate(btc["close_ts"])}
    inside = [t for t in ts if t in idx]
    if len(inside) < 2:
        return None
    return btc["close"][idx[inside[-1]]] / btc["close"][idx[inside[0]]] - 1


def curve_stats(eq: List[float], bars_per_year: float) -> dict:
    total, cagr, mdd, sharpe = equity_stats(eq, bars_per_year)
    return {"return": total, "cagr": cagr, "max_drawdown": mdd, "sharpe": sharpe}
