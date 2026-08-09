from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pandas as pd

from .audit import mapping_sha256
from .autolive_integrity import DEFAULT_INTEGRITY_KEY_ENV, MIN_HMAC_KEY_BYTES
from .config import project_path
from .io_utils import atomic_to_csv


ALERT_SCHEMA = "ashare_quant.alert.v1"
ALERT_OUTBOX_FILE = "auto_live_alert_outbox.csv"
ALERT_SELFTEST_FILE = "auto_live_alert_selftest.csv"
ALERT_AUDIT_FILE = "auto_live_alert_audit.csv"
ALERT_OUTBOX_COLUMNS = [
    "alert_id", "event_type", "severity", "status", "attempts", "environment",
    "message", "context_json", "created_at", "last_attempt_at", "delivered_at", "error",
]
ALERT_SELFTEST_COLUMNS = [
    "run_id", "status", "environment", "capabilities", "config_sha256",
    "generated_at", "message",
]
ALERT_AUDIT_COLUMNS = [
    "check", "passed", "value", "threshold", "severity", "next_action", "message",
]


def write_auto_live_alert_selftest(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = _output_dir(config)
    settings = alert_settings(config)
    run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    result = _invoke(
        config,
        mode="self_test",
        run_id=run_id,
        payload={"event_type": "self_test", "severity": "info", "message": "auto-live alert self-test", "context": {}},
    )
    row = {
        "run_id": run_id,
        "status": str(result.get("status", "failed")).lower(),
        "environment": str(result.get("environment", "unknown")).lower(),
        "capabilities": ",".join(map(str, result.get("capabilities", []))) if isinstance(result.get("capabilities"), list) else "",
        "config_sha256": _config_hash(config),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message": str(result.get("message", "")),
    }
    selftest_path = output_dir / ALERT_SELFTEST_FILE
    atomic_to_csv(pd.DataFrame([row], columns=ALERT_SELFTEST_COLUMNS), selftest_path, index=False, encoding="utf-8-sig")
    paths = write_auto_live_alert_audit(config)
    paths[ALERT_SELFTEST_FILE] = selftest_path
    if settings["required"] and not _audit_passed(paths[ALERT_AUDIT_FILE]):
        raise RuntimeError("auto-live alert self-test did not certify a live alert channel")
    return paths


def send_auto_live_alert(
    config: dict[str, Any],
    *,
    event_type: str,
    severity: str,
    message: str,
    context: dict[str, Any] | None = None,
    strict: bool = False,
    alert_id: str | None = None,
) -> dict[str, Path]:
    output_dir = _output_dir(config)
    settings = alert_settings(config)
    identifier = alert_id or datetime.now().strftime("%Y%m%d%H%M%S%f")
    outbox_path = output_dir / ALERT_OUTBOX_FILE
    existing = _read_csv(outbox_path)
    current = existing[existing["alert_id"].astype(str) == identifier] if not existing.empty and "alert_id" in existing else pd.DataFrame()
    attempts = int(pd.to_numeric(current.iloc[-1].get("attempts", 0), errors="coerce") or 0) + 1 if not current.empty else 1
    created_at = str(current.iloc[-1].get("created_at", "")) if not current.empty else datetime.now(timezone.utc).isoformat(timespec="seconds")
    context_value = _safe_json(context or {})
    status = "local_only"
    environment = "disabled"
    error = ""
    delivered_at = ""
    try:
        if settings["enabled"]:
            result = _invoke(
                config,
                mode="deliver",
                run_id=identifier,
                payload={"event_type": event_type, "severity": severity, "message": message, "context": context or {}},
            )
            result_status = str(result.get("status", "failed")).lower()
            environment = str(result.get("environment", "unknown")).lower()
            environment_ok = (not settings["require_live"]) or environment == "live"
            delivered = result_status in {"ok", "delivered"} and environment_ok
            status = "delivered" if delivered else "pending"
            delivered_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if delivered else ""
            if not delivered:
                error = f"status={result_status};environment={environment};message={result.get('message', '')}"
        elif settings["required"]:
            status = "pending"
            error = "alerting required but disabled"
    except Exception as exc:
        status = "pending"
        environment = "unknown"
        error = f"{type(exc).__name__}: {exc}"

    row = {
        "alert_id": identifier,
        "event_type": str(event_type),
        "severity": str(severity).lower(),
        "status": status,
        "attempts": attempts,
        "environment": environment,
        "message": str(message),
        "context_json": context_value,
        "created_at": created_at,
        "last_attempt_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "delivered_at": delivered_at,
        "error": error,
    }
    remaining = existing[existing["alert_id"].astype(str) != identifier] if not existing.empty and "alert_id" in existing else existing
    atomic_to_csv(pd.concat([remaining, pd.DataFrame([row], columns=ALERT_OUTBOX_COLUMNS)], ignore_index=True), outbox_path, index=False, encoding="utf-8-sig")
    paths = write_auto_live_alert_audit(config)
    paths[ALERT_OUTBOX_FILE] = outbox_path
    if strict and status != "delivered":
        raise RuntimeError(f"auto-live alert delivery failed: {error or status}")
    return paths


def send_auto_live_incident_alert(
    config: dict[str, Any],
    *,
    event_type: str,
    incident_key: str,
    severity: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Deliver one alert per incident, while allowing pending delivery retries."""
    identifier = "incident-" + hashlib.sha256(
        f"{event_type}|{incident_key}".encode("utf-8")
    ).hexdigest()[:32]
    output_dir = _output_dir(config)
    outbox_path = output_dir / ALERT_OUTBOX_FILE
    existing = _read_csv(outbox_path)
    current = (
        existing[existing["alert_id"].astype(str) == identifier]
        if not existing.empty and "alert_id" in existing
        else pd.DataFrame()
    )
    current_status = str(current.iloc[-1].get("status", "")).lower() if not current.empty else ""
    if current_status == "delivered" or (current_status == "local_only" and not alert_settings(config)["enabled"]):
        paths = write_auto_live_alert_audit(config)
        paths[ALERT_OUTBOX_FILE] = outbox_path
        return paths
    return send_auto_live_alert(
        config,
        event_type=event_type,
        severity=severity,
        message=message,
        context={"incident_key": str(incident_key), **(context or {})},
        strict=False,
        alert_id=identifier,
    )


def retry_auto_live_alerts(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = _output_dir(config)
    outbox = _read_csv(output_dir / ALERT_OUTBOX_FILE)
    pending = outbox[outbox["status"].astype(str).str.lower() == "pending"].copy() if not outbox.empty and "status" in outbox else pd.DataFrame()
    paths: dict[str, Path] = {}
    for row in pending.itertuples(index=False):
        try:
            context = json.loads(str(getattr(row, "context_json", "{}")) or "{}")
        except json.JSONDecodeError:
            context = {}
        paths.update(send_auto_live_alert(
            config,
            event_type=str(getattr(row, "event_type", "retry")),
            severity=str(getattr(row, "severity", "critical")),
            message=str(getattr(row, "message", "")),
            context=context if isinstance(context, dict) else {},
            alert_id=str(getattr(row, "alert_id", "")),
        ))
    return paths or write_auto_live_alert_audit(config)


def write_auto_live_alert_audit(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = _output_dir(config)
    outbox_path = output_dir / ALERT_OUTBOX_FILE
    selftest_path = output_dir / ALERT_SELFTEST_FILE
    audit_path = output_dir / ALERT_AUDIT_FILE
    if not outbox_path.exists():
        atomic_to_csv(pd.DataFrame(columns=ALERT_OUTBOX_COLUMNS), outbox_path, index=False, encoding="utf-8-sig")
    if not selftest_path.exists():
        atomic_to_csv(pd.DataFrame(columns=ALERT_SELFTEST_COLUMNS), selftest_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(build_auto_live_alert_audit(config), audit_path, index=False, encoding="utf-8-sig")
    return {ALERT_OUTBOX_FILE: outbox_path, ALERT_SELFTEST_FILE: selftest_path, ALERT_AUDIT_FILE: audit_path}


def build_auto_live_alert_audit(config: dict[str, Any], *, now: datetime | None = None) -> pd.DataFrame:
    settings = alert_settings(config)
    output_dir = _output_dir(config)
    selftest = _read_csv(output_dir / ALERT_SELFTEST_FILE)
    outbox = _read_csv(output_dir / ALERT_OUTBOX_FILE)
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, action: str, message: str) -> None:
        rows.append({
            "check": check, "passed": bool(passed), "value": value, "threshold": threshold,
            "severity": "ok" if passed else "alert_blocker",
            "next_action": "" if passed else action, "message": message,
        })

    required = bool(settings["required"])
    enabled = bool(settings["enabled"])
    add("alerting_enabled_when_required", enabled or not required, enabled, "enabled when required", "配置 auto_live.alerting.enabled=true。", "无人值守真实提交必须启用外部告警。")
    command = settings["command"]
    configured = isinstance(command, list) and bool(command)
    add("alert_command_configured", configured or not required, len(command) if isinstance(command, list) else 0, ">0 arguments when required", "配置经过审计的告警命令。", "告警命令必须明确配置。")
    executable_ok = _command_executable(command) if configured else False
    add("alert_command_executable", executable_ok or not required, command[0] if configured else "missing", "existing executable when required", "修正告警命令入口。", "告警进程必须可启动。")
    pinned_ok, pinned_value = _pinned_files_status(settings["pinned_files"])
    add("alert_files_pinned", pinned_ok or not required, pinned_value, "all configured hashes match when required", "核对告警脚本并更新固定 SHA-256。", "告警桥接文件变化必须显式复核。")
    key = _key(settings["key_env"])
    key_ok = (not settings["require_hmac"]) or len(key) >= MIN_HMAC_KEY_BYTES
    add("alert_hmac_key_ready", key_ok, f"required={settings['require_hmac']};key_bytes={len(key)}", "optional or >=32 bytes", f"在 {settings['key_env']} 配置至少 32 字节密钥。", "告警请求和结果必须防篡改。")

    row = selftest.iloc[-1] if not selftest.empty else pd.Series(dtype=object)
    status = str(row.get("status", "")).lower()
    environment = str(row.get("environment", "")).lower()
    capabilities = {item.strip() for item in str(row.get("capabilities", "")).split(",") if item.strip()}
    config_match = str(row.get("config_sha256", "")) == _config_hash(config)
    generated = pd.to_datetime(row.get("generated_at", ""), errors="coerce", utc=True)
    current = pd.Timestamp(now or datetime.now(timezone.utc))
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    age = float((current.tz_convert("UTC") - generated).total_seconds()) if not pd.isna(generated) else float("inf")
    selftest_ok = status in {"ok", "ready"} and "alert" in capabilities and config_match and age <= float(settings["selftest_max_age_seconds"])
    add("alert_selftest_current", selftest_ok or not required, f"status={status or 'missing'};capabilities={','.join(sorted(capabilities))};config_match={config_match};age_seconds={age:.1f}", f"ok + alert capability + current config + age <= {settings['selftest_max_age_seconds']:g}", "运行 auto-live-alert-selftest。", "正式提交前告警通道必须通过当前配置自检。")
    live_ok = environment == "live"
    add("alert_environment_live", live_ok or not required or not settings["require_live"], environment or "missing", "live when required", "将告警桥接器接入真实通知通道并重新自检。", "模拟通知不能充当真实无人值守告警。")
    pending = outbox[outbox["status"].astype(str).str.lower() == "pending"] if not outbox.empty and "status" in outbox else pd.DataFrame()
    add("alert_outbox_clear", pending.empty or not required, len(pending), 0, "运行 auto-live-alert-retry 并处理仍失败的通知。", "正式提交前不能积压未送达的关键告警。")
    return pd.DataFrame(rows, columns=ALERT_AUDIT_COLUMNS)


def auto_live_alert_status(config: dict[str, Any]) -> tuple[bool, str, bool]:
    settings = alert_settings(config)
    audit = build_auto_live_alert_audit(config)
    passed = bool(not audit.empty and audit["passed"].astype(bool).all())
    failed = audit.loc[~audit["passed"].astype(bool), "check"].astype(str).tolist()
    return passed, f"required={settings['required']};passed={int(audit['passed'].sum())}/{len(audit)};failed={';'.join(failed)}", bool(settings["required"])


def alert_settings(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    cfg = auto.get("alerting", {}) if isinstance(auto.get("alerting", {}), dict) else {}
    command = cfg.get("command", [])
    return {
        "enabled": _bool(cfg.get("enabled", False)),
        "required": _bool(cfg.get("required_before_submit", False)),
        "require_live": _bool(cfg.get("require_live_environment", True)),
        "require_hmac": _bool(cfg.get("require_hmac", False)),
        "key_env": str(cfg.get("hmac_key_env", DEFAULT_INTEGRITY_KEY_ENV) or DEFAULT_INTEGRITY_KEY_ENV),
        "command": [str(item) for item in command] if isinstance(command, list) else [],
        "timeout_seconds": max(_float(cfg.get("timeout_seconds", 15), 15), 1.0),
        "working_dir": str(cfg.get("working_dir", "") or ""),
        "selftest_max_age_seconds": max(_float(cfg.get("selftest_max_age_seconds", 86400), 86400), 1.0),
        "pinned_files": cfg.get("pinned_files", []) if isinstance(cfg.get("pinned_files", []), list) else [],
    }


def verify_alert_request(payload: dict[str, Any]) -> tuple[bool, str]:
    return _verify_envelope(payload, prefix="request")


def finalize_alert_response(request: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    integrity = request.get("integrity", {}) if isinstance(request.get("integrity"), dict) else {}
    required = _bool(integrity.get("required", False))
    key_env = str(integrity.get("key_env", DEFAULT_INTEGRITY_KEY_ENV))
    clean = _without_integrity(response)
    payload_hash = _payload_hash(clean)
    request_hash = str(integrity.get("payload_sha256", ""))
    key = _key(key_env)
    result = dict(clean)
    result["integrity"] = {
        "required": required, "key_env": key_env, "request_payload_sha256": request_hash,
        "payload_sha256": payload_hash,
        "hmac_sha256": _sign(key, f"response|{request_hash}|{payload_hash}") if key else "",
    }
    return result


def _invoke(config: dict[str, Any], *, mode: str, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = alert_settings(config)
    command = settings["command"]
    if not settings["enabled"]:
        raise RuntimeError("alerting is disabled")
    if not command:
        raise RuntimeError("alert command is not configured")
    output_dir = _output_dir(config)
    request_path = output_dir / f"auto_live_alert_request_{run_id}.json"
    result_path = output_dir / f"auto_live_alert_result_{run_id}.json"
    envelope = {
        "schema_version": ALERT_SCHEMA, "mode": mode, "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_sha256": _config_hash(config), "result_path": str(result_path), **payload,
    }
    envelope = _attach_integrity(envelope, settings)
    request_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    cwd = project_path(settings["working_dir"]) if settings["working_dir"] else None
    try:
        completed = subprocess.run(
            [*command, str(request_path), str(result_path)],
            cwd=str(cwd) if cwd else None,
            timeout=float(settings["timeout_seconds"]),
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"alert command timed out after {settings['timeout_seconds']:g}s") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"alert command exited {completed.returncode}: {(completed.stderr or completed.stdout)[:240]}")
    if not result_path.exists():
        raise RuntimeError("alert command did not write result JSON")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("alert result must be a JSON object")
    valid, reason = _verify_response(envelope, result)
    if not valid:
        raise RuntimeError(f"alert result integrity failed: {reason}")
    return result


def _attach_integrity(payload: dict[str, Any], settings: dict[str, object]) -> dict[str, Any]:
    required = bool(settings["require_hmac"])
    key_env = str(settings["key_env"])
    key = _key(key_env)
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        raise RuntimeError(f"alert HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes in {key_env}")
    payload_hash = _payload_hash(payload)
    result = dict(payload)
    result["integrity"] = {
        "required": required, "key_env": key_env, "payload_sha256": payload_hash,
        "hmac_sha256": _sign(key, f"request|{payload_hash}") if key else "",
    }
    return result


def _verify_envelope(payload: dict[str, Any], *, prefix: str) -> tuple[bool, str]:
    integrity = payload.get("integrity", {}) if isinstance(payload.get("integrity"), dict) else {}
    required = _bool(integrity.get("required", False))
    key_env = str(integrity.get("key_env", DEFAULT_INTEGRITY_KEY_ENV))
    actual = _payload_hash(_without_integrity(payload))
    recorded = str(integrity.get("payload_sha256", ""))
    if not recorded or not hmac.compare_digest(actual, recorded):
        return False, f"{prefix}_payload_hash_mismatch"
    key = _key(key_env)
    signature = str(integrity.get("hmac_sha256", ""))
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        return False, f"{prefix}_key_missing_or_weak"
    if required and not signature:
        return False, f"{prefix}_signature_missing"
    if signature and (not key or not hmac.compare_digest(signature, _sign(key, f"request|{actual}"))):
        return False, f"{prefix}_signature_mismatch"
    return True, f"{prefix}_valid"


def _verify_response(request: dict[str, Any], response: dict[str, Any]) -> tuple[bool, str]:
    request_integrity = request.get("integrity", {}) if isinstance(request.get("integrity"), dict) else {}
    integrity = response.get("integrity", {}) if isinstance(response.get("integrity"), dict) else {}
    required = _bool(request_integrity.get("required", False))
    key_env = str(request_integrity.get("key_env", DEFAULT_INTEGRITY_KEY_ENV))
    if not integrity:
        return (False, "response_integrity_missing") if required else (True, "response_integrity_optional")
    request_hash = str(request_integrity.get("payload_sha256", ""))
    if str(integrity.get("request_payload_sha256", "")) != request_hash:
        return False, "response_request_hash_mismatch"
    actual = _payload_hash(_without_integrity(response))
    recorded = str(integrity.get("payload_sha256", ""))
    if not recorded or not hmac.compare_digest(actual, recorded):
        return False, "response_payload_hash_mismatch"
    key = _key(key_env)
    signature = str(integrity.get("hmac_sha256", ""))
    expected = _sign(key, f"response|{request_hash}|{actual}") if key else ""
    if required and (len(key) < MIN_HMAC_KEY_BYTES or not signature):
        return False, "response_signature_missing_or_key_weak"
    if signature and (not expected or not hmac.compare_digest(signature, expected)):
        return False, "response_signature_mismatch"
    return True, "response_valid"


def _pinned_files_status(entries: object) -> tuple[bool, str]:
    if not isinstance(entries, list) or not entries:
        return False, "configured=0"
    passed = 0
    for entry in entries:
        item = entry if isinstance(entry, dict) else {}
        path = project_path(str(item.get("path", "")))
        expected = str(item.get("sha256", "")).lower()
        if path.is_file() and len(expected) == 64 and hmac.compare_digest(_file_sha256(path), expected):
            passed += 1
    return passed == len(entries), f"passed={passed}/{len(entries)}"


def _command_executable(command: list[str]) -> bool:
    if not command:
        return False
    candidate = Path(command[0])
    return candidate.exists() if candidate.is_absolute() or candidate.parent != Path(".") else shutil_which(command[0]) is not None


def shutil_which(value: str) -> str | None:
    import shutil
    return shutil.which(value)


def _config_hash(config: dict[str, Any]) -> str:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    return mapping_sha256(auto.get("alerting", {}) if isinstance(auto.get("alerting", {}), dict) else {})


def _output_dir(config: dict[str, Any]) -> Path:
    path = project_path(config["paths"]["output_dir"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audit_passed(path: Path) -> bool:
    frame = _read_csv(path)
    return bool(not frame.empty and frame["passed"].astype(bool).all())


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False)
    except Exception:
        return pd.DataFrame()


def _safe_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _without_integrity(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if str(key) != "integrity"}


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_safe_json(payload).encode("utf-8")).hexdigest()


def _sign(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _key(key_env: object) -> bytes:
    return os.environ.get(str(key_env), "").encode("utf-8") if str(key_env) else b""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _float(value: object, default: float) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(parsed) if not pd.isna(parsed) else default


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
