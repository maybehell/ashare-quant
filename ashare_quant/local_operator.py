from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import backtest_config_sha256, mapping_sha256
from .challengers import primary_challenger_config_paths
from .config import project_path
from .io_utils import atomic_to_csv


LOCAL_OPERATOR_COLUMNS = ["item", "value", "passed", "level", "message"]


def strategy_operating_mode(
    health: pd.DataFrame,
    promotion_decision: pd.DataFrame,
    challenger_observation_ok: bool,
    *,
    formal_candidate_observation: pd.DataFrame | None = None,
    formal_candidate_observation_ok: bool = False,
) -> tuple[str, str]:
    paper_allowed = str(_check_value(health, "paper_orders_allowed")).lower() in {"true", "1", "yes"}
    strict_allowed = str(_check_value(health, "strict_live_allowed")).lower() in {"true", "1", "yes"}
    if strict_allowed:
        return "operator_live_review", "策略严格门禁已通过；仍需账户、券商和人工确认链复核。"
    if paper_allowed:
        return "paper_observation", "策略仅允许模拟盘观察；严格实盘门禁尚未通过。"
    if not promotion_decision.empty:
        row = promotion_decision.iloc[-1]
        candidate = str(row.get("candidate_strategy_name", "") or "").strip()
        decision = str(row.get("decision", "") or "").strip()
        if candidate and challenger_observation_ok and not decision.startswith("do_not_promote"):
            return (
                "successor_observation",
                f"正式策略已暂停；继续观察替代候选 {candidate}，中央决策={decision or 'missing'}，不得生成工单。",
            )
    formal = formal_candidate_observation if formal_candidate_observation is not None else pd.DataFrame()
    if formal_candidate_observation_ok and not formal.empty:
        name_column = "candidate_name" if "candidate_name" in formal.columns else "candidate_id"
        names = [str(value).strip() for value in formal[name_column].tolist() if str(value).strip()]
        observed = pd.to_numeric(
            formal["max_observed_trading_days"]
            if "max_observed_trading_days" in formal.columns
            else pd.Series(0, index=formal.index),
            errors="coerce",
        ).fillna(0).astype(int)
        required = pd.to_numeric(
            formal["required_forward_trading_days"]
            if "required_forward_trading_days" in formal.columns
            else pd.Series(0, index=formal.index),
            errors="coerce",
        ).fillna(0).astype(int)
        weakest = min(
            zip(observed.tolist(), required.tolist()),
            key=lambda pair: pair[0] / max(pair[1], 1),
        )
        label = "、".join(dict.fromkeys(names)) or "已登记候选"
        return (
            "successor_observation",
            f"正式策略已暂停；冻结替代候选 {label} 正在前向观察，统一门禁进度={weakest[0]}/{weakest[1]}，不得生成工单。",
        )
    return "strategy_halted", "正式策略已暂停且没有可用替代候选；仅刷新研究证据，不得生成工单。"


def write_local_operator_status(
    strategy_config: dict[str, Any],
    live_config: dict[str, Any],
    *,
    phase_errors: dict[str, str] | None = None,
) -> dict[str, Path]:
    strategy_dir = project_path(strategy_config["paths"]["output_dir"])
    live_dir = project_path(live_config["paths"]["output_dir"])
    output_dir = strategy_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    practical = _read_csv(strategy_dir / "practical_readiness.csv")
    health = _read_csv(strategy_dir / "strategy_health.csv")
    reliability = _read_csv(strategy_dir / "strategy_reliability.csv")
    candidate_stability = _read_csv(strategy_dir / "strategy_candidate_stability.csv")
    challenger = _read_csv(strategy_dir / "primary_challenger_observation.csv")
    promotion_decision = _read_csv(strategy_dir / "strategy_promotion_decision.csv")
    formal_candidate = _read_csv(strategy_dir / "forward_only_candidate_observation.csv")
    broker_roadmap = _read_csv(strategy_dir / "broker_deployment_roadmap.csv")
    daemon_status = _read_json(strategy_dir / "observation_daemon_status.json")
    scheduled_observation = _read_csv(strategy_dir / "daily_observation_summary.csv")
    broker_refresh = _read_csv(live_dir / "huatai_read_only_refresh.csv")
    client_status = _read_csv(live_dir / "huatai_client_status.csv")
    deployment = _read_csv(live_dir / "live_strategy_evidence_sync_summary.csv")
    account = _read_csv(live_dir / "auto_live_broker_account_audit.csv")
    account_snapshot = _read_csv(live_dir / "auto_live_broker_account_snapshot.csv")
    account_preview = _read_csv(live_dir / "broker_account_import_preview_audit.csv")
    account_initialization = _read_csv(live_dir / "broker_account_initialization_audit.csv")
    account_initialization_provenance = _read_csv(live_dir / "broker_account_initialization_provenance.csv")
    account_binding = _read_csv(live_dir / "auto_live_account_binding_audit.csv")
    capital_feasibility = _read_csv(live_dir / "broker_capital_feasibility_audit.csv")
    canary_capital = _read_csv(live_dir / "broker_canary_capital_audit.csv")
    order_preview = _read_csv(live_dir / "broker_order_preview_audit.csv")
    canary = _read_csv(live_dir / "huatai_desktop_canary_audit.csv")
    launcher_ok, launcher_state, launcher_message = _launcher_runtime_status(
        strategy_dir / "local_run_runtime.json",
        int(strategy_config.get("local_operator", {}).get("max_runtime_seconds", 7200)),
    )
    phase_errors = phase_errors or {}

    practical_ok, practical_value = _passed_count(practical)
    account_audit_ok, account_value = _passed_count(account)
    refresh_ok, refresh_value, refresh_message = _broker_refresh_status(broker_refresh)
    client_ok, client_value, client_message = _huatai_client_status(client_status)
    snapshot_max_age = int(live_config.get("auto_live", {}).get("account_snapshot_max_age_seconds", 300))
    snapshot_fresh, snapshot_value = _snapshot_freshness(account_snapshot, snapshot_max_age)
    account_ok = account_audit_ok and snapshot_fresh
    canary_ok, canary_value = _passed_count(canary)
    paper_allowed = _check_value(health, "paper_orders_allowed")
    strict_allowed = _check_value(health, "strict_live_allowed")
    health_status = _check_value(health, "strategy_health_status") or "missing"
    reliability_score = _item_value(reliability, "reliability_score") or "missing"
    selection_bias_ok, selection_bias_value = _specific_item_status(candidate_stability, "strategy_selection_bias_ready")
    family_ok, family_value = _specific_item_status(candidate_stability, "candidate_family_redundancy_ready")
    family_selection_ok, family_selection_value = _specific_item_status(candidate_stability, "strategy_family_selection_ready")
    expected_challenger_configs = primary_challenger_config_paths(strategy_config)
    challenger_ok, challenger_value, challenger_message = _challenger_observation_set_status(
        challenger,
        expected_configs=expected_challenger_configs,
        max_age_seconds=int(strategy_config.get("local_operator", {}).get("challenger_max_age_seconds", 129600)),
    )
    recovery_ok, recovery_value, recovery_message = _recovery_candidate_status(promotion_decision)
    recovery_capital_ok, recovery_capital_value, recovery_capital_message = _recovery_candidate_capital_status(promotion_decision)
    strategy_validation = strategy_config.get("validation", {}) if isinstance(strategy_config.get("validation"), dict) else {}
    formal_ok, formal_value, formal_message = _formal_candidate_observation_status(
        formal_candidate,
        max_age_seconds=int(
            float(strategy_config.get("observation_task", {}).get("max_age_hours", 36)) * 3600
        ),
        expected_configs=strategy_validation.get("forward_only_candidate_configs", []),
    )
    daemon_ok, daemon_value, daemon_message = _observation_daemon_status(
        daemon_status,
        max_age_seconds=int(
            float(strategy_config.get("observation_task", {}).get("daemon_heartbeat_max_minutes", 5)) * 60
        ),
    )
    observation_settings = strategy_config.get("observation_task", {}) if isinstance(strategy_config.get("observation_task"), dict) else {}
    scheduled_required = str(observation_settings.get("required", False)).strip().lower() in {"true", "1", "yes"}
    startup_required = scheduled_required and str(
        observation_settings.get("startup_required", False)
    ).strip().lower() in {"true", "1", "yes"}
    startup_ok, startup_value, startup_message = _observation_startup_status(
        required=startup_required,
    )
    daemon_gate_ok = (daemon_ok and startup_ok) if scheduled_required else True
    scheduled_ok, scheduled_value, scheduled_message = _scheduled_observation_status(
        scheduled_observation,
        required=scheduled_required,
    )
    roadmap_ok, roadmap_value, roadmap_message = _broker_deployment_roadmap_status(broker_roadmap)

    auto = live_config.get("auto_live", {}) if isinstance(live_config.get("auto_live"), dict) else {}
    deployment_required = bool(auto.get("require_strategy_evidence_sync", False))
    deployment_ok, deployment_value, deployment_message = _deployment_evidence_status(
        deployment,
        strategy_config,
        live_config,
        required=deployment_required,
        max_age_seconds=int(auto.get("strategy_evidence_sync_max_age_seconds", 7200)),
    )
    preview_ok, preview_value = _specific_check_status(account_preview, "broker_account_preview_ready")
    initialization_ok, initialization_value = _specific_check_status(account_initialization, "broker_account_initialization_committed")
    initialization_ready, initialization_ready_value = _specific_check_status(account_initialization, "broker_account_initialization_ready")
    initialization_required = bool(str(live_config.get("paths", {}).get("state_db", "") or "").strip())
    initialization_authorized = bool(auto.get("allow_live_ledger_initialization", False))
    initialization_gate_ok = initialization_ok if initialization_required else True
    if not initialization_ok and initialization_ready:
        initialization_value = initialization_ready_value
    order_preview_ok, order_preview_value = _specific_check_status(order_preview, "broker_order_preview_ready")
    submit_enabled = bool(auto.get("allow_broker_submit", False))
    emergency_stop = bool(auto.get("emergency_stop", True))
    cert_cfg = auto.get("desktop_canary_certification", {}) if isinstance(auto.get("desktop_canary_certification"), dict) else {}
    cert_path = project_path(cert_cfg.get("certification_path", "outputs_live/huatai_desktop_canary_certification.json"))
    certificate_present = cert_path.exists()
    binding_cfg = auto.get("account_binding", {}) if isinstance(auto.get("account_binding"), dict) else {}
    binding_required = bool(binding_cfg.get("required", False))
    binding_ok_raw, binding_value = _passed_count(account_binding)
    binding_ok = binding_ok_raw if binding_required else True
    capital_ok_raw, capital_value = _specific_check_status(capital_feasibility, "broker_capital_feasibility_ready")
    capital_required = initialization_required
    capital_ok = capital_ok_raw if capital_required else True
    canary_capital_ok, canary_capital_value = _specific_check_status(canary_capital, "broker_canary_capital_ready")

    strategy_phase_ok = not bool(phase_errors.get("strategy"))
    broker_command_ok = not bool(phase_errors.get("broker")) and refresh_ok
    broker_phase_ok = broker_command_ok and snapshot_fresh
    safe_default = not submit_enabled and emergency_stop and not certificate_present
    local_runtime_ok = launcher_ok and daemon_gate_ok and scheduled_ok and strategy_phase_ok and deployment_ok and broker_phase_ok and account_ok and initialization_gate_ok and binding_ok and capital_ok
    executable_strategy = str(paper_allowed).lower() in {"true", "1", "yes"}
    strict_strategy = str(strict_allowed).lower() in {"true", "1", "yes"}
    operating_mode, operating_message = strategy_operating_mode(
        health,
        promotion_decision,
        challenger_ok,
        formal_candidate_observation=formal_candidate,
        formal_candidate_observation_ok=formal_ok,
    )
    operating_mode_ok = operating_mode != "strategy_halted"

    if not local_runtime_ok:
        status = "blocked"
        headline = "本地运行存在阻断"
        if not launcher_ok:
            next_action = launcher_message
        elif not refresh_ok:
            next_action = refresh_message
        elif not startup_ok:
            next_action = startup_message
        elif not daemon_gate_ok:
            next_action = daemon_message
        elif not scheduled_ok:
            next_action = scheduled_message
        elif operating_mode == "successor_observation":
            status = "observation_only"
            headline = "本地观察链路正常，实盘门禁尚未通过"
            next_action = operating_message
        elif operating_mode == "strategy_halted":
            next_action = operating_message
        elif not deployment_ok:
            next_action = deployment_message
        elif not strict_strategy:
            next_action = "继续前向验证；严格实盘门禁通过前不初始化真实账户账本。"
        elif capital_required and snapshot_fresh and not capital_ok:
            next_action = (
                "当前账户资金无法按目标权重和 100 股整手规则表示策略组合；"
                f"资金审计={capital_value}。保持只读观察，不得生成真实可执行订单。"
            )
        elif initialization_required and snapshot_fresh and initialization_ready and not initialization_ok and not initialization_authorized:
            next_action = (
                "实盘账本初始化预检已通过，但该步骤尚未获得配置授权；"
                "保持只读连接，不执行账本初始化。"
            )
        elif initialization_required and snapshot_fresh and initialization_ready and not initialization_ok:
            snapshot_id = _provenance_value(account_initialization_provenance, "snapshot_run_id")
            account_id = _provenance_value(account_initialization_provenance, "account_id")
            next_action = (
                "实盘账本初始化预检已通过；核对当前快照后显式运行："
                "python -m ashare_quant --config configs/huatai_desktop_canary.yaml broker-account-init "
                f"--snapshot-run-id {snapshot_id or '<SNAPSHOT_RUN_ID>'} "
                f"--account-id {account_id or '<MASKED_ACCOUNT_ID>'} --confirm"
            )
        elif initialization_required and snapshot_fresh and initialization_ok and not binding_ok:
            next_action = (
                "实盘账本已初始化；重新刷新账户对账后运行："
                "python -m ashare_quant --config configs/huatai_desktop_canary.yaml "
                "auto-live-bind-account --confirm"
            )
        elif not snapshot_fresh and broker_command_ok:
            next_action = "华泰账户快照已过期；打开并登录客户端后重新运行本地流程。"
        else:
            next_action = _first_error(phase_errors) or "查看策略和券商审计中的失败项。"
    elif not challenger_ok:
        status = "observation_degraded"
        headline = "主策略链路正常，候选观察未完成"
        next_action = challenger_message
    elif not executable_strategy:
        status = "observation_only"
        headline = "正式策略已暂停，系统仅执行替代候选观察" if operating_mode == "successor_observation" else "本地链路正常，策略仅限观察"
        next_action = operating_message
    elif not strict_strategy:
        status = "paper_only"
        headline = "本地链路正常，仅允许模拟盘"
        next_action = "继续前向验证，严格实盘门禁通过前不进入真实账户。"
    else:
        status = "operator_review_required"
        headline = "本地链路正常，等待人工实盘复核"
        next_action = "每次实盘仍需盘中账户刷新、单次确认和短时 Canary 凭证。"

    overall_operational = status != "blocked"
    rows = [
        _row("overall_status", status, overall_operational, "runtime", headline),
        _row("local_launcher_state", launcher_state, launcher_ok, "runtime", launcher_message),
        _row("observation_startup", startup_value, startup_ok, "runtime", startup_message),
        _row("observation_daemon", daemon_value, daemon_ok, "runtime", daemon_message),
        _row("scheduled_observation_run", scheduled_value, scheduled_ok, "runtime", scheduled_message),
        _row("formal_forward_candidate", formal_value, formal_ok, "strategy", formal_message),
        _row("strategy_phase", "workflow_completed" if strategy_phase_ok else phase_errors.get("strategy", "failed"), strategy_phase_ok, "strategy", "策略一键流程是否完整执行；不代表策略获准交易。"),
        _row("strategy_operating_mode", operating_mode, operating_mode_ok, "strategy", operating_message),
        _row("strategy_practical_readiness", practical_value, practical_ok, "strategy", "策略实用门禁；不通过时禁止执行工单。"),
        _row("strategy_health_status", health_status, executable_strategy, "strategy", "运行态策略健康结论。"),
        _row("strategy_reliability_score", reliability_score, reliability_score != "missing", "strategy", "研究可信度分数，不等同于未来收益保证。"),
        _row("strategy_selection_bias", selection_bias_value, selection_bias_ok, "strategy", "候选家族互补时间折选择偏差；通过也不替代冻结后前向验证。"),
        _row("strategy_candidate_family", family_value, family_ok, "strategy", "\u5019\u9009\u65cf\u6709\u6548\u72ec\u7acb\u6570\u91cf\u5ba1\u8ba1\uff1b\u7528\u4e8e\u9632\u6b62\u9ad8\u5ea6\u76f8\u4f3c\u53c2\u6570\u53d8\u4f53\u865a\u589e\u7814\u7a76\u8bc1\u636e\u3002"),
        _row("strategy_family_selection", family_selection_value, family_selection_ok, "strategy", "\u5019\u9009\u53bb\u91cd\u540e\u7684\u7b56\u7565\u65cf\u7ea7\u4e92\u8865\u7559\u51fa\u5ba1\u8ba1\u3002"),
        _row("primary_challenger_observation", challenger_value, challenger_ok, "research", challenger_message),
        _row("strict_live_allowed", strict_allowed or "False", strict_strategy, "strategy", "严格实盘策略门禁。"),
        _row("recovery_candidate", recovery_value, recovery_ok, "research", recovery_message),
        _row("recovery_candidate_capital", recovery_capital_value, recovery_capital_ok, "research", recovery_capital_message),
        _row("live_strategy_evidence_sync", deployment_value, deployment_ok, "deployment", deployment_message),
        _row("huatai_client", client_value, client_ok, "broker", client_message),
        _row("huatai_read_only_refresh", refresh_value, refresh_ok, "broker", refresh_message),
        _row("broker_phase", "ok" if broker_phase_ok else (phase_errors.get("broker") or refresh_value or "snapshot_stale"), broker_phase_ok, "broker", "华泰只读刷新与审计结果。"),
        _row("broker_deployment_roadmap", roadmap_value, roadmap_ok, "broker", roadmap_message),
        _row("broker_account_audit", account_value, account_audit_ok, "broker", "真实账户只读对账。"),
        _row("broker_order_preview", order_preview_value, order_preview_ok, "broker", "Non-executable order preview generated from the current broker snapshot."),
        _row("broker_account_initialization", initialization_value, initialization_gate_ok, "broker", "One-time atomic live-ledger initialization; signed account binding remains a separate explicit action."),
        _row("broker_account_binding", binding_value if binding_required else "not_required", binding_ok, "broker", "Signed binding between the isolated live ledger and the current broker account."),
        _row("broker_capital_feasibility", capital_value if capital_required else "not_required", capital_ok, "broker", "Current broker capital must represent the target portfolio under board-lot and weight constraints."),
        _row("broker_canary_capital", canary_capital_value, canary_capital_ok, "broker", "Execution-only one-lot affordability; this never replaces the full strategy capital gate or authorizes submission."),
        _row("broker_account_import_preview", preview_value, preview_ok, "broker", "券商快照生成的不写入账户预检。"),
        _row("broker_snapshot_fresh", snapshot_value, snapshot_fresh, "broker", "状态页重新计算快照年龄，不复用过期余额。"),
        _row("canary_arming_audit", canary_value, canary_ok, "broker", "Canary 武装审计；安全开关关闭时预期不会全通过。"),
        _row("broker_submit_enabled", submit_enabled, not submit_enabled, "safety", "本地一键流程永不打开提交开关。"),
        _row("emergency_stop_active", emergency_stop, emergency_stop, "safety", "本地一键流程保持急停。"),
        _row("canary_certificate_present", certificate_present, not certificate_present, "safety", "本地一键流程不签发交易凭证。"),
        _row("safe_default_state", safe_default, safe_default, "safety", "提交关闭、急停开启且无武装凭证。"),
        _row("next_action", next_action, True, "info", next_action),
    ]
    csv_path = output_dir / "local_operator_status.csv"
    html_path = output_dir / "local_operator_status.html"
    atomic_to_csv(pd.DataFrame(rows, columns=LOCAL_OPERATOR_COLUMNS), csv_path, index=False, encoding="utf-8-sig")
    _write_html(html_path, rows, headline, status, strategy_dir, live_dir)
    return {
        "local_operator_status.csv": csv_path,
        "local_operator_status.html": html_path,
    }


def _row(item: str, value: object, passed: bool, level: str, message: str) -> dict[str, object]:
    return {"item": item, "value": value, "passed": bool(passed), "level": level, "message": message}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _formal_candidate_observation_status(
    frame: pd.DataFrame,
    *,
    max_age_seconds: int = 129600,
    expected_configs: list[str] | None = None,
) -> tuple[bool, str, str]:
    if frame.empty:
        return False, "not_registered", "尚未登记正式冻结候选。"
    candidate_ids = (
        frame["candidate_id"]
        if "candidate_id" in frame.columns
        else pd.Series("", index=frame.index)
    ).fillna("").astype(str).str.strip()
    statuses = (
        frame["status"]
        if "status" in frame.columns
        else pd.Series("", index=frame.index)
    ).fillna("").astype(str).str.strip().str.lower()
    observed = pd.to_numeric(
        frame["max_observed_trading_days"]
        if "max_observed_trading_days" in frame.columns
        else pd.Series(0, index=frame.index),
        errors="coerce",
    ).fillna(0).astype(int)
    required = pd.to_numeric(
        frame["required_forward_trading_days"]
        if "required_forward_trading_days" in frame.columns
        else pd.Series(0, index=frame.index),
        errors="coerce",
    ).fillna(0).astype(int)
    evidence_columns = (
        "frozen_input_evidence_passed",
        "membership_evidence_passed",
        "benchmark_mapping_evidence_passed",
        "shadow_evaluation_evidence_passed",
        "forward_risk_evaluation_evidence_passed",
    )
    evidence_ok = all(
        column in frame.columns
        and frame[column].fillna("").astype(str).str.strip().str.lower().isin({"true", "1", "yes"}).all()
        for column in evidence_columns
    )
    current = statuses.isin({"observed", "waiting_for_market_data"}).all()
    generated_at = (
        frame["generated_at"]
        if "generated_at" in frame.columns
        else pd.Series("", index=frame.index)
    )
    ages = [_timestamp_age_seconds(value) for value in generated_at]
    fresh = bool(ages) and all(
        age is not None and -30 <= age <= max(max_age_seconds, 60)
        for age in ages
    )
    identities_ok = bool(candidate_ids.ne("").all() and candidate_ids.is_unique)
    observed_config_values = (
        frame["candidate_config"]
        if "candidate_config" in frame.columns
        else pd.Series("", index=frame.index)
    )
    observed_configs = {
        _candidate_config_identity(value)
        for value in observed_config_values
        if str(value).strip()
    }
    configured_values = expected_configs if isinstance(expected_configs, list) else []
    expected = {
        _candidate_config_identity(value)
        for value in configured_values
        if str(value).strip()
    }
    coverage_ok = not expected or observed_configs == expected
    progress_ok = bool(required.gt(0).all() and observed.ge(0).all())
    progress = list(zip(observed.tolist(), required.tolist()))
    weakest = min(progress, key=lambda pair: pair[0] / max(pair[1], 1))
    passed = bool(identities_ok and current and evidence_ok and fresh and progress_ok and coverage_ok)
    candidate_value = candidate_ids.iloc[0] if len(candidate_ids) == 1 else ",".join(candidate_ids)
    coverage_count = len(observed_configs & expected) if expected else len(observed_configs)
    coverage_total = len(expected) if expected else len(observed_configs)
    value = (
        f"candidate={candidate_value or 'missing'};candidate_count={len(frame)};"
        f"status={'current' if current else 'invalid'};forward={weakest[0]}/{weakest[1]};"
        f"evidence={evidence_ok};fresh={fresh};identities={identities_ok};"
        f"coverage={coverage_count}/{coverage_total};coverage_ok={coverage_ok}"
    )
    if passed:
        return True, value, "全部正式冻结候选身份与冻结证据有效；最慢前向进度不代表已获准交易。"
    return False, value, "至少一个正式冻结候选的身份、时效、进度、登记覆盖或冻结证据不完整；禁止晋级。"


def _candidate_config_identity(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(project_path(text).resolve()).replace("\\", "/").casefold()
    except OSError:
        return text.replace("\\", "/").casefold()


def _observation_daemon_status(
    payload: dict[str, Any],
    *,
    max_age_seconds: int,
) -> tuple[bool, str, str]:
    if not payload:
        return False, "not_configured", "未发现自动观察守护状态。"
    status = str(payload.get("status", "") or "").strip().lower()
    mode = str(payload.get("mode", "") or "").strip()
    age = _timestamp_age_seconds(payload.get("generated_at", ""))
    state = payload.get("state", {}) if isinstance(payload.get("state"), dict) else {}
    last_success = str(state.get("last_success_date", "") or "").strip()
    attempt = str(state.get("attempt_count", "") or "0").strip()
    exit_value = state.get("last_exit_code", "")
    exit_code = "" if exit_value is None or str(exit_value).strip() == "" else str(exit_value).strip()
    fresh = age is not None and -30 <= age <= max(max_age_seconds, 60)
    reported_hash = str(payload.get("script_sha256", "") or "").strip().lower()
    expected_hash = _file_sha256(project_path("scripts/observation_daemon.ps1"))
    script_current = bool(reported_hash and expected_hash and reported_hash == expected_hash)
    passed = bool(
        mode == "observation_only_daemon"
        and status in {"waiting", "running", "completed", "retry_wait"}
        and fresh
        and script_current
    )
    value = (
        f"status={status or 'missing'};last_success={last_success or 'none'};"
        f"attempt={attempt};exit={exit_code or 'none'};age_seconds="
        f"{'missing' if age is None else f'{max(age, 0):.0f}'};"
        f"script={'current' if script_current else 'stale_or_missing'}"
    )
    if passed:
        return True, value, "自动观察守护心跳正常；该进程只刷新冻结候选，不生成实盘订单。"
    return False, value, "自动观察守护缺失、停止、心跳过期或运行代码已过时；请重启观察守护进程。"


def _observation_startup_status(
    *,
    required: bool,
    startup_path: Path | None = None,
) -> tuple[bool, str, str]:
    if not required:
        return True, "not_required", "Current configuration does not require a persistent observation Startup entry."
    if startup_path is None:
        appdata = str(os.environ.get("APPDATA", "") or "").strip()
        if not appdata:
            return False, "startup_folder_unavailable", "Windows Startup folder is unavailable; reinstall the observation runtime."
        startup_path = (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / "AshareQuantDailyObservation.cmd"
        )
    try:
        launcher = startup_path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return False, f"missing:{startup_path}", "Observation Startup entry is missing; reinstall and start the observation runtime."
    expected_supervisor = str(project_path("scripts/observation_supervisor.ps1").resolve())
    normalized = launcher.replace("/", "\\").casefold()
    expected = expected_supervisor.replace("/", "\\").casefold()
    safe = (
        expected in normalized
        and "observation_supervisor.ps1" in normalized
        and "auto-live-run" not in normalized
        and "broker-account-init" not in normalized
    )
    value = f"installed:{startup_path}" if safe else f"invalid:{startup_path}"
    if safe:
        return True, value, "Observation-only Startup entry is installed and points to the current supervisor."
    return False, value, "Observation Startup entry is stale or unsafe; reinstall it before relying on unattended observation."


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _scheduled_observation_status(frame: pd.DataFrame, *, required: bool) -> tuple[bool, str, str]:
    if not required:
        return True, "not_required", "当前配置不要求无人值守观察任务。"
    if frame.empty:
        return False, "missing", "未找到自动观察任务审计；守护心跳不能替代任务成功证据。"
    row = frame.iloc[-1]
    status_value = row.get("status", "")
    exit_value = row.get("exit_code", "")
    finished_value = row.get("finished_at", "")
    status = "" if pd.isna(status_value) else str(status_value).strip().lower()
    exit_code = "" if pd.isna(exit_value) else str(exit_value).strip()
    finished_at = "" if pd.isna(finished_value) else str(finished_value).strip()
    passed = status in {"completed", "running", "scheduled"}
    value = f"status={status or 'missing'};exit={exit_code or 'none'};finished={finished_at or 'none'}"
    if passed:
        return True, value, "自动观察任务最近成功、正在运行或尚未到首次调度时间。"
    return False, value, "最近一次自动观察任务失败或过期；守护进程仍会按计划重试，成功前不得视为无人值守链路正常。"


def _broker_deployment_roadmap_status(frame: pd.DataFrame) -> tuple[bool, str, str]:
    if frame.empty or "status" not in frame.columns:
        return False, "missing", "尚未生成券商部署阶段路线图。"
    statuses = frame["status"].astype(str)
    read_only = int((statuses == "actionable_read_only").sum())
    ledger = int((statuses == "authorization_required").sum())
    canary = int((statuses == "future_canary_only").sum())
    completed = int((statuses == "completed").sum())
    passed = read_only == 0 and ledger == 0 and canary == 0
    value = f"completed={completed};read_only={read_only};ledger_auth={ledger};future_canary={canary}"
    message = (
        "券商部署阶段已全部完成，仍需单次人工 Canary 审批。"
        if passed
        else "券商阻断已按只读操作、账本授权和未来 Canary 分阶段展示；不会自动越级。"
    )
    return passed, value, message


def _provenance_value(frame: pd.DataFrame, item: str) -> str:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return ""
    selected = frame[frame["item"].astype(str) == item]
    if selected.empty:
        return ""
    value = selected.iloc[-1].get("value", "")
    return "" if pd.isna(value) else str(value).strip()


def _passed_count(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or "passed" not in frame:
        return False, "missing"
    passed = frame["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    return bool(passed.all()), f"{int(passed.sum())}/{len(frame)}"


def _recovery_candidate_status(frame: pd.DataFrame) -> tuple[bool, str, str]:
    if frame.empty:
        return False, "missing", "\u7f3a\u5c11\u6062\u590d\u5019\u9009\u51b3\u7b56\uff1b\u8fd0\u884c compare \u6216\u4e00\u952e\u89c2\u5bdf\u6d41\u7a0b\u3002"
    row = frame.iloc[-1]
    decision = str(row.get("decision", "") or "").strip()
    name = str(row.get("candidate_strategy_name", "") or "").strip()
    days = str(row.get("forward_days", "") or "0").strip()
    required = str(row.get("required_forward_days", "") or "?").strip()
    excess = pd.to_numeric(row.get("forward_excess_return", ""), errors="coerce")
    excess_text = "missing" if pd.isna(excess) else f"{float(excess):.2%}"
    value = f"{name or 'missing'};decision={decision or 'missing'};forward={days}/{required};excess={excess_text}"
    if decision == "observe_only":
        return True, value, "\u6062\u590d\u5019\u9009\u6b63\u5728\u72ec\u7acb\u7d2f\u8ba1\u524d\u5411\u548c Shadow \u8bc1\u636e\uff1b\u4e0d\u7ee7\u627f\u65e7\u5019\u9009\u6837\u672c\uff0c\u4e0d\u5141\u8bb8\u6a21\u62df\u6216\u5b9e\u76d8\u4e0b\u5355\u3002"
    if decision == "paper_trade_only":
        return True, value, "\u6062\u590d\u5019\u9009\u4ec5\u83b7\u51c6\u6a21\u62df\u76d8\uff1b\u4e25\u683c\u5b9e\u76d8\u4ecd\u88ab\u963b\u65ad\u3002"
    if decision == "allow_manual_live_review":
        return True, value, "\u6062\u590d\u5019\u9009\u5df2\u8fdb\u5165\u4eba\u5de5\u5b9e\u76d8\u590d\u6838\uff0c\u4e0d\u4ee3\u8868\u5141\u8bb8\u81ea\u52a8\u63d0\u4ea4\u3002"
    if decision.startswith("do_not_promote"):
        return False, value, "\u6062\u590d\u5019\u9009\u5df2\u89e6\u53d1\u5931\u8d25\u6216\u9694\u79bb\u6761\u4ef6\uff1b\u7981\u6b62\u664b\u7ea7\u3002"
    return False, value, "\u6062\u590d\u5019\u9009\u4ecd\u5904\u4e8e\u7814\u7a76\u6216\u8bc1\u636e\u4e0d\u5b8c\u6574\u72b6\u6001\u3002"


def _recovery_candidate_capital_status(frame: pd.DataFrame) -> tuple[bool, str, str]:
    if frame.empty:
        return False, "missing", "\u7f3a\u5c11\u6062\u590d\u5019\u9009\u548c\u672c\u91d1\u654f\u611f\u6027\u8bc1\u636e\u3002"
    output_value = str(frame.iloc[-1].get("candidate_output_dir", "") or "").strip()
    if not output_value:
        return False, "missing", "\u6062\u590d\u5019\u9009\u672a\u8bb0\u5f55\u9694\u79bb\u8f93\u51fa\u76ee\u5f55\u3002"
    audit = _read_csv(project_path(output_value) / "capital_sensitivity_audit.csv")
    passed, value = _specific_check_status(audit, "capital_sensitivity_minimum_passing_capital")
    if passed:
        return True, f"minimum_passing_capital={value}", "\u5df2\u5728\u591a\u6863\u672c\u91d1\u4e0b\u9a8c\u8bc1\u6574\u624b\u7ea6\u675f\uff1b\u8be5\u95e8\u69db\u4e0d\u7b49\u540c\u4e8e\u63a8\u8350\u6295\u5165\u91d1\u989d\u3002"
    return False, value, "\u6062\u590d\u5019\u9009\u7684\u591a\u672c\u91d1\u6574\u624b\u56de\u6d4b\u5c1a\u672a\u901a\u8fc7\u3002"


def _deployment_evidence_status(
    frame: pd.DataFrame,
    strategy_config: dict[str, Any],
    live_config: dict[str, Any],
    *,
    required: bool,
    max_age_seconds: int,
) -> tuple[bool, str, str]:
    if not required:
        return True, "not_required", "Live evidence publishing is not required by this configuration."
    if frame.empty:
        return False, "missing", "缺少实盘策略证据发布；重新运行 local-run 或 live-evidence-sync。"
    row = frame.iloc[-1]
    status = str(row.get("status", "") or "").strip().lower()
    age = _timestamp_age_seconds(row.get("generated_at", ""))
    source_match = str(row.get("source_config_sha256", "") or "") == mapping_sha256(strategy_config)
    live_match = str(row.get("live_config_sha256", "") or "") == mapping_sha256(live_config)
    backtest_match = str(row.get("backtest_config_sha256", "") or "") == backtest_config_sha256(live_config)
    read_only = not any(
        str(row.get(column, "")).strip().lower() in {"true", "1", "yes"}
        for column in ("orders_copied", "positions_copied", "account_state_copied")
    )
    fresh = age is not None and -30 <= age <= max(max_age_seconds, 60)
    passed = status == "ok" and source_match and live_match and backtest_match and read_only and fresh
    value = (
        f"{status or 'invalid'};age_seconds={max(age or 0, 0):.0f};"
        f"source_match={source_match};live_match={live_match};backtest_match={backtest_match};read_only={read_only}"
    )
    message = (
        "实盘工作区已收到当前、只读的策略证据。"
        if passed
        else "实盘策略证据过期或指纹不匹配；重新运行 local-run 或 live-evidence-sync。"
    )
    return passed, value, message


def _specific_check_status(frame: pd.DataFrame, check: str) -> tuple[bool, str]:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False, "missing"
    selected = frame[frame["check"].astype(str) == check]
    if selected.empty:
        return False, "missing"
    row = selected.iloc[-1]
    passed = str(row.get("passed", "")).strip().lower() in {"true", "1", "yes"}
    return passed, str(row.get("value", "") or ("passed" if passed else "blocked"))


def _specific_item_status(frame: pd.DataFrame, item: str) -> tuple[bool, str]:
    if frame.empty or not {"item", "passed"}.issubset(frame.columns):
        return False, "missing"
    selected = frame[frame["item"].astype(str) == item]
    if selected.empty:
        return False, "missing"
    row = selected.iloc[-1]
    passed = str(row.get("passed", "")).strip().lower() in {"true", "1", "yes"}
    return passed, str(row.get("value", "") or ("passed" if passed else "blocked"))

def _broker_refresh_status(frame: pd.DataFrame) -> tuple[bool, str, str]:
    if frame.empty:
        return True, "not_run", "尚未运行独立华泰只读刷新；账户快照新鲜度仍会单独校验。"
    row = frame.iloc[-1]
    status = str(row.get("status", "") or "").strip().lower()
    stage = str(row.get("stage", "") or "unknown").strip()
    desktop_stage = str(row.get("desktop_stage", "") or "unknown").strip()
    value = f"{status or 'invalid'};stage={stage};desktop={desktop_stage}"
    if status == "ok":
        return True, value, "华泰只读账户刷新最近一次执行成功。"
    if desktop_stage == "client_process_missing":
        waiting_value = f"waiting_for_client;stage={stage};desktop={desktop_stage}"
        return False, waiting_value, "华泰客户端当前离线；运行 broker-refresh 自动启动客户端，登录后打开资金/持仓页面。"
    if desktop_stage in {"client_window_absent", "client_window_missing", "trading_window_missing"}:
        return False, value, "华泰进程已启动但没有可读取窗口；重新打开客户端并登录，再运行 broker-refresh。"
    if desktop_stage in {"client_window_hidden", "client_window_not_visible"}:
        return False, value, "华泰窗口当前隐藏；从任务栏或托盘恢复窗口，打开资金/持仓页面后运行 broker-refresh。"
    if desktop_stage == "client_window_minimized":
        return False, value, "华泰窗口当前最小化；恢复窗口并打开资金/持仓页面后运行 broker-refresh。"
    if desktop_stage == "trading_page_unavailable":
        return False, value, "华泰主程序和交易窗口已运行，但尚未检测到资金/持仓表格；登录后打开“资金股票”页面，再运行 broker-refresh。"
    if desktop_stage == "trading_module_ui_not_loaded":
        return False, value, "华泰主程序和交易进程已运行，但交易模块界面尚未加载；进入交易账户并打开“资金股票”页面，再运行 broker-refresh。"
    message = str(row.get("message", "") or "").strip() or "华泰只读刷新失败。"
    return False, value, f"{message}；登录客户端并打开资金/持仓页面后运行 broker-refresh。"


def _snapshot_freshness(frame: pd.DataFrame, max_age_seconds: int) -> tuple[bool, str]:
    if frame.empty:
        return False, "missing"
    column = "generated_at" if "generated_at" in frame else ("broker_time" if "broker_time" in frame else "")
    if not column:
        return False, "timestamp_missing"
    generated = pd.to_datetime(frame.iloc[0][column], errors="coerce", utc=True)
    if pd.isna(generated):
        return False, "timestamp_invalid"
    age = (pd.Timestamp.now(tz="UTC") - generated).total_seconds()
    passed = -30 <= age <= max(max_age_seconds, 1)
    return bool(passed), f"age_seconds={age:.1f};max={max_age_seconds}"


def _huatai_client_status(frame: pd.DataFrame) -> tuple[bool, str, str]:
    if frame.empty:
        return False, "not_checked", "尚未检查华泰客户端安装和进程状态。"
    row = frame.iloc[-1]
    status = str(row.get("status", "") or "invalid").strip().lower()
    hexin_value = pd.to_numeric(row.get("hexin_process_count", 0), errors="coerce")
    xiadan_value = pd.to_numeric(row.get("xiadan_process_count", 0), errors="coerce")
    hexin = 0 if pd.isna(hexin_value) else int(hexin_value)
    xiadan = 0 if pd.isna(xiadan_value) else int(xiadan_value)
    value = f"{status};hexin={hexin};xiadan={xiadan}"
    if status == "trading_process_ready":
        return True, value, "华泰主程序和交易进程已运行，仍需通过账户页面只读校验。"
    if status == "started_login_required":
        return False, value, "华泰客户端已打开；请完成登录并打开“资金股票”页面。"
    message = str(row.get("message", "") or "华泰客户端未就绪。")
    return False, value, message


def _timestamp_age_seconds(value: object) -> float | None:
    generated = pd.to_datetime(value, errors="coerce")
    if pd.isna(generated):
        return None
    timestamp = pd.Timestamp(generated)
    if timestamp.tzinfo is None:
        now = pd.Timestamp.now()
    else:
        timestamp = timestamp.tz_convert("UTC")
        now = pd.Timestamp.now(tz="UTC")
    return float((now - timestamp).total_seconds())


def _challenger_observation_set_status(
    frame: pd.DataFrame,
    *,
    expected_configs: list[str],
    max_age_seconds: int,
) -> tuple[bool, str, str]:
    if not expected_configs:
        return True, "not_configured", "No registered primary challengers."
    if frame.empty:
        return False, "missing", "Registered primary challenger observation is missing."

    if len(expected_configs) == 1 and len(frame) == 1:
        legacy = frame.iloc[0]
        legacy_status = str(legacy.get("status", "") or "").strip().lower()
        legacy_config = str(legacy.get("candidate_config", "") or "").strip()
        if legacy_status != "observed" and not legacy_config:
            name = str(legacy.get("candidate_name", "") or "").strip() or "unknown"
            message = str(legacy.get("message", "") or "").strip() or "challenger observation failed"
            return False, f"{legacy_status or 'failed'};candidate={name};legacy_identity_missing", message
    observed_rows: list[pd.Series] = []
    configs = frame.get("candidate_config", pd.Series(dtype=object))
    for expected_config in expected_configs:
        matching = frame[configs.map(lambda value: _same_project_path(expected_config, value))]
        if matching.empty:
            return (
                False,
                f"observed={len(observed_rows)}/{len(expected_configs)};config_mismatch",
                "\u4e3b\u6311\u6218\u8005\u914d\u7f6e\u5df2\u53d8\u66f4\u6216\u89c2\u5bdf\u4e0d\u5b8c\u6574\uff1b\u65e7\u7ed3\u679c\u4e0d\u53ef\u590d\u7528\u3002",
            )
        observed_rows.append(matching.iloc[-1])

    names: list[str] = []
    ages: list[float] = []
    backtest_modes: list[str] = []
    for row in observed_rows:
        status = str(row.get("status", "") or "").strip().lower()
        name = str(row.get("candidate_name", "") or "").strip() or "unknown"
        names.append(name)
        age = _timestamp_age_seconds(row.get("generated_at", ""))
        if age is None:
            return False, f"candidate={name};timestamp_invalid", "\u4e3b\u6311\u6218\u8005\u89c2\u5bdf\u65f6\u95f4\u65e0\u6548\uff1b\u8bf7\u91cd\u65b0\u8fd0\u884c\u3002"
        ages.append(age)
        mode = str(row.get("backtest_mode", "") or "").strip()
        if mode:
            backtest_modes.append(mode)
        if status != "observed":
            message = str(row.get("message", "") or "").strip() or "\u4e3b\u6311\u6218\u8005\u89c2\u5bdf\u5931\u8d25\u3002"
            return False, f"{status or 'missing'};candidate={name};age_seconds={max(age, 0):.0f}", message

    max_age = max(ages) if ages else float("inf")
    value = f"observed={len(observed_rows)}/{len(expected_configs)};candidates={','.join(names)};max_age_seconds={max(max_age, 0):.0f}"
    if backtest_modes:
        value += f";backtest={','.join(backtest_modes)}"
    if not all(-30 <= age <= max(max_age_seconds, 60) for age in ages):
        return False, value, f"\u4e3b\u6311\u6218\u8005\u89c2\u5bdf\u5df2\u8d85\u8fc7 {max(max_age_seconds, 60)} \u79d2\uff1b\u8bf7\u91cd\u65b0\u8fd0\u884c\u3002"
    return True, value, "\u767b\u8bb0\u7684\u4e3b\u6311\u6218\u8005\u56de\u6d4b\u3001Shadow \u548c\u524d\u5411\u5ba1\u8ba1\u5df2\u5168\u90e8\u5237\u65b0\u3002"


def _challenger_observation_status(
    frame: pd.DataFrame,
    *,
    configured: bool,
    expected_config: str,
    max_age_seconds: int,
) -> tuple[bool, str, str]:
    if not configured:
        return True, "not_configured", "未配置主候选；一键流程仅观察正式策略。"
    if frame.empty:
        return False, "missing", "缺少主候选观察结果；重新运行一键流程。"
    row = frame.iloc[-1]
    status = str(row.get("status", "") or "").strip().lower()
    name = str(row.get("candidate_name", "") or "").strip() or "unknown"
    observed_config = str(row.get("candidate_config", "") or "").strip()
    identity_ok = _same_project_path(expected_config, observed_config)
    age = _timestamp_age_seconds(row.get("generated_at", ""))
    if age is None:
        return False, f"{status or 'invalid'};candidate={name};timestamp_invalid", "主候选观察时间无效；重新运行一键流程。"
    fresh = -30 <= age <= max(max_age_seconds, 60)
    elapsed = str(row.get("elapsed_seconds", "") or "").strip()
    backtest_mode = str(row.get("backtest_mode", "") or "").strip()
    value = f"{status or 'missing'};candidate={name};age_seconds={max(age, 0):.0f}"
    if elapsed:
        value += f";elapsed_seconds={elapsed}"
    if backtest_mode:
        value += f";backtest={backtest_mode}"
    if status != "observed":
        message = str(row.get("message", "") or "").strip() or "主候选观察失败；查看 primary_challenger_observation.csv。"
        return False, value, message
    if not identity_ok:
        value += ";config_mismatch"
        return False, value, "主候选配置已变更；旧观察结果不可复用，请重新运行一键流程。"
    if not fresh:
        return False, value, f"主候选观察已超过 {max(max_age_seconds, 60)} 秒；重新运行一键流程。"
    return True, value, "主候选回测、Shadow 和前向审计已在本次观察周期刷新。"


def _same_project_path(left: object, right: object) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if not left_value or not right_value:
        return False
    try:
        return project_path(left_value).resolve() == project_path(right_value).resolve()
    except OSError:
        return left_value.replace("\\", "/").casefold() == right_value.replace("\\", "/").casefold()


def _launcher_runtime_status(path: Path, max_runtime_seconds: int) -> tuple[bool, str, str]:
    if not path.exists():
        return True, "direct_or_not_started", "未发现启动器心跳；命令行直接运行不受影响。"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False, "invalid", "本地启动器心跳损坏，请重新运行 run_local.cmd。"
    status = str(payload.get("status", "")).strip().lower()
    updated = pd.to_datetime(payload.get("updated_at", ""), errors="coerce", utc=True)
    if status == "running":
        if pd.isna(updated):
            return False, "stale", "运行中心跳缺少有效时间，请重新运行 run_local.cmd。"
        age = (pd.Timestamp.now(tz="UTC") - updated).total_seconds()
        if age > max(max_runtime_seconds, 60):
            return False, "stale", f"上次运行心跳已停滞 {age:.0f} 秒，可安全重新启动。"
        return True, "running", f"本地流程正在运行，心跳年龄 {max(age, 0):.0f} 秒。"
    if status == "completed":
        return True, "completed", "上次本地流程已正常完成。"
    if status == "failed":
        code = payload.get("exit_code", "unknown")
        return False, "failed", f"上次本地流程失败（退出码 {code}），请查看运行日志。"
    return False, "invalid", "本地启动器心跳状态未知，请重新运行 run_local.cmd。"


def _check_value(frame: pd.DataFrame, check: str) -> str:
    if frame.empty or not {"check", "value"}.issubset(frame.columns):
        return ""
    rows = frame[frame["check"].astype(str) == check]
    return "" if rows.empty else str(rows.iloc[0]["value"])


def _item_value(frame: pd.DataFrame, item: str) -> str:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return ""
    rows = frame[frame["item"].astype(str) == item]
    return "" if rows.empty else str(rows.iloc[0]["value"])


def _first_error(errors: dict[str, str]) -> str:
    for name in ("strategy", "broker"):
        value = str(errors.get(name, "")).strip()
        if value:
            return f"{name}: {value}"
    return ""


def _write_html(path: Path, rows: list[dict[str, object]], headline: str, status: str, strategy_dir: Path, live_dir: Path) -> None:
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    table_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row['item']))}</td>"
        f"<td>{escape(str(row['value']))}</td>"
        f"<td><span class=\"{'ok' if row['passed'] else 'fail'}\">{'通过' if row['passed'] else '未通过'}</span></td>"
        f"<td>{escape(str(row['message']))}</td>"
        "</tr>"
        for row in rows
    )
    strategy_report = (strategy_dir / "report.html").resolve().as_uri()
    live_report = (live_dir / "report.html").resolve().as_uri()
    content = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A 股量化系统 - 本地运行状态</title>
<style>
:root{{--bg:#f5f5f7;--panel:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--blue:#0071e3;--green:#248a3d;--red:#d70015}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI",sans-serif;letter-spacing:0}}
main{{max-width:1120px;margin:0 auto;padding:40px 24px 64px}} h1{{font-size:32px;margin:0 0 8px}} .sub{{color:var(--muted);margin-bottom:24px}}
.status{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:22px;margin-bottom:18px}} .status h2{{font-size:20px;margin:0 0 8px}} .code{{color:var(--muted);font-size:13px}}
.actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}} a{{color:#fff;background:var(--blue);padding:9px 14px;border-radius:7px;text-decoration:none}} a.secondary{{background:#e8e8ed;color:var(--text)}}
.table-wrap{{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px}} table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{padding:12px 14px;text-align:left;border-bottom:1px solid #ececf0;vertical-align:top}} th{{color:var(--muted);font-weight:600}} .ok{{color:var(--green)}} .fail{{color:var(--red)}}
</style></head><body><main>
<h1>本地运行状态</h1><div class="sub">{escape(generated)} · 无需 AI 参与运行</div>
<section class="status"><h2>{escape(headline)}</h2><div class="code">状态：{escape(status)}</div><div class="actions"><a href="{strategy_report}">策略报告</a><a class="secondary" href="{live_report}">华泰审计报告</a></div></section>
<div class="table-wrap"><table><thead><tr><th>检查项</th><th>当前值</th><th>状态</th><th>说明</th></tr></thead><tbody>{table_rows}</tbody></table></div>
</main></body></html>"""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
