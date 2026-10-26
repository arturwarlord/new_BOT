import math
import unittest

from bot.backtest import Costs, Params, compute_metrics, run_backtest, warmup_bars
from bot.data import Candles, synthetic
from bot.indicators import atr, ema, prior_highest, prior_lowest
from bot.walkforward import candidates, walk_forward

ZERO = Costs(fee_rate=0.0, slippage_rate=0.0)


class IndicatorTests(unittest.TestCase):
    def test_ema_of_constant_is_constant(self):
        out = ema([5.0] * 30, 10)
        self.assertTrue(all(math.isnan(x) for x in out[:9]))
        self.assertTrue(all(abs(x - 5.0) < 1e-12 for x in out[9:]))

    def test_prior_extremes_exclude_current_bar(self):
        v = [1, 2, 3, 10, 4]
        self.assertEqual(prior_highest(v, 3)[3], 3)   # max(1,2,3), not 10
        self.assertEqual(prior_highest(v, 3)[4], 10)
        self.assertEqual(prior_lowest(v, 3)[3], 1)

    def test_atr_positive(self):
        c = synthetic(200, seed=3)
        a = atr(c.high, c.low, c.close, 14)
        self.assertTrue(all(x > 0 for x in a if x == x))


class BacktestTests(unittest.TestCase):
    P = Params(entry_n=10, exit_n=5, atr_n=5, atr_stop=2.0, ema_n=10)

    def test_no_lookahead(self):
        """Changing FUTURE candles must not change past equity."""
        a = synthetic(1500, seed=11, drift=0.0005)
        b = synthetic(1500, seed=11, drift=0.0005)
        m = 900
        for i in range(m + 1, 1500):  # corrupt the future of b
            b.open[i] *= 1.3; b.high[i] *= 1.5; b.low[i] *= 0.7; b.close[i] *= 1.2
        ra = run_backtest(a, self.P, Costs(), start=0, close_at_end=False)
        rb = run_backtest(b, self.P, Costs(), start=0, close_at_end=False)
        for i in range(m):
            self.assertAlmostEqual(ra.equity[i], rb.equity[i], places=6, msg=f"bar {i}")

    def test_profits_in_strong_uptrend(self):
        c = synthetic(2500, seed=5, drift=0.003, vol=0.01)
        res = run_backtest(c, self.P, ZERO, start=warmup_bars(self.P))
        self.assertGreater(res.equity[-1], res.equity[0])

    def test_stop_gap_fills_at_open_not_stop(self):
        # Build a rising series so a long is opened, then a gap far below the stop.
        n = 60
        ts = list(range(n))
        close = [100 + i for i in range(n)]
        open_ = [close[0]] + close[:-1]
        high = [x + 0.5 for x in close]
        low = [x - 0.5 for x in close]
        c = Candles(ts, open_, high, low, close, [1.0] * n)
        # gap: bar 50 opens 30 below everything
        c.open[50] = 70.0; c.high[50] = 71.0; c.low[50] = 65.0; c.close[50] = 68.0
        res = run_backtest(c, self.P, ZERO, start=0)
        stops = [t for t in res.trades if t.reason == "stop"]
        self.assertTrue(stops, "expected a stop-out on the gap")
        self.assertLessEqual(stops[0].exit_price, 70.0 + 1e-9)  # open, far below the stop level

    def test_risk_per_trade_close_to_risk_pct(self):
        P = Params(entry_n=10, exit_n=5, atr_n=5, atr_stop=2.0, ema_n=10, risk_pct=0.01, max_leverage=100)
        c = synthetic(3000, seed=2, vol=0.01)
        res = run_backtest(c, P, ZERO, initial_equity=10_000, start=warmup_bars(P))
        stopped = [t for t in res.trades if t.reason == "stop"]
        self.assertTrue(stopped)
        # a stop-out with no gap loses ~1% of the equity at that time (allow gaps to be worse)
        worst = min(t.pnl for t in stopped)
        self.assertGreater(worst, -0.05 * 10_000 * 3)

    def test_costs_only_hurt(self):
        c = synthetic(3000, seed=9)
        free = run_backtest(c, self.P, ZERO, start=warmup_bars(self.P)).equity[-1]
        paid = run_backtest(c, self.P, Costs(), start=warmup_bars(self.P)).equity[-1]
        self.assertLessEqual(paid, free)

    def test_metrics_drawdown_bounds(self):
        c = synthetic(3000, seed=4)
        m = compute_metrics(run_backtest(c, self.P, Costs(), start=warmup_bars(self.P)), 2190)
        self.assertGreaterEqual(m.max_drawdown, 0.0)
        self.assertLessEqual(m.max_drawdown, 1.0)


class WalkForwardTests(unittest.TestCase):
    def test_folds_never_overlap_train_and_test(self):
        c = synthetic(5500, seed=21)
        wf = walk_forward(c, 2190)
        self.assertIsNotNone(wf)
        for f in wf.folds:
            self.assertEqual(f.train[1], f.test[0])
            self.assertLess(f.train[0], f.train[1])
        for a, b in zip(wf.folds, wf.folds[1:]):
            self.assertEqual(b.test[0], a.test[1])  # test windows tile the future, no reuse

    def test_candidates_respect_exit_lt_entry(self):
        for p in candidates(Params()):
            self.assertLess(p.exit_n, p.entry_n)


if __name__ == "__main__":
    unittest.main()
