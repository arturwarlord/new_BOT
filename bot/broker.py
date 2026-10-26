"""Execution backends with one interface:

* PaperBroker  - simulated account stored in a JSON file (default, no keys needed)
* BitgetBroker - Bitget USDT-M futures REST v2 (live or demo with a Demo API key)

Both assume one-way position mode and keep an exchange-side (or simulated) stop-loss on every position."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from . import data as D
from .engine import Costs

log = logging.getLogger("bot")


@dataclass
class Pos:
    side: int          # +1 long, -1 short
    qty: float
    entry: float
    stop: Optional[float] = None


class Broker:
    name = "base"

    def equity(self) -> float: ...
    def position(self, symbol: str) -> Optional[Pos]: ...
    def open(self, symbol: str, side: int, qty: float, price: float, stop: float) -> Optional[Pos]: ...
    def close(self, symbol: str, price: float, reason: str = "") -> None: ...
    def set_stop(self, symbol: str, stop: float) -> None: ...
    def prepare(self, symbol: str, leverage: int) -> None: ...
    def sync(self, symbol: str, candles) -> Optional[str]:
        """Apply what happened since the last run (e.g. a stop hit). Returns an event string or None."""
        return None

    def round_qty(self, symbol: str, qty: float, price: float) -> float:
        return qty


# ----------------------------------------------------------------------------- paper

class PaperBroker(Broker):
    name = "paper"

    def __init__(self, path: str = "state/paper_account.json", initial_equity: float = 10_000.0,
                 costs: Costs = Costs()):
        self.path, self.costs = path, costs
        if os.path.exists(path):
            with open(path) as f:
                self.s = json.load(f)
        else:
            self.s = {"balance": initial_equity, "positions": {}, "marks": {}, "history": []}
            self._save()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f, indent=2)
        os.replace(tmp, self.path)

    def equity(self) -> float:
        eq = self.s["balance"]
        for sym, p in self.s["positions"].items():
            mark = self.s["marks"].get(sym, p["entry"])
            eq += (mark - p["entry"]) * p["qty"] * p["side"]
        return eq

    def position(self, symbol):
        p = self.s["positions"].get(symbol)
        return None if p is None else Pos(p["side"], p["qty"], p["entry"], p.get("stop"))

    def _fill(self, symbol, side, qty, price, reason):
        fill = price * (1 + self.costs.slippage_rate * side)
        fee = abs(fill * qty) * self.costs.fee_rate
        self.s["balance"] -= fee
        return fill, fee

    def open(self, symbol, side, qty, price, stop):
        fill, fee = self._fill(symbol, side, qty, price, "open")
        # only candles that open after the last candle seen by sync() can hit the new stop
        self.s["positions"][symbol] = {"side": side, "qty": qty, "entry": fill, "stop": stop,
                                       "opened": int(time.time() * 1000),
                                       "checked_ts": self.s.setdefault("seen_ts", {}).get(symbol, -1) + 1}
        self.s["marks"][symbol] = price
        self.s["history"].append({"t": int(time.time()), "symbol": symbol, "action": "open",
                                  "side": side, "qty": qty, "price": fill, "fee": fee, "stop": stop})
        self._save()
        return self.position(symbol)

    def close(self, symbol, price, reason=""):
        p = self.s["positions"].pop(symbol, None)
        if p is None:
            return
        fill, fee = self._fill(symbol, -p["side"], p["qty"], price, "close")
        pnl = (fill - p["entry"]) * p["qty"] * p["side"]
        self.s["balance"] += pnl
        self.s["marks"][symbol] = price
        self.s["history"].append({"t": int(time.time()), "symbol": symbol, "action": "close", "reason": reason,
                                  "price": fill, "pnl": pnl - fee, "fee": fee})
        self._save()

    def set_stop(self, symbol, stop):
        if symbol in self.s["positions"]:
            self.s["positions"][symbol]["stop"] = stop
            self._save()

    def prepare(self, symbol, leverage):
        pass

    def sync(self, symbol, c):
        """Walk the candles closed since the last check and fire the simulated stop (worst case)."""
        p = self.s["positions"].get(symbol)
        if len(c):
            self.s["marks"][symbol] = c.close[-1]
            self.s.setdefault("seen_ts", {})[symbol] = c.ts[-1]
        if p is None:
            self._save()
            return None
        event = None
        for i in range(len(c)):
            if c.ts[i] < p.get("checked_ts", 0):
                continue
            stop = p.get("stop")
            if stop is not None and (p["side"] == 1 and c.low[i] <= stop or p["side"] == -1 and c.high[i] >= stop):
                fill = min(stop, c.open[i]) if p["side"] == 1 else max(stop, c.open[i])
                self.close(symbol, fill, "stop")
                return f"{symbol}: stop hit at {fill:g}"
            p["checked_ts"] = c.ts[i] + 1
        self._save()
        return event


# ----------------------------------------------------------------------------- bitget

class BitgetError(RuntimeError):
    pass


class BitgetBroker(Broker):
    """Bitget USDT-M futures, one-way position mode, cross margin.

    Keys come from the environment: BITGET_API_KEY, BITGET_API_SECRET, BITGET_API_PASSPHRASE.
    demo=True sends the `paptrading: 1` header (requires a Demo API key created in demo mode)."""
    name = "bitget"

    def __init__(self, demo: bool = True, margin_coin: str = "USDT", product_type: str = D.PRODUCT_TYPE,
                 margin_mode: str = "crossed"):
        self.key = os.environ.get("BITGET_API_KEY", "")
        self.secret = os.environ.get("BITGET_API_SECRET", "")
        self.passphrase = os.environ.get("BITGET_API_PASSPHRASE", "")
        if not (self.key and self.secret and self.passphrase):
            raise BitgetError("set BITGET_API_KEY, BITGET_API_SECRET and BITGET_API_PASSPHRASE")
        self.demo = demo
        self.margin_coin = os.environ.get("BITGET_MARGIN_COIN", margin_coin)
        self.product_type = os.environ.get("BITGET_PRODUCT_TYPE", product_type)
        self.margin_mode = margin_mode
        self._contracts: Dict[str, dict] = {}
        self._positions: Optional[Dict[str, Pos]] = None
        self.name = "bitget-demo" if demo else "bitget-LIVE"

    # -- transport
    def _request(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> dict:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        payload = json.dumps(body) if body else ""
        ts = str(int(time.time() * 1000))
        prehash = ts + method.upper() + path + query + payload
        sign = base64.b64encode(hmac.new(self.secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
        headers = {"ACCESS-KEY": self.key, "ACCESS-SIGN": sign, "ACCESS-TIMESTAMP": ts,
                   "ACCESS-PASSPHRASE": self.passphrase, "Content-Type": "application/json", "locale": "en-US"}
        if self.demo:
            headers["paptrading"] = "1"
        req = urllib.request.Request(D.BASE_URL + path + query, data=payload.encode() if payload else None,
                                     headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                out = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise BitgetError(f"{method} {path}: HTTP {e.code} {e.read().decode(errors='replace')}") from e
        if out.get("code") != "00000":
            raise BitgetError(f"{method} {path}: {out}")
        return out.get("data")

    # -- helpers
    def contract(self, symbol: str) -> dict:
        if symbol not in self._contracts:
            rows = D.fetch_contracts(self.product_type, symbol)
            if not rows:
                raise BitgetError(f"unknown contract {symbol}")
            self._contracts[symbol] = rows[0]
        return self._contracts[symbol]

    def round_qty(self, symbol, qty, price):
        info = self.contract(symbol)
        step = float(info.get("sizeMultiplier") or 10 ** -int(info.get("volumePlace", 3)))
        q = math.floor(qty / step + 1e-9) * step
        places = int(info.get("volumePlace", 3))
        q = round(q, places)
        if q < float(info.get("minTradeNum") or 0) or q * price < float(info.get("minTradeUSDT") or 5):
            return 0.0
        return q

    def _price(self, symbol: str, price: float) -> str:
        info = self.contract(symbol)
        places = int(info.get("pricePlace", 2))
        step = float(info.get("priceEndStep") or 1) * 10 ** -places
        return f"{round(round(price / step) * step, places):.{places}f}"

    # -- interface
    def equity(self):
        rows = self._request("GET", "/api/v2/mix/account/accounts", {"productType": self.product_type})
        for r in rows or []:
            if r.get("marginCoin", "").upper() == self.margin_coin.upper():
                return float(r.get("usdtEquity") or r.get("accountEquity") or 0)
        raise BitgetError(f"no {self.margin_coin} futures account")

    def _all_positions(self) -> Dict[str, Pos]:
        if self._positions is None:
            rows = self._request("GET", "/api/v2/mix/position/all-position",
                                 {"productType": self.product_type, "marginCoin": self.margin_coin}) or []
            out = {}
            for r in rows:
                qty = float(r.get("total") or 0)
                if qty <= 0:
                    continue
                side = 1 if r.get("holdSide") in ("long", "buy") else -1
                stop = float(r["stopLoss"]) if r.get("stopLoss") not in (None, "", "0") else None
                out[r["symbol"]] = Pos(side, qty, float(r.get("openPriceAvg") or 0), stop)
            self._positions = out
        return self._positions

    def position(self, symbol):
        return self._all_positions().get(symbol)

    def prepare(self, symbol, leverage):
        try:
            self._request("POST", "/api/v2/mix/account/set-leverage", body={
                "symbol": symbol, "productType": self.product_type, "marginCoin": self.margin_coin,
                "leverage": str(int(max(1, leverage)))})
        except BitgetError as e:
            log.warning("set-leverage %s: %s", symbol, e)

    def open(self, symbol, side, qty, price, stop):
        self._request("POST", "/api/v2/mix/order/place-order", body={
            "symbol": symbol, "productType": self.product_type, "marginMode": self.margin_mode,
            "marginCoin": self.margin_coin, "size": str(qty), "side": "buy" if side == 1 else "sell",
            "orderType": "market", "clientOid": f"nb-{uuid.uuid4().hex[:24]}",
            "presetStopLossPrice": self._price(symbol, stop)})
        self._positions = None
        return self.position(symbol)

    def close(self, symbol, price, reason=""):
        p = self.position(symbol)
        if p is None:
            return
        self._cancel_stops(symbol)
        self._request("POST", "/api/v2/mix/order/place-order", body={
            "symbol": symbol, "productType": self.product_type, "marginMode": self.margin_mode,
            "marginCoin": self.margin_coin, "size": str(p.qty), "side": "sell" if p.side == 1 else "buy",
            "orderType": "market", "reduceOnly": "YES", "clientOid": f"nb-{uuid.uuid4().hex[:24]}"})
        self._positions = None

    def _cancel_stops(self, symbol):
        try:
            self._request("POST", "/api/v2/mix/order/cancel-plan-order", body={
                "symbol": symbol, "productType": self.product_type, "marginCoin": self.margin_coin,
                "planType": "pos_loss"})
        except BitgetError as e:
            log.info("cancel pos_loss %s: %s", symbol, e)

    def set_stop(self, symbol, stop):
        p = self.position(symbol)
        if p is None:
            return
        self._cancel_stops(symbol)
        self._request("POST", "/api/v2/mix/order/place-tpsl-order", body={
            "symbol": symbol, "productType": self.product_type, "marginCoin": self.margin_coin,
            "planType": "pos_loss", "triggerPrice": self._price(symbol, stop), "triggerType": "mark_price",
            "holdSide": "buy" if p.side == 1 else "sell"})
        self._positions = None
