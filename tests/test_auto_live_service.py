import json
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive_service import (
    activate_auto_live_service_stop,
    clear_auto_live_service_stop,
    run_auto_live_service,
    write_auto_live_service_lock_audit,
)


class AutoLiveServiceTests(unittest.TestCase):
    def fresh_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def config(self, output: Path, submit_enabled: bool = False) -> dict:
        return {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "service": {
                    "enabled": submit_enabled,
                    "allow_submit": submit_enabled,
                    "interval_seconds": 1,
                    "max_cycles": 0,
                    "max_consecutive_failures": 2,
                    "failure_backoff_seconds": 1,
                    "lock_stale_seconds": 60,
                }
            },
        }

    def fake_cycle(self, output: Path, status: str = "audit_complete"):
        calls = []

        def run(config, *, submit=False, confirm_account_sync=False):
            calls.append((submit, confirm_account_sync))
            cycle_id = f"cycle-{len(calls)}"
            path = output / "auto_live_cycle_summary.csv"
            pd.DataFrame(
                [
                    {
                        "cycle_id": cycle_id,
                        "mode": "submit" if submit else "audit",
                        "status": status,
                        "blocking_stage": "auto_live_gate" if status == "blocked" else "",
                        "submitted_count": 1 if status == "completed" else 0,
                        "queried_count": 1 if status == "completed" else 0,
                        "fill_count": 0,
                        "started_at": "2026-07-11T09:00:00",
                        "completed_at": "2026-07-11T09:00:01",
                        "message": status,
                    }
                ]
            ).to_csv(path, index=False)
            return {"auto_live_cycle_summary.csv": path}

        return calls, run

    def test_default_service_runs_one_audit_cycle_and_releases_lock(self):
        output = self.fresh_output("auto_live_service_once")
        config = self.config(output)
        calls, fake = self.fake_cycle(output)

        with patch("ashare_quant.autolive_service.run_auto_live_cycle", side_effect=fake):
            paths = run_auto_live_service(config)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        history = pd.read_csv(paths["auto_live_service_history.csv"])
        heartbeat = pd.read_csv(paths["auto_live_service_heartbeat.csv"])
        self.assertEqual(calls, [(False, False)])
        self.assertEqual(summary.loc[0, "status"], "completed_once")
        self.assertEqual(len(history), 1)
        self.assertEqual(heartbeat.loc[0, "status"], "completed_once")
        self.assertFalse((output / "auto_live_service.lock").exists())

    def test_audit_blocker_is_reported_without_submit_fuse(self):
        output = self.fresh_output("auto_live_service_audit_blocker")
        config = self.config(output)
        calls, fake = self.fake_cycle(output, status="blocked")

        with patch("ashare_quant.autolive_service.run_auto_live_cycle", side_effect=fake):
            paths = run_auto_live_service(config)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        alerts = pd.read_csv(output / "auto_live_alert_outbox.csv")
        self.assertEqual(summary.loc[0, "status"], "completed_once_with_blockers")
        self.assertEqual(int(summary.loc[0, "blocked_cycle_count"]), 1)
        self.assertEqual(int(summary.loc[0, "consecutive_failures"]), 0)
        self.assertTrue(alerts.empty)

    def test_submit_service_requires_two_service_level_switches(self):
        output = self.fresh_output("auto_live_service_submit_guard")
        config = self.config(output, submit_enabled=False)

        with patch("ashare_quant.autolive_service.run_auto_live_cycle") as mocked:
            paths = run_auto_live_service(config, submit=True)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        self.assertEqual(summary.loc[0, "status"], "blocked_configuration")
        self.assertEqual(int(summary.loc[0, "cycle_count"]), 0)
        mocked.assert_not_called()

    def test_existing_service_lock_blocks_second_instance(self):
        output = self.fresh_output("auto_live_service_lock")
        config = self.config(output)
        (output / "auto_live_service.lock").write_text(
            json.dumps({"service_run_id": "active", "pid": 123, "created_at": datetime.now().isoformat()}),
            encoding="utf-8",
        )

        with patch("ashare_quant.autolive_service.run_auto_live_cycle") as mocked:
            paths = run_auto_live_service(config)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        self.assertEqual(summary.loc[0, "status"], "blocked_service_lock")
        mocked.assert_not_called()
        self.assertTrue((output / "auto_live_service.lock").exists())

    def test_loop_honors_max_cycles_and_wait_interval(self):
        output = self.fresh_output("auto_live_service_loop")
        config = self.config(output)
        calls, fake = self.fake_cycle(output)
        sleeps = []

        with patch("ashare_quant.autolive_service.run_auto_live_cycle", side_effect=fake):
            paths = run_auto_live_service(config, loop=True, interval_seconds=2, max_cycles=2, sleep_fn=sleeps.append)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        history = pd.read_csv(paths["auto_live_service_history.csv"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [2.0])
        self.assertEqual(summary.loc[0, "status"], "completed_max_cycles")
        self.assertEqual(len(history), 2)

    def test_single_cycle_exception_is_not_reported_as_completed(self):
        output = self.fresh_output("auto_live_service_single_exception")
        config = self.config(output)

        with patch("ashare_quant.autolive_service.run_auto_live_cycle", side_effect=RuntimeError("bridge down")):
            paths = run_auto_live_service(config)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        history = pd.read_csv(paths["auto_live_service_history.csv"])
        self.assertEqual(summary.loc[0, "status"], "stopped_cycle_exception")
        self.assertEqual(int(summary.loc[0, "failed_cycle_count"]), 1)
        self.assertEqual(history.loc[0, "cycle_status"], "exception")

    def test_alert_retry_exception_is_preserved_as_cycle_failure(self):
        output = self.fresh_output("auto_live_service_retry_exception")
        config = self.config(output)

        with (
            patch("ashare_quant.autolive_service.retry_auto_live_alerts", side_effect=RuntimeError("outbox unreadable")),
            patch("ashare_quant.autolive_service.send_auto_live_incident_alert", side_effect=RuntimeError("alert disk full")),
            patch("ashare_quant.autolive_service.run_auto_live_cycle") as cycle_mock,
        ):
            paths = run_auto_live_service(config)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        history = pd.read_csv(paths["auto_live_service_history.csv"])
        cycle_mock.assert_not_called()
        self.assertEqual(summary.loc[0, "status"], "stopped_cycle_exception")
        self.assertIn("outbox unreadable", history.loc[0, "message"])
        self.assertIn("alert_outbox_error=RuntimeError: alert disk full", history.loc[0, "message"])

    def test_consecutive_exceptions_trigger_failure_fuse(self):
        output = self.fresh_output("auto_live_service_failure_fuse")
        config = self.config(output)
        sleeps = []

        with patch("ashare_quant.autolive_service.run_auto_live_cycle", side_effect=RuntimeError("bridge down")):
            paths = run_auto_live_service(config, loop=True, max_cycles=10, sleep_fn=sleeps.append)

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        history = pd.read_csv(paths["auto_live_service_history.csv"])
        self.assertEqual(summary.loc[0, "status"], "stopped_failure_limit")
        self.assertEqual(int(summary.loc[0, "failed_cycle_count"]), 2)
        self.assertEqual(len(history), 2)
        self.assertEqual(sleeps, [1.0])
        alerts = pd.read_csv(output / "auto_live_alert_outbox.csv")
        self.assertEqual(alerts["event_type"].tolist().count("service_cycle_exception"), 1)
        self.assertEqual(alerts["event_type"].tolist().count("service_failure_fuse"), 1)
        self.assertTrue((alerts["status"] == "local_only").all())

    def test_service_retries_pending_alerts_before_every_cycle(self):
        output = self.fresh_output("auto_live_service_alert_retry")
        config = self.config(output)
        calls, fake = self.fake_cycle(output)

        with (
            patch("ashare_quant.autolive_service.run_auto_live_cycle", side_effect=fake),
            patch("ashare_quant.autolive_service.retry_auto_live_alerts") as retry_mock,
        ):
            run_auto_live_service(config, loop=True, interval_seconds=1, max_cycles=2, sleep_fn=lambda _: None)

        self.assertEqual(len(calls), 2)
        self.assertEqual(retry_mock.call_count, 2)
        retry_mock.assert_called_with(config)

    def test_repeated_submit_blockers_alert_once_and_trigger_fuse(self):
        output = self.fresh_output("auto_live_service_submit_blocker_fuse")
        config = self.config(output, submit_enabled=True)
        calls, fake = self.fake_cycle(output, status="blocked")
        sleeps = []

        with patch("ashare_quant.autolive_service.run_auto_live_cycle", side_effect=fake):
            paths = run_auto_live_service(
                config,
                submit=True,
                loop=True,
                max_cycles=10,
                sleep_fn=sleeps.append,
            )

        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        alerts = pd.read_csv(output / "auto_live_alert_outbox.csv")
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary.loc[0, "status"], "stopped_failure_limit")
        self.assertEqual(int(summary.loc[0, "blocked_cycle_count"]), 2)
        self.assertEqual(int(summary.loc[0, "consecutive_failures"]), 2)
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(alerts["event_type"].tolist().count("service_cycle_blocked"), 1)
        self.assertEqual(alerts["event_type"].tolist().count("service_failure_fuse"), 1)

    def test_stop_file_blocks_and_requires_explicit_resume(self):
        output = self.fresh_output("auto_live_service_stop")
        config = self.config(output)
        activate_auto_live_service_stop(config, reason="maintenance")

        with patch("ashare_quant.autolive_service.run_auto_live_cycle") as mocked:
            paths = run_auto_live_service(config)
        summary = pd.read_csv(paths["auto_live_service_summary.csv"])
        self.assertEqual(summary.loc[0, "status"], "stopped")
        mocked.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "--confirm"):
            clear_auto_live_service_stop(config)
        clear_auto_live_service_stop(config, confirm=True)
        self.assertFalse((output / "auto_live_service.stop").exists())

    def test_stale_heartbeat_is_audited_but_lock_is_not_deleted(self):
        output = self.fresh_output("auto_live_service_stale_lock")
        config = self.config(output)
        now = datetime(2026, 7, 11, 9, 30, 0)
        (output / "auto_live_service.lock").write_text(
            json.dumps({"service_run_id": "stale", "pid": 123, "created_at": (now - timedelta(minutes=10)).isoformat()}),
            encoding="utf-8",
        )
        pd.DataFrame([{"heartbeat_at": (now - timedelta(minutes=5)).isoformat()}]).to_csv(
            output / "auto_live_service_heartbeat.csv", index=False
        )

        path = write_auto_live_service_lock_audit(config, now=now)

        audit = pd.read_csv(path).set_index("check")
        self.assertFalse(bool(audit.loc["service_lock_clear", "passed"]))
        self.assertFalse(bool(audit.loc["service_heartbeat_fresh", "passed"]))
        self.assertTrue((output / "auto_live_service.lock").exists())


if __name__ == "__main__":
    unittest.main()
