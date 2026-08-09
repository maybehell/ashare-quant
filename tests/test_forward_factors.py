import unittest

import pandas as pd

from ashare_quant.forward_factors import FORWARD_FACTOR_COLUMNS, build_forward_factor_monitor, forward_factor_warnings, summarize_forward_factor


class ForwardFactorMonitorTests(unittest.TestCase):
    def test_warning_summarizes_adverse_effective_weight(self):
        frame = pd.DataFrame(
            [
                {"factor": "liquidity", "configured_weight": 0.60, "member_blend_weight": 1.0, "status": "adverse"},
                {"factor": "momentum", "configured_weight": 0.15, "member_blend_weight": 1.0, "status": "adverse"},
                {"factor": "low_volatility", "configured_weight": 0.25, "member_blend_weight": 1.0, "status": "supportive"},
            ]
        )

        warnings = forward_factor_warnings(frame)

        self.assertEqual(len(warnings), 1)
        self.assertIn("75.0%", warnings[0])
        self.assertIn("liquidity 60.0%", warnings[0])
        self.assertIn("\u4e0d\u7528\u4e8e\u56de\u770b\u8c03\u53c2", warnings[0])

    def test_summarize_forward_factor_detects_supportive_cross_section(self):
        frame = pd.DataFrame({
            "momentum": list(range(100)),
            "forward_return": [value / 1000 for value in range(100)],
        })

        result = summarize_forward_factor(frame, "momentum", min_symbols=30, benchmark_return=0.02)

        self.assertIsNotNone(result)
        self.assertGreater(result["long_short_spread"], 0)
        self.assertAlmostEqual(result["rank_ic"], 1.0)
        self.assertEqual(result["status"], "supportive")
        self.assertAlmostEqual(result["top_bucket_excess_return"], result["top_bucket_return"] - 0.02)

    def test_monitor_without_frozen_candidate_is_empty(self):
        result = build_forward_factor_monitor(
            {"strategy": {}, "strategy_health": {}},
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            manifest={},
        )

        self.assertTrue(result.empty)
        self.assertEqual(result.columns.tolist(), FORWARD_FACTOR_COLUMNS)


if __name__ == "__main__":
    unittest.main()
