import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.data import DataStore
from ashare_quant.report import write_html_report
from ashare_quant.survivorship import (
    build_survivorship_bias_audit,
    survivorship_evidence_sha256,
    survivorship_warnings,
    write_survivorship_bias_audit,
)


class SurvivorshipBiasAuditTests(unittest.TestCase):
    def setUp(self):
        self.stocks = pd.DataFrame(
            [
                {"symbol": "000001", "list_date": "2010-01-01"},
                {"symbol": "000002", "list_date": "2020-01-01"},
                {"symbol": "000003", "list_date": "2000-01-01"},
                {"symbol": "000099", "list_date": "2005-01-01"},
            ]
        )
        self.prices = pd.DataFrame(
            [
                {"date": "2018-12-28", "symbol": "000001", "close": 10},
                {"date": "2018-12-28", "symbol": "000099", "close": 8},
                {"date": "2021-12-31", "symbol": "000001", "close": 12},
                {"date": "2021-12-31", "symbol": "000002", "close": 11},
                {"date": "2021-12-31", "symbol": "000003", "close": 9},
                {"date": "2021-12-31", "symbol": "000099", "close": 7},
            ]
        )
        self.committed = ["000001", "000002", "000003"]

    def test_quantifies_current_pool_coverage_without_claiming_correction(self):
        audit, snapshots = build_survivorship_bias_audit(
            self.stocks,
            self.prices,
            self.committed,
            start="2018-01-01",
            end="2021-12-31",
        )
        values = audit.set_index("item")

        self.assertFalse(bool(values.loc["point_in_time_universe_available", "passed"]))
        self.assertFalse(bool(values.loc["survivorship_bias_corrected", "passed"]))
        self.assertEqual(int(values.loc["current_pool_symbols", "value"]), 3)
        self.assertEqual(int(values.loc["all_cached_historical_symbols", "value"]), 4)
        self.assertEqual(int(values.loc["cached_symbols_excluded_from_current_pool", "value"]), 1)
        self.assertEqual(values.loc["survivorship_risk_level", "value"], "high")
        self.assertEqual(len(snapshots), 2)
        self.assertAlmostEqual(
            float(snapshots.iloc[0]["current_pool_priced_share_of_cache"]),
            0.5,
        )
        self.assertEqual(
            int(snapshots.iloc[0]["current_pool_not_yet_listed_symbols"]),
            1,
        )
        self.assertEqual(len(survivorship_evidence_sha256(audit, snapshots)), 64)
        self.assertGreaterEqual(len(survivorship_warnings(audit)), 4)

    def test_writer_binds_provenance_and_html_report_renders_chinese_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "paths": {
                    "cache_dir": str(root / "cache"),
                    "state_db": str(root / "state.sqlite3"),
                    "output_dir": str(root / "outputs"),
                },
                "backtest": {"start": "2018-01-01", "end": "2021-12-31"},
            }
            store = DataStore.from_config(config)
            store.write_frame("stocks", self.stocks)
            store.write_frame("prices", self.prices)
            store.write_frame(
                "fetch_history_provenance",
                pd.DataFrame({"symbol": self.committed}),
            )

            paths = write_survivorship_bias_audit(config)
            audit = pd.read_csv(paths["survivorship_bias_audit.csv"])
            snapshots = pd.read_csv(paths["survivorship_bias_snapshots.csv"])
            report_path = root / "outputs" / "report.html"
            write_html_report(
                report_path,
                survivorship_bias_audit=audit,
                survivorship_bias_snapshots=snapshots,
            )
            html = report_path.read_text(encoding="utf-8")

            self.assertEqual(
                audit.set_index("item").loc["committed_universe_source", "value"],
                "fetch_history_provenance",
            )
            self.assertIn("幸存者偏差审计", html)
            self.assertIn("逐日历史可投资股票池可用", html)
            self.assertIn("长期超额收益必须按高幸存者偏差风险解释", html)
            self.assertIn("幸存者偏差年度快照", html)


if __name__ == "__main__":
    unittest.main()
