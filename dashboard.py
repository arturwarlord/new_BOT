"""Static HTML dashboard: paper account vs. what the backtest expects, plus the whole market analysis.

    python dashboard.py                      # -> site/index.html
    python dashboard.py --record --signals   # also append an equity snapshot and fetch live signals

Reads config/portfolio.json, reports/scan.json, reports/curves.json, state/paper_account.json and state/equity_history.json.
Stdlib only; the page is a single self-contained file (inline CSS + SVG charts, no JavaScript CDN).
"""
import argparse
import datetime as dt
import html
import json
import math
import os
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

DAY_MS = 86_400_000
HORIZONS = (7, 14, 28)
COLORS = ["#4ea1ff", "#f5a623", "#7ed321", "#e94b8a", "#b38cff", "#50e3c2", "#ff6b5b", "#c7c7c7"]


def load(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def esc(x) -> str:
    return html.escape(str(x))


def pct(x: Optional[float], signed: bool = True) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def money(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:+,.2f}".replace(",", " ")


def cls(x: Optional[float]) -> str:
    return "" if not x else ("pos" if x > 0 else "neg")


def utc(ms: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime(fmt)


# ----------------------------------------------------------------------------- paper account

def equity_of(acc: dict) -> float:
    marks = acc.get("marks", {})
    return acc.get("balance", 0.0) + sum((marks.get(s, p["entry"]) - p["entry"]) * p["qty"] * p["side"]
                                         for s, p in acc.get("positions", {}).items())


def record(acc: dict, path: str, now_ms: Optional[int] = None, keep: int = 5000) -> List[dict]:
    """Append an equity snapshot (replacing one taken less than 10 minutes ago)."""
    hist = load(path, [])
    now_ms = now_ms or int(time.time() * 1000)
    snap = {"t": now_ms, "equity": round(equity_of(acc), 4), "balance": round(acc.get("balance", 0.0), 4),
            "open": len(acc.get("positions", {}))}
    if hist and now_ms - hist[-1]["t"] < 10 * 60_000:
        hist[-1] = snap
    else:
        hist.append(snap)
    hist = hist[-keep:]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(hist, f)
    return hist


def trades(acc: dict) -> List[dict]:
    """Pair every close with its open."""
    opens, out = {}, []
    for h in acc.get("history", []):
        if h.get("action") == "open":
            opens[h["symbol"]] = h
        elif h.get("action") == "close":
            o = opens.pop(h["symbol"], {})
            out.append({"symbol": h["symbol"], "side": o.get("side"), "qty": o.get("qty"),
                        "entry": o.get("price"), "exit": h.get("price"), "opened": o.get("t"), "closed": h["t"],
                        "pnl": h.get("pnl", 0.0) - o.get("fee", 0.0), "reason": h.get("reason", "")})
    return out


def max_drawdown(values: Sequence[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, 1 - v / peak)
    return mdd


def account_stats(acc: dict, hist: List[dict], initial: float) -> dict:
    eq = equity_of(acc)
    tr = trades(acc)
    wins = [t["pnl"] for t in tr if t["pnl"] > 0]
    losses = [t["pnl"] for t in tr if t["pnl"] <= 0]
    fees = sum(h.get("fee", 0.0) for h in acc.get("history", []))
    starts = [h["t"] for h in hist] + [h["t"] * 1000 for h in acc.get("history", [])]
    start = min(starts) if starts else None
    return {"equity": eq, "balance": acc.get("balance", initial), "pnl": eq - initial, "ret": eq / initial - 1,
            "unrealized": eq - acc.get("balance", initial), "realized": sum(t["pnl"] for t in tr), "fees": fees,
            "trades": len(tr), "win_rate": len(wins) / len(tr) if tr else None,
            "avg_win": sum(wins) / len(wins) if wins else None,
            "avg_loss": sum(losses) / len(losses) if losses else None,
            "max_dd": max_drawdown([h["equity"] for h in hist] + [eq]),
            "start": start, "days": (time.time() * 1000 - start) / DAY_MS if start else 0.0}


# ----------------------------------------------------------------------------- expectations

def daily_returns(curve: List[list]) -> List[float]:
    v = [p[1] for p in curve]
    return [b / a - 1 for a, b in zip(v, v[1:]) if a > 0]


def expectation(curve: List[list]) -> Optional[dict]:
    """Mean/stdev of daily portfolio returns -> expected return and ~90% range over a horizon."""
    r = daily_returns(curve)
    if len(r) < 20:
        return None
    mu = sum(r) / len(r)
    sd = math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1))
    return {"mu": mu, "sd": sd, "days": len(r),
            "h": {n: (mu * n, mu * n - 1.645 * sd * math.sqrt(n), mu * n + 1.645 * sd * math.sqrt(n))
                  for n in HORIZONS}}


def rolling_windows(curve: List[list], n: int) -> Optional[dict]:
    v = [p[1] for p in curve]
    w = sorted(v[i + n] / v[i] - 1 for i in range(len(v) - n) if v[i] > 0)
    if len(w) < 10:
        return None
    q = lambda f: w[min(len(w) - 1, int(f * len(w)))]  # noqa: E731
    return {"n": len(w), "positive": sum(x > 0 for x in w) / len(w), "p05": q(0.05), "median": q(0.5),
            "p95": q(0.95)}


# ----------------------------------------------------------------------------- svg

def nice_ticks(lo: float, hi: float, k: int = 5) -> List[float]:
    if hi <= lo:
        hi = lo + 1
    step = 10 ** math.floor(math.log10((hi - lo) / k))
    for m in (1, 2, 2.5, 5, 10):
        if (hi - lo) / (step * m) <= k:
            step *= m
            break
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-12:
        out.append(round(t, 10))
        t += step
    return out


def line_chart(series: List[Tuple[str, str, List[list]]], y_fmt=lambda v: f"{v:g}", height: int = 260, width: int = 900,
               band: Optional[Tuple[List[list], List[list]]] = None, baseline: Optional[float] = None) -> str:
    """series: (label, color, [[ts_ms, value], ...]). band: (lower, upper) shaded area."""
    pts = [p for _, _, s in series for p in s] + ([p for b in band for p in b] if band else [])
    if len(pts) < 2:
        return '<div class="empty">Пока недостаточно данных для графика</div>'
    W, H, L, R, T, B = width, height, 64, 12, 10, 26
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    if baseline is not None:
        y0, y1 = min(y0, baseline), max(y1, baseline)
    pad = (y1 - y0) * 0.05 or abs(y1) * 0.01 or 1
    y0, y1 = y0 - pad, y1 + pad
    x1 = x1 if x1 > x0 else x0 + DAY_MS
    sx = lambda x: L + (x - x0) / (x1 - x0) * (W - L - R)  # noqa: E731
    sy = lambda y: T + (y1 - y) / (y1 - y0) * (H - T - B)  # noqa: E731
    g = [f'<svg viewBox="0 0 {W} {H}" class="chart" preserveAspectRatio="none" role="img">']
    for t in nice_ticks(y0, y1):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" class="grid"/>'
                 f'<text x="{L - 6}" y="{sy(t) + 4:.1f}" class="yl">{esc(y_fmt(t))}</text>')
    span = (x1 - x0) / DAY_MS
    fmt = "%d.%m %H:%M" if span < 3 else ("%d.%m" if span < 200 else "%m.%Y")
    n_x = 6 if W >= 700 else 4
    for i in range(n_x):
        x = x0 + (x1 - x0) * i / (n_x - 1)
        g.append(f'<text x="{sx(x):.1f}" y="{H - 6}" class="xl">{utc(x, fmt)}</text>')
    if baseline is not None:
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(baseline):.1f}" y2="{sy(baseline):.1f}" class="base"/>')
    if band:
        lo, hi = band
        poly = [f"{sx(x):.1f},{sy(y):.1f}" for x, y in hi] + [f"{sx(x):.1f},{sy(y):.1f}" for x, y in reversed(lo)]
        g.append(f'<polygon points="{" ".join(poly)}" class="band"/>')
    for label, color, s in series:
        if not s:
            continue
        d = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in s)
        dash = ' stroke-dasharray="6 4"' if label.startswith("~") else ""
        g.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"{dash}>'
                 f'<title>{esc(label.lstrip("~"))}: {esc(y_fmt(s[-1][1]))}</title></polyline>')
        if len(s) == 1:
            g.append(f'<circle cx="{sx(s[0][0]):.1f}" cy="{sy(s[0][1]):.1f}" r="4" fill="{color}"/>')
    g.append("</svg>")
    legend = "".join(f'<span><i style="background:{c}"></i>{esc(lbl.lstrip("~"))}</span>' for lbl, c, s in series if s)
    return "".join(g) + f'<div class="legend">{legend}</div>'


# ----------------------------------------------------------------------------- page

CSS = """
:root{--bg:#0f1420;--card:#171e2e;--line:#273046;--fg:#e6e9ef;--mut:#8b95a8;--pos:#3ecf8e;--neg:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:20px 24px 8px}h1{margin:0;font-size:22px}h2{font-size:17px;margin:0 0 12px}h3{font-size:14px;margin:16px 0 8px;color:var(--mut)}
nav{display:flex;gap:14px;flex-wrap:wrap;padding:0 24px 8px;position:sticky;top:0;background:var(--bg);z-index:2;border-bottom:1px solid var(--line)}
nav a{color:var(--mut);text-decoration:none;padding:8px 0}nav a:hover{color:var(--fg)}
main{padding:16px 24px 40px;max-width:1280px}section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:0 0 18px}
.mut{color:var(--mut)}.pos{color:var(--pos)}.neg{color:var(--neg)}.warn{background:#3a2d12;border-color:#6b5420}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:6px}
.kpi{background:#1d2538;border-radius:8px;padding:10px 12px}.kpi b{display:block;font-size:20px;margin-top:2px}.kpi span{color:var(--mut);font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child,td.l,th.l{text-align:left}th{color:var(--mut);font-weight:500;position:sticky;top:38px;background:var(--card)}
.scroll{overflow-x:auto;max-height:560px;overflow-y:auto}
.chart{width:100%;height:auto;display:block}.chart .grid{stroke:var(--line)}.chart .base{stroke:#5b6680;stroke-dasharray:3 3}
.chart text{fill:var(--mut);font-size:11px}.chart .yl{text-anchor:end}.chart .xl{text-anchor:middle}.chart .band{fill:#4ea1ff22}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--mut);font-size:12px;margin-top:4px}.legend i{display:inline-block;width:12px;height:3px;margin-right:6px;vertical-align:middle}
.empty{color:var(--mut);padding:30px;text-align:center;border:1px dashed var(--line);border-radius:8px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:18px}
.tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:12px;background:#273046}.tag.long{background:#1d4d3a}.tag.short{background:#5a2730}
.heat td{text-align:center}code{color:#c3cbe0;font-size:12px}
"""


def kpi(label: str, value: str, klass: str = "", sub: str = "") -> str:
    return f'<div class="kpi"><span>{esc(label)}</span><b class="{klass}">{value}</b><span>{sub}</span></div>'


def side_tag(side) -> str:
    s = {1: "long", -1: "short", "long": "long", "short": "short"}.get(side, "flat")
    return f'<span class="tag {s}">{s}</span>'


def table(head: List[str], rows: List[List[str]], scroll: bool = False) -> str:
    h = "".join(f"<th>{esc(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    t = f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"
    return f'<div class="scroll">{t}</div>' if scroll else t


def build(pf: dict, scan: dict, curves: dict, acc: dict, hist: List[dict], signals: Optional[List[dict]] = None,
          initial: float = 10_000.0, now_ms: Optional[int] = None) -> str:
    now_ms = now_ms or int(time.time() * 1000)
    holdout = pf.get("holdout_check") or {}
    full = pf.get("backtest_portfolio_oos") or {}
    sleeves = pf.get("sleeves", [])
    st = account_stats(acc, hist, initial)
    exp_h = expectation(curves.get("holdout", []))
    exp_f = expectation(curves.get("portfolio_oos", []))
    P = []

    # ---------------- paper account
    P.append('<section id="paper"><h2>Бумажный счёт (симуляция, реальных ордеров нет)</h2><div class="kpis">')
    P.append(kpi("Капитал", f"{st['equity']:,.2f}".replace(",", " "), "", f"старт {initial:,.0f} USDT".replace(",", " ")))
    P.append(kpi("Прибыль / убыток", money(st["pnl"]), cls(st["pnl"]), pct(st["ret"])))
    P.append(kpi("Закрытые сделки", money(st["realized"]), cls(st["realized"]), f"{st['trades']} сделок"))
    P.append(kpi("Открытые позиции", money(st["unrealized"]), cls(st["unrealized"]),
                 f"{len(acc.get('positions', {}))} позиций"))
    P.append(kpi("Доля прибыльных", pct(st["win_rate"], False), "",
                 f"ср. плюс {money(st['avg_win'])} / минус {money(st['avg_loss'])}"))
    P.append(kpi("Макс. просадка", pct(st["max_dd"], False), "", f"комиссии {st['fees']:.2f}"))
    P.append(kpi("Работает", f"{st['days']:.1f} дн.", "", f"с {utc(st['start'])} UTC" if st["start"] else "ещё не запускался"))
    P.append("</div>")

    # paper vs expected
    eq_pts = [[h["t"], h["equity"]] for h in hist]
    if st["start"] and (not eq_pts or eq_pts[-1][0] < now_ms - 60_000):
        eq_pts.append([now_ms, st["equity"]])
    band, exp_line = None, []
    if exp_h and st["start"]:
        t0 = min([st["start"]] + [p[0] for p in eq_pts])
        horizon = max(28.0, (now_ms - t0) / DAY_MS + 3)
        steps = [i * horizon / 60 for i in range(61)]
        exp_line = [[t0 + d * DAY_MS, initial * (1 + exp_h["mu"]) ** d] for d in steps]
        lo = [[t0 + d * DAY_MS, initial * (1 + exp_h["mu"] * d - 1.645 * exp_h["sd"] * math.sqrt(d))] for d in steps]
        hi = [[t0 + d * DAY_MS, initial * (1 + exp_h["mu"] * d + 1.645 * exp_h["sd"] * math.sqrt(d))] for d in steps]
        band = (lo, hi)
    P.append("<h3>Факт против ожидания (по отложенной проверке бэктеста)</h3>")
    P.append(line_chart([("Бумажный счёт", "#f5a623", eq_pts), ("~Ожидание (среднее)", "#4ea1ff", exp_line)],
                        lambda v: f"{v:,.0f}".replace(",", " "), band=band, baseline=initial))
    P.append('<p class="mut">Синяя зона — примерно 90% диапазон, в котором должен находиться счёт, если рынок '
             'ведёт себя как в последние 180 дней бэктеста. Выход вниз за зону 2–4 недели подряд — повод '
             'перепроверить стратегии, а не наращивать риск.</p>')

    # positions
    pos_rows = []
    by_sym = {s["symbol"]: s for s in sleeves}
    for sym, p in acc.get("positions", {}).items():
        m = acc.get("marks", {}).get(sym, p["entry"])
        u = (m - p["entry"]) * p["qty"] * p["side"]
        risk = None if p.get("stop") is None else (p["stop"] - m) * p["qty"] * p["side"]
        pos_rows.append([esc(sym), esc(by_sym.get(sym, {}).get("strategy", "—")), side_tag(p["side"]), f"{p['qty']:g}",
                         f"{p['entry']:.6g}", f"{m:.6g}", f"{abs(p['qty'] * m):,.0f}".replace(",", " "),
                         f'<span class="{cls(u)}">{money(u)}</span>',
                         f'<span class="{cls(u)}">{pct((m / p["entry"] - 1) * p["side"])}</span>',
                         "—" if p.get("stop") is None else f"{p['stop']:.6g}",
                         f'<span class="{cls(risk)}">{money(risk)}</span>'])
    P.append("<h3>Открытые позиции</h3>")
    P.append(table(["Монета", "Стратегия", "Сторона", "Кол-во", "Вход", "Цена", "Объём $", "P/L", "P/L %", "Стоп",
                    "P/L если стоп"], pos_rows) if pos_rows else '<div class="empty">Открытых позиций нет</div>')

    tr = trades(acc)
    P.append("<h3>Закрытые сделки</h3>")
    if tr:
        rows = [[utc(t["closed"] * 1000), esc(t["symbol"]), side_tag(t["side"]), "—" if t["entry"] is None else f"{t['entry']:.6g}",
                 f"{t['exit']:.6g}", f'<span class="{cls(t["pnl"])}">{money(t["pnl"])}</span>', esc(t["reason"])]
                for t in reversed(tr[-100:])]
        P.append(table(["Закрыта (UTC)", "Монета", "Сторона", "Вход", "Выход", "P/L", "Причина"], rows, scroll=True))
        per: Dict[str, List[float]] = {}
        for t in tr:
            per.setdefault(t["symbol"], []).append(t["pnl"])
        P.append("<h3>Итог по монетам</h3>")
        P.append(table(["Монета", "Сделок", "Прибыльных", "P/L"],
                       [[esc(s), str(len(v)), pct(sum(x > 0 for x in v) / len(v), False),
                         f'<span class="{cls(sum(v))}">{money(sum(v))}</span>'] for s, v in sorted(per.items())]))
    else:
        P.append('<div class="empty">Закрытых сделок пока нет</div>')
    P.append("</section>")

    # ---------------- expectations
    P.append('<section id="expect"><h2>Предполагаемая прибыль и убыток</h2>')
    rows = []
    for n in HORIZONS:
        r = [f"{n} дней"]
        if exp_h:
            m, lo, hi = exp_h["h"][n]
            r += [f'<span class="{cls(m)}">{pct(m)}</span>', f'{pct(lo)} … {pct(hi)}',
                  f'{money(initial * lo)} … {money(initial * hi)}']
        else:
            r += ["—"] * 3
        w = rolling_windows(curves.get("portfolio_oos", []), n)
        r += [pct(w["positive"], False), f'{pct(w["p05"])} / {pct(w["median"])} / {pct(w["p95"])}'] if w else ["—", "—"]
        rows.append(r)
    P.append(table(["Срок", "Ожидание", "~90% диапазон", f"В USDT при {initial:,.0f}".replace(",", " "),
                    "Плюсовых периодов (история)", "Худшие 5% / медиана / лучшие 5% (история)"], rows))
    P.append('<p class="mut">«Ожидание» и диапазон посчитаны по дневным колебаниям портфеля на <b>отложенных 180 днях</b> '
             '(их не использовали при выборе). Колонки «история» — все отрезки такой длины во всём out-of-sample бэктесте; '
             'они оптимистичнее, потому что портфель выбран как лучший. Это оценка, а не обещание.</p>')
    if exp_h and exp_f:
        P.append(f'<p class="mut">Средний дневной результат: отложенная проверка {pct(exp_h["mu"])} '
                 f'(σ {pct(exp_h["sd"], False)}), вся история {pct(exp_f["mu"])} (σ {pct(exp_f["sd"], False)}).</p>')
    P.append("</section>")

    # ---------------- signals
    if signals is not None:
        P.append('<section id="signals"><h2>Сигналы сейчас</h2>')
        rows = [[esc(s["symbol"]), esc(s["strategy"]), side_tag(s["target"]), f"{s['last_close']:.6g}",
                 "—" if not s.get("stop_if_entering_now") else f"{s['stop_if_entering_now']:.6g}",
                 esc(s["last_candle_utc"][:16].replace("T", " ")), f"<code>{esc(json.dumps(s['params']))}</code>"]
                for s in signals]
        P.append(table(["Монета", "Стратегия", "Хочет", "Цена", "Стоп при входе", "Свеча (UTC)", "Параметры"], rows)
                 if rows else '<div class="empty">Нет данных</div>')
        P.append("</section>")

    # ---------------- portfolio + backtest
    P.append('<section id="portfolio"><h2>Выбранный портфель</h2>')
    P.append(f'<p class="mut">Анализ от {esc(scan.get("generated_at", pf.get("generated_at", "—")))}. '
             f'Риск {pf.get("risk", {}).get("risk_pct", 0) * 100:.1f}% капитала на сделку, плечо позиции ≤ '
             f'{pf.get("risk", {}).get("max_leverage", "—")}×, суммарное ≤ {pf.get("limits", {}).get("max_total_leverage", "—")}×, '
             f'стоп на день при просадке {pf.get("limits", {}).get("max_daily_loss", 0) * 100:.0f}%.</p>')
    rows = []
    for s in sleeves:
        o = s.get("oos", {})
        label = s["strategy"] + (" (auto)" if s.get("selected_as") == "auto" else "")
        rows.append([esc(s["symbol"]), esc(label), f"<code>{esc(json.dumps(s.get('params', {})))}</code>",
                     f'<span class="{cls(o.get("oos_return"))}">{pct(o.get("oos_return"))}</span>', pct(o.get("oos_cagr")),
                     pct(o.get("oos_max_drawdown"), False), f'{o.get("oos_sharpe", 0):.2f}', str(o.get("oos_trades", "—")),
                     pct(o.get("positive_folds"), False)])
    P.append(table(["Монета", "Стратегия", "Параметры", "OOS доходность", "CAGR", "Просадка", "Sharpe", "Сделок",
                    "Плюсовых окон"], rows))
    P.append('<div class="grid2">')
    P.append(f'<div><h3>Отложенная проверка: последние 180 дней (с {esc(holdout.get("from", "—"))})</h3><div class="kpis">'
             + kpi("Портфель", pct(holdout.get("return")), cls(holdout.get("return")))
             + kpi("BTC купить и держать", pct(holdout.get("btc_buy_hold")), cls(holdout.get("btc_buy_hold")))
             + kpi("Макс. просадка", pct(holdout.get("max_drawdown"), False))
             + kpi("Sharpe", f'{holdout.get("sharpe") or 0:.2f}') + "</div>"
             + line_chart([("Портфель", "#4ea1ff", curves.get("holdout", [])),
                           ("BTC", "#8b95a8", curves.get("btc_holdout", []))], pct_axis, 300, 540, baseline=1.0)
             + f'<p class="mut">Слоты, выбранные только по данным до отсечки: {esc(", ".join(holdout.get("sleeves", [])) or "—")}</p></div>')
    P.append(f'<div><h3>Весь out-of-sample ({esc(full.get("from", "—"))} → {esc(full.get("to", "—"))})</h3><div class="kpis">'
             + kpi("Портфель", pct(full.get("return")), cls(full.get("return")), f'CAGR {pct(full.get("cagr"))}')
             + kpi("BTC купить и держать", pct(full.get("btc_buy_hold")), cls(full.get("btc_buy_hold")))
             + kpi("Макс. просадка", pct(full.get("max_drawdown"), False))
             + kpi("Sharpe", f'{full.get("sharpe") or 0:.2f}') + "</div>"
             + line_chart([("Портфель", "#4ea1ff", curves.get("portfolio_oos", [])),
                           ("BTC", "#8b95a8", curves.get("btc_oos", []))], pct_axis, 300, 540, baseline=1.0)
             + '<p class="mut">Завышено: портфель выбран как лучший из многих вариантов по этой же истории.</p></div>')
    P.append("</div>")
    sc = curves.get("sleeves", {})
    if sc:
        P.append("<h3>Каждый слот отдельно (out-of-sample, риск 1% на сделку)</h3>")
        P.append(line_chart([(k, COLORS[i % len(COLORS)], v) for i, (k, v) in enumerate(sc.items())],
                            pct_axis, baseline=1.0))
    P.append("</section>")

    # ---------------- market analysis
    ranking = scan.get("ranking", [])
    P.append('<section id="market"><h2>Анализ рынка</h2>')
    strategies = sorted({r["strategy"] for r in ranking})
    best: Dict[str, Dict[str, dict]] = {}
    for r in ranking:
        best.setdefault(r["symbol"], {})[r["strategy"]] = r
    if best:
        P.append("<h3>Sharpe out-of-sample: монета × стратегия (жирным — прошло фильтры)</h3>")
        rows = []
        for sym in sorted(best, key=lambda s: -max(x.get("oos_sharpe") or -9 for x in best[s].values())):
            cells = [esc(sym)]
            for stg in strategies:
                r = best[sym].get(stg)
                if r is None:
                    cells.append("—")
                    continue
                v = r.get("oos_sharpe") or 0.0
                a = max(-1.0, min(1.0, v / 2))
                bg = f"rgba(62,207,142,{a * 0.6:.2f})" if a > 0 else f"rgba(255,107,107,{-a * 0.6:.2f})"
                txt = f"<b>{v:.2f}</b>" if r.get("eligible") else f"{v:.2f}"
                cells.append(f'<span style="display:block;background:{bg};border-radius:4px;padding:2px 0">{txt}</span>')
            rows.append(cells)
        P.append('<div class="heat">' + table(["Монета"] + strategies, rows, scroll=True) + "</div>")
    P.append("<h3>Все пары по Sharpe</h3>")
    rows = [[esc(r["symbol"]), esc(r["strategy"]), f'<span class="{cls(r.get("oos_return"))}">{pct(r.get("oos_return"))}</span>',
             pct(r.get("oos_cagr")), pct(r.get("oos_max_drawdown"), False), f'{r.get("oos_sharpe") or 0:.2f}',
             str(r.get("oos_trades", "")), str(r.get("folds", "")), pct(r.get("positive_folds"), False),
             '<span class="pos">ок</span>' if r.get("eligible") else f'<span class="mut">{esc(r.get("rejected_because", ""))}</span>']
            for r in ranking]
    P.append(table(["Монета", "Стратегия", "Доходность", "CAGR", "Просадка", "Sharpe", "Сделок", "Окон", "Плюсовых",
                    "Статус"], rows, scroll=True) if rows else '<div class="empty">Нет данных анализа</div>')
    prof, uni = scan.get("profiles", {}), {u["symbol"]: u for u in scan.get("universe", [])}
    if prof:
        P.append("<h3>Профиль монет</h3>")
        rows = [[esc(s), f'{uni.get(s, {}).get("volume_24h_usdt", 0) / 1e6:,.0f}'.replace(",", " "),
                 f'{p.get("years", 0):.1f}', pct(p.get("annual_volatility"), False), f'{p.get("avg_adx", 0):.0f}',
                 f'{p.get("adx_now", 0):.0f}', esc(p.get("trend_now", "")),
                 f'<span class="{cls(p.get("return_1y"))}">{pct(p.get("return_1y"))}</span>',
                 f'{(p.get("avg_funding_8h") or 0) * 100:.4f}%']
                for s, p in sorted(prof.items(), key=lambda kv: -uni.get(kv[0], {}).get("volume_24h_usdt", 0))]
        P.append(table(["Монета", "Объём 24ч, $ млн", "Лет истории", "Волатильность/год", "Ср. ADX", "ADX сейчас",
                        "Тренд сейчас", "За 1 год", "Ср. funding 8ч"], rows, scroll=True))
    if scan.get("errors"):
        P.append('<p class="mut">Не удалось загрузить: ' + esc(", ".join(scan["errors"])) + "</p>")
    P.append("</section>")

    nav = [("paper", "Бумажный счёт"), ("expect", "Ожидания"), ("signals", "Сигналы"), ("portfolio", "Портфель и бэктест"),
           ("market", "Анализ рынка")]
    nav = [(a, b) for a, b in nav if a != "signals" or signals is not None]
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>new_BOT — дашборд</title><style>{CSS}</style></head><body>'
            f'<header><h1>new_BOT — дашборд</h1><div class="mut">Обновлено {utc(now_ms)} UTC · режим: paper · '
            f'Bitget USDT-M {esc(pf.get("granularity", "4H"))}</div></header>'
            f'<nav>{"".join(f"<a href=#{a}>{b}</a>" for a, b in nav)}</nav><main>{"".join(P)}'
            f'<section class="warn"><b>Важно.</b> Все цифры бэктеста — история, а не обещание. Бумажный счёт не отправляет '
            f'ордера. Прибыль не гарантирована; к реальным деньгам — только после нескольких недель бумажной и демо-торговли.'
            f'</section></main></body></html>')


def pct_axis(v: float) -> str:
    return f"{(v - 1) * 100:+.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--portfolio", default="config/portfolio.json")
    ap.add_argument("--scan", default="reports/scan.json")
    ap.add_argument("--curves", default="reports/curves.json")
    ap.add_argument("--account", default="state/paper_account.json")
    ap.add_argument("--history", default="state/equity_history.json")
    ap.add_argument("--initial", type=float, default=10_000.0)
    ap.add_argument("--out", default="site/index.html")
    ap.add_argument("--record", action="store_true", help="append an equity snapshot to --history")
    ap.add_argument("--signals", action="store_true", help="fetch current signals (network)")
    a = ap.parse_args()

    pf, scan = load(a.portfolio, {}), load(a.scan, {})
    acc = load(a.account, {"balance": a.initial, "positions": {}, "marks": {}, "history": []})
    hist = record(acc, a.history) if a.record and os.path.exists(a.account) else load(a.history, [])
    signals = None
    if a.signals and pf.get("sleeves"):
        try:
            from bot.trader import describe
            signals = describe(pf)
        except Exception as e:  # dashboard must still render if the exchange is unreachable
            print(f"signals unavailable: {e}", file=sys.stderr)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        f.write(build(pf, scan, load(a.curves, {}), acc, hist, signals, a.initial))
    print(f"Saved {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
