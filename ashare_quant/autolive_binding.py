from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import mapping_sha256
from .autolive_integrity import DEFAULT_INTEGRITY_KEY_ENV, MIN_HMAC_KEY_BYTES
from .config import project_path
from .io_utils import atomic_to_csv


ACCOUNT_BINDING_FILE = "auto_live_account_binding.json"
ACCOUNT_BINDING_AUDIT_FILE = "auto_live_account_binding_audit.csv"
AUTO_LIVE_ACCOUNT_BINDING_AUDIT_COLUMNS = [
    "check", "passed", "value", "threshold", "severity", "next_action", "message",
]
BINDING_SCHEMA_VERSION = 1


def bind_auto_live_account(config: dict[str, Any], *, confirm: bool = False) -> dict[str, Path]:
    output_dir = _output_dir(config)
    if not confirm:
        raise RuntimeError("account binding requires explicit --confirm")
    snapshot = _read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    broker_audit = _read_csv(output_dir / "auto_live_broker_account_audit.csv")
    if len(snapshot) != 1:
        raise RuntimeError("account binding requires exactly one current broker account snapshot")
    row = snapshot.iloc[0]
    environment = str(row.get("environment", "")).strip().lower()
    account_id = str(row.get("account_id", "")).strip()
    broker = str(row.get("broker", "")).strip()
    if environment != "live" or not account_id or not broker:
        raise RuntimeError("account binding requires environment=live, broker, and a stable masked account_id")
    if not _all_passed(broker_audit):
        raise RuntimeError("account binding requires every broker account reconciliation check to pass")
    current_hash = _config_hash(config)
    if str(row.get("auto_live_config_sha256", "")).strip() != current_hash:
        raise RuntimeError("broker account snapshot does not match the current auto-live configuration")

    settings = _settings(config)
    key = _key(settings["key_env"])
    if settings["require_hmac"] and len(key) < MIN_HMAC_KEY_BYTES:
        raise RuntimeError(
            f"account binding HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes "
            f"in {settings['key_env']}"
        )
    payload: dict[str, Any] = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "binding_id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "active": True,
        "environment": "live",
        "broker": broker,
        "account_id": account_id,
        "snapshot_run_id": str(row.get("snapshot_run_id", "")).strip(),
        "auto_live_config_sha256": current_hash,
        "state_db_path": _state_db_path(config),
        "hmac_required": bool(settings["require_hmac"]),
        "hmac_key_env": str(settings["key_env"]),
    }
    payload_hash = _payload_hash(payload)
    payload["payload_sha256"] = payload_hash
    payload["hmac_sha256"] = _sign(key, payload_hash) if key else ""
    binding_path = output_dir / ACCOUNT_BINDING_FILE
    _atomic_json(binding_path, payload)
    paths = write_auto_live_account_binding_audit(config)
    if not _all_passed(_read_csv(paths[ACCOUNT_BINDING_AUDIT_FILE])):
        raise RuntimeError("new account binding failed its post-write integrity audit")
    return paths


def unbind_auto_live_account(config: dict[str, Any], *, confirm: bool = False) -> dict[str, Path]:
    if not confirm:
        raise RuntimeError("account unbinding requires explicit --confirm")
    path = _output_dir(config) / ACCOUNT_BINDING_FILE
    if path.exists():
        path.unlink()
    return write_auto_live_account_binding_audit(config)


def write_auto_live_account_binding_audit(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = _output_dir(config)
    binding_path = output_dir / ACCOUNT_BINDING_FILE
    audit_path = output_dir / ACCOUNT_BINDING_AUDIT_FILE
    atomic_to_csv(build_auto_live_account_binding_audit(config), audit_path, index=False, encoding="utf-8-sig")
    paths = {ACCOUNT_BINDING_AUDIT_FILE: audit_path}
    if binding_path.exists():
        paths[ACCOUNT_BINDING_FILE] = binding_path
    return paths


def build_auto_live_account_binding_audit(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = _output_dir(config)
    binding_path = output_dir / ACCOUNT_BINDING_FILE
    snapshot = _read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    broker_audit = _read_csv(output_dir / "auto_live_broker_account_audit.csv")
    settings = _settings(config)
    required = bool(settings["required"])
    payload, read_error = _read_json(binding_path)
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, action: str, message: str) -> None:
        rows.append({
            "check": check, "passed": bool(passed), "value": value, "threshold": threshold,
            "severity": "ok" if passed else "account_binding_blocker",
            "next_action": "" if passed else action, "message": message,
        })

    exists = binding_path.exists()
    add("account_binding_file_present", exists or not required, str(binding_path) if exists else "missing", "present when required", "先读取并对账真实券商账户，再显式执行 auto-live-bind-account --confirm。", "正式自动实盘必须把本地状态库绑定到明确的真实券商账户。")
    add("account_binding_json_readable", (not exists and not required) or (exists and not read_error), read_error or ("readable" if exists else "optional"), "readable JSON", "从可信备份恢复绑定文件，或重新完成账户绑定。", "绑定文件必须可完整解析。")
    if not exists or read_error:
        return pd.DataFrame(rows, columns=AUTO_LIVE_ACCOUNT_BINDING_AUDIT_COLUMNS)

    expected_keys = {
        "schema_version", "binding_id", "created_at", "active", "environment", "broker",
        "account_id", "snapshot_run_id", "auto_live_config_sha256", "state_db_path",
        "hmac_required", "hmac_key_env", "payload_sha256", "hmac_sha256",
    }
    missing = sorted(expected_keys - set(payload))
    version = pd.to_numeric(payload.get("schema_version"), errors="coerce")
    schema_ok = not missing and not pd.isna(version) and int(version) == BINDING_SCHEMA_VERSION
    add("account_binding_schema_valid", schema_ok, ",".join(missing) if missing else payload.get("schema_version"), BINDING_SCHEMA_VERSION, "重新生成账户绑定文件。", "绑定文件字段和版本必须完整。")
    add("account_binding_active", _bool(payload.get("active")), payload.get("active"), True, "重新显式绑定账户。", "已停用绑定不得用于订单提交。")
    add("account_binding_environment_live", str(payload.get("environment", "")).lower() == "live", payload.get("environment", "missing"), "live", "仅绑定真实券商环境快照。", "模拟或测试账户不能成为自动实盘绑定。")
    config_hash = _config_hash(config)
    add("account_binding_config_match", str(payload.get("auto_live_config_sha256", "")) == config_hash, str(payload.get("auto_live_config_sha256", ""))[:16], config_hash[:16], "配置变更后重新执行账户读取、对账和绑定。", "账户绑定必须对应当前自动实盘配置。")
    db_path = _state_db_path(config)
    add("account_binding_state_db_match", _same_path(str(payload.get("state_db_path", "")), db_path), payload.get("state_db_path", "missing"), db_path, "对当前状态库重新完成账户绑定。", "防止把一个账户的本地状态误用于另一个状态库。")

    snapshot_ok = len(snapshot) == 1
    snapshot_row = snapshot.iloc[0] if snapshot_ok else pd.Series(dtype=object)
    broker_match = snapshot_ok and str(snapshot_row.get("broker", "")).strip() == str(payload.get("broker", "")).strip()
    account_match = snapshot_ok and str(snapshot_row.get("account_id", "")).strip() == str(payload.get("account_id", "")).strip()
    environment_match = snapshot_ok and str(snapshot_row.get("environment", "")).strip().lower() == "live"
    add("account_binding_broker_match", broker_match, str(snapshot_row.get("broker", "missing")) if snapshot_ok else "missing", payload.get("broker", "missing"), "重新读取预期券商账户并复核绑定。", "最新券商快照必须来自已绑定券商。")
    add("account_binding_account_id_match", account_match, "matched" if account_match else "missing_or_mismatch", "stable masked account_id matches", "停止提交并核对当前登录账户，然后重新绑定。", "账户号变化时必须阻断，避免下单到错误账户。")
    add("account_binding_snapshot_live", environment_match, snapshot_row.get("environment", "missing") if snapshot_ok else "missing", "live", "重新读取真实券商账户快照。", "最新快照必须明确来自真实环境。")
    add("account_binding_broker_audit_passed", _all_passed(broker_audit), f"{_passed_count(broker_audit)}/{len(broker_audit)}", "all broker account checks passed", "先修复券商账户和本地状态对账。", "账户绑定不能替代每次提交前的实时账户对账。")

    clean = {key: value for key, value in payload.items() if key not in {"payload_sha256", "hmac_sha256"}}
    actual_hash = _payload_hash(clean)
    recorded_hash = str(payload.get("payload_sha256", ""))
    hash_ok = bool(recorded_hash) and hmac.compare_digest(recorded_hash, actual_hash)
    add("account_binding_payload_hash_valid", hash_ok, actual_hash[:16], recorded_hash[:16] or "missing", "重新生成账户绑定文件，禁止手工修改。", "绑定载荷哈希必须匹配。")
    required_hmac = bool(settings["require_hmac"])
    key_env_match = str(payload.get("hmac_key_env", "")) == str(settings["key_env"])
    required_match = _bool(payload.get("hmac_required")) == required_hmac
    key = _key(settings["key_env"])
    signature = str(payload.get("hmac_sha256", ""))
    signature_ok = bool(key) and bool(signature) and hash_ok and hmac.compare_digest(signature, _sign(key, actual_hash))
    hmac_ok = key_env_match and required_match and (signature_ok if required_hmac or signature else True)
    add("account_binding_hmac_valid", hmac_ok, f"required={required_hmac};key_bytes={len(key)};signed={bool(signature)}", "valid signature when required/present", f"在 {settings['key_env']} 配置原绑定密钥，或重新执行绑定。", "HMAC 防止通过手工改写绑定文件切换账户。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_ACCOUNT_BINDING_AUDIT_COLUMNS)


def auto_live_account_binding_status(config: dict[str, Any]) -> tuple[bool, str, bool]:
    settings = _settings(config)
    audit = build_auto_live_account_binding_audit(config)
    passed = bool(not audit.empty and audit["passed"].astype(bool).all())
    failed = audit.loc[~audit["passed"].astype(bool), "check"].astype(str).tolist() if not audit.empty else ["audit_missing"]
    return passed, f"required={settings['required']};passed={_passed_count(audit)}/{len(audit)};failed={';'.join(failed)}", bool(settings["required"])


def _settings(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    binding = auto.get("account_binding", {}) if isinstance(auto.get("account_binding", {}), dict) else {}
    adapter = auto.get("command_adapter", {}) if isinstance(auto.get("command_adapter", {}), dict) else {}
    return {
        "required": _bool(binding.get("required", False)),
        "require_hmac": _bool(binding.get("require_hmac", adapter.get("require_hmac_integrity", False))),
        "key_env": str(binding.get("hmac_key_env", adapter.get("integrity_key_env", DEFAULT_INTEGRITY_KEY_ENV)) or DEFAULT_INTEGRITY_KEY_ENV).strip(),
    }


def _config_hash(config: dict[str, Any]) -> str:
    return mapping_sha256(config.get("auto_live", {}))


def _output_dir(config: dict[str, Any]) -> Path:
    path = project_path(config["paths"]["output_dir"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_db_path(config: dict[str, Any]) -> str:
    value = config.get("paths", {}).get("state_db", "data/state.sqlite3")
    return str(project_path(value).resolve())


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return pd.DataFrame()


def _all_passed(frame: pd.DataFrame) -> bool:
    return bool(not frame.empty and "passed" in frame.columns and frame["passed"].map(_bool).all())


def _passed_count(frame: pd.DataFrame) -> int:
    return int(frame["passed"].map(_bool).sum()) if not frame.empty and "passed" in frame.columns else 0


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    return (payload, "") if isinstance(payload, dict) else ({}, "binding root must be an object")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sign(key: bytes, payload_hash: str) -> str:
    return hmac.new(key, f"account_binding|{payload_hash}".encode("utf-8"), hashlib.sha256).hexdigest()


def _key(key_env: object) -> bytes:
    return os.environ.get(str(key_env), "").encode("utf-8") if str(key_env) else b""


def _same_path(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except Exception:
        return left == right


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
