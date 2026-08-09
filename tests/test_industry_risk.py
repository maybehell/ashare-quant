import unittest

import pandas as pd

from ashare_quant.industry_risk import build_industry_risk_audit
from ashare_quant.readiness import build_live_readiness, build_practical_readiness


class IndustryRiskTests(unittest.TestCase):
    def policy_config(self, tolerance: float = 0.015) -> dict:
        return {
            "strategy": {
                "industry_relative_cap": {
                    "enabled": True,
                    "max_active_weight": 0.10,
                    "drift_tolerance": tolerance,
                }
            },
            "universe": {"scope": "curated"},
            "data": {},
            "live_guard": {
                "enabled": True,
                "min_price_symbol_count": 0,
                "require_cost_stress": False,
                "require_candidate_forward": False,
                "require_shadow_forward": False,
                "require_metadata": False,
            },
            "practical_guard": {
                "enabled": True,
                "require_current_evidence": False,
                "min_price_symbol_count": 0,
                "require_cost_stress": False,
                "require_out_of_sample": False,
                "require_walk_forward": False,
                "require_metadata": False,
            },
        }

    def exposure(self, tech_weight: float) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "industry": "tech",
                    "weight": tech_weight,
                    "reference_weight": 0.50,
                    "active_cap": 0.60,
                },
                {
                    "industry": "bank",
                    "weight": 1.0 - tech_weight,
                    "reference_weight": 0.50,
                    "active_cap": 0.60,
                },
            ]
        )

    def test_absolute_post_trade_cap_audits_round_lot_drift(self):
        config = {
            "strategy": {
                "max_industry_weight": 0.30,
                "post_trade_industry_cap": {
                    "enabled": True,
                    "drift_tolerance": 0.015,
                },
            }
        }
        exposure = pd.DataFrame(
            [
                {"industry": "electronics", "weight": 0.3103},
                {"industry": "bank", "weight": 0.20},
            ]
        )

        audit = build_industry_risk_audit(exposure, config).set_index("check")

        self.assertAlmostEqual(
            float(audit.loc["industry_max_cap_overrun", "value"]),
            0.0103,
        )
        self.assertTrue(bool(audit.loc["industry_risk_ready", "passed"]))
        self.assertEqual(
            audit.loc["industry_strict_cap_violation_count", "severity"],
            "warning",
        )

    def test_absolute_post_trade_cap_blocks_excessive_drift(self):
        config = {
            "strategy": {
                "max_industry_weight": 0.30,
                "post_trade_industry_cap": {
                    "enabled": True,
                    "drift_tolerance": 0.015,
                },
            }
        }
        exposure = pd.DataFrame(
            [{"industry": "electronics", "weight": 0.32}]
        )

        audit = build_industry_risk_audit(exposure, config).set_index("check")

        self.assertFalse(bool(audit.loc["industry_risk_ready", "passed"]))
        self.assertEqual(audit.loc["industry_max_cap_overrun", "severity"], "blocker")

    def test_disabled_policy_is_not_applicable(self):
        audit = build_industry_risk_audit(pd.DataFrame(), {"strategy": {}})
        row = audit.set_index("check").loc["industry_risk_ready"]

        self.assertTrue(bool(row["passed"]))
        self.assertEqual(row["value"], "not_applicable")

    def test_small_post_trade_drift_passes_with_warning(self):
        audit = build_industry_risk_audit(self.exposure(0.6095), self.policy_config())
        rows = audit.set_index("check")

        self.assertEqual(int(rows.loc["industry_strict_cap_violation_count", "value"]), 1)
        self.assertEqual(rows.loc["industry_strict_cap_violation_count", "severity"], "warning")
        self.assertAlmostEqual(float(rows.loc["industry_max_cap_overrun", "value"]), 0.0095)
        self.assertTrue(bool(rows.loc["industry_risk_ready", "passed"]))

    def test_severe_post_trade_drift_blocks(self):
        audit = build_industry_risk_audit(self.exposure(0.63), self.policy_config())
        rows = audit.set_index("check")

        self.assertEqual(int(rows.loc["industry_drift_tolerance_violation_count", "value"]), 1)
        self.assertFalse(bool(rows.loc["industry_risk_ready", "passed"]))
        self.assertIn("tech", rows.loc["industry_drift_tolerance_violation_count", "message"])

    def test_enabled_policy_blocks_missing_exposure(self):
        audit = build_industry_risk_audit(pd.DataFrame(), self.policy_config())
        row = audit.set_index("check").loc["industry_risk_ready"]

        self.assertFalse(bool(row["passed"]))
        self.assertEqual(row["value"], "missing_exposure")

    def test_readiness_builders_include_blocking_industry_gate(self):
        config = self.policy_config()
        failed_audit = build_industry_risk_audit(self.exposure(0.63), config)
        metrics = {
            "excess_total_return": 0.1,
            "max_drawdown": -0.1,
            "annualized_turnover": 1.0,
        }
        empty = pd.DataFrame()

        live = build_live_readiness(
            config,
            metrics,
            empty,
            empty,
            empty,
            industry_risk_audit=failed_audit,
        ).set_index("check")
        practical = build_practical_readiness(
            config,
            metrics,
            empty,
            empty,
            empty,
            industry_risk_audit=failed_audit,
        ).set_index("check")

        self.assertFalse(bool(live.loc["post_trade_industry_risk", "passed"]))
        self.assertFalse(bool(practical.loc["post_trade_industry_risk", "passed"]))


if __name__ == "__main__":
    unittest.main()
