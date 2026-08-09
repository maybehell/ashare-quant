from pathlib import Path
import subprocess
import sys
import unittest


class ObservationTaskScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd()
        self.runner = self.root / "scripts" / "daily_observation.ps1"
        self.installer = self.root / "scripts" / "install_observation_task.ps1"

    def test_daily_runner_is_observation_only(self):
        text = self.runner.read_text(encoding="utf-8")

        self.assertIn('"forward-daily"', text)
        self.assertIn('"--refresh"', text)
        self.assertNotIn('"observe-candidates"', text)
        self.assertIn('"report"', text)
        self.assertIn('"observation-alert"', text)
        self.assertIn("$resolvedAlertConfig", text)
        self.assertIn("$observationExitCode", text)
        self.assertIn("$MaxRuntimeHours", text)
        self.assertIn('"stale_lock"', text)
        self.assertIn("Publish-StaleObservationLock", text)
        self.assertIn("$processMatchesLock", text)
        self.assertIn("process_started_at", text)
        self.assertIn("process_name", text)
        self.assertIn("daily_observation_delivery_status.csv", text)
        self.assertIn("exit $observationExitCode", text)
        self.assertNotIn("exit $alertExitCode", text)
        self.assertIn("exit 2", text)
        self.assertNotIn('"paper"', text)
        self.assertNotIn('"daily"', text)
        self.assertNotIn('"auto-live-run"', text)
        self.assertNotIn('"submit"', text)
        self.assertIn("[switch]$SkipAlert", text)
        self.assertIn("if (-not $SkipAlert)", text)
        self.assertIn("[switch]$BrokerReadOnlyRefresh", text)
        self.assertIn('"broker-refresh"', text)
        self.assertIn("broker_readonly_refresh_exit_code", text)

    def test_installer_requires_explicit_register_switch(self):
        text = self.installer.read_text(encoding="utf-8")
        preview_guard = text.index("if (-not $Register)")
        registration = text.index("Register-ScheduledTask")

        self.assertLess(preview_guard, registration)
        self.assertIn("-ConfirmReplace", text)
        self.assertIn("observation_only", text)
        self.assertIn('"-AlertConfig"', text)
        self.assertIn('"-MaxRuntimeHours"', text)
        self.assertIn("New-TimeSpan -Hours $MaxRuntimeHours", text)
        self.assertIn("[switch]$BrokerReadOnlyRefresh", text)
        self.assertIn('"-BrokerReadOnlyRefresh"', text)

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows Task Scheduler script")
    def test_default_installer_run_is_preview_only(self):
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.installer),
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
        self.assertIn("Mode: observation_only", completed.stdout)
        self.assertIn("Alert config:", completed.stdout)
        self.assertIn("Preview only", completed.stdout)
        self.assertIn("Maximum runtime: 1 hours", completed.stdout)


if __name__ == "__main__":
    unittest.main()
