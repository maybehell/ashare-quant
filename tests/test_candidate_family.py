import json
import unittest

import pandas as pd

from ashare_quant.candidate_family import (
    build_candidate_family_redundancy,
    build_candidate_family_selection_bias,
    fold_similarity_matrix,
)


class CandidateFamilyRedundancyTests(unittest.TestCase):
    @staticmethod
    def research(rows: list[tuple[str, int, list[float]]]) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "name": name,
                "rank": rank,
                "time_fold_excess_returns": json.dumps(values),
            }
            for name, rank, values in rows
        ])

    @staticmethod
    def config() -> dict:
        return {
            "validation": {
                "candidate_similarity_threshold": 0.95,
                "min_candidate_family_clusters": 3,
                "min_effective_candidate_count": 2.0,
                "max_largest_candidate_cluster_share": 0.75,
                "min_family_selection_clusters": 3,
                "min_family_selection_folds": 4,
                "min_family_selection_splits": 6,
                "max_family_selection_bias_probability": 0.50,
                "min_family_selected_test_median_excess_return": 0.0,
                "min_family_selected_test_positive_ratio": 0.50,
                "min_family_selection_consensus_ratio": 0.50,
            }
        }

    def test_highly_redundant_family_fails_effective_breadth(self):
        research = self.research([
            ("base", 1, [0.03, 0.02, -0.01, 0.01, -0.02, 0.00]),
            ("variant_a", 2, [0.031, 0.019, -0.011, 0.011, -0.019, -0.001]),
            ("variant_b", 3, [0.029, 0.021, -0.009, 0.009, -0.021, 0.001]),
            ("variant_c", 4, [0.030, 0.020, -0.010, 0.010, -0.020, 0.000]),
        ])

        audit, families, similarities = build_candidate_family_redundancy(research, self.config())

        checks = audit.set_index("item")
        self.assertFalse(bool(checks.loc["candidate_family_redundancy_ready", "passed"]))
        self.assertEqual(int(checks.loc["candidate_family_cluster_count", "value"]), 1)
        self.assertLess(float(checks.loc["candidate_family_effective_count", "value"]), 2.0)
        self.assertEqual(families["cluster_id"].nunique(), 1)
        self.assertTrue(similarities["above_threshold"].all())

    def test_distinct_fold_behaviours_pass_and_are_order_stable(self):
        rows = [
            ("a", 1, [1, -1, 0, 0, 0, 0]),
            ("b", 2, [0, 0, 1, -1, 0, 0]),
            ("c", 3, [0, 0, 0, 0, 1, -1]),
            ("d", 4, [1, 1, -1, -1, 0, 0]),
        ]
        audit, families, _ = build_candidate_family_redundancy(self.research(rows), self.config())
        _, reordered, _ = build_candidate_family_redundancy(self.research(list(reversed(rows))), self.config())

        checks = audit.set_index("item")
        self.assertTrue(bool(checks.loc["candidate_family_redundancy_ready", "passed"]))
        self.assertEqual(int(checks.loc["candidate_family_cluster_count", "value"]), 4)
        self.assertGreaterEqual(float(checks.loc["candidate_family_effective_count", "value"]), 3.9)
        mapping = dict(zip(families["candidate"], families["cluster_id"]))
        reordered_mapping = dict(zip(reordered["candidate"], reordered["cluster_id"]))
        self.assertEqual(mapping, reordered_mapping)

    def test_constant_vectors_are_finite_and_exact_duplicates_cluster(self):
        similarity = fold_similarity_matrix([
            [0.01, 0.01, 0.01, 0.01],
            [0.01, 0.01, 0.01, 0.01],
            [0.02, 0.02, 0.02, 0.02],
        ])

        self.assertFalse(pd.isna(similarity).any())
        self.assertEqual(float(similarity[0, 1]), 1.0)
        self.assertEqual(float(similarity[0, 2]), 0.0)

    def test_missing_fold_evidence_fails_closed(self):
        audit, families, similarities = build_candidate_family_redundancy(
            pd.DataFrame([{"name": "only"}]),
            self.config(),
        )
        checks = audit.set_index("item")
        self.assertFalse(bool(checks.loc["candidate_family_redundancy_ready", "passed"]))
        self.assertTrue(families.empty)
        self.assertTrue(similarities.empty)


    def test_family_level_selection_passes_for_consistent_independent_family(self):
        research = self.research([
            ("stable", 1, [0.06, 0.04, 0.05, 0.05, 0.05, 0.05]),
            ("second", 2, [0.02, 0.02, 0.03, 0.01, 0.02, 0.02]),
            ("third", 3, [0.01, 0.01, 0.01, 0.01, 0.02, 0.00]),
        ])

        audit, details = build_candidate_family_selection_bias(research, self.config())

        checks = audit.set_index("item")
        self.assertTrue(bool(checks.loc["strategy_family_selection_ready", "passed"]))
        self.assertEqual(set(details["selected_representative"]), {"stable"})
        self.assertTrue(details["selected_test_positive"].all())

    def test_family_level_selection_flags_fold_specific_winners(self):
        research = self.research([
            ("fold_1", 1, [0.10, -0.10, -0.10, -0.10]),
            ("fold_2", 2, [-0.10, 0.10, -0.10, -0.10]),
            ("fold_3", 3, [-0.10, -0.10, 0.10, -0.10]),
            ("fold_4", 4, [-0.10, -0.10, -0.10, 0.10]),
        ])

        audit, details = build_candidate_family_selection_bias(research, self.config())

        checks = audit.set_index("item")
        self.assertFalse(bool(checks.loc["strategy_family_selection_ready", "passed"]))
        self.assertGreater(float(checks.loc["family_selection_bias_probability", "value"]), 0.50)
        self.assertLess(float(checks.loc["family_selected_median_test_excess", "value"]), 0.0)
        self.assertFalse(details["selected_test_positive"].any())
if __name__ == "__main__":
    unittest.main()
