"""Bar-by-bar backtest of a Donchian-breakout trend-following strategy.

Rules (all decisions use CLOSED candles only):
  * signal is computed at the close of bar i, order is filled at the OPEN of bar i+1
  * long  entry: close > highest high of previous `entry_n` bars AND close > EMA(ema_n)
  * short entry: close < lowest  low  of previous `entry_n` bars AND close < EMA(ema_n)
  * exit: close crosses the opposite `exit_n` channel, or the ratcheting ATR stop is hit intrabar
  * position size: risk `risk_pct` of equity per trade (distance to initial stop), capped by max_leverage
  * costs: taker fee per side + slippage per fill. Funding rates are NOT modelled.
  * if a gap jumps past the stop, the fill is at the (worse) open price
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from .data import Candles
from .indicators import atr, ema, is_valid, prior_highest, prior_lowest


@dataclass(frozen=True)
class Params:
    entry_n: int = 55
    exit_n: int = 20
    atr_n: int = 14
    atr_stop: float = 3.0
    ema_n: int = 200
    risk_pct: float = 0.01
    max_leverage: float = 2.0
    allow_short: bool = True


@dataclass(frozen=True)
class Costs:
    fee_rate: float = 0.0006       # 0.06% taker, per side
    slippage_rate: float = 0.0005  # 0.05% against us, per fill


@dataclass
class Trade:
    side: int            # +1 long, -1 short
    entry_i: int
    exit_i: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float           # net of fees
    reason: str


@dataclass
class Position:
    side: int
    entry_i: int
    entry_price: float
    qty: float
    stop: float
    entry_fee: float


@dataclass
class Result:
    equity: List[float]            # mark-to-market equity at each bar close, from `start`
    trades: List[Trade]
    start: int
    open_position: Optional[Position] = None
    pending: Optional[str] = None  # action planned for the next open: 'long' | 'short' | 'exit'
    last_close: float = float("nan")
    params: Optional[Params] = None


def warmup_bars(p: Params) -> int:
    return max(p.entry_n, p.exit_n, p.atr_n, p.ema_n) + 2


def run_backtest(c: Candles, p: Params, costs: Costs = Costs(), initial_equity: float = 10_000.0,
                 start: int = 0, close_at_end: bool = True) -> Result:
    n = len(c)
    ema_ = ema(c.close, p.ema_n)
    atr_ = atr(c.high, c.low, c.close, p.atr_n)
    ehi = prior_highest(c.high, p.entry_n)
    elo = prior_lowest(c.low, p.entry_n)
    xhi = prior_highest(c.high, p.exit_n)
    xlo = prior_lowest(c.low, p.exit_n)

    balance = initial_equity
    pos: Optional[Position] = None
    pending: Optional[tuple] = None   # ('long'|'short', stop_distance) or ('exit', reason)
    trades: List[Trade] = []
    equity: List[float] = []

    def close_pos(i: int, raw_price: float, reason: str) -> None:
        nonlocal balance, pos
        assert pos is not None
        fill = raw_price * (1 - costs.slippage_rate * pos.side)
        fee = abs(fill * pos.qty) * costs.fee_rate
        gross = (fill - pos.entry_price) * pos.qty * pos.side
        balance += gross - fee
        trades.append(Trade(pos.side, pos.entry_i, i, pos.entry_price, fill, pos.qty,
                            gross - fee - pos.entry_fee, reason))
        pos = None

    for i in range(n):
        # 1) execute what was decided at the previous close, at this bar's open
        if pending is not None:
            kind = pending[0]
            if kind == "exit" and pos is not None:
                close_pos(i, c.open[i], pending[1])
            elif kind in ("long", "short") and pos is None:
                side = 1 if kind == "long" else -1
                fill = c.open[i] * (1 + costs.slippage_rate * side)
                stop_dist = pending[1]
                if stop_dist > 0 and balance > 0:
                    qty = balance * p.risk_pct / stop_dist
                    qty = min(qty, balance * p.max_leverage / fill)
                    fee = fill * qty * costs.fee_rate
                    balance -= fee
                    pos = Position(side, i, fill, qty, fill - side * stop_dist, fee)
            pending = None

        # 2) intrabar stop
        if pos is not None:
            if pos.side == 1 and c.low[i] <= pos.stop:
                close_pos(i, min(pos.stop, c.open[i]), "stop")
            elif pos.side == -1 and c.high[i] >= pos.stop:
                close_pos(i, max(pos.stop, c.open[i]), "stop")

        # 3) mark to market at the close
        unreal = 0.0 if pos is None else (c.close[i] - pos.entry_price) * pos.qty * pos.side
        if i >= start:
            equity.append(balance + unreal)

        # 4) decide at the close (executed next open)
        a = atr_[i]
        if not (is_valid(a) and is_valid(ema_[i]) and is_valid(ehi[i]) and is_valid(xlo[i])):
            continue
        if pos is not None:
            ns = c.close[i] - p.atr_stop * a if pos.side == 1 else c.close[i] + p.atr_stop * a
            pos.stop = max(pos.stop, ns) if pos.side == 1 else min(pos.stop, ns)  # ratchet only
            if pos.side == 1 and c.close[i] < xlo[i]:
                pending = ("exit", "channel")
            elif pos.side == -1 and c.close[i] > xhi[i]:
                pending = ("exit", "channel")
        elif i >= start and (i + 1 < n or not close_at_end):
            if c.close[i] > ehi[i] and c.close[i] > ema_[i]:
                pending = ("long", p.atr_stop * a)
            elif p.allow_short and c.close[i] < elo[i] and c.close[i] < ema_[i]:
                pending = ("short", p.atr_stop * a)

    open_pos = pos
    if close_at_end and pos is not None:
        close_pos(n - 1, c.close[n - 1], "end")
        if equity:
            equity[-1] = balance
        open_pos = None
    return Result(equity, trades, start, open_pos, pending[0] if pending else None,
                  c.close[-1] if n else float("nan"), p)


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

    def as_dict(self) -> dict:
        return asdict(self)


def compute_metrics(res: Result, bars_per_year: float, initial_equity: float = 10_000.0) -> Metrics:
    eq = res.equity
    if len(eq) < 2:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0)
    total = eq[-1] / eq[0] - 1
    years = len(eq) / bars_per_year
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else -1.0
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    sharpe = mean / math.sqrt(var) * math.sqrt(bars_per_year) if var > 0 else 0.0
    wins = [t.pnl for t in res.trades if t.pnl > 0]
    losses = [-t.pnl for t in res.trades if t.pnl <= 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (float("inf") if wins else 0.0)
    nt = len(res.trades)
    avg = (sum(t.pnl for t in res.trades) / nt / eq[0]) if nt else 0.0
    return Metrics(total, cagr, mdd, sharpe, nt, (len(wins) / nt if nt else 0.0), pf, avg)


def buy_and_hold(c: Candles, start: int = 0) -> float:
    return c.close[-1] / c.close[start] - 1
