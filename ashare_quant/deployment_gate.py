from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import mapping_sha256
from .autolive import auto_live_config_sha256
from .autolive_account import live_ledger_initialization_status
from .autolive_alerts import build_auto_live_alert_audit
from .autolive_integrity import build_command_adapter_file_audit
from .candidate import forward_risk_evaluation_identity, live_strategy_hash, load_candidate_manifest
from .capital_modes import build_capital_mode_assessment
from .config import load_config, project_path
from .data import DataStore, committed_fetch_market_scope
from .diagnostics import latest_universe_history_coverage
from .forward_selection import (
    audit_forward_curve_ledger,
    candidate_set_records,
    candidate_set_sha256,
)
from .huatai_certification import build_huatai_canary_arming_audit
from .io_utils import atomic_to_csv
from .readiness import build_live_readiness_from_outputs
from .shadow_policy import shadow_evaluation_identity
from .strategy_activation import build_active_execution_status


DEPLOYMENT_GATE_COLUMNS = [
    "domain",
    "check",
    "passed",
    "value",
    "threshold",
    "blocking",
    "next_action",
    "message",
]

BROKER_DEPLOYMENT_ROADMAP_COLUMNS = [
    "phase",
    "status",
    "check",
    "passed",
    "allowed_in_read_only_run",
    "authorization",
    "next_action",
    "message",
]

READ_ONLY_BROKER_CHECKS = {
    "client_install_integrity",
    "client_process_ready",
    "client_session_ready",
    "read_only_refresh",
    "account_snapshot_broker_side_audit",
    "broker_readonly_history_integrity",
    "account_snapshot_fresh",
    "adapter_file_integrity",
    "adapter_read_only_replay_certification",
}
LEDGER_AUTHORIZATION_CHECKS = {
    "account_binding",
    "isolated_live_ledger_initialized",
}
CANARY_AUTHORIZATION_CHECKS = {
    "adapter_replay_certification",
    "canary_sandbox_evidence_current",
    "canary_configuration_integrity",
    "canary_capital_affordability",
}
FULL_STRATEGY_CAPITAL_CHECKS = {
    "full_strategy_capital_compatibility",
}

DOMAIN_LABELS = {
    "strategy": "策略",
    "broker": "券商",
    "safety": "安全",
}

CHECK_LABELS = {
    "candidate_cache_isolation": "冻结候选缓存隔离",
    "candidate_observation_current": "候选观察身份与时效",
    "strict_live_readiness": "严格实盘策略门禁",
    "execution_strategy_identity": "执行策略身份绑定",
    "frozen_input_evidence": "冻结输入证据",
    "forward_validation_passed": "前向收益验证通过",
    "index_membership_evidence": "指数成分证据",
    "benchmark_index_mapping_evidence": "基准指数映射证据",
    "candidate_market_data_current": "候选行情日期就绪",
    "pending_attribution_timing_evidence": "待归因信号时点证据",
    "shadow_evaluation_evidence": "Shadow 评估口径证据",

    "forward_risk_evaluation_evidence": "\u524d\u5411\u98ce\u9669\u8bc4\u4f30\u53e3\u5f84\u8bc1\u636e",
    "forward_window_complete": "冻结后前向窗口",
    "client_install_integrity": "客户端安装完整性",
    "client_process_ready": "客户端进程就绪",
    "client_session_ready": "客户端登录会话",
    "read_only_refresh": "账户只读刷新",
    "account_snapshot_broker_side_audit": "券商侧账户快照审计",
    "account_snapshot_audit": "账户快照审计",
    "broker_readonly_history_integrity": "\u53ea\u8bfb\u5386\u53f2\u94fe\u5b8c\u6574\u6027",
    "broker_readonly_history_depth": "\u53ea\u8bfb\u89c2\u5bdf\u6df1\u5ea6",
    "account_snapshot_fresh": "账户快照时效",
    "account_binding": "账户与账本绑定",
    "adapter_file_integrity": "桥接器文件完整性",
    "adapter_read_only_replay_certification": "桥接器真实账户只读回放",
    "adapter_replay_certification": "桥接器生命周期回放",
    "canary_sandbox_evidence_current": "Canary 沙箱证据当前有效",
    "alert_channel_current": "邮件告警时效",
    "canary_configuration_integrity": "Canary 配置完整性",
    "canary_capital_affordability": "Canary 一手资金可负担性",
    "full_strategy_capital_compatibility": "完整策略资金兼容性",
    "full_strategy_capital_ready": "完整策略资金",
    "ready_for_live_strategy_review": "完整策略实盘复核",
    "emergency_stop_active": "紧急停机开启",
    "broker_submit_disabled": "券商提交关闭",
    "unattended_service_disabled": "无人值守服务关闭",
    "arming_certificate_absent": "无遗留武装凭证",
    "isolated_live_ledger_initialized": "隔离实盘账本",
    "broker_read_only_ready": "券商只读连接",
    "broker_execution_ready": "券商执行链路",
}


def write_deployment_gate(
    strategy_config: dict[str, Any],
    live_config: dict[str, Any],
) -> dict[str, Path]:
    strategy_dir = project_path(strategy_config["paths"]["output_dir"])
    live_dir = project_path(live_config["paths"]["output_dir"])
    strategy_dir.mkdir(parents=True, exist_ok=True)

    observation = _read_csv(strategy_dir / "forward_only_candidate_observation.csv")
    selection = _read_csv(strategy_dir / "forward_candidate_selection_summary.csv")
    selected_refresh = _read_csv(strategy_dir / "selected_candidate_evidence_refresh.csv")
    client = _read_csv(live_dir / "huatai_client_status.csv")
    refresh = _read_csv(live_dir / "huatai_read_only_refresh.csv")
    broker_audit = _read_csv(live_dir / "auto_live_broker_account_audit.csv")
    broker_snapshot = _read_csv(live_dir / "auto_live_broker_account_snapshot.csv")
    broker_history_audit = _read_csv(live_dir / "huatai_read_only_history_audit.csv")
    binding = _read_csv(live_dir / "auto_live_account_binding_audit.csv")
    adapter_files = build_command_adapter_file_audit(live_config)
    replay = _read_csv(live_dir / "auto_live_adapter_replay_summary.csv")
    alerts = build_auto_live_alert_audit(live_config)
    canary = build_huatai_canary_arming_audit(live_config)
    canary_capital = _read_csv(live_dir / "broker_canary_capital_audit.csv")
    canary_capital_candidates = _read_csv(live_dir / "broker_canary_capital_candidates.csv")
    capital_transition = _read_csv(live_dir / "broker_capital_transition.csv")
    capital_sensitivity = _read_csv(strategy_dir / "micro_capital_sensitivity.csv")
    active_execution = build_active_execution_status(strategy_config)

    configured = _forward_candidate_configs(strategy_config)
    cache_isolation_ok, cache_isolation_value = forward_candidate_cache_isolation(strategy_config, configured)
    observed_rows = _matching_observation_rows(observation, configured)
    base_identity_results = [_observation_identity_current(row) for row in observed_rows]
    shadow_identity_results = [_observation_shadow_identity_current(row) for row in observed_rows]
    forward_risk_identity_results = [
        _observation_forward_risk_identity_current(row) for row in observed_rows
    ]
    base_identity_current = bool(observed_rows) and all(passed for passed, _ in base_identity_results)
    shadow_identity_current = bool(observed_rows) and all(passed for passed, _ in shadow_identity_results)
    forward_risk_identity_current = bool(observed_rows) and all(
        passed for passed, _ in forward_risk_identity_results
    )
    identity_results = [
        (
            base_passed and shadow_passed and risk_passed,
            f"{base_detail};{shadow_detail};{risk_detail}",
        )
        for (base_passed, base_detail), (shadow_passed, shadow_detail), (risk_passed, risk_detail)
        in zip(base_identity_results, shadow_identity_results, forward_risk_identity_results)
    ]
    identity_current = (
        base_identity_current and shadow_identity_current and forward_risk_identity_current
    )
    observation_max_age_hours = max(
        float(strategy_config.get("observation_task", {}).get("max_age_hours", 36)),
        0.0,
    )
    freshness_results = [
        _observation_freshness(row, observation_max_age_hours)
        for row in observed_rows
    ]
    observations_fresh = bool(observed_rows) and all(passed for passed, _ in freshness_results)
    observation_evidence_current = (
        bool(configured)
        and len(observed_rows) == len(configured)
        and identity_current
        and observations_fresh
    )
    observation_current = (
        observation_evidence_current
        and all(str(row.get("status", "")).strip().lower() == "observed" for row in observed_rows)
    )
    observed_status_count = sum(
        str(row.get("status", "")).strip().lower() == "observed"
        for row in observed_rows
    )
    observation_value = (
        f"rows={len(observed_rows)}/{len(configured)};"
        f"status={observed_status_count}/{len(configured)};"
        f"identity={sum(passed for passed, _ in identity_results)}/{len(configured)};"
        f"fresh={sum(passed for passed, _ in freshness_results)}/{len(configured)}"
    )
    selection_row = _last_row(selection)
    selection_ok, selection_value = _forward_candidate_selection_evidence(
        strategy_config,
        selection_row,
        observed_rows,
    )
    selected_refresh_row = _last_row(selected_refresh)
    refresh_status = str(selected_refresh_row.get("status", "") or "").strip().lower()
    selected_value = selection_row.get("selected_candidate_id", "")
    selected_id = "" if pd.isna(selected_value) else str(selected_value).strip()
    refresh_candidate_value = selected_refresh_row.get("selected_candidate_id", "")
    refresh_candidate_id = (
        "" if pd.isna(refresh_candidate_value) else str(refresh_candidate_value).strip()
    )
    selected_refresh_required = len(observed_rows) > 1 and selection_ok
    selected_refresh_ok = bool(
        not selected_refresh_required
        or (
            refresh_status in {"already_ready", "refreshed_ready"}
            and refresh_candidate_id == selected_id
            and _truth(selected_refresh_row.get("winner_identity_valid"))
            and _truth(selected_refresh_row.get("strict_ready_after"))
            and _truth(selected_refresh_row.get("protected_artifacts_unchanged"))
            and not _truth(selected_refresh_row.get("broker_submit_enabled"))
        )
    )
    selected_refresh_value = (
        f"status={refresh_status or 'missing'};selected={selected_id or 'missing'};"
        f"evidence_candidate={refresh_candidate_id or 'missing'};required={selected_refresh_required}"
    )
    execution_identity_ok, execution_identity_value = _execution_strategy_identity(
        strategy_config,
        observed_rows,
        selection_row,
    )
    execution_identity_ok = observation_current and execution_identity_ok
    active_execution_ok, active_execution_value = _all_checks_passed(active_execution)
    base_evidence_current = (
        bool(configured)
        and len(observed_rows) == len(configured)
        and base_identity_current
    )
    frozen_ok = base_evidence_current and all(_truth(row.get("frozen_input_evidence_passed")) for row in observed_rows)
    membership_ok = base_evidence_current and all(_truth(row.get("membership_evidence_passed")) for row in observed_rows)
    benchmark_mapping_ok = base_evidence_current and all(
        _truth(row.get("benchmark_mapping_evidence_passed")) for row in observed_rows
    )
    shadow_evaluation_ok = base_evidence_current and shadow_identity_current and all(
        _truth(row.get("shadow_evaluation_evidence_passed")) for row in observed_rows
    )
    forward_risk_evaluation_ok = (
        base_evidence_current
        and forward_risk_identity_current
        and all(
            _truth(row.get("forward_risk_evaluation_evidence_passed"))
            for row in observed_rows
        )
    )
    market_data_ok = base_evidence_current and observations_fresh and all(
        _truth(row.get("market_data_current")) for row in observed_rows
    )
    market_data_value = "|".join(
        f"expected={row.get('expected_market_date', '')};"
        f"complete={row.get('complete_market_end', '')};"
        f"benchmark={row.get('benchmark_end', '')};"
        f"calendar={row.get('trade_calendar_covered', '')}"
        for row in observed_rows
    )
    attribution_timing_results = [
        _pending_attribution_timing_evidence(row) for row in observed_rows
    ]
    attribution_timing_ok = base_evidence_current and all(
        passed for passed, _ in attribution_timing_results
    )
    attribution_timing_value = "|".join(detail for _, detail in attribution_timing_results)
    forward_complete = observation_current and all(
        _as_int(row.get("required_forward_trading_days")) > 0
        and _as_int(row.get("remaining_forward_trading_days")) == 0
        and str(row.get("forward_observation_status", "")).strip().lower() == "shadow_window_complete"
        for row in observed_rows
    )
    observed_days = min((_as_int(row.get("max_observed_trading_days")) for row in observed_rows), default=0)
    forward_validation_ok = observation_current and all(
        _forward_validation_passed(row)
        for row in observed_rows
    )
    required_days = max((_as_int(row.get("required_forward_trading_days")) for row in observed_rows), default=0)
    strict_readiness_rows = _strict_readiness_observation_rows(
        observed_rows,
        selection_row,
        selection_ok,
    )
    strict_readiness_results = [
        _candidate_strict_live_readiness(row)
        for row in strict_readiness_rows
    ]
    strict_readiness_ok = (
        observation_current
        and bool(strict_readiness_rows)
        and len(strict_readiness_results) == len(strict_readiness_rows)
        and all(passed for passed, _ in strict_readiness_results)
    )
    evaluated_ids = ",".join(
        str(row.get("candidate_id", "") or "").strip()
        for row in strict_readiness_rows
    )
    strict_readiness_value = (
        f"passed={sum(passed for passed, _ in strict_readiness_results)}/{len(strict_readiness_rows)};"
        f"evaluated={evaluated_ids or 'missing'};"
        + "|".join(detail for _, detail in strict_readiness_results)
    )
    client_row = _last_row(client)
    client_installed = _truth(client_row.get("installed")) and _truth(client_row.get("sha256_match"))
    client_process_ready = (
        str(client_row.get("status", "")).strip().lower() == "trading_process_ready"
        and (_as_int(client_row.get("hexin_process_count")) + _as_int(client_row.get("xiadan_process_count"))) > 0
    )
    refresh_row = _last_row(refresh)
    refresh_ok = str(refresh_row.get("status", "")).strip().lower() == "ok"
    desktop_stage = str(refresh_row.get("desktop_stage", "") or "").strip().lower()
    client_session_ready = bool(
        client_process_ready
        and (
            refresh_ok
            or (
                _as_int(refresh_row.get("visible_client_windows")) > 0
                and _as_int(refresh_row.get("trading_grid_count")) > 0
            )
        )
    )
    account_audit_ok, account_audit_value = _all_checks_passed(broker_audit)
    broker_side_audit = broker_audit
    if {"check", "passed"}.issubset(broker_audit.columns):
        broker_side_audit = broker_audit.loc[
            ~broker_audit["check"].astype(str).str.startswith("local_")
        ]
    broker_side_audit_ok, broker_side_audit_value = _all_checks_passed(broker_side_audit)
    history_integrity_ok, history_integrity_value = _selected_checks_passed(
        broker_history_audit, {"broker_readonly_history_integrity"}
    )
    history_depth_ok, history_depth_value = _selected_checks_passed(
        broker_history_audit, {"broker_readonly_history_depth"}
    )
    snapshot_max_age = int(live_config.get("auto_live", {}).get("account_snapshot_max_age_seconds", 300))
    snapshot_ok, snapshot_value = _broker_snapshot_freshness(broker_snapshot, snapshot_max_age)
    capital_mode = build_capital_mode_assessment(
        capital_transition,
        capital_sensitivity,
        strategy_config,
        snapshot_current=snapshot_ok,
    )
    capital_mode_row = _last_row(capital_mode)
    binding_ok, binding_value = _all_checks_passed(binding)
    adapter_ok, adapter_value = _all_checks_passed(adapter_files)
    replay_row = _last_row(replay)
    replay_status = str(replay_row.get("status", "")).strip().lower()
    replay_fingerprint = str(replay_row.get("auto_live_config_sha256", "") or "").strip()
    expected_replay_fingerprint = auto_live_config_sha256(live_config)
    replay_config_match = bool(replay_fingerprint) and replay_fingerprint == expected_replay_fingerprint
    replay_read_only_ok = replay_status in {"certified_read_only_live", "certified_live"} and replay_config_match
    replay_ok = replay_status == "certified_live" and replay_config_match
    replay_value = f"status={replay_status or 'missing'};config_match={replay_config_match}"
    alert_ok, alert_value = _all_checks_passed(alerts)
    canary_integrity_ok, canary_integrity_value = _selected_checks_passed(
        canary,
        {
            "action_confirmation_required",
            "unattended_service_disabled",
            "canary_hard_limits",
            "canary_bridge_config",
            "certification_path_match",
            "command_uses_canary_bridge",
            "canary_bridge_hash_pinned",
            "certification_hmac_ready",
        },
    )
    canary_sandbox_ok, canary_sandbox_value = _selected_checks_passed(
        canary,
        {
            "buy_form_preview_current",
            "sell_form_preview_current",
            "orders_empty_monitor_current",
            "trades_empty_monitor_current",
            "cancels_empty_monitor_current",
            "live_account_snapshot_current",
            "sandbox_evidence_config_match",
            "sandbox_evidence_certified",
        },
    )

    canary_capital_evidence_ok, canary_capital_evidence_value = _selected_checks_passed(
        canary_capital,
        {"broker_canary_capital_ready"},
    )
    canary_capital_ok = canary_capital_evidence_ok and snapshot_ok
    canary_capital_value = (
        f"evidence={canary_capital_evidence_value};account_snapshot={snapshot_value}"
    )

    capital_transition_row = _last_row(capital_transition)
    full_strategy_capital_ok = bool(
        snapshot_ok
        and _truth(capital_transition_row.get("snapshot_fresh"))
        and _truth(capital_transition_row.get("strategy_identity_preserved"))
        and _truth(capital_transition_row.get("full_strategy_ready"))
    )
    full_strategy_capital_value = (
        f"current={capital_transition_row.get('current_total_asset', 'missing')};"
        f"minimum={capital_transition_row.get('minimum_full_strategy_equity', 'missing')};"
        f"gap={capital_transition_row.get('full_strategy_capital_gap', 'missing')};"
        f"snapshot_current={snapshot_ok};"
        f"identity_preserved={_truth(capital_transition_row.get('strategy_identity_preserved'))}"
    )

    auto = live_config.get("auto_live", {}) if isinstance(live_config.get("auto_live"), dict) else {}
    service = auto.get("service", {}) if isinstance(auto.get("service"), dict) else {}
    emergency_stop = bool(auto.get("emergency_stop", True))
    submit_disabled = not bool(auto.get("allow_broker_submit", False))
    service_disabled = not bool(service.get("enabled", False)) and not bool(service.get("allow_submit", False))
    cert_cfg = auto.get("desktop_canary_certification", {})
    cert_path = project_path(cert_cfg.get("certification_path", "outputs_live/huatai_desktop_canary_certification.json"))
    certificate_absent = not cert_path.exists()
    ledger_initialized, ledger_value = live_ledger_initialization_status(live_config)

    committed_history_ok = True
    committed_history_value = "not_applicable"
    try:
        strategy_store = DataStore.from_config(strategy_config)
        strategy_stocks, strategy_prices, _ = strategy_store.load_market_data()
        scoped_prices, scope_ready, scope_value = committed_fetch_market_scope(
            strategy_store, strategy_prices
        )
        if scope_ready:
            coverage = latest_universe_history_coverage(
                strategy_stocks, scoped_prices, strategy_config
            )
            minimum = float(
                strategy_config.get("live_guard", {}).get(
                    "min_latest_universe_history_coverage_ratio", 0.9
                )
            )
            ratio = float(coverage.get("ratio", 0.0) or 0.0)
            committed_history_ok = bool(coverage and ratio >= minimum)
            committed_history_value = (
                f"covered={coverage.get('covered', 0)}/{coverage.get('expected', 0)};"
                f"ratio={ratio:.4f};latest={coverage.get('latest', 'missing')};"
                f"{scope_value}"
            )
    except FileNotFoundError:
        committed_history_ok = True
        committed_history_value = "not_applicable"
    except (OSError, ValueError, TypeError, KeyError):
        committed_history_ok = False
        committed_history_value = "audit_failed"

    rows = [
        _row("strategy", "candidate_cache_isolation", cache_isolation_ok, cache_isolation_value, "base cache and all frozen candidate caches are distinct", "为每个冻结候选配置独立 cache_dir。", "冻结候选不得与默认策略或其他候选共用可写行情缓存。"),
        _row("strategy", "candidate_observation_current", observation_current, observation_value, f"all registered candidates observed, identity-bound, age <= {observation_max_age_hours:g}h", "运行 forward-daily。", "候选身份、冻结策略哈希、输出目录和最新观察时效必须匹配登记配置。"),
        _row("strategy", "frozen_input_evidence", frozen_ok, frozen_ok, True, "恢复冻结输入或重新登记候选，不得覆盖旧哈希。", "冻结股票、行情、财务和基准数据哈希必须完整。"),
        _row("strategy", "index_membership_evidence", membership_ok, membership_ok, True, "恢复冻结指数成分证据。", "时点指数成分行数与哈希必须匹配。"),
        _row("strategy", "benchmark_index_mapping_evidence", benchmark_mapping_ok, benchmark_mapping_ok, True, "重新冻结并核验基准指数映射。", "内部、BaoStock 与新浪代码必须与候选冻结身份一致。"),
        _row("strategy", "shadow_evaluation_evidence", shadow_evaluation_ok, shadow_evaluation_ok, True, "恢复冻结的 Shadow 评估口径或重新登记候选。", "前向收益必须使用冻结的含现金总组合与成本口径。"),

        _row("strategy", "forward_risk_evaluation_evidence", forward_risk_evaluation_ok, forward_risk_evaluation_ok, True, "\u6062\u590d\u51bb\u7ed3\u7684\u524d\u5411\u98ce\u9669\u8bc4\u4f30\u53e3\u5f84\u3002", "\u4e3b\u52a8\u56de\u64a4\u3001\u8ddf\u8e2a\u8bef\u5dee\u548c\u4fe1\u606f\u6bd4\u7387\u5fc5\u987b\u4f7f\u7528\u51bb\u7ed3\u7684\u5b9a\u4e49\u4e0e\u9608\u503c\u3002"),
        _row("strategy", "candidate_market_data_current", market_data_ok, market_data_value, "complete and benchmark dates >= expected finalized session", "等待数据源完成当日收盘数据并自动重试。", "候选完整行情、基准和交易日历必须覆盖预期交易日。"),
        _row("strategy", "committed_universe_history_coverage", committed_history_ok, committed_history_value, ">= 90% of latest committed universe has history from listing/backtest start", "补齐当前抓取股票池的长期历史后重新回测。", "当前股票池历史过浅会把当前成分倒灌到历史并夸大回测收益，因此禁止策略晋级。"),
        _row("strategy", "pending_attribution_timing_evidence", attribution_timing_ok, attribution_timing_value, "pending signals require a close strictly after the earliest execution date", "重新运行 forward-daily 生成带归因边界的不可改写观察证据。", "待归因信号必须明确区分执行日建仓与后续交易日收盘收益，字段缺失或日期不一致时禁止晋级。"),
        _row("strategy", "forward_window_complete", forward_complete, f"{observed_days}/{required_days}", f"{required_days}/{required_days}", "继续每日运行 forward-daily，禁止提前晋级。", "只统计冻结后真实新增的影子观察交易日。"),
        _row("strategy", "strict_live_readiness", strict_readiness_ok, strict_readiness_value, "all strict live-readiness checks rebuilt from current candidate evidence", "刷新候选回测、成本压力、参数稳定性和前向证据，所有严格实盘检查必须通过。", "总部署门禁会从候选原始产物重新计算严格实盘准备度，不能只依赖前向窗口或手工修改汇总 CSV。"),
        _row("broker", "client_install_integrity", client_installed, str(client_row.get("status", "missing")), "installed and executable hash matched", "重新检查华泰客户端安装与文件哈希。", "客户端程序必须来自已固定的本机路径。"),
        _row("strategy", "forward_validation_passed", forward_validation_ok, forward_validation_ok, True, "继续运行 forward-daily；满期后前向超额必须达标。", "仅收满观察天数不够，冻结候选的前向收益验证也必须通过。"),
        _row("strategy", "forward_candidate_selection", selection_ok, selection_value, "one preregistered paired winner after the common forward window", "继续累计共同前向交易日；只有配对优势达到冻结阈值才能选中执行候选。", "多候选必须使用冻结后的配对日收益决赛，不得人工挑选最好回测。"),
        _row("strategy", "selected_candidate_evidence_refresh", selected_refresh_ok, selected_refresh_value, "waiting before selection; strict-ready identity-bound winner after selection", "胜者产生后自动刷新其回测、成本压力和整手资金证据；失败时保持未激活。", "该阶段只刷新研究证据，必须保持激活记录、订单、RPA 工单和券商提交状态不变。"),
        _row("strategy", "execution_strategy_identity", execution_identity_ok, execution_identity_value, "one frozen candidate and execution strategy hash match", "将一键运行配置切换到已完成前向验证的冻结候选；不得只替换报告或观察配置。", "部署门禁认可的策略必须与实际生成订单的执行策略完全一致。"),
        _row("strategy", "active_execution_loader_integrity", active_execution_ok, active_execution_value, "all checks passed", "停用损坏的激活记录，或使用不带显式 --config 的默认一键入口重新加载。", "激活记录、默认加载器和实际执行策略哈希必须一致，显式配置不得绕过。"),
        _row("broker", "client_process_ready", client_process_ready, str(client_row.get("status", "missing")), "trading_process_ready", "重新启动华泰客户端交易进程。", "行情和交易进程必须同时运行。"),
        _row("broker", "client_session_ready", client_session_ready, desktop_stage or "unverified", "visible authenticated trading window", "重新打开华泰客户端、登录并停留在资金股票页面。", "仅有后台进程不能证明登录会话可读。"),
        _row("broker", "read_only_refresh", refresh_ok, str(refresh_row.get("status", "missing")), "ok", "运行 broker-refresh；该命令不提交订单。", "最近一次真实账户只读刷新必须成功。"),
        _row("broker", "account_snapshot_broker_side_audit", broker_side_audit_ok, broker_side_audit_value, "all broker-side checks passed", "重新执行华泰只读账户刷新。", "券商侧资金、持仓、身份和控件映射必须通过审计；该检查不要求创建本地实盘账本。", blocking=False),
        _row("broker", "broker_readonly_history_integrity", history_integrity_ok, history_integrity_value, "history hash chain and current tail verified", "\u91cd\u65b0\u6267\u884c\u534e\u6cf0\u53ea\u8bfb\u5237\u65b0\uff0c\u4fee\u590d\u6216\u91cd\u5efa\u635f\u574f\u7684\u5386\u53f2\u8bc1\u636e\u3002", "\u6bcf\u4e2a\u6210\u529f\u5feb\u7167\u90fd\u5fc5\u987b\u901a\u8fc7\u9632\u7be1\u6539\u54c8\u5e0c\u94fe\u4e0e\u5f53\u524d\u5feb\u7167\u5339\u914d\u6821\u9a8c\u3002"),
        _row("broker", "broker_readonly_history_depth", history_depth_ok, history_depth_value, "at least 3 successful snapshots across at least 2 dates", "\u5728\u4e0d\u540c\u65e5\u671f\u7ee7\u7eed\u6267\u884c\u53ea\u8bfb\u5237\u65b0\uff1b\u4e0d\u5f97\u56e0\u5355\u6b21\u6210\u529f\u653e\u884c\u5b9e\u76d8\u6267\u884c\u3002", "\u6267\u884c\u590d\u6838\u8981\u6c42\u8de8\u65e5\u91cd\u590d\u8bfb\u53d6\u6210\u529f\uff0c\u4ee5\u66b4\u9732\u4f1a\u8bdd\u3001\u63a7\u4ef6\u6620\u5c04\u548c\u8d26\u6237\u8eab\u4efd\u7684\u95f4\u6b47\u6027\u95ee\u9898\u3002"),
        _row("broker", "account_snapshot_audit", account_audit_ok, account_audit_value, "all broker and local reconciliation checks passed", "经授权初始化隔离实盘账本后，再执行本地与券商对账。" if broker_side_audit_ok else "重新执行华泰只读账户刷新。", "执行链路还要求本地实盘账本与券商快照逐项对账；不影响只读连接结论。"),
        _row("broker", "account_snapshot_fresh", snapshot_ok, snapshot_value, f"age <= {snapshot_max_age}s", "登录客户端后重新执行 broker-refresh。", "余额和持仓快照不能复用过期数据。"),
        _row("broker", "account_binding", binding_ok, binding_value, "all checks passed", "完成账本初始化后，以当前配置重新签署账户绑定。", "真实账本必须绑定唯一脱敏券商账户。"),
        _row("broker", "adapter_file_integrity", adapter_ok, adapter_value, "all checks passed", "复核桥接代码变化并更新固定哈希。", "命令适配器及其完整依赖必须通过哈希固定。"),
        _row("broker", "adapter_read_only_replay_certification", replay_read_only_ok, replay_value, "certified_read_only_live or certified_live and current auto_live config", "重新执行真实账户只读刷新并生成回放审计。", "该检查只证明真实账户读取协议，不证明下单、查询或撤单能力。", blocking=False),
        _row("broker", "adapter_replay_certification", replay_ok, replay_value, "certified_live and current auto_live config", "在受控真实环境完成提交、查询、撤单和账户快照生命周期认证。", "完整生命周期回放必须绑定当前配置，并覆盖幂等性和券商单号稳定性。"),
        _row("broker", "alert_channel_current", alert_ok, alert_value, "all checks passed", "重新运行邮件告警自检。", "外部告警配置、HMAC 和时效必须全部通过。"),
        _row("broker", "canary_sandbox_evidence_current", canary_sandbox_ok, canary_sandbox_value, "all 8 dynamic read-only checks current", "重新登录客户端并依次刷新表单预览、空委托监控和账户快照，再生成沙箱汇总。", "动态沙箱摘要及其买卖预览、委托、成交、撤单和账户快照原始证据必须绑定当前配置且仍在有效期内。"),
        _row("broker", "canary_configuration_integrity", canary_integrity_ok, canary_integrity_value, "selected integrity checks passed", "重新生成当前配置下的桌面 Canary 沙箱证据。", "Canary 限额、确认链和桥接配置必须与当前哈希一致。"),
        _row("broker", "canary_capital_affordability", canary_capital_ok, canary_capital_value, "fresh live available cash can afford one eligible board lot within the 1000 yuan Canary limit", "重新登录客户端并刷新可用资金；Canary 只允许一笔不超过 1000 元的整手验证。", "小额 Canary 只验证执行链，不能替代完整策略的资金可执行性。"),
        _row("broker", "full_strategy_capital_compatibility", full_strategy_capital_ok, full_strategy_capital_value, "fresh broker total assets >= frozen full-strategy minimum equity with identity preserved", "保持完整策略禁用；小额账户仅可继续隔离的执行 Canary 复核，不得把一手验证视为策略部署。", "完整策略资金门禁独立于 Canary；系统不会建议入金，也不会自动缩减或改写冻结策略。", blocking=False),
        _row("broker", "capital_operating_mode", True, str(capital_mode_row.get("mode_label_cn", "证据缺失")), "refresh_required / observation_only / canary_review_only / full_strategy_review", str(capital_mode_row.get("reason_cn", "重新生成资金可行性证据。")), "运行模式仅解释资金证据，不授权下单，也不改变任何正式门禁。", blocking=False),
        _row("safety", "emergency_stop_active", emergency_stop, emergency_stop, True, "保持急停开启，直到人工批准单次 Canary。", "只读阶段必须保持急停。"),
        _row("safety", "broker_submit_disabled", submit_disabled, submit_disabled, True, "保持 allow_broker_submit=false。", "该门禁不得开启券商提交。"),
        _row("safety", "unattended_service_disabled", service_disabled, service_disabled, True, "保持自动服务禁用。", "Canary 前不得启动无人值守提交服务。"),
        _row("safety", "arming_certificate_absent", certificate_absent, certificate_absent, True, "撤销遗留武装证书。", "只读阶段不应存在短时提交凭证。"),
        _row("broker", "isolated_live_ledger_initialized", ledger_initialized, ledger_value, "configured SQLite contains at least one account snapshot", "经用户逐项核对快照后，再显式初始化隔离实盘账本。", "该检查只报告状态，不会创建数据库。"),
    ]

    strategy_ready = all(row["passed"] for row in rows if row["domain"] == "strategy")
    broker_read_only_ready = all(
        row["passed"] for row in rows
        if row["domain"] == "broker" and row["check"] in READ_ONLY_BROKER_CHECKS
    )
    broker_execution_ready = all(row["passed"] for row in rows if row["domain"] == "broker" and row["blocking"])
    broker_ready = broker_execution_ready
    safety_ready = all(row["passed"] for row in rows if row["domain"] == "safety")
    ready = strategy_ready and broker_ready and safety_ready
    ready_for_live_strategy_review = ready and full_strategy_capital_ok
    fallback = "该域已通过，保持当前证据新鲜度。"
    strategy_action = next((row["next_action"] for row in rows if row["domain"] == "strategy" and row["blocking"] and not row["passed"]), fallback)
    broker_read_only_action = next((row["next_action"] for row in rows if row["domain"] == "broker" and row["check"] in READ_ONLY_BROKER_CHECKS and not row["passed"]), fallback)
    broker_action = next((row["next_action"] for row in rows if row["domain"] == "broker" and row["blocking"] and not row["passed"]), fallback)
    safety_action = next((row["next_action"] for row in rows if row["domain"] == "safety" and row["blocking"] and not row["passed"]), "保持当前停机保护。")
    capital_action = (
        fallback
        if full_strategy_capital_ok
        else "保持完整策略禁用；小额账户仅可继续隔离的执行 Canary 复核。"
    )
    next_action = strategy_action if not strategy_ready else broker_action if not broker_ready else safety_action if not safety_ready else "可提交人工 Canary 武装申请；仍不得自动下单。"
    rows.extend(
        [
            _row("summary", "strategy_ready", strategy_ready, strategy_ready, True, strategy_action, "冻结候选前向证据结论。", blocking=False),
            _row("summary", "broker_read_only_ready", broker_read_only_ready, broker_read_only_ready, True, broker_read_only_action, "华泰登录会话、真实账户只读采集及券商侧账户审计结论。", blocking=False),
            _row("summary", "broker_execution_ready", broker_execution_ready, broker_execution_ready, True, broker_action, "本地实盘账本、绑定、完整生命周期回放和 Canary 证据结论。", blocking=False),
            _row("summary", "broker_ready", broker_ready, broker_ready, True, broker_action, "兼容字段，等同 broker_execution_ready，不代表只读连接失败。", blocking=False),
            _row("summary", "full_strategy_capital_ready", full_strategy_capital_ok, full_strategy_capital_ok, True, capital_action, "当前真实账户资产必须满足冻结策略的整手复制门槛；与一手 Canary 分开判断。", blocking=False),
            _row("summary", "safe_default", safety_ready, safety_ready, True, safety_action, "提交关闭、急停开启、服务禁用。", blocking=False),
            _row("summary", "ready_to_request_canary", ready, ready, True, next_action, "仅表示可以申请人工 Canary 复核，不代表获准下单。", blocking=False),
            _row("summary", "ready_for_live_strategy_review", ready_for_live_strategy_review, ready_for_live_strategy_review, True, capital_action if ready else next_action, "表示策略、券商执行、安全和完整策略资金门禁均已满足，仍只允许申请人工实盘复核。", blocking=False),
        ]
    )

    broker_roadmap = _build_broker_deployment_roadmap(rows)
    broker_roadmap_path = strategy_dir / "broker_deployment_roadmap.csv"
    atomic_to_csv(broker_roadmap, broker_roadmap_path, index=False, encoding="utf-8-sig")

    frame = pd.DataFrame(rows, columns=DEPLOYMENT_GATE_COLUMNS)
    csv_path = strategy_dir / "deployment_gate.csv"
    html_path = strategy_dir / "deployment_gate.html"
    atomic_to_csv(frame, csv_path, index=False, encoding="utf-8-sig")
    _write_html(html_path, frame, next_action)
    display_paths = {
        "broker_canary_capital_audit.csv": strategy_dir / "broker_canary_capital_audit.csv",
        "broker_canary_capital_candidates.csv": strategy_dir / "broker_canary_capital_candidates.csv",
        "broker_capital_transition.csv": strategy_dir / "broker_capital_transition.csv",
        "capital_mode_assessment.csv": strategy_dir / "capital_mode_assessment.csv",
    }
    for name, display_frame in {
        "broker_canary_capital_audit.csv": canary_capital,
        "broker_canary_capital_candidates.csv": canary_capital_candidates,
        "broker_capital_transition.csv": capital_transition,
        "capital_mode_assessment.csv": capital_mode,
    }.items():
        atomic_to_csv(display_frame, display_paths[name], index=False, encoding="utf-8-sig")
    return {
        "deployment_gate.csv": csv_path,
        "deployment_gate.html": html_path,
        "broker_deployment_roadmap.csv": broker_roadmap_path,
        **display_paths,
    }


def _pending_attribution_timing_evidence(row: pd.Series) -> tuple[bool, str]:
    pending_count = _as_int(row.get("pending_signal_count"))
    if pending_count <= 0:
        return True, "no_pending_signals"
    execution_date = pd.to_datetime(row.get("next_pending_execution_date"), errors="coerce")
    close_after = pd.to_datetime(
        row.get("first_attribution_requires_close_after"), errors="coerce"
    )
    passed = bool(
        not pd.isna(execution_date)
        and not pd.isna(close_after)
        and close_after == execution_date
    )
    execution_value = "missing" if pd.isna(execution_date) else execution_date.date().isoformat()
    close_after_value = "missing" if pd.isna(close_after) else close_after.date().isoformat()
    return (
        passed,
        f"pending={pending_count};execution={execution_value};close_after={close_after_value}",
    )


def _forward_validation_passed(observation_row: dict[str, Any]) -> bool:
    output_value = str(observation_row.get("candidate_output_dir", "") or "").strip()
    candidate_id = str(observation_row.get("candidate_id", "") or "").strip()
    if not output_value or not candidate_id:
        return False
    validation = _read_csv(Path(output_value) / "live_candidate_validation.csv")
    if validation.empty or not {"check", "passed", "value"}.issubset(validation.columns):
        return False
    identity = validation[validation["check"].astype(str) == "candidate_exists"]
    status = validation[validation["check"].astype(str) == "forward_validation_status"]
    if identity.empty or status.empty:
        return False
    return bool(
        str(identity.iloc[-1].get("value", "") or "").strip() == candidate_id
        and _truth(status.iloc[-1].get("passed"))
        and str(status.iloc[-1].get("value", "") or "").strip().lower() == "validated"
    )


def _strict_readiness_observation_rows(
    observed_rows: list[pd.Series],
    selection_row: pd.Series,
    selection_ok: bool,
) -> list[pd.Series]:
    if len(observed_rows) <= 1 or not selection_ok:
        return observed_rows
    selected_value = selection_row.get("selected_candidate_id", "")
    selected_id = "" if pd.isna(selected_value) else str(selected_value).strip()
    return [
        row
        for row in observed_rows
        if str(row.get("candidate_id", "") or "").strip() == selected_id
    ]

def _candidate_strict_live_readiness(
    observation_row: dict[str, Any],
) -> tuple[bool, str]:
    configured_path = str(observation_row.get("candidate_config", "") or "").strip()
    candidate_id = str(observation_row.get("candidate_id", "") or "").strip()
    if not configured_path or not candidate_id:
        return False, "candidate_identity_missing"
    try:
        candidate_config = load_config(project_path(configured_path))
        readiness = build_live_readiness_from_outputs(candidate_config)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return False, f"rebuild_failed:{type(exc).__name__}"
    if readiness.empty or not {"check", "passed", "value"}.issubset(readiness.columns):
        return False, "strict_readiness_missing"

    identity = readiness[readiness["check"].astype(str) == "candidate_exists"]
    if identity.empty or str(identity.iloc[-1].get("value", "") or "").strip() != candidate_id:
        return False, "candidate_id_mismatch"
    passed = readiness["passed"].map(_truth)
    failed = readiness.loc[~passed, "check"].astype(str).tolist()
    detail = f"checks={int(passed.sum())}/{len(readiness)}"
    if failed:
        detail += ";failed=" + ",".join(failed[:8])
        if len(failed) > 8:
            detail += f",+{len(failed) - 8}"
    return bool(passed.all()), detail


def _forward_candidate_selection_evidence(
    config: dict[str, Any],
    selection_row: pd.Series,
    observed_rows: list[pd.Series],
) -> tuple[bool, str]:
    if len(observed_rows) <= 1:
        return True, "single_candidate_policy_not_required"
    if selection_row.empty:
        return False, f"candidate_count={len(observed_rows)};selection=missing"
    records = candidate_set_records(pd.DataFrame(observed_rows))
    expected_hash = candidate_set_sha256(records)
    actual_hash = str(selection_row.get("candidate_set_sha256", "") or "").strip()
    selected_value = selection_row.get("selected_candidate_id", "")
    selected_id = "" if pd.isna(selected_value) else str(selected_value).strip()
    candidate_ids = {
        str(row.get("candidate_id", "") or "").strip() for row in observed_rows
    }
    ledger_ok, ledger_value = audit_forward_curve_ledger(config, records, selection_row)
    status = str(selection_row.get("status", "") or "").strip().lower()
    observed_days = _as_int(
        selection_row.get("minimum_observed_forward_trading_days")
    )
    minimum_days = _as_int(selection_row.get("minimum_common_trading_days"))
    required_days = _as_int(selection_row.get("required_common_trading_days"))
    evidence_after = str(selection_row.get("evidence_after", "") or "").strip()
    passed = bool(
        len(records) == len(observed_rows)
        and expected_hash
        and actual_hash == expected_hash
        and _truth(selection_row.get("policy_valid"))
        and _truth(selection_row.get("selection_ready"))
        and status == "ready"
        and selected_id in candidate_ids
        and required_days > 0
        and minimum_days >= required_days
        and ledger_ok
    )
    return (
        passed,
        f"status={status or 'missing'};selected={selected_id or 'missing'};"
        f"observed_days={observed_days};paired_post_policy_days={minimum_days}/{required_days};"
        f"policy_after={evidence_after or 'missing'};"
        f"set_match={bool(expected_hash and actual_hash == expected_hash)};ledger={ledger_value}",
    )


def _execution_strategy_identity(
    strategy_config: dict[str, Any],
    observed_rows: list[pd.Series],
    selection_row: pd.Series | None = None,
) -> tuple[bool, str]:
    execution_hash = live_strategy_hash(strategy_config)
    if not observed_rows:
        return False, f"candidate_count=0;execution_hash={execution_hash[:16]}"
    if len(observed_rows) == 1:
        row = observed_rows[0]
    else:
        selection_row = selection_row if selection_row is not None else pd.Series(dtype=object)
        selected_value = selection_row.get("selected_candidate_id", "")
        selected_id = "" if pd.isna(selected_value) else str(selected_value).strip()
        selection_ready = bool(
            not selection_row.empty
            and _truth(selection_row.get("selection_ready"))
            and str(selection_row.get("status", "") or "").strip().lower() == "ready"
        )
        matches = [
            row
            for row in observed_rows
            if str(row.get("candidate_id", "") or "").strip() == selected_id
        ]
        if not selection_ready or len(matches) != 1:
            return (
                False,
                f"candidate_count={len(observed_rows)};selected={selected_id or 'missing'};"
                f"selection_ready={selection_ready};execution_hash={execution_hash[:16]}",
            )
        row = matches[0]
    configured_path = str(row.get("candidate_config", "") or "").strip()
    if not configured_path:
        return False, f"candidate_config=missing;execution_hash={execution_hash[:16]}"
    try:
        candidate_config = load_config(project_path(configured_path))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return False, f"candidate_config={type(exc).__name__};execution_hash={execution_hash[:16]}"
    manifest = load_candidate_manifest(candidate_config)
    if not manifest:
        return False, f"candidate_manifest=missing;execution_hash={execution_hash[:16]}"
    candidate_id = str(manifest.get("candidate_id", "") or "").strip()
    candidate_hash = str(manifest.get("strategy_hash", "") or "").strip()
    observed_candidate_id = str(row.get("candidate_id", "") or "").strip()
    passed = bool(
        candidate_id
        and candidate_id == observed_candidate_id
        and candidate_hash
        and candidate_hash == execution_hash
    )
    return (
        passed,
        f"candidate={candidate_id or 'missing'};candidate_hash={candidate_hash[:16] or 'missing'};"
        f"execution_hash={execution_hash[:16]}",
    )

def forward_candidate_cache_isolation(
    base_config: dict[str, Any],
    configured: list[str] | None = None,
) -> tuple[bool, str]:
    candidate_paths = configured if configured is not None else _forward_candidate_configs(base_config)
    base_value = str(base_config.get("paths", {}).get("cache_dir", "") or "").strip()
    if not base_value:
        return False, "base_cache_missing"
    base_cache = project_path(base_value).resolve()
    seen: dict[Path, str] = {}
    details: list[str] = []
    passed = bool(candidate_paths)
    for configured_path in candidate_paths:
        try:
            candidate_config = load_config(project_path(configured_path))
            cache_value = str(candidate_config.get("paths", {}).get("cache_dir", "") or "").strip()
            if not cache_value:
                raise ValueError("cache_dir missing")
            candidate_cache = project_path(cache_value).resolve()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            passed = False
            details.append(f"{configured_path}=unreadable:{type(exc).__name__}")
            continue
        if candidate_cache == base_cache:
            passed = False
            details.append(f"{configured_path}=shared_with_base")
            continue
        prior = seen.get(candidate_cache)
        if prior is not None:
            passed = False
            details.append(f"{configured_path}=shared_with:{prior}")
            continue
        seen[candidate_cache] = configured_path
        details.append(f"{configured_path}=isolated:{candidate_cache}")
    return passed, ";".join(details) if details else "no_forward_candidates"

def _forward_candidate_configs(config: dict[str, Any]) -> list[str]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    values = validation.get("forward_only_candidate_configs", [])
    return [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []


def _matching_observation_rows(frame: pd.DataFrame, configured: list[str]) -> list[pd.Series]:
    if frame.empty or "candidate_config" not in frame.columns:
        return []
    rows: list[pd.Series] = []
    for configured_path in configured:
        expected = project_path(configured_path).resolve()
        matching = frame[
            frame["candidate_config"].map(

                lambda value: _same_path(expected, project_path(str(value)).resolve())
            )
        ]
        if not matching.empty:
            rows.append(matching.iloc[-1])
    return rows

def _observation_identity_current(row: pd.Series) -> tuple[bool, str]:
    configured_path = str(row.get("candidate_config", "") or "").strip()
    if not configured_path:
        return False, "candidate_config_missing"
    try:
        candidate_config = load_config(project_path(configured_path))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return False, f"candidate_config_unreadable:{type(exc).__name__}"

    validation = (
        candidate_config.get("validation", {})
        if isinstance(candidate_config.get("validation", {}), dict)
        else {}
    )
    if not bool(validation.get("research_only", False)):
        return False, "research_only_not_enabled"
    if str(validation.get("experiment_status", "") or "").strip() != "preregistered_forward_only":
        return False, "experiment_status_mismatch"

    manifest = load_candidate_manifest(candidate_config)
    if not manifest:
        return False, "candidate_manifest_missing"
    candidate_id = str(row.get("candidate_id", "") or "").strip()
    manifest_id = str(manifest.get("candidate_id", "") or "").strip()
    if not candidate_id or candidate_id != manifest_id:
        return False, "candidate_id_mismatch"
    frozen_hash = str(manifest.get("strategy_hash", "") or "").strip()
    if not frozen_hash or frozen_hash != live_strategy_hash(candidate_config):
        return False, "strategy_hash_mismatch"

    if str(manifest.get("freeze_mode", "") or "").strip() != "forward_only_preregistration":
        return False, "freeze_mode_mismatch"
    if bool(manifest.get("promotion_eligible_at_freeze", True)):
        return False, "promotion_eligibility_mismatch"

    observed_name = str(row.get("candidate_name", "") or "").strip()
    manifest_name = str(manifest.get("strategy_name", "") or "").strip()
    if not observed_name or observed_name != manifest_name:
        return False, "strategy_name_mismatch"
    observed_output = str(row.get("candidate_output_dir", "") or "").strip()
    configured_output = str(candidate_config.get("paths", {}).get("output_dir", "") or "").strip()
    if not observed_output or not configured_output:
        return False, "candidate_output_missing"
    if not _same_path(Path(observed_output).resolve(), project_path(configured_output).resolve()):
        return False, "candidate_output_mismatch"
    return True, f"candidate_id={candidate_id};strategy_hash={frozen_hash[:16]}"


def _observation_shadow_identity_current(row: pd.Series) -> tuple[bool, str]:
    configured_path = str(row.get("candidate_config", "") or "").strip()
    if not configured_path:
        return False, "candidate_config_missing"
    try:
        candidate_config = load_config(project_path(configured_path))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return False, f"candidate_config_unreadable:{type(exc).__name__}"
    observed_hash = str(row.get("shadow_evaluation_sha256", "") or "").strip()
    current_hash = mapping_sha256(shadow_evaluation_identity(candidate_config))
    if not observed_hash:
        return False, "shadow_evaluation_sha256_missing"
    if observed_hash != current_hash:
        return False, "shadow_evaluation_identity_mismatch"
    return True, f"shadow_evaluation_sha256={current_hash[:16]}"

def _observation_forward_risk_identity_current(row: pd.Series) -> tuple[bool, str]:
    configured_path = str(row.get("candidate_config", "") or "").strip()
    if not configured_path:
        return False, "candidate_config_missing"
    try:
        candidate_config = load_config(project_path(configured_path))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return False, f"candidate_config_unreadable:{type(exc).__name__}"
    observed_hash = str(row.get("forward_risk_evaluation_sha256", "") or "").strip()
    current_hash = mapping_sha256(forward_risk_evaluation_identity(candidate_config))
    if not observed_hash:
        return False, "forward_risk_evaluation_sha256_missing"
    if observed_hash != current_hash:
        return False, "forward_risk_evaluation_identity_mismatch"
    return True, f"forward_risk_evaluation_sha256={current_hash[:16]}"


def _observation_freshness(
    row: pd.Series,
    max_age_hours: float,
    *,
    now: pd.Timestamp | None = None,
) -> tuple[bool, str]:
    raw = str(row.get("generated_at", "") or "").strip()
    if not raw:
        return False, "generated_at_missing"
    generated = pd.to_datetime(raw, errors="coerce")
    if pd.isna(generated):
        return False, "generated_at_invalid"
    if generated.tzinfo is None:
        local_timezone = datetime.now().astimezone().tzinfo
        generated = generated.tz_localize(local_timezone)
    generated = generated.tz_convert("UTC")
    current = now if now is not None else pd.Timestamp.now(tz="UTC")
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")
    age_seconds = float((current - generated).total_seconds())
    threshold_seconds = max_age_hours * 3600.0
    passed = -300.0 <= age_seconds <= threshold_seconds
    return passed, f"age_seconds={age_seconds:.1f};max_age_hours={max_age_hours:g}"


def _same_path(left: Path, right: Path) -> bool:
    return str(left).replace("\\", "/").casefold() == str(right).replace("\\", "/").casefold()


def _all_checks_passed(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False, "missing"
    passed = frame["passed"].map(_truth)
    return bool(passed.all()), f"{int(passed.sum())}/{len(passed)}"


def _selected_checks_passed(frame: pd.DataFrame, required: set[str]) -> tuple[bool, str]:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False, "missing"
    selected = frame[frame["check"].astype(str).isin(required)]
    present = set(selected["check"].astype(str))
    passed = selected["passed"].map(_truth)
    ok = present == required and bool(passed.all())
    return ok, f"{int(passed.sum())}/{len(required)}"


def _freshness(frame: pd.DataFrame, max_age_seconds: int) -> tuple[bool, str]:
    if frame.empty:
        return False, "missing"
    timestamp_column = "generated_at" if "generated_at" in frame.columns else "broker_time" if "broker_time" in frame.columns else ""
    if not timestamp_column:
        return False, "timestamp_missing"
    generated = pd.to_datetime(frame.iloc[-1].get(timestamp_column), errors="coerce", utc=True)
    if pd.isna(generated):
        return False, "timestamp_invalid"
    age = float((pd.Timestamp.now(tz="UTC") - generated).total_seconds())
    return -30 <= age <= max(max_age_seconds, 1), f"age_seconds={max(age, 0):.1f}"


def _broker_snapshot_freshness(frame: pd.DataFrame, max_age_seconds: int) -> tuple[bool, str]:
    fresh, age_value = _freshness(frame, max_age_seconds)
    if len(frame) != 1:
        return False, f"{age_value};snapshot_rows={len(frame)}"

    row = frame.iloc[0]
    run_id = str(row.get("snapshot_run_id", "") or "").strip()
    account_id = str(row.get("account_id", "") or "").strip()
    environment = str(row.get("environment", "") or "").strip().lower()
    broker = str(row.get("broker", "") or "").strip().lower()
    reasons: list[str] = []
    if not run_id or run_id.lower().startswith("invalid-"):
        reasons.append("snapshot_run_id_invalid")
    if not account_id:
        reasons.append("account_id_missing")
    if environment != "live":
        reasons.append(f"environment={environment or 'missing'}")
    if broker != "huatai":
        reasons.append(f"broker={broker or 'missing'}")
    for field in ("cash", "available_cash", "market_value", "total_asset"):
        value = pd.to_numeric(pd.Series([row.get(field)]), errors="coerce").iloc[0]
        if pd.isna(value) or float(value) < 0:
            reasons.append(f"{field}_invalid")
    valid = not reasons
    detail = "valid_live_snapshot" if valid else ",".join(reasons)
    return fresh and valid, f"{age_value};{detail}"

def _last_row(frame: pd.DataFrame) -> pd.Series:
    return frame.iloc[-1] if not frame.empty else pd.Series(dtype=object)


def _truth(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _as_int(value: object) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0 if pd.isna(parsed) else int(parsed)


def _build_broker_deployment_roadmap(rows: list[dict[str, object]]) -> pd.DataFrame:
    roadmap: list[dict[str, object]] = []
    for row in rows:
        if row.get("domain") != "broker":
            continue
        check = str(row.get("check", ""))
        passed = bool(row.get("passed"))
        if passed:
            phase = "complete"
            status = "completed"
            allowed = False
            authorization = "none"
        elif check in READ_ONLY_BROKER_CHECKS:
            phase = "read_only_connection"
            status = "actionable_read_only"
            allowed = True
            authorization = "existing_read_only_authorization"
        elif check in LEDGER_AUTHORIZATION_CHECKS:
            phase = "live_ledger"
            status = "authorization_required"
            allowed = False
            authorization = "explicit_live_ledger_authorization_required"
        elif check in CANARY_AUTHORIZATION_CHECKS:
            phase = "canary_certification"
            status = "future_canary_only"
            allowed = False
            authorization = "explicit_canary_authorization_required"
        elif check in FULL_STRATEGY_CAPITAL_CHECKS:
            phase = "full_strategy_capital"
            status = "capital_incompatible"
            allowed = False
            authorization = "strategy_or_capital_review_required"
        else:
            phase = "system_integrity"
            status = "actionable_read_only"
            allowed = True
            authorization = "existing_read_only_authorization"
        roadmap.append(
            {
                "phase": phase,
                "status": status,
                "check": check,
                "passed": passed,
                "allowed_in_read_only_run": allowed,
                "authorization": authorization,
                "next_action": row.get("next_action", ""),
                "message": row.get("message", ""),
            }
        )
    return pd.DataFrame(roadmap, columns=BROKER_DEPLOYMENT_ROADMAP_COLUMNS)

def _row(
    domain: str,
    check: str,
    passed: bool,
    value: object,
    threshold: object,
    next_action: str,
    message: str,
    *,
    blocking: bool = True,
) -> dict[str, object]:
    return {
        "domain": domain,
        "check": check,
        "passed": bool(passed),
        "value": value,
        "threshold": threshold,
        "blocking": bool(blocking),
        "next_action": next_action,
        "message": message,
    }


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _write_html(path: Path, frame: pd.DataFrame, next_action: str) -> None:
    summary = frame[frame["domain"] == "summary"].set_index("check")
    ready = _truth(summary.loc["ready_to_request_canary", "value"])
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    labels = {
        "strategy_ready": "策略证据",
        "broker_read_only_ready": "华泰只读",
        "broker_execution_ready": "执行链路",
        "full_strategy_capital_ready": "完整策略资金",
        "safe_default": "安全停机",
        "ready_to_request_canary": "Canary 申请",
        "ready_for_live_strategy_review": "完整策略复核",
    }
    summary_html = "".join(
        f'<div class="metric"><span>{escape(labels[item])}</span>'
        f'<strong class="{"ok" if _truth(row["value"]) else "fail"}">{"通过" if _truth(row["value"]) else "未通过"}</strong></div>'
        for item, label in labels.items()
        for row in [summary.loc[item]]
    )
    action_labels = {
        "strategy_ready": "策略线",
        "broker_read_only_ready": "只读线",
        "broker_execution_ready": "执行线",
        "full_strategy_capital_ready": "资金线",
        "safe_default": "安全线",
    }
    parallel_actions = "".join(
        f'<div class="action-row"><b>{label}</b><p>{escape(str(summary.loc[item, "next_action"]))}</p></div>'
        for item, label in action_labels.items()
    )
    detail = frame[frame["domain"] != "summary"]
    rows = "".join(
        "<tr>"
        f"<td>{escape(DOMAIN_LABELS.get(str(row.domain), str(row.domain)))}</td>"
        f"<td>{escape(CHECK_LABELS.get(str(row.check), str(row.check)))}</td>"
        f'<td class="{"ok" if row.passed else "fail"}">{"通过" if row.passed else "未通过"}</td>'
        f"<td>{escape(_display_value(row.value))}</td><td>{escape(str(row.message))}</td>"
        "</tr>"
        for row in detail.itertuples(index=False)
    )
    content = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>A 股量化系统 - 实盘部署门禁</title>
<style>
:root{{--bg:#f5f5f7;--panel:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--blue:#0071e3;--green:#248a3d;--red:#d70015}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI",sans-serif;letter-spacing:0}}
main{{max-width:1180px;margin:auto;padding:36px 22px 60px}}h1{{font-size:30px;margin:0 0 6px}}.sub{{color:var(--muted);margin-bottom:22px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}}.metric{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;min-height:86px}}
.metric span{{display:block;color:var(--muted);font-size:13px;margin-bottom:12px}}.metric strong{{font-size:20px}}.ok{{color:var(--green)}}.fail{{color:var(--red)}}
.action{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:17px;margin-bottom:18px}}.action b{{display:block;margin-bottom:6px}}.action p{{margin:0;color:var(--muted)}}
.action-row{{display:grid;grid-template-columns:72px 1fr;gap:10px;padding:10px 0;border-top:1px solid #ececf0}}.action-row:first-of-type{{margin-top:12px}}.action-row b{{margin:0}}.action-row p{{margin:0}}
.table{{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:11px 12px;border-bottom:1px solid #ececf0;text-align:left;vertical-align:top}}th{{color:var(--muted);font-weight:600}}
@media(max-width:760px){{.summary{{grid-template-columns:repeat(2,minmax(0,1fr))}}main{{padding:24px 12px 40px}}}}
</style></head><body><main><h1>实盘部署门禁</h1><div class="sub">{escape(generated)} · 只读审计，不启动客户端、不创建账本、不生成订单</div>
<section class="summary">{summary_html}</section>
<section class="action"><b>{"可以申请人工 Canary 复核" if ready else "当前不可申请 Canary"}</b><p>{escape(next_action)}</p>{parallel_actions}</section>
<div class="table"><table><thead><tr><th>域</th><th>检查项</th><th>结论</th><th>当前值</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></div>
</main></body></html>"""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _display_value(value: object) -> str:
    text = str(value)
    if text.lower() == "true":
        return "是"
    if text.lower() == "false":
        return "否"
    return text
