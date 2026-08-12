from datetime import datetime, timedelta, timezone

import pandas as pd

from ashare_quant.observation_task import build_observation_task_status


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def running_daemon() -> dict[str, object]:
    return {
        "status": "running",
        "mode": "observation_only_daemon",
        "pid": 456,
        "generated_at": (NOW - timedelta(seconds=20)).isoformat(),
        "state": {
            "child_pid": 789,
            "child_started_at": (NOW - timedelta(minutes=2)).isoformat(),
            "elapsed_seconds": 120,
        },
    }


def test_live_daemon_child_is_reported_as_running():
    summary, audit = build_observation_task_status(
        pd.DataFrame(),
        {},
        {"observation_task": {"required": True}},
        daemon_status=running_daemon(),
        daemon_lock={"pid": 456, "mode": "observation_only_daemon"},
        now=NOW,
        pid_alive=lambda pid: pid in {456, 789},
    )

    by_check = audit.set_index("check")
    assert summary.loc[0, "status"] == "running"
    assert int(summary.loc[0, "elapsed_seconds"]) == 120
    assert bool(by_check.loc["observation_last_run_completed", "passed"])
    assert bool(by_check.loc["observation_result_fresh", "passed"])
    assert bool(by_check.loc["observation_daemon_ready", "passed"])


def test_daemon_running_claim_with_dead_child_fails_closed():
    summary, audit = build_observation_task_status(
        pd.DataFrame(),
        {},
        {"observation_task": {"required": True}},
        daemon_status=running_daemon(),
        daemon_lock={"pid": 456, "mode": "observation_only_daemon"},
        now=NOW,
        pid_alive=lambda pid: pid == 456,
    )

    by_check = audit.set_index("check")
    assert summary.loc[0, "status"] == "not_run"
    assert not bool(by_check.loc["observation_last_run_completed", "passed"])
    assert not bool(by_check.loc["observation_daemon_ready", "passed"])
