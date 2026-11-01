import unittest

from bot.data import synthetic
from bot.engine import Costs, Risk
from walkforward_portfolio import evaluate, select_candidate


class WalkForwardPortfolioTests(unittest.TestCase):
    def config(self):
        return {
            "granularity": "4H",
            "risk": {"risk_pct": 0.01, "max_leverage": 2.0},
            "costs": {"fee_rate": 0.0006, "slippage_rate": 0.0005},
            "limits": {"max_total_leverage": 3.0, "max_daily_loss": 0.05},
            "sleeves": [{
                "symbol": "BTCUSDT",
                "strategy": "donchian",
                "params": {"entry_n": 20, "exit_n": 10, "atr_stop": 2.5, "allow_short": True},
            }],
        }

    def test_future_mutation_does_not_change_training_selection(self):
        c = synthetic(n=1800, seed=17, drift=0.0001, vol=0.01)
        sleeve = self.config()["sleeves"][0]
        first, _ = select_candidate(c, sleeve, 600, 1100, Costs(), Risk(0.01, 2.0))
        changed = synthetic(n=1800, seed=99, drift=-0.0003, vol=0.03)
        changed.ts[:1100] = c.ts[:1100]
        changed.open[:1100] = c.open[:1100]
        changed.high[:1100] = c.high[:1100]
        changed.low[:1100] = c.low[:1100]
        changed.close[:1100] = c.close[:1100]
        changed.volume[:1100] = c.volume[:1100]
        second, _ = select_candidate(changed, sleeve, 600, 1100, Costs(), Risk(0.01, 2.0))
        self.assertEqual(
            None if first is None else (first.strategy, first.params),
            None if second is None else (second.strategy, second.params),
        )

    def test_walkforward_compares_same_selections_at_two_risk_levels(self):
        c = synthetic(n=1900, seed=23, drift=0.0002, vol=0.009)
        result = evaluate(
            self.config(), {"BTCUSDT": (c, [0.0] * len(c))},
            train_bars=500, test_bars=200, warmup=600,
            initial=10_000.0, cost_multiplier=1.0,
        )
        self.assertGreaterEqual(result["fold_count"], 2)
        self.assertTrue(result["same_selected_parameters_for_risk_comparison"])
        self.assertEqual(result["control"]["risk_pct"], 0.01)
        self.assertEqual(result["v2"]["risk_pct"], 0.005)
        self.assertEqual(result["control"]["fold_count"], result["v2"]["fold_count"])
        self.assertEqual(len(result["folds"]), result["fold_count"])


if __name__ == "__main__":
    unittest.main()
