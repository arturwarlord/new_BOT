import unittest

from compare_portfolios import replay, validate_configs
from bot.data import synthetic


class PortfolioComparisonTests(unittest.TestCase):
    def configs(self):
        sleeve = [{"symbol": "BTCUSDT", "strategy": "tsmom", "params": {"lookback": 126}}]
        common = {
            "granularity": "4H",
            "costs": {"fee_rate": 0.0006, "slippage_rate": 0.0005},
            "limits": {"max_total_leverage": 3.0, "max_daily_loss": 0.05},
            "sleeves": sleeve,
        }
        control = {**common, "risk": {"risk_pct": 0.01, "max_leverage": 2.0}}
        experiment = {**common, "risk": {"risk_pct": 0.005, "max_leverage": 2.0}}
        return control, experiment

    def test_accepts_same_portfolio_and_lower_v2_risk(self):
        control, experiment = self.configs()
        validate_configs(control, experiment)

    def test_rejects_different_sleeves(self):
        control, experiment = self.configs()
        experiment["sleeves"] = []
        with self.assertRaisesRegex(ValueError, "sleeves"):
            validate_configs(control, experiment)

    def test_rejects_v2_risk_not_lower(self):
        control, experiment = self.configs()
        experiment["risk"]["risk_pct"] = 0.01
        with self.assertRaisesRegex(ValueError, "lower"):
            validate_configs(control, experiment)

    def test_replay_produces_portfolio_curve_offline(self):
        c = synthetic(n=900, seed=23, drift=0.0002, vol=0.008)
        cfg = {
            "granularity": "4H",
            "risk": {"risk_pct": 0.005, "max_leverage": 2.0},
            "costs": {"fee_rate": 0.0006, "slippage_rate": 0.0005},
            "limits": {"max_total_leverage": 3.0, "max_daily_loss": 0.05},
            "sleeves": [{
                "symbol": "BTCUSDT",
                "strategy": "tsmom",
                "params": {"lookback": 126, "atr_stop": 3.0, "allow_short": True},
            }],
        }
        result = replay(cfg, {"BTCUSDT": (c, [0.0] * len(c))}, 900, 10_000.0)
        self.assertEqual(result["risk_pct"], 0.005)
        self.assertGreater(len(result["series"]), 1)
        self.assertTrue(all(point[1] > 0 for point in result["series"]))


if __name__ == "__main__":
    unittest.main()
