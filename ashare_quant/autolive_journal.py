from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import mapping_sha256
from .autolive_integrity import DEFAULT_INTEGRITY_KEY_ENV, MIN_HMAC_KEY_BYTES
from .config import project_path
from .io_utils import atomic_to_csv


AUTO_LIVE_JOURNAL_COLUMNS = [
    "sequence", "event_key", "event_type", "event_id", "event_time",
    "prev_event_hash", "payload_sha256", "event_hash", "hmac_sha256", "payload_json",
]
AUTO_LIVE_JOURNAL_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_JOURNAL_SUMMARY_COLUMNS = [
    "status", "event_count", "last_sequence", "last_event_hash", "hmac_required",
    "hmac_key_env", "hmac_key_present", "passed_checks", "total_checks", "generated_at", "message",
]
AUTO_LIVE_JOURNAL_ERROR_COLUMNS = ["event_key", "event_type", "event_id", "error", "resolved", "generated_at"]
JOURNAL_FILE = "auto_live_event_journal.csv"
JOURNAL_LOCK_FILE = "auto_live_journal.lock"
GENESIS_HASH = "0" * 64


def append_auto_live_event(
    config: dict[str, Any],
    event_type: str,
    event_id: str,
    payload: dict[str, Any],
    *,
    event_time: datetime | None = None,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / JOURNAL_FILE
    event_type = str(event_type).strip()
    event_id = str(event_id).strip()
    if not event_type or not event_id:
        raise RuntimeError("auto-live journal requires non-empty event_type and event_id")

    lock_path = _acquire_lock(output_dir / JOURNAL_LOCK_FILE, event_type, event_id)
    try:
        existing, read_error = _read_journal(journal_path)
        audit, _ = build_auto_live_journal_audit(config, existing, read_error=read_error, lock_present=False)
        if not audit["passed"].astype(bool).all():
            failed = ";".join(audit.loc[~audit["passed"].astype(bool), "check"].astype(str))
            raise RuntimeError(f"auto-live journal integrity failed before append: {failed}")

        sequence = len(existing) + 1
        event_key = f"{event_type}:{event_id}"
        if not existing.empty and existing["event_key"].astype(str).eq(event_key).any():
            raise RuntimeError(f"duplicate auto-live journal event_key: {event_key}")
        previous = str(existing.iloc[-1]["event_hash"]) if not existing.empty else GENESIS_HASH
        clean_payload = _json_safe({
            **payload,
            "auto_live_config_sha256": mapping_sha256(config.get("auto_live", {})),
        })
        payload_json = _canonical_json(clean_payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        timestamp = (event_time or datetime.now()).isoformat(timespec="microseconds")
        event_hash = _event_hash(sequence, event_key, timestamp, previous, payload_hash)
        journal = _journal_config(config)
        key = _journal_key(journal)
        if journal["require_hmac"] and len(key) < MIN_HMAC_KEY_BYTES:
            raise RuntimeError(f"auto-live journal HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes in {journal['key_env']}")
        signature = hmac.new(key, event_hash.encode("utf-8"), hashlib.sha256).hexdigest() if key else ""
        _append_csv_row(journal_path, {
            "sequence": sequence,
            "event_key": event_key,
            "event_type": event_type,
            "event_id": event_id,
            "event_time": timestamp,
            "prev_event_hash": previous,
            "payload_sha256": payload_hash,
            "event_hash": event_hash,
            "hmac_sha256": signature,
            "payload_json": payload_json,
        })
    finally:
        _release_lock(lock_path)
    return write_auto_live_journal_audit(config)


def record_auto_live_event(
    config: dict[str, Any],
    event_type: str,
    event_id: str,
    payload: dict[str, Any],
    *,
    strict: bool,
) -> dict[str, Path]:
    event_key = f"{str(event_type).strip()}:{str(event_id).strip()}"
    try:
        paths = append_auto_live_event(config, event_type, event_id, payload)
        _resolve_event_errors(config, event_key)
        return {**paths, **write_auto_live_journal_audit(config)}
    except Exception as exc:
        error_path = _append_event_error(config, event_type, event_id, exc)
        paths = write_auto_live_journal_audit(config)
        paths[error_path.name] = error_path
        if strict:
            raise
        return paths


def write_auto_live_journal_audit(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / JOURNAL_FILE
    frame, read_error = _read_journal(journal_path)
    audit, summary = build_auto_live_journal_audit(
        config,
        frame,
        read_error=read_error,
        lock_present=(output_dir / JOURNAL_LOCK_FILE).exists(),
        errors=_read_errors(output_dir / "auto_live_journal_errors.csv"),
    )
    if not journal_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_JOURNAL_COLUMNS), journal_path, index=False, encoding="utf-8-sig")
    audit_path = output_dir / "auto_live_journal_audit.csv"
    summary_path = output_dir / "auto_live_journal_summary.csv"
    error_path = output_dir / "auto_live_journal_errors.csv"
    if not error_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_JOURNAL_ERROR_COLUMNS), error_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    return {JOURNAL_FILE: journal_path, "auto_live_journal_audit.csv": audit_path, "auto_live_journal_summary.csv": summary_path, "auto_live_journal_errors.csv": error_path}


def build_auto_live_journal_audit(
    config: dict[str, Any],
    frame: pd.DataFrame,
    *,
    read_error: str = "",
    lock_present: bool = False,
    errors: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    journal = _journal_config(config)
    key = _journal_key(journal)

    def add(check: str, passed: bool, value: object, threshold: object, next_action: str, message: str) -> None:
        rows.append({
            "check": check, "passed": bool(passed), "value": value, "threshold": threshold,
            "severity": "ok" if passed else "journal_blocker", "next_action": "" if passed else next_action,
            "message": message,
        })

    add("journal_lock_clear", not lock_present, "locked" if lock_present else "clear", "no journal lock", "确认没有写入进程后再人工处理遗留锁。", "事件日志写锁存在时禁止新的自动实盘动作。")
    add("journal_csv_readable", not read_error, read_error or f"rows={len(frame)}", "readable CSV", "保留原文件并从可信备份恢复。", "事件日志必须可完整解析。")
    schema_ok = frame.empty or set(AUTO_LIVE_JOURNAL_COLUMNS).issubset(frame.columns)
    add("journal_schema_valid", schema_ok, ",".join(frame.columns) if not frame.empty else "empty", ",".join(AUTO_LIVE_JOURNAL_COLUMNS), "恢复事件日志表头。", "事件日志字段必须完整。")
    key_ready = (not journal["require_hmac"]) or len(key) >= MIN_HMAC_KEY_BYTES
    add("journal_hmac_key_ready", key_ready, f"required={journal['require_hmac']};key_env={journal['key_env']};key_bytes={len(key)}", "optional or key >= 32 bytes", f"在 {journal['key_env']} 配置至少 32 字节密钥。", "强制日志 HMAC 时必须提供强密钥。")
    unresolved = pd.DataFrame() if errors is None or errors.empty else errors[~errors["resolved"].map(_bool)]
    add("journal_append_errors_clear", unresolved.empty, len(unresolved), 0, "修复日志后重放缺失事件并将错误标记为已解决。", "任何未解决的事件追加失败都会阻断新提交。")

    checks = {"sequence": True, "previous": True, "payload": True, "event": True, "hmac": True, "time": True, "unique": True}
    previous = GENESIS_HASH
    seen: set[str] = set()
    if schema_ok and not frame.empty:
        for position, row in enumerate(frame.itertuples(index=False), start=1):
            sequence = _integer(getattr(row, "sequence", 0))
            event_key = str(getattr(row, "event_key", ""))
            event_time = str(getattr(row, "event_time", ""))
            prev_hash = str(getattr(row, "prev_event_hash", ""))
            payload_json = str(getattr(row, "payload_json", ""))
            payload_hash = str(getattr(row, "payload_sha256", ""))
            recorded_hash = str(getattr(row, "event_hash", ""))
            signature = str(getattr(row, "hmac_sha256", "") or "")
            checks["sequence"] &= sequence == position
            checks["previous"] &= hmac.compare_digest(prev_hash, previous)
            actual_payload = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            checks["payload"] &= hmac.compare_digest(payload_hash, actual_payload)
            expected_event = _event_hash(sequence, event_key, event_time, prev_hash, payload_hash)
            checks["event"] &= hmac.compare_digest(recorded_hash, expected_event)
            if journal["require_hmac"] or signature:
                expected_signature = hmac.new(key, recorded_hash.encode("utf-8"), hashlib.sha256).hexdigest() if key else ""
                checks["hmac"] &= bool(signature) and bool(key) and hmac.compare_digest(signature, expected_signature)
            checks["time"] &= not pd.isna(pd.to_datetime(event_time, errors="coerce"))
            checks["unique"] &= bool(event_key) and event_key not in seen
            seen.add(event_key)
            previous = recorded_hash

    specs = [
        ("journal_sequence_contiguous", "sequence", len(frame), "1..N", "从可信备份恢复，禁止手工重编号。"),
        ("journal_previous_hash_chain", "previous", "valid", "each prev hash matches", "调查缺失或插入的事件。"),
        ("journal_payload_hashes_valid", "payload", "valid", "all payload hashes match", "调查事件内容是否被修改。"),
        ("journal_event_hashes_valid", "event", "valid", "all event hashes match", "调查事件链是否被重写。"),
        ("journal_hmac_signatures_valid", "hmac", "valid", "all present/required signatures valid", "核对密钥与事件文件。"),
        ("journal_event_times_valid", "time", "valid", "parseable timestamps", "恢复无效时间记录。"),
        ("journal_event_keys_unique", "unique", f"unique={len(seen)};rows={len(frame)}", "all unique", "调查重复动作或日志重放。"),
    ]
    for check, key_name, passed_value, threshold, action in specs:
        add(check, checks[key_name], passed_value if checks[key_name] else "failed", threshold, action, "事件日志哈希链约束。")

    audit = pd.DataFrame(rows, columns=AUTO_LIVE_JOURNAL_AUDIT_COLUMNS)
    passed_count = int(audit["passed"].sum())
    all_passed = bool(len(audit) and passed_count == len(audit))
    status = "verified" if all_passed and not frame.empty else ("empty_ready" if all_passed else "failed")
    message = "事件哈希链验证通过。" if status == "verified" else ("事件日志为空，已准备写入首个事件。" if status == "empty_ready" else "事件日志完整性失败，自动实盘应保持阻断。")
    summary = pd.DataFrame([{
        "status": status, "event_count": len(frame),
        "last_sequence": _integer(frame.iloc[-1].get("sequence", 0)) if not frame.empty else 0,
        "last_event_hash": str(frame.iloc[-1].get("event_hash", "")) if not frame.empty else GENESIS_HASH,
        "hmac_required": bool(journal["require_hmac"]), "hmac_key_env": str(journal["key_env"]),
        "hmac_key_present": bool(key), "passed_checks": passed_count, "total_checks": len(audit),
        "generated_at": datetime.now().isoformat(timespec="seconds"), "message": message,
    }], columns=AUTO_LIVE_JOURNAL_SUMMARY_COLUMNS)
    return audit, summary

def auto_live_journal_status(config: dict[str, Any]) -> tuple[bool, str, bool]:
    output_dir = project_path(config["paths"]["output_dir"])
    frame, read_error = _read_journal(output_dir / JOURNAL_FILE)
    audit, summary = build_auto_live_journal_audit(config, frame, read_error=read_error, lock_present=(output_dir / JOURNAL_LOCK_FILE).exists(), errors=_read_errors(output_dir / "auto_live_journal_errors.csv"))
    passed = bool(not audit.empty and audit["passed"].astype(bool).all())
    status = str(summary.iloc[0].get("status", "missing")) if not summary.empty else "missing"
    enabled = bool(_journal_config(config)["enabled"])
    return passed, f"enabled={enabled};status={status};passed={int(audit['passed'].sum())}/{len(audit)}", enabled


def _journal_config(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    journal = auto.get("journal", {}) if isinstance(auto.get("journal", {}), dict) else {}
    return {
        "enabled": _bool(journal.get("enabled", False)),
        "require_hmac": _bool(journal.get("require_hmac", False)),
        "key_env": str(journal.get("hmac_key_env", DEFAULT_INTEGRITY_KEY_ENV) or DEFAULT_INTEGRITY_KEY_ENV).strip(),
    }


def _journal_key(journal: dict[str, object]) -> bytes:
    key_env = str(journal.get("key_env", ""))
    return os.environ.get(key_env, "").encode("utf-8") if key_env else b""


def _event_hash(sequence: int, event_key: str, event_time: str, previous: str, payload_hash: str) -> str:
    return hashlib.sha256(f"{sequence}|{event_key}|{event_time}|{previous}|{payload_hash}".encode("utf-8")).hexdigest()


def _acquire_lock(path: Path, event_type: str, event_id: str) -> Path:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"event_type": event_type, "event_id": event_id, "pid": os.getpid(), "created_at": datetime.now().isoformat(timespec="seconds")}, handle, ensure_ascii=False)
    return path


def _release_lock(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _append_csv_row(path: Path, row: dict[str, object]) -> None:
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUTO_LIVE_JOURNAL_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def _read_journal(path: Path) -> tuple[pd.DataFrame, str]:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=AUTO_LIVE_JOURNAL_COLUMNS), ""
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:
        return pd.DataFrame(columns=AUTO_LIVE_JOURNAL_COLUMNS), f"{type(exc).__name__}: {exc}"
    return frame, ""


def _read_errors(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=AUTO_LIVE_JOURNAL_ERROR_COLUMNS)
    try:
        frame = pd.read_csv(path, keep_default_na=False)
    except Exception:
        return pd.DataFrame([{"event_key": "unreadable", "event_type": "", "event_id": "", "error": "error file unreadable", "resolved": False, "generated_at": ""}], columns=AUTO_LIVE_JOURNAL_ERROR_COLUMNS)
    for column in AUTO_LIVE_JOURNAL_ERROR_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[AUTO_LIVE_JOURNAL_ERROR_COLUMNS]


def _append_event_error(config: dict[str, Any], event_type: str, event_id: str, exc: Exception) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    path = output_dir / "auto_live_journal_errors.csv"
    existing = _read_errors(path)
    row = pd.DataFrame([{
        "event_key": f"{event_type}:{event_id}", "event_type": event_type, "event_id": event_id,
        "error": f"{type(exc).__name__}: {exc}", "resolved": False,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }], columns=AUTO_LIVE_JOURNAL_ERROR_COLUMNS)
    atomic_to_csv(pd.concat([existing, row], ignore_index=True), path, index=False, encoding="utf-8-sig")
    return path


def _resolve_event_errors(config: dict[str, Any], event_key: str) -> None:
    path = project_path(config["paths"]["output_dir"]) / "auto_live_journal_errors.csv"
    errors = _read_errors(path)
    if errors.empty:
        return
    matched = errors["event_key"].astype(str).eq(event_key)
    if matched.any():
        errors.loc[matched, "resolved"] = True
        atomic_to_csv(errors, path, index=False, encoding="utf-8-sig")


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}


def _integer(value: object) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return int(parsed) if not pd.isna(parsed) else 0
