from pathlib import Path
import json
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.compare import compare_backtest_outputs, current_data_manifest, discover_output_dirs, promotion_status, strategy_promotion_decision, strategy_promotion_report


class CompareTests(unittest.TestCase):
    def test_current_data_manifest_prefers_live_cache_fingerprint(self):
        frames = {
            "stocks": pd.DataFrame([{"symbol": "000001"}]),
            "prices": pd.DataFrame([{"date": "2026-01-02", "symbol": "000001", "close": 10.0}]),
            "fundamentals": pd.DataFrame(),
            "benchmark": pd.DataFrame([{"date": "2026-01-02", "symbol": "000300", "close": 4000.0}]),
        }
        with patch("ashare_quant.compare.current_manifest_frames", return_value=frames):
            current = current_data_manifest(
                {"strategy": {"name": "test"}},
                {"prices_sha256": "stale", "prices_rows": "999"},
            )

        self.assertNotEqual(current["prices_sha256"], "stale")
        self.assertEqual(current["prices_rows"], "1")

    def test_research_only_promotions_rank_by_recent_out_of_sample_excess(self):
        comparison = pd.DataFrame(
            [
                {"output_dir": "negative", "strategy_name": "negative"},
                {"output_dir": "positive", "strategy_name": "positive"},
            ]
        )
        rows = [
            {
                "strategy_name": "negative",
                "promotion_status": "research_only",
                "data_current": True,
                "out_of_sample_excess_return": -0.05,
                "bootstrap_probability_excess_positive": 0.90,
                "bootstrap_lower_95_excess_return": -0.10,
                "excess_total_return": 0.30,
                "information_ratio": 0.40,
            },
            {
                "strategy_name": "positive",
                "promotion_status": "research_only",
                "data_current": True,
                "out_of_sample_excess_return": 0.01,
                "bootstrap_probability_excess_positive": 0.60,
                "bootstrap_lower_95_excess_return": -0.20,
                "excess_total_return": 0.05,
                "information_ratio": 0.10,
            },
        ]
        with patch("ashare_quant.compare.promotion_row", side_effect=rows):
            result = strategy_promotion_report(comparison)

        self.assertEqual(result.iloc[0]["strategy_name"], "positive")

    def test_collecting_candidate_is_not_failed_by_short_term_negative_excess(self):
        status = promotion_status(
            practical_passed=True,
            strict_passed=False,
            stability_passed=True,
            forward_excess=-0.01,
            failed_live=["candidate_forward_trading_days", "candidate_forward_excess_return"],
            forward_status="collecting_forward_data",
        )

        self.assertEqual(status, "collecting_evidence")

    def test_explicit_forward_failure_status_is_underperforming(self):
        early = promotion_status(
            True,
            False,
            True,
            -0.06,
            ["candidate_early_forward_degradation"],
            forward_status="early_forward_degradation",
        )
        complete = promotion_status(
            True,
            False,
            True,
            -0.01,
            ["candidate_forward_excess_return"],
            forward_status="failed_forward_excess",
        )

        self.assertEqual(early, "forward_underperforming")
        self.assertEqual(complete, "forward_underperforming")

    def test_compare_backtest_outputs_sorts_by_excess_return(self):
        root = Path.cwd() / "test_outputs" / "compare"
        base = root / "outputs"
        alt = root / "outputs_alt"
        for path, name, excess in [(base, "base", 0.01), (alt, "alt", 0.05)]:
            path.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"metric": "total_return", "value": excess + 0.1},
                    {"metric": "benchmark_total_return", "value": 0.1},
                    {"metric": "excess_total_return", "value": excess},
                    {"metric": "max_drawdown", "value": -0.1},
                    {"metric": "information_ratio", "value": 0.2},
                ]
            ).to_csv(path / "performance_metrics.csv", index=False)
            pd.DataFrame([
                {"item": "strategy_name", "value": name},
                {"item": "stocks_sha256", "value": "stocks-current"},
                {"item": "prices_sha256", "value": "prices-current"},
                {"item": "fundamentals_sha256", "value": "fund-current"},
                {"item": "benchmark_sha256", "value": "benchmark-current"},
                {"item": "prices_last_date", "value": "2026-07-10"},
                {"item": "benchmark_last_date", "value": "2026-07-10"},
            ]).to_csv(path / "run_manifest.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "probability_excess_positive": 0.98 if name == "alt" else 0.80,
                        "lower_95_excess_return": 0.02 if name == "alt" else -0.10,
                    }
                ]
            ).to_csv(path / "excess_bootstrap.csv", index=False)
        pd.DataFrame([{"check": "candidate_forward_trading_days", "passed": "False", "value": 2, "threshold": 60, "message": ""}]).to_csv(base / "live_readiness.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": "True", "value": "ok", "threshold": "ok", "message": ""}]).to_csv(base / "practical_readiness.csv", index=False)
        pd.DataFrame([{"item": "stable", "value": "ok", "threshold": "ok", "passed": "True", "severity": "ok", "note": ""}]).to_csv(base / "strategy_candidate_stability.csv", index=False)
        pd.DataFrame([{"candidate_id": "base-candidate", "status": "collecting_forward_data", "observed_forward_trading_days": 2, "required_forward_trading_days": 60, "remaining_forward_trading_days": 58, "forward_excess_return": 0.01, "optimized_on_current_cache_at_freeze": True}]).to_csv(base / "live_candidate_forward_progress.csv", index=False)
        pd.DataFrame([{"scenario": "high_cost", "excess_total_return": 0.02}]).to_csv(base / "cost_stress.csv", index=False)

        pd.DataFrame([{"check": "all", "passed": "True", "value": "ok", "threshold": "ok", "message": ""}]).to_csv(alt / "live_readiness.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": "True", "value": "ok", "threshold": "ok", "message": ""}]).to_csv(alt / "practical_readiness.csv", index=False)
        pd.DataFrame([{"item": "stable", "value": "ok", "threshold": "ok", "passed": "True", "severity": "ok", "note": ""}]).to_csv(alt / "strategy_candidate_stability.csv", index=False)
        pd.DataFrame([{"candidate_id": "alt-candidate", "status": "validated", "observed_forward_trading_days": 60, "required_forward_trading_days": 60, "remaining_forward_trading_days": 0, "forward_excess_return": 0.03, "optimized_on_current_cache_at_freeze": True}]).to_csv(alt / "live_candidate_forward_progress.csv", index=False)
        pd.DataFrame([{"scenario": "high_cost", "excess_total_return": 0.04}]).to_csv(alt / "cost_stress.csv", index=False)

        config = {"paths": {"output_dir": base}, "strategy": {"name": "base"}}
        out = compare_backtest_outputs(config)
        result = pd.read_csv(out)
        self.assertEqual(result["strategy_name"].iloc[0], "alt")
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result["bootstrap_probability_excess_positive"].iloc[0], 0.98)
        self.assertAlmostEqual(result["bootstrap_lower_95_excess_return"].iloc[0], 0.02)
        self.assertFalse(bool(result[result["strategy_name"] == "base"]["live_readiness_passed"].iloc[0]))

        promotion = pd.read_csv(base / "strategy_promotion.csv")
        self.assertEqual(promotion["strategy_name"].iloc[0], "alt")
        self.assertEqual(promotion["promotion_status"].iloc[0], "strict_live_ready")
        self.assertEqual(promotion[promotion["strategy_name"] == "base"]["promotion_status"].iloc[0], "collecting_evidence")
        self.assertEqual(promotion[promotion["strategy_name"] == "base"]["anti_overfit_status"].iloc[0], "not_cleared")

        decision = pd.read_csv(base / "strategy_promotion_decision.csv")
        self.assertEqual(decision["decision"].iloc[0], "allow_manual_live_review")
        self.assertTrue(bool(decision["can_promote"].iloc[0]))
        self.assertEqual(decision["candidate_strategy_name"].iloc[0], "alt")
        self.assertEqual(decision["current_candidate_id"].iloc[0], "base-candidate")
        self.assertEqual(int(decision["current_forward_days"].iloc[0]), 2)
        self.assertTrue(bool(decision["candidate_evidence_is_separate"].iloc[0]))
        self.assertIn("独立保留", decision["reason"].iloc[0])


    def test_explicit_comparison_keeps_configured_reconciliation_pool(self):
        root = Path.cwd() / "test_outputs" / "compare_reconciliation_pool"
        base = root / "outputs"
        candidate_output = root / "outputs_candidate"
        candidate_config = root / "candidate.json"
        for path, name in ((base, "base"), (candidate_output, "candidate")):
            path.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"metric": "total_return", "value": 0.10},
                    {"metric": "benchmark_total_return", "value": 0.08},
                    {"metric": "excess_total_return", "value": 0.02},
                    {"metric": "max_drawdown", "value": -0.10},
                    {"metric": "information_ratio", "value": 0.20},
                ]
            ).to_csv(path / "performance_metrics.csv", index=False)
            pd.DataFrame(
                [
                    {"item": "strategy_name", "value": name},
                    {"item": "stocks_sha256", "value": "stocks-current"},
                    {"item": "prices_sha256", "value": "prices-current"},
                    {"item": "fundamentals_sha256", "value": "fund-current"},
                    {"item": "benchmark_sha256", "value": "benchmark-current"},
                ]
            ).to_csv(path / "run_manifest.csv", index=False)
        candidate_config.write_text(
            json.dumps(
                {"paths": {"output_dir": str(candidate_output)}, "strategy": {"name": "candidate"}}
            ),
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(base)},
            "strategy": {"name": "base"},
            "validation": {"comparison_candidate_configs": [str(candidate_config)]},
        }

        result = pd.read_csv(compare_backtest_outputs(config, [str(base)]))

        self.assertEqual(set(result["strategy_name"]), {"base", "candidate"})
    def test_configured_primary_challenger_controls_decision_target(self):
        root = Path.cwd() / "test_outputs" / "compare_primary"
        primary_output = root / "outputs_primary"
        primary_config = root / "primary.yaml"
        primary_output.mkdir(parents=True, exist_ok=True)
        primary_config.write_text(
            '{"paths":{"output_dir":"' + str(primary_output).replace("\\", "/") + '"},"strategy":{"name":"primary"}}',
            encoding="utf-8",
        )
        primary_config.write_text(
            json.dumps(
                {
                    "paths": {"output_dir": str(primary_output)},
                    "validation": {"candidate_failure_history_path": str(root / "isolated_failure_history.csv")},
                    "strategy": {"name": "primary"},
                }
            ),
            encoding="utf-8",
        )
        promotion = pd.DataFrame([
            {"rank": 1, "output_dir": str(root / "outputs_other"), "strategy_name": "other", "promotion_status": "strict_live_ready", "strict_live_passed": True, "practical_passed": True, "anti_overfit_status": "cleared_by_forward_validation", "candidate_id": "other-id"},
            {"rank": 2, "output_dir": str(primary_output), "strategy_name": "primary", "promotion_status": "collecting_evidence", "strict_live_passed": False, "practical_passed": True, "anti_overfit_status": "not_cleared", "candidate_id": "primary-id", "forward_days": 3},
        ])
        config = {"paths": {"output_dir": str(root / "outputs")}, "strategy": {"name": "base"}, "validation": {"primary_challenger_config": str(primary_config)}}

        decision = strategy_promotion_decision(config, promotion).iloc[0]

        self.assertEqual(decision["candidate_strategy_name"], "primary")
        self.assertEqual(decision["selection_basis"], "configured_primary_challenger")
        self.assertEqual(decision["decision"], "observe_only")
        self.assertFalse(bool(decision["can_promote"]))

    def test_registered_challenger_set_excludes_unregistered_experiments(self):
        root = Path.cwd() / "test_outputs" / "compare_primary_set"
        registered = []
        promotion_rows = [
            {
                "rank": 1,
                "output_dir": str(root / "outputs_unregistered"),
                "strategy_name": "unregistered",
                "promotion_status": "strict_live_ready",
                "strict_live_passed": True,
                "practical_passed": True,
                "anti_overfit_status": "cleared_by_forward_validation",
                "candidate_id": "unregistered-id",
            }
        ]
        for idx in (1, 2):
            candidate_output = root / f"outputs_registered_{idx}"
            candidate_config = root / f"registered_{idx}.json"
            candidate_output.mkdir(parents=True, exist_ok=True)
            candidate_config.write_text(
                json.dumps(
                    {
                        "paths": {"output_dir": str(candidate_output)},
                        "validation": {"candidate_failure_history_path": str(root / f"failure_{idx}.csv")},
                        "strategy": {"name": f"registered-{idx}"},
                    }
                ),
                encoding="utf-8",
            )
            registered.append(str(candidate_config))
            promotion_rows.append(
                {
                    "rank": idx + 1,
                    "output_dir": str(candidate_output),
                    "strategy_name": f"registered-{idx}",
                    "promotion_status": "collecting_evidence",
                    "strict_live_passed": False,
                    "practical_passed": True,
                    "anti_overfit_status": "not_cleared",
                    "candidate_id": f"registered-{idx}-id",
                }
            )
        config = {
            "paths": {"output_dir": str(root / "outputs")},
            "strategy": {"name": "base"},
            "validation": {"primary_challenger_configs": registered},
        }

        decision = strategy_promotion_decision(config, pd.DataFrame(promotion_rows)).iloc[0]

        self.assertEqual(decision["candidate_strategy_name"], "registered-1")
        self.assertEqual(decision["selection_basis"], "configured_primary_challenger_set")
        self.assertEqual(decision["decision"], "observe_only")

    def test_quarantined_primary_member_cannot_be_promoted(self):
        root = Path.cwd() / "test_outputs" / "compare_quarantined_primary"
        primary_output = root / "outputs_primary"
        primary_config = root / "primary.yaml"
        primary_output.mkdir(parents=True, exist_ok=True)
        primary_config.write_text(
            '{"paths":{"output_dir":"' + str(primary_output).replace("\\", "/") + '"},"strategy":{"name":"primary"}}',
            encoding="utf-8",
        )
        primary_config.write_text(
            json.dumps(
                {
                    "paths": {"output_dir": str(primary_output)},
                    "validation": {"candidate_failure_history_path": str(root / "isolated_failure_history.csv")},
                    "strategy": {"name": "primary"},
                }
            ),
            encoding="utf-8",
        )
        promotion = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "output_dir": str(primary_output),
                    "strategy_name": "primary",
                    "promotion_status": "strict_live_ready",
                    "strict_live_passed": True,
                    "practical_passed": True,
                    "anti_overfit_status": "cleared_by_forward_validation",
                    "candidate_id": "primary-id",
                }
            ]
        )
        config = {
            "paths": {"output_dir": str(root / "outputs")},
            "strategy": {"name": "base"},
            "validation": {"primary_challenger_config": str(primary_config)},
        }

        with patch("ashare_quant.compare.quarantined_ensemble_members", return_value=["failed-member"]):
            decision = strategy_promotion_decision(config, promotion).iloc[0]

        self.assertEqual(decision["decision"], "do_not_promote_quarantined_dependency")
        self.assertFalse(bool(decision["can_promote"]))
        self.assertFalse(bool(decision["can_paper_trade"]))
        self.assertIn("quarantined_candidate_dependency", decision["failed_live_checks"])

    def test_candidate_discovery_excludes_stress_and_operational_outputs(self):
        root = Path.cwd() / "test_outputs" / "compare_discovery"
        base = root / "outputs"
        expected = root / "outputs_alpha"
        excluded = [root / "outputs_alpha_stress_base", root / "outputs_live", root / "outputs_execution_drill"]
        for path in [base, expected, *excluded]:
            path.mkdir(parents=True, exist_ok=True)

        discovered = {path.resolve() for path in discover_output_dirs(base)}

        self.assertIn(base.resolve(), discovered)
        self.assertIn(expected.resolve(), discovered)
        self.assertTrue(all(path.resolve() not in discovered for path in excluded))

    def test_stale_high_return_candidate_cannot_win_promotion(self):
        root = Path.cwd() / "test_outputs" / "compare_stale"
        base = root / "outputs"
        stale = root / "outputs_stale"
        for path, name, price_hash, excess in [
            (base, "base", "current", 0.02),
            (stale, "stale_high_return", "old", 0.50),
        ]:
            path.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([
                {"metric": "total_return", "value": excess + 0.1},
                {"metric": "benchmark_total_return", "value": 0.1},
                {"metric": "excess_total_return", "value": excess},
                {"metric": "max_drawdown", "value": -0.1},
                {"metric": "information_ratio", "value": 0.2},
            ]).to_csv(path / "performance_metrics.csv", index=False)
            pd.DataFrame([
                {"item": "strategy_name", "value": name},
                {"item": "stocks_sha256", "value": "stocks"},
                {"item": "prices_sha256", "value": price_hash},
                {"item": "fundamentals_sha256", "value": "fund"},
                {"item": "benchmark_sha256", "value": "benchmark"},
                {"item": "prices_last_date", "value": "2026-07-10" if price_hash == "current" else "2026-06-30"},
                {"item": "benchmark_last_date", "value": "2026-07-10" if price_hash == "current" else "2026-06-30"},
            ]).to_csv(path / "run_manifest.csv", index=False)
            pd.DataFrame([{"check": "all", "passed": True}]).to_csv(path / "live_readiness.csv", index=False)
            pd.DataFrame([{"check": "all", "passed": True}]).to_csv(path / "practical_readiness.csv", index=False)
            pd.DataFrame([{"item": "stable", "passed": True}]).to_csv(path / "strategy_candidate_stability.csv", index=False)
            pd.DataFrame([{"candidate_id": name, "status": "validated", "observed_forward_trading_days": 60, "required_forward_trading_days": 60, "remaining_forward_trading_days": 0, "forward_excess_return": 0.02}]).to_csv(path / "live_candidate_forward_progress.csv", index=False)
        config = {"paths": {"output_dir": base}, "strategy": {"name": "base"}}
        out = compare_backtest_outputs(config)
        comparison = pd.read_csv(out)
        promotion = pd.read_csv(base / "strategy_promotion.csv")
        decision = pd.read_csv(base / "strategy_promotion_decision.csv")
        self.assertEqual(comparison.iloc[0]["strategy_name"], "base")
        self.assertFalse(bool(comparison[comparison["strategy_name"] == "stale_high_return"].iloc[0]["data_current"]))
        self.assertEqual(promotion[promotion["strategy_name"] == "stale_high_return"].iloc[0]["promotion_status"], "stale_evidence")
        self.assertEqual(decision.iloc[0]["candidate_strategy_name"], "base")


if __name__ == "__main__":
    unittest.main()
