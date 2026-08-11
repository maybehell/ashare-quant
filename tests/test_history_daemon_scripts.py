from datetime import datetime, timedelta, timezone
import hashlib
import json
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
    assert "source_plan_identity" in text
    assert "Get-FileHash" in text
    assert "script_sha256" in text
    assert "schema_version = 3" in text
    assert "validated partial data is preserved" in text
    assert '"history-validate"' in text
    assert '"auto-live-run"' not in text
    assert '"submit"' not in text.lower()


def test_history_daemon_accelerates_only_zero_failure_healthy_batches():
    text = DAEMON.read_text(encoding="utf-8")

    assert "HealthyIntervalMinutes = 15" in text
    assert "HealthyIntervalMinutes cannot exceed IntervalMinutes" in text
    assert "Get-NextBatchIntervalMinutes" in text
    assert '$Progress.source_status -eq "ok"' in text
    assert "$Progress.failed_items -eq 0" in text
    assert "$Progress.attempted_symbols -gt 0" in text
    assert "$Progress.successful_symbols -eq [int]$Progress.attempted_symbols" in text
    assert "Healthy zero-failure batch completed" in text


def source_plan_identity() -> str:
    default_hash = hashlib.sha256((ROOT / "configs" / "default.yaml").read_bytes()).hexdigest()
    override_hash = hashlib.sha256(
        (ROOT / "configs" / "extended_history_validation.yaml").read_bytes()
    ).hexdigest()
    return f"{default_hash}:{override_hash}"


def run_dry_daemon(
    output_dir: Path, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(DAEMON),
            "-Config",
            "configs/extended_history_validation.yaml",
            "-OutputDir",
            str(output_dir),
            "-RunOnce",
            "-DryRun",
            "-QuietStart",
            "00:00",
            "-QuietEnd",
            "00:00",
            *(extra_args or []),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )


def write_future_source_backoff(output_dir: Path, identity: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "state": "source_unavailable",
        "source_plan_identity": identity,
        "next_run_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "source_failure_streak": 2,
        "source_backoff_minutes": 240,
    }
    (output_dir / "history_backfill_daemon_status.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_history_daemon_discards_backoff_from_changed_source_plan(tmp_path):
    output_dir = tmp_path / "changed-plan"
    write_future_source_backoff(output_dir, "obsolete")

    completed = run_dry_daemon(output_dir)

    assert completed.returncode == 0, completed.stderr
    status = json.loads((output_dir / "history_backfill_daemon_status.json").read_text(encoding="utf-8-sig"))
    assert status["state"] == "dry_run"
    assert status["source_plan_identity"] == source_plan_identity()


def test_history_daemon_honors_backoff_for_current_source_plan(tmp_path):
    output_dir = tmp_path / "current-plan"
    write_future_source_backoff(output_dir, source_plan_identity())

    completed = run_dry_daemon(output_dir)

    assert completed.returncode == 75, completed.stderr
    status = json.loads((output_dir / "history_backfill_daemon_status.json").read_text(encoding="utf-8-sig"))
    assert status["state"] == "source_unavailable"
    assert status["source_failure_streak"] == 2
    assert "still active" in status["message"]

def test_history_startup_installer_persists_source_backoff_setting():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "SourceFailureBackoffMinutes" in text
    assert '"-SourceFailureBackoffMinutes"' in text
    assert "Source-wide failure backoff" in text


def test_history_daemon_rejects_unsafe_healthy_intervals(tmp_path):
    too_fast = run_dry_daemon(
        tmp_path / "too-fast", ["-HealthyIntervalMinutes", "10"]
    )
    slower_than_regular = run_dry_daemon(
        tmp_path / "too-slow",
        ["-IntervalMinutes", "30", "-HealthyIntervalMinutes", "45"],
    )

    assert too_fast.returncode == 1
    assert "HealthyIntervalMinutes must be at least 15" in too_fast.stderr
    assert slower_than_regular.returncode == 1
    assert "HealthyIntervalMinutes cannot exceed IntervalMinutes" in slower_than_regular.stderr


def test_history_startup_installer_persists_healthy_interval_setting():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "HealthyIntervalMinutes = 15" in text
    assert '"-HealthyIntervalMinutes"' in text
    assert "healthy=$HealthyIntervalMinutes" in text


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