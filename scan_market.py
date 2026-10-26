"""Analyse the market and choose what to trade.

    python scan_market.py                       # top-30 liquid perpetuals, 4 years of 4H candles
    python scan_market.py --symbols BTCUSDT ETHUSDT SOLUSDT
    python scan_market.py --top 40 --max-sleeves 6

Writes reports/MARKET_ANALYSIS.md, reports/scan.json and config/portfolio.json (used by trade.py).
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

from bot import data as D
from bot.engine import Costs, Risk
from bot.scanner import (Filters, btc_buy_hold, curve_stats, portfolio_curve, rank, scan, select,
                         universe)
from bot.strategies import STRATEGIES


def pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def day(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def sleeve_json(row: dict, weight: float) -> dict:
    strategy, params = row["latest"]
    return {
        "symbol": row["symbol"], "strategy": strategy, "params": params, "weight": weight,
        "selected_as": row["strategy"],
        "oos": {k: row[k] for k in ("oos_return", "oos_cagr", "oos_max_drawdown", "oos_sharpe",
                                    "oos_trades", "folds", "positive_folds")},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-volume", type=float, default=5e6, help="min 24h USDT volume")
    ap.add_argument("--include-rwa", action="store_true", help="also scan tokenised stocks/commodities")
    ap.add_argument("--granularity", default="4H")
    ap.add_argument("--days", type=int, default=1460)
    ap.add_argument("--holdout-days", type=int, default=180)
    ap.add_argument("--max-sleeves", type=int, default=6)
    ap.add_argument("--risk", type=float, default=0.01, help="equity risked per trade per sleeve")
    ap.add_argument("--max-leverage", type=float, default=2.0, help="per-sleeve notional cap")
    ap.add_argument("--max-total-leverage", type=float, default=3.0, help="same limit as trade.py")
    ap.add_argument("--max-daily-loss", type=float, default=0.05, help="same limit as trade.py")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="reports")
    ap.add_argument("--portfolio", default="config/portfolio.json")
    a = ap.parse_args()

    t0 = time.time()
    bpy = D.BARS_PER_YEAR[a.granularity]
    costs, risk, filters = Costs(), Risk(a.risk, a.max_leverage), Filters()

    if a.symbols:
        uni = [{"symbol": s, "volume_24h_usdt": None, "funding_rate": None} for s in a.symbols]
    else:
        uni = universe(a.top, a.min_volume, a.include_rwa)
    print(f"Universe: {len(uni)} symbols: {' '.join(u['symbol'] for u in uni)}")
    results = scan([u["symbol"] for u in uni], a.granularity, a.days, costs, risk, a.workers)
    for r in results:
        if "error" in r:
            print(f"  ! {r['symbol']}: {r['error']}")
    good = [r for r in results if "error" not in r and r["wf"]["auto"].fold_returns]
    if not good:
        print("No symbol had enough history for walk-forward.")
        return 1

    # Honest check: choose the portfolio using only data before the holdout, then look at the holdout.
    last_ts = max(r["close_ts"][-1] for r in good)
    cutoff = last_ts - a.holdout_days * 24 * 3_600_000
    pre_sel = select(rank(good, bpy, filters, cutoff), a.max_sleeves, filters, cutoff)
    lim = {"max_total_leverage": a.max_total_leverage, "max_daily_loss": a.max_daily_loss}
    hts, heq = portfolio_curve(pre_sel, start_ts=cutoff, **lim)
    holdout = {"from": day(cutoff), "sleeves": [f"{s['symbol']}:{s['strategy']}" for s in pre_sel],
               **curve_stats(heq, bpy), "btc_buy_hold": btc_buy_hold(good, hts)}

    ranked = rank(good, bpy, filters)
    final = select(ranked, a.max_sleeves, filters)
    fts, feq = portfolio_curve(final, **lim)
    full = {**curve_stats(feq, bpy), "from": day(fts[0]) if fts else None, "to": day(fts[-1]) if fts else None,
            "btc_buy_hold": btc_buy_hold(good, fts)}

    # ---- outputs
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(os.path.dirname(a.portfolio) or ".", exist_ok=True)
    weight = round(1 / len(final), 4) if final else 0
    portfolio = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "granularity": a.granularity,
        "risk": {"risk_pct": risk.risk_pct, "max_leverage": risk.max_leverage},
        "limits": lim,
        "costs": {"fee_rate": costs.fee_rate, "slippage_rate": costs.slippage_rate},
        "sleeves": [sleeve_json(r, weight) for r in final],
        "backtest_portfolio_oos": full,
        "holdout_check": holdout,
    }
    with open(a.portfolio, "w") as f:
        json.dump(portfolio, f, indent=2)

    prof = {r["symbol"]: r for r in good}
    vol = {u["symbol"]: u for u in uni}
    scan_json = {
        "generated_at": portfolio["generated_at"],
        "universe": uni,
        "profiles": {s: {**r["profile"], "avg_funding_8h": r["avg_funding_8h"]} for s, r in prof.items()},
        "ranking": [{k: v for k, v in row.items() if k != "_wf"} for row in ranked],
        "errors": {r["symbol"]: r["error"] for r in results if "error" in r},
    }
    with open(os.path.join(a.out, "scan.json"), "w") as f:
        json.dump(scan_json, f, indent=2, default=str)

    L = []
    L.append(f"# Анализ рынка Bitget USDT-M ({a.granularity})\n")
    L.append(f"Сгенерировано: {portfolio['generated_at']}. История: до {a.days} дней. "
             f"Комиссия {costs.fee_rate*100:.2f}% + проскальзывание {costs.slippage_rate*100:.2f}% на сторону, funding учтён. "
             f"Риск {risk.risk_pct*100:.1f}% капитала на сделку в каждом слоте, плечо слота ≤ {risk.max_leverage:g}×; "
             f"портфель считается с лимитами живого бота: суммарное плечо ≤ {a.max_total_leverage:g}×, "
             f"стоп торговли на день после просадки {a.max_daily_loss:.0%}.\n")
    L.append("Все цифры ниже — **out-of-sample** (walk-forward: параметры подбираются на 1 году, проверяются на следующих 3 месяцах, которых бот не видел).\n")
    L.append("## Выбранный портфель\n")
    if final:
        L.append("| Монета | Стратегия | Параметры (последнее окно) | OOS доходность | OOS CAGR | Макс. просадка | Sharpe | Сделок | Плюсовых окон |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in final:
            s, p = r["latest"]
            label = s if r["strategy"] == s else f"{s} (через auto)"
            L.append(f"| {r['symbol']} | {label} | `{json.dumps(p)}` | {pct(r['oos_return'])} | {pct(r['oos_cagr'])} | "
                     f"{pct(r['oos_max_drawdown'])} | {r['oos_sharpe']:.2f} | {r['oos_trades']} | {pct(r['positive_folds'])} |")
        L.append(f"\n**Портфель целиком (OOS {full['from']} → {full['to']}):** доходность {pct(full['return'])}, "
                 f"CAGR {pct(full['cagr'])}, макс. просадка {pct(full['max_drawdown'])}, Sharpe {full['sharpe']:.2f}; "
                 f"BTC buy&hold за тот же период: {pct(full['btc_buy_hold'])}.\n")
    else:
        L.append("Ни одна пара (монета, стратегия) не прошла фильтры. Торговать сейчас нечем — это тоже результат.\n")
    L.append("## Контрольная проверка на отложенных данных\n")
    L.append(f"Портфель выбран **только по данным до {holdout['from']}**, затем проверен на последних {a.holdout_days} днях: "
             f"слоты {', '.join(holdout['sleeves']) or '—'}; доходность {pct(holdout['return'])}, "
             f"макс. просадка {pct(holdout['max_drawdown'])}, Sharpe {holdout['sharpe']:.2f}; "
             f"BTC buy&hold: {pct(holdout['btc_buy_hold'])}. Это ближе всего к тому, что можно ожидать в реальности.\n")
    L.append(f"Фильтры отбора: сделок ≥ {filters.min_trades}, OOS доходность > 0, Sharpe ≥ {filters.min_sharpe}, "
             f"просадка ≤ {pct(filters.max_drawdown)}, OOS-окон ≥ {filters.min_folds}, плюсовых окон ≥ {pct(filters.min_positive_folds)}, "
             f"корреляция со уже выбранными ≤ {filters.max_correlation}, одна стратегия на монету.\n")
    L.append("## Топ-25 пар (монета × стратегия) по OOS Sharpe\n")
    L.append("| # | Монета | Стратегия | OOS доходность | Просадка | Sharpe | Сделок | Плюсовых окон | Статус |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ranked[:25], 1):
        status = "✔ проходит" if r["eligible"] else r["rejected_because"]
        L.append(f"| {i} | {r['symbol']} | {r['strategy']} | {pct(r['oos_return'])} | {pct(r['oos_max_drawdown'])} | "
                 f"{r['oos_sharpe']:.2f} | {r['oos_trades']} | {pct(r['positive_folds'])} | {status} |")
    L.append("\n## Какая стратегия где работает (OOS Sharpe)\n")
    names = list(STRATEGIES) + ["auto"]
    L.append("| Монета | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    by = {(r["symbol"], r["strategy"]): r for r in ranked}
    for s in prof:
        L.append(f"| {s} | " + " | ".join(f"{by[(s, n)]['oos_sharpe']:.2f}" if (s, n) in by else "" for n in names) + " |")
    L.append("\n## Характер рынков\n")
    L.append("| Монета | Объём 24ч, $млн | Лет истории | Волатильность/год | Средний ADX | Efficiency ratio | Тренд сейчас (EMA200) | ADX сейчас | Доходность 1г | Средний funding/8ч |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for s, r in prof.items():
        p = r["profile"]
        v = vol.get(s, {}).get("volume_24h_usdt")
        fund = "" if r["avg_funding_8h"] is None else f"{r['avg_funding_8h'] * 100:.4f}%"
        L.append(f"| {s} | {'' if v is None else round(v / 1e6)} | {p['years']} | {pct(p['annual_volatility'])} | {p['avg_adx']:.1f} | "
                 f"{p['avg_efficiency_ratio']:.3f} | {p['trend_now']} | {'' if p['adx_now'] is None else round(p['adx_now'], 1)} | "
                 f"{pct(p['return_1y'])} | {fund} |")
    L.append("\n## Стратегии\n")
    for s in STRATEGIES.values():
        L.append(f"- **{s.name}** — {s.description}.")
    L.append("- **auto** — на каждом окне обучения выбирает лучшую стратегию и параметры из всех перечисленных.")
    L.append("\n> Прошлые результаты не гарантируют будущих. Отбор лучших пар из многих вариантов завышает OOS-цифры "
             "(эффект множественного тестирования) — поэтому смотрите на контрольную проверку и начинайте с бумажной торговли.")
    with open(os.path.join(a.out, "MARKET_ANALYSIS.md"), "w") as f:
        f.write("\n".join(L) + "\n")

    print("\n".join(L[:12 + len(final)]))
    print(f"\nSaved {a.portfolio}, {a.out}/MARKET_ANALYSIS.md, {a.out}/scan.json in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
