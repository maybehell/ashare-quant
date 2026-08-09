from datetime import date
import unittest

import pandas as pd

from ashare_quant.universe import eligible_universe


class UniverseTests(unittest.TestCase):
    def test_filters_bse_st_new_and_illiquid(self):
        stocks = pd.DataFrame(
            [
                {"symbol": "600001", "name": "ok", "board": "main", "industry": "a", "list_date": date(2020, 1, 1), "is_st": False},
                {"symbol": "830001", "name": "bse", "board": "bse", "industry": "a", "list_date": date(2020, 1, 1), "is_st": False},
                {"symbol": "600002", "name": "ST", "board": "main", "industry": "a", "list_date": date(2020, 1, 1), "is_st": True},
                {"symbol": "600003", "name": "new", "board": "main", "industry": "a", "list_date": date(2024, 2, 1), "is_st": False},
                {"symbol": "600004", "name": "low", "board": "main", "industry": "a", "list_date": date(2020, 1, 1), "is_st": False},
            ]
        )
        prices = pd.DataFrame(
            [
                {"date": date(2024, 3, 1), "symbol": "600001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 100},
                {"date": date(2024, 3, 1), "symbol": "830001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 100},
                {"date": date(2024, 3, 1), "symbol": "600002", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 100},
                {"date": date(2024, 3, 1), "symbol": "600003", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 100},
                {"date": date(2024, 3, 1), "symbol": "600004", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1},
            ]
        )
        cfg = {"universe": {"boards": ["main", "gem", "star"], "exclude_st": True, "min_listing_days": 60, "min_avg_amount": 50, "liquidity_lookback": 1}}
        eligible = eligible_universe(stocks, prices, date(2024, 3, 1), cfg)
        self.assertEqual(eligible["symbol"].tolist(), ["600001"])


if __name__ == "__main__":
    unittest.main()
