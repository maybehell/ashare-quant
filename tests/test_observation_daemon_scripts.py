from datetime import datetime, timedelta
from pathlib import Path
import json
import subprocess
import sys
import unittest


class ObservationDaemonScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd()
        self.daemon = self.root / "scripts" / "observation_daemon.ps1"
        self.runner = self.root / "scripts" / "daily_observation.ps1"
        self.installer = self.root / "scripts" / "install_observation_startup.ps1"
        self.supervisor = self.root / "scripts" / "observation_supervisor.ps1"

    def test_daemon_is_observation_only_and_fail_safe(self):
        text = self.daemon.read_text(encoding="utf-8")
        self.assertIn("daily_observation.ps1", text)
        self.assertIn("observation_only_daemon", text)
        self.assertIn("observation_daemon.lock.json", text)
        self.assertIn("observation_daemon.stop", text)
        self.assertIn("MaxDailyAttempts", text)
        self.assertIn("RetryMinutes", text)
        self.assertIn('Publish-Status "running"', text)
        self.assertIn("Start-Process", text)
        self.assertIn("$null = $child.Handle", text)
        self.assertIn("$child.Refresh()", text)
        self.assertIn("-WindowStyle Hidden", text)
        self.assertIn("Stop-Process -Id $child.Id", text)
        self.assertIn("$retryBase = Get-Date", text)
        self.assertIn("last_success_date", text)
        self.assertNotIn('"paper"', text)
        self.assertNotIn('"auto-live-run"', text)
        self.assertNotIn('"submit"', text)
        self.assertIn("process_started_at", text)
        self.assertIn("Test-LockOwner", text)
        self.assertIn("Sync-RunnerCompletion", text)
        self.assertIn("Repair-PreScheduleSuccess", text)
        self.assertIn("discarded_pre_schedule_success", text)
        self.assertIn("daily_observation_status.csv", text)
        self.assertIn("script_sha256", text)
        self.assertIn("Get-FileHash", text)
        self.assertIn("recovered_from_daily_observation_status", text)
        self.assertIn("$finishedAt.TimeOfDay -ge $scheduledTime", text)
        self.assertIn("calendar-status", text)
        self.assertIn("--refresh-missing", text)
        self.assertIn("--strict-exit-code", text)
        self.assertIn("market_closed", text)
        self.assertIn("trading_calendar_unavailable", text)
        self.assertIn("[switch]$BrokerReadOnlyRefresh", text)
        self.assertIn('$arguments += "-BrokerReadOnlyRefresh"', text)
        self.assertIn("broker_readonly_refresh = [bool]$BrokerReadOnlyRefresh", text)

    def test_daily_runner_refreshes_local_status_without_trading_commands(self):
        text = self.runner.read_text(encoding="utf-8")

        self.assertIn('"local-status"', text)
        self.assertIn('"--live-config"', text)
        self.assertIn("Local operator status refresh failed", text)
        self.assertIn('"broker-refresh"', text)
        self.assertIn("[switch]$BrokerReadOnlyRefresh", text)
        self.assertNotIn('"auto-live-run"', text)
        self.assertNotIn('"broker-account-init"', text)
    @unittest.skipUnless(sys.platform.startswith("win"), "Windows daemon schedule boundary")
    def test_completion_before_schedule_does_not_suppress_scheduled_run(self):
        if datetime.now().astimezone().weekday() >= 5:
            self.skipTest("Schedule-boundary semantics require a weekday")
        now = datetime.now().astimezone()
        if now.hour == 23 and now.minute >= 58:
            self.skipTest("Cannot construct a same-day future schedule near midnight")
        start_at = (now + timedelta(minutes=2)).strftime("%H:%M")
        output = self.root / "test_outputs" / f"daemon_pre_schedule_{now:%Y%m%d%H%M%S%f}"
        output.mkdir(parents=True, exist_ok=True)
        config = (self.root / "configs" / "default.yaml").resolve()
        status = output / "daily_observation_status.csv"
        status.write_text(
            "status,mode,started_at,finished_at,elapsed_seconds,exit_code,config,max_symbols,message\n"
            f'completed,observation_only,{now.isoformat()},{now.isoformat()},1,0,"{config}",800,completed\n',
            encoding="utf-8-sig",
        )

        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.daemon),
                "-Config", str(config),
                "-AlertConfig", str(self.root / "configs" / "huatai_desktop_canary.yaml"),
                "-OutputDir", str(output),
                "-StartAt", start_at,
                "-PollSeconds", "10",
                "-Once",
                "-DryRun",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        daemon_status = json.loads(
            (output / "observation_daemon_status.json").read_text(encoding="utf-8-sig")
        )
        self.assertEqual(daemon_status["message"], "waiting_for_schedule")
        self.assertEqual(daemon_status["state"]["last_success_date"], "")
        self.assertRegex(daemon_status["script_sha256"], r"^[0-9a-f]{64}$")
    @unittest.skipUnless(sys.platform.startswith("win"), "Windows daemon state repair")
    def test_inherited_pre_schedule_success_is_repaired_and_run_remains_due(self):
        if datetime.now().astimezone().weekday() >= 5:
            self.skipTest("Schedule-boundary semantics require a weekday")
        now = datetime.now().astimezone()
        if now.hour == 0 and now.minute < 2:
            self.skipTest("Cannot construct a same-day prior schedule just after midnight")
        scheduled = now - timedelta(minutes=1)
        invalid_finished = now - timedelta(minutes=2)
        output = self.root / "test_outputs" / f"daemon_state_repair_{now:%Y%m%d%H%M%S%f}"
        output.mkdir(parents=True, exist_ok=True)
        config = (self.root / "configs" / "default.yaml").resolve()
        (output / "observation_daemon_state.json").write_text(
            json.dumps({
                "last_success_date": now.strftime("%Y-%m-%d"),
                "attempt_date": now.strftime("%Y-%m-%d"),
                "attempt_count": 1,
                "next_retry_at": "",
                "last_exit_code": 0,
                "last_message": "legacy_false_success",
                "last_finished_at": invalid_finished.isoformat(),
            }),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.daemon),
                "-Config", str(config),
                "-AlertConfig", str(self.root / "configs" / "huatai_desktop_canary.yaml"),
                "-OutputDir", str(output),
                "-StartAt", scheduled.strftime("%H:%M"),
                "-PollSeconds", "10",
                "-Once",
                "-DryRun",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        daemon_status = json.loads(
            (output / "observation_daemon_status.json").read_text(encoding="utf-8-sig")
        )
        self.assertEqual(daemon_status["status"], "dry_run_due")
        self.assertEqual(daemon_status["state"]["last_success_date"], "")
        repaired = json.loads(
            (output / "observation_daemon_state.json").read_text(encoding="utf-8-sig")
        )
        self.assertEqual(repaired["last_message"], "discarded_pre_schedule_success")
    def test_startup_installer_requires_explicit_registration(self):
        text = self.installer.read_text(encoding="utf-8")
        preview_guard = text.index("if (-not $Register)")
        write_action = text.index("Set-Content -LiteralPath $target")
        self.assertLess(preview_guard, write_action)
        self.assertIn("SpecialFolder]::Startup", text)
        self.assertIn("-ConfirmReplace", text)
        self.assertIn("observation_only_supervisor", text)
        self.assertIn("$StartNow", text)
        self.assertIn("observation_supervisor.ps1", text)
        self.assertIn("[switch]$BrokerReadOnlyRefresh", text)
        self.assertIn('"-BrokerReadOnlyRefresh"', text)


    def test_supervisor_only_restarts_observation_daemon(self):
        text = self.supervisor.read_text(encoding="utf-8")
        self.assertIn("observation_only_supervisor", text)
        self.assertIn("observation_daemon.ps1", text)
        self.assertIn("Get-LiveProcessFromLock", text)
        self.assertIn("MaxRestartsPerWindow", text)
        self.assertIn('Publish-Status "backoff"', text)
        self.assertIn("observation_daemon.stop", text)
        self.assertIn("-WindowStyle Hidden", text)
        self.assertIn("[switch]$BrokerReadOnlyRefresh", text)
        self.assertIn('$daemonArguments += "-BrokerReadOnlyRefresh"', text)
        self.assertIn("broker_readonly_refresh = [bool]$BrokerReadOnlyRefresh", text)
        self.assertNotIn("auto-live-run", text)
        self.assertNotIn("export-orders", text)
        self.assertNotIn("submit", text.lower())

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows daemon lock identity")
    def test_stale_lock_with_reused_pid_is_recovered(self):
        output = self.root / "test_outputs" / "observation_daemon_stale_reused_pid"
        output.mkdir(parents=True, exist_ok=True)
        lock = output / "observation_daemon.lock.json"
        sleeper = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 15"],
            cwd=self.root,
        )
        try:
            lock.write_text(
                json.dumps(
                    {
                        "pid": sleeper.pid,
                        "started_at": "2000-01-01T00:00:00+08:00",
                        "process_started_at": "2000-01-01T00:00:00+08:00",
                        "process_name": "powershell",
                        "mode": "observation_only_daemon",
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.daemon),
                    "-Config", str(self.root / "configs" / "default.yaml"),
                    "-AlertConfig", str(self.root / "configs" / "huatai_desktop_canary.yaml"),
                    "-OutputDir", str(output),
                    "-StartAt", "00:00",
                    "-PollSeconds", "10",
                    "-Once",
                    "-DryRun",
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=5)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((output / "observation_daemon_status.json").exists())
        self.assertFalse(lock.exists())
    @unittest.skipUnless(sys.platform.startswith("win"), "Windows Startup installer")
    def test_default_startup_installer_is_preview_only(self):
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.installer),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Mode: observation_only_supervisor", completed.stdout)
        self.assertIn("Preview only", completed.stdout)


    @unittest.skipUnless(sys.platform.startswith("win"), "Windows daemon completion recovery")
    def test_completed_runner_status_prevents_duplicate_daily_run(self):
        if datetime.now().astimezone().weekday() >= 5:
            self.skipTest("Schedule-boundary semantics require a weekday")
        output = self.root / "test_outputs" / "observation_daemon_completion_recovery"
        output.mkdir(parents=True, exist_ok=True)
        config = (self.root / "configs" / "default.yaml").resolve()
        now = datetime.now().astimezone()
        status = output / "daily_observation_status.csv"
        status.write_text(
            "status,mode,started_at,finished_at,elapsed_seconds,exit_code,config,max_symbols,message\n"
            f'completed,observation_only,{now.isoformat()},{now.isoformat()},1,0,"{config}",800,completed\n',
            encoding="utf-8-sig",
        )

        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.daemon),
                "-Config", str(config),
                "-AlertConfig", str(self.root / "configs" / "huatai_desktop_canary.yaml"),
                "-OutputDir", str(output),
                "-StartAt", "00:00",
                "-PollSeconds", "10",
                "-Once",
                "-DryRun",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        daemon_status = json.loads(
            (output / "observation_daemon_status.json").read_text(encoding="utf-8-sig")
        )
        self.assertEqual(daemon_status["message"], "already_completed_today")
        self.assertEqual(
            daemon_status["state"]["last_message"],
            "recovered_from_daily_observation_status",
        )


if __name__ == "__main__":
    unittest.main()