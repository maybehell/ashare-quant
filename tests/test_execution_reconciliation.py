from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.audit import frame_sha256
from ashare_quant.compare import promotion_row
from ashare_quant.execution_reconciliation import build_execution_model_reconciliation
from ashare_quant.validation import validate_config


class ExecutionModelReconciliationTests(unittest.TestCase):
    def config(self, name: str) -> dict:
        return {
            "paths": {
                "output_dir": str(Path.cwd() / "test_outputs" / name),
                "cache_dir": "data/cache",
                "state_db": "data/state.sqlite3",
            },
            "data": {"max_symbols": 10},
            "universe": {
                "boards": ["main"],
                "min_listing_days": 60,
                "min_avg_amount": 1,
                "liquidity_lookback": 20,
            },
            "backtest": {"start": "2023-01-01", "initial_cash": 1_000_000, "rebalance": "monthly"},
            "strategy": {
                "name": "formal",
                "top_n": 20,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {"momentum": 20},
                "weights": {"momentum": 1.0},
            },
            "trading": {
                "lot_size": 100,
                "slippage_bps": 5,
                "commission_rate": 0.0003,
                "min_commission": 5,
                "stamp_tax_rate": 0.0005,
            },
            "validation": {
                "require_execution_model_reconciliation": True,
                "min_execution_reconciliation_candidates": 4,
                "max_execution_excess_gap": 0.10,
                "min_execution_rank_correlation": 0.70,
                "min_execution_sign_agreement_ratio": 1.0,
            },
        }

    def evidence(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        research = pd.DataFrame(
            [
                {"name": "a", "rank": 1, "strategy_return": 0.30, "excess_return": 0.20},
                {"name": "b", "rank": 2, "strategy_return": 0.25, "excess_return": 0.15},
                {"name": "c", "rank": 3, "strategy_return": 0.20, "excess_return": 0.10},
                {"name": "d", "rank": 4, "strategy_return": 0.15, "excess_return": 0.05},
            ]
        )
        comparison = pd.DataFrame(
            [
                {"strategy_name": "a", "total_return": 0.32, "excess_total_return": 0.22, "data_current": True},
                {"strategy_name": "b", "total_return": 0.27, "excess_total_return": 0.17, "data_current": True},
                {"strategy_name": "c", "total_return": 0.21, "excess_total_return": 0.11, "data_current": True},
                {"strategy_name": "d", "total_return": 0.16, "excess_total_return": 0.06, "data_current": True},
            ]
        )
        market_hashes = {
            "stocks_sha256": "stocks",
            "prices_sha256": "prices",
            "fundamentals_sha256": "fundamentals",
            "benchmark_sha256": "benchmark",
        }
        research_manifest = pd.DataFrame(
            [
                {"item": "strategy_research_sha256", "value": frame_sha256(research)},
                *({"item": item, "value": value} for item, value in market_hashes.items()),
            ]
        )
        current_manifest = pd.DataFrame(
            [{"item": item, "value": value} for item, value in market_hashes.items()]
        )
        return comparison, research, research_manifest, current_manifest

    def test_reconciles_current_correlated_execution_evidence(self):
        comparison, research, research_manifest, current_manifest = self.evidence()

        detail, summary, enriched = build_execution_model_reconciliation(
            self.config("execution_reconciliation_pass"),
            comparison,
            research=research,
            research_manifest=research_manifest,
            current_manifest=current_manifest,
        )

        checks = summary.set_index("item")
        self.assertTrue(bool(checks.loc["execution_model_reconciliation_ready", "passed"]))
        self.assertAlmostEqual(float(checks.loc["execution_excess_rank_correlation", "value"]), 1.0)
        self.assertTrue(detail["reconciled"].all())
        self.assertTrue(enriched["execution_model_reconciled"].all())
        self.assertTrue((detail["status"] == "reconciled").all())

    def test_direction_or_gap_mismatch_blocks_all_candidates(self):
        comparison, research, research_manifest, current_manifest = self.evidence()
        comparison.loc[comparison["strategy_name"] == "a", "excess_total_return"] = -0.25

        detail, summary, enriched = build_execution_model_reconciliation(
            self.config("execution_reconciliation_fail"),
            comparison,
            research=research,
            research_manifest=research_manifest,
            current_manifest=current_manifest,
        )

        checks = summary.set_index("item")
        self.assertFalse(bool(checks.loc["execution_model_reconciliation_ready", "passed"]))
        self.assertFalse(detail["reconciled"].any())
        self.assertFalse(enriched["execution_model_reconciled"].any())
        self.assertEqual(
            detail.loc[detail["strategy_name"] == "a", "status"].iloc[0],
            "global_reconciliation_failed",
        )

    def test_promotion_is_blocked_when_reconciliation_is_missing(self):
        output = Path.cwd() / "test_outputs" / "execution_reconciliation_promotion"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"check": "all", "passed": True}]).to_csv(output / "practical_readiness.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": True}]).to_csv(output / "live_readiness.csv", index=False)
        pd.DataFrame([{"item": "stable", "passed": True}]).to_csv(output / "strategy_candidate_stability.csv", index=False)
        pd.DataFrame([{"candidate_id": "candidate", "status": "validated", "forward_excess_return": 0.02}]).to_csv(
            output / "live_candidate_forward_progress.csv", index=False
        )
        pd.DataFrame([{"scenario": "high_cost", "excess_total_return": 0.01}]).to_csv(output / "cost_stress.csv", index=False)
        pd.DataFrame([{"item": "strategy_name", "value": "candidate"}]).to_csv(output / "run_manifest.csv", index=False)

        row = promotion_row(
            output,
            1,
            {
                "strategy_name": "candidate",
                "data_current": True,
                "execution_model_reconciled": False,
            },
            config=self.config("execution_reconciliation_promotion"),
        )

        self.assertFalse(bool(row["practical_passed"]))
        self.assertFalse(bool(row["strict_live_passed"]))
        self.assertEqual(row["promotion_status"], "research_only")
        self.assertIn("execution_model_reconciliation", row["failed_live_checks"])

    def test_invalid_reconciliation_thresholds_are_rejected(self):
        config = self.config("execution_reconciliation_invalid")
        config["validation"]["min_execution_reconciliation_candidates"] = 1
        config["validation"]["max_execution_excess_gap"] = 1.1
        config["validation"]["min_execution_rank_correlation"] = 1.1
        config["validation"]["min_execution_sign_agreement_ratio"] = -0.1

        errors = validate_config(config)

        self.assertTrue(any("min_execution_reconciliation_candidates" in error for error in errors))
        self.assertTrue(any("max_execution_excess_gap" in error for error in errors))
        self.assertTrue(any("min_execution_rank_correlation" in error for error in errors))
        self.assertTrue(any("min_execution_sign_agreement_ratio" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
