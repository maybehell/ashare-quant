from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pandas as pd

from .autolive import (
    AUTO_LIVE_CANCEL_AUDIT_COLUMNS,
    AUTO_LIVE_CANCEL_RESULT_COLUMNS,
    AUTO_LIVE_ORDER_STATUS_COLUMNS,
    CANCEL_ORDER_STATUSES,
    CANCEL_ORDER_SUCCESS_STATUSES,
    CANCELABLE_BROKER_ORDER_STATUSES,
    COMMAND_ADAPTER_SCHEMA,
    all_passed,
    auto_live_config_sha256,
    auto_live_query_adapter_ready,
    build_command_adapter_invocation,
    command_result_integrity_status,
    dataframe_records,
    parse_bool_like,
    read_csv,
    truncate_text,
)
from .autolive_integrity import attach_request_integrity, verify_adapter_request_integrity
from .autolive_alerts import send_auto_live_incident_alert
from .autolive_journal import record_auto_live_event
from .config import project_path
from .io_utils import atomic_to_csv


AUTO_LIVE_CANCEL_VERIFICATION_COLUMNS = [
    "cancel_run_id",
    "source_query_run_id",
    "verification_query_run_id",
    "source_run_id",
    "ticket_id",
    "broker_order_id",
    "cancel_status",
    "broker_status",
    "terminal",
    "outcome",
    "message",
]
CANCEL_VERIFICATION_TERMINAL_STATUSES = {"canceled", "filled"}


def cancel_auto_live_orders(config: dict[str, Any], raise_on_blocked: bool = True) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    statuses = read_csv(output_dir / "auto_live_order_status.csv")
    status_audit = read_csv(output_dir / "auto_live_order_status_audit.csv")
    cancel_run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    adapter = str(config.get("auto_live", {}).get("broker_adapter", "") or "").strip().lower()
    result_path = output_dir / "auto_live_cancel_results.csv"
    audit_path = output_dir / "auto_live_cancel_audit.csv"
    adapter_ready, adapter_value = auto_live_query_adapter_ready(config)
    source_ready, source_value = cancel_source_preflight(
        statuses,
        status_audit,
        output_dir=output_dir if adapter == "command" else None,
    )

    cancelable = cancelable_order_statuses(statuses)
    adapter_invoked = bool(
        adapter_ready
        and source_ready
        and not cancelable.empty
        and adapter in {"mock", "command"}
    )
    lock_path: Path | None = None
    if adapter_invoked:
        source_query_run_id = str(cancelable["query_run_id"].iloc[0])
        try:
            lock_path = acquire_auto_live_cancel_lock(output_dir, cancel_run_id, source_query_run_id)
        except FileExistsError:
            adapter_invoked = False
            source_ready = False
            source_value = "cancel_lock_active"
    try:
        if not adapter_invoked:
            results = pd.DataFrame(columns=AUTO_LIVE_CANCEL_RESULT_COLUMNS)
        elif adapter == "mock":
            results = cancel_with_mock_adapter(statuses, cancel_run_id)
        else:
            results = cancel_with_command_adapter(config, statuses, cancel_run_id)
    finally:
        release_auto_live_cancel_lock(lock_path)

    audit = build_auto_live_cancel_audit(
        statuses,
        status_audit,
        results,
        cancel_run_id,
        adapter_ready,
        adapter_value,
        source_ready=source_ready,
        source_value=source_value,
    )
    run_result_path: Path | None = None
    if adapter_invoked:
        run_result_path = output_dir / f"auto_live_cancel_results_{cancel_run_id}.csv"
        atomic_to_csv(results, run_result_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(results, result_path, index=False, encoding="utf-8-sig")
    elif not result_path.exists():
        atomic_to_csv(results, result_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    alert_paths: dict[str, Path] = {}
    if adapter_invoked and not all_passed(audit):
        failed = ";".join(audit.loc[~audit["passed"].map(parse_bool_like), "check"].astype(str).tolist())
        alert_paths = send_auto_live_incident_alert(
            config,
            event_type="cancel_request_failed",
            incident_key=cancel_run_id,
            severity="critical",
            message=f"Broker cancellation request is not safely acknowledged: {failed or 'cancel_audit_failed'}.",
            context={"cancel_run_id": cancel_run_id, "failed_checks": failed},
        )
    journal_paths = record_auto_live_event(
        config,
        "order_cancel",
        cancel_run_id,
        {
            "adapter": adapter,
            "source_rows": len(statuses),
            "result_rows": len(results),
            "audit_passed": all_passed(audit),
            "cancel_statuses": results["cancel_status"].astype(str).value_counts().to_dict() if not results.empty and "cancel_status" in results.columns else {},
        },
        strict=False,
    )
    if raise_on_blocked and not all_passed(audit):
        failed = ";".join(audit.loc[~audit["passed"].map(parse_bool_like), "check"].astype(str).tolist())
        raise RuntimeError(f"auto-live cancel blocked: {failed or 'cancel_audit_failed'}")
    paths = {"auto_live_cancel_results.csv": result_path, "auto_live_cancel_audit.csv": audit_path, **journal_paths, **alert_paths}
    if run_result_path is not None:
        paths[run_result_path.name] = run_result_path
    return paths


def acquire_auto_live_cancel_lock(output_dir: Path, cancel_run_id: str, source_query_run_id: str) -> Path:
    path = output_dir / "auto_live_cancel.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        payload = {
            "cancel_run_id": str(cancel_run_id),
            "source_query_run_id": str(source_query_run_id),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "pid": os.getpid(),
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def release_auto_live_cancel_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def write_auto_live_cancel_lock_audit(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_cancel_lock_audit.csv"
    atomic_to_csv(build_auto_live_cancel_lock_audit(output_dir), path, index=False, encoding="utf-8-sig")
    return path


def build_auto_live_cancel_lock_audit(output_dir: Path) -> pd.DataFrame:
    lock_path = output_dir / "auto_live_cancel.lock"
    lock_exists = lock_path.exists()
    lock_payload = _read_json_mapping(lock_path) if lock_exists else {}
    cancel_run_id = str(lock_payload.get("cancel_run_id", "") or "").strip()
    source_query_run_id = str(lock_payload.get("source_query_run_id", "") or "").strip()
    payload_valid = bool(
        not lock_exists
        or (
            len(cancel_run_id) == 20
            and cancel_run_id.isdigit()
            and len(source_query_run_id) == 20
            and source_query_run_id.isdigit()
            and str(lock_payload.get("created_at", "") or "").strip()
        )
    )
    statuses = read_csv(output_dir / "auto_live_order_status.csv")
    status_audit = read_csv(output_dir / "auto_live_order_status_audit.csv")
    status_audit_ok = all_passed(status_audit)
    query_ids = (
        statuses["query_run_id"].astype(str).str.strip().unique().tolist()
        if not statuses.empty and "query_run_id" in statuses.columns
        else []
    )
    current_query_run_id = query_ids[0] if len(query_ids) == 1 else ""
    fresh_query = bool(
        not lock_exists
        or (
            payload_valid
            and len(current_query_run_id) == 20
            and current_query_run_id.isdigit()
            and current_query_run_id > cancel_run_id
        )
    )
    request_path = output_dir / f"auto_live_cancel_request_{cancel_run_id}.json"
    request = _read_json_mapping(request_path) if cancel_run_id else {}
    request_exists = bool(
        request
        and str(request.get("mode", "")) == "cancel_orders"
        and str(request.get("run_id", "")) == cancel_run_id
    )
    cancelable_count = len(cancelable_order_statuses(statuses))
    request_state_safe = bool(not lock_exists or not request_exists or cancelable_count == 0)
    recovery_ready = bool(
        not lock_exists
        or (
            payload_valid
            and status_audit_ok
            and fresh_query
            and request_state_safe
        )
    )
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else "cancel_blocker",
                "next_action": "" if passed else "重新查询券商委托；确认遗留锁对应请求未发出，或相关订单均已进入 canceled/filled 终态。",
                "message": message,
            }
        )

    add("cancel_lock_clear", not lock_exists, "clear" if not lock_exists else cancel_run_id or "invalid", "lock absent", "撤单适配器调用采用单实例原子锁。")
    add("cancel_lock_payload_valid", payload_valid, cancel_run_id or "no_lock", "valid run and source query IDs", "遗留锁必须含有可追溯的撤单批次身份。")
    add("cancel_lock_recovery_query_audited", not lock_exists or status_audit_ok, status_audit_ok, True, "崩溃后清锁必须先完成通过审计的券商查询。")
    add("cancel_lock_recovery_query_fresh", fresh_query, f"lock={cancel_run_id};query={current_query_run_id}", "query_run_id newer than cancel_run_id", "不能使用加锁前的旧委托状态清除遗留锁。")
    add("cancel_lock_request_state_safe", request_state_safe, f"request_exists={request_exists};cancelable={cancelable_count}", "no request persisted, or no cancelable orders remain", "若原始撤单请求已落盘，则必须确认相关在途委托已消失。")
    add("cancel_lock_recovery_ready", recovery_ready, recovery_ready, True, "只有完整性、最新查询和请求状态同时满足时才允许显式清锁。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_CANCEL_AUDIT_COLUMNS)


def clear_auto_live_cancel_lock(config: dict[str, Any], *, confirm: bool = False) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    audit_path = output_dir / "auto_live_cancel_lock_audit.csv"
    audit = build_auto_live_cancel_lock_audit(output_dir)
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    lock_path = output_dir / "auto_live_cancel.lock"
    if not lock_path.exists():
        return audit_path
    recovery = audit.loc[audit["check"] == "cancel_lock_recovery_ready", "passed"]
    recovery_ready = bool(not recovery.empty and parse_bool_like(recovery.iloc[-1]))
    if not confirm:
        raise RuntimeError(f"auto-live cancel lock clear blocked: explicit --confirm required; audit={audit_path}")
    if not recovery_ready:
        raise RuntimeError(f"auto-live cancel lock clear blocked: recovery audit failed; audit={audit_path}")
    lock_path.unlink()
    atomic_to_csv(build_auto_live_cancel_lock_audit(output_dir), audit_path, index=False, encoding="utf-8-sig")
    return audit_path


def cancel_verification_needed(output_dir: Path) -> bool:
    results = read_csv(output_dir / "auto_live_cancel_results.csv")
    if results.empty or "cancel_run_id" not in results.columns:
        return False
    result_ids = results["cancel_run_id"].astype(str).str.strip().unique().tolist()
    if len(result_ids) != 1 or not result_ids[0]:
        return True
    verification = read_csv(output_dir / "auto_live_cancel_verification.csv")
    audit = read_csv(output_dir / "auto_live_cancel_verification_audit.csv")
    if verification.empty or "cancel_run_id" not in verification.columns or not all_passed(audit):
        return True
    verified_ids = verification["cancel_run_id"].astype(str).str.strip().unique().tolist()
    return verified_ids != result_ids


def verify_auto_live_cancellations(
    config: dict[str, Any],
    raise_on_blocked: bool = True,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    results = read_csv(output_dir / "auto_live_cancel_results.csv")
    statuses = read_csv(output_dir / "auto_live_order_status.csv")
    status_audit = read_csv(output_dir / "auto_live_order_status_audit.csv")
    verification_path = output_dir / "auto_live_cancel_verification.csv"
    audit_path = output_dir / "auto_live_cancel_verification_audit.csv"

    cancel_run_ids = (
        results["cancel_run_id"].astype(str).str.strip().unique().tolist()
        if not results.empty and "cancel_run_id" in results.columns
        else []
    )
    cancel_run_id = cancel_run_ids[0] if len(cancel_run_ids) == 1 else ""
    request_path = output_dir / f"auto_live_cancel_request_{cancel_run_id}.json"
    request = _read_json_mapping(request_path)
    request_identity_ok = bool(
        cancel_run_id
        and str(request.get("mode", "")) == "cancel_orders"
        and str(request.get("run_id", "")) == cancel_run_id
    )
    request_integrity_ok, request_integrity_value = (
        verify_adapter_request_integrity(request)
        if request_identity_ok
        else (False, "cancel_request_missing_or_identity_mismatch")
    )
    source_query_run_id = str(request.get("source_query_run_id", "") or "").strip()
    request_orders = request.get("orders", []) if isinstance(request.get("orders", []), list) else []
    request_pairs = {
        (str(row.get("ticket_id", "") or "").strip(), str(row.get("broker_order_id", "") or "").strip())
        for row in request_orders
        if isinstance(row, dict)
    }
    result_pairs = {
        (str(row.ticket_id).strip(), str(row.broker_order_id).strip())
        for row in results.itertuples(index=False)
    } if {"ticket_id", "broker_order_id"}.issubset(results.columns) else set()
    request_results_bound = bool(request_pairs) and request_pairs == result_pairs and len(request_pairs) == len(results)

    query_run_ids = (
        statuses["query_run_id"].astype(str).str.strip().unique().tolist()
        if not statuses.empty and "query_run_id" in statuses.columns
        else []
    )
    verification_query_run_id = query_run_ids[0] if len(query_run_ids) == 1 else ""
    fresh_query = bool(
        source_query_run_id
        and verification_query_run_id
        and verification_query_run_id != source_query_run_id
    )
    status_audit_ok = all_passed(status_audit)

    status_by_pair: dict[tuple[str, str], Any] = {}
    duplicate_status_pairs = False
    if {"ticket_id", "broker_order_id"}.issubset(statuses.columns):
        for row in statuses.itertuples(index=False):
            pair = (str(row.ticket_id).strip(), str(row.broker_order_id).strip())
            if pair in status_by_pair:
                duplicate_status_pairs = True
            status_by_pair[pair] = row

    rows: list[dict[str, object]] = []
    for result in results.itertuples(index=False):
        pair = (str(getattr(result, "ticket_id", "")).strip(), str(getattr(result, "broker_order_id", "")).strip())
        status_row = status_by_pair.get(pair)
        broker_status = str(getattr(status_row, "broker_status", "unknown") or "unknown").lower()
        terminal = status_row is not None and broker_status in CANCEL_VERIFICATION_TERMINAL_STATUSES
        outcome = "canceled" if broker_status == "canceled" else ("filled_before_cancel" if broker_status == "filled" else "pending_confirmation")
        rows.append(
            {
                "cancel_run_id": cancel_run_id,
                "source_query_run_id": source_query_run_id,
                "verification_query_run_id": verification_query_run_id,
                "source_run_id": getattr(result, "source_run_id", ""),
                "ticket_id": pair[0],
                "broker_order_id": pair[1],
                "cancel_status": getattr(result, "cancel_status", ""),
                "broker_status": broker_status,
                "terminal": bool(terminal),
                "outcome": outcome,
                "message": "Broker query confirmed a terminal order state." if terminal else "A fresh broker query has not confirmed canceled or filled status.",
            }
        )
    verification = pd.DataFrame(rows, columns=AUTO_LIVE_CANCEL_VERIFICATION_COLUMNS)
    audit = build_auto_live_cancel_verification_audit(
        results,
        verification,
        cancel_run_ids=cancel_run_ids,
        request_identity_ok=request_identity_ok,
        request_integrity_ok=request_integrity_ok,
        request_integrity_value=request_integrity_value,
        request_results_bound=request_results_bound,
        status_audit_ok=status_audit_ok,
        fresh_query=fresh_query,
        source_query_run_id=source_query_run_id,
        verification_query_run_id=verification_query_run_id,
        duplicate_status_pairs=duplicate_status_pairs,
    )
    atomic_to_csv(verification, verification_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    alert_paths: dict[str, Path] = {}
    if not all_passed(audit):
        failed = ";".join(audit.loc[~audit["passed"].map(parse_bool_like), "check"].astype(str).tolist())
        alert_paths = send_auto_live_incident_alert(
            config,
            event_type="cancel_verification_pending",
            incident_key=cancel_run_id or "missing_cancel_run_id",
            severity="critical",
            message=f"Broker cancellation has not reached a verified terminal state: {failed or 'cancel_verification_failed'}.",
            context={
                "cancel_run_id": cancel_run_id,
                "source_query_run_id": source_query_run_id,
                "verification_query_run_id": verification_query_run_id,
                "failed_checks": failed,
            },
        )
    if raise_on_blocked and not all_passed(audit):
        failed = ";".join(audit.loc[~audit["passed"].map(parse_bool_like), "check"].astype(str).tolist())
        raise RuntimeError(f"auto-live cancel verification blocked: {failed or 'cancel_verification_failed'}")
    return {
        verification_path.name: verification_path,
        audit_path.name: audit_path,
        **alert_paths,
    }


def build_auto_live_cancel_verification_audit(
    results: pd.DataFrame,
    verification: pd.DataFrame,
    *,
    cancel_run_ids: list[str],
    request_identity_ok: bool,
    request_integrity_ok: bool,
    request_integrity_value: str,
    request_results_bound: bool,
    status_audit_ok: bool,
    fresh_query: bool,
    source_query_run_id: str,
    verification_query_run_id: str,
    duplicate_status_pairs: bool,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else "cancel_blocker",
                "next_action": "" if passed else "重新查询券商委托状态，并确认所有撤单对象已进入 canceled 或 filled 终态。",
                "message": message,
            }
        )

    add("cancel_results_present", not results.empty, len(results), ">0 rows", "撤单终态验证必须绑定已保存的撤单回报。")
    add("cancel_result_run_id_unique", len(cancel_run_ids) == 1 and bool(cancel_run_ids[0]), ",".join(cancel_run_ids) or "missing", "one cancel_run_id", "撤单回报必须来自单一撤单批次。")
    add("cancel_request_identity_valid", request_identity_ok, request_integrity_value, "matching cancel request", "撤单回报必须能追溯到原始 cancel_orders 请求。")
    add("cancel_request_integrity_valid", request_integrity_ok, request_integrity_value, "valid request integrity", "原始撤单请求必须通过完整性校验。")
    add("cancel_results_bound_to_request", request_results_bound, f"results={len(results)}", "same ticket_id and broker_order_id pairs", "撤单回报必须逐笔绑定原始请求。")
    add("cancel_verification_status_audit_passed", status_audit_ok, status_audit_ok, True, "终态确认必须基于通过审计的最新券商查询。")
    add("cancel_verification_query_fresh", fresh_query, f"source={source_query_run_id};verification={verification_query_run_id}", "different non-empty query_run_id", "撤单后必须重新查询，不能复用发起撤单前的状态。")
    add("cancel_verification_status_pairs_unique", not duplicate_status_pairs, duplicate_status_pairs, False, "券商状态中的订单身份必须唯一。")
    matched = int(verification["broker_status"].astype(str).ne("unknown").sum()) if not verification.empty else 0
    add("cancel_verification_rows_match", len(verification) == len(results) and matched == len(results), f"matched={matched};expected={len(results)}", "one matched status per cancel result", "每笔撤单回报都必须在最新券商状态中找到。")
    terminal = bool(not verification.empty and verification["terminal"].map(parse_bool_like).all())
    states = ",".join(sorted(verification["broker_status"].astype(str).unique())) if not verification.empty else "missing"
    add("cancel_verification_terminal", terminal, states, "canceled or filled", "cancel_requested 仅表示请求已受理；只有最新查询显示 canceled 或 filled 才算闭环。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_CANCEL_AUDIT_COLUMNS)


def _read_json_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def cancelable_order_statuses(statuses: pd.DataFrame) -> pd.DataFrame:
    if statuses.empty:
        return pd.DataFrame(columns=AUTO_LIVE_ORDER_STATUS_COLUMNS)
    frame = statuses.copy()
    for column in AUTO_LIVE_ORDER_STATUS_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    broker_status = frame["broker_status"].astype(str).str.lower()
    broker_ids = frame["broker_order_id"].astype(str).str.strip()
    return frame[broker_status.isin(CANCELABLE_BROKER_ORDER_STATUSES) & (broker_ids != "")][AUTO_LIVE_ORDER_STATUS_COLUMNS].copy()


def cancel_source_preflight(
    statuses: pd.DataFrame,
    status_audit: pd.DataFrame,
    *,
    output_dir: Path | None = None,
) -> tuple[bool, str]:
    if statuses.empty:
        return False, "order_status_missing"
    if status_audit.empty or "check" not in status_audit.columns or "passed" not in status_audit.columns:
        return False, "order_status_audit_missing_or_invalid"
    required_checks = {
        "query_source_integrity",
        "query_rows_match_submitted_orders",
        "query_ticket_ids_match_submitted",
        "query_broker_order_ids_match_submitted",
        "query_run_id_match",
        "broker_status_values_valid",
        "broker_statuses_actionable",
        "filled_quantity_within_order_quantity",
        "fill_money_fields_non_negative",
    }
    if output_dir is not None:
        required_checks.add("order_status_file_sha256")
    audit_by_check = {
        str(row.check): parse_bool_like(row.passed)
        for row in status_audit[["check", "passed"]].itertuples(index=False)
    }
    missing = sorted(required_checks - set(audit_by_check))
    if missing:
        return False, "order_status_audit_required_checks_missing:" + ",".join(missing)
    failed = sorted(check for check in required_checks if not audit_by_check.get(check, False))
    if failed or not bool(status_audit["passed"].map(parse_bool_like).all()):
        return False, "order_status_audit_failed:" + ",".join(failed or ["other_check"])
    if output_dir is not None:
        status_path = output_dir / "auto_live_order_status.csv"
        hash_rows = status_audit.loc[status_audit["check"].astype(str) == "order_status_file_sha256", "value"]
        expected_hash = str(hash_rows.iloc[-1] if not hash_rows.empty else "").strip()
        try:
            current_hash = hashlib.sha256(status_path.read_bytes()).hexdigest()
        except OSError:
            return False, "order_status_file_missing"
        if not expected_hash or current_hash != expected_hash:
            return False, "order_status_file_sha256_mismatch"

    source = cancelable_order_statuses(statuses)
    if source.empty:
        return True, "no_cancelable_orders"
    ticket_ids = source["ticket_id"].astype(str).str.strip().tolist()
    broker_ids = source["broker_order_id"].astype(str).str.strip().tolist()
    if any(not value for value in ticket_ids):
        return False, "cancel_source_ticket_id_missing"
    if any(not value for value in broker_ids):
        return False, "cancel_source_broker_order_id_missing"
    if len(ticket_ids) != len(set(ticket_ids)):
        return False, "cancel_source_ticket_id_duplicate"
    if len(broker_ids) != len(set(broker_ids)):
        return False, "cancel_source_broker_order_id_duplicate"

    query_ids = source["query_run_id"].astype(str).str.strip().unique().tolist()
    source_ids = source["source_run_id"].astype(str).str.strip().unique().tolist()
    if len(query_ids) != 1 or not query_ids[0]:
        return False, "cancel_source_query_run_id_ambiguous"
    if len(source_ids) != 1 or not source_ids[0]:
        return False, "cancel_source_run_id_ambiguous"
    if output_dir is not None and prior_cancel_request_exists(output_dir, query_ids[0]):
        return False, f"cancel_source_query_already_used:{query_ids[0]}"
    return True, "cancel_source_identity_verified"


def prior_cancel_request_exists(output_dir: Path, source_query_run_id: str) -> bool:
    for path in output_dir.glob("auto_live_cancel_request_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and str(payload.get("mode", "")) == "cancel_orders"
            and str(payload.get("source_query_run_id", "")) == str(source_query_run_id)
        ):
            return True
    return False


def cancel_with_mock_adapter(statuses: pd.DataFrame, cancel_run_id: str) -> pd.DataFrame:
    rows = [cancel_result_row(row, cancel_run_id, "canceled", "Mock adapter marked the order canceled.") for row in cancelable_order_statuses(statuses).itertuples(index=False)]
    return pd.DataFrame(rows, columns=AUTO_LIVE_CANCEL_RESULT_COLUMNS)


def cancel_with_command_adapter(config: dict[str, Any], statuses: pd.DataFrame, cancel_run_id: str) -> pd.DataFrame:
    source = cancelable_order_statuses(statuses)
    if source.empty:
        return pd.DataFrame(columns=AUTO_LIVE_CANCEL_RESULT_COLUMNS)
    output_dir = project_path(config["paths"]["output_dir"])
    request_path = output_dir / f"auto_live_cancel_request_{cancel_run_id}.json"
    result_path = output_dir / f"auto_live_cancel_result_{cancel_run_id}.json"
    write_command_adapter_cancel_request(config, source, cancel_run_id, request_path, result_path)
    argv = build_command_adapter_invocation(config, request_path, result_path, cancel_run_id)
    adapter_cfg = config.get("auto_live", {}).get("command_adapter", {})
    timeout = float(adapter_cfg.get("timeout_seconds", 30))
    cwd_value = adapter_cfg.get("working_dir", "")
    cwd = project_path(cwd_value) if cwd_value else None
    try:
        completed = subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        return command_cancel_error_results(source, cancel_run_id, "failed", f"Command adapter cancel timed out after {timeout:g}s.")
    except OSError as exc:
        return command_cancel_error_results(source, cancel_run_id, "failed", f"Command adapter cancel failed to start: {exc}")
    if completed.returncode != 0:
        message = truncate_text((completed.stderr or completed.stdout or "").strip(), 300)
        return command_cancel_error_results(source, cancel_run_id, "failed", f"Command adapter cancel exited {completed.returncode}: {message}")
    if not result_path.exists():
        message = truncate_text((completed.stdout or "").strip(), 300)
        return command_cancel_error_results(source, cancel_run_id, "unknown", f"Command adapter cancel did not write result file. {message}")
    integrity_passed, integrity_value = command_result_integrity_status(config, request_path, result_path)
    if not integrity_passed:
        return command_cancel_error_results(source, cancel_run_id, "unknown", f"Command adapter cancel result integrity failed: {integrity_value}")
    return normalize_command_adapter_cancel_results(source, cancel_run_id, result_path)


def write_command_adapter_cancel_request(config: dict[str, Any], source: pd.DataFrame, cancel_run_id: str, request_path: Path, result_path: Path) -> None:
    source_run_id = str(source["source_run_id"].iloc[0]) if not source.empty and "source_run_id" in source.columns else ""
    source_query_run_id = str(source["query_run_id"].iloc[0]) if not source.empty and "query_run_id" in source.columns else ""
    payload = {
        "schema_version": COMMAND_ADAPTER_SCHEMA,
        "mode": "cancel_orders",
        "run_id": cancel_run_id,
        "source_run_id": source_run_id,
        "source_query_run_id": source_query_run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result_path": str(result_path),
        "auto_live_config_sha256": auto_live_config_sha256(config),
        "orders": dataframe_records(source),
    }
    payload = attach_request_integrity(config, payload)
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_command_adapter_cancel_results(source: pd.DataFrame, cancel_run_id: str, result_path: Path) -> pd.DataFrame:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return command_cancel_error_results(source, cancel_run_id, "unknown", f"Command adapter cancel result is not valid JSON: {exc}")
    raw_results = payload.get("results", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_results, list):
        return command_cancel_error_results(source, cancel_run_id, "unknown", "Command adapter cancel result must be a list or {'results': [...]}.")
    if len(raw_results) != len(source) or not all(isinstance(item, dict) for item in raw_results):
        return command_cancel_error_results(
            source,
            cancel_run_id,
            "unknown",
            "Command adapter cancel result row count or row type does not match the cancel request.",
        )
    result_pairs = [
        (str(item.get("ticket_id", "") or "").strip(), str(item.get("broker_order_id", "") or "").strip())
        for item in raw_results
    ]
    ticket_ids = [pair[0] for pair in result_pairs]
    broker_ids = [pair[1] for pair in result_pairs]
    if (
        any(not value for value in ticket_ids + broker_ids)
        or len(ticket_ids) != len(set(ticket_ids))
        or len(broker_ids) != len(set(broker_ids))
    ):
        return command_cancel_error_results(
            source,
            cancel_run_id,
            "unknown",
            "Command adapter cancel result contains missing or duplicate identifiers.",
        )
    source_pairs = {
        (str(row.ticket_id).strip(), str(row.broker_order_id).strip())
        for row in source.itertuples(index=False)
    }
    if set(result_pairs) != source_pairs:
        return command_cancel_error_results(
            source,
            cancel_run_id,
            "unknown",
            "Command adapter cancel result identities do not match the requested ticket_id and broker_order_id pairs.",
        )
    by_pair = {pair: item for pair, item in zip(result_pairs, raw_results)}
    rows = []
    for source_row in source.itertuples(index=False):
        ticket_id = str(getattr(source_row, "ticket_id", "")).strip()
        broker_order_id = str(getattr(source_row, "broker_order_id", "")).strip()
        item = by_pair[(ticket_id, broker_order_id)]
        cancel_status = str(item.get("cancel_status", item.get("status", "unknown")) or "unknown").lower()
        if cancel_status not in CANCEL_ORDER_STATUSES:
            cancel_status = "unknown"
        message = "" if item.get("message") is None else str(item.get("message"))
        rows.append(cancel_result_row(source_row, cancel_run_id, cancel_status, message, item.get("broker_order_id")))
    return pd.DataFrame(rows, columns=AUTO_LIVE_CANCEL_RESULT_COLUMNS)


def command_cancel_error_results(source: pd.DataFrame, cancel_run_id: str, status: str, message: str) -> pd.DataFrame:
    rows = [cancel_result_row(row, cancel_run_id, status, message) for row in source.itertuples(index=False)]
    return pd.DataFrame(rows, columns=AUTO_LIVE_CANCEL_RESULT_COLUMNS)


def cancel_result_row(status_row: Any, cancel_run_id: str, cancel_status: str, message: str, broker_order_id: object | None = None) -> dict[str, object]:
    resolved_broker_order_id = getattr(status_row, "broker_order_id", "") if broker_order_id is None else broker_order_id
    return {
        "cancel_run_id": cancel_run_id,
        "source_run_id": getattr(status_row, "source_run_id", ""),
        "ticket_id": getattr(status_row, "ticket_id", ""),
        "symbol": getattr(status_row, "symbol", ""),
        "side": getattr(status_row, "side", ""),
        "quantity": getattr(status_row, "quantity", ""),
        "broker_order_id": resolved_broker_order_id,
        "cancel_status": cancel_status,
        "message": message,
    }


def build_auto_live_cancel_audit(
    statuses: pd.DataFrame,
    status_audit: pd.DataFrame,
    results: pd.DataFrame,
    cancel_run_id: str,
    adapter_ready: bool,
    adapter_value: str,
    *,
    source_ready: bool = True,
    source_value: str = "cancel_source_preflight_not_evaluated",
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "severity": "ok" if passed else severity, "next_action": "" if passed else next_action, "message": message})

    cancelable = cancelable_order_statuses(statuses)
    status_audit_passed = not status_audit.empty and "passed" in status_audit.columns and bool(status_audit["passed"].map(parse_bool_like).all())
    status_audit_value = "missing" if status_audit.empty else f"{int(status_audit['passed'].map(parse_bool_like).sum())}/{len(status_audit)}"
    add("cancel_adapter_ready", adapter_ready, adapter_value, "adapter cancel ready", "cancel_blocker", "先通过 command self-test 并确认 cancel_orders 能力。", "撤单必须使用已审计且具备撤单能力的适配器。")
    add("cancel_source_preflight", source_ready, source_value, "audited unique source identity and unused query batch", "cancel_blocker", "重新查询券商委托状态；不要重复使用已发起撤单的查询批次。", "调用撤单适配器前必须验证状态审计、唯一订单身份和查询批次幂等性。")
    add("order_status_present", not statuses.empty, len(statuses), ">0 rows", "cancel_blocker", "先运行 auto-live-query 查询券商委托状态。", "撤单必须基于最新券商委托状态。")
    add("order_status_audit_passed", status_audit_passed, status_audit_value, "all order status checks passed", "cancel_blocker", "先修复 auto_live_order_status_audit.csv 中的阻断项。", "委托状态审计通过后才允许撤销仍在途委托。")
    add("cancelable_orders_identified", True, len(cancelable), ">=0 cancelable rows", "ok", "", "系统只会撤销 accepted/submitted/partially_filled 且有券商委托号的订单。")
    add("cancel_rows_match_cancelable_orders", len(results) == len(cancelable), f"cancel_rows={len(results)};cancelable={len(cancelable)}", "one result per cancelable order", "cancel_blocker", "检查适配器 cancel_orders 是否漏回或多回。", "每个可撤委托都必须有一条撤单回报。")

    expected_tickets = set(cancelable["ticket_id"].astype(str)) if "ticket_id" in cancelable.columns else set()
    result_tickets = set(results["ticket_id"].astype(str)) if "ticket_id" in results.columns else set()
    add("cancel_ticket_ids_match", result_tickets == expected_tickets, f"matched={len(result_tickets & expected_tickets)};expected={len(expected_tickets)}", "same ticket_id set", "cancel_blocker", "检查 cancel_orders 返回 ticket_id。", "撤单回报必须能映射到原始委托。")

    expected_broker_ids = set(cancelable["broker_order_id"].astype(str).str.strip()) if "broker_order_id" in cancelable.columns else set()
    result_broker_ids = set(results["broker_order_id"].astype(str).str.strip()) if "broker_order_id" in results.columns else set()
    add("cancel_broker_order_ids_match", result_broker_ids == expected_broker_ids, f"matched={len(result_broker_ids & expected_broker_ids)};expected={len(expected_broker_ids)}", "same broker_order_id set", "cancel_blocker", "检查 cancel_orders 返回 broker_order_id。", "撤单必须绑定券商委托号。")

    cancel_run_ids = results["cancel_run_id"].astype(str).tolist() if "cancel_run_id" in results.columns else []
    run_id_ok = True if len(cancelable) == 0 and len(results) == 0 else bool(cancel_run_ids) and all(value == str(cancel_run_id) for value in cancel_run_ids)
    add("cancel_run_id_match", run_id_ok, ",".join(sorted(set(cancel_run_ids))) if cancel_run_ids else "no_cancelable_orders", cancel_run_id, "cancel_blocker", "确认撤单结果来自本次 cancel_run_id。", "撤单回报必须绑定本次撤单运行号。")

    cancel_statuses = results["cancel_status"].astype(str).str.lower() if "cancel_status" in results.columns else pd.Series(dtype=str)
    status_counts = cancel_statuses.value_counts().to_dict() if not cancel_statuses.empty else {}
    status_text = ";".join(f"{key}={value}" for key, value in sorted(status_counts.items())) or "no_cancelable_orders"
    valid_statuses = len(cancelable) == 0 or (not cancel_statuses.empty and set(cancel_statuses).issubset(CANCEL_ORDER_STATUSES))
    success_statuses = len(cancelable) == 0 or (not cancel_statuses.empty and set(cancel_statuses).issubset(CANCEL_ORDER_SUCCESS_STATUSES))
    add("cancel_status_values_valid", valid_statuses, status_text, ",".join(sorted(CANCEL_ORDER_STATUSES)), "cancel_blocker", "修正适配器 cancel_status 映射。", "撤单状态必须归一化到系统支持集合。")
    add("cancel_request_acknowledged", success_statuses, status_text, ",".join(sorted(CANCEL_ORDER_SUCCESS_STATUSES)), "cancel_blocker", "人工核对券商端未受理的撤单请求，必要时手工撤单。", "该检查只确认撤单请求已受理；随后必须重新查询，并由撤单终态审计确认 canceled 或 filled。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_CANCEL_AUDIT_COLUMNS)
