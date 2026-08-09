import argparse
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.cli import (
    invalidate_huatai_broker_account_evidence,
    run_huatai_read_only_refresh,
)


class BrokerSnapshotInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        self.output.mkdir(parents=True, exist_ok=True)
        for path in self.output.glob("*"):
            if path.is_file():
                path.unlink()
        self.config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {"emergency_stop": True, "allow_broker_submit": False},
        }

    def write_stale_evidence(self):
        pd.DataFrame(
            [
                {
                    "snapshot_run_id": "stale-run",
                    "broker": "huatai",
                    "account_id": "masked",
                    "environment": "live",
                    "cash": 2567.44,
                    "available_cash": 0.0,
                    "market_value": 0.0,
                    "total_asset": 2567.44,
                    "frozen_cash": 0.0,
                    "positions_count": 0,
                    "generated_at": "2026-07-31T19:00:00+08:00",
                }
            ]
        ).to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)
        pd.DataFrame(columns=["snapshot_run_id", "symbol"]).to_csv(
            self.output / "auto_live_broker_positions.csv", index=False
        )
        pd.DataFrame([{"check": "old", "passed": True}]).to_csv(
            self.output / "auto_live_broker_account_audit.csv", index=False
        )

    def test_invalidation_archives_stale_snapshot_and_publishes_blocker(self):
        self.write_stale_evidence()

        paths = invalidate_huatai_broker_account_evidence(
            self.config, "desktop layout unreadable"
        )

        snapshot = pd.read_csv(paths["auto_live_broker_account_snapshot.csv"])
        audit = pd.read_csv(paths["auto_live_broker_account_audit.csv"])
        invalidation = pd.read_csv(paths["auto_live_broker_snapshot_invalidation.csv"])
        archive = Path(invalidation.loc[0, "archive_dir"])
        self.assertTrue(str(snapshot.loc[0, "snapshot_run_id"]).startswith("invalid-"))
        self.assertEqual(snapshot.loc[0, "environment"], "unknown")
        self.assertEqual(float(snapshot.loc[0, "cash"]), -1.0)
        self.assertEqual(float(snapshot.loc[0, "unclassified_asset_value"]), -1.0)
        self.assertFalse(bool(audit.loc[0, "passed"]))
        self.assertEqual(invalidation.loc[0, "previous_snapshot_run_id"], "stale-run")
        self.assertTrue((archive / "auto_live_broker_account_snapshot.csv").exists())
        archived = pd.read_csv(archive / "auto_live_broker_account_snapshot.csv")
        self.assertEqual(archived.loc[0, "snapshot_run_id"], "stale-run")

    def test_desktop_collection_failure_invalidates_old_standard_snapshot(self):
        self.write_stale_evidence()
        raw_snapshot = self.output / "huatai_account_snapshot.json"
        raw_snapshot.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "diagnostics": {"stage": "account_controls_unreadable"},
                    "message": "layout changed",
                }
            ),
            encoding="utf-8",
        )
        completed = argparse.Namespace(returncode=1, stdout="layout changed", stderr="")

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=raw_snapshot),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch(
                "ashare_quant.cli.write_huatai_client_status",
                return_value=self.output / "huatai_client_status.csv",
            ),
            patch(
                "ashare_quant.cli.write_huatai_canary_arming_audit",
                return_value=self.output / "canary.csv",
            ),
            patch(
                "ashare_quant.cli.regenerate_report",
                return_value=self.output / "report.html",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "layout changed"):
                run_huatai_read_only_refresh(self.config)

        current = pd.read_csv(self.output / "auto_live_broker_account_snapshot.csv")
        refresh = pd.read_csv(self.output / "huatai_read_only_refresh.csv")
        self.assertTrue(str(current.loc[0, "snapshot_run_id"]).startswith("invalid-"))
        self.assertEqual(refresh.loc[0, "status"], "failed")
        self.assertTrue((self.output / "auto_live_broker_snapshot_invalidation.csv").exists())


if __name__ == "__main__":
    unittest.main()
