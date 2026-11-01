import unittest

from compare_portfolios import validate_configs


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


if __name__ == "__main__":
    unittest.main()
