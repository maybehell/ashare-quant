import unittest

import numpy as np
import pandas as pd

from ashare_quant.statistical_validation import block_bootstrap_excess


class StatisticalValidationTests(unittest.TestCase):
    def config(self, **overrides):
        values = {"block_days": 10, "samples": 500, "seed": 7, "min_observations": 100}
        values.update(overrides)
        return {"validation": {"excess_bootstrap": values}}

    def test_positive_paired_excess_is_detected_deterministically(self):
        rng = np.random.default_rng(11)
        benchmark = rng.normal(0.0001, 0.008, 500)
        strategy = benchmark + rng.normal(0.0006, 0.002, 500)
        comparison = pd.DataFrame({"strategy_return": strategy, "benchmark_return": benchmark})

        first = block_bootstrap_excess(comparison, self.config())
        second = block_bootstrap_excess(comparison, self.config())

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(first.loc[0, "status"], "ok")
        self.assertGreater(first.loc[0, "probability_excess_positive"], 0.95)
        self.assertGreater(first.loc[0, "lower_95_excess_return"], 0)

    def test_insufficient_history_is_explicit(self):
        comparison = pd.DataFrame(
            {"strategy_return": [0.01] * 20, "benchmark_return": [0.0] * 20}
        )
        result = block_bootstrap_excess(comparison, self.config(min_observations=100))
        self.assertEqual(result.loc[0, "status"], "insufficient_observations")
        self.assertTrue(pd.isna(result.loc[0, "lower_95_excess_return"]))

    def test_missing_or_invalid_pairs_do_not_become_evidence(self):
        missing = block_bootstrap_excess(pd.DataFrame(), self.config())
        self.assertEqual(missing.loc[0, "status"], "missing")
        invalid = pd.DataFrame(
            {"strategy_return": [-1.0] * 200, "benchmark_return": [0.0] * 200}
        )
        result = block_bootstrap_excess(invalid, self.config())
        self.assertEqual(result.loc[0, "status"], "insufficient_observations")


if __name__ == "__main__":
    unittest.main()
