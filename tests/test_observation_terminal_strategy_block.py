from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from ashare_quant.observation_task import build_observation_task_status


ROOT = Path.cwd()
RUNNER = ROOT / "scripts" / "daily_observation.ps1"
DAEMON = ROOT / "scripts" / "observation_daemon.ps1"


def test_strategy_blocked_daemon_is_alive_but_observation_remains_blocked():
    now = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)
    daemon = {
        "status": "strategy_blocked",
        "mode": "observation_only_daemon",
        "pid": 456,
        "generated_at": (now - timedelta(seconds=20)).isoformat(),
        "state": {"last_exit_code": 20, "attempt_count": 1},
    }
    summary, audit = build_observation_task_status(
        pd.DataFrame(),
        {},
        {"observation_task": {"required": True}},
        daemon_status=daemon,
        daemon_lock={"pid": 456, "mode": "observation_only_daemon"},
        now=now,
        pid_alive=lambda pid: pid == 456,
    )

    by_check = audit.set_index("check")
    assert summary.loc[0, "status"] == "strategy_blocked"
    assert summary.loc[0, "daemon_status"] == "strategy_blocked"
    assert bool(by_check.loc["observation_daemon_ready", "passed"])
    assert not bool(by_check.loc["observation_last_run_completed", "passed"])
    assert not bool(by_check.loc["observation_result_fresh", "passed"])


def test_runner_classifies_preflight_as_terminal_after_running_readonly_followups():
    text = RUNNER.read_text(encoding="utf-8-sig")
    classify = text.index("Forward candidate preflight failed before market refresh:")
    broker = text.index('"broker-refresh"', classify)
    report = text.index('"report"', broker)
    alert = text.index('"observation-alert"', report)

    assert "Get-ObservationFailure" in text
    assert "exit_code = if ($strategyBlocked) { 20 } else { 1 }" in text
    catch = text.index("} catch {", classify)
    assert "Get-ObservationFailure $failureText" in text[catch:broker]
    assert '"strategy_blocked"' in text
    assert classify < broker < report < alert
    assert "exit $observationExitCode" in text


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows daemon state behavior")
def test_daemon_does_not_retry_terminal_strategy_block_today(tmp_path):
    now = datetime.now().astimezone()
    if now.weekday() >= 5:
        pytest.skip("Requires a weekday schedule")
    output = tmp_path / "terminal-strategy-block"
    output.mkdir(parents=True)
    state = {
        "last_success_date": "",
        "attempt_date": now.strftime("%Y-%m-%d"),
        "attempt_count": 1,
        "next_retry_at": "",
        "last_exit_code": 20,
        "last_message": "strategy_blocked",
        "last_finished_at": now.isoformat(),
    }
    (output / "observation_daemon_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(DAEMON),
            "-Config", str(ROOT / "configs" / "default.yaml"),
            "-AlertConfig", str(ROOT / "configs" / "huatai_desktop_canary.yaml"),
            "-OutputDir", str(output),
            "-StartAt", "00:00",
            "-PollSeconds", "10",
            "-Once",
            "-DryRun",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    status = json.loads(
        (output / "observation_daemon_status.json").read_text(encoding="utf-8-sig")
    )
    assert status["status"] == "waiting"
    assert status["message"] == "strategy_blocked_no_retry"
    assert status["state"]["attempt_count"] == 1


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows daemon state behavior")
def test_daemon_recovers_terminal_strategy_block_from_runner_status(tmp_path):
    now = datetime.now().astimezone()
    if now.weekday() >= 5:
        pytest.skip("Requires a weekday schedule")
    output = tmp_path / "recovered-terminal-strategy-block"
    output.mkdir(parents=True)
    config = (ROOT / "configs" / "default.yaml").resolve()
    pd.DataFrame(
        [
            {
                "status": "strategy_blocked",
                "mode": "observation_only",
                "started_at": (now - timedelta(minutes=2)).isoformat(),
                "finished_at": (now - timedelta(minutes=1)).isoformat(),
                "elapsed_seconds": 1.0,
                "exit_code": 20,
                "config": str(config),
                "max_symbols": 800,
                "message": "strategy_blocked",
            }
        ]
    ).to_csv(output / "daily_observation_status.csv", index=False)

    completed = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(DAEMON),
            "-Config", str(config),
            "-AlertConfig", str(ROOT / "configs" / "huatai_desktop_canary.yaml"),
            "-OutputDir", str(output),
            "-StartAt", "00:00",
            "-PollSeconds", "10",
            "-Once",
            "-DryRun",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    state = json.loads(
        (output / "observation_daemon_state.json").read_text(encoding="utf-8-sig")
    )
    assert state["last_exit_code"] == 20
    assert state["next_retry_at"] == ""
    assert state["last_message"] == "recovered_strategy_block_from_daily_observation_status"
