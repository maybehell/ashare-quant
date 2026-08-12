from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv


OBSERVATION_STATUS_COLUMNS = [
    "status",
    "mode",
    "started_at",
    "finished_at",
    "elapsed_seconds",
    "exit_code",
    "config",
    "max_symbols",
    "message",
]
OBSERVATION_SUMMARY_COLUMNS = [
    "status",
    "required",
    "mode",
    "started_at",
    "finished_at",
    "age_hours",
    "elapsed_seconds",
    "exit_code",
    "message",
    "daemon_status",
    "daemon_pid",
    "daemon_age_minutes",
]
OBSERVATION_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "message"]


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _as_float(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            exit_code = ctypes.c_ulong()
            try:
                return bool(ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))) and exit_code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


def _latest_status(status: pd.DataFrame) -> dict[str, object]:
    if status.empty:
        return {}
    frame = status.copy()
    if "finished_at" in frame.columns:
        frame["_finished"] = pd.to_datetime(frame["finished_at"], errors="coerce", utc=True)
        frame = frame.sort_values("_finished", na_position="first")
    return frame.iloc[-1].drop(labels=["_finished"], errors="ignore").to_dict()


def _load_lock(lock_path: Path) -> dict[str, object]:
    if not lock_path.exists():
        return {}
    try:
        loaded = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"invalid": True}
    return loaded if isinstance(loaded, dict) else {"invalid": True}


def build_observation_task_status(
    status: pd.DataFrame,
    lock: dict[str, object],
    config: dict[str, Any],
    *,
    daemon_status: dict[str, object] | None = None,
    daemon_lock: dict[str, object] | None = None,
    now: datetime | None = None,
    pid_alive: Callable[[int], bool] = _pid_is_alive,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    settings = config.get("observation_task", {}) if isinstance(config.get("observation_task"), dict) else {}
    required = _as_bool(settings.get("required", False))
    max_age_hours = max(float(settings.get("max_age_hours", 36)), 0.0)
    max_runtime_hours = max(float(settings.get("max_runtime_hours", 6)), 0.0)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    latest = _latest_status(status)
    daemon_status = daemon_status or {}
    daemon_lock = daemon_lock or {}
    daemon_evidence = bool(daemon_status or daemon_lock)
    daemon_mode = str(daemon_status.get("mode", daemon_lock.get("mode", "")) or "")
    daemon_pid = int(_as_float(daemon_status.get("pid", daemon_lock.get("pid"))) or 0)
    daemon_state = daemon_status.get("state") if isinstance(daemon_status.get("state"), dict) else {}
    daemon_child_pid = int(_as_float(daemon_state.get("child_pid")) or 0)
    daemon_child_alive = daemon_child_pid > 0 and pid_alive(daemon_child_pid)
    daemon_claims_running = str(daemon_status.get("status", "")).lower() == "running"
    daemon_generated = _timestamp(daemon_status.get("generated_at"))
    daemon_age_minutes = (
        (current - daemon_generated).total_seconds() / 60
        if daemon_generated
        else None
    )
    daemon_heartbeat_limit = max(
        float(settings.get("daemon_heartbeat_max_minutes", 5)), 0.0
    )
    daemon_alive = daemon_pid > 0 and pid_alive(daemon_pid)
    daemon_fresh = (
        daemon_age_minutes is not None
        and daemon_age_minutes <= daemon_heartbeat_limit
    )
    daemon_ready = (
        daemon_mode == "observation_only_daemon"
        and daemon_alive
        and daemon_fresh
        and str(daemon_status.get("status", "")).lower()
        in {"waiting", "running", "completed", "retry_wait"}
        and (not daemon_claims_running or daemon_child_alive)
    )
    daemon_execution_running = daemon_ready and daemon_claims_running

    lock_invalid = bool(lock.get("invalid"))
    lock_started = _timestamp(lock.get("started_at"))
    lock_age = (current - lock_started).total_seconds() / 3600 if lock_started else None
    lock_pid = int(_as_float(lock.get("pid")) or 0)
    lock_alive = bool(lock) and not lock_invalid and pid_alive(lock_pid)
    lock_fresh = lock_age is not None and lock_age <= max_runtime_hours
    running = lock_alive and lock_fresh

    finished = _timestamp(latest.get("finished_at"))
    age_hours = (current - finished).total_seconds() / 3600 if finished else None
    exit_code = _as_float(latest.get("exit_code"))
    mode = str(lock.get("mode") if running else latest.get("mode", "") or "")
    completion_ok = str(latest.get("status", "")).lower() == "completed" and exit_code == 0
    fresh = completion_ok and age_hours is not None and age_hours <= max_age_hours

    task_running = running or daemon_execution_running
    if task_running:
        summary_status = "running"
        message = "observation_in_progress"
    elif lock:
        summary_status = "stale_lock"
        message = "observation_lock_is_invalid_stale_or_process_missing"
    elif not latest and daemon_ready:
        summary_status = "scheduled"
        message = "daemon_waiting_for_schedule"
    elif not latest:
        summary_status = "not_run"
        message = "observation_task_has_not_run"
    elif not completion_ok:
        summary_status = "failed"
        message = str(latest.get("message", "observation_failed") or "observation_failed")
    elif not fresh:
        summary_status = "stale"
        message = "last_successful_observation_is_stale"
    else:
        summary_status = "completed"
        message = str(latest.get("message", "completed") or "completed")

    summary = pd.DataFrame(
        [
            {
                "status": summary_status,
                "required": required,
                "mode": "observation_only_daemon" if daemon_ready and not running else mode or "observation_only",
                "started_at": (
                    lock.get("started_at", "")
                    if running
                    else daemon_state.get("child_started_at", "")
                    if daemon_execution_running
                    else latest.get("started_at", "")
                ),
                "finished_at": latest.get("finished_at", ""),
                "age_hours": None if age_hours is None else round(max(age_hours, 0.0), 3),
                "elapsed_seconds": (
                    daemon_state.get("elapsed_seconds", "")
                    if daemon_execution_running
                    else latest.get("elapsed_seconds", "")
                ),
                "exit_code": latest.get("exit_code", ""),
                "message": message,
                "daemon_status": str(daemon_status.get("status", "") or "not_configured"),
                "daemon_pid": daemon_pid or "",
                "daemon_age_minutes": None if daemon_age_minutes is None else round(max(daemon_age_minutes, 0.0), 3),
            }
        ],
        columns=OBSERVATION_SUMMARY_COLUMNS,
    )

    no_evidence_ok = not required and not latest and not lock
    mode_ok = mode in {"", "observation_only"} and (not daemon_evidence or daemon_mode == "observation_only_daemon")
    completion_passed = task_running or completion_ok or no_evidence_ok
    freshness_passed = task_running or fresh or no_evidence_ok
    lock_passed = not lock or running
    rows = [
        {
            "check": "observation_mode_is_read_only",
            "passed": mode_ok,
            "value": mode or "not_run",
            "threshold": "observation_only",
            "severity": "critical" if not mode_ok else "info",
            "message": "The scheduled workflow must only collect data and observe candidates; it must not submit orders.",
        },
        {
            "check": "observation_last_run_completed",
            "passed": completion_passed,
            "value": summary_status,
            "threshold": "completed_or_running" if required else "completed_running_or_optional_not_run",
            "severity": "warning" if not completion_passed else "info",
            "message": "A required or previously used observation task must finish with exit code 0.",
        },
        {
            "check": "observation_result_fresh",
            "passed": freshness_passed,
            "value": "not_run" if age_hours is None else round(max(age_hours, 0.0), 3),
            "threshold": f"<= {max_age_hours:g} hours",
            "severity": "warning" if not freshness_passed else "info",
            "message": "The most recent successful observation result must be recent enough for unattended monitoring.",
        },
        {
            "check": "observation_lock_healthy",
            "passed": lock_passed,
            "value": "absent" if not lock else ("running" if running else "stale_or_invalid"),
            "threshold": f"absent or live <= {max_runtime_hours:g} hours",
            "severity": "warning" if not lock_passed else "info",
            "message": "A leftover lock indicates an interrupted, excessive, or unverified observation process.",
        },
        {
            "check": "observation_daemon_ready",
            "passed": not daemon_evidence or daemon_ready,
            "value": "not_configured" if not daemon_evidence else (
                f"status={daemon_status.get('status', 'missing')};pid={daemon_pid};"
                f"alive={daemon_alive};child_pid={daemon_child_pid or ''};"
                f"child_alive={daemon_child_alive if daemon_claims_running else ''};"
                f"age_minutes={'' if daemon_age_minutes is None else round(max(daemon_age_minutes, 0.0), 3)}"
            ),
            "threshold": f"live observation_only_daemon heartbeat <= {daemon_heartbeat_limit:g} minutes",
            "severity": "warning" if daemon_evidence and not daemon_ready else "info",
            "message": "When the user-level daemon is installed, its process and heartbeat must both be current.",
        },
    ]
    return summary, pd.DataFrame(rows, columns=OBSERVATION_AUDIT_COLUMNS)


def write_observation_task_status(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "daily_observation_status.csv"
    try:
        status = pd.read_csv(status_path, encoding="utf-8-sig") if status_path.exists() else pd.DataFrame(columns=OBSERVATION_STATUS_COLUMNS)
    except (OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        status = pd.DataFrame(columns=OBSERVATION_STATUS_COLUMNS)
    summary, audit = build_observation_task_status(
        status,
        _load_lock(output_dir / "daily_observation.lock.json"),
        config,
        daemon_status=_load_lock(output_dir / "observation_daemon_status.json"),
        daemon_lock=_load_lock(output_dir / "observation_daemon.lock.json"),
    )
    summary_path = output_dir / "daily_observation_summary.csv"
    audit_path = output_dir / "daily_observation_audit.csv"
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    return {summary_path.name: summary_path, audit_path.name: audit_path}
