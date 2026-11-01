"""Rolling walk-forward validation for the deployed portfolio.

Each sleeve keeps its configured strategy family. Parameters are selected using only the
preceding training window, then frozen for the immediately following non-overlapping test
window. The same fold selections are replayed at 1% and 0.5% risk. No real orders.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from types import SimpleNamespace

from bot import data as D
from bot.walkforward import build_candidates, fold_bounds, score
from bot.engine import Costs, Risk, compute_metrics, equity_stats, run
from bot.scanner import portfolio_curve
from compare_portfolios import load_json, validate_configs


def select_candidate(c, sleeve, start, end, costs, selection_risk, min_trades=5):
    """Select params using only candles in the training interval."""
    candidates = build_candidates(c, names=[sleeve["strategy"]])
    scores = []
    for candidate in candidates:
        result = run(c, candidate.signals, costs, selection_risk, start, end)
        metrics = compute_metrics(result, D.BARS_PER_YEAR["4H"])
        scores.append(score(metrics, min_trades))
    if not scores:
        return None, float("-inf")
    best_i = max(range(len(scores)), key=scores.__getitem__)
    return (candidates[best_i], scores[best_i]) if scores[best_i] > 0 else (None, scores[best_i])


def iso(ts):
    return dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).isoformat()


def evaluate(config, market, train_bars=2190, test_bars=540, warmup=600,
             initial=10_000.0, cost_multiplier=1.0, v2_risk_pct=None):
    gran = config.get("granularity", "4H")
    if gran != "4H":
        raise ValueError("Walk-forward runner currently requires 4H candles")
    if train_bars <= 0 or test_bars <= 0 or warmup < 1:
        raise ValueError("train_bars, test_bars and warmup must be positive")

    symbols = [s["symbol"] for s in config["sleeves"]]
    missing = [s for s in symbols if s not in market]
    if missing:
        raise ValueError("Missing market data: " + ", ".join(missing))
    lengths = {len(market[s][0]) for s in symbols}
    if len(lengths) != 1:
        raise ValueError("All symbols must have the same candle count for aligned folds")
    n = lengths.pop()
    folds = fold_bounds(n, warmup, train_bars, test_bars)
    if len(folds) < 2:
        raise ValueError(f"Need at least two folds; got {len(folds)}")

    base = Costs(**config.get("costs", {}))
    costs = Costs(base.fee_rate * cost_multiplier, base.slippage_rate * cost_multiplier)
    max_lev = config["risk"].get("max_leverage", 2.0)
    selection_risk = Risk(config["risk"]["risk_pct"], max_lev)
    risks = {
        "control": Risk(config["risk"]["risk_pct"], max_lev),
        "v2": Risk(v2_risk_pct if v2_risk_pct is not None else config["risk"]["risk_pct"] * 0.5, max_lev),
    }
    curves = {label: [] for label in risks}
    pnls = {label: [] for label in risks}
    first_c = market[symbols[0]][0]
    fold_report = []
    for i, (a, b, e) in enumerate(folds):
        fold_report.append({
            "fold": i + 1,
            "train_from": iso(first_c.ts[a]),
            "train_to_exclusive": iso(first_c.ts[b]),
            "test_from": iso(first_c.ts[b]),
            "test_to_exclusive": iso(first_c.ts[e - 1] + D.GRANULARITY_MS[gran]),
            "selected": {},
        })

    for sleeve in config["sleeves"]:
        symbol = sleeve["symbol"]
        candles, funding = market[symbol]
        wf_by_risk = {}
        for label in risks:
            wf = SimpleNamespace(
                oos_ts=[candles.ts[folds[0][1] - 1]],
                oos_equity=[1.0],
                oos_exposure=[0.0],
            )
            curves[label].append({"symbol": symbol, "_wf": wf})
            wf_by_risk[label] = wf

        candidates = build_candidates(candles, names=[sleeve["strategy"]])
        for fold_i, (a, b, e) in enumerate(folds):
            scored = []
            for candidate in candidates:
                train_result = run(
                    candles, candidate.signals, costs, selection_risk,
                    start=a, end=b, initial_equity=initial, funding=funding,
                )
                metrics = compute_metrics(train_result, D.BARS_PER_YEAR[gran])
                scored.append(score(metrics, min_trades=5))
            best_i = max(range(len(scored)), key=scored.__getitem__) if scored else -1
            chosen = candidates[best_i] if best_i >= 0 and scored[best_i] > 0 else None
            fold_report[fold_i]["selected"][symbol] = (
                {"strategy": chosen.strategy, "params": chosen.params}
                if chosen is not None else None
            )

            for label, risk in risks.items():
                if chosen is None:
                    curve = [1.0] * (e - b)
                    exposure = [0.0] * (e - b)
                    trades = []
                else:
                    result = run(
                        candles, chosen.signals, costs, risk,
                        start=b, end=e, initial_equity=initial, funding=funding,
                    )
                    curve = [v / initial for v in result.equity]
                    exposure = result.exposure
                    trades = result.trades
                    pnls[label].extend(t.pnl for t in trades)
                wf = wf_by_risk[label]
                base_equity = wf.oos_equity[-1]
                wf.oos_ts.extend(candles.ts[b:e])
                wf.oos_equity.extend(base_equity * v for v in curve)
                wf.oos_exposure.extend(exposure)

    results = {}
    for label, risk in risks.items():
        ts, equity = portfolio_curve(
            curves[label],
            max_total_leverage=config.get("limits", {}).get("max_total_leverage"),
            max_daily_loss=config.get("limits", {}).get("max_daily_loss"),
        )
        total, cagr, max_dd, sharpe = equity_stats(equity, D.BARS_PER_YEAR[gran])
        trade_pnls = pnls[label]
        gross_profit = sum(x for x in trade_pnls if x > 0)
        gross_loss = -sum(x for x in trade_pnls if x <= 0)
        index = {timestamp: i + 1 for i, timestamp in enumerate(ts)}
        previous = 1.0
        fold_returns = []
        for (_, _, e) in folds:
            end_ts = first_c.ts[e - 1]
            current = equity[index[end_ts]] if end_ts in index else previous
            fold_returns.append(current / previous - 1 if previous else 0.0)
            previous = current
        results[label] = {
            "risk_pct": risk.risk_pct,
            "return": total,
            "cagr": cagr,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "trades": len(trade_pnls),
            "win_rate": sum(x > 0 for x in trade_pnls) / len(trade_pnls) if trade_pnls else 0.0,
            "profit_factor": gross_profit / gross_loss if gross_loss else (99.0 if gross_profit else 0.0),
            "positive_folds": sum(x > 0 for x in fold_returns),
            "fold_count": len(fold_returns),
            "fold_returns": fold_returns,
            "equity_points": len(equity),
        }

    return {
        "cost_multiplier": cost_multiplier,
        "costs": {"fee_rate_per_side": costs.fee_rate, "slippage_rate_per_fill": costs.slippage_rate},
        "train_bars": train_bars,
        "test_bars": test_bars,
        "warmup_bars": warmup,
        "fold_count": len(folds),
        "test_from": fold_report[0]["test_from"],
        "test_to": fold_report[-1]["test_to_exclusive"],
        "same_selected_parameters_for_risk_comparison": True,
        "control": results["control"],
        "v2": results["v2"],
        "folds": fold_report,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", default="config/portfolio.json")
    parser.add_argument("--experiment", default="config/portfolio_v2_experiment.json")
    parser.add_argument("--days", type=int, default=1460)
    parser.add_argument("--train-bars", type=int, default=2190)
    parser.add_argument("--test-bars", type=int, default=540)
    parser.add_argument("--warmup", type=int, default=600)
    parser.add_argument("--initial", type=float, default=10_000.0)
    parser.add_argument("--cost-multipliers", default="1,1.5,2")
    parser.add_argument("--out", default="reports/v2_walkforward_validation.json")
    args = parser.parse_args()

    control = load_json(args.control)
    experiment = load_json(args.experiment)
    validate_configs(control, experiment)
    gran = control.get("granularity", "4H")
    market = {}
    for sleeve in control["sleeves"]:
        symbol = sleeve["symbol"]
        candles = D.load_or_fetch(symbol, gran, args.days)
        try:
            history = D.fetch_funding_history(symbol)
        except Exception as exc:
            print(f"WARNING: funding history unavailable for {symbol}: {exc}", file=sys.stderr)
            history = {}
        funding = D.funding_per_bar(candles, history, D.GRANULARITY_MS[gran])
        market[symbol] = (candles, funding)

    multipliers = [float(x.strip()) for x in args.cost_multipliers.split(",") if x.strip()]
    if not multipliers or any(x < 1 for x in multipliers):
        raise ValueError("Cost multipliers must be >= 1")
    scenarios = [
        evaluate(control, market, args.train_bars, args.test_bars, args.warmup,
                 args.initial, multiplier, experiment["risk"]["risk_pct"])
        for multiplier in multipliers
    ]
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "granularity": gran,
        "days_requested": args.days,
        "initial_equity_usdt": args.initial,
        "symbols": [s["symbol"] for s in control["sleeves"]],
        "method": (
            "Rolling chronological walk-forward. Each deployed sleeve keeps its configured "
            "strategy family; parameters are selected only on the preceding training window "
            "and frozen for the next non-overlapping test window. The same selections are "
            "replayed at control and half risk. Positions close at fold boundaries."
        ),
        "selection_rule": "Training-window CAGR / max(5% drawdown, observed max drawdown), minimum 5 trades; selected using control risk.",
        "caveats": [
            "This validates the configured strategy families, not a newly optimized portfolio universe.",
            "Portfolio leverage and daily-loss aggregation reuse bot.scanner.portfolio_curve and remain an approximation.",
            "Cost stress scenarios reselect parameters within each training window at the corresponding cost level.",
            "Historical results do not guarantee future performance.",
        ],
        "scenarios": scenarios,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    summary = [{
        "cost_multiplier": s["cost_multiplier"],
        "fold_count": s["fold_count"],
        "control": {k: v for k, v in s["control"].items() if k != "fold_returns"},
        "v2": {k: v for k, v in s["v2"].items() if k != "fold_returns"},
    } for s in scenarios]
    print(json.dumps({"summary": summary, "saved": args.out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
