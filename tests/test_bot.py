import base64
import hashlib
import hmac
import json
import math
import os
import tempfile
import unittest

from bot import indicators as ind
from bot import strategies as S
from unittest import mock

from bot.broker import BitgetBroker, BitgetError, PaperBroker
from bot.data import Candles, synthetic
from bot.engine import Costs, Risk, Signals, compute_metrics, run
from bot.scanner import Filters, portfolio_curve, rank, select
from bot.trader import Limits, State, cycle
from bot.walkforward import AUTO, fold_bounds, walk_forward

ZERO = Costs(fee_rate=0.0, slippage_rate=0.0)
NaN = float("nan")


def candles_from_close(close):
    n = len(close)
    open_ = [close[0]] + close[:-1]
    return Candles(list(range(n)), open_, [max(o, c) + 0.5 for o, c in zip(open_, close)],
                   [min(o, c) - 0.5 for o, c in zip(open_, close)], list(close), [1.0] * n)


class IndicatorTests(unittest.TestCase):
    def test_ema_sma_of_constant(self):
        self.assertTrue(all(abs(x - 5) < 1e-12 for x in ind.ema([5.0] * 30, 10)[9:]))
        self.assertTrue(all(abs(x - 5) < 1e-12 for x in ind.sma([5.0] * 30, 10)[9:]))
        self.assertTrue(math.isnan(ind.sma([5.0] * 30, 10)[8]))

    def test_prior_extremes_exclude_current_bar_and_match_naive(self):
        v = [1, 2, 3, 10, 4]
        self.assertEqual(ind.prior_highest(v, 3)[3], 3)
        self.assertEqual(ind.prior_highest(v, 3)[4], 10)
        self.assertEqual(ind.prior_lowest(v, 3)[3], 1)
        c = synthetic(500, seed=1)
        fast = ind.prior_highest(c.high, 20)
        for i in range(20, 500):
            self.assertEqual(fast[i], max(c.high[i - 20:i]))

    def test_rsi_adx_ranges(self):
        c = synthetic(800, seed=3)
        for x in ind.rsi(c.close, 14) + ind.adx(c.high, c.low, c.close, 14):
            if x == x:
                self.assertTrue(0 <= x <= 100)

    def test_rsi_extremes(self):
        self.assertAlmostEqual(ind.rsi([float(i) for i in range(1, 60)], 14)[-1], 100.0)

    def test_supertrend_follows_trend(self):
        up = candles_from_close([100 + i for i in range(200)])
        _, d = ind.supertrend(up.high, up.low, up.close, 10, 3.0)
        self.assertEqual(d[-1], 1)

    def test_indicators_are_causal(self):
        c = synthetic(600, seed=8)
        cut = 400
        short = c.slice(0, cut)
        for f in (lambda x: ind.atr(x.high, x.low, x.close, 14), lambda x: ind.adx(x.high, x.low, x.close, 14),
                  lambda x: ind.rsi(x.close, 14), lambda x: ind.supertrend(x.high, x.low, x.close, 10, 3)[0]):
            a, b = f(c)[:cut], f(short)
            for i in range(cut):
                if a[i] == a[i]:
                    self.assertAlmostEqual(a[i], b[i], places=9)


class EngineTests(unittest.TestCase):
    def test_no_lookahead_for_every_strategy(self):
        a = synthetic(1500, seed=11, drift=0.0005)
        b = synthetic(1500, seed=11, drift=0.0005)
        m = 1100
        for i in range(m + 1, 1500):
            b.open[i] *= 1.3; b.high[i] *= 1.5; b.low[i] *= 0.7; b.close[i] *= 1.2
        for name, st in S.STRATEGIES.items():
            p = st.default()
            ra = run(a, st.signals(S.IndicatorCache(a), p), Costs(), close_at_end=False)
            rb = run(b, st.signals(S.IndicatorCache(b), p), Costs(), close_at_end=False)
            for i in range(m):
                self.assertAlmostEqual(ra.equity[i], rb.equity[i], places=6, msg=f"{name} bar {i}")

    def test_stop_gap_fills_at_open_and_locks_side(self):
        c = candles_from_close([100.0 + i for i in range(60)])
        c.open[50], c.high[50], c.low[50], c.close[50] = 70.0, 71.0, 65.0, 68.0
        sig = Signals([1] * 60, [2.0] * 60)
        res = run(c, sig, ZERO)
        stops = [t for t in res.trades if t.reason == "stop"]
        self.assertEqual(len(stops), 1)
        self.assertLessEqual(stops[0].exit_price, 70.0)
        self.assertEqual(len(res.trades), 1, "must not re-enter the same side while the target is unchanged")

    def test_entry_event_rearms_after_stop(self):
        c = candles_from_close([100.0 + i for i in range(60)])
        c.open[30], c.high[30], c.low[30], c.close[30] = 80.0, 81.0, 79.0, 80.0
        ev = [0] * 60
        ev[40] = 1
        res = run(c, Signals([1] * 60, [2.0] * 60, entry_event=ev), ZERO)
        self.assertEqual(len(res.trades), 2)
        self.assertEqual(res.trades[1].entry_i, 41)

    def test_risk_sizing(self):
        c = candles_from_close([100.0] * 10 + [100.0 - i for i in range(1, 30)])
        res = run(c, Signals([1] * 39, [5.0] * 39), ZERO, Risk(0.01, 100), initial_equity=10_000)
        loss = -res.trades[0].pnl
        self.assertAlmostEqual(loss, 100.0, delta=1.0)  # 1% of equity at the stop

    def test_leverage_cap(self):
        c = candles_from_close([100.0] * 20)
        res = run(c, Signals([1] * 20, [1.0] * 20), ZERO, Risk(0.5, 2.0), close_at_end=False)
        self.assertLessEqual(res.open_position.qty * 100.0, 2.0 * 10_000 + 1e-6)

    def test_funding_charged_to_longs(self):
        c = candles_from_close([100.0] * 50)
        sig = Signals([1] * 50, [10.0] * 50)
        free = run(c, sig, ZERO).equity[-1]
        paid = run(c, sig, ZERO, funding=[0.001] * 50).equity[-1]
        self.assertLess(paid, free)

    def test_max_hold_exits(self):
        c = candles_from_close([100.0] * 80)
        res = run(c, Signals([1] * 80, [5.0] * 80, max_hold=10), ZERO)
        self.assertEqual(res.trades[0].exit_i - res.trades[0].entry_i, 10)
        self.assertEqual(len(res.trades), 1)

    def test_costs_only_hurt(self):
        c = synthetic(3000, seed=9)
        st = S.get("donchian")
        sig = st.signals(S.IndicatorCache(c), st.default())
        self.assertLessEqual(run(c, sig, Costs()).equity[-1], run(c, sig, ZERO).equity[-1])

    def test_trend_strategies_profit_in_strong_uptrend(self):
        c = synthetic(3000, seed=5, drift=0.003, vol=0.01)
        k = S.IndicatorCache(c)
        for name in ("donchian", "ema_trend", "supertrend", "tsmom"):
            st = S.get(name)
            res = run(c, st.signals(k, st.default()), ZERO, start=S.warmup_bars())
            self.assertGreater(res.equity[-1], res.equity[0], name)

    def test_metrics_bounds(self):
        c = synthetic(3000, seed=4)
        st = S.get("mean_reversion")
        m = compute_metrics(run(c, st.signals(S.IndicatorCache(c), st.default()), Costs()), 2190)
        self.assertTrue(0 <= m.max_drawdown <= 1 and 0 <= m.exposure <= 1)


class WalkForwardTests(unittest.TestCase):
    def test_fold_bounds_tile_the_future(self):
        f = fold_bounds(5000, 600, 2190, 540)
        for a, b in zip(f, f[1:]):
            self.assertEqual(b[1], a[2])
        self.assertLessEqual(f[-1][2], 5000)
        self.assertLess(5000 - f[-1][2], 180)
        g = fold_bounds(5200, 600, 2190, 540)
        self.assertEqual(g[-1][2], 5200)

    def test_walk_forward_shapes(self):
        c = synthetic(5000, seed=21)
        wf = walk_forward(c, 2190, names=["donchian", "tsmom"])
        self.assertEqual(set(wf), {"donchian", "tsmom", AUTO})
        for r in wf.values():
            self.assertEqual(len(r.oos_ts), len(r.oos_equity))
            self.assertEqual(len(r.fold_returns), len(fold_bounds(5000, 600, 2190, 540)))
            self.assertTrue(all(t2 > t1 for t1, t2 in zip(r.oos_ts, r.oos_ts[1:])))

    def test_grids_valid(self):
        for st in S.STRATEGIES.values():
            self.assertTrue(st.grid())
            for p in st.grid():
                self.assertEqual(set(p), set(st.default()))


class ScannerTests(unittest.TestCase):
    def test_select_respects_filters_and_one_per_symbol(self):
        results = []
        for i, sym in enumerate(["AAA", "BBB"]):
            c = synthetic(5000, seed=30 + i, drift=0.002, vol=0.01)
            results.append({"symbol": sym, "wf": walk_forward(c, 2190, names=["tsmom", "donchian"])})
        ranked = rank(results, 2190, Filters(min_sharpe=-99, min_positive_folds=0, min_trades=0,
                                              max_drawdown=1, min_folds=0))
        chosen = select(ranked, 5, Filters(max_correlation=1.0))
        self.assertEqual(len({c["symbol"] for c in chosen}), len(chosen))
        ts, eq = portfolio_curve(chosen)
        self.assertEqual(len(eq), len(ts) + 1)


class TraderTests(unittest.TestCase):
    def test_paper_cycle_opens_with_stop_and_respects_lock(self):
        up = synthetic(1200, seed=5, drift=0.003, vol=0.01)
        pf = {"granularity": "4H", "risk": {"risk_pct": 0.01, "max_leverage": 2.0},
              "sleeves": [{"symbol": "AAA", "strategy": "tsmom", "params": S.get("tsmom").default()}]}
        with tempfile.TemporaryDirectory() as d:
            broker = PaperBroker(os.path.join(d, "acct.json"), 10_000, ZERO)
            state = State(os.path.join(d, "state.json"))
            ev = cycle(pf, broker, state, Limits(), fetch=lambda s, g, n: up.slice(0, 1000))
            pos = broker.position("AAA")
            self.assertIsNotNone(pos, ev)
            self.assertEqual(pos.side, 1)
            self.assertLess(pos.stop, pos.entry)
            # crash through the stop on the next bar -> paper stop fires, side locked, no re-entry
            crash = up.slice(0, 1001)
            crash.low[1000] = pos.stop * 0.5
            crash.open[1000] = crash.close[1000] = pos.stop * 0.9
            ev = cycle(pf, broker, state, Limits(), fetch=lambda s, g, n: crash)
            self.assertIsNone(broker.position("AAA"), ev)
            self.assertEqual(state.s["locks"]["AAA"], 1)
            self.assertLess(broker.equity(), 10_000)


class BitgetBrokerTests(unittest.TestCase):
    ENV = {"BITGET_API_KEY": "k", "BITGET_API_SECRET": "s3cret", "BITGET_API_PASSPHRASE": "p"}

    def test_requires_keys(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(BitgetError):
                BitgetBroker()

    def test_signed_request_and_demo_header(self):
        seen = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"code": "00000", "data": [{"marginCoin": "USDT", "usdtEquity": "123.4"}]}).encode()

        def fake_urlopen(req, timeout=0):
            seen["req"] = req
            return Resp()

        with mock.patch.dict(os.environ, self.ENV, clear=True), \
                mock.patch("urllib.request.urlopen", fake_urlopen):
            b = BitgetBroker(demo=True)
            self.assertEqual(b.equity(), 123.4)
        req = seen["req"]
        h = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(h["paptrading"], "1")
        path = "/api/v2/mix/account/accounts?productType=USDT-FUTURES"
        self.assertTrue(req.full_url.endswith(path))
        expect = base64.b64encode(hmac.new(b"s3cret", (h["access-timestamp"] + "GET" + path).encode(),
                                           hashlib.sha256).digest()).decode()
        self.assertEqual(h["access-sign"], expect)


if __name__ == "__main__":
    unittest.main()
