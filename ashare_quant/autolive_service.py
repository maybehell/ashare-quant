from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
import time
from typing import Any, Callable

import pandas as pd
from pandas.errors import EmptyDataError

from .autolive_cycle import run_auto_live_cycle
from .autolive_alerts import retry_auto_live_alerts, send_auto_live_incident_alert
from .config import project_path
from .io_utils import atomic_to_csv


AUTO_LIVE_SERVICE_SUMMARY_COLUMNS = [
    "service_run_id",
    "mode",
    "status",
    "cycle_count",
    "completed_cycle_count",
    "blocked_cycle_count",
    "failed_cycle_count",
    "consecutive_failures",
    "stop_requested",
    "started_at",
    "completed_at",
    "message",
]
AUTO_LIVE_SERVICE_HISTORY_COLUMNS = [
    "service_run_id",
    "service_cycle_no",
    "mode",
    "cycle_id",
    "cycle_status",
    "blocking_stage",
    "submitted_count",
    "queried_count",
    "fill_count",
    "started_at",
    "completed_at",
    "message",
]
AUTO_LIVE_SERVICE_HEARTBEAT_COLUMNS = [
    "service_run_id",
    "pid",
    "host",
    "mode",
    "status",
    "service_cycle_no",
    "last_cycle_id",
    "last_cycle_status",
    "heartbeat_at",
    "next_run_at",
    "message",
]
AUTO_LIVE_SERVICE_LOCK_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_SERVICE_STOP_AUDIT_COLUMNS = ["action", "passed", "value", "message", "generated_at"]
AUTO_LIVE_SERVICE_LOCK_FILE = "auto_live_service.lock"
AUTO_LIVE_SERVICE_STOP_FILE = "auto_live_service.stop"
AUTO_LIVE_SUCCESS_CYCLE_STATUSES = {
    "completed",
    "completed_and_synced",
    "audit_complete",
    "audit_complete_with_blockers",
}


def run_auto_live_service(
    config: dict[str, Any],
    *,
    submit: bool = False,
    confirm_account_sync: bool = False,
    loop: bool = False,
    interval_seconds: float | None = None,
    max_cycles: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = datetime.now,
) -> dict[str, Path]:
    if confirm_account_sync and not submit:
        raise RuntimeError("auto-live service blocked: account sync requires submit mode")

    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    service = _service_config(config)
    interval = float(interval_seconds if interval_seconds is not None else service["interval_seconds"])
    maximum = int(max_cycles if max_cycles is not None else service["max_cycles"])
    started = now_fn()
    service_run_id = started.strftime("%Y%m%d%H%M%S%f")
    mode = "submit_and_sync" if confirm_account_sync else ("submit" if submit else "audit")
    paths = _service_paths(output_dir)
    counts = {"cycle": 0, "completed": 0, "blocked": 0, "failed": 0, "consecutive": 0}
    lock_path: Path | None = None

    _ensure_service_artifacts(paths)
    write_auto_live_service_lock_audit(config, now=started)

    if interval <= 0:
        return _write_blocked_summary(paths, service_run_id, mode, "blocked_configuration", "interval_seconds 必须大于 0。", started)
    if maximum < 0:
        return _write_blocked_summary(paths, service_run_id, mode, "blocked_configuration", "max_cycles 不能小于 0。", started)
    if submit and not bool(service["enabled"]):
        return _write_blocked_summary(paths, service_run_id, mode, "blocked_configuration", "提交服务未启用：auto_live.service.enabled=false。", started)
    if submit and not bool(service["allow_submit"]):
        return _write_blocked_summary(paths, service_run_id, mode, "blocked_configuration", "提交服务未授权：auto_live.service.allow_submit=false。", started)
    if paths["auto_live_service.stop"].exists():
        return _write_blocked_summary(paths, service_run_id, mode, "stopped", "检测到自动实盘服务停止文件，本次未运行周期。", started, stop_requested=True)

    try:
        lock_path = _acquire_service_lock(paths["auto_live_service.lock"], service_run_id, mode, started)
    except FileExistsError:
        write_auto_live_service_lock_audit(config, now=started)
        return _write_blocked_summary(paths, service_run_id, mode, "blocked_service_lock", "已有自动实盘服务实例或遗留锁；未启动第二实例。", started)

    try:
        _write_heartbeat(paths, service_run_id, mode, "starting", 0, "", "", started, None, "服务已取得单实例锁。")
        while True:
            if paths["auto_live_service.stop"].exists():
                _write_summary(paths, service_run_id, mode, "stopped", counts, started, now_fn(), True, "检测到服务停止文件，循环已停止。")
                break
            cycle_no = counts["cycle"] + 1
            cycle_started = now_fn()
            _write_heartbeat(paths, service_run_id, mode, "running_cycle", cycle_no, "", "", cycle_started, None, "正在运行自动实盘周期。")
            try:
                retry_auto_live_alerts(config)
                cycle_paths = run_auto_live_cycle(
                    config,
                    submit=submit,
                    confirm_account_sync=confirm_account_sync,
                )
                cycle = _read_csv(cycle_paths.get("auto_live_cycle_summary.csv"))
                row = cycle.iloc[-1] if not cycle.empty else pd.Series(dtype=object)
                cycle_status = str(row.get("status", "missing"))
                cycle_id = str(row.get("cycle_id", ""))
                blocking_stage = str(row.get("blocking_stage", ""))
                message = str(row.get("message", cycle_status))
                counts["cycle"] += 1
                if cycle_status in AUTO_LIVE_SUCCESS_CYCLE_STATUSES:
                    counts["completed"] += 1
                    counts["consecutive"] = 0
                else:
                    counts["blocked"] += 1
                    counts["consecutive"] = counts["consecutive"] + 1 if submit else 0
                    if submit:
                        alert_error = _send_service_incident(
                            config,
                            event_type="service_cycle_blocked",
                            incident_key=f"{service_run_id}:{blocking_stage or cycle_status}",
                            severity="critical",
                            message=message,
                            context={
                                "service_run_id": service_run_id,
                                "cycle_no": cycle_no,
                                "mode": mode,
                                "cycle_id": cycle_id,
                                "blocking_stage": blocking_stage,
                            },
                        )
                        if alert_error:
                            message = f"{message}; {alert_error}"
                history_row = {
                    "service_run_id": service_run_id,
                    "service_cycle_no": cycle_no,
                    "mode": mode,
                    "cycle_id": cycle_id,
                    "cycle_status": cycle_status,
                    "blocking_stage": blocking_stage,
                    "submitted_count": _integer(row.get("submitted_count", 0)),
                    "queried_count": _integer(row.get("queried_count", 0)),
                    "fill_count": _integer(row.get("fill_count", 0)),
                    "started_at": str(row.get("started_at", cycle_started.isoformat(timespec="seconds"))),
                    "completed_at": str(row.get("completed_at", now_fn().isoformat(timespec="seconds"))),
                    "message": message,
                }
            except Exception as exc:
                counts["cycle"] += 1
                counts["failed"] += 1
                counts["consecutive"] += 1
                cycle_status = "exception"
                cycle_id = ""
                message = f"{type(exc).__name__}: {exc}"
                alert_error = _send_service_incident(
                    config,
                    event_type="service_cycle_exception",
                    incident_key=f"{service_run_id}:{type(exc).__name__}:{exc}",
                    severity="critical",
                    message=message,
                    context={"service_run_id": service_run_id, "cycle_no": cycle_no, "mode": mode},
                )
                if alert_error:
                    message = f"{message}; {alert_error}"
                history_row = {
                    "service_run_id": service_run_id,
                    "service_cycle_no": cycle_no,
                    "mode": mode,
                    "cycle_id": "",
                    "cycle_status": cycle_status,
                    "blocking_stage": "service_cycle_exception",
                    "submitted_count": 0,
                    "queried_count": 0,
                    "fill_count": 0,
                    "started_at": cycle_started.isoformat(timespec="seconds"),
                    "completed_at": now_fn().isoformat(timespec="seconds"),
                    "message": message,
                }
            _append_service_history(paths["auto_live_service_history.csv"], history_row)
            running_status = "running_with_blockers" if counts["blocked"] else ("running_with_failures" if counts["failed"] else "running")
            _write_summary(paths, service_run_id, mode, running_status, counts, started, now_fn(), False, message)

            if counts["consecutive"] >= int(service["max_consecutive_failures"]):
                fuse_message = f"Auto-live service stopped after {counts['consecutive']} consecutive failures."
                alert_error = _send_service_incident(
                    config,
                    event_type="service_failure_fuse",
                    incident_key=service_run_id,
                    severity="critical",
                    message=fuse_message,
                    context={"service_run_id": service_run_id, "cycle_no": cycle_no, "mode": mode},
                )
                if alert_error:
                    fuse_message = f"{fuse_message}; {alert_error}"
                _write_summary(paths, service_run_id, mode, "stopped_failure_limit", counts, started, now_fn(), False, fuse_message)
                _write_heartbeat(paths, service_run_id, mode, "stopped_failure_limit", cycle_no, cycle_id, cycle_status, now_fn(), None, message)
                break
            if not loop or (maximum > 0 and counts["cycle"] >= maximum):
                final_status = _service_terminal_status(loop, counts, cycle_status)
                _write_summary(paths, service_run_id, mode, final_status, counts, started, now_fn(), False, "服务运行已按配置完成。")
                _write_heartbeat(paths, service_run_id, mode, final_status, cycle_no, cycle_id, cycle_status, now_fn(), None, message)
                break

            delay = float(service["failure_backoff_seconds"]) if counts["consecutive"] else interval
            next_run = now_fn() + timedelta(seconds=delay)
            _write_heartbeat(paths, service_run_id, mode, "waiting", cycle_no, cycle_id, cycle_status, now_fn(), next_run, f"等待 {delay:g} 秒后运行下一周期。")
            sleep_fn(delay)
    except KeyboardInterrupt:
        _write_summary(paths, service_run_id, mode, "stopped_by_operator", counts, started, now_fn(), True, "服务收到人工中断。")
        _write_heartbeat(paths, service_run_id, mode, "stopped_by_operator", counts["cycle"], "", "", now_fn(), None, "服务收到人工中断。")
    finally:
        _release_service_lock(lock_path)
        write_auto_live_service_lock_audit(config, now=now_fn())
    return _public_service_paths(paths)


def _send_service_incident(
    config: dict[str, Any],
    *,
    event_type: str,
    incident_key: str,
    severity: str,
    message: str,
    context: dict[str, Any],
) -> str:
    try:
        send_auto_live_incident_alert(
            config,
            event_type=event_type,
            incident_key=incident_key,
            severity=severity,
            message=message,
            context=context,
        )
    except Exception as exc:
        return f"alert_outbox_error={type(exc).__name__}: {exc}"
    return ""


def _service_terminal_status(loop: bool, counts: dict[str, int], cycle_status: str) -> str:
    if cycle_status == "exception":
        return "stopped_cycle_exception" if not loop else "stopped_max_cycles_with_failure"
    if counts["consecutive"] > 0:
        return "stopped_cycle_blocked" if not loop else "stopped_max_cycles_with_blockers"
    if cycle_status not in AUTO_LIVE_SUCCESS_CYCLE_STATUSES:
        return "completed_once_with_blockers" if not loop else "completed_max_cycles_with_blockers"
    if counts["failed"] > 0 or counts["blocked"] > 0:
        return "completed_once_with_recovery" if not loop else "completed_max_cycles_with_recovery"
    return "completed_once" if not loop else "completed_max_cycles"

def activate_auto_live_service_stop(config: dict[str, Any], reason: str = "operator_stop") -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _service_paths(output_dir)
    payload = {"reason": str(reason), "created_at": datetime.now().isoformat(timespec="seconds"), "pid": os.getpid()}
    paths["auto_live_service.stop"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    row = {"action": "stop", "passed": True, "value": reason, "message": "服务停止文件已创建；运行中的服务会在下一周期前退出。", "generated_at": datetime.now().isoformat(timespec="seconds")}
    _append_stop_audit(paths["auto_live_service_stop_audit.csv"], row)
    return {"auto_live_service.stop": paths["auto_live_service.stop"], "auto_live_service_stop_audit.csv": paths["auto_live_service_stop_audit.csv"]}


def clear_auto_live_service_stop(config: dict[str, Any], *, confirm: bool = False) -> dict[str, Path]:
    if not confirm:
        raise RuntimeError("auto-live service resume blocked: --confirm is required")
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _service_paths(output_dir)
    existed = paths["auto_live_service.stop"].exists()
    if existed:
        paths["auto_live_service.stop"].unlink()
    row = {"action": "resume", "passed": True, "value": f"file_existed={existed}", "message": "服务停止文件已清除；交易门禁和紧急停机仍会重新检查。", "generated_at": datetime.now().isoformat(timespec="seconds")}
    _append_stop_audit(paths["auto_live_service_stop_audit.csv"], row)
    return {"auto_live_service_stop_audit.csv": paths["auto_live_service_stop_audit.csv"]}


def write_auto_live_service_lock_audit(config: dict[str, Any], now: datetime | None = None) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _service_paths(output_dir)
    _ensure_service_artifacts(paths)
    lock = paths["auto_live_service.lock"]
    heartbeat = _read_csv(paths["auto_live_service_heartbeat.csv"])
    current = now or datetime.now()
    stale_seconds = float(_service_config(config)["lock_stale_seconds"])
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, next_action: str, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "severity": "ok" if passed else "service_blocker", "next_action": "" if passed else next_action, "message": message})

    if not lock.exists():
        add("service_lock_clear", True, "clear", "no lock file", "", "当前没有自动实盘服务锁。")
        add("service_lock_metadata_valid", True, "not_required", "valid when lock exists", "", "无锁时无需校验元数据。")
        add("service_heartbeat_fresh", True, "not_required", f"age <= {stale_seconds:g}s when lock exists", "", "无锁时无需校验活动心跳。")
    else:
        payload = _read_json(lock)
        run_id = str(payload.get("service_run_id", ""))
        pid = _integer(payload.get("pid", 0))
        created = pd.to_datetime(payload.get("created_at", ""), errors="coerce")
        metadata_valid = bool(run_id and pid > 0 and not pd.isna(created))
        add("service_lock_clear", False, f"run_id={run_id or 'unknown'};pid={pid}", "no lock file", "核对心跳与进程；不要启动第二实例或直接删除锁。", "存在服务锁时禁止并发启动。")
        add("service_lock_metadata_valid", metadata_valid, "valid" if metadata_valid else "invalid", "run_id,pid,created_at", "保留锁并核对关联进程。", "锁元数据用于崩溃恢复。")
        heartbeat_time = pd.to_datetime(heartbeat.iloc[-1].get("heartbeat_at", ""), errors="coerce") if not heartbeat.empty else pd.NaT
        age = float((pd.Timestamp(current) - heartbeat_time).total_seconds()) if not pd.isna(heartbeat_time) else float("inf")
        fresh = age <= stale_seconds
        add("service_heartbeat_fresh", fresh, f"age_seconds={age:.1f}" if age != float("inf") else "missing", f"<= {stale_seconds:g}", "确认原进程已退出后再人工处理遗留锁。", "心跳过期仅表示需要人工恢复，不会自动删除锁。")
    atomic_to_csv(pd.DataFrame(rows, columns=AUTO_LIVE_SERVICE_LOCK_AUDIT_COLUMNS), paths["auto_live_service_lock_audit.csv"], index=False, encoding="utf-8-sig")
    return paths["auto_live_service_lock_audit.csv"]


def _service_config(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    service = auto.get("service", {}) if isinstance(auto.get("service", {}), dict) else {}
    return {
        "enabled": _bool(service.get("enabled", False)),
        "allow_submit": _bool(service.get("allow_submit", False)),
        "interval_seconds": max(1.0, _float(service.get("interval_seconds", 60), 60)),
        "max_cycles": max(0, _integer(service.get("max_cycles", 0))),
        "max_consecutive_failures": max(1, _integer(service.get("max_consecutive_failures", 3))),
        "failure_backoff_seconds": max(1.0, _float(service.get("failure_backoff_seconds", 300), 300)),
        "lock_stale_seconds": max(30.0, _float(service.get("lock_stale_seconds", 300), 300)),
    }


def _service_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "auto_live_service_summary.csv": output_dir / "auto_live_service_summary.csv",
        "auto_live_service_history.csv": output_dir / "auto_live_service_history.csv",
        "auto_live_service_heartbeat.csv": output_dir / "auto_live_service_heartbeat.csv",
        "auto_live_service_lock_audit.csv": output_dir / "auto_live_service_lock_audit.csv",
        "auto_live_service_stop_audit.csv": output_dir / "auto_live_service_stop_audit.csv",
        "auto_live_service.lock": output_dir / AUTO_LIVE_SERVICE_LOCK_FILE,
        "auto_live_service.stop": output_dir / AUTO_LIVE_SERVICE_STOP_FILE,
    }


def _ensure_service_artifacts(paths: dict[str, Path]) -> None:
    for name, columns in {
        "auto_live_service_summary.csv": AUTO_LIVE_SERVICE_SUMMARY_COLUMNS,
        "auto_live_service_history.csv": AUTO_LIVE_SERVICE_HISTORY_COLUMNS,
        "auto_live_service_heartbeat.csv": AUTO_LIVE_SERVICE_HEARTBEAT_COLUMNS,
        "auto_live_service_lock_audit.csv": AUTO_LIVE_SERVICE_LOCK_AUDIT_COLUMNS,
        "auto_live_service_stop_audit.csv": AUTO_LIVE_SERVICE_STOP_AUDIT_COLUMNS,
    }.items():
        if not paths[name].exists():
            atomic_to_csv(pd.DataFrame(columns=columns), paths[name], index=False, encoding="utf-8-sig")


def _acquire_service_lock(path: Path, service_run_id: str, mode: str, started: datetime) -> Path:
    payload = {"service_run_id": service_run_id, "mode": mode, "pid": os.getpid(), "host": socket.gethostname(), "created_at": started.isoformat(timespec="seconds")}
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def _release_service_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _write_heartbeat(paths: dict[str, Path], service_run_id: str, mode: str, status: str, cycle_no: int, cycle_id: str, cycle_status: str, now: datetime, next_run: datetime | None, message: str) -> None:
    frame = pd.DataFrame([{"service_run_id": service_run_id, "pid": os.getpid(), "host": socket.gethostname(), "mode": mode, "status": status, "service_cycle_no": cycle_no, "last_cycle_id": cycle_id, "last_cycle_status": cycle_status, "heartbeat_at": now.isoformat(timespec="seconds"), "next_run_at": next_run.isoformat(timespec="seconds") if next_run else "", "message": message}], columns=AUTO_LIVE_SERVICE_HEARTBEAT_COLUMNS)
    atomic_to_csv(frame, paths["auto_live_service_heartbeat.csv"], index=False, encoding="utf-8-sig")


def _write_summary(paths: dict[str, Path], service_run_id: str, mode: str, status: str, counts: dict[str, int], started: datetime, completed: datetime, stop_requested: bool, message: str) -> None:
    frame = pd.DataFrame([{"service_run_id": service_run_id, "mode": mode, "status": status, "cycle_count": counts["cycle"], "completed_cycle_count": counts["completed"], "blocked_cycle_count": counts["blocked"], "failed_cycle_count": counts["failed"], "consecutive_failures": counts["consecutive"], "stop_requested": bool(stop_requested), "started_at": started.isoformat(timespec="seconds"), "completed_at": completed.isoformat(timespec="seconds"), "message": message}], columns=AUTO_LIVE_SERVICE_SUMMARY_COLUMNS)
    atomic_to_csv(frame, paths["auto_live_service_summary.csv"], index=False, encoding="utf-8-sig")


def _write_blocked_summary(paths: dict[str, Path], service_run_id: str, mode: str, status: str, message: str, started: datetime, stop_requested: bool = False) -> dict[str, Path]:
    counts = {"cycle": 0, "completed": 0, "blocked": 0, "failed": 0, "consecutive": 0}
    _write_summary(paths, service_run_id, mode, status, counts, started, datetime.now(), stop_requested, message)
    _write_heartbeat(paths, service_run_id, mode, status, 0, "", "", datetime.now(), None, message)
    return _public_service_paths(paths)


def _public_service_paths(paths: dict[str, Path]) -> dict[str, Path]:
    return {name: path for name, path in paths.items() if name.endswith(".csv")}


def _append_service_history(path: Path, row: dict[str, object]) -> None:
    existing = _read_csv(path)
    for column in AUTO_LIVE_SERVICE_HISTORY_COLUMNS:
        if column not in existing.columns:
            existing[column] = ""
    updated = pd.concat([existing[AUTO_LIVE_SERVICE_HISTORY_COLUMNS], pd.DataFrame([row], columns=AUTO_LIVE_SERVICE_HISTORY_COLUMNS)], ignore_index=True)
    atomic_to_csv(updated, path, index=False, encoding="utf-8-sig")


def _append_stop_audit(path: Path, row: dict[str, object]) -> None:
    existing = _read_csv(path)
    for column in AUTO_LIVE_SERVICE_STOP_AUDIT_COLUMNS:
        if column not in existing.columns:
            existing[column] = ""
    updated = pd.concat([existing[AUTO_LIVE_SERVICE_STOP_AUDIT_COLUMNS], pd.DataFrame([row], columns=AUTO_LIVE_SERVICE_STOP_AUDIT_COLUMNS)], ignore_index=True)
    atomic_to_csv(updated, path, index=False, encoding="utf-8-sig")


def _read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (EmptyDataError, OSError):
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float(value: object, default: float) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(parsed) if not pd.isna(parsed) else float(default)


def _integer(value: object) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return int(parsed) if not pd.isna(parsed) else 0
