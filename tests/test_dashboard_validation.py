import unittest

from dashboard import validation_section


class DashboardWalkForwardTests(unittest.TestCase):
    def test_validation_section_renders_cost_scenarios_and_fold_results(self):
        report = {
            "generated_at": "2026-11-01T23:15:00+00:00",
            "caveats": ["Historical simulation only."],
            "scenarios": [{
                "cost_multiplier": 1.0,
                "fold_count": 1,
                "test_from": "2025-07-13T00:00:00+00:00",
                "test_to": "2025-10-11T00:00:00+00:00",
                "control": {
                    "return": 0.10, "max_drawdown": 0.05, "sharpe": 1.5,
                    "positive_folds": 1, "fold_count": 1, "fold_returns": [0.10],
                },
                "v2": {
                    "return": 0.05, "max_drawdown": 0.025, "sharpe": 1.4,
                    "positive_folds": 1, "fold_count": 1, "fold_returns": [0.05],
                },
                "folds": [{
                    "fold": 1,
                    "test_from": "2025-07-13T00:00:00+00:00",
                    "test_to_exclusive": "2025-10-11T00:00:00+00:00",
                }],
            }, {
                "cost_multiplier": 2.0,
                "fold_count": 1,
                "test_from": "2025-07-13T00:00:00+00:00",
                "test_to": "2025-10-11T00:00:00+00:00",
                "control": {
                    "return": 0.08, "max_drawdown": 0.05, "sharpe": 1.2,
                    "positive_folds": 1, "fold_count": 1, "fold_returns": [0.08],
                },
                "v2": {
                    "return": 0.04, "max_drawdown": 0.025, "sharpe": 1.1,
                    "positive_folds": 1, "fold_count": 1, "fold_returns": [0.04],
                },
                "folds": [],
            }],
        }
        html = validation_section(report)
        self.assertIn("Независимая walk-forward проверка", html)
        self.assertIn("2×", html)
        self.assertIn("Контроль 1%", html)
        self.assertIn("V2 0,5%", html)
        self.assertIn("2025-07-13", html)
        self.assertIn("Historical simulation only.", html)

    def test_empty_validation_hides_section(self):
        self.assertEqual(validation_section({}), "")


if __name__ == "__main__":
    unittest.main()
