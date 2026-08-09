from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv


DEFAULT_INTEGRITY_KEY_ENV = "ASHARE_QUANT_ADAPTER_HMAC_KEY"
INTEGRITY_ALGORITHM = "hmac-sha256"
MIN_HMAC_KEY_BYTES = 32
AUTO_LIVE_ADAPTER_FILE_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "path", "expected_sha256", "actual_sha256", "next_action", "message"]


def write_command_adapter_file_audit(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_adapter_file_audit.csv"
    atomic_to_csv(build_command_adapter_file_audit(config), path, index=False, encoding="utf-8-sig")
    return path


def build_command_adapter_file_audit(config: dict[str, Any]) -> pd.DataFrame:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    adapter = auto.get("command_adapter", {}) if isinstance(auto.get("command_adapter", {}), dict) else {}
    required = _bool(adapter.get("require_pinned_files", False))
    require_complete_manifest = _bool(adapter.get("require_complete_pin_manifest", False))
    raw_entries = adapter.get("pinned_files", [])
    entries = raw_entries if isinstance(raw_entries, list) else []
    raw_required_paths = adapter.get("required_pinned_paths", [])
    required_paths = [str(value or "").strip() for value in raw_required_paths] if isinstance(raw_required_paths, list) else []
    required_paths = [value for value in required_paths if value]
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, path_value: str, expected: str, actual: str, next_action: str, message: str) -> None:
        rows.append({
            "check": check,
            "passed": bool(passed),
            "value": value,
            "threshold": threshold,
            "severity": "ok" if passed else "auto_blocker",
            "path": path_value,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "next_action": "" if passed else next_action,
            "message": message,
        })

    add("adapter_file_pinning_enabled", True, required, "explicit configuration", "", "", "", "", "文件哈希锁定是否强制由配置明确记录。")
    configured = bool(entries)
    add("adapter_pinned_files_configured", configured or not required, len(entries), ">0 when required", "", "", "", "配置 command_adapter.pinned_files。", "强制文件锁定时必须至少固定一个桥接文件。")
    entry_paths = [str(entry.get("path", "") or "").strip() for entry in entries if isinstance(entry, dict)]
    entry_paths = [value for value in entry_paths if value]
    unique_entry_paths = set(entry_paths)
    add("adapter_pinned_file_paths_unique", len(entry_paths) == len(unique_entry_paths), len(unique_entry_paths), len(entry_paths), "", "", "", "删除重复的 pinned_files.path。", "每个固定文件路径只能出现一次，避免重复条目掩盖依赖缺失。")
    add("adapter_complete_pin_manifest_enabled", True, require_complete_manifest, "explicit configuration", "", "", "", "", "是否强制核对桥接器全部直接运行时依赖。")
    add("adapter_required_pinned_paths_configured", bool(required_paths) or not require_complete_manifest, len(required_paths), ">0 when complete manifest required", "", "", "", "配置 command_adapter.required_pinned_paths。", "完整依赖清单开启时必须明确列出所有必需固定路径。")
    missing_required = sorted(set(required_paths) - unique_entry_paths) if require_complete_manifest else []
    add("adapter_required_pinned_paths_complete", not missing_required, ";".join(missing_required) if missing_required else "complete", "all required paths pinned", "", "", "", "把缺失运行时依赖加入 pinned_files 并填写 SHA-256。", "桥接脚本、配置及其直接导入的本地执行模块必须全部固定。")
    for index, entry in enumerate(entries, start=1):
        item = entry if isinstance(entry, dict) else {}
        raw_path = str(item.get("path", "") or "").strip()
        expected = str(item.get("sha256", "") or "").strip().lower()
        candidate = project_path(raw_path) if raw_path else Path("")
        exists = bool(raw_path and candidate.exists() and candidate.is_file())
        actual = _file_sha256(candidate) if exists else ""
        expected_valid = len(expected) == 64 and all(char in "0123456789abcdef" for char in expected)
        add(f"adapter_pinned_file_{index}_path_valid", exists, str(candidate.resolve()) if exists else raw_path or "missing", "existing regular file", raw_path, expected, actual, "修正 pinned_files.path。", "固定文件必须存在且为普通文件。")
        add(f"adapter_pinned_file_{index}_expected_hash_valid", expected_valid, expected or "missing", "64 lowercase hex characters", raw_path, expected, actual, "填写完整 SHA-256。", "期望哈希必须使用完整 SHA-256。")
        add(f"adapter_pinned_file_{index}_hash_match", exists and expected_valid and hmac.compare_digest(actual, expected), actual or "missing", expected or "configured sha256", raw_path, expected, actual, "核对文件变更；确认可信后更新固定哈希并重新认证。", "桥接脚本或配置的任何字节变化都必须显式复核。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_FILE_AUDIT_COLUMNS)


def command_adapter_file_status(config: dict[str, Any]) -> tuple[bool, str, bool]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    adapter = auto.get("command_adapter", {}) if isinstance(auto.get("command_adapter", {}), dict) else {}
    required = _bool(adapter.get("require_pinned_files", False))
    audit = build_command_adapter_file_audit(config)
    passed = bool(not audit.empty and audit["passed"].astype(bool).all())
    entries = adapter.get("pinned_files", [])
    configured = bool(entries) if isinstance(entries, list) else False
    effective = passed if configured else not required
    failed = audit.loc[~audit["passed"].astype(bool), "check"].astype(str).tolist() if not audit.empty else ["audit_missing"]
    return effective, f"required={required};passed={int(audit['passed'].sum()) if not audit.empty else 0}/{len(audit)};failed={';'.join(failed)}", required




def command_integrity_status(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    adapter = auto.get("command_adapter", {}) if isinstance(auto.get("command_adapter", {}), dict) else {}
    required = _bool(adapter.get("require_hmac_integrity", False))
    key_env = str(adapter.get("integrity_key_env", DEFAULT_INTEGRITY_KEY_ENV) or DEFAULT_INTEGRITY_KEY_ENV).strip()
    key = os.environ.get(key_env, "").encode("utf-8") if key_env else b""
    key_present = bool(key)
    key_strong = len(key) >= MIN_HMAC_KEY_BYTES
    return {
        "required": required,
        "key_env": key_env,
        "key_present": key_present,
        "key_bytes": len(key),
        "key_strong": key_strong,
        "ready": (not required) or (bool(key_env) and key_strong),
    }


def attach_request_integrity(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    status = command_integrity_status(config)
    required = bool(status["required"])
    key_env = str(status["key_env"])
    key = os.environ.get(key_env, "").encode("utf-8") if key_env else b""
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        raise RuntimeError(f"command adapter HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes in environment variable {key_env}")
    clean = _without_integrity(payload)
    payload_hash = payload_sha256(clean)
    signature = _sign(key, _request_signature_text(required, key_env, payload_hash)) if key else ""
    result = dict(clean)
    result["integrity"] = {
        "algorithm": INTEGRITY_ALGORITHM,
        "required": required,
        "key_env": key_env,
        "payload_sha256": payload_hash,
        "hmac_sha256": signature,
    }
    return result


def verify_adapter_request_integrity(payload: dict[str, Any]) -> tuple[bool, str]:
    integrity = payload.get("integrity", {}) if isinstance(payload.get("integrity", {}), dict) else {}
    if not integrity:
        return True, "integrity_optional_missing"
    required = _bool(integrity.get("required", False))
    key_env = str(integrity.get("key_env", DEFAULT_INTEGRITY_KEY_ENV) or DEFAULT_INTEGRITY_KEY_ENV).strip()
    expected_hash = str(integrity.get("payload_sha256", ""))
    actual_hash = payload_sha256(_without_integrity(payload))
    if not expected_hash or not hmac.compare_digest(expected_hash, actual_hash):
        return False, "request_payload_sha256_mismatch"
    signature = str(integrity.get("hmac_sha256", ""))
    key = os.environ.get(key_env, "").encode("utf-8") if key_env else b""
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        return False, "request_hmac_key_missing_or_weak"
    if required and not signature:
        return False, "request_hmac_missing"
    if signature:
        if not key:
            return False, "request_hmac_key_missing"
        expected = _sign(key, _request_signature_text(required, key_env, actual_hash))
        if not hmac.compare_digest(signature, expected):
            return False, "request_hmac_mismatch"
    return True, "request_integrity_valid"


def finalize_adapter_response(request: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    request_integrity = request.get("integrity", {}) if isinstance(request.get("integrity", {}), dict) else {}
    required = _bool(request_integrity.get("required", False))
    key_env = str(request_integrity.get("key_env", DEFAULT_INTEGRITY_KEY_ENV) or DEFAULT_INTEGRITY_KEY_ENV).strip()
    request_hash = payload_sha256(_without_integrity(request))
    clean = _without_integrity(response)
    response_hash = payload_sha256(clean)
    key = os.environ.get(key_env, "").encode("utf-8") if key_env else b""
    signature = _sign(key, _response_signature_text(required, key_env, request_hash, response_hash)) if key else ""
    result = dict(clean)
    result["integrity"] = {
        "algorithm": INTEGRITY_ALGORITHM,
        "required": required,
        "key_env": key_env,
        "request_payload_sha256": request_hash,
        "payload_sha256": response_hash,
        "hmac_sha256": signature,
    }
    return result


def verify_adapter_result_integrity(
    config: dict[str, Any],
    request: dict[str, Any],
    response: dict[str, Any],
) -> tuple[bool, str]:
    status = command_integrity_status(config)
    required = bool(status["required"])
    integrity = response.get("integrity", {}) if isinstance(response.get("integrity", {}), dict) else {}
    if not integrity:
        return (False, "result_integrity_missing") if required else (True, "integrity_optional_missing")
    key_env = str(status["key_env"])
    if str(integrity.get("key_env", "")) != key_env:
        return False, "result_integrity_key_env_mismatch"
    if _bool(integrity.get("required", False)) != required:
        return False, "result_integrity_required_flag_mismatch"
    request_hash = payload_sha256(_without_integrity(request))
    returned_request_hash = str(integrity.get("request_payload_sha256", ""))
    if not returned_request_hash or not hmac.compare_digest(returned_request_hash, request_hash):
        return False, "result_request_sha256_mismatch"
    response_hash = payload_sha256(_without_integrity(response))
    returned_response_hash = str(integrity.get("payload_sha256", ""))
    if not returned_response_hash or not hmac.compare_digest(returned_response_hash, response_hash):
        return False, "result_payload_sha256_mismatch"
    signature = str(integrity.get("hmac_sha256", ""))
    key = os.environ.get(key_env, "").encode("utf-8") if key_env else b""
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        return False, "result_hmac_key_missing_or_weak"
    if required and not signature:
        return False, "result_hmac_missing"
    if signature:
        if not key:
            return False, "result_hmac_key_missing"
        expected = _sign(key, _response_signature_text(required, key_env, request_hash, response_hash))
        if not hmac.compare_digest(signature, expected):
            return False, "result_hmac_mismatch"
    return True, "result_integrity_valid"


def payload_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



def _without_integrity(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if str(key) != "integrity"}


def _sign(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _request_signature_text(required: bool, key_env: str, payload_hash: str) -> str:
    return f"request|{INTEGRITY_ALGORITHM}|{required}|{key_env}|{payload_hash}"


def _response_signature_text(required: bool, key_env: str, request_hash: str, response_hash: str) -> str:
    return f"response|{INTEGRITY_ALGORITHM}|{required}|{key_env}|{request_hash}|{response_hash}"


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
