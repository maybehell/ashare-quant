import json
from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.selection_bias import (
    build_strategy_selection_bias,
    write_strategy_selection_bias,
)


class StrategySelectionBiasTests(unittest.TestCase):
    @staticmethod
    def research(rows: list[tuple[str, list[float]]]) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "rank": index + 1,
                "name": name,
                "time_fold_excess_returns": json.dumps(values),
            }
            for index, (name, values) in enumerate(rows)
        ])

    @staticmethod
    def config(output: Path | None = None) -> dict:
        config = {
            "validation": {
                "min_selection_bias_candidates": 4,
                "min_selection_bias_folds": 4,
                "min_selection_bias_splits": 6,
                "max_selection_bias_probability": 0.50,
                "min_selected_test_median_excess_return": 0.0,
                "min_selected_test_positive_ratio": 0.50,
                "min_candidate_family_clusters": 1,
                "min_effective_candidate_count": 1.0,
                "max_largest_candidate_cluster_share": 1.0,
                "min_family_selection_clusters": 2,
                "min_family_selection_folds": 2,
                "min_family_selection_splits": 1,
                "max_family_selection_bias_probability": 1.0,
                "min_family_selected_test_median_excess_return": -1.0,
                "min_family_selected_test_positive_ratio": 0.0,
                "min_family_selection_consensus_ratio": 0.0,
            }
        }
        if output is not None:
            config["paths"] = {"output_dir": str(output)}
        return config

    def test_stable_candidate_family_passes_complementary_fold_audit(self):
        research = self.research([
            ("stable", [0.04, 0.05, 0.03, 0.04]),
            ("second", [0.02, 0.01, 0.02, 0.01]),
            ("mixed", [0.01, -0.01, 0.01, -0.01]),
            ("weak", [-0.02, -0.01, -0.03, -0.02]),
        ])
        audit, details = build_strategy_selection_bias(research, self.config())
        checks = audit.set_index("item")
        self.assertTrue(bool(checks.loc["strategy_selection_bias_ready", "passed"]))
        self.assertEqual(float(checks.loc["fold_selection_bias_probability", "value"]), 0.0)
        self.assertEqual(len(details), 6)
        self.assertEqual(set(details["selected_candidate"]), {"stable"})
        self.assertTrue(details["selected_test_positive"].all())

    def test_fold_specific_winners_are_flagged_as_selection_bias(self):
        research = self.research([
            ("fold_1", [0.10, -0.10, -0.10, -0.10]),
            ("fold_2", [-0.10, 0.10, -0.10, -0.10]),
            ("fold_3", [-0.10, -0.10, 0.10, -0.10]),
            ("fold_4", [-0.10, -0.10, -0.10, 0.10]),
        ])
        audit, details = build_strategy_selection_bias(research, self.config())
        checks = audit.set_index("item")
        self.assertFalse(bool(checks.loc["strategy_selection_bias_ready", "passed"]))
        self.assertGreater(float(checks.loc["fold_selection_bias_probability", "value"]), 0.50)
        self.assertLess(float(checks.loc["selected_candidate_median_test_excess", "value"]), 0.0)
        self.assertTrue(details["selected_test_below_median"].all())

    def test_writer_creates_diagnostic_only_artifacts(self):
        output = Path.cwd() / "test_outputs" / "selection_bias_writer"
        output.mkdir(parents=True, exist_ok=True)
        research = self.research([
            ("stable", [0.04, 0.05, 0.03, 0.04]),
            ("second", [0.02, 0.01, 0.02, 0.01]),
            ("mixed", [0.01, -0.01, 0.01, -0.01]),
            ("weak", [-0.02, -0.01, -0.03, -0.02]),
        ])
        research.to_csv(output / "strategy_research.csv", index=False)
        paths = write_strategy_selection_bias(self.config(output))
        self.assertTrue(paths["strategy_selection_bias.csv"].exists())
        self.assertTrue(paths["strategy_selection_bias_splits.csv"].exists())
        self.assertTrue(paths["strategy_candidate_family.csv"].exists())
        self.assertTrue(paths["strategy_candidate_similarity.csv"].exists())
        self.assertTrue(paths["strategy_family_selection.csv"].exists())
        self.assertTrue(paths["strategy_family_selection_splits.csv"].exists())
        self.assertFalse((output / "orders.csv").exists())
        self.assertFalse((output / "rpa_orders.csv").exists())
        self.assertFalse((output / "auto_live_submission_intent.csv").exists())

    def test_missing_fold_evidence_fails_closed(self):
        audit, details = build_strategy_selection_bias(pd.DataFrame([{"name": "only"}]), self.config())
        checks = audit.set_index("item")
        self.assertFalse(bool(checks.loc["strategy_selection_bias_ready", "passed"]))
        self.assertTrue(details.empty)


if __name__ == "__main__":
    unittest.main()
