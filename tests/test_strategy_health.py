
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.strategy_health import (
    build_strategy_health,
    configured_promotion_decision,
    forward_factor_degradation_status,
    strategy_health_allows_paper,
)


class StrategyHealthTests(unittest.TestCase):
    def base_config(self):
        return {
            "strategy": {"name": "current"},
            "strategy_health": {
                "min_reliability_score": 70,
                "min_latest_rolling_excess_return": -0.05,
                "recent_rolling_windows": 3,
                "min_recent_rolling_positive_ratio": 0.5,
            },
            "live_guard": {
                "min_forward_trading_days": 60,
                "min_forward_excess_return": 0.0,
                "min_shadow_observed_signals": 20,
                "min_shadow_avg_excess_return": 0.0,
                "allow_optimized_on_current_cache": False,
            },
        }

    def reliability(self, score=92.9, optimized=True):
        return pd.DataFrame(
            [
                {"item": "reliability_score", "value": score, "passed": True},
                {"item": "optimized_on_current_cache", "value": optimized, "passed": not optimized},
            ]
        )

    def readiness(self, passed=True):
        return pd.DataFrame([{"check": "ok", "passed": passed, "value": "ok", "threshold": "ok", "message": ""}])

    def promotion(
        self,
        can_promote=False,
        candidate="candidate",
        decision="observe_only",
        current_candidate_id="current-id",
        can_paper_trade=False,
        candidate_id="current-id",
    ):
        return pd.DataFrame(
            [
                {
                    "decision": decision,
                    "can_promote": can_promote,
                    "can_paper_trade": can_paper_trade,
                    "candidate_id": candidate_id,
                    "candidate_strategy_name": candidate,
                    "current_strategy_name": "current",
                    "current_candidate_id": current_candidate_id,
                }
            ]
        )

    def factor_summary(self, passed=True, value="passed"):
        return pd.DataFrame([{"check": "factor_robustness_status", "passed": passed, "value": value, "threshold": "passed", "message": ""}])

    def test_collecting_forward_and_shadow_allows_paper_only_observe(self):
        health = build_strategy_health(
            self.base_config(),
            strategy_reliability=self.reliability(92.9, optimized=True),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}, {"excess_return": 0.02}, {"excess_return": 0.03}]),
            forward_progress=pd.DataFrame(
                [
                    {
                        "candidate_id": "current-id",
                        "observed_forward_trading_days": 9,
                        "required_forward_trading_days": 60,
                        "forward_excess_return": -0.004,
                        "required_forward_excess_return": 0.0,
                    }
                ]
            ),
            shadow_forward_audit=pd.DataFrame([{"status": "ok", "excess_return": 0.01, "candidate_id": "current-id"}]),
            promotion_decision=self.promotion(can_promote=False),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
            factor_robustness_summary=self.factor_summary(False, "review_required"),
        )
        values = dict(zip(health["check"], health["value"]))
        passed = dict(zip(health["check"], health["passed"]))

        self.assertTrue(bool(passed["paper_orders_allowed"]))
        self.assertFalse(bool(passed["factor_robustness_status"]))
        self.assertFalse(bool(passed["strict_live_allowed"]))
        self.assertEqual(values["strategy_health_status"], "paper_only_observe")
        self.assertEqual(strategy_health_allows_paper(health), (True, "True"))
        self.assertEqual(values["deployment_evidence_grade"], "\u89c2\u5bdf\u7ea7")
        self.assertEqual(float(values["deployment_evidence_score"]), 55.0)

    def test_forward_factor_degradation_waits_for_minimum_sample(self):
        monitor = pd.DataFrame([
            {"candidate_id": "current-id", "forward_trading_days": 2, "configured_weight": 0.75, "member_blend_weight": 1.0, "status": "adverse"},
            {"candidate_id": "current-id", "forward_trading_days": 2, "configured_weight": 0.25, "member_blend_weight": 1.0, "status": "supportive"},
        ])

        passed, value = forward_factor_degradation_status(
            monitor,
            "current-id",
            min_trading_days=20,
            adverse_weight_limit=0.67,
        )

        self.assertTrue(passed)
        self.assertEqual(value, "collecting;days=2/20")

    def test_forward_factor_degradation_requires_bound_candidate_identity(self):
        monitor = pd.DataFrame([
            {"candidate_id": "frozen-id", "forward_trading_days": 24, "configured_weight": 1.0, "member_blend_weight": 1.0, "status": "supportive"},
        ])

        self.assertEqual(
            forward_factor_degradation_status(monitor, "", min_trading_days=20, adverse_weight_limit=0.67),
            (False, "current_candidate_unbound"),
        )
        self.assertEqual(
            forward_factor_degradation_status(monitor, "other-id", min_trading_days=20, adverse_weight_limit=0.67),
            (False, "candidate_identity_mismatch"),
        )
    def test_forward_factor_degradation_blocks_dominant_adverse_weight(self):
        monitor = pd.DataFrame([
            {"candidate_id": "current-id", "forward_trading_days": 24, "configured_weight": 0.15, "member_blend_weight": 1.0, "status": "adverse"},
            {"candidate_id": "current-id", "forward_trading_days": 24, "configured_weight": 0.25, "member_blend_weight": 1.0, "status": "supportive"},
            {"candidate_id": "current-id", "forward_trading_days": 24, "configured_weight": 0.60, "member_blend_weight": 1.0, "status": "adverse"},
        ])

        passed, value = forward_factor_degradation_status(
            monitor,
            "current-id",
            min_trading_days=20,
            adverse_weight_limit=0.67,
        )

        self.assertFalse(passed)
        self.assertEqual(value, "days=24;adverse_weight=0.7500")

    def test_forward_factor_degradation_blocks_paper_orders(self):
        config = self.base_config()
        config["strategy_health"]["forward_factor_min_trading_days"] = 20
        config["strategy_health"]["forward_factor_adverse_weight_blocker"] = 0.67
        monitor = pd.DataFrame([
            {"candidate_id": "current-id", "forward_trading_days": 24, "configured_weight": 0.70, "member_blend_weight": 1.0, "status": "adverse"},
            {"candidate_id": "current-id", "forward_trading_days": 24, "configured_weight": 0.30, "member_blend_weight": 1.0, "status": "supportive"},
        ])

        health = build_strategy_health(
            config,
            strategy_reliability=self.reliability(90, optimized=False),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}] * 3),
            promotion_decision=self.promotion(),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
            forward_factor_monitor=monitor,
        )

        row = health[health["check"] == "forward_factor_degradation_clear"].iloc[0]
        self.assertFalse(bool(row["passed"]))
        paper_row = health[health["check"] == "paper_orders_allowed"].iloc[0]
        self.assertFalse(bool(paper_row["passed"]))

    def test_optimized_guard_uses_strict_candidate_validation_result(self):
        for strict_passed, expected_passed, expected_value in [
            (False, False, True),
            (True, True, "candidate_validated"),
        ]:
            with self.subTest(strict_passed=strict_passed):
                health = build_strategy_health(
                    self.base_config(),
                    strategy_reliability=self.reliability(90, optimized=True),
                    rolling_validation=pd.DataFrame([{"excess_return": 0.01}] * 3),
                    promotion_decision=self.promotion(can_promote=False),
                    practical_readiness=self.readiness(True),
                    live_readiness=pd.DataFrame([{
                        "check": "optimized_on_current_cache",
                        "passed": strict_passed,
                        "value": "candidate_validated" if strict_passed else True,
                        "threshold": "false or validated_candidate",
                        "message": "",
                    }]),
                )

                row = health[health["check"] == "optimized_on_current_cache_guard"].iloc[0]
                self.assertEqual(bool(row["passed"]), expected_passed)
                self.assertEqual(row["value"], expected_value)
    def test_recent_rolling_deterioration_pauses_paper_orders(self):
        health = build_strategy_health(
            self.base_config(),
            strategy_reliability=self.reliability(90, optimized=False),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}, {"excess_return": -0.08}, {"excess_return": -0.09}]),
            forward_progress=pd.DataFrame(),
            shadow_forward_audit=pd.DataFrame(),
            promotion_decision=self.promotion(can_promote=False),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
        )
        values = dict(zip(health["check"], health["value"]))
        passed = dict(zip(health["check"], health["passed"]))

        self.assertFalse(bool(passed["latest_rolling_excess_return"]))
        self.assertFalse(bool(passed["paper_orders_allowed"]))
        self.assertEqual(values["strategy_health_status"], "pause_paper_orders")

        self.assertEqual(values["deployment_evidence_grade"], "\u6682\u505c\u7ea7")
        self.assertEqual(float(values["deployment_evidence_score"]), 30.0)
    def test_completed_forward_underperformance_pauses_paper_orders(self):
        config = self.base_config()
        shadow = pd.DataFrame([{"status": "ok", "observed_trading_days": 5, "excess_return": 0.01, "candidate_id": "current-id"} for _ in range(20)])
        health = build_strategy_health(
            config,
            strategy_reliability=self.reliability(90, optimized=False),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}, {"excess_return": 0.02}, {"excess_return": 0.03}]),
            forward_progress=pd.DataFrame(
                [
                    {
                        "candidate_id": "current-id",
                        "observed_forward_trading_days": 60,
                        "required_forward_trading_days": 60,
                        "forward_excess_return": -0.01,
                        "required_forward_excess_return": 0.0,
                    }
                ]
            ),
            shadow_forward_audit=shadow,
            promotion_decision=self.promotion(can_promote=True, candidate="current", decision="allow_manual_live_review"),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(True),
            factor_robustness_summary=self.factor_summary(True),
        )
        passed = dict(zip(health["check"], health["passed"]))

        self.assertFalse(bool(passed["forward_no_confirmed_failure"]))
        self.assertFalse(bool(passed["paper_orders_allowed"]))
        self.assertEqual(strategy_health_allows_paper(health)[0], False)

    def test_missing_health_frame_does_not_allow_paper(self):
        self.assertEqual(strategy_health_allows_paper(pd.DataFrame()), (False, "missing"))


    def test_early_forward_warning_is_visible_but_does_not_retime_parameters(self):
        health = build_strategy_health(
            self.base_config(),
            strategy_reliability=self.reliability(90, optimized=False),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}, {"excess_return": 0.02}, {"excess_return": 0.03}]),
            forward_progress=pd.DataFrame([{
                "candidate_id": "current-id",
                "status": "early_forward_warning",
                "observed_forward_trading_days": 17,
                "required_forward_trading_days": 60,
                "forward_excess_return": -0.041,
                "required_forward_excess_return": 0.0,
            }]),
            shadow_forward_audit=pd.DataFrame(),
            promotion_decision=self.promotion(can_promote=False),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
            factor_robustness_summary=self.factor_summary(False, "review_required"),
        )
        passed = dict(zip(health["check"], health["passed"]))
        self.assertFalse(bool(passed["forward_early_warning_clear"]))
        self.assertTrue(bool(passed["forward_no_early_degradation"]))
        self.assertTrue(bool(passed["paper_orders_allowed"]))

    def test_early_forward_degradation_pauses_paper(self):
        health = build_strategy_health(
            self.base_config(),
            strategy_reliability=self.reliability(90, optimized=False),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}, {"excess_return": 0.02}, {"excess_return": 0.03}]),
            forward_progress=pd.DataFrame([{
                "candidate_id": "current-id",
                "status": "early_forward_degradation",
                "observed_forward_trading_days": 8,
                "required_forward_trading_days": 60,
                "forward_excess_return": -0.06,
                "required_forward_excess_return": 0.0,
            }]),
            shadow_forward_audit=pd.DataFrame(),
            promotion_decision=self.promotion(can_promote=False),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
            factor_robustness_summary=self.factor_summary(False, "review_required"),
        )
        passed = dict(zip(health["check"], health["passed"]))
        self.assertFalse(bool(passed["forward_no_early_degradation"]))
        self.assertFalse(bool(passed["paper_orders_allowed"]))

    def test_foreign_forward_evidence_is_not_applied_to_current_strategy(self):
        health = build_strategy_health(
            self.base_config(),
            strategy_reliability=self.reliability(90, optimized=False),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}]),
            forward_progress=pd.DataFrame([{
                "candidate_id": "challenger-id",
                "status": "early_forward_degradation",
                "observed_forward_trading_days": 60,
                "required_forward_trading_days": 60,
                "forward_excess_return": -0.20,
            }]),
            promotion_decision=self.promotion(current_candidate_id="current-id"),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
        )
        passed = dict(zip(health["check"], health["passed"]))
        values = dict(zip(health["check"], health["value"]))

        self.assertFalse(bool(passed["forward_evidence_matches_current_strategy"]))
        self.assertTrue(bool(passed["forward_no_early_degradation"]))
        self.assertEqual(values["forward_evidence_complete"], "missing")

    def test_shadow_counts_only_current_candidate(self):
        shadow = pd.DataFrame([
            {"status": "ok", "observed_trading_days": 10, "excess_return": 0.20, "candidate_id": "old-id"},
            {"status": "ok", "observed_trading_days": 2, "excess_return": 0.50, "candidate_id": "current-id"},
            {"status": "ok", "observed_trading_days": 5, "excess_return": 0.01, "candidate_id": "current-id"},
        ])
        config = self.base_config()
        config["live_guard"]["min_shadow_observed_signals"] = 2
        config["live_guard"]["min_shadow_observed_days_per_signal"] = 5
        health = build_strategy_health(
            config,
            strategy_reliability=self.reliability(90, optimized=False),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}]),
            forward_progress=pd.DataFrame([{
                "candidate_id": "current-id",
                "status": "collecting_forward_data",
                "observed_forward_trading_days": 1,
                "required_forward_trading_days": 60,
                "forward_excess_return": 0.0,
            }]),
            shadow_forward_audit=shadow,
            promotion_decision=self.promotion(),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
        )
        values = dict(zip(health["check"], health["value"]))
        passed = dict(zip(health["check"], health["passed"]))

        self.assertFalse(bool(passed["shadow_evidence_complete"]))
        self.assertEqual(values["shadow_evidence_complete"], 1)

    def test_isolated_candidate_uses_verified_local_identity_for_observation(self):
        config = self.base_config()
        health = build_strategy_health(
            config,
            strategy_reliability=self.reliability(90, optimized=True),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}]),
            forward_progress=pd.DataFrame(
                [
                    {
                        "candidate_id": "isolated-id",
                        "status": "collecting_forward_data",
                        "observed_forward_trading_days": 6,
                        "required_forward_trading_days": 60,
                        "forward_excess_return": 0.01,
                    }
                ]
            ),
            shadow_forward_audit=pd.DataFrame(
                [{"status": "ok", "observed_trading_days": 6, "excess_return": 0.01, "candidate_id": "isolated-id"}]
            ),
            promotion_decision=pd.DataFrame(),
            practical_readiness=self.readiness(True),
            live_readiness=pd.DataFrame(
                [
                    {"check": "candidate_exists", "passed": True, "value": "isolated-id"},
                    {"check": "candidate_hash_match", "passed": True, "value": "match"},
                ]
            ),
            factor_robustness_summary=self.factor_summary(True),
        )
        values = dict(zip(health["check"], health["value"]))
        passed = dict(zip(health["check"], health["passed"]))

        self.assertTrue(bool(passed["forward_evidence_matches_current_strategy"]))
        self.assertTrue(bool(passed["shadow_evidence_matches_current_candidate"]))
        self.assertEqual(values["forward_evidence_complete"], 6)
        self.assertEqual(values["shadow_evidence_complete"], 1)
        self.assertFalse(bool(passed["promotion_decision_allows_strict"]))
        self.assertFalse(bool(passed["strict_live_allowed"]))

    def test_isolated_candidate_does_not_trust_unverified_local_identity(self):
        health = build_strategy_health(
            self.base_config(),
            strategy_reliability=self.reliability(90, optimized=True),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}]),
            forward_progress=pd.DataFrame(
                [{"candidate_id": "isolated-id", "status": "collecting_forward_data", "observed_forward_trading_days": 6}]
            ),
            shadow_forward_audit=pd.DataFrame(
                [{"status": "ok", "observed_trading_days": 6, "excess_return": 0.01, "candidate_id": "isolated-id"}]
            ),
            promotion_decision=pd.DataFrame(),
            practical_readiness=self.readiness(True),
            live_readiness=pd.DataFrame(
                [
                    {"check": "candidate_exists", "passed": True, "value": "different-id"},
                    {"check": "candidate_hash_match", "passed": True, "value": "match"},
                ]
            ),
        )
        passed = dict(zip(health["check"], health["passed"]))

        self.assertFalse(bool(passed["forward_evidence_matches_current_strategy"]))
        self.assertFalse(bool(passed["shadow_evidence_matches_current_candidate"]))


    def central_promotion_health(self, can_paper_trade: bool) -> pd.DataFrame:
        config = self.base_config()
        config["validation"] = {
            "require_central_promotion_for_paper": True,
            "promotion_decision_path": "outputs/strategy_promotion_decision.csv",
        }
        return build_strategy_health(
            config,
            strategy_reliability=self.reliability(90, optimized=True),
            rolling_validation=pd.DataFrame([{"excess_return": 0.01}]),
            forward_progress=pd.DataFrame(
                [
                    {
                        "candidate_id": "current-id",
                        "status": "collecting_forward_data",
                        "observed_forward_trading_days": 3,
                        "required_forward_trading_days": 60,
                        "forward_excess_return": -0.01,
                    }
                ]
            ),
            promotion_decision=self.promotion(
                candidate="current",
                can_paper_trade=can_paper_trade,
                candidate_id="current-id",
            ),
            practical_readiness=self.readiness(True),
            live_readiness=self.readiness(False),
        )

    def test_required_central_promotion_blocks_candidate_paper(self):
        health = self.central_promotion_health(False).set_index("check")

        self.assertFalse(bool(health.loc["promotion_decision_allows_paper", "passed"]))
        self.assertFalse(bool(health.loc["paper_orders_allowed", "passed"]))

    def test_matching_central_promotion_allows_candidate_paper_observation(self):
        health = self.central_promotion_health(True).set_index("check")

        self.assertTrue(bool(health.loc["forward_evidence_matches_current_strategy", "passed"]))
        self.assertTrue(bool(health.loc["promotion_decision_allows_paper", "passed"]))
        self.assertTrue(bool(health.loc["paper_orders_allowed", "passed"]))

    def test_configured_promotion_decision_prefers_central_artifact(self):
        root = Path.cwd() / "test_outputs" / "strategy_health_central_decision"
        local = root / "candidate"
        central = root / "champion" / "strategy_promotion_decision.csv"
        local.mkdir(parents=True, exist_ok=True)
        central.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"decision": "local"}]).to_csv(
            local / "strategy_promotion_decision.csv", index=False
        )
        pd.DataFrame([{"decision": "central"}]).to_csv(central, index=False)
        config = {
            "paths": {"output_dir": str(local)},
            "validation": {"promotion_decision_path": str(central)},
        }

        resolved = configured_promotion_decision(config, local)

        self.assertEqual(resolved.loc[0, "decision"], "central")

if __name__ == "__main__":
    unittest.main()
