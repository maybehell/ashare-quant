from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.observation_task import build_observation_task_status, write_observation_task_status


NOW = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)


def completed_status(finished_at: datetime = NOW - timedelta(hours=2)) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "status": "completed",
                "mode": "observation_only",
                "started_at": (finished_at - timedelta(minutes=10)).isoformat(),
                "finished_at": finished_at.isoformat(),
                "elapsed_seconds": 600,
                "exit_code": 0,
                "config": "configs/default.yaml",
                "max_symbols": 800,
                "message": "completed",
            }
        ]
    )


class ObservationTaskTests(unittest.TestCase):
    def test_optional_task_that_has_not_run_is_advisory(self):
        summary, audit = build_observation_task_status(
            pd.DataFrame(),
            {},
            {"observation_task": {"required": False}},
            now=NOW,
        )

        self.assertEqual(summary.loc[0, "status"], "not_run")
        self.assertTrue(audit["passed"].all())

    def test_fresh_completed_task_passes(self):
        summary, audit = build_observation_task_status(
            completed_status(),
            {},
            {"observation_task": {"required": True, "max_age_hours": 36}},
            now=NOW,
        )

        self.assertEqual(summary.loc[0, "status"], "completed")
        self.assertEqual(float(summary.loc[0, "age_hours"]), 2.0)
        self.assertTrue(audit["passed"].all())

    def test_failed_task_fails_completion_and_freshness(self):
        status = completed_status()
        status.loc[0, "status"] = "failed"
        status.loc[0, "exit_code"] = 1
        status.loc[0, "message"] = "command_failed"

        summary, audit = build_observation_task_status(status, {}, {}, now=NOW)
        by_check = audit.set_index("check")

        self.assertEqual(summary.loc[0, "status"], "failed")
        self.assertFalse(bool(by_check.loc["observation_last_run_completed", "passed"]))
        self.assertFalse(bool(by_check.loc["observation_result_fresh", "passed"]))

    def test_old_success_is_stale(self):
        summary, audit = build_observation_task_status(
            completed_status(NOW - timedelta(hours=40)),
            {},
            {"observation_task": {"max_age_hours": 36}},
            now=NOW,
        )

        self.assertEqual(summary.loc[0, "status"], "stale")
        self.assertFalse(bool(audit.set_index("check").loc["observation_result_fresh", "passed"]))

    def test_live_daemon_is_reported_without_claiming_a_completed_run(self):
        daemon_status = {
            "status": "waiting",
            "mode": "observation_only_daemon",
            "pid": 456,
            "generated_at": (NOW - timedelta(minutes=1)).isoformat(),
        }
        daemon_lock = {"pid": 456, "mode": "observation_only_daemon"}

        summary, audit = build_observation_task_status(
            pd.DataFrame(),
            {},
            {"observation_task": {"required": True}},
            daemon_status=daemon_status,
            daemon_lock=daemon_lock,
            now=NOW,
            pid_alive=lambda pid: pid == 456,
        )
        by_check = audit.set_index("check")

        self.assertEqual(summary.loc[0, "status"], "scheduled")
        self.assertEqual(summary.loc[0, "mode"], "observation_only_daemon")
        self.assertEqual(int(summary.loc[0, "daemon_pid"]), 456)
        self.assertTrue(bool(by_check.loc["observation_daemon_ready", "passed"]))
        self.assertFalse(bool(by_check.loc["observation_last_run_completed", "passed"]))

    def test_running_daemon_heartbeat_is_ready(self):
        daemon_status = {
            "status": "running",
            "mode": "observation_only_daemon",
            "pid": 456,
            "generated_at": (NOW - timedelta(seconds=20)).isoformat(),
            "state": {"child_pid": 789, "elapsed_seconds": 120},
        }
        summary, audit = build_observation_task_status(
            pd.DataFrame(),
            {},
            {"observation_task": {"required": True}},
            daemon_status=daemon_status,
            daemon_lock={"pid": 456, "mode": "observation_only_daemon"},
            now=NOW,
            pid_alive=lambda pid: pid in {456, 789},
        )

        self.assertEqual(summary.loc[0, "status"], "running")
        self.assertEqual(summary.loc[0, "daemon_status"], "running")
        self.assertTrue(bool(audit.set_index("check").loc["observation_daemon_ready", "passed"]))
    def test_stale_daemon_heartbeat_fails_daemon_audit(self):
        daemon_status = {
            "status": "waiting",
            "mode": "observation_only_daemon",
            "pid": 456,
            "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
        }
        _, audit = build_observation_task_status(
            pd.DataFrame(),
            {},
            {},
            daemon_status=daemon_status,
            daemon_lock={"pid": 456, "mode": "observation_only_daemon"},
            now=NOW,
            pid_alive=lambda pid: True,
        )

        self.assertFalse(bool(audit.set_index("check").loc["observation_daemon_ready", "passed"]))
    def test_live_fresh_lock_reports_running(self):
        lock = {
            "pid": 123,
            "started_at": (NOW - timedelta(minutes=20)).isoformat(),
            "mode": "observation_only",
        }
        summary, audit = build_observation_task_status(
            pd.DataFrame(),
            lock,
            {"observation_task": {"required": True, "max_runtime_hours": 6}},
            now=NOW,
            pid_alive=lambda pid: pid == 123,
        )

        self.assertEqual(summary.loc[0, "status"], "running")
        self.assertTrue(audit["passed"].all())

    def test_live_lock_over_runtime_is_stale(self):
        lock = {
            "pid": 123,
            "started_at": (NOW - timedelta(hours=7)).isoformat(),
            "mode": "observation_only",
        }
        summary, audit = build_observation_task_status(
            pd.DataFrame(),
            lock,
            {"observation_task": {"required": True, "max_runtime_hours": 6}},
            now=NOW,
            pid_alive=lambda pid: pid == 123,
        )
        by_check = audit.set_index("check")

        self.assertEqual(summary.loc[0, "status"], "stale_lock")
        self.assertFalse(bool(by_check.loc["observation_last_run_completed", "passed"]))
        self.assertFalse(bool(by_check.loc["observation_lock_healthy", "passed"]))


    def test_dead_lock_is_detected(self):
        lock = {
            "pid": 123,
            "started_at": (NOW - timedelta(minutes=20)).isoformat(),
            "mode": "observation_only",
        }
        summary, audit = build_observation_task_status(
            pd.DataFrame(),
            lock,
            {},
            now=NOW,
            pid_alive=lambda pid: False,
        )

        self.assertEqual(summary.loc[0, "status"], "stale_lock")
        self.assertFalse(bool(audit.set_index("check").loc["observation_lock_healthy", "passed"]))

    def test_writer_does_not_create_raw_status_file(self):
        output = Path.cwd() / "test_outputs" / "observation_task_writer"
        output.mkdir(parents=True, exist_ok=True)
        raw_status = output / "daily_observation_status.csv"
        raw_status.unlink(missing_ok=True)

        paths = write_observation_task_status(
            {"paths": {"output_dir": str(output)}, "observation_task": {"required": False}}
        )

        self.assertFalse(raw_status.exists())
        self.assertTrue(paths["daily_observation_summary.csv"].exists())
        self.assertTrue(paths["daily_observation_audit.csv"].exists())


if __name__ == "__main__":
    unittest.main()
