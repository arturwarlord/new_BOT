"""Portfolio trader: turns the latest closed candles into target positions for every sleeve in
config/portfolio.json and reconciles the broker with them. Same rules as the backtest engine:
decisions on closed bars, stop on every position, no re-entry on the same side after a stop
until the strategy changes its mind, ATR trailing stops that only ratchet."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from . import data as D
from . import strategies as S
from .broker import Broker
from .indicators import is_valid
from .notify import notify

log = logging.getLogger("bot")


@dataclass
class Limits:
    max_total_leverage: float = 3.0   # sum of all position notionals / equity
    max_daily_loss: float = 0.05      # flatten everything and pause for the UTC day after this drawdown
    history_days: int = 150           # candles fetched per cycle (warm-up for every strategy)


def load_portfolio(path: str = "config/portfolio.json") -> dict:
    with open(path) as f:
        pf = json.load(f)
    if not pf.get("sleeves"):
        raise SystemExit(f"{path} has no sleeves: run scan_market.py first")
    return pf


class State:
    def __init__(self, path: str):
        self.path = path
        self.s = {"locks": {}, "held": {}, "opened_bar": {}, "stops": {}, "day": None, "day_start_equity": None,
                  "halted_day": None, "last_bar": {}}
        if os.path.exists(path):
            with open(path) as f:
                self.s.update(json.load(f))

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f, indent=2)
        os.replace(tmp, self.path)


@dataclass
class Decision:
    symbol: str
    strategy: str
    target: int
    stop_dist: float
    trail_dist: Optional[float]
    max_hold: int
    entry_event: int
    last_close: float
    last_ts: int


def decide(sleeve: dict, c) -> Decision:
    strat = S.get(sleeve["strategy"])
    sig = strat.signals(S.IndicatorCache(c), sleeve["params"])
    i = len(c) - 1
    return Decision(sleeve["symbol"], sleeve["strategy"], sig.target[i], sig.stop_dist[i],
                    sig.trail_dist[i] if sig.trail_dist is not None else None, sig.max_hold,
                    sig.entry_event[i] if sig.entry_event is not None else 0, c.close[i], c.ts[i])


def cycle(pf: dict, broker: Broker, state: State, limits: Limits = Limits(), fetch=D.fetch_bitget) -> List[str]:
    gran = pf.get("granularity", "4H")
    risk = pf["risk"]
    events: List[str] = []
    s = state.s
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    candles = {}
    for sl in pf["sleeves"]:
        c = fetch(sl["symbol"], gran, limits.history_days)
        candles[sl["symbol"]] = c
        ev = broker.sync(sl["symbol"], c)
        if ev:
            events.append(ev)

    equity = broker.equity()
    if s["day"] != today:
        s["day"], s["day_start_equity"] = today, equity
    if s["halted_day"] == today:
        events.append(f"trading paused for {today} (daily loss limit)")
        state.save()
        return events
    if s["day_start_equity"] and equity < s["day_start_equity"] * (1 - limits.max_daily_loss):
        for sl in pf["sleeves"]:
            if broker.position(sl["symbol"]):
                broker.close(sl["symbol"], candles[sl["symbol"]].close[-1], "daily loss limit")
        s["halted_day"] = today
        events.append(f"DAILY LOSS LIMIT: equity {equity:.2f} < {s['day_start_equity']:.2f} - "
                      f"{limits.max_daily_loss:.0%}; all positions closed, paused until tomorrow (UTC)")
        state.save()
        return events

    def notional_open() -> float:
        tot = 0.0
        for sl in pf["sleeves"]:
            p = broker.position(sl["symbol"])
            if p:
                tot += p.qty * candles[sl["symbol"]].close[-1]
        return tot

    for sl in pf["sleeves"]:
        sym = sl["symbol"]
        c = candles[sym]
        d = decide(sl, c)
        pos = broker.position(sym)
        lock = s["locks"].get(sym, 0)
        held = s["held"].get(sym, 0)
        new_bar = s["last_bar"].get(sym) != d.last_ts

        # position disappeared on the exchange (stop-loss / liquidation / manual) -> lock that side
        if pos is None and held:
            lock = held
            s["held"][sym] = held = 0
            events.append(f"{sym}: position closed outside the bot, {('long', '', 'short')[1 - lock]} side locked")

        target = d.target
        if lock and (target != lock or d.entry_event == lock):
            lock = 0
        if lock and target == lock and pos is None:
            target = 0
        if pos is not None and d.max_hold and target == pos.side:
            opened = s["opened_bar"].get(sym)
            if opened is not None and sum(1 for t in c.ts if t >= opened) >= d.max_hold:
                target, lock = 0, pos.side
        s["locks"][sym] = lock

        if pos is not None and pos.side != target:
            broker.close(sym, d.last_close, "signal")
            events.append(f"{sym}: CLOSE {'long' if pos.side == 1 else 'short'} {pos.qty:g} @~{d.last_close:g} ({sl['strategy']})")
            s["held"][sym] = 0
            pos = None

        if pos is None and target != 0 and new_bar:
            if not (is_valid(d.stop_dist) and d.stop_dist > 0):
                continue
            qty = equity * risk["risk_pct"] / d.stop_dist
            qty = min(qty, equity * risk["max_leverage"] / d.last_close)
            room = max(limits.max_total_leverage * equity - notional_open(), 0.0)
            qty = min(qty, room / d.last_close)
            qty = broker.round_qty(sym, qty, d.last_close)
            if qty <= 0:
                events.append(f"{sym}: wanted {'long' if target == 1 else 'short'} but size is below the minimum / leverage cap")
            else:
                stop = d.last_close - target * d.stop_dist
                broker.prepare(sym, int(risk["max_leverage"]) + 1)
                broker.open(sym, target, qty, d.last_close, stop)
                s["held"][sym] = target
                s["opened_bar"][sym] = d.last_ts
                s["stops"][sym] = stop
                events.append(f"{sym}: OPEN {'LONG' if target == 1 else 'SHORT'} {qty:g} @~{d.last_close:g} "
                              f"stop {stop:g} ({sl['strategy']} {json.dumps(sl['params'])})")
        elif pos is not None and d.trail_dist is not None and is_valid(d.trail_dist):
            ns = d.last_close - pos.side * d.trail_dist
            known = [x for x in (pos.stop, s["stops"].get(sym)) if x is not None]
            cur = (max(known) if pos.side == 1 else min(known)) if known else None
            if cur is None or (ns > cur if pos.side == 1 else ns < cur):  # stops only ratchet
                broker.set_stop(sym, ns)
                s["stops"][sym] = ns
                events.append(f"{sym}: trail stop -> {ns:g}")
        s["last_bar"][sym] = d.last_ts

    state.save()
    return events


def describe(pf: dict, fetch=D.fetch_bitget, history_days: int = 150) -> List[dict]:
    """Read-only view of what every sleeve wants right now."""
    out = []
    for sl in pf["sleeves"]:
        c = fetch(sl["symbol"], pf.get("granularity", "4H"), history_days)
        d = decide(sl, c)
        out.append({"symbol": d.symbol, "strategy": d.strategy, "params": sl["params"],
                    "target": {1: "long", -1: "short", 0: "flat"}[d.target],
                    "stop_if_entering_now": None if not is_valid(d.stop_dist) else d.last_close - d.target * d.stop_dist
                    if d.target else None,
                    "last_close": d.last_close,
                    "last_candle_utc": dt.datetime.fromtimestamp(d.last_ts / 1000, dt.timezone.utc).isoformat()})
    return out


def report(events: List[str], broker: Broker) -> None:
    for e in events:
        log.info(e)
    if events:
        notify(f"[new_BOT {broker.name}] equity {broker.equity():.2f}\n" + "\n".join(events))
