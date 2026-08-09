from datetime import date
from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.config import load_config
from ashare_quant.data import DataStore
from ashare_quant.source_health import build_data_source_health


class SourceHealthTests(unittest.TestCase):
    def test_source_health_flags_stale_complete_date_and_partial_raw_latest(self):
        root = Path.cwd() / "test_outputs" / "source_health_stale"
        config = load_config()
        config["paths"]["cache_dir"] = str(root / "cache")
        config["paths"]["state_db"] = str(root / "state.sqlite3")
        config["paths"]["output_dir"] = str(root / "outputs")
        config["data"]["max_cache_age_days"] = 10
        config["data"]["min_daily_price_coverage_ratio"] = 0.9
        config["data"]["min_fetch_success_symbols"] = 1
        config["data"]["max_symbols"] = 2
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame(
                [
                    {"symbol": "000001", "name": "A", "board": "main", "industry": "银行", "list_date": "2020-01-01", "is_st": False},
                    {"symbol": "000002", "name": "B", "board": "main", "industry": "地产", "list_date": "2020-01-01", "is_st": False},
                ]
            ),
        )
        store.write_frame(
            "prices",
            pd.DataFrame(
                [
                    {"date": "2026-01-01", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 100},
                    {"date": "2026-01-01", "symbol": "000002", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 90},
                    {"date": "2026-01-02", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 80},
                ]
            ),
        )
        store.write_frame("fundamentals", pd.DataFrame(columns=["date", "symbol", "pe", "pb", "roe"]))
        store.write_frame(
            "benchmark",
            pd.DataFrame(
                [
                    {"date": "2026-01-01", "symbol": "000300", "name": "沪深300", "close": 100},
                    {"date": "2026-01-02", "symbol": "000300", "name": "沪深300", "close": 101},
                ]
            ),
        )
        store.write_frame("prices_partial", pd.DataFrame([
            {"date": "2026-01-03", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 100},
            {"date": "2026-01-03", "symbol": "000002", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 90},
        ]))
        store.write_frame(
            "fetch_history_provenance",
            pd.DataFrame(
                [
                    {"symbol": "000001", "source": "stock_zh_a_hist_tx", "amount_quality": "estimated_close_x_volume"},
                    {"symbol": "000002", "source": "stock_zh_a_hist", "amount_quality": "reported"},
                ]
            ),
        )
        config["data"]["max_estimated_amount_source_ratio"] = 0.25

        store.write_frame("fetch_failures", pd.DataFrame([{"symbol": "000003", "stage": "history", "error": "timeout"}]))

        health = build_data_source_health(config, store=store, today=date(2026, 1, 15))
        by_check = {row.check: row for row in health.itertuples(index=False)}

        self.assertEqual(str(by_check["complete_market_date"].value), "2026-01-01")
        self.assertFalse(bool(by_check["price_cache_age_days"].passed))
        self.assertEqual(float(by_check["price_cache_age_days"].value), 14.0)
        self.assertFalse(bool(by_check["raw_latest_coverage"].passed))
        self.assertFalse(bool(by_check["partial_recovery_available"].passed))
        self.assertIn("recover-partial", by_check["partial_recovery_available"].command)
        self.assertIn("fetch --cached-universe", by_check["price_cache_age_days"].command)
        self.assertFalse(bool(by_check["recent_fetch_failures"].passed))
        self.assertIn("history:1", by_check["recent_fetch_failures"].value)
        self.assertFalse(bool(by_check["history_source_provenance"].passed))
        self.assertIn("0.5000", by_check["history_source_provenance"].value)

    def test_source_health_missing_cache_has_recovery_command(self):
        root = Path.cwd() / "test_outputs" / "source_health_missing"
        config = load_config()
        config["paths"]["cache_dir"] = str(root / "cache")
        config["paths"]["state_db"] = str(root / "state.sqlite3")
        config["paths"]["output_dir"] = str(root / "outputs")

        health = build_data_source_health(config, store=DataStore.from_config(config))
        row = health.iloc[0]

        self.assertEqual(row["check"], "cached_market_data")
        self.assertFalse(bool(row["passed"]))
        self.assertIn("fetch --sample", row["command"])


if __name__ == "__main__":
    unittest.main()