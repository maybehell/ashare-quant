import unittest
from datetime import date
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from ashare_quant.audit import backtest_config_sha256, build_run_manifest, capital_sensitivity_config_sha256, mapping_sha256
from ashare_quant.config import load_config
from ashare_quant.data import DataStore, make_sample_data
from ashare_quant.readiness import (
    build_live_readiness,
    build_live_readiness_roadmap,
    build_practical_readiness,
    build_practical_readiness_from_outputs,
    current_manifest_frames,
    data_quality_with_current_cache_age,
    ensure_live_readiness,
    practical_readiness_level,
    readiness_evidence_issues,
    readiness_passed,
    shadow_observation_values,
)


class ReadinessTests(unittest.TestCase):
    def practical_capital_config(self) -> dict:
        return {
            "universe": {"scope": "curated"},
            "data": {"max_cache_age_days": 10},
            "backtest": {"initial_cash": 1_000_000},
            "strategy": {"top_n": 20},
            "practical_guard": {
                "enabled": True,
                "require_current_evidence": False,
                "require_capital_sensitivity": True,
                "min_total_excess_return": 0.0,
                "max_strategy_drawdown": 1.0,
                "max_annualized_turnover": 100.0,
                "require_out_of_sample": False,
                "require_walk_forward": False,
                "require_cost_stress": False,
                "require_metadata": False,
                "allow_optimized_on_current_cache": True,
                "min_price_symbol_count": 1,
            },
        }

    def practical_capital_manifest(self, suffix: str = "") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"item": "stocks_sha256", "value": f"stocks{suffix}"},
                {"item": "prices_sha256", "value": f"prices{suffix}"},
                {"item": "fundamentals_sha256", "value": f"fundamentals{suffix}"},
                {"item": "benchmark_sha256", "value": f"benchmark{suffix}"},
            ]
        )

    def practical_capital_data_hash(self, suffix: str = "") -> str:
        manifest = self.practical_capital_manifest(suffix)
        return mapping_sha256(
            {str(row.item): str(row.value) for row in manifest.itertuples(index=False)}
        )

    def practical_capital_readiness(self, config: dict, audit: pd.DataFrame) -> pd.DataFrame:
        quality = pd.DataFrame(
            [
                {"item": "universe_scope", "value": "curated"},
                {"item": "price_symbol_count", "value": 100},
                {"item": "priced_stock_coverage", "value": 1.0},
                {"item": "price_cache_age_days", "value": 0},
                {"item": "unknown_industry_ratio", "value": 0.0},
                {"item": "imputed_list_date_ratio", "value": 0.0},
                {"item": "optimized_on_current_cache", "value": False},
            ]
        )
        return build_practical_readiness(
            config,
            {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            quality,
            pd.DataFrame(),
            pd.DataFrame(),
            run_manifest=self.practical_capital_manifest(),
            capital_sensitivity_audit=audit,
        )

    def test_practical_readiness_blocks_missing_capital_sensitivity(self):
        readiness = self.practical_capital_readiness(self.practical_capital_config(), pd.DataFrame())
        rows = readiness.set_index("check")

        self.assertFalse(bool(rows.loc["capital_sensitivity_config_current", "passed"]))
        self.assertFalse(bool(rows.loc["capital_sensitivity_ready", "passed"]))

    def test_practical_readiness_accepts_matching_capital_sensitivity(self):
        config = self.practical_capital_config()
        audit = pd.DataFrame(
            [
                {
                    "check": "capital_sensitivity_strategy_config_sha256",
                    "passed": True,
                    "value": capital_sensitivity_config_sha256(config),
                },
                {
                    "check": "capital_sensitivity_reference_market_data_sha256",
                    "passed": True,
                    "value": self.practical_capital_data_hash(),
                },
                {
                    "check": "capital_sensitivity_ready",
                    "passed": True,
                    "value": "minimum_passing_capital=600000.00",
                },
            ]
        )
        rows = self.practical_capital_readiness(config, audit).set_index("check")

        self.assertTrue(bool(rows.loc["capital_sensitivity_config_current", "passed"]))
        self.assertTrue(bool(rows.loc["capital_sensitivity_data_current", "passed"]))
        self.assertTrue(bool(rows.loc["capital_sensitivity_ready", "passed"]))

    def test_practical_readiness_rejects_foreign_capital_market_data(self):
        config = self.practical_capital_config()
        audit = pd.DataFrame(
            [
                {
                    "check": "capital_sensitivity_strategy_config_sha256",
                    "passed": True,
                    "value": capital_sensitivity_config_sha256(config),
                },
                {
                    "check": "capital_sensitivity_reference_market_data_sha256",
                    "passed": True,
                    "value": self.practical_capital_data_hash("-old"),
                },
                {
                    "check": "capital_sensitivity_ready",
                    "passed": True,
                    "value": "minimum_passing_capital=600000.00",
                },
            ]
        )

        rows = self.practical_capital_readiness(config, audit).set_index("check")

        self.assertTrue(bool(rows.loc["capital_sensitivity_config_current", "passed"]))
        self.assertFalse(bool(rows.loc["capital_sensitivity_data_current", "passed"]))
        self.assertTrue(bool(rows.loc["capital_sensitivity_ready", "passed"]))

    def test_practical_readiness_rejects_foreign_capital_sensitivity_config(self):
        config = self.practical_capital_config()
        audit = pd.DataFrame(
            [
                {
                    "check": "capital_sensitivity_strategy_config_sha256",
                    "passed": True,
                    "value": "foreign-config",
                },
                {
                    "check": "capital_sensitivity_ready",
                    "passed": True,
                    "value": "minimum_passing_capital=600000.00",
                },
            ]
        )
        rows = self.practical_capital_readiness(config, audit).set_index("check")

        self.assertFalse(bool(rows.loc["capital_sensitivity_config_current", "passed"]))
        self.assertTrue(bool(rows.loc["capital_sensitivity_ready", "passed"]))

    def test_data_quality_cache_age_is_recomputed_from_market_date(self):
        data_quality = pd.DataFrame(
            [
                {"item": "price_end", "value": "2026-01-01", "severity": "info", "note": ""},
                {"item": "price_cache_age_days", "value": 0, "severity": "ok", "note": "old"},
            ]
        )
        manifest = pd.DataFrame([{"item": "prices_last_date", "value": "2026-01-01"}])

        updated = data_quality_with_current_cache_age(
            data_quality,
            manifest,
            {"data": {"max_cache_age_days": 10}},
            today=date(2026, 1, 15),
        )

        row = updated[updated["item"] == "price_cache_age_days"].iloc[0]
        self.assertEqual(int(row["value"]), 14)
        self.assertEqual(row["severity"], "warning")

    def test_practical_readiness_from_outputs_recomputes_stale_cache_age(self):
        output_dir = Path.cwd() / "test_outputs" / "current_age_readiness"
        output_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output_dir)},
            "universe": {"scope": "curated"},
            "data": {"max_cache_age_days": 10},
            "practical_guard": {
                "enabled": True,
                "require_current_evidence": False,
                "min_total_excess_return": 0.0,
                "max_strategy_drawdown": 0.5,
                "max_annualized_turnover": 10.0,
                "require_out_of_sample": True,
                "min_out_of_sample_excess_return": 0.0,
                "require_walk_forward": True,
                "min_walk_forward_folds": 2,
                "min_walk_forward_avg_excess_return": 0.0,
                "min_walk_forward_positive_ratio": 0.0,
                "min_price_symbol_count": 1,
                "require_metadata": False,
                "allow_optimized_on_current_cache": True,
                "require_cost_stress": False,
            },
        }
        pd.DataFrame(
            [
                {"date": "2000-01-01", "strategy_nav": 1.0, "benchmark_nav": 1.0},
                {"date": "2000-01-02", "strategy_nav": 1.2, "benchmark_nav": 1.0},
            ]
        ).to_csv(output_dir / "benchmark_comparison.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [
                {"metric": "max_drawdown", "value": -0.1},
                {"metric": "annualized_turnover", "value": 1.0},
            ]
        ).to_csv(output_dir / "performance_metrics.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [
                {"item": "universe_scope", "value": "curated", "severity": "info", "note": ""},
                {"item": "price_symbol_count", "value": 100, "severity": "info", "note": ""},
                {"item": "priced_stock_coverage", "value": 0.1, "severity": "info", "note": ""},
                {"item": "price_end", "value": "2000-01-01", "severity": "info", "note": ""},
                {"item": "price_cache_age_days", "value": 0, "severity": "ok", "note": "stale"},
                {"item": "unknown_industry_ratio", "value": 0.0, "severity": "ok", "note": ""},
                {"item": "imputed_list_date_ratio", "value": 0.0, "severity": "ok", "note": ""},
                {"item": "optimized_on_current_cache", "value": False, "severity": "ok", "note": ""},
            ]
        ).to_csv(output_dir / "data_quality.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]).to_csv(
            output_dir / "robustness_split.csv", index=False, encoding="utf-8-sig"
        )
        pd.DataFrame([{"folds": 3, "avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]).to_csv(
            output_dir / "walk_forward_summary.csv", index=False, encoding="utf-8-sig"
        )
        pd.DataFrame([{"item": "prices_last_date", "value": "2000-01-01"}]).to_csv(
            output_dir / "run_manifest.csv", index=False, encoding="utf-8-sig"
        )

        readiness = build_practical_readiness_from_outputs(config)
        row = readiness[readiness["check"] == "price_cache_age_days"].iloc[0]
        self.assertFalse(bool(row["passed"]))
        self.assertGreater(float(row["value"]), 10.0)
    def test_practical_readiness_allows_curated_metadata_warnings(self):
        config = {
            "universe": {"scope": "curated"},
            "data": {"max_cache_age_days": 10},
            "practical_guard": {
                "enabled": True,
                "require_current_evidence": True,
                "min_total_excess_return": 0.02,
                "max_strategy_drawdown": 0.30,
                "max_annualized_turnover": 8.0,
                "require_out_of_sample": True,
                "min_out_of_sample_excess_return": 0.0,
                "require_cost_stress": True,
                "require_current_cost_stress": False,
                "cost_stress_scenario": "high_cost",
                "min_cost_stress_excess_return": 0.0,
                "max_cost_stress_drawdown": 0.30,
                "require_metadata": False,
                "allow_optimized_on_current_cache": True,
                "min_price_symbol_count": 80,
            },
        }
        readiness = build_practical_readiness(
            config,
            {"excess_total_return": 0.08, "max_drawdown": -0.18, "annualized_turnover": 3.0},
            pd.DataFrame(
                [
                    {"item": "universe_scope", "value": "curated"},
                    {"item": "price_symbol_count", "value": 120},
                    {"item": "priced_stock_coverage", "value": 0.14},
                    {"item": "price_cache_age_days", "value": 1},
                    {"item": "unknown_industry_ratio", "value": 0.99},
                    {"item": "imputed_list_date_ratio", "value": 0.99},
                    {"item": "optimized_on_current_cache", "value": True},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.01}]),
            pd.DataFrame([{"avg_test_excess_return": -0.02, "positive_test_ratio": 0.4}]),
            cost_stress=pd.DataFrame([{"scenario": "high_cost", "excess_total_return": 0.01, "max_drawdown": -0.20}]),
            evidence_issues=[],
        )

        self.assertTrue(readiness_passed(readiness))
        self.assertEqual(practical_readiness_level(readiness), "实用可运行级")

    def test_practical_readiness_blocks_negative_excess(self):
        readiness = build_practical_readiness(
            {
                "universe": {"scope": "curated"},
                "data": {"max_cache_age_days": 10},
                "practical_guard": {
                    "enabled": True,
                    "require_current_evidence": False,
                    "min_total_excess_return": 0.02,
                    "max_strategy_drawdown": 0.30,
                    "max_annualized_turnover": 8.0,
                    "require_out_of_sample": False,
                    "require_cost_stress": False,
                    "require_metadata": False,
                    "allow_optimized_on_current_cache": True,
                    "min_price_symbol_count": 80,
                },
            },
            {"excess_total_return": -0.01, "max_drawdown": -0.10, "annualized_turnover": 2.0},
            pd.DataFrame(
                [
                    {"item": "universe_scope", "value": "curated"},
                    {"item": "price_symbol_count", "value": 120},
                    {"item": "priced_stock_coverage", "value": 0.14},
                    {"item": "price_cache_age_days", "value": 1},
                    {"item": "unknown_industry_ratio", "value": 0.99},
                    {"item": "imputed_list_date_ratio", "value": 0.99},
                    {"item": "optimized_on_current_cache", "value": True},
                ]
            ),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("total_excess_return", failed)
        self.assertEqual(practical_readiness_level(readiness), "未通过")

    def test_live_readiness_requires_candidate_stability_evidence(self):
        config = {
            "universe": {"scope": "curated"},
            "data": {"max_cache_age_days": 10},
            "live_guard": {
                "enabled": True,
                "require_candidate_stability": True,
                "allow_optimized_on_current_cache": True,
                "min_total_excess_return": 0.0,
                "min_out_of_sample_excess_return": 0.0,
                "min_walk_forward_avg_excess_return": 0.0,
                "min_walk_forward_positive_ratio": 0.0,
                "min_price_symbol_count": 1,
                "max_unknown_industry_ratio": 1.0,
                "max_imputed_list_date_ratio": 1.0,
            },
        }
        quality = pd.DataFrame(
            [
                {"item": "universe_scope", "value": "curated"},
                {"item": "price_symbol_count", "value": 100},
                {"item": "priced_stock_coverage", "value": 0.1},
                {"item": "price_cache_age_days", "value": 0},
                {"item": "unknown_industry_ratio", "value": 0.0},
                {"item": "imputed_list_date_ratio", "value": 0.0},
                {"item": "optimized_on_current_cache", "value": False},
            ]
        )
        metrics = {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0}
        robustness = pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}])
        walk_forward = pd.DataFrame([{"folds": 3, "avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}])

        missing = build_live_readiness(config, metrics, quality, robustness, walk_forward)
        missing_passed = dict(zip(missing["check"], missing["passed"]))
        self.assertFalse(bool(missing_passed["candidate_stability_evidence"]))
        self.assertFalse(bool(missing_passed["candidate_stability_passed"]))

        stability = pd.DataFrame(
            [
                {"item": "candidate_count", "passed": True},
                {"item": "research_source_data_current", "passed": True},
                {"item": "research_artifact_hash_match", "passed": True},
            ]
        )
        current = build_live_readiness(
            config,
            metrics,
            quality,
            robustness,
            walk_forward,
            candidate_stability=stability,
        )
        current_passed = dict(zip(current["check"], current["passed"]))
        self.assertTrue(bool(current_passed["candidate_stability_evidence"]))
        self.assertTrue(bool(current_passed["candidate_stability_passed"]))
    def test_negative_excess_blocks_readiness(self):
        readiness = build_live_readiness(
            {"live_guard": {"enabled": True}, "data": {"max_cache_age_days": 10}},
            {"excess_total_return": -0.01},
            pd.DataFrame(
                [
                    {"item": "unknown_industry_ratio", "value": 0.1},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": -0.02}]),
            pd.DataFrame([{"avg_test_excess_return": -0.01, "positive_test_ratio": 0.5}]),
        )
        self.assertFalse(readiness_passed(readiness))
        failed = readiness[~readiness["passed"]]
        self.assertIn("total_excess_return", failed["check"].tolist())

    def test_metadata_and_cache_optimization_block_live_grade(self):
        readiness = build_live_readiness(
            {
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.6,
                    "min_price_symbol_count": 100,
                    "max_unknown_industry_ratio": 0.2,
                    "max_imputed_list_date_ratio": 0.2,
                    "allow_optimized_on_current_cache": False,
                },
                "data": {"max_cache_age_days": 10},
            },
            {"excess_total_return": 0.1},
            pd.DataFrame(
                [
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "unknown_industry_ratio", "value": 0.9},
                    {"item": "imputed_list_date_ratio", "value": 0.8},
                    {"item": "optimized_on_current_cache", "value": True},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
        )
        self.assertFalse(readiness_passed(readiness))
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("unknown_industry_ratio", failed)
        self.assertIn("imputed_list_date_ratio", failed)
        self.assertIn("optimized_on_current_cache", failed)

    def test_validated_candidate_clears_cache_optimization_block(self):
        readiness = build_live_readiness(
            {
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.6,
                    "min_price_symbol_count": 100,
                    "max_unknown_industry_ratio": 0.2,
                    "max_imputed_list_date_ratio": 0.2,
                    "allow_optimized_on_current_cache": False,
                },
                "data": {"max_cache_age_days": 10},
            },
            {"excess_total_return": 0.1},
            pd.DataFrame(
                [
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "unknown_industry_ratio", "value": 0.01},
                    {"item": "imputed_list_date_ratio", "value": 0.01},
                    {"item": "optimized_on_current_cache", "value": True},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
            pd.DataFrame(
                [
                    {"check": "candidate_exists", "passed": True, "value": "abc", "threshold": "present", "message": ""},
                    {"check": "candidate_hash_match", "passed": True, "value": "abc", "threshold": "abc", "message": ""},
                    {"check": "candidate_forward_trading_days", "passed": True, "value": 80, "threshold": 60, "message": ""},
                    {"check": "candidate_forward_excess_return", "passed": True, "value": 0.02, "threshold": 0.0, "message": ""},
                    {"check": "forward_validation_status", "passed": True, "value": "validated", "threshold": "validated", "message": ""},
                ]
            ),
        )
        self.assertTrue(readiness_passed(readiness))

    def test_curated_universe_low_market_coverage_does_not_block(self):
        readiness = build_live_readiness(
            {
                "universe": {"scope": "curated"},
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.0,
                    "min_price_symbol_count": 100,
                    "max_unknown_industry_ratio": 0.2,
                    "max_imputed_list_date_ratio": 0.2,
                    "allow_optimized_on_current_cache": True,
                },
                "data": {"max_cache_age_days": 10},
            },
            {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            pd.DataFrame(
                [
                    {"item": "universe_scope", "value": "curated"},
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "priced_stock_coverage", "value": 0.14},
                    {"item": "unknown_industry_ratio", "value": 0.01},
                    {"item": "imputed_list_date_ratio", "value": 0.01},
                    {"item": "optimized_on_current_cache", "value": False},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
        )
        self.assertTrue(readiness_passed(readiness))

    def test_full_market_universe_requires_price_coverage(self):
        readiness = build_live_readiness(
            {
                "universe": {"scope": "full_market"},
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.0,
                    "min_price_symbol_count": 100,
                    "min_full_market_priced_stock_coverage": 0.7,
                    "max_unknown_industry_ratio": 0.2,
                    "max_imputed_list_date_ratio": 0.2,
                    "allow_optimized_on_current_cache": True,
                },
                "data": {"max_cache_age_days": 10},
            },
            {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            pd.DataFrame(
                [
                    {"item": "universe_scope", "value": "full_market"},
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "priced_stock_coverage", "value": 0.14},
                    {"item": "unknown_industry_ratio", "value": 0.01},
                    {"item": "imputed_list_date_ratio", "value": 0.01},
                    {"item": "optimized_on_current_cache", "value": False},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
        )
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("priced_stock_coverage", failed)

    def test_shadow_observation_required_when_enabled(self):
        readiness = build_live_readiness(
            {
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.6,
                    "min_price_symbol_count": 100,
                    "max_unknown_industry_ratio": 0.2,
                    "max_imputed_list_date_ratio": 0.2,
                    "allow_optimized_on_current_cache": False,
                    "require_shadow_observation": True,
                    "require_current_candidate_shadow": False,
                    "min_shadow_observed_signals": 2,
                    "min_shadow_avg_excess_return": 0.0,
                    "min_shadow_positive_ratio": 0.5,
                    "min_shadow_covered_weight": 0.95,
                },
                "data": {"max_cache_age_days": 10},
            },
            {"excess_total_return": 0.1},
            pd.DataFrame(
                [
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "unknown_industry_ratio", "value": 0.01},
                    {"item": "imputed_list_date_ratio", "value": 0.01},
                    {"item": "optimized_on_current_cache", "value": False},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
            shadow_forward_audit=pd.DataFrame(),
        )
        self.assertFalse(readiness_passed(readiness))
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("shadow_observed_signal_count", failed)
        self.assertIn("shadow_avg_excess_return", failed)

    def test_shadow_observation_can_pass_live_gate(self):
        readiness = build_live_readiness(
            {
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.6,
                    "min_price_symbol_count": 100,
                    "max_unknown_industry_ratio": 0.2,
                    "max_imputed_list_date_ratio": 0.2,
                    "allow_optimized_on_current_cache": False,
                    "require_shadow_observation": True,
                    "require_current_candidate_shadow": False,
                    "min_shadow_observed_signals": 2,
                    "min_shadow_avg_excess_return": 0.0,
                    "min_shadow_positive_ratio": 0.5,
                    "min_shadow_covered_weight": 0.95,
                },
                "data": {"max_cache_age_days": 10},
            },
            {"excess_total_return": 0.1},
            pd.DataFrame(
                [
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "unknown_industry_ratio", "value": 0.01},
                    {"item": "imputed_list_date_ratio", "value": 0.01},
                    {"item": "optimized_on_current_cache", "value": False},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
            shadow_forward_audit=pd.DataFrame(
                [
                    {"observed_trading_days": 5, "covered_weight": 1.0, "excess_return": 0.02, "status": "ok"},
                    {"observed_trading_days": 5, "covered_weight": 0.98, "excess_return": 0.01, "status": "ok"},
                ]
            ),
        )
        self.assertTrue(readiness_passed(readiness))

    def test_shadow_observation_filters_current_candidate_and_strategy(self):
        audit = pd.DataFrame(
            [
                {"observed_trading_days": 5, "covered_weight": 1.0, "excess_return": 0.02, "status": "ok", "signal_hash": "a", "candidate_id": "candidate-a", "strategy_hash": "strategy-a"},
                {"observed_trading_days": 5, "covered_weight": 1.0, "excess_return": 0.08, "status": "ok", "signal_hash": "b", "candidate_id": "candidate-b", "strategy_hash": "strategy-a"},
                {"observed_trading_days": 5, "covered_weight": 1.0, "excess_return": 0.06, "status": "ok", "signal_hash": "c", "candidate_id": "candidate-a", "strategy_hash": "strategy-b"},
            ]
        )
        stats = shadow_observation_values(audit, candidate_id="candidate-a", strategy_hash="strategy-a", require_current=True)
        self.assertEqual(stats["observed_signal_count"], 1)
        self.assertAlmostEqual(float(stats["avg_excess_return"]), 0.02)

        legacy = audit.drop(columns=["strategy_hash"])
        legacy_stats = shadow_observation_values(legacy, candidate_id="candidate-a", strategy_hash="strategy-a", require_current=True)
        self.assertEqual(legacy_stats["observed_signal_count"], 0)

    def test_shadow_observation_requires_minimum_days_per_signal(self):
        audit = pd.DataFrame(
            [
                {"observed_trading_days": 1, "covered_weight": 1.0, "excess_return": 0.03, "status": "ok", "signal_hash": "short"},
                {"observed_trading_days": 5, "covered_weight": 1.0, "excess_return": 0.02, "status": "ok", "signal_hash": "long"},
            ]
        )
        stats = shadow_observation_values(audit, min_observed_days=5)
        self.assertEqual(stats["observed_signal_count"], 1)
        self.assertAlmostEqual(float(stats["avg_excess_return"]), 0.02)

    def test_shadow_distinct_cohorts_do_not_count_near_duplicate_runs(self):
        audit = pd.DataFrame([
            {"execution_date": "2026-06-15", "observed_trading_days": 10, "covered_weight": 1.0, "excess_return": 0.02, "status": "ok", "signal_hash": "a"},
            {"execution_date": "2026-06-17", "observed_trading_days": 10, "covered_weight": 1.0, "excess_return": -0.03, "status": "ok", "signal_hash": "b"},
            {"execution_date": "2026-07-01", "observed_trading_days": 10, "covered_weight": 1.0, "excess_return": 0.04, "status": "ok", "signal_hash": "c"},
        ])

        stats = shadow_observation_values(audit, min_observed_days=5, min_distinct_gap_days=15)

        self.assertEqual(stats["observed_signal_count"], 3)
        self.assertEqual(stats["distinct_cohort_count"], 2)
        self.assertAlmostEqual(float(stats["avg_excess_return"]), 0.03)
        self.assertEqual(float(stats["positive_excess_ratio"]), 1.0)

    def test_readiness_roadmap_classifies_forward_and_shadow_gaps(self):
        readiness = pd.DataFrame(
            [
                {"check": "candidate_forward_trading_days", "passed": False, "value": 2, "threshold": 60, "message": "need days"},
                {"check": "shadow_observed_signal_count", "passed": False, "value": 0, "threshold": "20 signals with >= 5 days", "message": "need shadow"},
                {"check": "cost_stress_config_hash", "passed": False, "value": "old", "threshold": "new", "message": "stale stress"},
            ]
        )
        roadmap = build_live_readiness_roadmap(readiness, {})
        by_check = {row.check: row for row in roadmap.itertuples(index=False)}
        self.assertEqual(by_check["candidate_forward_trading_days"].status, "collecting_forward_data")
        self.assertEqual(by_check["candidate_forward_trading_days"].remaining, 58.0)
        self.assertEqual(by_check["shadow_observed_signal_count"].status, "collecting_shadow_signals")
        self.assertEqual(by_check["shadow_observed_signal_count"].remaining, 20)
        self.assertEqual(by_check["cost_stress_config_hash"].command, r".\scripts\aq.ps1 stress")

    def test_drawdown_and_turnover_are_live_gates(self):
        readiness = build_live_readiness(
            {
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "min_information_ratio": 0.30,
                    "max_tracking_error": 0.20,
                    "max_strategy_drawdown": 0.2,
                    "max_annualized_turnover": 2.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.0,
                    "min_price_symbol_count": 1,
                    "max_unknown_industry_ratio": 1.0,
                    "max_imputed_list_date_ratio": 1.0,
                    "allow_optimized_on_current_cache": True,
                },
                "data": {"max_cache_age_days": 10},
            },
            {
                "excess_total_return": 0.1,
                "information_ratio": 0.20,
                "tracking_error": 0.25,
                "max_drawdown": -0.25,
                "annualized_turnover": 3.0,
            },
            pd.DataFrame(
                [
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "unknown_industry_ratio", "value": 0.01},
                    {"item": "imputed_list_date_ratio", "value": 0.01},
                    {"item": "optimized_on_current_cache", "value": False},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
        )
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("information_ratio", failed)
        self.assertIn("tracking_error", failed)
        self.assertIn("max_strategy_drawdown", failed)
        self.assertIn("annualized_turnover", failed)

    def test_cost_stress_required_when_enabled(self):
        readiness = build_live_readiness(
            {
                "live_guard": {
                    "enabled": True,
                    "min_total_excess_return": 0.0,
                    "max_strategy_drawdown": 0.5,
                    "max_annualized_turnover": 10.0,
                    "min_out_of_sample_excess_return": 0.0,
                    "min_walk_forward_avg_excess_return": 0.0,
                    "min_walk_forward_positive_ratio": 0.0,
                    "min_price_symbol_count": 1,
                    "max_unknown_industry_ratio": 1.0,
                    "max_imputed_list_date_ratio": 1.0,
                    "allow_optimized_on_current_cache": True,
                    "require_cost_stress": True,
                    "require_current_cost_stress": False,
                    "cost_stress_scenario": "high_cost",
                    "min_cost_stress_excess_return": 0.02,
                    "max_cost_stress_drawdown": 0.25,
                },
                "data": {"max_cache_age_days": 10},
            },
            {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            pd.DataFrame(
                [
                    {"item": "price_symbol_count", "value": 500},
                    {"item": "unknown_industry_ratio", "value": 0.01},
                    {"item": "imputed_list_date_ratio", "value": 0.01},
                    {"item": "optimized_on_current_cache", "value": False},
                    {"item": "price_cache_age_days", "value": 1},
                ]
            ),
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
            cost_stress=pd.DataFrame([{"scenario": "high_cost", "excess_total_return": 0.01, "max_drawdown": -0.30}]),
        )
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("cost_stress_excess_return", failed)
        self.assertIn("cost_stress_max_drawdown", failed)

    def test_cost_stress_requires_current_config_hash(self):
        config = {
            "live_guard": {
                "enabled": True,
                "min_total_excess_return": 0.0,
                "max_strategy_drawdown": 0.5,
                "max_annualized_turnover": 10.0,
                "min_out_of_sample_excess_return": 0.0,
                "min_walk_forward_avg_excess_return": 0.0,
                "min_walk_forward_positive_ratio": 0.0,
                "min_price_symbol_count": 1,
                "max_unknown_industry_ratio": 1.0,
                "max_imputed_list_date_ratio": 1.0,
                "allow_optimized_on_current_cache": True,
                "require_cost_stress": True,
                "require_current_cost_stress": True,
                "cost_stress_scenario": "high_cost",
                "min_cost_stress_excess_return": 0.02,
                "max_cost_stress_drawdown": 0.25,
            },
            "data": {"max_cache_age_days": 10},
        }
        common_quality = pd.DataFrame(
            [
                {"item": "price_symbol_count", "value": 500},
                {"item": "unknown_industry_ratio", "value": 0.01},
                {"item": "imputed_list_date_ratio", "value": 0.01},
                {"item": "optimized_on_current_cache", "value": False},
                {"item": "price_cache_age_days", "value": 1},
            ]
        )
        readiness = build_live_readiness(
            config,
            {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            common_quality,
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
            cost_stress=pd.DataFrame(
                [{"scenario": "high_cost", "excess_total_return": 0.03, "max_drawdown": -0.1, "config_sha256": "old"}]
            ),
        )
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("cost_stress_config_hash", failed)

        manifest = pd.DataFrame(
            [
                {"item": "stocks_sha256", "value": "stocks"},
                {"item": "prices_sha256", "value": "prices"},
                {"item": "fundamentals_sha256", "value": "fundamentals"},
                {"item": "benchmark_sha256", "value": "benchmark"},
            ]
        )
        current = build_live_readiness(
            config,
            {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            common_quality,
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
            cost_stress=pd.DataFrame(
                [
                    {
                        "scenario": "high_cost",
                        "excess_total_return": 0.03,
                        "max_drawdown": -0.1,
                        "config_sha256": backtest_config_sha256(config),
                        "stocks_sha256": "stocks",
                        "prices_sha256": "prices",
                        "fundamentals_sha256": "fundamentals",
                        "benchmark_sha256": "benchmark",
                    }
                ]
            ),
            run_manifest=manifest,
        )
        self.assertTrue(readiness_passed(current))

        stale_data = build_live_readiness(
            config,
            {"excess_total_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            common_quality,
            pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.05}]),
            pd.DataFrame([{"avg_test_excess_return": 0.03, "positive_test_ratio": 1.0}]),
            cost_stress=pd.DataFrame(
                [
                    {
                        "scenario": "high_cost",
                        "excess_total_return": 0.03,
                        "max_drawdown": -0.1,
                        "config_sha256": backtest_config_sha256(config),
                        "stocks_sha256": "stocks",
                        "prices_sha256": "old_prices",
                        "fundamentals_sha256": "fundamentals",
                        "benchmark_sha256": "benchmark",
                    }
                ]
            ),
            run_manifest=manifest,
        )
        failed = set(stale_data.loc[~stale_data["passed"], "check"])
        self.assertIn("cost_stress_data_hash", failed)

    def test_readiness_evidence_detects_config_drift(self):
        root = Path.cwd() / "test_outputs" / "readiness_evidence"
        config = load_config()
        config["paths"]["cache_dir"] = str(root / "cache")
        config["paths"]["state_db"] = str(root / "state.sqlite3")
        config["paths"]["output_dir"] = str(root / "outputs")
        store = DataStore.from_config(config)
        make_sample_data(store)
        frames = current_manifest_frames(config)
        output_dir = Path(config["paths"]["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        build_run_manifest(config, **frames).to_csv(output_dir / "run_manifest.csv", index=False, encoding="utf-8-sig")

        operational_drift = load_config()
        operational_drift["paths"] = config["paths"].copy()
        operational_drift["observation_task"]["required"] = True
        self.assertEqual(readiness_evidence_issues(operational_drift), [])

        self.assertEqual(readiness_evidence_issues(config), [])

        drifted = load_config()
        drifted["paths"] = config["paths"].copy()
        drifted["strategy"]["top_n"] = int(drifted["strategy"]["top_n"]) + 1
        issues = readiness_evidence_issues(drifted)
        self.assertIn("backtest_config_sha256 不一致", issues)

    def test_ensure_readiness_recomputes_outputs_instead_of_trusting_stale_csv(self):
        root = Path.cwd() / "test_outputs" / "runtime_readiness"
        config = load_config()
        config["paths"]["cache_dir"] = str(root / "cache")
        config["paths"]["state_db"] = str(root / "state.sqlite3")
        config["paths"]["output_dir"] = str(root / "outputs")
        config["data"]["max_cache_age_days"] = 9999
        config["live_guard"].update(
            {
                "min_total_excess_return": 0.0,
                "min_out_of_sample_excess_return": 0.0,
                "min_walk_forward_avg_excess_return": 0.0,
                "min_walk_forward_positive_ratio": 0.0,
                "min_price_symbol_count": 1,
                "max_unknown_industry_ratio": 1.0,
                "max_imputed_list_date_ratio": 1.0,
                "allow_optimized_on_current_cache": True,
                "require_shadow_observation": True,
                "require_current_candidate_shadow": False,
                "min_shadow_observed_signals": 1,
                "min_shadow_avg_excess_return": 0.0,
                "min_shadow_positive_ratio": 0.0,
                "min_shadow_covered_weight": 0.0,
            }
        )
        store = DataStore.from_config(config)
        make_sample_data(store)
        frames = current_manifest_frames(config)
        output_dir = Path(config["paths"]["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        build_run_manifest(config, **frames).to_csv(output_dir / "run_manifest.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame([{"check": "stale_all_passed", "passed": True, "value": "old", "threshold": "", "message": ""}]).to_csv(
            output_dir / "live_readiness.csv", index=False, encoding="utf-8-sig"
        )
        pd.DataFrame(
            [
                {"date": "2026-01-01", "strategy_nav": 1.0, "benchmark_nav": 1.0, "strategy_return": 0.0, "benchmark_return": 0.0, "excess_return": 0.0},
                {"date": "2026-01-02", "strategy_nav": 1.1, "benchmark_nav": 1.0, "strategy_return": 0.1, "benchmark_return": 0.0, "excess_return": 0.1},
            ]
        ).to_csv(output_dir / "benchmark_comparison.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [
                {"item": "price_symbol_count", "value": 10},
                {"item": "price_cache_age_days", "value": 0},
                {"item": "unknown_industry_ratio", "value": 0.0},
                {"item": "imputed_list_date_ratio", "value": 0.0},
                {"item": "optimized_on_current_cache", "value": False},
            ]
        ).to_csv(output_dir / "data_quality.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.1}]).to_csv(
            output_dir / "robustness_split.csv", index=False, encoding="utf-8-sig"
        )
        pd.DataFrame([{"avg_test_excess_return": 0.1, "positive_test_ratio": 1.0}]).to_csv(
            output_dir / "walk_forward_summary.csv", index=False, encoding="utf-8-sig"
        )
        pd.DataFrame(columns=["signal_date", "execution_date", "observed_trading_days", "covered_weight", "excess_return", "status"]).to_csv(
            output_dir / "shadow_forward_audit.csv", index=False, encoding="utf-8-sig"
        )

        with self.assertRaisesRegex(RuntimeError, "shadow_observed_signal_count"):
            ensure_live_readiness(config)


    def test_strict_readiness_requires_factor_robustness_when_configured(self):
        config = {
            "live_guard": {
                "enabled": True,
                "require_factor_robustness": True,
                "allow_optimized_on_current_cache": True,
            },
            "data": {"max_cache_age_days": 9999},
        }
        common = {
            "config": config,
            "metrics": {},
            "data_quality": pd.DataFrame(),
            "robustness": pd.DataFrame(),
            "walk_forward_summary": pd.DataFrame(),
        }

        missing = build_live_readiness(**common)
        factor_summary = pd.DataFrame([
            {"check": "factor_robustness_status", "passed": True, "value": "passed"},
            {"check": "factor_robustness_config_sha256", "passed": True, "value": backtest_config_sha256(config)},
            {"check": "factor_robustness_data_sha256", "passed": True, "value": "a" * 64},
            {"check": "factor_robustness_forward_isolation", "passed": True, "value": "sample_end=2026-01-01;freeze_cutoff=2026-01-01"},
        ])
        with patch(
            "ashare_quant.readiness.expected_factor_robustness_data_sha256",
            return_value="a" * 64,
        ):
            passed = build_live_readiness(
                **common,
                factor_robustness_summary=factor_summary,
            )
        with patch(
            "ashare_quant.readiness.expected_factor_robustness_data_sha256",
            return_value="b" * 64,
        ):
            stale = build_live_readiness(
                **common,
                factor_robustness_summary=factor_summary,
            )

        missing_row = missing[missing["check"] == "factor_robustness_status"].iloc[0]
        passed_row = passed[passed["check"] == "factor_robustness_status"].iloc[0]
        self.assertFalse(bool(missing_row["passed"]))
        self.assertEqual(missing_row["value"], "missing")
        self.assertTrue(bool(passed_row["passed"]))
        self.assertEqual(passed_row["value"], "passed")
        for check in (
            "factor_robustness_config_current",
            "factor_robustness_data_bound",
            "factor_robustness_data_current",
            "factor_robustness_forward_isolation",
        ):
            row = passed[passed["check"] == check].iloc[0]
            self.assertTrue(bool(row["passed"]), check)
        stale_data = stale[stale["check"] == "factor_robustness_data_current"].iloc[0]
        self.assertFalse(bool(stale_data["passed"]))
        self.assertEqual(stale_data["threshold"], "b" * 64)
if __name__ == "__main__":
    unittest.main()
