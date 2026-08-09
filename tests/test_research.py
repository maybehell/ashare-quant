from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from ashare_quant.audit import frame_sha256
from ashare_quant.data import DataStore, make_sample_data
from ashare_quant.research import ResearchSimulationCache, baseline_candidate_list, candidate_list, run_strategy_baselines, run_strategy_research
from ashare_quant.validation import filter_complete_market_data, max_lookback_days
from ashare_quant.walkforward import apply_candidate, simulate_period


class ResearchTests(unittest.TestCase):
    def test_strategy_research_writes_ranked_candidates(self):
        root = Path.cwd() / "test_outputs" / "research"
        config = {
            "paths": {
                "cache_dir": root / "cache",
                "output_dir": root / "outputs",
                "state_db": root / "state.sqlite3",
            },
            "data": {"max_symbols": 20},
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
            "strategy": {
                "name": "base",
                "top_n": 4,
                "max_stock_weight": 0.25,
                "max_industry_weight": 0.60,
                "lookbacks": {"momentum": 20, "long_momentum": 40, "volatility": 20, "drawdown": 20, "liquidity": 20},
                "weights": {"momentum": 0.3, "long_momentum": 0.2, "low_volatility": 0.2, "liquidity": 0.3},
            },
            "trading": {
                "lot_size": 100,
                "slippage_bps": 5,
                "commission_rate": 0.0003,
                "min_commission": 5,
                "stamp_tax_rate": 0.0005,
            },
            "walk_forward": {
                "candidates": [
                    {"name": "liquidity", "top_n": 4, "weights": {"liquidity": 1.0}},
                    {"name": "lowvol", "top_n": 4, "weights": {"low_volatility": 1.0}, "ensemble_members": [{"name": "lowvol_member", "blend_weight": 1.0, "top_n": 4, "max_stock_weight": 0.25, "weights": {"low_volatility": 1.0}}]},
                ]
            },
        }
        make_sample_data(DataStore.from_config(config))
        path = run_strategy_research(config)
        result = pd.read_csv(path)
        self.assertFalse(result.empty)
        self.assertEqual(result["rank"].iloc[0], 1)
        self.assertIn("out_of_sample_excess_return", result.columns)
        self.assertIn("in_sample_excess_return", result.columns)
        self.assertIn("recent_excess_concentration", result.columns)
        self.assertIn("annual_positive_ratio", result.columns)
        self.assertIn("anti_overfit_passed", result.columns)
        self.assertIn("overfit_penalty", result.columns)
        self.assertIn("stability_score", result.columns)
        self.assertIn("time_fold_stability_passed", result.columns)
        self.assertIn("time_fold_positive_ratio", result.columns)
        self.assertIn("time_fold_top_half_ratio", result.columns)
        self.assertIn("strategy_hash", result.columns)
        self.assertIn("ensemble_members", result.columns)
        self.assertIn("research_origin", result.columns)
        self.assertIn("gross_strategy_return", result.columns)
        self.assertIn("transaction_cost_drag", result.columns)
        self.assertIn("estimated_trading_cost", result.columns)
        self.assertIn("annualized_turnover", result.columns)
        self.assertIn("rebalance_count", result.columns)
        self.assertIn("source_config", result.columns)
        self.assertIn("research_only", result.columns)
        self.assertIn("experiment_status", result.columns)
        self.assertIn("experiment_reason", result.columns)
        self.assertIn("config_overrides", result.columns)
        self.assertTrue(result["strategy_hash"].astype(str).str.len().gt(0).all())
        self.assertTrue((pd.to_numeric(result["time_fold_count"], errors="coerce") >= 2).all())
        self.assertIn("lowvol_member", result.loc[result["name"] == "lowvol", "ensemble_members"].iloc[0])
        self.assertTrue((pd.to_numeric(result["transaction_cost_drag"], errors="coerce") >= 0).all())
        self.assertTrue((pd.to_numeric(result["annualized_turnover"], errors="coerce") > 0).all())

        manifest_path = path.parent / "strategy_research_manifest.csv"
        self.assertTrue(manifest_path.exists())
        manifest = pd.read_csv(manifest_path)
        values = dict(zip(manifest["item"], manifest["value"]))
        self.assertEqual(int(values["strategy_research_rows"]), len(result))
        self.assertEqual(values["strategy_research_sha256"], frame_sha256(result))
        bias = pd.read_csv(path.parent / "strategy_selection_bias.csv")
        splits = pd.read_csv(path.parent / "strategy_selection_bias_splits.csv")
        families = pd.read_csv(path.parent / "strategy_candidate_family.csv")
        similarities = pd.read_csv(path.parent / "strategy_candidate_similarity.csv")
        family_selection = pd.read_csv(path.parent / "strategy_family_selection.csv")
        family_selection_splits = pd.read_csv(path.parent / "strategy_family_selection_splits.csv")
        self.assertFalse(bias.empty)
        self.assertIn("strategy_selection_bias_ready", bias["item"].tolist())
        self.assertEqual(int(values["strategy_selection_bias_rows"]), len(bias))
        self.assertEqual(values["strategy_selection_bias_sha256"], frame_sha256(bias))
        self.assertEqual(int(values["strategy_selection_bias_splits_rows"]), len(splits))
        self.assertEqual(values["strategy_selection_bias_splits_sha256"], frame_sha256(splits))

        self.assertEqual(int(values["strategy_candidate_family_rows"]), len(families))
        self.assertEqual(values["strategy_candidate_family_sha256"], frame_sha256(families))
        self.assertEqual(int(values["strategy_candidate_similarity_rows"]), len(similarities))
        self.assertEqual(values["strategy_candidate_similarity_sha256"], frame_sha256(similarities))
        self.assertEqual(int(values["strategy_family_selection_rows"]), len(family_selection))
        self.assertEqual(values["strategy_family_selection_sha256"], frame_sha256(family_selection))
        self.assertEqual(int(values["strategy_family_selection_splits_rows"]), len(family_selection_splits))
        self.assertEqual(values["strategy_family_selection_splits_sha256"], frame_sha256(family_selection_splits))

    def test_research_refresh_skips_unapproved_candidates_when_gate_is_closed(self):
        root = Path.cwd() / "test_outputs" / "research_gate_filter"
        config = {"paths": {"output_dir": root, "state_db": root / "state.sqlite3"}}
        approved = {"name": "approved", "weights": {"liquidity": 1.0}}
        blocked = {"name": "blocked", "weights": {"momentum": 1.0}}
        configured = [approved, blocked]
        snapshot = {"status": "stop_new_candidates", "new_candidates": ["blocked"]}
        store = MagicMock()

        with (
            patch("ashare_quant.research.candidate_list", return_value=configured),
            patch("ashare_quant.research.gate_snapshot", return_value=snapshot),
            patch("ashare_quant.research.assert_research_candidates_allowed") as allowed,
            patch("ashare_quant.research.evaluate_candidates", return_value=[]) as evaluate,
            patch("ashare_quant.research.write_ranked_result"),
            patch("ashare_quant.research.refresh_strategy_research_metadata"),
            patch("ashare_quant.research.write_strategy_selection_bias"),
            patch("ashare_quant.research.write_strategy_research_manifest"),
            patch("ashare_quant.research.write_research_gate") as write_gate,
            patch("ashare_quant.research.DataStore.from_config", return_value=store),
        ):
            run_strategy_research(config)

        allowed.assert_called_once_with(config, [approved])
        evaluate.assert_called_once_with(config, [approved])
        self.assertEqual(write_gate.call_args_list[0].args, (config, [approved]))
        self.assertTrue(write_gate.call_args_list[0].kwargs["commit"])
        self.assertEqual(write_gate.call_args_list[1].args, (config, configured))
        self.assertFalse(write_gate.call_args_list[1].kwargs["commit"])
        store.log_run.assert_called_once_with(
            "research", "ok", "evaluated 0 candidates; blocked_new=1"
        )
    @patch("ashare_quant.research.load_config")
    def test_candidate_list_marks_archived_configs_and_preserves_overrides(self, mocked_load_config):
        mocked_load_config.return_value = {
            "strategy": {
                "name": "archived",
                "top_n": 80,
                "weights": {"residual_reversal": 1.0},
            },
            "validation": {
                "research_only": True,
                "experiment_status": "rejected",
                "rejection_reason": "negative held-out excess",
            },
            "breadth_overlay": {"enabled": True, "minimum_exposure": 0.4},
        }
        config = {
            "strategy": {"name": "formal", "weights": {"liquidity": 1.0}},
            "walk_forward": {
                "candidates": [
                    {"name": "formal", "weights": {"momentum": 1.0}},
                    {"name": "archived", "weights": {"liquidity": 1.0}},
                ]
            },
            "validation": {"research_archive_configs": ["configs/archive.yaml"]},
            "breadth_overlay": {"enabled": False},
        }

        candidates = candidate_list(config)

        formal = next(candidate for candidate in candidates if candidate["name"] == "formal")
        self.assertEqual(formal["research_origin"], "formal")
        self.assertEqual(formal["experiment_status"], "formal")
        archived = next(candidate for candidate in candidates if candidate["name"] == "archived")
        self.assertEqual(archived["research_origin"], "walk_forward+archive")
        self.assertEqual(archived["source_config"], "configs/archive.yaml")
        self.assertTrue(archived["research_only"])
        self.assertEqual(archived["experiment_status"], "rejected")
        self.assertEqual(archived["experiment_reason"], "negative held-out excess")
        self.assertTrue(archived["config_overrides"]["breadth_overlay"]["enabled"])
        self.assertEqual(archived["weights"], {"liquidity": 1.0})
        mocked_load_config.assert_called_once_with("configs/archive.yaml")

    @patch("ashare_quant.research.load_config")
    def test_archived_candidate_missing_overlay_explicitly_disables_current_overlay(self, mocked_load_config):
        mocked_load_config.return_value = {
            "strategy": {"name": "archived", "weights": {"liquidity": 1.0}},
            "validation": {"research_only": True},
        }
        config = {
            "strategy": {"name": "formal", "weights": {"momentum": 1.0}},
            "walk_forward": {"candidates": []},
            "validation": {"research_archive_configs": ["configs/archive.yaml"]},
            "breadth_overlay": {"enabled": True},
        }

        archived = next(candidate for candidate in candidate_list(config) if candidate["name"] == "archived")
        applied = apply_candidate(config, archived)

        self.assertEqual(archived["config_overrides"]["breadth_overlay"], {})
        self.assertEqual(applied["breadth_overlay"], {})

    def test_strategy_baselines_include_formal_and_common_factor_references(self):
        root = Path.cwd() / "test_outputs" / "baselines"
        config = {
            "paths": {
                "cache_dir": root / "cache",
                "output_dir": root / "outputs",
                "state_db": root / "state.sqlite3",
            },
            "data": {"max_symbols": 20},
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
            "strategy": {
                "name": "formal",
                "top_n": 4,
                "max_stock_weight": 0.25,
                "max_industry_weight": 0.60,
                "lookbacks": {"momentum": 20, "long_momentum": 40, "volatility": 20, "drawdown": 20, "liquidity": 20},
                "weights": {"momentum": 0.3, "low_volatility": 0.3, "liquidity": 0.4},
            },
            "trading": {
                "lot_size": 100,
                "slippage_bps": 5,
                "commission_rate": 0.0003,
                "min_commission": 5,
                "stamp_tax_rate": 0.0005,
            },
        }
        names = [candidate["name"] for candidate in baseline_candidate_list(config)]
        self.assertIn("formal", names)
        self.assertIn("baseline_low_volatility", names)
        self.assertIn("baseline_conservative_proxy", names)

        make_sample_data(DataStore.from_config(config))
        path = run_strategy_baselines(config)
        result = pd.read_csv(path)
        self.assertFalse(result.empty)
        self.assertIn("baseline_type", result.columns)
        self.assertIn("description", result.columns)
        self.assertIn("formal", result["name"].tolist())
        self.assertIn("common_factor", result["baseline_type"].tolist())

    def test_research_simulation_cache_matches_reference_simulator(self):
        root = Path.cwd() / "test_outputs" / "research_cache"
        config = {
            "paths": {
                "cache_dir": root / "cache",
                "output_dir": root / "outputs",
                "state_db": root / "state.sqlite3",
            },
            "data": {"max_symbols": 20},
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
            "strategy": {
                "name": "formal",
                "top_n": 4,
                "max_stock_weight": 0.25,
                "max_industry_weight": 0.60,
                "weighting": "liquidity_sqrt",
                "lookbacks": {"momentum": 20, "long_momentum": 40, "volatility": 20, "drawdown": 20, "liquidity": 20},
                "weights": {"low_drawdown": 1.0},
            },
            "trading": {
                "lot_size": 100,
                "slippage_bps": 5,
                "commission_rate": 0.0003,
                "min_commission": 5,
                "stamp_tax_rate": 0.0005,
            },
        }
        store = DataStore.from_config(config)
        make_sample_data(store)
        stocks, prices, fundamentals = store.load_market_data()
        benchmark = store.load_benchmark()
        prices, benchmark = filter_complete_market_data(prices, benchmark, config)
        candidate_config = apply_candidate(config, baseline_candidate_list(config)[0])
        candidate_config["strategy"]["adaptive_weights"] = {
            "enabled": True,
            "benchmark_ma_days": 20,
            "benchmark_vol_days": 20,
            "high_volatility_threshold": 1.0,
            "risk_on_blend": 0.5,
            "risk_on_weights": {"momentum": 0.2, "low_drawdown": 0.4, "liquidity": 0.4},
        }
        candidate_config["strategy"]["industry_neutralization"] = {
            "enabled": True,
            "min_group_size": 2,
        }
        candidate_config["strategy"]["cross_sectional_neutralization"] = {
            "enabled": True,
            "exposures": ["liquidity"],
            "min_observations": 5,
        }
        dates = sorted(prices["date"].unique())
        usable_dates = dates[max_lookback_days(candidate_config) :]

        reference = simulate_period(prices, stocks, fundamentals, benchmark, candidate_config, usable_dates)
        cached = ResearchSimulationCache(prices, stocks, fundamentals, benchmark).simulate(candidate_config, usable_dates)

        for key in reference:
            self.assertAlmostEqual(reference[key], cached[key], places=12)

    def test_apply_candidate_preserves_adaptive_weights(self):
        config = {
            "strategy": {
                "name": "base",
                "top_n": 10,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {"momentum": 20},
                "weights": {"liquidity": 1.0},
            }
        }
        candidate = {
            "name": "adaptive",
            "adaptive_weights": {
                "enabled": True,
                "risk_on_blend": 0.5,
                "risk_on_weights": {"momentum": 0.5, "liquidity": 0.5},
            },
            "industry_neutralization": {"enabled": True, "min_group_size": 3},
            "cross_sectional_neutralization": {"enabled": True, "exposures": ["liquidity"]},
        }

        applied = apply_candidate(config, candidate)

        self.assertTrue(applied["strategy"]["adaptive_weights"]["enabled"])
        self.assertEqual(applied["strategy"]["adaptive_weights"]["risk_on_weights"]["momentum"], 0.5)
        self.assertTrue(applied["strategy"]["industry_neutralization"]["enabled"])
        self.assertEqual(applied["strategy"]["cross_sectional_neutralization"]["exposures"], ["liquidity"])

    def test_ensemble_candidate_is_preserved_and_static_candidate_drops_it(self):
        config = {
            "strategy": {
                "name": "ensemble",
                "top_n": 10,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {},
                "weights": {"liquidity": 1.0},
                "ensemble_members": [{"blend_weight": 1.0, "weights": {"momentum": 1.0}}],
            }
        }
        current = apply_candidate(config, config["strategy"])
        static = apply_candidate(config, {"name": "static", "weights": {"low_volatility": 1.0}})
        self.assertIn("ensemble_members", current["strategy"])
        self.assertNotIn("ensemble_members", static["strategy"])

    def test_static_candidate_does_not_inherit_base_adaptive_weights(self):
        config = {
            "strategy": {
                "name": "base",
                "top_n": 10,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {"momentum": 20},
                "weights": {"liquidity": 1.0},
                "adaptive_weights": {"enabled": True, "risk_on_weights": {"momentum": 1.0}},
            }
        }

        applied = apply_candidate(config, {"name": "static", "weights": {"low_volatility": 1.0}})

        self.assertNotIn("adaptive_weights", applied["strategy"])


if __name__ == "__main__":
    unittest.main()
