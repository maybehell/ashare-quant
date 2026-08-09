import argparse
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from ashare_quant.cli import run_observe_candidates


class ObservationProgressCliTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / "observation_progress_cli"
        self.output.mkdir(parents=True, exist_ok=True)
        self.config = {
            "paths": {"output_dir": str(self.output), "state_db": str(self.output / "state.sqlite3")},
            "strategy": {"name": "frozen-strategy"},
        }
        self.args = argparse.Namespace(
            refresh=True,
            max_symbols=None,
            cached_universe=True,
            no_metadata=True,
            allow_stale=True,
            skip_stress=True,
            candidate_config=[],
        )

    def write_progress(self, date: str, days: int) -> None:
        pd.DataFrame(
            [
                {
                    "candidate_id": "candidate-a",
                    "latest_comparison_date": date,
                    "observed_forward_trading_days": days,
                }
            ]
        ).to_csv(self.output / "live_candidate_forward_progress.csv", index=False)

    def run_with_update(self, date: str, days: int):
        store = Mock()

        def observe(*_args, **_kwargs):
            self.write_progress(date, days)
            return {"report.html": self.output / "report.html"}

        with (
            patch("ashare_quant.cli.DataStore.from_config", return_value=store),
            patch("ashare_quant.cli.fetch_akshare", return_value=[]),
            patch("ashare_quant.cli.run_observe", side_effect=observe),
            patch("ashare_quant.cli.compare_backtest_outputs", return_value=self.output / "strategy_backtest_compare.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=self.output / "report.html"),
        ):
            return run_observe_candidates(self.config, self.args)

    def test_refresh_writes_progress_audit_when_evidence_advances(self):
        self.write_progress("2026-07-15", 3)

        paths = self.run_with_update("2026-07-16", 4)

        self.assertIn("daily_observation_progress.csv", paths)
        progress = pd.read_csv(paths["daily_observation_progress.csv"])
        self.assertEqual(progress.loc[0, "status"], "progressed")

    def test_refresh_fails_when_market_advances_without_forward_evidence(self):
        self.write_progress("2026-07-15", 3)

        with self.assertRaisesRegex(RuntimeError, "observation_forward_evidence_progress"):
            self.run_with_update("2026-07-16", 3)

        audit = pd.read_csv(self.output / "daily_observation_progress_audit.csv")
        failed = audit[audit["passed"].astype(str).str.lower() == "false"]
        self.assertEqual(failed["check"].tolist(), ["observation_forward_evidence_progress"])


if __name__ == "__main__":
    unittest.main()
