from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path.cwd()
DAEMON = ROOT / "scripts" / "history_backfill_daemon.ps1"
INSTALLER = ROOT / "scripts" / "install_history_backfill_startup.ps1"


def test_history_daemon_has_audited_source_outage_backoff():
    text = DAEMON.read_text(encoding="utf-8")

    assert "SourceFailureBackoffMinutes" in text
    assert 'source_status -eq "source_unavailable"' in text
    assert "$exitCode = 75" in text
    assert 'Write-Status "source_unavailable"' in text
    assert "source_failure_streak" in text
    assert "source_backoff_minutes" in text
    assert "Persisted historical source outage backoff is still active." in text
    assert "$sourceResumeAt" in text
    assert "validated partial data is preserved" in text
    assert '"history-validate"' in text
    assert '"auto-live-run"' not in text
    assert '"submit"' not in text.lower()


def test_history_startup_installer_persists_source_backoff_setting():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "SourceFailureBackoffMinutes" in text
    assert '"-SourceFailureBackoffMinutes"' in text
    assert "Source-wide failure backoff" in text


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="PowerShell syntax validation")
def test_history_daemon_and_installer_parse_after_backoff_change():
    for script in (DAEMON, INSTALLER):
        command = (
            "$errors=$null; [void][System.Management.Automation.Language.Parser]::"
            f"ParseFile('{script}', [ref]$null, [ref]$errors); "
            "if($errors.Count){$errors | ForEach-Object {Write-Error $_}; exit 1}"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr