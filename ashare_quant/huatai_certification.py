from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .autolive import auto_live_config_sha256
from .config import project_path
from .huatai_canary import HARD_MAX_CANARY_AMOUNT, certification_hmac_sha256
from .io_utils import atomic_to_csv


ARMING_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]
SANDBOX_REQUIRED_CHECKS = (
    "action_confirmation_required",
    "unattended_service_disabled",
    "canary_hard_limits",
    "canary_bridge_config",
    "certification_path_match",
    "command_uses_canary_bridge",
    "canary_bridge_hash_pinned",
    "certification_hmac_ready",
    "buy_form_preview_current",
    "sell_form_preview_current",
    "orders_empty_monitor_current",
    "trades_empty_monitor_current",
    "cancels_empty_monitor_current",
    "live_account_snapshot_current",
)


def build_huatai_canary_arming_audit(
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> pd.DataFrame:
    current = _utc_now(now)
    auto = _mapping(config.get("auto_live"))
    cert = _mapping(auto.get("desktop_canary_certification"))
    evidence = _mapping(cert.get("evidence"))
    rows: list[dict[str, Any]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "message": message,
            }
        )

    add(
        "main_submit_switch_enabled",
        bool(auto.get("allow_broker_submit", False)),
        bool(auto.get("allow_broker_submit", False)),
        True,
        "武装票据必须绑定最终提交配置；票据缺失时桥接器仍保持失败关闭。",
    )
    add(
        "main_emergency_stop_inactive",
        not bool(auto.get("emergency_stop", True)),
        bool(auto.get("emergency_stop", True)),
        False,
        "武装票据必须绑定最终停机状态，其他门禁和一次性确认仍继续生效。",
    )
    confirmation = _mapping(auto.get("action_confirmation"))
    add(
        "action_confirmation_required",
        bool(confirmation.get("required_before_submit", False)),
        bool(confirmation.get("required_before_submit", False)),
        True,
        "首笔 Canary 必须要求绑定订单的短时单次确认。",
    )
    service = _mapping(auto.get("service"))
    service_disabled = not bool(service.get("enabled", False)) and not bool(service.get("allow_submit", False))
    add(
        "unattended_service_disabled",
        service_disabled,
        f"enabled={bool(service.get('enabled', False))};allow_submit={bool(service.get('allow_submit', False))}",
        "both false",
        "armed 阶段禁止无人值守服务提交。",
    )

    rollout = _mapping(auto.get("rollout"))
    canary = _mapping(_mapping(rollout.get("stages")).get("canary"))
    max_orders = _int(canary.get("max_orders"))
    max_amount = _float(canary.get("max_single_order_amount"))
    limits_ok = max_orders == 1 and max_amount is not None and 0 < max_amount <= float(HARD_MAX_CANARY_AMOUNT)
    add(
        "canary_hard_limits",
        limits_ok,
        f"max_orders={max_orders};max_single_order_amount={max_amount}",
        "1 order and <=1000 CNY",
        "首笔 Canary 的主系统限额不得超过桥接器硬上限。",
    )

    bridge_path = _path(cert.get("bridge_config"))
    bridge = _load_mapping_file(bridge_path)
    huatai = _mapping(bridge.get("huatai", bridge))
    policy = _mapping(huatai.get("desktop_canary"))
    bridge_ok = (
        bridge_path is not None
        and str(huatai.get("live_backend", "")).lower() == "desktop_canary"
        and bool(policy.get("enabled", False))
        and bool(policy.get("allow_submit", False))
        and str(policy.get("required_certification_stage", "")).lower() == "armed"
    )
    add(
        "canary_bridge_config",
        bridge_ok,
        str(bridge_path or "missing"),
        "desktop_canary enabled with required stage armed",
        "独立桥接配置必须声明首单阶段，不能复用只读配置。",
    )
    main_cert_path = _path(cert.get("certification_path"))
    bridge_cert_path = _path(policy.get("certification_path"))
    cert_paths_match = (
        main_cert_path is not None
        and bridge_cert_path is not None
        and main_cert_path.resolve() == bridge_cert_path.resolve()
    )
    add(
        "certification_path_match",
        cert_paths_match,
        f"main={main_cert_path or 'missing'};bridge={bridge_cert_path or 'missing'}",
        "same resolved path",
        "主系统签发路径必须与桥接器验证路径完全一致。",
    )
    adapter = _mapping(auto.get("command_adapter"))
    command = adapter.get("command", []) if isinstance(adapter.get("command", []), list) else []
    command_bridge = _path(command[-1]) if command else None
    command_matches = bridge_path is not None and command_bridge is not None and command_bridge.resolve() == bridge_path.resolve()
    add(
        "command_uses_canary_bridge",
        command_matches,
        str(command_bridge or "missing"),
        str(bridge_path or "missing"),
        "主命令适配器必须调用本次审计的 Canary 桥接配置。",
    )
    pins = adapter.get("pinned_files", []) if isinstance(adapter.get("pinned_files", []), list) else []
    bridge_pin = next(
        (
            item
            for item in pins
            if isinstance(item, dict)
            and bridge_path is not None
            and _path(item.get("path")) is not None
            and _path(item.get("path")).resolve() == bridge_path.resolve()
        ),
        {},
    )
    actual_bridge_hash = _file_sha256(bridge_path)
    pin_matches = bool(actual_bridge_hash) and str(bridge_pin.get("sha256", "")) == actual_bridge_hash
    add(
        "canary_bridge_hash_pinned",
        pin_matches,
        f"expected={str(bridge_pin.get('sha256', ''))};actual={actual_bridge_hash}",
        "matching SHA-256",
        "Canary 桥接配置的任何字节变化都必须重新复核和钉扎。",
    )
    hmac_env = str(policy.get("hmac_key_env", "ASHARE_QUANT_ADAPTER_HMAC_KEY"))
    hmac_key = os.environ.get(hmac_env, "").encode("utf-8")
    hmac_ready = bool(policy.get("require_hmac", False)) and len(hmac_key) >= 32
    add(
        "certification_hmac_ready",
        hmac_ready,
        f"required={bool(policy.get('require_hmac', False))};key_env={hmac_env};key_bytes={len(hmac_key)}",
        "required and >=32 bytes",
        "武装票据必须使用已有适配器 HMAC 密钥签名。",
    )

    preview_max_age = int(cert.get("preview_max_age_seconds", 604800))
    for side, key in (("buy", "buy_preview"), ("sell", "sell_preview")):
        payload, path = _load_evidence(evidence.get(key))
        age = _age_seconds(payload, current)
        passed = (
            payload.get("status") == "preview_verified"
            and payload.get("environment") == "live"
            and payload.get("side") == side
            and payload.get("form_readback_verified") is True
            and payload.get("form_restore_verified") is True
            and payload.get("submit_invoked") is False
            and payload.get("cancel_invoked") is False
            and age is not None
            and 0 <= age <= preview_max_age
        )
        add(
            f"{side}_form_preview_current",
            passed,
            f"path={path or 'missing'};age_seconds={age}",
            f"verified live preview and restore <= {preview_max_age}s and no submit",
            "买卖表单必须在真实客户端完成字段回读及原值恢复回读，且预览从未点击提交。",
        )

    monitor_max_age = int(cert.get("monitor_max_age_seconds", 86400))
    for name, key in (("orders", "orders_monitor"), ("trades", "trades_monitor"), ("cancels", "cancels_monitor")):
        payload, path = _load_evidence(evidence.get(key))
        age = _age_seconds(payload, current)
        passed = (
            payload.get("status") == "empty_verified"
            and payload.get("environment") == "live"
            and payload.get("submit_invoked") is False
            and payload.get("cancel_invoked") is False
            and bool(payload.get("image_sha256"))
            and age is not None
            and 0 <= age <= monitor_max_age
        )
        add(
            f"{name}_empty_monitor_current",
            passed,
            f"path={path or 'missing'};age_seconds={age}",
            f"empty_verified <= {monitor_max_age}s",
            "首单前必须确认客户端没有遗留委托、成交或可撤记录。",
        )

    snapshot, snapshot_path = _load_evidence(evidence.get("account_snapshot"))
    snapshot_age = _age_seconds(snapshot, current)
    account = _mapping(snapshot.get("account"))
    snapshot_max_age = int(cert.get("account_snapshot_max_age_seconds", 300))
    snapshot_ok = (
        snapshot.get("status") == "ok"
        and snapshot.get("environment") == "live"
        and bool(account.get("account_id"))
        and _float(account.get("available_cash")) is not None
        and snapshot_age is not None
        and 0 <= snapshot_age <= snapshot_max_age
    )
    add(
        "live_account_snapshot_current",
        snapshot_ok,
        f"path={snapshot_path or 'missing'};account_id={account.get('account_id', '')};age_seconds={snapshot_age}",
        f"live snapshot <= {snapshot_max_age}s",
        "武装前必须重新读取当前真实账户，旧快照不能复用。",
    )

    sandbox, sandbox_path = _load_evidence(evidence.get("sandbox_summary"))
    sandbox_hash = str(sandbox.get("auto_live_config_sha256", ""))
    current_hash = auto_live_config_sha256(config)
    sandbox_certified = (
        sandbox.get("status") == "sandbox_certified"
        and sandbox.get("buy_form_preview") is True
        and sandbox.get("sell_form_preview") is True
        and sandbox.get("orders_empty_verified") is True
        and sandbox.get("submit_invoked") is False
        and sandbox.get("cancel_invoked") is False
    )
    add(
        "sandbox_evidence_config_match",
        bool(sandbox_hash) and sandbox_hash == current_hash,
        f"path={sandbox_path or 'missing'};evidence={sandbox_hash[:16]};current={current_hash[:16]}",
        "sandbox evidence bound to current config",
        "安全配置变化后必须重新生成沙箱汇总，旧指纹不能用于武装。",
    )
    add(
        "sandbox_evidence_certified",
        sandbox_certified,
        f"path={sandbox_path or 'missing'};status={sandbox.get('status', 'missing')}",
        "sandbox_certified with read-only evidence and no submit/cancel",
        "重新运行只读 Canary 预检并生成完整沙箱证据，禁止手工改写认证状态。",
    )
    return pd.DataFrame(rows, columns=ARMING_AUDIT_COLUMNS)


def write_huatai_canary_arming_audit(config: dict[str, Any]) -> Path:
    auto = _mapping(config.get("auto_live"))
    cert = _mapping(auto.get("desktop_canary_certification"))
    path = _path(cert.get("audit_path", "outputs_live/huatai_desktop_canary_audit.csv"))
    if path is None:
        raise RuntimeError("Huatai Canary audit path is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_to_csv(build_huatai_canary_arming_audit(config), path, index=False, encoding="utf-8-sig")
    return path


def write_huatai_execution_sandbox_summary(
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> Path:
    current = _utc_now(now)
    auto = _mapping(config.get("auto_live"))
    cert = _mapping(auto.get("desktop_canary_certification"))
    evidence = _mapping(cert.get("evidence"))
    path = _path(evidence.get("sandbox_summary"))
    if path is None:
        raise RuntimeError("Huatai Canary sandbox summary path is invalid")

    audit = build_huatai_canary_arming_audit(config, now=current).set_index("check")
    checks = {
        name: bool(audit.loc[name, "passed"]) if name in audit.index else False
        for name in SANDBOX_REQUIRED_CHECKS
    }
    blockers = [name for name, passed in checks.items() if not passed]
    evidence_keys = (
        "buy_preview",
        "sell_preview",
        "orders_monitor",
        "trades_monitor",
        "cancels_monitor",
        "account_snapshot",
    )
    evidence_hashes = {
        name: _file_sha256(_path(evidence.get(name)))
        for name in evidence_keys
    }
    payload = {
        "schema_version": "ashare_quant.huatai_execution_sandbox.v2",
        "generated_at": current.isoformat(timespec="seconds"),
        "status": "sandbox_certified" if not blockers else "blocked",
        "account_environment": "live" if checks["live_account_snapshot_current"] else "unknown",
        "buy_form_preview": checks["buy_form_preview_current"],
        "sell_form_preview": checks["sell_form_preview_current"],
        "orders_empty_verified": checks["orders_empty_monitor_current"],
        "trades_empty_verified": checks["trades_empty_monitor_current"],
        "cancels_empty_verified": checks["cancels_empty_monitor_current"],
        "submit_invoked": False,
        "cancel_invoked": False,
        "ready_for_live_canary": False,
        "auto_live_config_sha256": auto_live_config_sha256(config),
        "evidence_sha256": evidence_hashes,
        "checks": checks,
        "blockers": blockers,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    if blockers:
        raise RuntimeError("Huatai Canary sandbox blocked: " + ";".join(blockers))
    return path


def arm_huatai_desktop_canary(
    config: dict[str, Any],
    *,
    confirm: bool,
    now: datetime | None = None,
) -> dict[str, Path]:
    if not confirm:
        raise RuntimeError("Huatai Canary arming requires explicit --confirm")
    current = _utc_now(now)
    auto = _mapping(config.get("auto_live"))
    cert_cfg = _mapping(auto.get("desktop_canary_certification"))
    audit = build_huatai_canary_arming_audit(config, now=current)
    audit_path = _path(cert_cfg.get("audit_path", "outputs_live/huatai_desktop_canary_audit.csv"))
    cert_path = _path(cert_cfg.get("certification_path", "outputs_live/huatai_desktop_canary_certification.json"))
    if audit_path is None or cert_path is None:
        raise RuntimeError("Huatai Canary certification paths are invalid")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    failed = audit.loc[~audit["passed"], "check"].astype(str).tolist()
    if failed:
        raise RuntimeError("Huatai Canary arming blocked: " + ";".join(failed))

    bridge_path = _path(cert_cfg.get("bridge_config"))
    bridge = _load_mapping_file(bridge_path)
    policy = _mapping(_mapping(bridge.get("huatai", bridge)).get("desktop_canary"))
    hmac_env = str(policy.get("hmac_key_env", "ASHARE_QUANT_ADAPTER_HMAC_KEY"))
    key = os.environ.get(hmac_env, "").encode("utf-8")
    if len(key) < 32:
        raise RuntimeError("Huatai Canary certification HMAC key is missing or too short")
    ttl = min(max(int(cert_cfg.get("arming_ttl_seconds", 1800)), 60), 1800)
    canary = _mapping(_mapping(_mapping(auto.get("rollout")).get("stages")).get("canary"))
    payload: dict[str, Any] = {
        "schema_version": "ashare_quant.huatai_desktop_canary_certification.v1",
        "status": "armed",
        "auto_live_config_sha256": auto_live_config_sha256(config),
        "bridge_config_sha256": _file_sha256(bridge_path),
        "submit_form_preview": True,
        "empty_order_monitor": True,
        "account_snapshot_live": True,
        "operator_confirmation_required": True,
        "max_orders": 1,
        "max_order_amount": min(float(canary.get("max_single_order_amount", 1000)), 1000.0),
        "submit_contract": False,
        "query_contract": False,
        "cancel_contract": False,
        "nonempty_order_parser": False,
        "created_at": current.isoformat(timespec="seconds"),
        "expires_at": (current + timedelta(seconds=ttl)).isoformat(timespec="seconds"),
        "hmac_key_env": hmac_env,
    }
    payload["hmac_sha256"] = certification_hmac_sha256(payload, key)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cert_path.with_name(f".{cert_path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(cert_path)
    return {
        "huatai_desktop_canary_audit.csv": audit_path,
        "huatai_desktop_canary_certification.json": cert_path,
    }


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_evidence(value: object) -> tuple[dict[str, Any], Path | None]:
    path = _path(value)
    return _load_mapping_file(path), path


def _load_mapping_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            import yaml  # type: ignore

            payload = yaml.safe_load(text) or {}
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _path(value: object) -> Path | None:
    text = str(value or "").strip()
    return project_path(text) if text else None


def _age_seconds(payload: dict[str, Any], now: datetime) -> float | None:
    value = payload.get("generated_at") or payload.get("broker_time")
    try:
        generated = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if generated.tzinfo is None:
        return None
    return (now - generated.astimezone(timezone.utc)).total_seconds()


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Huatai Canary audit time must be timezone-aware")
    return current.astimezone(timezone.utc)


def _float(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def _int(value: object) -> int | None:
    parsed = _float(value)
    return None if parsed is None else int(parsed)


def _file_sha256(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()
