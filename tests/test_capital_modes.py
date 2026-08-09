import unittest

import pandas as pd

from ashare_quant.capital_modes import build_capital_mode_assessment


class CapitalModeAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "strategy": {
                "top_n": 100,
                "max_stock_weight": 0.05,
            }
        }
        self.sensitivity = pd.DataFrame(
            [
                {
                    "initial_cash": 2500,
                    "final_position_count": 0,
                    "average_cash_ratio": 1.0,
                    "return_drag_vs_reference": -0.26,
                    "passed": False,
                },
                {
                    "initial_cash": 100000,
                    "final_position_count": 24,
                    "average_cash_ratio": 0.75,
                    "return_drag_vs_reference": -0.07,
                    "passed": False,
                },
                {
                    "initial_cash": 600000,
                    "final_position_count": 98,
                    "average_cash_ratio": 0.25,
                    "return_drag_vs_reference": 0.0,
                    "passed": True,
                },
            ]
        )

    def transition(self, **updates):
        row = {
            "snapshot_fresh": True,
            "current_total_asset": 2567.44,
            "available_cash": 2567.44,
            "minimum_full_strategy_equity": 600000,
            "canary_ready": False,
            "full_strategy_ready": False,
        }
        row.update(updates)
        return pd.DataFrame([row])

    def test_small_account_is_observation_only_and_does_not_adapt_strategy(self):
        result = build_capital_mode_assessment(
            self.transition(), self.sensitivity, self.config
        ).iloc[0]

        self.assertEqual(result["mode"], "observation_only")
        self.assertEqual(result["nearest_tested_capital"], 2500)
        self.assertEqual(result["nearest_tested_positions"], 0)
        self.assertEqual(result["minimum_diversified_positions"], 20)
        self.assertFalse(result["strategy_deployment_allowed"])

    def test_canary_affordability_never_enables_strategy_deployment(self):
        result = build_capital_mode_assessment(
            self.transition(canary_ready=True), self.sensitivity, self.config
        ).iloc[0]

        self.assertEqual(result["mode"], "canary_review_only")
        self.assertFalse(result["strategy_deployment_allowed"])

    def test_stale_snapshot_requires_refresh_even_when_old_flags_are_green(self):
        result = build_capital_mode_assessment(
            self.transition(snapshot_fresh=False, canary_ready=True, full_strategy_ready=True),
            self.sensitivity,
            self.config,
        ).iloc[0]

        self.assertEqual(result["mode"], "refresh_required")
        self.assertFalse(result["canary_ready"])
        self.assertFalse(result["full_strategy_ready"])
        self.assertFalse(result["strategy_deployment_allowed"])

    def test_current_snapshot_gate_overrides_stale_green_transition(self):
        result = build_capital_mode_assessment(
            self.transition(canary_ready=True),
            self.sensitivity,
            self.config,
            snapshot_current=False,
        ).iloc[0]

        self.assertEqual(result["mode"], "refresh_required")
        self.assertFalse(result["snapshot_fresh"])
        self.assertFalse(result["strategy_deployment_allowed"])

    def test_stale_sensitivity_blocks_full_strategy_but_preserves_canary_review(self):
        result = build_capital_mode_assessment(
            self.transition(
                current_total_asset=600000,
                available_cash=600000,
                canary_ready=True,
                full_strategy_ready=True,
            ),
            self.sensitivity,
            self.config,
            sensitivity_current=False,
        ).iloc[0]

        self.assertEqual(result["mode"], "canary_review_only")
        self.assertFalse(result["capital_sensitivity_current"])
        self.assertTrue(result["canary_ready"])
        self.assertTrue(result["full_strategy_ready"])
        self.assertFalse(result["strategy_deployment_allowed"])

    def test_full_strategy_mode_requires_fresh_full_capital_evidence(self):
        result = build_capital_mode_assessment(
            self.transition(
                current_total_asset=600000,
                available_cash=600000,
                full_strategy_ready=True,
            ),
            self.sensitivity,
            self.config,
        ).iloc[0]

        self.assertEqual(result["mode"], "full_strategy_review")
        self.assertTrue(result["strategy_deployment_allowed"])


if __name__ == "__main__":
    unittest.main()
