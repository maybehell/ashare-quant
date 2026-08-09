import unittest

import pandas as pd

from ashare_quant.capital_aware import build_capital_aware_target_weights


def config(**policy):
    return {
        "strategy": {
            "max_stock_weight": 0.40,
            "max_industry_weight": 0.50,
            "capital_aware": {
                "enabled": True,
                "min_positions": 3,
                "max_positions": 3,
                "max_candidate_rank": 10,
                "require_industry": True,
                **policy,
            },
        },
        "trading": {
            "lot_size": 100,
            "reserve_cash_ratio": 0.10,
            "commission_rate": 0.0003,
            "min_commission": 5,
            "stamp_tax_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
        },
    }


class CapitalAwareTests(unittest.TestCase):
    def setUp(self):
        self.scores = pd.DataFrame(
            [
                {"symbol": "600001", "score": 5.0, "industry": "A"},
                {"symbol": "600002", "score": 4.0, "industry": "A"},
                {"symbol": "600003", "score": 3.0, "industry": "B"},
                {"symbol": "600004", "score": 2.0, "industry": "C"},
                {"symbol": "600005", "score": 1.0, "industry": "D"},
            ]
        )

    def test_selects_affordable_high_scores_with_industry_cap(self):
        prices = {"600001": 6.0, "600002": 5.0, "600003": 7.0, "600004": 8.0, "600005": 9.0}
        weights = build_capital_aware_target_weights(self.scores, prices, 2567.44, 1.0, config())
        self.assertEqual(set(weights.index), {"600001", "600002", "600003"})
        self.assertLessEqual(float(weights[["600001", "600002"]].sum()), 0.50)
        self.assertLessEqual(float(weights.sum()), 1.0)

    def test_skips_top_ranked_stock_when_one_lot_exceeds_stock_cap(self):
        prices = {"600001": 20.0, "600002": 5.0, "600003": 7.0, "600004": 8.0, "600005": 9.0}
        weights = build_capital_aware_target_weights(self.scores, prices, 2567.44, 1.0, config())
        self.assertNotIn("600001", weights.index)
        self.assertEqual(len(weights), 3)

    def test_fails_closed_when_minimum_diversification_is_unaffordable(self):
        prices = {symbol: 20.0 for symbol in self.scores["symbol"]}
        weights = build_capital_aware_target_weights(self.scores, prices, 2567.44, 1.0, config())
        self.assertTrue(weights.empty)

    def test_reduced_exposure_can_fail_closed_instead_of_fractional_lot(self):
        prices = {"600001": 6.0, "600002": 5.0, "600003": 7.0, "600004": 8.0, "600005": 9.0}
        weights = build_capital_aware_target_weights(self.scores, prices, 2567.44, 0.35, config())
        self.assertTrue(weights.empty)


if __name__ == "__main__":
    unittest.main()
