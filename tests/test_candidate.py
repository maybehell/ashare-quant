import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from ashare_quant.audit import frame_sha256
from ashare_quant.candidate import load_candidate_manifest, seal_candidate_frozen_inputs
from ashare_quant.audit import backtest_config_sha256
from ashare_quant.shadow_policy import shadow_evaluation_evidence
from ashare_quant.candidate import build_candidate_forward_progress, build_candidate_manifest, build_candidate_validation, candidate_failure_history_path, candidate_frozen_input_evidence, candidate_index_membership_evidence, candidate_validation_passed, candidate_shadow_observation, freeze_live_candidate, is_candidate_quarantined, live_strategy_config, live_strategy_hash, matching_candidate_id, preregister_forward_candidate, record_candidate_failure


class CandidateValidationTests(unittest.TestCase):
    def root_for(self, name: str) -> Path:
        root = Path.cwd() / "test_outputs" / "candidate_tests" / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    def base_config(self, root: Path) -> dict:
        return {
            "paths": {"output_dir": str(root / "outputs")},
            "validation": {"candidate_path": str(root / "data" / "live_candidate.json")},
            "live_guard": {"min_forward_trading_days": 3, "min_forward_excess_return": 0.01},
            "strategy": {"name": "test", "top_n": 10},
            "trading": {"slippage_bps": 5},
            "universe": {"boards": ["main"]},
            "benchmark": {"symbol": "000300"},
            "risk_overlay": {"enabled": False},
            "rules": {"effective_date": "2026-07-06"},
        }

    def test_empty_candidate_path_means_no_candidate_manifest(self):
        self.assertIsNone(
            load_candidate_manifest({"validation": {"candidate_path": ""}})
        )

    def test_research_only_strategy_cannot_be_frozen_even_with_force(self):
        root = self.root_for("research_only")
        config = self.base_config(root)
        config["validation"].update(
            {
                "research_only": True,
                "experiment_status": "rejected",
                "experiment_reason": "failed isolated robustness test",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "Cannot freeze research-only strategy.*rejected"):
            freeze_live_candidate(config, object(), force=True)

    def test_forward_only_research_candidate_can_be_preregistered_but_not_promoted_at_freeze(self):
        root = self.root_for("forward_only_preregister")
        config = self.base_config(root)
        config["validation"].update(
            {
                "research_only": True,
                "experiment_status": "preregistered_forward_only",
                "research_rationale": "structural point-in-time index candidate",
            }
        )

        class Store:
            def load_market_data(self):
                return (
                    pd.DataFrame([{"symbol": "600000"}]),
                    pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
                    pd.DataFrame(),
                )

            def load_benchmark(self, required=False):
                return pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}])

        paths = preregister_forward_candidate(config, Store(), force=True)
        manifest = json.loads(Path(config["validation"]["candidate_path"]).read_text(encoding="utf-8"))

        self.assertEqual(manifest["freeze_mode"], "forward_only_preregistration")
        self.assertFalse(manifest["promotion_eligible_at_freeze"])
        self.assertFalse(manifest["historical_selection_evidence_allowed"])
        self.assertFalse(manifest["candidate_stability_passed_at_freeze"])
        self.assertEqual(matching_candidate_id(config, manifest), manifest["candidate_id"])
        self.assertEqual(manifest["forward_risk_evaluation_schema_version"], 1)
        self.assertEqual(len(manifest["forward_risk_evaluation_sha256"]), 64)
        self.assertAlmostEqual(manifest["max_forward_active_drawdown"], 0.10)
        self.assertAlmostEqual(manifest["max_forward_tracking_error"], 0.20)
        self.assertAlmostEqual(manifest["min_forward_information_ratio"], 0.0)
        self.assertEqual(len(paths), 2)
        validation = build_candidate_validation(config, pd.DataFrame()).set_index("check")
        progress = build_candidate_forward_progress(config, pd.DataFrame()).iloc[0]
        self.assertEqual(
            validation.loc["forward_validation_status", "value"],
            "waiting_for_post_freeze_market_data",
        )
        self.assertEqual(progress["status"], "waiting_for_post_freeze_market_data")
        pending_audit = pd.DataFrame([
            {
                "candidate_id": manifest["candidate_id"],
                "signal_date": "2026-01-04",
                "execution_date": "2026-01-05",
                "observed_trading_days": 0,
                "status": "not_observable",
            }
        ])
        pending_progress = build_candidate_forward_progress(
            config, pd.DataFrame(), shadow_audit=pending_audit
        ).iloc[0]
        self.assertEqual(int(pending_progress["pending_signal_count"]), 1)
        self.assertEqual(pending_progress["latest_pending_signal_date"], "2026-01-04")
        self.assertEqual(pending_progress["next_pending_execution_date"], "2026-01-05")
        self.assertEqual(pending_progress["first_attribution_requires_close_after"], "2026-01-05")
        self.assertIn("2026-01-05 之后的首个完整交易日收盘后归因", pending_progress["next_action"])

    def test_forward_failure_quarantines_strategy_and_blocks_force_refreeze(self):
        root = self.root_for("quarantined_refreeze")
        config = self.base_config(root)
        manifest = {
            "candidate_id": "failed-candidate",
            "strategy_hash": live_strategy_hash(config),
            "strategy_name": "test",
            "freeze_price_end": "2026-01-03",
        }
        candidate_path = Path(config["validation"]["candidate_path"])
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(json.dumps(manifest), encoding="utf-8")
        progress = pd.DataFrame(
            [
                {
                    "candidate_id": "failed-candidate",
                    "status": "early_forward_degradation",
                    "freeze_price_end": "2026-01-03",
                    "observed_forward_trading_days": 8,
                    "forward_excess_return": -0.07,
                }
            ]
        )

        history_path = record_candidate_failure(config, progress)

        self.assertEqual(history_path, candidate_failure_history_path(config))
        self.assertTrue(is_candidate_quarantined(config))
        recovery = pd.DataFrame(
            [
                {"date": "2026-01-03", "strategy_nav": 1.00, "benchmark_nav": 1.00},
                {"date": "2026-01-04", "strategy_nav": 1.02, "benchmark_nav": 1.00},
                {"date": "2026-01-05", "strategy_nav": 1.05, "benchmark_nav": 1.01},
                {"date": "2026-01-06", "strategy_nav": 1.08, "benchmark_nav": 1.01},
                {"date": "2026-01-07", "strategy_nav": 1.10, "benchmark_nav": 1.02},
            ]
        )
        stale_shadow = pd.DataFrame(
            [
                {
                    "candidate_id": "failed-candidate",
                    "evidence_type": "continuous_portfolio",
                    "status": "continuous_ok",
                    "execution_date": "2026-01-04",
                    "latest_date": "2026-01-05",
                    "observed_trading_days": 1,
                    "portfolio_return": -0.01,
                    "benchmark_return": 0.0,
                }
            ]
        )

        validation = build_candidate_validation(config, recovery, stale_shadow).set_index("check")
        recovered_progress = build_candidate_forward_progress(config, recovery, stale_shadow).iloc[0]

        self.assertFalse(bool(validation.loc["candidate_quarantine_clear", "passed"]))
        self.assertIn(
            "early_forward_degradation",
            validation.loc["candidate_quarantine_clear", "value"],
        )
        self.assertEqual(
            validation.loc["forward_validation_status", "value"],
            "quarantined_forward_failure",
        )
        self.assertEqual(recovered_progress["status"], "quarantined_forward_failure")
        self.assertEqual(recovered_progress["overfit_status"], "quarantined")
        self.assertEqual(int(recovered_progress["observed_forward_trading_days"]), 4)
        self.assertGreater(float(recovered_progress["forward_excess_return"]), 0.0)

        config["strategy"]["name"] = "renamed-test"
        self.assertTrue(is_candidate_quarantined(config))
        with self.assertRaisesRegex(RuntimeError, "quarantined after forward failure"):
            freeze_live_candidate(config, object(), force=True)

    def test_candidate_failure_history_is_idempotent(self):
        root = self.root_for("quarantine_idempotent")
        config = self.base_config(root)
        manifest = {
            "candidate_id": "failed-candidate",
            "strategy_hash": live_strategy_hash(config),
            "strategy_name": "test",
        }
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        progress = pd.DataFrame([{"candidate_id": "failed-candidate", "status": "failed_forward_excess"}])

        record_candidate_failure(config, progress)
        record_candidate_failure(config, progress)

        history = pd.read_csv(candidate_failure_history_path(config))
        self.assertEqual(len(history), 1)

    def test_ensemble_execution_schema_invalidates_legacy_candidate_hash(self):
        root = self.root_for("ensemble_execution_schema")
        config = self.base_config(root)
        single_hash = live_strategy_hash(config)
        config["strategy"]["ensemble_members"] = [
            {"name": "a", "blend_weight": 1.0, "weights": {"momentum": 1.0}},
            {"name": "b", "blend_weight": 1.0, "weights": {"low_volatility": 1.0}},
        ]

        ensemble_hash = live_strategy_hash(config)
        payload = live_strategy_config(config)

        self.assertNotEqual(single_hash, ensemble_hash)
        self.assertEqual(payload["implementation"]["ensemble_execution_schema"], 2)
        self.assertEqual(
            matching_candidate_id(config, {"candidate_id": "legacy", "strategy_hash": single_hash}),
            "",
        )
        self.assertEqual(
            matching_candidate_id(config, {"candidate_id": "current", "strategy_hash": ensemble_hash}),
            "current",
        )

    def test_breadth_overlay_is_part_of_candidate_hash_without_changing_legacy_payload(self):
        root = self.root_for("breadth_candidate_hash")
        config = self.base_config(root)
        legacy_hash = live_strategy_hash(config)
        self.assertNotIn("breadth_overlay", live_strategy_config(config))

        config["breadth_overlay"] = {
            "enabled": True,
            "moving_average_days": 60,
            "weak_threshold": 0.35,
            "critical_threshold": 0.20,
        }
        breadth_hash = live_strategy_hash(config)
        breadth_backtest_hash = backtest_config_sha256(config)
        self.assertNotEqual(legacy_hash, breadth_hash)
        self.assertEqual(live_strategy_config(config)["breadth_overlay"]["moving_average_days"], 60)

        config["breadth_overlay"]["critical_threshold"] = 0.15
        self.assertNotEqual(breadth_hash, live_strategy_hash(config))
        self.assertNotEqual(breadth_backtest_hash, backtest_config_sha256(config))

    def test_missing_candidate_fails_validation(self):
        root = self.root_for("missing")
        config = self.base_config(root)
        validation = build_candidate_validation(config, pd.DataFrame())
        self.assertFalse(candidate_validation_passed(validation))
        self.assertIn("candidate_exists", validation["check"].tolist())

    def test_missing_candidate_forward_progress_does_not_require_freeze(self):
        root = self.root_for("missing_progress")
        config = self.base_config(root)
        config["live_guard"]["require_candidate_stability"] = True
        path = Path(config["validation"]["candidate_path"])
        if path.exists():
            path.unlink()

        progress = build_candidate_forward_progress(config, pd.DataFrame())

        self.assertEqual(progress["status"].iloc[0], "missing_candidate")
        self.assertEqual(int(progress["observed_forward_trading_days"].iloc[0]), 0)
        self.assertEqual(progress["overfit_status"].iloc[0], "not_frozen")

    def test_frozen_candidate_collects_forward_days(self):
        root = self.root_for("collecting")
        config = self.base_config(root)
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
        comparison = pd.DataFrame(
            [
                {"date": "2026-01-03", "strategy_nav": 1.0, "benchmark_nav": 1.0},
                {"date": "2026-01-04", "strategy_nav": 1.01, "benchmark_nav": 1.0},
            ]
        )
        validation = build_candidate_validation(config, comparison)
        self.assertFalse(candidate_validation_passed(validation))
        indexed = validation.set_index("check")
        self.assertFalse(bool(indexed.loc["candidate_forward_excess_return", "passed"]))
        self.assertFalse(bool(indexed.loc["candidate_forward_active_max_drawdown", "passed"]))
        self.assertFalse(bool(indexed.loc["candidate_forward_tracking_error", "passed"]))
        self.assertFalse(bool(indexed.loc["candidate_forward_information_ratio", "passed"]))
        status = validation[validation["check"] == "forward_validation_status"]["value"].iloc[0]
        self.assertEqual(status, "collecting_forward_data")
        progress = build_candidate_forward_progress(config, comparison)
        self.assertEqual(progress["status"].iloc[0], "collecting_forward_data")
        self.assertEqual(int(progress["observed_forward_trading_days"].iloc[0]), 1)
        self.assertEqual(int(progress["remaining_forward_trading_days"].iloc[0]), 2)
        self.assertEqual(progress["overfit_status"].iloc[0], "collecting")

    def test_candidate_manifest_freezes_all_known_data_not_only_complete_market_date(self):
        root = self.root_for("complete_date")
        config = self.base_config(root)
        config["data"] = {"min_daily_price_coverage_ratio": 0.9, "require_benchmark_on_signal_date": True}
        prices = pd.DataFrame(
            [
                {"date": "2026-01-03", "symbol": "600000"},
                {"date": "2026-01-03", "symbol": "000001"},
                {"date": "2026-01-04", "symbol": "600000"},
            ]
        )
        benchmark = pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}, {"date": "2026-01-04", "symbol": "000300"}])

        manifest = build_candidate_manifest(config, pd.DataFrame([{"symbol": "600000"}]), prices, pd.DataFrame(), benchmark)

        self.assertEqual(manifest["freeze_price_end"], "2026-01-04")
        self.assertEqual(manifest["freeze_known_data_end"], "2026-01-04")
        self.assertEqual(manifest["freeze_raw_price_end"], "2026-01-04")
        self.assertEqual(manifest["freeze_complete_market_end"], "2026-01-03")
        self.assertEqual(manifest["freeze_boundary_schema_version"], 2)

    def test_candidate_validation_requires_stability_evidence_frozen_with_manifest(self):
        root = self.root_for("stability_evidence")
        config = self.base_config(root)
        config["live_guard"]["require_candidate_stability"] = True
        stability = pd.DataFrame(
            [
                {"item": "candidate_count", "passed": True},
                {"item": "research_source_data_current", "passed": True},
                {"item": "research_artifact_hash_match", "passed": True},
            ]
        )
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
            candidate_stability=stability,
        )
        self.assertEqual(manifest["candidate_stability_rows"], 3)
        self.assertTrue(manifest["candidate_stability_sha256"])
        self.assertTrue(manifest["candidate_stability_passed_at_freeze"])
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
        comparison = pd.DataFrame(
            [
                {"date": "2026-01-03", "strategy_nav": 1.00, "benchmark_nav": 1.00},
                {"date": "2026-01-04", "strategy_nav": 1.01, "benchmark_nav": 1.00},
                {"date": "2026-01-05", "strategy_nav": 1.04, "benchmark_nav": 1.01},
                {"date": "2026-01-06", "strategy_nav": 1.06, "benchmark_nav": 1.02},
                {"date": "2026-01-07", "strategy_nav": 1.08, "benchmark_nav": 1.03},
            ]
        )

        validation = build_candidate_validation(config, comparison)
        passed = dict(zip(validation["check"], validation["passed"]))
        self.assertTrue(bool(passed["candidate_stability_evidence_at_freeze"]))
        self.assertTrue(bool(passed["candidate_stability_passed_at_freeze"]))
        self.assertTrue(candidate_validation_passed(validation))

        manifest.pop("candidate_stability_sha256")
        manifest.pop("candidate_stability_rows")
        manifest.pop("candidate_stability_passed_at_freeze")
        path.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
        legacy = build_candidate_validation(config, comparison)
        legacy_passed = dict(zip(legacy["check"], legacy["passed"]))
        self.assertFalse(bool(legacy_passed["candidate_stability_evidence_at_freeze"]))
        status = legacy[legacy["check"] == "forward_validation_status"]["value"].iloc[0]
        self.assertEqual(status, "candidate_stability_failed")
    def test_candidate_passes_after_forward_validation(self):
        root = self.root_for("validated")
        config = self.base_config(root)
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
        comparison = pd.DataFrame(
            [
                {"date": "2026-01-03", "strategy_nav": 1.00, "benchmark_nav": 1.00},
                {"date": "2026-01-04", "strategy_nav": 1.01, "benchmark_nav": 1.00},
                {"date": "2026-01-05", "strategy_nav": 1.04, "benchmark_nav": 1.01},
                {"date": "2026-01-06", "strategy_nav": 1.06, "benchmark_nav": 1.02},
                {"date": "2026-01-07", "strategy_nav": 1.08, "benchmark_nav": 1.03},
            ]
        )
        validation = build_candidate_validation(config, comparison)
        self.assertTrue(candidate_validation_passed(validation))
        status = validation[validation["check"] == "forward_validation_status"]["value"].iloc[0]
        self.assertEqual(status, "validated")
        progress = build_candidate_forward_progress(config, comparison)
        self.assertEqual(progress["status"].iloc[0], "validated")
        self.assertEqual(int(progress["remaining_forward_trading_days"].iloc[0]), 0)
        self.assertGreater(float(progress["forward_excess_return"].iloc[0]), 0.01)


    def test_early_forward_warning_is_visible_before_hard_stop(self):
        root = self.root_for("early_warning")
        config = self.base_config(root)
        config["live_guard"].update({
            "min_forward_trading_days": 60,
            "min_early_forward_trading_days": 5,
            "min_early_forward_excess_return": -0.05,
            "early_forward_warning_ratio": 0.60,
        })
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
        comparison = pd.DataFrame([
            {"date": "2026-01-03", "strategy_nav": 1.00, "benchmark_nav": 1.00},
            {"date": "2026-01-04", "strategy_nav": 1.00, "benchmark_nav": 1.00},
            {"date": "2026-01-05", "strategy_nav": 0.99, "benchmark_nav": 1.00},
            {"date": "2026-01-06", "strategy_nav": 0.98, "benchmark_nav": 1.00},
            {"date": "2026-01-07", "strategy_nav": 0.975, "benchmark_nav": 1.00},
            {"date": "2026-01-08", "strategy_nav": 0.97, "benchmark_nav": 1.00},
            {"date": "2026-01-09", "strategy_nav": 0.96, "benchmark_nav": 1.00},
        ])
        progress = build_candidate_forward_progress(config, comparison)
        validation = build_candidate_validation(config, comparison).set_index("check")
        self.assertEqual(progress["status"].iloc[0], "early_forward_warning")
        self.assertEqual(progress["overfit_status"].iloc[0], "warning")
        self.assertTrue(bool(validation.loc["candidate_early_forward_degradation", "passed"]))
        self.assertFalse(bool(validation.loc["candidate_early_forward_warning_clear", "passed"]))

    def test_early_forward_degradation_is_not_plain_collecting(self):
        root = self.root_for("early_degradation")
        config = self.base_config(root)
        config["live_guard"].update({
            "min_forward_trading_days": 60,
            "min_early_forward_trading_days": 5,
            "min_early_forward_excess_return": -0.05,
        })
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, default=str), encoding="utf-8")
        comparison = pd.DataFrame([
            {"date": "2026-01-03", "strategy_nav": 1.00, "benchmark_nav": 1.00},
            {"date": "2026-01-04", "strategy_nav": 1.00, "benchmark_nav": 1.00},
            {"date": "2026-01-05", "strategy_nav": 0.99, "benchmark_nav": 1.00},
            {"date": "2026-01-06", "strategy_nav": 0.98, "benchmark_nav": 1.00},
            {"date": "2026-01-07", "strategy_nav": 0.97, "benchmark_nav": 1.00},
            {"date": "2026-01-08", "strategy_nav": 0.94, "benchmark_nav": 1.00},
            {"date": "2026-01-09", "strategy_nav": 0.93, "benchmark_nav": 1.00},
        ])
        progress = build_candidate_forward_progress(config, comparison)
        validation = build_candidate_validation(config, comparison).set_index("check")
        self.assertEqual(progress["status"].iloc[0], "early_forward_degradation")
        self.assertEqual(progress["overfit_status"].iloc[0], "early_failure")
        self.assertFalse(bool(validation.loc["candidate_early_forward_degradation", "passed"]))

    def test_frozen_input_evidence_allows_append_but_rejects_historical_change(self):
        root = self.root_for("frozen_inputs")
        config = self.base_config(root)
        stocks = pd.DataFrame(
            [{"symbol": "600000", "name": "A", "industry": "bank"}]
        )
        prices = pd.DataFrame(
            [{"date": "2026-01-03", "symbol": "600000", "close": 10.0}]
        )
        fundamentals = pd.DataFrame(
            [{"date": "2026-01-03", "symbol": "600000", "pe": 8.0}]
        )
        benchmark = pd.DataFrame(
            [{"date": "2026-01-03", "symbol": "000300", "name": "CSI300", "close": 100.0}]
        )
        manifest = build_candidate_manifest(
            config, stocks, prices, fundamentals, benchmark
        )
        self.assertEqual(manifest["frozen_input_schema_version"], 1)
        self.assertTrue(manifest["frozen_prices_sha256"])

        current_prices = pd.concat(
            [
                prices,
                pd.DataFrame(
                    [{"date": "2026-01-04", "symbol": "600000", "close": 11.0}]
                ),
            ],
            ignore_index=True,
        )
        current_benchmark = pd.concat(
            [
                benchmark,
                pd.DataFrame(
                    [{"date": "2026-01-04", "symbol": "000300", "name": "CSI300", "close": 101.0}]
                ),
            ],
            ignore_index=True,
        )
        store = Mock()
        store.load_market_data.return_value = (stocks, current_prices, fundamentals)
        store.load_benchmark.return_value = current_benchmark
        evidence = candidate_frozen_input_evidence(config, manifest, store)
        self.assertTrue(bool(evidence["passed"].all()))

        altered = current_prices.copy()
        altered.loc[altered["date"] == "2026-01-03", "close"] = 99.0
        store.load_market_data.return_value = (stocks, altered, fundamentals)
        evidence = candidate_frozen_input_evidence(config, manifest, store).set_index("input")
        self.assertFalse(bool(evidence.loc["prices", "passed"]))
    def test_point_in_time_membership_is_bound_only_through_freeze_date(self):
        root = self.root_for("membership_freeze")
        config = self.base_config(root)
        config["strategy"]["benchmark_membership"] = {
            "enabled": True,
            "index_symbol": "000300",
        }
        membership = pd.DataFrame(
            [
                {
                    "snapshot_date": date(2026, 1, 1),
                    "index_symbol": "000300",
                    "symbol": "600000",
                },
                {
                    "snapshot_date": date(2026, 1, 4),
                    "index_symbol": "000300",
                    "symbol": "600001",
                },
            ]
        )
        prices = pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}])
        benchmark = pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}])

        with patch(
            "ashare_quant.candidate.effective_index_membership",
            return_value=membership,
        ):
            manifest = build_candidate_manifest(
                config,
                pd.DataFrame([{"symbol": "600000"}]),
                prices,
                pd.DataFrame(),
                benchmark,
            )
            passed, current_rows, current_hash, frozen_rows, frozen_hash = (
                candidate_index_membership_evidence(config, manifest)
            )

        self.assertEqual(manifest["index_membership_rows_at_freeze"], 1)
        self.assertTrue(manifest["index_membership_sha256_at_freeze"])
        self.assertTrue(passed)
        self.assertEqual(current_rows, frozen_rows)
        self.assertEqual(current_hash, frozen_hash)

        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        altered = membership.copy()
        altered.loc[
            altered["snapshot_date"] == date(2026, 1, 1),
            "symbol",
        ] = "600999"
        with patch(
            "ashare_quant.candidate.effective_index_membership",
            return_value=altered,
        ):
            comparison = pd.DataFrame(
                [
                    {"date": "2026-01-03", "strategy_nav": 1.0, "benchmark_nav": 1.0},
                    {"date": "2026-01-04", "strategy_nav": 1.0, "benchmark_nav": 1.0},
                    {"date": "2026-01-05", "strategy_nav": 1.0, "benchmark_nav": 1.0},
                    {"date": "2026-01-06", "strategy_nav": 1.0, "benchmark_nav": 1.0},
                ]
            )
            validation = build_candidate_validation(config, comparison).set_index("check")

        self.assertFalse(bool(validation.loc["candidate_index_membership_evidence", "passed"]))
        self.assertEqual(
            validation.loc["forward_validation_status", "value"],
            "index_membership_evidence_mismatch",

        )
    def test_cached_benchmark_membership_is_frozen_without_universe_filter(self):
        root = self.root_for("membership_audit_without_filter")
        config = self.base_config(root)
        cache = root / "cache"
        config["paths"]["cache_dir"] = str(cache)
        cache.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "snapshot_date": "2026-01-02",
                    "index_symbol": "sh.000300",
                    "symbol": "sh.600000",
                }
            ]
        ).to_parquet(cache / "index_membership.parquet", index=False)
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )

        evidence = candidate_index_membership_evidence(config, manifest)

        self.assertEqual(manifest["index_membership_rows_at_freeze"], 1)
        self.assertTrue(manifest["index_membership_sha256_at_freeze"])
        self.assertEqual(manifest["benchmark_symbol_baostock"], "sh.000300")
        self.assertTrue(evidence[0])
        self.assertTrue(shadow_evaluation_evidence(config, manifest)[0])
    def test_legacy_membership_hash_migrates_without_changing_candidate_id(self):
        root = self.root_for("membership_hash_migration")
        config = self.base_config(root)
        config["strategy"]["benchmark_membership"] = {
            "enabled": True,
            "index_symbol": "sh000300",
        }
        membership = pd.DataFrame(
            [
                {
                    "snapshot_date": date(2026, 1, 1),
                    "update_date": date(2025, 12, 31),
                    "index_symbol": "000300",
                    "symbol": "600000",
                    "name": "A",
                    "source": "legacy",
                    "retrieved_at": "2026-01-01T18:00:00",
                }
            ]
        )
        stocks = pd.DataFrame([{"symbol": "600000"}])
        prices = pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}])
        fundamentals = pd.DataFrame()
        benchmark = pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}])

        with patch("ashare_quant.candidate.effective_index_membership", return_value=membership):
            manifest = build_candidate_manifest(
                config,
                stocks,
                prices,
                fundamentals,
                benchmark,
            )
        candidate_id = manifest["candidate_id"]
        manifest.pop("index_membership_hash_schema_version")
        manifest["index_membership_sha256_at_freeze"] = frame_sha256(membership)
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, default=str), encoding="utf-8")

        store = Mock()
        store.load_market_data.return_value = (stocks, prices, fundamentals)
        store.load_benchmark.return_value = benchmark
        with patch("ashare_quant.candidate.effective_index_membership", return_value=membership):
            seal_candidate_frozen_inputs(config, store)
            migrated = json.loads(path.read_text(encoding="utf-8"))
            evidence = candidate_index_membership_evidence(config, migrated)

        self.assertEqual(migrated["candidate_id"], candidate_id)
        self.assertEqual(migrated["index_membership_hash_schema_version"], 3)
        self.assertEqual(migrated["index_membership_hash_migration_from_schema"], 1)
        self.assertEqual(
            migrated["index_membership_legacy_sha256_at_freeze"],
            frame_sha256(membership),
        )
        self.assertNotEqual(
            migrated["index_membership_sha256_at_freeze"],
            migrated["index_membership_legacy_sha256_at_freeze"],
        )
        self.assertTrue(evidence[0])
    def test_semantic_membership_hash_ignores_alias_order_duplicates_and_metadata(self):
        root = self.root_for("semantic_membership_hash")
        config = self.base_config(root)
        config["strategy"]["benchmark_membership"] = {
            "enabled": True,
            "index_symbol": "sh000300",
        }
        membership = pd.DataFrame(
            [
                {
                    "snapshot_date": date(2026, 1, 1),
                    "update_date": date(2025, 12, 31),
                    "index_symbol": "sh.000300",
                    "symbol": "sh.600000",
                    "name": "old name",
                    "source": "source-a",
                    "retrieved_at": "2026-01-01T18:00:00",
                },
                {
                    "snapshot_date": date(2026, 1, 1),
                    "update_date": date(2026, 1, 1),
                    "index_symbol": "沪深300",
                    "symbol": "600000",
                    "name": "new name",
                    "source": "source-b",
                    "retrieved_at": "2026-01-02T18:00:00",
                },
            ]
        )
        prices = pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}])
        benchmark = pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}])

        with patch("ashare_quant.candidate.effective_index_membership", return_value=membership):
            manifest = build_candidate_manifest(
                config,
                pd.DataFrame([{"symbol": "600000"}]),
                prices,
                pd.DataFrame(),
                benchmark,
            )
            evidence = candidate_index_membership_evidence(config, manifest)

        self.assertEqual(manifest["index_membership_rows_at_freeze"], 1)
        self.assertEqual(evidence[1], 1)
        self.assertTrue(evidence[0])

        changed = membership.iloc[[0]].copy()
        changed.loc[:, "symbol"] = "600001"
        with patch("ashare_quant.candidate.effective_index_membership", return_value=changed):
            changed_evidence = candidate_index_membership_evidence(config, manifest)
        self.assertFalse(changed_evidence[0])


    def test_legacy_forward_only_candidate_cannot_count_shadow_days(self):
        root = self.root_for("legacy_forward_boundary")
        config = self.base_config(root)
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )
        manifest["freeze_mode"] = "forward_only_preregistration"
        manifest.pop("freeze_boundary_schema_version")
        manifest.pop("freeze_known_data_end")
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, default=str), encoding="utf-8")
        shadow_audit = pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-03",
                    "execution_date": "2026-01-04",
                    "latest_date": "2026-01-06",
                    "observed_trading_days": 2,
                    "portfolio_return": 0.012,
                    "benchmark_return": 0.010,
                    "candidate_id": manifest["candidate_id"],
                    "status": "ok",
                }
            ]
        )

        validation = build_candidate_validation(
            config, pd.DataFrame(), shadow_audit=shadow_audit
        ).set_index("check")
        progress = build_candidate_forward_progress(
            config, pd.DataFrame(), shadow_audit=shadow_audit
        ).iloc[0]

        self.assertFalse(bool(validation.loc["candidate_freeze_boundary_evidence", "passed"]))
        self.assertEqual(validation.loc["forward_validation_status", "value"], "unsafe_freeze_boundary")
        self.assertEqual(int(validation.loc["candidate_forward_trading_days", "value"]), 0)
        self.assertEqual(progress["status"], "unsafe_freeze_boundary")
        self.assertEqual(int(progress["observed_forward_trading_days"]), 0)

    def test_forward_only_candidate_uses_immutable_shadow_observation(self):
        root = self.root_for("forward_only_shadow_audit")
        config = self.base_config(root)
        config["live_guard"]["require_candidate_stability"] = True
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )
        manifest["freeze_mode"] = "forward_only_preregistration"
        manifest["required_forward_trading_days"] = 60
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        shadow_audit = pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-03",
                    "execution_date": "2026-01-04",
                    "latest_date": "2026-01-06",
                    "observed_trading_days": 2,
                    "portfolio_return": 0.012,
                    "benchmark_return": 0.010,
                    "candidate_id": manifest["candidate_id"],
                    "status": "ok",
                },
                {
                    "signal_date": "2026-01-03",
                    "execution_date": "2026-01-04",
                    "latest_date": "2026-01-05",
                    "observed_trading_days": 50,
                    "portfolio_return": 0.50,
                    "benchmark_return": 0.01,
                    "candidate_id": "another-candidate",
                    "status": "ok",
                },
                {
                    "signal_date": "2026-01-03",
                    "execution_date": "2026-01-03",
                    "latest_date": "2026-03-31",
                    "observed_trading_days": 60,
                    "portfolio_return": 0.80,
                    "benchmark_return": 0.0,
                    "candidate_id": manifest["candidate_id"],
                    "status": "failed",
                },
            ]
        )

        validation = build_candidate_validation(
            config, pd.DataFrame(), shadow_audit=shadow_audit
        ).set_index("check")
        progress = build_candidate_forward_progress(
            config, pd.DataFrame(), shadow_audit=shadow_audit
        ).iloc[0]

        self.assertEqual(validation.loc["forward_validation_status", "value"], "collecting_forward_data")
        self.assertEqual(int(validation.loc["candidate_forward_trading_days", "value"]), 2)
        self.assertNotIn("candidate_stability_evidence_at_freeze", validation.index)
        self.assertEqual(int(progress["observed_forward_trading_days"]), 2)
        self.assertEqual(int(progress["remaining_forward_trading_days"]), 58)
        self.assertAlmostEqual(float(progress["forward_excess_return"]), 0.002)


    def test_forward_only_candidate_rejects_positive_excess_with_excessive_active_drawdown(self):
        root = self.root_for("forward_risk_path")
        config = self.base_config(root)
        config["live_guard"].update({
            "min_forward_trading_days": 60,
            "max_forward_active_drawdown": 0.10,
            "max_forward_tracking_error": 0.20,
            "min_forward_information_ratio": 0.0,
        })
        manifest = build_candidate_manifest(
            config,
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "600000"}]),
            pd.DataFrame(),
            pd.DataFrame([{"date": "2026-01-03", "symbol": "000300"}]),
        )
        manifest["freeze_mode"] = "forward_only_preregistration"
        path = Path(config["validation"]["candidate_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        shadow_audit = pd.DataFrame([{
            "candidate_id": manifest["candidate_id"],
            "signal_date": "2026-01-03",
            "execution_date": "2026-01-04",
            "latest_date": "2026-04-30",
            "observed_trading_days": 60,
            "portfolio_return": 0.05,
            "benchmark_return": 0.02,
            "active_max_drawdown": 0.12,
            "tracking_error": 0.10,
            "information_ratio": 0.50,
            "status": "continuous_ok",
            "evidence_type": "continuous_portfolio",
        }])

        validation = build_candidate_validation(
            config, pd.DataFrame(), shadow_audit=shadow_audit
        ).set_index("check")
        progress = build_candidate_forward_progress(
            config, pd.DataFrame(), shadow_audit=shadow_audit
        ).iloc[0]

        self.assertTrue(bool(validation.loc["candidate_forward_excess_return", "passed"]))
        self.assertFalse(bool(validation.loc["candidate_forward_active_max_drawdown", "passed"]))
        self.assertTrue(bool(validation.loc["candidate_forward_tracking_error", "passed"]))
        self.assertTrue(bool(validation.loc["candidate_forward_information_ratio", "passed"]))
        self.assertEqual(validation.loc["forward_validation_status", "value"], "failed_forward_risk")
        self.assertEqual(progress["status"], "failed_forward_risk")
        self.assertAlmostEqual(float(progress["forward_active_max_drawdown"]), 0.12)

        manifest["max_forward_active_drawdown"] = 0.50
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        tampered = build_candidate_validation(
            config, pd.DataFrame(), shadow_audit=shadow_audit
        ).set_index("check")
        self.assertFalse(bool(tampered.loc["candidate_forward_risk_evaluation_policy", "passed"]))
        self.assertEqual(
            tampered.loc["forward_validation_status", "value"],
            "forward_risk_evaluation_policy_mismatch",
        )


    def test_candidate_progress_prefers_continuous_portfolio_evidence(self):
        audit = pd.DataFrame([
            {
                "candidate_id": "candidate", "signal_date": "2026-01-01",
                "execution_date": "2026-01-02", "latest_date": "2026-01-20",
                "observed_trading_days": 10, "portfolio_return": 0.20,
                "benchmark_return": 0.02, "status": "ok", "evidence_type": "",
            },
            {
                "candidate_id": "candidate", "signal_date": "2026-01-01",
                "execution_date": "2026-01-02", "latest_date": "2026-01-06",
                "observed_trading_days": 2, "portfolio_return": 0.01,
                "benchmark_return": 0.005, "status": "continuous_ok",
                "evidence_type": "continuous_portfolio",
            },
        ])

        observation = candidate_shadow_observation(
            audit, "candidate", freeze_date="2026-01-01"
        )

        self.assertEqual(observation["days"], 2)
        self.assertAlmostEqual(observation["strategy_return"], 0.01)
        self.assertAlmostEqual(observation["excess_return"], 0.005)
if __name__ == "__main__":
    unittest.main()
