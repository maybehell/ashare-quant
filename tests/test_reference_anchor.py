from datetime import date
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.factors import build_configured_target_portfolio, decompose_reference_anchor_target


class ReferenceAnchorTests(unittest.TestCase):
    def setUp(self):
        self.scores = pd.DataFrame(
            [
                {"symbol": "a", "score": 4.0, "avg_amount": 1.0, "industry": "unknown"},
                {"symbol": "b", "score": 3.0, "avg_amount": 4.0, "industry": "unknown"},
                {"symbol": "c", "score": 2.0, "avg_amount": 9.0, "industry": "unknown"},
                {"symbol": "d", "score": 1.0, "avg_amount": 16.0, "industry": "unknown"},
            ]
        )
        self.stocks = pd.DataFrame({"symbol": ["a", "b", "c", "d"]})
        self.config = {
            "strategy": {
                "top_n": 2,
                "max_stock_weight": 0.5,
                "max_industry_weight": 1.0,
                "weighting": "equal",
                "reference_anchor": {
                    "enabled": True,
                    "weight": 0.5,
                    "top_n": 4,
                    "max_stock_weight": 0.5,
                    "weighting": "liquidity_sqrt",
                },
            }
        }

    def test_anchor_blends_diversified_core_with_alpha_satellite(self):
        with patch("ashare_quant.factors.compute_scores", return_value=self.scores):
            weights, signal = build_configured_target_portfolio(
                pd.DataFrame(), self.stocks, pd.DataFrame(), date(2026, 7, 21), self.config
            )

        self.assertEqual(set(weights.index), {"a", "b", "c", "d"})
        self.assertAlmostEqual(float(weights["a"]), 0.30)
        self.assertAlmostEqual(float(weights["b"]), 0.35)
        self.assertAlmostEqual(float(weights["c"]), 0.15)
        self.assertAlmostEqual(float(weights["d"]), 0.20)
        self.assertAlmostEqual(float(weights.sum()), 1.0)
        self.assertEqual(len(signal), 4)

        anchor, satellite = decompose_reference_anchor_target(
            self.scores, weights, self.config
        )

        self.assertAlmostEqual(float(anchor["a"]), 0.05)
        self.assertAlmostEqual(float(anchor["b"]), 0.10)
        self.assertAlmostEqual(float(anchor["c"]), 0.15)
        self.assertAlmostEqual(float(anchor["d"]), 0.20)
        self.assertAlmostEqual(float(satellite["a"]), 0.25)
        self.assertAlmostEqual(float(satellite["b"]), 0.25)
        self.assertAlmostEqual(float(satellite.sum()), 0.50)
        pd.testing.assert_series_equal((anchor + satellite).sort_index(), weights.sort_index())

    def test_disabled_anchor_preserves_original_alpha_portfolio(self):
        self.config["strategy"]["reference_anchor"]["enabled"] = False
        with patch("ashare_quant.factors.compute_scores", return_value=self.scores):
            weights, _ = build_configured_target_portfolio(
                pd.DataFrame(), self.stocks, pd.DataFrame(), date(2026, 7, 21), self.config
            )

        self.assertEqual(set(weights.index), {"a", "b"})
        self.assertAlmostEqual(float(weights["a"]), 0.5)
        self.assertAlmostEqual(float(weights["b"]), 0.5)


if __name__ == "__main__":
    unittest.main()
