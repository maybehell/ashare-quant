import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.data import DataStore
from ashare_quant.history_validation import (
    assert_research_only_history_config,
    require_completed_history_progress,
    run_extended_history_validation,
)


class ExtendedHistoryValidationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.config = {
            "paths": {
                "cache_dir": str(self.root / "cache"),
                "state_db": str(self.root / "research_state.sqlite3"),
                "output_dir": str(self.root / "validation_outputs"),
            },
            "data": {
                "history_staging_namespace": "extended_test",
            },
            "backtest": {
                "start": "2018-01-01",
                "end": "2026-08-07",
            },
            "validation": {
                "research_only": True,
                "apply_active_execution": False,
            },
            "auto_live": {
                "allow_broker_submit": False,
                "emergency_stop": True,
            },
        }
        self.store = DataStore.from_config(self.config)
        self.store.write_frame(
            "prices",
            pd.DataFrame(
                [
                    {
                        "date": "2026-08-07",
                        "symbol": "000001",
                        "open": 10,
                        "high": 10,
                        "low": 10,
                        "close": 10,
                        "volume": 100,
                        "amount": 1000,
                    }
                ]
            ),
        )

    def write_progress(self, **overrides):
        row = {
            "status": "complete",
            "requested_symbols": 2,
            "completed_symbols": 2,
            "remaining_symbols": 0,
            "failed_items": 0,
            "requested_start": "2018-01-01",
            "requested_end": "2026-08-07",
            "generated_at": "2026-08-10T01:00:00",
        }
        row.update(overrides)
        self.store.write_frame(
            "history_backfill_progress_extended_test",
            pd.DataFrame([row]),
        )

    def test_rejects_incomplete_or_failed_progress(self):
        self.write_progress(status="in_progress", completed_symbols=1, remaining_symbols=1)
        with self.assertRaisesRegex(RuntimeError, "not authoritatively complete"):
            require_completed_history_progress(self.store, self.config)

        self.write_progress(failed_items=1)
        with self.assertRaisesRegex(RuntimeError, "failures=1"):
            require_completed_history_progress(self.store, self.config)

    def test_research_only_boundary_rejects_execution_capability(self):
        unsafe = {
            **self.config,
            "auto_live": {
                "allow_broker_submit": True,
                "emergency_stop": False,
            },
        }
        with self.assertRaisesRegex(ValueError, "allow_broker_submit"):
            assert_research_only_history_config(unsafe)

    def test_runs_fixed_backtest_once_and_reuses_exact_completed_evidence(self):
        self.write_progress()
        output_dir = Path(self.config["paths"]["output_dir"])

        def fake_backtest(config):
            output_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"metric": "total_return", "value": 0.25},
                    {"metric": "benchmark_total_return", "value": 0.18},
                    {"metric": "excess_total_return", "value": 0.07},
                    {"metric": "max_drawdown", "value": -0.12},
                    {"metric": "information_ratio", "value": 0.8},
                ]
            ).to_csv(output_dir / "performance_metrics.csv", index=False)
            pd.DataFrame(
                [
                    {"date": "2026-08-06", "equity": 1000000},
                    {"date": "2026-08-07", "equity": 1010000},
                ]
            ).to_csv(output_dir / "equity_curve.csv", index=False)
            return {"performance_metrics.csv": output_dir / "performance_metrics.csv"}

        def fake_report(config):
            target = output_dir / "report.html"
            target.write_text("<html lang='zh-CN'></html>", encoding="utf-8")
            return target

        with (
            patch("ashare_quant.history_validation.run_backtest", side_effect=fake_backtest) as backtest,
            patch("ashare_quant.history_validation.regenerate_report", side_effect=fake_report),
        ):
            paths = run_extended_history_validation(self.config)
            self.assertEqual(backtest.call_count, 1)
            paths_again = run_extended_history_validation(self.config)
            self.assertEqual(backtest.call_count, 1)

        status = pd.read_csv(output_dir / "history_validation_status.csv").iloc[0]
        self.assertEqual(status["status"], "completed")
        self.assertEqual(int(status["effective_trading_days"]), 2)
        self.assertAlmostEqual(float(status["excess_total_return"]), 0.07)
        self.assertIn("history_validation_status.csv", paths)
        self.assertEqual(paths_again["report.html"], output_dir / "report.html")
        self.assertFalse((output_dir / "orders.csv").exists())
        self.assertFalse((output_dir / "rpa_orders.csv").exists())

    def test_failure_is_audited_without_execution_artifacts(self):
        self.write_progress()
        output_dir = Path(self.config["paths"]["output_dir"])
        with patch(
            "ashare_quant.history_validation.run_backtest",
            side_effect=RuntimeError("backtest failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "backtest failed"):
                run_extended_history_validation(self.config)

        status = pd.read_csv(output_dir / "history_validation_status.csv").iloc[0]
        self.assertEqual(status["status"], "failed")
        self.assertIn("backtest failed", status["message"])
        self.assertFalse((output_dir / "orders.csv").exists())
        self.assertFalse((output_dir / "rpa_orders.csv").exists())


if __name__ == "__main__":
    unittest.main()
