from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


ROLLOUT_APPROVAL_FILE = "auto_live_rollout_approval.json"
ROLLOUT_AUDIT_FILE = "auto_live_rollout_audit.csv"
ROLLOUT_HISTORY_FILE = "auto_live_rollout_history.csv"
ROLLOUT_AUDIT_COLUMNS = [
    "check", "passed", "value", "threshold", "severity", "next_action", "message",
]
ROLLOUT_HISTORY_COLUMNS = [
    "action", "approval_id", "stage", "account_id", "auto_live_config_sha256",
    "expires_at", "reason", "generated_at",
]
SCHEMA_VERSION = 1
VALID_STAGES = {"canary", "limited", "full"}


def approve_auto_live_rollout(
    config: dict[str, Any],
    *,
    stage: str,
    confirm: bool = False,
    reason: str = "operator_approved",
) -> dict[str, Path]:
    if not confirm:
        raise RuntimeError("rollout approval requires explicit --confirm")
    settings = rollout_settings(config)
    requested = str(stage).strip().lower()
    if requested not in VALID_STAGES:
        raise RuntimeError(f"invalid rollout stage: {requested}")
    if requested != settings["stage"]:
        raise RuntimeError(f"requested stage {requested} does not match configured stage {settings['stage']}")
    output_dir = _output_dir(config)
    evidence_errors = _approval_evidence_errors(output_dir)
    if evidence_errors:
        raise RuntimeError(f"rollout approval evidence incomplete: {';'.join(evidence_errors)}")
    binding = _read_json(output_dir / "auto_live_account_binding.json")
    if not binding or str(binding.get("environment", "")).lower() != "live" or not _bool(binding.get("active")):
        raise RuntimeError("rollout approval requires an active live account binding")
    key = _key(settings["key_env"])
    if settings["require_hmac"] and len(key) < MIN_HMAC_KEY_BYTES:
        raise RuntimeError(
            f"rollout HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes "
            f"in {settings['key_env']}"
        )
    now = datetime.now(timezone.utc)
    approval_id = now.strftime("%Y%m%d%H%M%S%f")
    limits = settings["limits"]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "approval_id": approval_id,
        "active": True,
        "stage": requested,
        "broker": str(binding.get("broker", "")),
        "account_id": str(binding.get("account_id", "")),
        "account_binding_id": str(binding.get("binding_id", "")),
        "auto_live_config_sha256": _config_hash(config),
        "issued_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(hours=float(settings["ttl_hours"]))).isoformat(timespec="seconds"),
        "limits": limits,
        "reason": str(reason),
        "hmac_required": bool(settings["require_hmac"]),
        "hmac_key_env": str(settings["key_env"]),
    }
    payload_hash = _payload_hash(payload)
    payload["payload_sha256"] = payload_hash
    payload["hmac_sha256"] = _sign(key, payload_hash) if key else ""
    approval_path = output_dir / ROLLOUT_APPROVAL_FILE
    _atomic_json(approval_path, payload)
    history_path = _append_history(config, {
        "action": "approve", "approval_id": approval_id, "stage": requested,
        "account_id": str(binding.get("account_id", "")),
        "auto_live_config_sha256": _config_hash(config),
        "expires_at": payload["expires_at"], "reason": str(reason),
        "generated_at": now.isoformat(timespec="seconds"),
    })
    paths = write_auto_live_rollout_audit(config)
    paths[ROLLOUT_HISTORY_FILE] = history_path
    if not _audit_passed(paths[ROLLOUT_AUDIT_FILE]):
        raise RuntimeError("new rollout approval failed post-write verification")
    return paths


def revoke_auto_live_rollout(
    config: dict[str, Any],
    *,
    confirm: bool = False,
    reason: str = "operator_revoked",
) -> dict[str, Path]:
    if not confirm:
        raise RuntimeError("rollout revocation requires explicit --confirm")
    output_dir = _output_dir(config)
    approval_path = output_dir / ROLLOUT_APPROVAL_FILE
    payload = _read_json(approval_path)
    if approval_path.exists():
        approval_path.unlink()
    history_path = _append_history(config, {
        "action": "revoke", "approval_id": str(payload.get("approval_id", "")),
        "stage": str(payload.get("stage", "")), "account_id": str(payload.get("account_id", "")),
        "auto_live_config_sha256": str(payload.get("auto_live_config_sha256", "")),
        "expires_at": str(payload.get("expires_at", "")), "reason": str(reason),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    paths = write_auto_live_rollout_audit(config)
    paths[ROLLOUT_HISTORY_FILE] = history_path
    return paths


def write_auto_live_rollout_audit(
    config: dict[str, Any],
    *,
    tickets: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> dict[str, Path]:
    output_dir = _output_dir(config)
    audit_path = output_dir / ROLLOUT_AUDIT_FILE
    history_path = output_dir / ROLLOUT_HISTORY_FILE
    if not history_path.exists():
        atomic_to_csv(pd.DataFrame(columns=ROLLOUT_HISTORY_COLUMNS), history_path, index=False, encoding="utf-8-sig")
    audit = build_auto_live_rollout_audit(config, tickets=tickets, now=now)
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    paths = {ROLLOUT_AUDIT_FILE: audit_path, ROLLOUT_HISTORY_FILE: history_path}
    approval_path = output_dir / ROLLOUT_APPROVAL_FILE
    if approval_path.exists():
        paths[ROLLOUT_APPROVAL_FILE] = approval_path
    return paths


def build_auto_live_rollout_audit(
    config: dict[str, Any],
    *,
    tickets: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    settings = rollout_settings(config)
    required = bool(settings["required"])
    stage = str(settings["stage"])
    output_dir = _output_dir(config)
    approval_path = output_dir / ROLLOUT_APPROVAL_FILE
    approval = _read_json(approval_path)
    binding = _read_json(output_dir / "auto_live_account_binding.json")
    frame = tickets.copy() if tickets is not None else pd.DataFrame()
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, action: str, message: str) -> None:
        rows.append({
            "check": check, "passed": bool(passed), "value": value, "threshold": threshold,
            "severity": "ok" if passed else "rollout_blocker",
            "next_action": "" if passed else action, "message": message,
        })

    stage_ok = stage in VALID_STAGES
    add("rollout_stage_valid", stage_ok or not required, stage, ",".join(sorted(VALID_STAGES)), "配置 canary、limited 或 full 阶段。", "正式自动实盘必须处于明确发布阶段。")
    exists = approval_path.is_file()
    add("rollout_approval_present", exists or not required, str(approval_path) if exists else "missing", "signed approval present when required", "完成真实账户、回放和告警认证后显式批准当前阶段。", "真实订单提交必须有短期、签名的投产授权。")
    if not exists:
        _add_limit_checks(add, frame, settings["limits"], required)
        return pd.DataFrame(rows, columns=ROLLOUT_AUDIT_COLUMNS)

    schema_value = pd.to_numeric(approval.get("schema_version"), errors="coerce")
    schema_ok = not pd.isna(schema_value) and int(schema_value) == SCHEMA_VERSION
    add("rollout_approval_schema_valid", schema_ok, approval.get("schema_version", "missing"), SCHEMA_VERSION, "重新签发投产授权。", "投产授权版本必须受支持。")
    add("rollout_approval_active", _bool(approval.get("active")), approval.get("active", "missing"), True, "重新签发投产授权。", "停用授权不能用于提交。")
    add("rollout_approval_stage_match", str(approval.get("stage", "")).lower() == stage, approval.get("stage", "missing"), stage, "为当前配置阶段重新签发授权。", "授权阶段必须与配置一致。")
    config_hash = _config_hash(config)
    add("rollout_approval_config_match", str(approval.get("auto_live_config_sha256", "")) == config_hash, str(approval.get("auto_live_config_sha256", ""))[:16], config_hash[:16], "配置变化后重新完成认证和授权。", "授权必须绑定当前自动实盘配置。")
    binding_match = bool(binding) and str(approval.get("account_binding_id", "")) == str(binding.get("binding_id", "")) and str(approval.get("account_id", "")) == str(binding.get("account_id", ""))
    add("rollout_approval_account_match", binding_match, "matched" if binding_match else "missing_or_mismatch", "current live account binding", "账户切换后重新认证并签发授权。", "Canary 授权不能跨券商账户复用。")
    current = pd.Timestamp(now or datetime.now(timezone.utc))
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    expires = pd.to_datetime(approval.get("expires_at", ""), errors="coerce", utc=True)
    not_expired = not pd.isna(expires) and current.tz_convert("UTC") <= expires
    remaining = float((expires - current.tz_convert("UTC")).total_seconds()) if not pd.isna(expires) else float("-inf")
    add("rollout_approval_not_expired", not_expired, f"remaining_seconds={remaining:.1f}", ">=0", "重新检查系统状态并签发短期授权。", "投产授权到期后必须自动失效。")
    clean = {k: v for k, v in approval.items() if k not in {"payload_sha256", "hmac_sha256"}}
    actual_hash = _payload_hash(clean)
    recorded_hash = str(approval.get("payload_sha256", ""))
    payload_ok = bool(recorded_hash) and hmac.compare_digest(actual_hash, recorded_hash)
    add("rollout_approval_hash_valid", payload_ok, actual_hash[:16], recorded_hash[:16] or "missing", "禁止手工修改授权文件，重新签发。", "授权载荷哈希必须匹配。")
    key = _key(settings["key_env"])
    signature = str(approval.get("hmac_sha256", ""))
    signature_ok = bool(key) and bool(signature) and payload_ok and hmac.compare_digest(signature, _sign(key, actual_hash))
    hmac_ok = signature_ok if settings["require_hmac"] or signature else True
    add("rollout_approval_hmac_valid", hmac_ok, f"required={settings['require_hmac']};key_bytes={len(key)};signed={bool(signature)}", "valid signature", f"在 {settings['key_env']} 使用原密钥验证或重新签发。", "HMAC 防止放大限额或替换账户。")
    approved_limits = approval.get("limits", {}) if isinstance(approval.get("limits"), dict) else {}
    add("rollout_approval_limits_match", approved_limits == settings["limits"], _safe_json(approved_limits), _safe_json(settings["limits"]), "配置限额变化后重新签发授权。", "签名限额必须与当前阶段限额完全一致。")
    _add_limit_checks(add, frame, settings["limits"], required)
    return pd.DataFrame(rows, columns=ROLLOUT_AUDIT_COLUMNS)


def auto_live_rollout_status(config: dict[str, Any], tickets: pd.DataFrame | None = None) -> tuple[bool, str, bool]:
    settings = rollout_settings(config)
    audit = build_auto_live_rollout_audit(config, tickets=tickets)
    passed = bool(not audit.empty and audit["passed"].astype(bool).all())
    failed = audit.loc[~audit["passed"].astype(bool), "check"].astype(str).tolist()
    return passed, f"required={settings['required']};stage={settings['stage']};passed={int(audit['passed'].sum())}/{len(audit)};failed={';'.join(failed)}", bool(settings["required"])


def rollout_settings(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    cfg = auto.get("rollout", {}) if isinstance(auto.get("rollout", {}), dict) else {}
    stage = str(cfg.get("stage", "disabled") or "disabled").strip().lower()
    stages = cfg.get("stages", {}) if isinstance(cfg.get("stages", {}), dict) else {}
    defaults = {
        "canary": {"max_orders": 1, "max_single_order_amount": 20000.0, "max_buy_amount": 20000.0, "max_gross_amount": 20000.0, "min_cash_buffer_amount": 500.0, "min_cash_buffer_ratio": 0.10},
        "limited": {"max_orders": 10, "max_single_order_amount": 50000.0, "max_buy_amount": 200000.0, "max_gross_amount": 200000.0, "min_cash_buffer_amount": 2000.0, "min_cash_buffer_ratio": 0.10},
        "full": {"max_orders": 50, "max_single_order_amount": 100000.0, "max_buy_amount": 500000.0, "max_gross_amount": 1000000.0, "min_cash_buffer_amount": 10000.0, "min_cash_buffer_ratio": 0.05},
    }
    raw = stages.get(stage, {}) if isinstance(stages.get(stage, {}), dict) else {}
    base = defaults.get(
        stage,
        {
            "max_orders": 0,
            "max_single_order_amount": 0.0,
            "max_buy_amount": 0.0,
            "max_gross_amount": 0.0,
            "min_cash_buffer_amount": 10000.0,
            "min_cash_buffer_ratio": 0.01,
        },
    )
    limits = {
        "max_orders": max(_integer(raw.get("max_orders", base["max_orders"])), 0),
        "max_single_order_amount": max(_float(raw.get("max_single_order_amount", base["max_single_order_amount"])), 0.0),
        "max_buy_amount": max(_float(raw.get("max_buy_amount", base["max_buy_amount"])), 0.0),
        "max_gross_amount": max(_float(raw.get("max_gross_amount", base["max_gross_amount"])), 0.0),
        "min_cash_buffer_amount": max(_float(raw.get("min_cash_buffer_amount", base["min_cash_buffer_amount"])), 0.0),
        "min_cash_buffer_ratio": max(_float(raw.get("min_cash_buffer_ratio", base["min_cash_buffer_ratio"])), 0.0),
    }
    return {
        "required": _bool(cfg.get("required_before_submit", False)),
        "stage": stage,
        "ttl_hours": max(_float(cfg.get("approval_ttl_hours", 24)), 0.1),
        "require_hmac": _bool(cfg.get("require_hmac", False)),
        "key_env": str(cfg.get("hmac_key_env", DEFAULT_INTEGRITY_KEY_ENV) or DEFAULT_INTEGRITY_KEY_ENV),
        "limits": limits,
    }


def _add_limit_checks(add, tickets: pd.DataFrame, limits: dict[str, object], enforced: bool) -> None:
    amounts = _ticket_amounts(tickets)
    sides = tickets["side"].astype(str).str.lower() if not tickets.empty and "side" in tickets.columns else pd.Series(dtype=str)
    order_count = len(tickets)
    single = float(amounts.max()) if not amounts.empty else 0.0
    gross = float(amounts.sum()) if not amounts.empty else 0.0
    buy = float(amounts[sides.eq("buy").to_numpy()].sum()) if len(amounts) == len(sides) and not amounts.empty else 0.0
    specs = [
        ("rollout_order_count_limit", order_count, int(limits["max_orders"])),
        ("rollout_single_order_limit", single, float(limits["max_single_order_amount"])),
        ("rollout_buy_amount_limit", buy, float(limits["max_buy_amount"])),
        ("rollout_gross_amount_limit", gross, float(limits["max_gross_amount"])),
    ]
    for check, value, threshold in specs:
        passed = (not enforced) or value <= threshold
        add(check, passed, value, f"<= {threshold:g}", "缩小当前批次或回退发布阶段，禁止扩大签名授权。", "分阶段投产限额必须在提交锁内再次复核。")


def _ticket_amounts(tickets: pd.DataFrame) -> pd.Series:
    if tickets.empty:
        return pd.Series(dtype=float)
    if "estimated_amount" in tickets.columns:
        values = pd.to_numeric(tickets["estimated_amount"], errors="coerce")
    elif "quantity" in tickets.columns and ("limit_price" in tickets.columns or "price" in tickets.columns):
        quantity = pd.to_numeric(tickets["quantity"], errors="coerce")
        price_source = tickets["limit_price"] if "limit_price" in tickets.columns else tickets["price"]
        price = pd.to_numeric(price_source, errors="coerce")
        values = quantity * price
    else:
        values = pd.Series(float("inf"), index=tickets.index, dtype=float)
    return values.fillna(float("inf")).abs()


def _approval_evidence_errors(output_dir: Path) -> list[str]:
    errors: list[str] = []
    for name in ["auto_live_account_binding_audit.csv", "auto_live_alert_audit.csv", "auto_live_adapter_file_audit.csv"]:
        if not _all_passed(_read_csv(output_dir / name)):
            errors.append(name)
    replay = _read_csv(output_dir / "auto_live_adapter_replay_summary.csv")
    if replay.empty or str(replay.iloc[-1].get("status", "")).lower() != "certified_live":
        errors.append("auto_live_adapter_replay_summary.csv")
    journal = _read_csv(output_dir / "auto_live_journal_summary.csv")
    if journal.empty or str(journal.iloc[-1].get("status", "")).lower() not in {"verified", "empty_ready"}:
        errors.append("auto_live_journal_summary.csv")
    intent = _read_csv(output_dir / "auto_live_submission_intent.csv")
    if not intent.empty and str(intent.iloc[-1].get("state", "")).lower() in {"submitting", "submission_uncertain"}:
        errors.append("unresolved_submission_intent")
    return errors


def _config_hash(config: dict[str, Any]) -> str:
    return mapping_sha256(config.get("auto_live", {}))


def _output_dir(config: dict[str, Any]) -> Path:
    path = project_path(config["paths"]["output_dir"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _append_history(config: dict[str, Any], row: dict[str, object]) -> Path:
    path = _output_dir(config) / ROLLOUT_HISTORY_FILE
    existing = _read_csv(path)
    atomic_to_csv(pd.concat([existing, pd.DataFrame([row], columns=ROLLOUT_HISTORY_COLUMNS)], ignore_index=True), path, index=False, encoding="utf-8-sig")
    return path


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False)
    except Exception:
        return pd.DataFrame()


def _all_passed(frame: pd.DataFrame) -> bool:
    return bool(not frame.empty and "passed" in frame.columns and frame["passed"].map(_bool).all())


def _audit_passed(path: Path) -> bool:
    return _all_passed(_read_csv(path))


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_safe_json(payload).encode("utf-8")).hexdigest()


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sign(key: bytes, payload_hash: str) -> str:
    return hmac.new(key, f"auto_live_rollout|{payload_hash}".encode("utf-8"), hashlib.sha256).hexdigest()


def _key(value: object) -> bytes:
    return os.environ.get(str(value), "").encode("utf-8") if str(value) else b""


def _integer(value: object) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return int(parsed) if not pd.isna(parsed) else 0


def _float(value: object) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(parsed) if not pd.isna(parsed) else 0.0


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
