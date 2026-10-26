"""Generic bar-by-bar backtest engine for target-position strategies.

A strategy emits, for every closed bar i:
  * target[i]      desired side (+1 long, -1 short, 0 flat), executed at the OPEN of bar i+1
  * stop_dist[i]   initial protective-stop distance (price units) for an entry at bar i+1
  * trail_dist[i]  optional ratcheting trailing-stop distance from the close of bar i
  * entry_event[i] optional "fresh entry signal" side, used to re-arm after a stop-out

Rules:
  * no look-ahead: decisions use closed bars only, fills happen at the next open
  * stops are checked intrabar with the worst case assumption; gaps fill at the (worse) open
  * after a stop-out the same side is not re-entered until the target changes
    (or the strategy emits a fresh entry event for that side)
  * position size = risk_pct of equity / stop distance, capped by max_leverage
  * costs: taker fee + slippage per fill, funding charged on open positions every bar
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import List, Optional, Sequence

from .indicators import is_valid


@dataclass(frozen=True)
class Costs:
    fee_rate: float = 0.0006       # Bitget USDT-M taker fee, per side
    slippage_rate: float = 0.0005  # against us, per fill


@dataclass(frozen=True)
class Risk:
    risk_pct: float = 0.01      # equity lost if the initial stop is hit (before gaps/costs)
    max_leverage: float = 2.0   # notional / equity cap per position


@dataclass
class Signals:
    target: List[int]
    stop_dist: List[float]
    trail_dist: Optional[List[float]] = None
    entry_event: Optional[List[int]] = None
    max_hold: int = 0  # bars; 0 = unlimited


@dataclass
class Trade:
    side: int
    entry_i: int
    exit_i: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float      # net of fees and funding
    reason: str


@dataclass
class Position:
    side: int
    entry_i: int
    entry_price: float
    qty: float
    stop: float
    entry_fee: float
    funding_paid: float = 0.0


@dataclass
class Result:
    equity: List[float]               # mark-to-market equity at each bar close in [start, end)
    trades: List[Trade]
    start: int
    end: int
    open_position: Optional[Position] = None
    next_target: int = 0              # side the strategy wants after the last bar (for live trading)
    next_stop_dist: float = float("nan")
    stop_lock: int = 0                # side blocked after a stop-out
    last_close: float = float("nan")
    funding_paid: float = 0.0
    bars_in_market: int = 0


def run(c, sig: Signals, costs: Costs = Costs(), risk: Risk = Risk(), start: int = 0,
        end: Optional[int] = None, initial_equity: float = 10_000.0, close_at_end: bool = True,
        funding: Optional[Sequence[float]] = None) -> Result:
    end = len(c) if end is None else min(end, len(c))
    o, h, l, cl = c.open, c.high, c.low, c.close
    target, stop_dist = sig.target, sig.stop_dist
    trail, event = sig.trail_dist, sig.entry_event

    balance = initial_equity
    pos: Optional[Position] = None
    pending = 0 if start == 0 else None   # None = nothing decided yet
    lock = 0
    trades: List[Trade] = []
    equity: List[float] = []
    funding_total = 0.0
    in_market = 0

    def close_pos(i: int, raw: float, reason: str) -> None:
        nonlocal balance, pos
        fill = raw * (1 - costs.slippage_rate * pos.side)
        fee = abs(fill * pos.qty) * costs.fee_rate
        gross = (fill - pos.entry_price) * pos.qty * pos.side
        balance += gross - fee
        trades.append(Trade(pos.side, pos.entry_i, i, pos.entry_price, fill, pos.qty,
                            gross - fee - pos.entry_fee - pos.funding_paid, reason))
        pos = None

    for i in range(start, end):
        # 1) execute the decision of the previous close at this open
        if pending is not None:
            if pos is not None and pending != pos.side:
                close_pos(i, o[i], "exit" if pending == 0 else "reverse")
            if pos is None and pending != 0:
                sd = stop_dist[i - 1]
                if is_valid(sd) and sd > 0 and balance > 0:
                    fill = o[i] * (1 + costs.slippage_rate * pending)
                    qty = min(balance * risk.risk_pct / sd, balance * risk.max_leverage / fill)
                    fee = fill * qty * costs.fee_rate
                    balance -= fee
                    pos = Position(pending, i, fill, qty, fill - pending * sd, fee)
        pending = None

        # 2) intrabar stop, worst case
        if pos is not None:
            if pos.side == 1 and l[i] <= pos.stop:
                close_pos(i, min(pos.stop, o[i]), "stop")
                lock = 1
            elif pos.side == -1 and h[i] >= pos.stop:
                close_pos(i, max(pos.stop, o[i]), "stop")
                lock = -1

        # 3) funding (positive rate: longs pay shorts) and mark to market
        if pos is not None:
            in_market += 1
            if funding is not None and funding[i]:
                f = pos.side * pos.qty * cl[i] * funding[i]
                balance -= f
                pos.funding_paid += f
                funding_total += f
        unreal = 0.0 if pos is None else (cl[i] - pos.entry_price) * pos.qty * pos.side
        equity.append(balance + unreal)

        # 4) decide at the close
        t = target[i]
        if lock and (t != lock or (event is not None and event[i] == lock)):
            lock = 0
        if lock and t == lock:
            t = 0 if pos is None else t
        if pos is not None:
            if sig.max_hold and i - pos.entry_i + 1 >= sig.max_hold and t == pos.side:
                t = 0
                lock = pos.side
            if trail is not None and t == pos.side and is_valid(trail[i]):
                ns = cl[i] - pos.side * trail[i]
                pos.stop = max(pos.stop, ns) if pos.side == 1 else min(pos.stop, ns)
        pending = t

    open_pos = pos
    if close_at_end and pos is not None and end > start:
        close_pos(end - 1, cl[end - 1], "end")
        equity[-1] = balance
        open_pos = None
    last = end - 1
    return Result(equity, trades, start, end, open_pos,
                  next_target=pending or 0,
                  next_stop_dist=stop_dist[last] if last >= 0 else float("nan"),
                  stop_lock=lock, last_close=cl[last] if last >= 0 else float("nan"),
                  funding_paid=funding_total, bars_in_market=in_market)


@dataclass
class Metrics:
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    trades: int
    win_rate: float
    profit_factor: float
    avg_trade_pct: float
    exposure: float

    def as_dict(self) -> dict:
        d = asdict(self)
        if math.isinf(d["profit_factor"]):
            d["profit_factor"] = 99.0
        return d


def equity_stats(eq: Sequence[float], bars_per_year: float):
    """(total_return, cagr, max_drawdown, sharpe) of an equity curve."""
    if len(eq) < 2 or eq[0] <= 0:
        return 0.0, 0.0, 0.0, 0.0
    total = eq[-1] / eq[0] - 1
    years = len(eq) / bars_per_year
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else -1.0
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    sharpe = mean / math.sqrt(var) * math.sqrt(bars_per_year) if var > 0 else 0.0
    return total, cagr, min(mdd, 1.0), sharpe


def compute_metrics(res: Result, bars_per_year: float) -> Metrics:
    total, cagr, mdd, sharpe = equity_stats(res.equity, bars_per_year)
    wins = [t.pnl for t in res.trades if t.pnl > 0]
    losses = [-t.pnl for t in res.trades if t.pnl <= 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (float("inf") if wins else 0.0)
    nt = len(res.trades)
    eq0 = res.equity[0] if res.equity else 1.0
    avg = (sum(t.pnl for t in res.trades) / nt / eq0) if nt else 0.0
    exposure = res.bars_in_market / max(len(res.equity), 1)
    return Metrics(total, cagr, mdd, sharpe, nt, (len(wins) / nt if nt else 0.0), pf, avg, exposure)


def buy_and_hold(c, start: int = 0, end: Optional[int] = None) -> float:
    end = len(c) if end is None else end
    return c.close[end - 1] / c.close[start] - 1
