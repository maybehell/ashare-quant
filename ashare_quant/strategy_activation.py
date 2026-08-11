from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from pandas.errors import EmptyDataError

from .audit import mapping_sha256
from .candidate import (
    build_candidate_validation,
    live_strategy_config,
    live_strategy_hash,
    load_candidate_manifest,
)
from .config import load_config, project_path
from .io_utils import atomic_to_csv, replace_with_retry
from .forward_selection import (
    audit_forward_curve_ledger,
    candidate_set_sha256,
    selection_policy_path,
)
from .readiness import build_live_readiness_from_outputs


ACTIVATION_SCHEMA_VERSION = 1
EXECUTION_EPOCH_SCHEMA_VERSION = 1
ACTIVATION_AUDIT_COLUMNS = [
    "check",
    "passed",
    "value",
    "expected",
    "blocking",
    "message",
]
EXECUTION_STATUS_COLUMNS = [
    "check",
    "passed",
    "value",
    "expected",
    "blocking",
    "message",
]


def active_execution_path(config: dict[str, Any]) -> Path:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    return project_path(
        validation.get(
            "active_execution_path",
            "data/active_execution_strategy.json",
        )
    )


def execution_epoch_path(config: dict[str, Any]) -> Path:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    output_dir = str(paths.get("output_dir", "") or "").strip()
    if bool(validation.get("research_only", False)) and output_dir:
        return project_path(output_dir) / ".execution_strategy_epoch.json"
    configured = str(validation.get("execution_epoch_path", "") or "").strip()
    if configured:
        return project_path(configured)
    if str(validation.get("active_execution_path", "") or "").strip():
        return active_execution_path(config).with_name("execution_strategy_epoch.json")
    if output_dir:
        return project_path(output_dir) / ".execution_strategy_epoch.json"
    return project_path("data/execution_strategy_epoch.json")


def candidate_activation_audit_path(config: dict[str, Any]) -> Path:
    return project_path(config["paths"]["output_dir"]) / "candidate_activation_audit.csv"


def candidate_deactivation_audit_path(config: dict[str, Any]) -> Path:
    return project_path(config["paths"]["output_dir"]) / "candidate_deactivation_audit.csv"


def active_execution_status_path(config: dict[str, Any]) -> Path:
    return project_path(config["paths"]["output_dir"]) / "active_execution_status.csv"


def build_candidate_activation_audit(
    base_config: dict[str, Any],
    candidate_config_path: str | Path,
    *,
    expected_candidate_id: str = "",
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, expected: object, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "expected": expected,
                "blocking": True,
                "message": message,
            }
        )

    validation = (
        base_config.get("validation", {})
        if isinstance(base_config.get("validation"), dict)
        else {}
    )
    registered = validation.get("forward_only_candidate_configs", [])
    registered_paths = [
        project_path(str(value)).resolve()
        for value in registered
        if str(value).strip()
    ] if isinstance(registered, list) else []
    requested_path = project_path(candidate_config_path).resolve()
    registry_unique = len(set(registered_paths)) == len(registered_paths)
    add(
        "candidate_registry_ready",
        bool(registered_paths) and registry_unique,
        len(registered_paths),
        ">=1 unique preregistered candidate",
        "候选登记必须非空且路径唯一；多候选由冻结的前向选择政策决定唯一胜者。",
    )
    add(
        "candidate_config_registered",
        requested_path in registered_paths,
        str(requested_path),
        "one path in the preregistered candidate set",
        "激活目标必须属于未改写的前向候选集合。",
    )
    try:
        candidate_config = load_config(requested_path)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        add(
            "candidate_config_readable",
            False,
            type(exc).__name__,
            "readable",
            "候选配置必须可读取。",
        )
        return pd.DataFrame(rows, columns=ACTIVATION_AUDIT_COLUMNS)
    add("candidate_config_readable", True, str(requested_path), "readable", "候选配置已读取。")

    candidate_validation = (
        candidate_config.get("validation", {})
        if isinstance(candidate_config.get("validation"), dict)
        else {}
    )
    research_only = bool(candidate_validation.get("research_only", False))
    experiment_status = str(candidate_validation.get("experiment_status", "") or "").strip()
    add(
        "forward_only_governance",
        research_only and experiment_status == "preregistered_forward_only",
        f"research_only={research_only};status={experiment_status or 'missing'}",
        "research_only=True;status=preregistered_forward_only",
        "只能从预登记的研究候选晋级，不能绕过候选治理。",
    )

    manifest = load_candidate_manifest(candidate_config)
    candidate_id = str((manifest or {}).get("candidate_id", "") or "").strip()
    frozen_hash = str((manifest or {}).get("strategy_hash", "") or "").strip()
    current_hash = live_strategy_hash(candidate_config)
    add(
        "candidate_manifest_identity",
        bool(
            manifest
            and candidate_id
            and frozen_hash
            and frozen_hash == current_hash
            and str(manifest.get("freeze_mode", "") or "") == "forward_only_preregistration"
        ),
        f"candidate={candidate_id or 'missing'};hash={current_hash[:16]}",
        f"frozen_hash={frozen_hash[:16] or 'missing'}",
        "候选 ID、冻结模式及策略哈希必须保持一致。",
    )
    expected_id = str(expected_candidate_id or "").strip()
    add(
        "expected_candidate_id",
        not expected_id or expected_id == candidate_id,
        candidate_id or "missing",
        expected_id or "not required for audit",
        "确认激活时必须携带界面上看到的精确候选 ID。",
    )

    selection_ok, selection_value = _forward_selection_winner_status(
        base_config,
        registered_paths,
        candidate_id,
    )
    add(
        "candidate_forward_selection_winner",
        selection_ok,
        selection_value,
        "single candidate or identity-bound frozen forward winner",
        "多候选激活必须匹配冻结政策在完整共同前向窗口中选出的唯一胜者。",
    )
    output_dir = project_path(candidate_config["paths"]["output_dir"])
    shadow = _read_csv(output_dir / "shadow_forward_audit.csv")
    current_validation = build_candidate_validation(
        candidate_config,
        pd.DataFrame(),
        shadow_audit=shadow,
    )
    required_checks = {
        "candidate_exists",
        "candidate_hash_match",
        "candidate_quarantine_clear",
        "candidate_forward_only_preregistration",
        "candidate_forward_risk_evaluation_policy",
        "candidate_forward_trading_days",
        "candidate_forward_excess_return",
        "candidate_forward_active_max_drawdown",
        "candidate_forward_tracking_error",
        "candidate_forward_information_ratio",
        "candidate_early_forward_degradation",
        "forward_validation_status",
    }
    if bool(candidate_validation.get("require_frozen_input_evidence", False)):
        required_checks.add("candidate_frozen_input_evidence")
    membership = (
        candidate_config.get("strategy", {}).get("benchmark_membership", {})
        if isinstance(candidate_config.get("strategy", {}), dict)
        else {}
    )
    if isinstance(membership, dict) and bool(membership.get("enabled", False)):
        required_checks.add("candidate_index_membership_evidence")
    indexed = current_validation.set_index("check") if not current_validation.empty else pd.DataFrame()
    missing_checks = sorted(required_checks - set(current_validation.get("check", pd.Series(dtype=str)).astype(str)))
    failed_checks = sorted(
        check
        for check in required_checks
        if check in indexed.index and not _truth(indexed.loc[check, "passed"])
    )
    status_rows = current_validation[current_validation.get("check", pd.Series(dtype=str)) == "forward_validation_status"]
    status = str(status_rows.iloc[-1].get("value", "") or "") if not status_rows.empty else "missing"
    validation_ok = not missing_checks and not failed_checks and status == "validated"
    add(
        "candidate_validation_current",
        validation_ok,
        f"status={status};missing={','.join(missing_checks)};failed={','.join(failed_checks)}",
        "status=validated;missing=;failed=",
        "激活前必须实时重算冻结输入、成分和前向收益门禁。",
    )

    strict_ok, strict_value = _candidate_strict_live_readiness_status(
        candidate_config,
        candidate_id,
    )
    add(
        "candidate_strict_live_readiness",
        strict_ok,
        strict_value,
        "all identity-bound strict live-readiness checks passed",
        "候选激活前必须通过当前成本、整手资金、稳健性、数据身份和前向证据的完整实盘门禁。",
    )
    override = live_strategy_config(candidate_config)
    override_hash = mapping_sha256(override)
    add(
        "activation_override_hash",
        bool(frozen_hash and override_hash == frozen_hash),
        override_hash[:16],
        frozen_hash[:16] or "frozen candidate hash",
        "写入激活记录的策略行为必须与冻结候选完全一致。",
    )
    return pd.DataFrame(rows, columns=ACTIVATION_AUDIT_COLUMNS)


def _candidate_strict_live_readiness_status(
    candidate_config: dict[str, Any],
    candidate_id: str,
) -> tuple[bool, str]:
    try:
        readiness = build_live_readiness_from_outputs(candidate_config)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return False, f"rebuild_failed={type(exc).__name__}"
    if readiness.empty or not {"check", "passed", "value"}.issubset(readiness.columns):
        return False, "strict_readiness_missing"
    identity = readiness[readiness["check"].astype(str) == "candidate_exists"]
    identity_ok = bool(
        len(identity) == 1
        and _truth(identity.iloc[-1].get("passed"))
        and str(identity.iloc[-1].get("value", "") or "").strip() == candidate_id
    )
    passed = readiness["passed"].map(_truth)
    failed = readiness.loc[~passed, "check"].astype(str).tolist()
    ok = identity_ok and bool(passed.all())
    detail = f"identity={identity_ok};checks={int(passed.sum())}/{len(readiness)}"
    if failed:
        detail += ";failed=" + ",".join(failed[:8])
        if len(failed) > 8:
            detail += f",+{len(failed) - 8}"
    return ok, detail

def _forward_selection_winner_status(
    base_config: dict[str, Any],
    registered_paths: list[Path],
    requested_candidate_id: str,
) -> tuple[bool, str]:
    if len(registered_paths) == 1:
        return True, "single_candidate_policy"
    if len(registered_paths) < 2:
        return False, "candidate_registry_empty"

    output_dir = project_path(base_config["paths"]["output_dir"])
    summary = _read_csv(output_dir / "forward_candidate_selection_summary.csv")
    if len(summary) != 1:
        return False, f"selection_summary_rows={len(summary)}"
    row = summary.iloc[-1]
    selected_value = row.get("selected_candidate_id", "")
    selected_id = "" if pd.isna(selected_value) else str(selected_value).strip()
    minimum_days = _integer(row.get("minimum_common_trading_days"))
    required_days = _integer(row.get("required_common_trading_days"))

    validation = base_config.get("validation", {})
    validation = validation if isinstance(validation, dict) else {}
    raw_values = validation.get("forward_only_candidate_configs", [])
    raw_values = raw_values if isinstance(raw_values, list) else []
    raw_by_path = {
        project_path(str(value)).resolve(): str(value).strip().replace(chr(92), "/")
        for value in raw_values
        if str(value).strip()
    }
    if set(raw_by_path) != {path.resolve() for path in registered_paths}:
        return False, "candidate_registry_paths_changed"

    records: list[dict[str, str]] = []
    for path in registered_paths:
        try:
            candidate_config = load_config(path)
            manifest = load_candidate_manifest(candidate_config) or {}
        except (OSError, ValueError, TypeError, KeyError):
            return False, f"candidate_unreadable={path}"
        candidate_id = str(manifest.get("candidate_id", "") or "").strip()
        strategy_hash = str(manifest.get("strategy_hash", "") or "").strip()
        if not candidate_id or not strategy_hash:
            return False, f"candidate_identity_missing={path}"
        records.append(
            {
                "candidate_id": candidate_id,
                "candidate_name": str(manifest.get("strategy_name", "") or "").strip(),
                "candidate_config": raw_by_path[path.resolve()],
                "candidate_output_dir": str(
                    project_path(candidate_config["paths"]["output_dir"]).resolve()
                ),
                "strategy_hash": strategy_hash,
            }
        )
    records = sorted(records, key=lambda item: item["candidate_id"])
    expected_set_hash = candidate_set_sha256(records)

    policy_path = selection_policy_path(base_config)
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, f"selection_policy_unreadable={type(exc).__name__}"
    policy_set_hash = str(policy.get("candidate_set_sha256", "") or "").strip()
    summary_set_hash = str(row.get("candidate_set_sha256", "") or "").strip()
    policy_hash = str(policy.get("policy_sha256", "") or "").strip()
    summary_policy_hash = str(row.get("policy_sha256", "") or "").strip()
    candidate_ids = {record["candidate_id"] for record in records}
    pair_ok, pair_value = _paired_selection_winner_status(
        _read_csv(output_dir / "forward_candidate_selection.csv"),
        candidate_ids,
        selected_id,
        required_days,
    )
    ledger_ok, ledger_value = audit_forward_curve_ledger(base_config, records, row)
    passed = bool(
        expected_set_hash
        and summary_set_hash == expected_set_hash
        and policy_set_hash == expected_set_hash
        and policy_hash
        and summary_policy_hash == policy_hash
        and _truth(row.get("policy_valid"))
        and _truth(row.get("selection_ready"))
        and str(row.get("status", "") or "").strip().lower() == "ready"
        and selected_id == requested_candidate_id
        and selected_id in candidate_ids
        and required_days > 0
        and minimum_days >= required_days
        and pair_ok
        and ledger_ok
    )
    return passed, (
        f"selected={selected_id or 'missing'};requested={requested_candidate_id or 'missing'};"
        f"days={minimum_days}/{required_days};set_match={summary_set_hash == expected_set_hash};"
        f"policy_match={bool(policy_hash and summary_policy_hash == policy_hash)};"
        f"pairs={pair_value};ledger={ledger_value}"
    )


def _paired_selection_winner_status(
    frame: pd.DataFrame,
    candidate_ids: set[str],
    selected_id: str,
    required_days: int,
) -> tuple[bool, str]:
    expected_pairs = len(candidate_ids) * (len(candidate_ids) - 1) // 2
    required = {
        "candidate_a_id",
        "candidate_b_id",
        "common_trading_days",
        "favored_candidate_id",
        "status",
    }
    if len(frame) != expected_pairs or not required.issubset(frame.columns):
        return False, f"rows={len(frame)}/{expected_pairs}"
    seen: set[tuple[str, str]] = set()
    wins = {candidate_id: 0 for candidate_id in candidate_ids}
    for row in frame.to_dict(orient="records"):
        a = str(row.get("candidate_a_id", "") or "").strip()
        b = str(row.get("candidate_b_id", "") or "").strip()
        favored = str(row.get("favored_candidate_id", "") or "").strip()
        pair = tuple(sorted((a, b)))
        if (
            a == b
            or {a, b}.difference(candidate_ids)
            or pair in seen
            or favored not in {a, b}
            or str(row.get("status", "") or "").strip().lower() != "dominance_ready"
            or _integer(row.get("common_trading_days")) < required_days
        ):
            return False, f"invalid_pair={a or 'missing'}:{b or 'missing'}"
        seen.add(pair)
        wins[favored] += 1
    winners = [candidate_id for candidate_id, count in wins.items() if count == len(candidate_ids) - 1]
    passed = len(winners) == 1 and winners[0] == selected_id
    return passed, f"rows={len(seen)}/{expected_pairs};winner={winners[0] if len(winners) == 1 else 'missing'}"


def _integer(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0

def write_candidate_activation_audit(
    base_config: dict[str, Any],
    candidate_config_path: str | Path,
    *,
    expected_candidate_id: str = "",
) -> Path:
    frame = build_candidate_activation_audit(
        base_config,
        candidate_config_path,
        expected_candidate_id=expected_candidate_id,
    )
    return atomic_to_csv(
        frame,
        candidate_activation_audit_path(base_config),
        index=False,
        encoding="utf-8-sig",
    )


def activate_forward_candidate(
    base_config: dict[str, Any],
    candidate_config_path: str | Path,
    *,
    expected_candidate_id: str,
    confirm: bool = False,
) -> dict[str, Path]:
    expected_id = str(expected_candidate_id or "").strip()
    if not expected_id:
        raise RuntimeError("Candidate activation requires the exact expected candidate ID.")
    audit_path = write_candidate_activation_audit(
        base_config,
        candidate_config_path,
        expected_candidate_id=expected_id,
    )
    audit = _read_csv(audit_path)
    failed = audit[~audit["passed"].map(_truth)] if not audit.empty else audit
    if not confirm:
        return {"candidate_activation_audit.csv": audit_path}
    if audit.empty or not failed.empty:
        names = ",".join(failed.get("check", pd.Series(dtype=str)).astype(str))
        raise RuntimeError(f"Candidate activation blocked by current audit: {names or 'missing audit'}")

    candidate_config = load_config(project_path(candidate_config_path))
    manifest = load_candidate_manifest(candidate_config) or {}
    candidate_id = str(manifest.get("candidate_id", "") or "").strip()
    if candidate_id != expected_id:
        raise RuntimeError("Candidate activation ID changed after audit.")
    override = live_strategy_config(candidate_config)
    strategy_hash = mapping_sha256(override)
    target = active_execution_path(base_config)
    payload = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "activation_id": uuid4().hex,
        "candidate_id": candidate_id,
        "candidate_config": str(project_path(candidate_config_path).resolve()),
        "strategy_hash": strategy_hash,
        "execution_override": override,
        "activated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "broker_submit_enabled": False,
        "fallback_strategy_hash": live_strategy_hash(base_config),
        "previous_execution_epoch_id": _existing_default_epoch_id(base_config),
    }
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if (
            str(existing.get("candidate_id", "") or "") == candidate_id
            and str(existing.get("strategy_hash", "") or "") == strategy_hash
        ):
            return {
                "candidate_activation_audit.csv": audit_path,
                "active_execution_strategy.json": target,
            }
        raise RuntimeError("A different execution candidate is already active; explicit rollback is required.")
    _atomic_write_json(target, payload)
    return {
        "candidate_activation_audit.csv": audit_path,
        "active_execution_strategy.json": target,
    }


def build_active_execution_status(config: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, expected: object, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "expected": expected,
                "blocking": True,
                "message": message,
            }
        )

    target = active_execution_path(config)
    if not target.exists():
        add(
            "active_record_integrity",
            True,
            "absent",
            "absent or valid",
            "未激活候选时使用默认执行策略。",
        )
        add(
            "active_loader_identity",
            True,
            "default",
            "default",
            "当前命令使用默认策略，未发现待应用的激活记录。",
        )
        add(
            "active_broker_submit_disabled",
            True,
            True,
            True,
            "策略激活层未授予券商提交权限。",
        )
        try:
            epoch = current_execution_epoch(config)
            epoch_ok = bool(
                epoch.get("execution_epoch_id")
                and epoch.get("execution_mode") == "default"
                and epoch.get("execution_strategy_hash") == live_strategy_hash(config)
            )
            epoch_value = (
                f"mode={epoch.get('execution_mode', '')};"
                f"epoch={epoch.get('execution_epoch_id', '')[:16]}"
            )
        except RuntimeError as exc:
            missing = "epoch is missing" in str(exc).lower()
            epoch_ok = missing
            epoch_value = (
                "uninitialized_default_no_orders_valid"
                if missing
                else f"{type(exc).__name__}:{exc}"
            )
        add(
            "execution_epoch_current",
            epoch_ok,
            epoch_value,
            "default epoch bound to current strategy hash",
            "默认执行纪元必须存在并绑定当前策略；旧纪元订单不可复用。",
        )
        return pd.DataFrame(rows, columns=EXECUTION_STATUS_COLUMNS)

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        add(
            "active_record_integrity",
            False,
            type(exc).__name__,
            "valid JSON mapping",
            "激活记录无法读取；禁止继续生成订单。",
        )
        return pd.DataFrame(rows, columns=EXECUTION_STATUS_COLUMNS)
    if not isinstance(payload, dict):
        add(
            "active_record_integrity",
            False,
            type(payload).__name__,
            "mapping",
            "激活记录顶层必须是对象；禁止继续生成订单。",
        )
        return pd.DataFrame(rows, columns=EXECUTION_STATUS_COLUMNS)

    override = payload.get("execution_override")
    expected_hash = str(payload.get("strategy_hash", "") or "").strip()
    actual_override_hash = mapping_sha256(override) if isinstance(override, dict) else ""
    candidate_id = str(payload.get("candidate_id", "") or "").strip()
    activation_id = str(payload.get("activation_id", "") or "").strip()
    record_ok = bool(
        int(payload.get("schema_version", 0) or 0) == ACTIVATION_SCHEMA_VERSION
        and activation_id
        and candidate_id
        and isinstance(override, dict)
        and bool(override)
        and expected_hash
        and actual_override_hash == expected_hash
    )
    add(
        "active_record_integrity",
        record_ok,
        f"candidate={candidate_id or 'missing'};epoch={activation_id[:16] or 'missing'};hash={actual_override_hash[:16] or 'missing'}",
        f"schema={ACTIVATION_SCHEMA_VERSION};hash={expected_hash[:16] or 'missing'}",
        "激活记录候选身份、版本和策略哈希必须完整一致。",
    )
    metadata = config.get("_active_execution")
    metadata = metadata if isinstance(metadata, dict) else {}
    loader_ok = bool(
        record_ok
        and metadata
        and str(metadata.get("candidate_id", "") or "") == candidate_id
        and str(metadata.get("strategy_hash", "") or "") == expected_hash
        and live_strategy_hash(config) == expected_hash
    )
    add(
        "active_loader_identity",
        loader_ok,
        (
            f"loaded_candidate={str(metadata.get('candidate_id', '') or 'missing')};"
            f"execution_hash={live_strategy_hash(config)[:16]}"
        ),
        f"candidate={candidate_id or 'missing'};hash={expected_hash[:16] or 'missing'}",
        "存在激活记录时，一键运行必须通过默认加载器应用同一冻结策略；显式 --config 不得绕过。",
    )
    submit_disabled = payload.get("broker_submit_enabled") is False
    add(
        "active_broker_submit_disabled",
        submit_disabled,
        submit_disabled,
        True,
        "策略激活记录不得授予券商提交权限。",
    )
    add(
        "execution_epoch_current",
        bool(record_ok and loader_ok and activation_id),
        f"mode=active_candidate;epoch={activation_id[:16] or 'missing'}",
        "active candidate activation_id bound to loader identity",
        "激活候选的 activation_id 同时也是订单执行纪元。",
    )
    return pd.DataFrame(rows, columns=EXECUTION_STATUS_COLUMNS)


def write_active_execution_status(config: dict[str, Any]) -> Path:
    return atomic_to_csv(
        build_active_execution_status(config),
        active_execution_status_path(config),
        index=False,
        encoding="utf-8-sig",
    )


def require_active_execution_consistency(config: dict[str, Any]) -> Path:
    path = write_active_execution_status(config)
    audit = _read_csv(path)
    failed = audit[~audit["passed"].map(_truth)] if not audit.empty else audit
    if audit.empty or not failed.empty:
        names = ",".join(failed.get("check", pd.Series(dtype=str)).astype(str))
        raise RuntimeError(
            "Execution strategy identity blocked order generation: "
            f"{names or 'missing active execution audit'}"
        )
    return path


def deactivate_execution_candidate(
    config: dict[str, Any],
    *,
    expected_candidate_id: str,
    confirm: bool = False,
) -> dict[str, Path]:
    expected_id = str(expected_candidate_id or "").strip()
    if not expected_id:
        raise RuntimeError("Candidate deactivation requires the exact active candidate ID.")

    target = active_execution_path(config)
    status = build_active_execution_status(config)
    active_metadata = config.get("_active_execution")
    active_metadata = active_metadata if isinstance(active_metadata, dict) else {}
    loaded_id = str(active_metadata.get("candidate_id", "") or "").strip()
    status.loc[len(status)] = {
        "check": "expected_candidate_id",
        "passed": bool(loaded_id and loaded_id == expected_id),
        "value": loaded_id or "missing",
        "expected": expected_id,
        "blocking": True,
        "message": "停用时必须输入当前默认加载器实际应用的精确候选 ID。",
    }
    audit_path = atomic_to_csv(
        status,
        candidate_deactivation_audit_path(config),
        index=False,
        encoding="utf-8-sig",
    )
    failed = status[~status["passed"].map(_truth)] if not status.empty else status
    if not confirm:
        return {"candidate_deactivation_audit.csv": audit_path}
    if status.empty or not failed.empty:
        names = ",".join(failed.get("check", pd.Series(dtype=str)).astype(str))
        raise RuntimeError(
            f"Candidate deactivation blocked by current audit: {names or 'missing audit'}"
        )

    payload = json.loads(target.read_text(encoding="utf-8"))
    strategy_hash = str(payload.get("strategy_hash", "") or "").strip()
    fallback_strategy_hash = str(payload.get("fallback_strategy_hash", "") or "").strip()
    if not fallback_strategy_hash:
        raise RuntimeError("Candidate deactivation record is missing its fallback strategy hash.")
    default_epoch = _rotate_default_execution_epoch(
        config, strategy_hash=fallback_strategy_hash, reason=f"deactivated:{expected_id}"
    )
    timestamp = datetime.now().astimezone()
    safe_id = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in expected_id
    )[:64]
    archive = target.parent / "active_execution_archive" / (
        f"{timestamp.strftime('%Y%m%dT%H%M%S%z')}-{safe_id}-{strategy_hash[:16]}.json"
    )
    archive_payload = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "event": "candidate_deactivated",
        "candidate_id": expected_id,
        "strategy_hash": strategy_hash,
        "active_record_sha256": mapping_sha256(payload),
        "deactivated_at": timestamp.isoformat(timespec="seconds"),
        "reason": "explicit_manual_rollback",
        "broker_submit_enabled": False,
        "active_record": payload,
    }
    _atomic_write_json(archive, archive_payload)
    archived = json.loads(archive.read_text(encoding="utf-8"))
    if (
        str(archived.get("active_record_sha256", "") or "") != mapping_sha256(payload)
        or archived.get("broker_submit_enabled") is not False
    ):
        raise RuntimeError("Candidate deactivation archive verification failed.")
    target.unlink()
    return {
        "candidate_deactivation_audit.csv": audit_path,
        "archived_execution_strategy.json": archive,
        "execution_strategy_epoch.json": default_epoch,
    }


def ensure_execution_epoch(config: dict[str, Any]) -> dict[str, str]:
    target = active_execution_path(config)
    if target.exists():
        status = build_active_execution_status(config)
        failed = status[~status["passed"].map(_truth)] if not status.empty else status
        if status.empty or not failed.empty:
            names = ",".join(failed.get("check", pd.Series(dtype=str)).astype(str))
            raise RuntimeError(
                "Active execution epoch is unavailable because strategy identity failed: "
                f"{names or 'missing audit'}"
            )
        payload = json.loads(target.read_text(encoding="utf-8"))
        return {
            "execution_epoch_id": str(payload.get("activation_id", "") or ""),
            "execution_mode": "active_candidate",
            "execution_candidate_id": str(payload.get("candidate_id", "") or ""),
            "execution_strategy_hash": str(payload.get("strategy_hash", "") or ""),
        }

    epoch_path = execution_epoch_path(config)
    if not epoch_path.exists():
        _rotate_default_execution_epoch(
            config,
            strategy_hash=live_strategy_hash(config),
            reason="initial_default_epoch",
        )
    payload = _read_default_execution_epoch(epoch_path)
    current_hash = live_strategy_hash(config)
    if str(payload.get("strategy_hash", "") or "") != current_hash:
        epoch_path = _rotate_default_execution_epoch(
            config,
            strategy_hash=current_hash,
            reason="default_strategy_changed",
        )
        payload = _read_default_execution_epoch(epoch_path)
    return {
        "execution_epoch_id": str(payload.get("epoch_id", "") or ""),
        "execution_mode": "default",
        "execution_candidate_id": "",
        "execution_strategy_hash": current_hash,
    }


def current_execution_epoch(config: dict[str, Any]) -> dict[str, str]:
    if active_execution_path(config).exists():
        return ensure_execution_epoch(config)
    payload = _read_default_execution_epoch(execution_epoch_path(config))
    current_hash = live_strategy_hash(config)
    if str(payload.get("strategy_hash", "") or "") != current_hash:
        raise RuntimeError("Execution epoch strategy hash does not match the current default strategy.")
    return {
        "execution_epoch_id": str(payload.get("epoch_id", "") or ""),
        "execution_mode": "default",
        "execution_candidate_id": "",
        "execution_strategy_hash": current_hash,
    }


def _read_default_execution_epoch(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("Execution strategy epoch is missing; regenerate orders through paper/practical.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Execution strategy epoch is unreadable: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Execution strategy epoch must be a mapping.")
    if (
        int(payload.get("schema_version", 0) or 0) != EXECUTION_EPOCH_SCHEMA_VERSION
        or str(payload.get("mode", "") or "") != "default"
        or not str(payload.get("epoch_id", "") or "").strip()
        or not str(payload.get("strategy_hash", "") or "").strip()
        or payload.get("broker_submit_enabled") is not False
    ):
        raise RuntimeError("Execution strategy epoch integrity check failed.")
    return payload


def _rotate_default_execution_epoch(
    config: dict[str, Any], *, strategy_hash: str, reason: str
) -> Path:
    target = execution_epoch_path(config)
    if target.exists():
        existing = _read_default_execution_epoch(target)
        archive_dir = target.parent / "execution_epoch_archive"
        created = str(existing.get("created_at", "") or "").replace(":", "").replace("+", "_")
        archive = archive_dir / f"{created or 'unknown'}-{str(existing.get('epoch_id', ''))[:16]}.json"
        if not archive.exists():
            _atomic_write_json(archive, existing)
    payload = {
        "schema_version": EXECUTION_EPOCH_SCHEMA_VERSION,
        "mode": "default",
        "epoch_id": uuid4().hex,
        "strategy_hash": str(strategy_hash or "").strip(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason": str(reason or "manual_rotation"),
        "broker_submit_enabled": False,
    }
    if not payload["strategy_hash"]:
        raise RuntimeError("Execution strategy epoch requires a strategy hash.")
    _atomic_write_json(target, payload)
    return target


def _existing_default_epoch_id(config: dict[str, Any]) -> str:
    target = execution_epoch_path(config)
    if not target.exists():
        return ""
    return str(_read_default_execution_epoch(target).get("epoch_id", "") or "")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (EmptyDataError, OSError, ValueError):
        return pd.DataFrame()


def _truth(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}
