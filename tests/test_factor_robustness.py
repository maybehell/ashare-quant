from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.candidate import candidate_evidence_identity_sha256
from ashare_quant.data import DataStore, make_sample_data
from ashare_quant.factor_robustness import (
    ensemble_diversity_summary,
    factor_robustness_candidates,
    factor_robustness_market_data,
    factor_robustness_provenance,
    factor_robustness_summary,
    robustness_status,
    run_factor_robustness,
)


class FactorRobustnessTests(unittest.TestCase):
    def test_candidate_evidence_identity_changes_on_refreeze(self):
        manifest = {
            "candidate_id": "candidate-a",
            "strategy_hash": "strategy",
            "freeze_mode": "forward_only_preregistration",
            "freeze_price_end": "2026-01-01",
            "frozen_prices_sha256": "prices",
        }
        first = candidate_evidence_identity_sha256(manifest)
        second = candidate_evidence_identity_sha256({**manifest, "candidate_id": "candidate-b"})
        moved_boundary = candidate_evidence_identity_sha256({**manifest, "freeze_price_end": "2026-01-02"})

        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, moved_boundary)
    def test_forward_candidate_factor_data_is_capped_at_freeze_boundary(self):
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        stocks = pd.DataFrame([{"symbol": "000001"}])
        prices = pd.DataFrame({"date": dates.date, "symbol": ["000001"] * 5, "close": range(5)})
        fundamentals = pd.DataFrame({"date": dates.date, "symbol": ["000001"] * 5, "roe": range(5)})
        benchmark = pd.DataFrame({"date": dates.date, "symbol": ["000300"] * 5, "close": range(5)})
        manifest = {
            "freeze_mode": "forward_only_preregistration",
            "freeze_known_data_end": "2026-01-03",
        }

        with (
            patch("ashare_quant.factor_robustness.load_research_market_data", return_value=(stocks, prices, fundamentals, benchmark)),
            patch("ashare_quant.factor_robustness.load_candidate_manifest", return_value=manifest),
        ):
            _, capped_prices, capped_fundamentals, capped_benchmark, cutoff = factor_robustness_market_data({}, object())

        self.assertEqual(cutoff, "2026-01-03")
        self.assertEqual(max(capped_prices["date"]).isoformat(), cutoff)
        self.assertEqual(max(capped_fundamentals["date"]).isoformat(), cutoff)
        self.assertEqual(max(capped_benchmark["date"]).isoformat(), cutoff)

    def test_factor_provenance_binds_config_data_and_forward_isolation(self):
        config = self.make_config("factor_provenance")
        dates = pd.to_datetime(["2026-01-01", "2026-01-02"]).date
        stocks = pd.DataFrame([{"symbol": "000001"}])
        prices = pd.DataFrame({"date": dates, "symbol": ["000001"] * 2, "close": [1.0, 1.1]})
        fundamentals = pd.DataFrame({"date": dates, "symbol": ["000001"] * 2, "roe": [0.1, 0.1]})
        benchmark = pd.DataFrame({"date": dates, "symbol": ["000300"] * 2, "close": [1.0, 1.0]})

        manifest = {
            "candidate_id": "candidate-a",
            "strategy_hash": "strategy",
            "freeze_mode": "forward_only_preregistration",
            "freeze_price_end": "2026-01-02",
            "frozen_prices_sha256": "prices",
        }
        with patch("ashare_quant.factor_robustness.load_candidate_manifest", return_value=manifest):
            provenance = factor_robustness_provenance(
                config, stocks, prices, fundamentals, benchmark, "2026-01-02"
            ).set_index("check")

        self.assertEqual(len(str(provenance.at["factor_robustness_candidate_identity_sha256", "value"])), 64)
        self.assertEqual(len(str(provenance.at["factor_robustness_config_sha256", "value"])), 64)
        self.assertEqual(len(str(provenance.at["factor_robustness_data_sha256", "value"])), 64)
        self.assertTrue(bool(provenance.at["factor_robustness_forward_isolation", "passed"]))
        self.assertEqual(provenance.at["factor_robustness_sample_end", "value"], "2026-01-02")
    def test_factor_candidates_drop_and_perturb_active_weights(self):
        config = self.make_config("factor_candidates")
        candidates = factor_robustness_candidates(config)
        scenarios = [candidate["scenario"] for candidate in candidates]

        self.assertEqual(scenarios.count("current"), 1)
        self.assertEqual(scenarios.count("drop_factor"), 3)
        self.assertEqual(scenarios.count("factor_up"), 3)
        self.assertEqual(scenarios.count("factor_down"), 3)

        base_sum = sum(config["strategy"]["weights"].values())
        for candidate in candidates:
            self.assertAlmostEqual(sum(candidate["weights"].values()), base_sum, places=8)
        dropped_liquidity = next(candidate for candidate in candidates if candidate["scenario"] == "drop_factor" and candidate["factor"] == "liquidity")
        self.assertEqual(dropped_liquidity["weights"]["liquidity"], 0.0)

    def test_factor_robustness_writes_result_and_summary(self):
        config = self.make_config("factor_run")
        make_sample_data(DataStore.from_config(config))

        paths = run_factor_robustness(config)

        result = pd.read_csv(paths["factor_robustness.csv"])
        summary = pd.read_csv(paths["factor_robustness_summary.csv"])
        self.assertFalse(result.empty)
        self.assertFalse(summary.empty)
        self.assertIn("out_of_sample_delta_vs_base", result.columns)
        self.assertIn("factor_robustness_status", summary["check"].tolist())

    def test_summary_flags_fragile_scenarios(self):
        result = pd.DataFrame(
            [
                {"scenario": "current", "factor": "", "passed": True, "out_of_sample_delta_vs_base": 0.0, "excess_delta_vs_base": 0.0, "max_drawdown_delta_vs_base": 0.0},
                {"scenario": "drop_factor", "factor": "liquidity", "passed": False, "out_of_sample_delta_vs_base": -0.2, "excess_delta_vs_base": -0.1, "max_drawdown_delta_vs_base": -0.05},
            ]
        )

        summary = factor_robustness_summary({"factor_robustness": {"min_pass_ratio": 0.6}}, result)

        values = dict(zip(summary["check"], summary["value"]))
        passed = dict(zip(summary["check"], summary["passed"]))
        self.assertEqual(values["fragile_scenario_count"], 1)
        self.assertFalse(bool(passed["factor_robustness_status"]))

    def test_summary_accepts_bounded_factor_fragility_but_requires_clean_members(self):
        rows = [
            {"scenario": "current", "factor": "", "passed": True, "status": "baseline", "ensemble_members": '[{"name":"a","blend_weight":1},{"name":"b","blend_weight":1}]', "out_of_sample_delta_vs_base": 0.0, "excess_delta_vs_base": 0.0, "max_drawdown_delta_vs_base": 0.0},
        ]
        for index in range(8):
            rows.append({"scenario": "drop_factor" if index < 4 else "factor_down", "factor": f"f{index % 4}", "passed": index != 0, "ensemble_members": "[]", "out_of_sample_delta_vs_base": -0.02, "excess_delta_vs_base": -0.02, "max_drawdown_delta_vs_base": -0.01})
        for scenario in ("drop_member", "member_up", "member_down"):
            rows.append({"scenario": scenario, "factor": "", "passed": True, "ensemble_members": "[]", "out_of_sample_delta_vs_base": -0.01, "excess_delta_vs_base": -0.01, "max_drawdown_delta_vs_base": 0.0})
        config = {"factor_robustness": {"min_pass_ratio": 0.75, "max_fragile_scenario_ratio": 0.25, "max_fragile_factor_ratio": 0.30, "require_zero_fragile_member_scenarios": True}}

        summary = factor_robustness_summary(config, pd.DataFrame(rows)).set_index("check")

        self.assertTrue(bool(summary.at["factor_robustness_status", "passed"]))
        self.assertEqual(float(summary.at["fragile_factor_scenario_ratio", "value"]), 0.125)
        rows[-1]["passed"] = False
        blocked = factor_robustness_summary(config, pd.DataFrame(rows)).set_index("check")
        self.assertFalse(bool(blocked.at["factor_robustness_status", "passed"]))
        self.assertFalse(bool(blocked.at["fragile_member_scenario_count", "passed"]))

    def test_summary_rejects_failed_baseline_even_when_all_stresses_pass(self):
        result = pd.DataFrame([
            {"scenario": "current", "factor": "", "passed": False, "status": "review", "out_of_sample_delta_vs_base": 0.0, "excess_delta_vs_base": 0.0, "max_drawdown_delta_vs_base": 0.0},
            {"scenario": "factor_up", "factor": "momentum", "passed": True, "out_of_sample_delta_vs_base": 0.0, "excess_delta_vs_base": 0.0, "max_drawdown_delta_vs_base": 0.0},
        ])
        summary = factor_robustness_summary({"factor_robustness": {"min_pass_ratio": 0.75}}, result).set_index("check")
        self.assertFalse(bool(summary.at["baseline_anti_overfit_passed", "passed"]))
        self.assertFalse(bool(summary.at["factor_robustness_status", "passed"]))


    def test_relative_perturbation_status_does_not_duplicate_baseline_gate(self):
        config = {
            "factor_robustness": {
                "max_total_excess_loss": 0.10,
                "max_oos_excess_loss": 0.08,
                "max_drawdown_worsening": 0.08,
            }
        }
        candidate = {"scenario": "factor_down"}
        metrics = {"anti_overfit_passed": False}

        passed, status, message = robustness_status(config, candidate, metrics, -0.01, -0.02, -0.01)
        fragile, _, fragile_message = robustness_status(config, candidate, metrics, -0.11, -0.02, -0.01)

        self.assertTrue(passed)
        self.assertEqual(status, "ok")
        self.assertEqual(message, "factor_robustness_ok")
        self.assertFalse(fragile)
        self.assertIn("total_excess_sensitive", fragile_message)
    def make_config(self, name: str) -> dict:
        root = Path.cwd() / "test_outputs" / name
        return {
            "paths": {
                "cache_dir": root / "cache",
                "output_dir": root / "outputs",
                "state_db": root / "state.sqlite3",
            },
            "data": {"max_symbols": 20, "min_daily_price_coverage_ratio": 0.9, "require_benchmark_on_signal_date": False},
            "universe": {
                "boards": ["main", "gem", "star"],
                "exclude_st": True,
                "min_listing_days": 60,
                "min_avg_amount": 0,
                "liquidity_lookback": 20,
            },
            "backtest": {"start": "2023-01-01", "end": None, "initial_cash": 1000000, "rebalance": "monthly"},
            "validation": {"holdout_split_ratio": 0.7},
            "risk_overlay": {"enabled": False},
            "factor_robustness": {"shock": 0.25, "min_pass_ratio": 0.6},
            "strategy": {
                "name": "formal",
                "top_n": 4,
                "max_stock_weight": 0.25,
                "max_industry_weight": 0.60,
                "weighting": "liquidity_sqrt",
                "lookbacks": {"momentum": 20, "long_momentum": 40, "volatility": 20, "drawdown": 20, "liquidity": 20},
                "weights": {"momentum": 0.2, "low_volatility": 0.3, "liquidity": 0.5},
            },
            "trading": {
                "lot_size": 100,
                "slippage_bps": 5,
                "commission_rate": 0.0003,
                "min_commission": 5,
                "stamp_tax_rate": 0.0005,
            },
        }


    def test_ensemble_factor_perturbations_change_members_not_ignored_base_weights(self):
        config = {
            "strategy": {
                "name": "ensemble",
                "weights": {"liquidity": 1.0},
                "ensemble_members": [
                    {"blend_weight": 1.0, "weights": {"liquidity": 0.6, "momentum": 0.4}},
                    {"blend_weight": 1.0, "weights": {"liquidity": 0.3, "low_volatility": 0.7}},
                ],
            },
            "factor_robustness": {"shock": 0.25},
        }
        candidates = factor_robustness_candidates(config)
        dropped = next(row for row in candidates if row["scenario"] == "drop_factor" and row["factor"] == "liquidity")
        for original, changed in zip(config["strategy"]["ensemble_members"], dropped["ensemble_members"]):
            self.assertAlmostEqual(sum(original["weights"].values()), sum(changed["weights"].values()))
            self.assertEqual(changed["weights"]["liquidity"], 0.0)
        momentum_drop = next(row for row in candidates if row["scenario"] == "drop_factor" and row["factor"] == "momentum")
        self.assertEqual(momentum_drop["ensemble_members"][1]["weights"], config["strategy"]["ensemble_members"][1]["weights"])

        self.assertEqual(sum(row["scenario"] == "drop_member" for row in candidates), 2)
        self.assertEqual(sum(row["scenario"] == "member_up" for row in candidates), 2)
        self.assertEqual(sum(row["scenario"] == "member_down" for row in candidates), 2)
        dropped_first = next(row for row in candidates if row["scenario"] == "drop_member" and row["member"] == "member_1")
        self.assertEqual(dropped_first["ensemble_members"][0]["blend_weight"], 0.0)
        self.assertEqual(dropped_first["ensemble_members"][1]["blend_weight"], 1.0)
        raised_first = next(row for row in candidates if row["scenario"] == "member_up" and row["member"] == "member_1")
        self.assertEqual(raised_first["ensemble_members"][0]["blend_weight"], 1.25)

    def test_summary_reports_member_level_fragility(self):
        result = pd.DataFrame(
            [
                {
                    "scenario": "current",
                    "factor": "",
                    "passed": True,
                    "ensemble_members": '[{"name":"alpha","blend_weight":1},{"name":"defensive","blend_weight":1}]',
                    "out_of_sample_delta_vs_base": 0.0,
                    "excess_delta_vs_base": 0.0,
                    "max_drawdown_delta_vs_base": 0.0,
                },
                {
                    "scenario": "drop_member",
                    "factor": "",
                    "passed": False,
                    "ensemble_members": "[]",
                    "out_of_sample_delta_vs_base": -0.2,
                    "excess_delta_vs_base": -0.1,
                    "max_drawdown_delta_vs_base": -0.05,
                },
            ]
        )

        summary = factor_robustness_summary({"factor_robustness": {"min_pass_ratio": 0.6}}, result).set_index("check")

        self.assertEqual(int(summary.at["ensemble_member_count", "value"]), 2)
        self.assertEqual(int(summary.at["member_stress_scenario_count", "value"]), 1)
        self.assertEqual(int(summary.at["fragile_member_scenario_count", "value"]), 1)
        self.assertFalse(bool(summary.at["factor_robustness_status", "passed"]))


    def test_ensemble_diversity_distinguishes_independent_and_duplicate_members(self):
        class FakeCache:
            def __init__(self, duplicate: bool):
                self.duplicate = duplicate
                self.prices = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=8, freq="MS")})

            def scores(self, asof, member_config):
                name = member_config["strategy"]["name"]
                symbols = ["A", "B", "E", "F"] if name == "alpha" or self.duplicate else ["C", "D", "G", "H"]
                return pd.DataFrame(
                    {
                        "symbol": symbols,
                        "score": [4.0, 3.0, 2.0, 1.0],
                        "industry": ["i1", "i2", "i3", "i4"],
                        "board": ["main"] * 4,
                    }
                )

        config = {
            "ensemble_diversity": {
                "lookback_rebalances": 8,
                "min_rebalances": 6,
                "max_mean_weight_overlap": 0.5,
                "max_single_weight_overlap": 0.75,
                "max_mean_holding_jaccard": 0.5,
                "min_effective_holdings": 3,
            },
            "strategy": {
                "top_n": 2,
                "max_stock_weight": 0.5,
                "max_industry_weight": 1.0,
                "weighting": "equal",
                "ensemble_members": [
                    {"name": "alpha", "blend_weight": 1.0, "weights": {"momentum": 1.0}},
                    {"name": "defensive", "blend_weight": 1.0, "weights": {"low_volatility": 1.0}},
                ],
            },
        }

        independent = ensemble_diversity_summary(config, FakeCache(False)).set_index("check")
        duplicate = ensemble_diversity_summary(config, FakeCache(True)).set_index("check")

        self.assertTrue(bool(independent.at["ensemble_diversity_status", "passed"]))
        self.assertEqual(float(independent.at["mean_member_weight_overlap", "value"]), 0.0)
        self.assertFalse(bool(duplicate.at["ensemble_diversity_status", "passed"]))
        self.assertEqual(float(duplicate.at["mean_member_weight_overlap", "value"]), 1.0)


if __name__ == "__main__":
    unittest.main()
