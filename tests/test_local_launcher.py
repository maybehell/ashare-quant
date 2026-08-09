import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalLauncherTests(unittest.TestCase):
    def test_batch_launcher_forwards_arguments_and_supports_no_pause(self):
        text = (ROOT / "run_local.cmd").read_text(encoding="utf-8")
        self.assertIn('-Refresh %*', text)
        self.assertIn('"-NoPause"', text)
        self.assertIn('if "%PAUSE_AFTER%"=="1" pause', text)

    def test_powershell_launcher_treats_native_stderr_as_progress(self):
        text = (ROOT / "scripts" / "local_run.ps1").read_text(encoding="utf-8")
        self.assertIn('[switch]$Help', text)
        self.assertIn('$ErrorActionPreference = "Continue"', text)
        self.assertIn('$exitCode = $LASTEXITCODE', text)

    @unittest.skipUnless(os.name == "nt", "Windows launcher is unavailable")
    def test_help_mode_does_not_start_local_workflow(self):
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "run_local.cmd", "-Help", "-NoPause"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("A-share quant local operator", output)
        self.assertNotIn("strategy preflight", output)


if __name__ == "__main__":
    unittest.main()
