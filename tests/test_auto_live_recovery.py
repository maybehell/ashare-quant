from contextlib import closing
import os
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive_recovery import (
    _replace_directory_with_retry,
    auto_live_recovery_config_status,
    build_auto_live_recovery_audit,
    create_auto_live_recovery_point,
)


class AutoLiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.output = self.root / "outputs"
        self.output.mkdir(parents=True)
        self.state_db = self.root / "state.sqlite3"
        with closing(sqlite3.connect(self.state_db)) as conn:
            conn.execute("pragma journal_mode=wal")
            conn.execute("create table account_snapshots(date text primary key, cash real, equity real)")
            conn.execute("insert into account_snapshots values ('2026-07-11', 1000, 1000)")
            conn.commit()
        self.config = {
            "paths": {"output_dir": str(self.output), "state_db": str(self.state_db)},
            "auto_live": {
                "recovery": {
                    "enabled": True,
                    "required_before_submit": True,
                    "retention_points": 2,
                    "require_hmac": True,
                    "hmac_key_env": "TEST_RECOVERY_KEY",
                    "critical_files": ["account_state_snapshot.csv"],
                    "required_files": ["account_state_snapshot.csv"],
                }
            },
        }
        pd.DataFrame([{"cash": 1000.0}]).to_csv(self.output / "account_state_snapshot.csv", index=False)
        self.tickets = pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100}])

    def create(self, run_id="run-1"):
        return create_auto_live_recovery_point(
            self.config,
            run_id=run_id,
            tickets=self.tickets,
            tickets_sha256="a" * 64,
            auto_live_config_sha256="b" * 64,
        )

    def test_wal_safe_signed_recovery_point_is_independently_readable(self):
        with patch.dict(os.environ, {"TEST_RECOVERY_KEY": "r" * 40}, clear=False):
            paths = self.create()
        index = pd.read_csv(paths["auto_live_recovery_points.csv"])
        point = Path(index.iloc[-1]["path"])
        with closing(sqlite3.connect(point / "state.sqlite3")) as conn:
            row = conn.execute("select cash from account_snapshots").fetchone()
        audit = pd.read_csv(paths["auto_live_recovery_audit.csv"])
        self.assertEqual(row[0], 1000)
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertTrue((point / "submission_tickets.csv").exists())
        self.assertTrue((point / "evidence" / "account_state_snapshot.csv").exists())

    def test_directory_commit_retries_transient_windows_access_denied(self):
        staging = self.root / "staging"
        final_dir = self.root / "committed"
        staging.mkdir()
        (staging / "manifest.json").write_text("{}", encoding="utf-8")
        real_replace = os.replace
        calls = 0

        def flaky_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise PermissionError(13, "transient access denied")
            return real_replace(source, destination)

        with patch("ashare_quant.autolive_recovery.os.replace", side_effect=flaky_replace):
            _replace_directory_with_retry(
                staging, final_dir, attempts=3, delay_seconds=0
            )

        self.assertEqual(calls, 3)
        self.assertFalse(staging.exists())
        self.assertTrue((final_dir / "manifest.json").is_file())

    def test_directory_commit_never_overwrites_existing_destination(self):
        staging = self.root / "staging"
        final_dir = self.root / "committed"
        staging.mkdir()
        final_dir.mkdir()

        with patch("ashare_quant.autolive_recovery.os.replace") as replace:
            with self.assertRaises(FileExistsError):
                _replace_directory_with_retry(staging, final_dir, delay_seconds=0)

        replace.assert_not_called()
        self.assertTrue(staging.exists())
        self.assertTrue(final_dir.exists())

    def test_backup_preserves_pre_mutation_database_state(self):
        with patch.dict(os.environ, {"TEST_RECOVERY_KEY": "s" * 40}, clear=False):
            paths = self.create()
        with closing(sqlite3.connect(self.state_db)) as conn:
            conn.execute("update account_snapshots set cash=500")
            conn.commit()
        point = Path(pd.read_csv(paths["auto_live_recovery_points.csv"]).iloc[-1]["path"])
        with closing(sqlite3.connect(point / "state.sqlite3")) as conn:
            backup_cash = conn.execute("select cash from account_snapshots").fetchone()[0]
        self.assertEqual(backup_cash, 1000)

    def test_tampered_recovery_file_fails_audit(self):
        with patch.dict(os.environ, {"TEST_RECOVERY_KEY": "t" * 40}, clear=False):
            paths = self.create()
            point = Path(pd.read_csv(paths["auto_live_recovery_points.csv"]).iloc[-1]["path"])
            (point / "submission_tickets.csv").write_text("tampered", encoding="utf-8")
            audit = build_auto_live_recovery_audit(self.config).set_index("check")
        self.assertFalse(bool(audit.loc["recovery_files_hash_valid", "passed"]))

    def test_required_recovery_rejects_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HMAC key missing"):
                self.create()
        self.assertFalse(list((self.output / "auto_live_backups").glob("*")) if (self.output / "auto_live_backups").exists() else [])

    def test_required_recovery_rejects_missing_evidence(self):
        (self.output / "account_state_snapshot.csv").unlink()
        with patch.dict(os.environ, {"TEST_RECOVERY_KEY": "m" * 40}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "required recovery evidence missing"):
                self.create()

    def test_retention_removes_only_old_recovery_children(self):
        outside = self.root / "do-not-delete.txt"
        outside.write_text("safe", encoding="utf-8")
        with patch.dict(os.environ, {"TEST_RECOVERY_KEY": "q" * 40}, clear=False):
            self.create("run-1")
            self.create("run-2")
            self.create("run-3")
        points = [path for path in (self.output / "auto_live_backups").iterdir() if path.is_dir()]
        self.assertEqual(len(points), 2)
        self.assertTrue(outside.exists())

    def test_config_status_blocks_required_missing_state_db(self):
        self.state_db.unlink()
        passed, value, required = auto_live_recovery_config_status(self.config)
        self.assertTrue(required)
        self.assertFalse(passed)
        self.assertIn("recovery_state_db_present", value)


if __name__ == "__main__":
    unittest.main()
