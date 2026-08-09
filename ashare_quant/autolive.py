from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .audit import frame_sha256, mapping_sha256
from .autolive_window import auto_live_submit_window_enforced, auto_live_window_audit_passed, auto_live_window_blockers, write_auto_live_window_audit
from .autolive_risk import auto_live_emergency_stop_status, build_auto_live_risk_audit, upsert_auto_live_risk_reservation, write_auto_live_risk_audit
from .autolive_journal import auto_live_journal_status, record_auto_live_event, write_auto_live_journal_audit
from .autolive_binding import auto_live_account_binding_status, write_auto_live_account_binding_audit
from .autolive_recovery import auto_live_recovery_config_status, create_auto_live_recovery_point, write_auto_live_recovery_audit
from .autolive_alerts import auto_live_alert_status, send_auto_live_alert, send_auto_live_incident_alert, write_auto_live_alert_audit
from .autolive_confirmation import (
    ActionConfirmation,
    CONFIRMATION_COLUMNS,
    action_confirmation_required,
    confirmation_path as action_confirmation_path,
    consume_action_confirmation,
    issue_action_confirmation,
    validate_action_confirmation,
)
from .autolive_rollout import auto_live_rollout_status, write_auto_live_rollout_audit
from .autolive_integrity import (
    attach_request_integrity,
    command_adapter_file_status,
    command_integrity_status,
    verify_adapter_request_integrity,
    verify_adapter_result_integrity,
    write_command_adapter_file_audit,
)
from .config import project_path
from .io_utils import atomic_to_csv
from .order_integrity import verify_rpa_ticket_execution_subset

AUTO_LIVE_READINESS_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_SUMMARY_COLUMNS = [
    "status",
    "auto_live_allowed",
    "manual_ticket_ready",
    "strict_live_ready",
    "execution_ready",
    "blockers",
    "blocker_details",
    "generated_at",
]
AUTO_LIVE_BLOCKER_DETAIL_COLUMNS = ["source", "check", "severity", "value", "next_action", "message"]
AUTO_LIVE_ADAPTER_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_ADAPTER_MANIFEST_COLUMNS = ["item", "value"]
AUTO_LIVE_ADAPTER_SELFTEST_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_ACK_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_ORDER_STATUS_COLUMNS = [
    "query_run_id",
    "source_run_id",
    "trade_date",
    "ticket_id",
    "symbol",
    "side",
    "quantity",
    "limit_price",
    "broker_order_id",
    "broker_status",
    "filled_quantity",
    "avg_fill_price",
    "filled_amount",
    "fee",
    "fee_status",
    "fee_source",
    "message",
]
AUTO_LIVE_ORDER_STATUS_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_SUBMISSION_RECOVERY_COLUMNS = ["source_run_id", "ticket_id", "submission_status", "broker_order_id", "broker_status", "resolved", "action", "message"]
AUTO_LIVE_CANCEL_RESULT_COLUMNS = ["cancel_run_id", "source_run_id", "ticket_id", "symbol", "side", "quantity", "broker_order_id", "cancel_status", "message"]
AUTO_LIVE_CANCEL_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS = ["snapshot_run_id", "broker", "account_id", "environment", "cash", "available_cash", "market_value", "total_asset", "frozen_cash", "unclassified_asset_value", "positions_count", "broker_time", "generated_at", "auto_live_config_sha256", "message"]
AUTO_LIVE_BROKER_POSITION_COLUMNS = ["snapshot_run_id", "symbol", "quantity", "sellable_quantity", "cost", "market_value", "last_price", "message"]
AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS = ["generated_at", "adapter", "attempted", "trigger", "status", "remaining_blockers", "message"]
AUTO_LIVE_ACCOUNT_RUNTIME_CHECKS = {"broker_account_snapshot_audit_passed", "broker_account_snapshot_environment_live", "broker_account_snapshot_config_match", "broker_account_snapshot_fresh"}
AUTO_LIVE_SUBMIT_LOCK_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_SUBMISSION_INTENT_COLUMNS = [
    "run_id",
    "state",
    "adapter",
    "order_count",
    "tickets_sha256",
    "auto_live_config_sha256",
    "dedupe_key",
    "request_file",
    "result_file",
    "created_at",
    "updated_at",
    "message",
]
AUTO_LIVE_RUN_COLUMNS = [
    "run_id",
    "status",
    "adapter",
    "order_count",
    "submitted_count",
    "blocked_reason",
    "tickets_sha256",
    "auto_live_config_sha256",
    "dedupe_key",
    "generated_at",
    "message",
]
AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS = [
    "run_id",
    "status",
    "adapter",
    "order_count",
    "submitted_count",
    "tickets_sha256",
    "auto_live_config_sha256",
    "dedupe_key",
    "generated_at",
    "message",
]
AUTO_LIVE_ORDER_RESULT_COLUMNS = ["run_id", "date", "ticket_id", "symbol", "side", "quantity", "limit_price", "status", "broker_order_id", "message"]
SUPPORTED_AUTO_LIVE_ADAPTERS = {"mock", "command"}
COMMAND_ADAPTER_SCHEMA = "ashare_quant.command_adapter.v1"
COMMAND_ADAPTER_SELFTEST_STATUS = {"ok", "ready", "dry_run"}
COMMAND_ADAPTER_REQUIRED_CAPABILITIES = {"submit_orders", "query_orders", "cancel_orders", "account_snapshot"}
BROKER_ORDER_STATUSES = {"accepted", "submitted", "partially_filled", "filled", "canceled", "rejected", "failed", "unknown"}
BROKER_ORDER_ACTIONABLE_STATUSES = {"accepted", "submitted", "partially_filled", "filled", "canceled"}
BROKER_ORDER_TERMINAL_STATUSES = {"filled", "canceled", "rejected", "failed"}
CANCELABLE_BROKER_ORDER_STATUSES = {"accepted", "submitted", "partially_filled"}
CANCEL_ORDER_STATUSES = {"canceled", "cancel_requested", "already_filled", "not_cancelable", "rejected", "failed", "unknown"}
CANCEL_ORDER_SUCCESS_STATUSES = {"canceled", "cancel_requested", "already_filled"}


def write_auto_live_status(config: dict[str, Any], tickets: pd.DataFrame | None = None) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_paths: dict[str, Path] = {}
    if tickets is None:
        from .autolive_batch import write_auto_live_batch_plan

        batch_paths = write_auto_live_batch_plan(config)
        current_batch = read_csv(batch_paths["auto_live_current_batch.csv"])
        active_batch = read_csv(batch_paths["auto_live_active_batch.csv"])
        tickets = current_batch if not current_batch.empty else (active_batch if not active_batch.empty else read_csv(output_dir / "rpa_orders.csv"))
    risk_audit_path = write_auto_live_risk_audit(config, tickets=tickets)
    journal_paths = write_auto_live_journal_audit(config)
    account_binding_paths = write_auto_live_account_binding_audit(config)
    recovery_paths = write_auto_live_recovery_audit(config)
    alert_paths = write_auto_live_alert_audit(config)
    rollout_paths = write_auto_live_rollout_audit(config, tickets=tickets)
    adapter_file_audit_path = write_command_adapter_file_audit(config)
    from .autolive_replay import write_auto_live_adapter_replay_audit

    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    configured_replay_dir = auto.get("adapter_replay_dir", "")
    replay_summary_path = output_dir / "auto_live_adapter_replay_summary.csv"
    has_local_replay = any(output_dir.glob("auto_live_*_request_*.json"))
    if configured_replay_dir or has_local_replay or not replay_summary_path.exists():
        replay_paths = write_auto_live_adapter_replay_audit(config, configured_replay_dir or None)
    else:
        replay_paths = {
            "auto_live_adapter_replay_audit.csv": output_dir / "auto_live_adapter_replay_audit.csv",
            "auto_live_adapter_replay_summary.csv": replay_summary_path,
        }
    readiness = build_auto_live_readiness(config, tickets=tickets)
    summary = build_auto_live_summary(config, readiness, tickets=tickets)
    adapter_audit = build_auto_live_adapter_audit(config)
    blocker_details = build_auto_live_blocker_details(config, readiness=readiness, adapter_audit=adapter_audit, tickets=tickets)
    adapter_manifest = build_auto_live_adapter_manifest(config)
    submit_lock_audit_path = write_auto_live_submit_lock_audit(config)
    readiness_path = output_dir / "auto_live_readiness.csv"
    summary_path = output_dir / "auto_live_summary.csv"
    adapter_audit_path = output_dir / "auto_live_adapter_audit.csv"
    blocker_details_path = output_dir / "auto_live_blocker_details.csv"
    adapter_manifest_path = output_dir / "auto_live_adapter_manifest.csv"
    submission_intent_path = output_dir / "auto_live_submission_intent.csv"
    blocked_attempt_path = output_dir / "auto_live_blocked_attempt.csv"
    confirmation_path = action_confirmation_path(config)
    if not submission_intent_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_SUBMISSION_INTENT_COLUMNS), submission_intent_path, index=False, encoding="utf-8-sig")
    if not blocked_attempt_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_RUN_COLUMNS), blocked_attempt_path, index=False, encoding="utf-8-sig")
    if not confirmation_path.exists():
        atomic_to_csv(pd.DataFrame(columns=CONFIRMATION_COLUMNS), confirmation_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(readiness, readiness_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(adapter_audit, adapter_audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(blocker_details, blocker_details_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(adapter_manifest, adapter_manifest_path, index=False, encoding="utf-8-sig")
    return {
        **batch_paths,
        **replay_paths,
        **journal_paths,
        **account_binding_paths,
        **recovery_paths,
        **alert_paths,
        **rollout_paths,
        "auto_live_readiness.csv": readiness_path,
        "auto_live_summary.csv": summary_path,
        "auto_live_adapter_audit.csv": adapter_audit_path,
        "auto_live_blocker_details.csv": blocker_details_path,
        "auto_live_adapter_manifest.csv": adapter_manifest_path,
        "auto_live_submit_lock_audit.csv": submit_lock_audit_path,
        "auto_live_submission_intent.csv": submission_intent_path,
        "auto_live_action_confirmation.csv": confirmation_path,
        "auto_live_blocked_attempt.csv": blocked_attempt_path,
        "auto_live_risk_audit.csv": risk_audit_path,
        "auto_live_adapter_file_audit.csv": adapter_file_audit_path,
        "auto_live_risk_reservations.csv": output_dir / "auto_live_risk_reservations.csv",
        "auto_live_stop_audit.csv": output_dir / "auto_live_stop_audit.csv",
    }


def build_auto_live_readiness(config: dict[str, Any], tickets: pd.DataFrame | None = None) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    execution = read_csv(output_dir / "execution_readiness.csv")
    modes = read_csv(output_dir / "execution_modes.csv")
    source_tickets = read_csv(output_dir / "rpa_orders.csv")
    tickets = source_tickets.copy() if tickets is None else tickets.copy()
    auto = config.get("auto_live", {})
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    execution_present = not execution.empty and {"check", "passed", "severity"}.issubset(execution.columns)
    add(
        "execution_readiness_present",
        execution_present,
        "present" if execution_present else "missing",
        "present execution_readiness.csv",
        "execution_blocker",
        "python -m ashare_quant execution-ready",
        "自动实盘检查必须先读取执行就绪证据。",
    )

    execution_passed = execution_present and all_passed(execution)
    add(
        "execution_readiness_all_passed",
        execution_passed,
        pass_count(execution),
        "all execution checks passed",
        "execution_blocker",
        "python -m ashare_quant execution-ready",
        "账户、订单来源、RPA 前置检查和安全策略必须全部通过。",
    )

    strict_mode = mode_passed(modes, "strict_live_execution")
    add(
        "strict_live_execution_mode",
        strict_mode,
        mode_value(modes, "strict_live_execution"),
        "strict_live_execution ready",
        "live_blocker",
        "python -m ashare_quant observe-candidates --quiet",
        "严格实盘模式必须通过前向验证、Shadow 样本和晋级决策。",
    )

    manual_mode = mode_passed(modes, "manual_ticket_review")
    add(
        "manual_ticket_review_mode",
        manual_mode,
        mode_value(modes, "manual_ticket_review"),
        "manual_ticket_review ready",
        "execution_blocker",
        "python -m ashare_quant practical",
        "人工复核工单链路必须先可用。",
    )

    ticket_ready = tickets_ready(tickets)
    add(
        "rpa_tickets_ready",
        ticket_ready,
        ticket_value(tickets),
        "ready tickets and no blocked tickets",
        "execution_blocker",
        "python -m ashare_quant export-orders",
        "自动实盘前必须有可执行工单，且不能有 blocked 工单。",
    )

    ticket_integrity_ok, ticket_integrity_reason = verify_rpa_ticket_execution_subset(
        config, tickets, source_tickets=source_tickets
    )
    add(
        "rpa_ticket_execution_integrity",
        ticket_integrity_ok,
        ticket_integrity_reason,
        "signed source manifest and selected tickets are an exact subset",
        "safety_blocker",
        "重新运行 python -m ashare_quant export-orders，禁止手工修改或注入工单。",
        "自动实盘入口会复核原始工单签名，并确认本次执行子集逐字段来自签名工单。",
    )

    risk_audit = build_auto_live_risk_audit(config, tickets=tickets)
    risk_passed = not risk_audit.empty and "passed" in risk_audit.columns and bool(risk_audit["passed"].map(parse_bool_like).all())
    risk_enforced = bool(auto.get("enabled", False))
    risk_gate_passed = (not risk_enforced) or risk_passed
    risk_failed = [] if risk_passed else risk_audit.loc[~risk_audit["passed"].map(parse_bool_like), "check"].astype(str).tolist() if not risk_audit.empty and "passed" in risk_audit.columns else ["risk_audit_missing"]
    add(
        "auto_live_risk_audit_passed",
        risk_gate_passed,
        "not_enforced_auto_live_disabled" if not risk_enforced else ("all_passed" if risk_passed else ";".join(risk_failed)),
        "all independent auto-live risk checks passed",
        "safety_blocker",
        "运行 python -m ashare_quant auto-live-risk，并修复风险限额或停机状态。",
        "券商提交前必须独立通过停机开关、金额、订单数、日累计额度和现金缓冲审计。",
    )

    journal_passed, journal_value, journal_required = auto_live_journal_status(config)
    add(
        "auto_live_journal_integrity",
        journal_passed,
        journal_value,
        "verified or empty_ready" if journal_required else "integrity valid",
        "safety_blocker",
        "运行 python -m ashare_quant auto-live-journal；保留原文件并从可信备份恢复。",
        "追加式事件日志必须保持可读、连续且哈希/HMAC 验证通过。",
    )

    binding_passed, binding_value, binding_required = auto_live_account_binding_status(config)
    add(
        "auto_live_account_binding",
        binding_passed,
        binding_value,
        "valid signed live-account binding" if binding_required else "optional",
        "account_binding_blocker",
        "先运行 auto-live-account 完成真实账户对账，再执行 auto-live-bind-account --confirm。",
        "本地状态库必须显式绑定到当前真实券商账户，账户切换、配置变化或文件篡改都会阻断提交。",
    )

    recovery_passed, recovery_value, recovery_required = auto_live_recovery_config_status(config)
    add(
        "auto_live_recovery_config",
        recovery_passed,
        recovery_value,
        "enabled state_db and HMAC ready" if recovery_required else "optional",
        "recovery_blocker",
        "配置 auto_live.recovery，并确认状态库和 HMAC 密钥可用。",
        "真实提交前必须能够创建独立、可验证的账户状态恢复点。",
    )

    alert_passed, alert_value, alert_required = auto_live_alert_status(config)
    add(
        "auto_live_alert_channel",
        alert_passed,
        alert_value,
        "current certified live alert channel and clear outbox" if alert_required else "optional",
        "alert_blocker",
        "配置真实告警后端，运行 auto-live-alert-selftest，并清空未送达告警。",
        "无人值守真实提交前必须证明异常能够送达到真实外部通道。",
    )

    rollout_passed, rollout_value, rollout_required = auto_live_rollout_status(config, tickets=tickets)
    add(
        "auto_live_rollout_authorization",
        rollout_passed,
        rollout_value,
        "valid signed stage approval and tickets within limits" if rollout_required else "optional",
        "rollout_blocker",
        "完成真实账户、回放和告警认证后，显式批准 Canary/Limited/Full 阶段。",
        "短期投产授权必须绑定当前账户、配置、阶段和本批订单限额。",
    )

    add(
        "auto_live_enabled",
        bool(auto.get("enabled", False)),
        bool(auto.get("enabled", False)),
        True,
        "auto_blocker",
        "在配置 auto_live.enabled=true 前，系统只允许模拟盘/人工复核。",
        "自动实盘需要显式配置开关，不能由回测收益自动开启。",
    )

    adapter = str(auto.get("broker_adapter", "") or "").strip()
    adapter_key = adapter.lower()
    add(
        "broker_adapter_configured",
        bool(adapter),
        adapter or "missing",
        "broker adapter name/config",
        "auto_blocker",
        "配置真实券商适配器，或保持自动实盘关闭。",
        "自动提交真实订单需要券商 API/适配器；RPA 填字段不等同于自动实盘提交。",
    )
    add(
        "broker_adapter_supported",
        adapter_supported(adapter_key),
        adapter_key or "missing",
        ",".join(sorted(SUPPORTED_AUTO_LIVE_ADAPTERS)),
        "auto_blocker",
        "使用系统明确支持并经过审计的券商适配器。",
        "自动实盘运行器只允许调用已登记的适配器，避免配置拼写错误或未知脚本在提交阶段才失败。",
    )
    if adapter_key == "mock":
        add(
            "mock_adapter_explicitly_allowed",
            bool(auto.get("allow_mock_adapter", False)),
            bool(auto.get("allow_mock_adapter", False)),
            True,
            "auto_blocker",
            "仅演练/测试配置可设置 auto_live.allow_mock_adapter=true；真实实盘应配置真实券商适配器。",
            "mock 适配器不会连接真实券商，必须显式声明才可通过自动实盘门控。",
        )
    if adapter_key == "command":
        command_status = command_adapter_status(config)
        add(
            "command_adapter_explicitly_allowed",
            bool(command_status["allow_command_adapter"]),
            bool(command_status["allow_command_adapter"]),
            True,
            "auto_blocker",
            "设置 auto_live.allow_command_adapter=true，并确认外部命令适配器只连接预期券商桥接程序。",
            "command 适配器可以调用本地外部程序，必须单独显式开启。",
        )
        add(
            "command_adapter_command_configured",
            bool(command_status["command_configured"]),
            command_status["command_value"],
            "auto_live.command_adapter.command configured",
            "auto_blocker",
            "配置 auto_live.command_adapter.command。",
            "外部命令适配器必须提供可执行命令。",
        )
        add(
            "command_adapter_executable_found",
            bool(command_status["executable_found"]),
            command_status["executable_value"],
            "command executable exists or is resolvable on PATH",
            "auto_blocker",
            "修正 auto_live.command_adapter.command 的第一个元素，确保可执行文件存在。",
            "系统会在提交前确认外部适配器入口可解析，避免临门失败。",
        )
        add(
            "command_adapter_timeout_valid",
            bool(command_status["timeout_valid"]),
            command_status["timeout_seconds"],
            ">0 seconds",
            "auto_blocker",
            "设置 auto_live.command_adapter.timeout_seconds 为正数。",
            "外部适配器必须有超时限制，避免自动实盘流程无限挂起。",
        )
        file_integrity_passed, file_integrity_value, file_integrity_required = command_adapter_file_status(config)
        add(
            "command_adapter_files_pinned",
            file_integrity_passed,
            file_integrity_value,
            "all configured files match SHA-256" if file_integrity_required else "optional",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-adapter-files，并核对可信文件哈希。",
            "桥接脚本和配置文件必须与已复核的固定 SHA-256 一致。",
        )
        integrity = command_integrity_status(config)
        add(
            "command_adapter_hmac_integrity_ready",
            bool(integrity["ready"]),
            f"required={integrity['required']};key_env={integrity['key_env']};key_present={integrity['key_present']};key_bytes={integrity['key_bytes']}",
            "optional or HMAC key >= 32 bytes",
            "auto_blocker",
            f"在环境变量 {integrity['key_env']} 中配置至少 32 字节随机密钥。",
            "启用 HMAC 完整性后，请求和结果必须使用仅存在于进程环境中的强密钥签名。",
        )
        selftest_passed, selftest_value = command_adapter_selftest_status(config)
        add(
            "command_adapter_selftest_passed",
            selftest_passed,
            selftest_value,
            "latest self-test passed for current auto_live config",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-adapter-selftest。",
            "command 适配器必须通过无订单 self-test，且结果绑定当前 auto_live 配置指纹。",
        )
        replay_passed, replay_value, replay_required = command_adapter_replay_status(config)
        add(
            "command_adapter_replay_certified_live",
            replay_passed,
            replay_value,
            "certified_live for current auto_live config" if replay_required else "optional",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-adapter-replay，并提供真实环境完整生命周期证据。",
            "启用回放认证门禁时，总门禁只接受绑定当前配置的真实环境完整生命周期认证。",
        )
        account_audit = read_csv(output_dir / "auto_live_broker_account_audit.csv")
        account_audit_passed = not account_audit.empty and "passed" in account_audit.columns and bool(account_audit["passed"].map(parse_bool_like).all())
        account_audit_value = "missing" if account_audit.empty else f"{int(account_audit['passed'].map(parse_bool_like).sum())}/{len(account_audit)}"
        add(
            "broker_account_snapshot_audit_passed",
            account_audit_passed,
            account_audit_value,
            "latest broker account snapshot audit passed",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-account，并核对券商账户现金和持仓。",
            "真实 command 自动实盘前必须读取并审计券商端账户快照，防止本地账户状态与券商端不一致。",
        )
        account_snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
        environment_passed, environment_value = auto_live_account_snapshot_environment_status(config, account_snapshot)
        add(
            "broker_account_snapshot_environment_live",
            environment_passed,
            environment_value,
            "live environment and non-empty account_id",
            "auto_blocker",
            "重新运行 python -m ashare_quant auto-live-account，并确认真实桥接器返回 environment=live。",
            "提交前会直接复核快照环境和账户号，不能只信任历史审计 CSV。",
        )
        binding_passed, binding_value = auto_live_account_snapshot_binding_status(config, account_snapshot)
        add(
            "broker_account_snapshot_config_match",
            binding_passed,
            binding_value,
            "snapshot fingerprint equals current auto_live config",
            "auto_blocker",
            "配置变更后重新运行 python -m ashare_quant auto-live-adapter-selftest 和 auto-live-account。",
            "账户快照必须绑定当前自动实盘配置，旧配置生成的快照不能复用。",
        )
        freshness_passed, freshness_value, freshness_threshold = auto_live_account_snapshot_freshness_status(config, account_snapshot)
        add(
            "broker_account_snapshot_fresh",
            freshness_passed,
            freshness_value,
            freshness_threshold,
            "auto_blocker",
            "提交前重新运行 python -m ashare_quant auto-live-account。",
            "账户快照必须足够新，避免使用已发生外部交易或资金变化前的账户状态。",
        )

    submit_allowed = bool(auto.get("allow_broker_submit", False))
    add(
        "broker_submit_explicitly_allowed",
        submit_allowed,
        submit_allowed,
        True,
        "auto_blocker",
        "在配置中显式确认 auto_live.allow_broker_submit=true。",
        "自动向券商提交订单必须有单独开关，不能复用模拟盘或 RPA 预览开关。",
    )

    return pd.DataFrame(rows, columns=AUTO_LIVE_READINESS_COLUMNS)


def build_auto_live_summary(config: dict[str, Any], readiness: pd.DataFrame, tickets: pd.DataFrame | None = None) -> pd.DataFrame:
    failed = failed_rows(readiness)
    safety = rows_with_severity(failed, "safety")
    execution = rows_with_severity(failed, "execution")
    live = rows_with_severity(failed, "live")
    auto = rows_with_severity(failed, "auto")
    if not safety.empty:
        status = "blocked_safety"
    elif not execution.empty and not live.empty:
        status = "blocked_execution_and_evidence"
    elif not execution.empty:
        status = "blocked_execution"
    elif not live.empty:
        status = "collecting_evidence"
    elif not auto.empty:
        status = "ready_for_manual_review"
    else:
        status = "auto_live_ready"
    blockers = ";".join(failed["check"].astype(str).tolist()) if not failed.empty and "check" in failed else ""
    details = blocker_detail_text(config, readiness, tickets=tickets)
    row = {
        "status": status,
        "auto_live_allowed": status == "auto_live_ready",
        "manual_ticket_ready": check_passed(readiness, "manual_ticket_review_mode"),
        "strict_live_ready": check_passed(readiness, "strict_live_execution_mode"),
        "execution_ready": check_passed(readiness, "execution_readiness_all_passed"),
        "blockers": blockers,
        "blocker_details": details,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return pd.DataFrame([row], columns=AUTO_LIVE_SUMMARY_COLUMNS)


def build_auto_live_blocker_details(
    config: dict[str, Any],
    readiness: pd.DataFrame | None = None,
    adapter_audit: pd.DataFrame | None = None,
    tickets: pd.DataFrame | None = None,
) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    frames = [
        ("auto_live_readiness", readiness if readiness is not None else build_auto_live_readiness(config)),
        ("execution_readiness", read_csv(output_dir / "execution_readiness.csv")),
        ("execution_modes", execution_modes_as_checks(read_csv(output_dir / "execution_modes.csv"))),
        ("evidence_plan", evidence_plan_as_checks(read_csv(output_dir / "evidence_plan.csv"))),
        ("auto_live_risk_audit", build_auto_live_risk_audit(config, tickets=tickets)),
        ("adapter_audit", adapter_audit if adapter_audit is not None else build_auto_live_adapter_audit(config)),
    ]
    rows: list[dict[str, object]] = []
    for source, frame in frames:
        if frame.empty or "passed" not in frame.columns:
            continue
        failed = frame[~frame["passed"].map(parse_bool_like)]
        for row in failed.itertuples(index=False):
            rows.append(
                {
                    "source": source,
                    "check": getattr(row, "check", ""),
                    "severity": getattr(row, "severity", ""),
                    "value": getattr(row, "value", ""),
                    "next_action": getattr(row, "next_action", ""),
                    "message": getattr(row, "message", ""),
                }
            )
    return pd.DataFrame(rows, columns=AUTO_LIVE_BLOCKER_DETAIL_COLUMNS)


def blocker_detail_text(config: dict[str, Any], readiness: pd.DataFrame, tickets: pd.DataFrame | None = None) -> str:
    details = build_auto_live_blocker_details(config, readiness=readiness, tickets=tickets)
    if details.empty or "check" not in details.columns:
        return ""
    ordered = []
    seen = set()
    priority_sources = ["execution_readiness", "execution_modes", "evidence_plan", "auto_live_risk_audit", "adapter_audit", "auto_live_readiness"]
    for source in priority_sources:
        subset = details[details["source"].astype(str) == source]
        for check in subset["check"].dropna().astype(str):
            if check and check not in seen:
                ordered.append(check)
                seen.add(check)
    return ";".join(ordered)


def execution_modes_as_checks(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "mode" not in frame.columns:
        return pd.DataFrame(columns=["check", "passed", "value", "severity", "next_action", "message"])
    rows = []
    auto_live_modes = {"manual_ticket_review", "strict_live_execution"}
    for row in frame.itertuples(index=False):
        mode = str(getattr(row, "mode", ""))
        if mode not in auto_live_modes:
            continue
        status = getattr(row, "status", "")
        blockers = getattr(row, "blockers", "")
        passed = getattr(row, "passed", str(status) == "ready")
        rows.append(
            {
                "check": mode,
                "passed": parse_bool_like(passed) or str(status) == "ready",
                "value": str(status) + (f" ({blockers})" if str(blockers).strip() else ""),
                "severity": "live_blocker" if "strict" in mode else "execution_blocker",
                "next_action": getattr(row, "next_action", ""),
                "message": "Execution mode must be ready before auto-live.",
            }
        )
    return pd.DataFrame(rows)


def evidence_plan_as_checks(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "area" not in frame.columns:
        return pd.DataFrame(columns=["check", "passed", "value", "severity", "next_action", "message"])
    rows = []
    for row in frame.itertuples(index=False):
        area = getattr(row, "area", "")
        status = str(getattr(row, "status", ""))
        blocker = getattr(row, "blocker", "")
        rows.append(
            {
                "check": str(area),
                "passed": status == "passed",
                "value": str(getattr(row, "current", "")),
                "severity": "live_blocker" if area in {"forward_validation", "shadow_observation", "strict_live_gate"} else "execution_blocker",
                "next_action": getattr(row, "next_action", ""),
                "message": str(blocker),
            }
        )
    return pd.DataFrame(rows)


def build_auto_live_adapter_audit(config: dict[str, Any]) -> pd.DataFrame:
    auto = config.get("auto_live", {})
    adapter = str(auto.get("broker_adapter", "") or "").strip().lower()
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    add(
        "adapter_name_present",
        bool(adapter),
        adapter or "missing",
        "configured adapter name",
        "auto_blocker",
        "在 configs/default.yaml 的 auto_live.broker_adapter 中配置已审计适配器。",
        "自动提交必须明确声明要调用的券商适配器。",
    )
    add(
        "adapter_supported",
        adapter_supported(adapter),
        adapter or "missing",
        ",".join(sorted(SUPPORTED_AUTO_LIVE_ADAPTERS)),
        "auto_blocker",
        "只允许使用系统登记并测试过的适配器。",
        "未知适配器不能进入自动实盘提交阶段。",
    )
    add(
        "submit_switch_explicit",
        bool(auto.get("allow_broker_submit", False)),
        bool(auto.get("allow_broker_submit", False)),
        True,
        "auto_blocker",
        "确认风险后显式设置 auto_live.allow_broker_submit=true。",
        "提交真实订单必须有独立开关。",
    )
    if adapter == "mock":
        add(
            "mock_adapter_guarded",
            bool(auto.get("allow_mock_adapter", False)),
            bool(auto.get("allow_mock_adapter", False)),
            True,
            "auto_blocker",
            "测试演练才可设置 auto_live.allow_mock_adapter=true；真实实盘不要使用 mock。",
            "mock 适配器只用于验证运行器，不代表真实券商能力。",
        )
        add(
            "adapter_submit_capability",
            bool(auto.get("allow_mock_adapter", False)),
            "mock_submit_and_query" if bool(auto.get("allow_mock_adapter", False)) else "mock_disabled",
            "submit_orders, query_orders, cancel_orders and account_snapshot capabilities enabled",
            "auto_blocker",
            "开启 mock 演练或接入真实券商适配器。",
            "适配器必须声明可处理订单提交、委托查询、撤单和账户快照接口。",
        )
    elif adapter == "command":
        command_status = command_adapter_status(config)
        add(
            "command_adapter_guarded",
            bool(command_status["allow_command_adapter"]),
            bool(command_status["allow_command_adapter"]),
            True,
            "auto_blocker",
            "设置 auto_live.allow_command_adapter=true。",
            "command 适配器可以调用外部程序，必须单独显式开启。",
        )
        add(
            "command_adapter_command_configured",
            bool(command_status["command_configured"]),
            command_status["command_value"],
            "configured command",
            "auto_blocker",
            "配置 auto_live.command_adapter.command。",
            "外部命令适配器必须提供命令列表或命令字符串。",
        )
        add(
            "command_adapter_executable_found",
            bool(command_status["executable_found"]),
            command_status["executable_value"],
            "resolvable executable",
            "auto_blocker",
            "修正 auto_live.command_adapter.command 的可执行入口。",
            "执行前必须能解析外部适配器入口。",
        )
        add(
            "command_adapter_timeout_valid",
            bool(command_status["timeout_valid"]),
            command_status["timeout_seconds"],
            ">0 seconds",
            "auto_blocker",
            "设置 auto_live.command_adapter.timeout_seconds 为正数。",
            "自动实盘适配器必须有超时保护。",
        )
        file_integrity_passed, file_integrity_value, file_integrity_required = command_adapter_file_status(config)
        add(
            "command_adapter_files_pinned",
            file_integrity_passed,
            file_integrity_value,
            "all configured files match SHA-256" if file_integrity_required else "optional",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-adapter-files，并核对可信文件哈希。",
            "桥接脚本和配置文件必须与已复核的固定 SHA-256 一致。",
        )
        integrity = command_integrity_status(config)
        add(
            "command_adapter_hmac_integrity_ready",
            bool(integrity["ready"]),
            f"required={integrity['required']};key_env={integrity['key_env']};key_present={integrity['key_present']};key_bytes={integrity['key_bytes']}",
            "optional or HMAC key >= 32 bytes",
            "auto_blocker",
            f"在环境变量 {integrity['key_env']} 中配置至少 32 字节随机密钥。",
            "启用 HMAC 完整性后，请求和结果必须使用仅存在于进程环境中的强密钥签名。",
        )
        selftest_passed, selftest_value = command_adapter_selftest_status(config)
        add(
            "command_adapter_selftest_passed",
            selftest_passed,
            selftest_value,
            "latest self-test passed for current auto_live config",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-adapter-selftest，并确认外部券商桥接程序按契约返回。",
            "command 适配器必须先通过无订单 self-test，且自检配置指纹必须匹配当前 auto_live 配置。",
        )
        replay_passed, replay_value, replay_required = command_adapter_replay_status(config)
        add(
            "command_adapter_replay_certified_live",
            replay_passed,
            replay_value,
            "certified_live for current auto_live config" if replay_required else "optional",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-adapter-replay，并提供真实环境完整生命周期证据。",
            "启用回放认证门禁时，提交、查询、撤单和账户快照必须在真实券商环境完成离线审计。",
        )
        command_ready = all(bool(command_status[key]) for key in ["allow_command_adapter", "command_configured", "executable_found", "timeout_valid"]) and file_integrity_passed and bool(integrity["ready"]) and selftest_passed and replay_passed
        add(
            "adapter_submit_capability",
            command_ready,
            "command_submit_and_query" if command_ready else "command_not_ready",
            "submit_orders, query_orders, cancel_orders and account_snapshot capabilities enabled",
            "auto_blocker",
            "完成 command 适配器配置审计和 self-test 后再开启自动实盘。",
            "command 适配器按 v1 请求/结果契约提交订单、查询委托状态、撤销未成交委托并读取券商账户快照。",
        )
    else:
        add(
            "adapter_submit_capability",
            False,
            "unsupported" if adapter else "missing",
            "submit_orders, query_orders, cancel_orders and account_snapshot capabilities enabled",
            "auto_blocker",
            "实现并登记真实券商适配器后再开启自动实盘。",
            "当前系统没有真实券商提交适配器。",
        )
    add(
        "adapter_config_fingerprint",
        True,
        auto_live_config_sha256(config)[:16],
        "sha256 present",
        "auto_blocker",
        "",
        "每次自动实盘检查都会记录 auto_live 配置指纹，便于复盘。",
    )
    return pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_AUDIT_COLUMNS)


def build_auto_live_adapter_manifest(config: dict[str, Any]) -> pd.DataFrame:
    auto = config.get("auto_live", {})
    adapter = str(auto.get("broker_adapter", "") or "").strip().lower()
    output_dir = project_path(config["paths"]["output_dir"])
    tickets = read_csv(output_dir / "rpa_orders.csv")
    integrity = command_integrity_status(config)
    file_integrity_passed, file_integrity_value, file_integrity_required = command_adapter_file_status(config)
    rows = [
        {"item": "generated_at", "value": datetime.now().isoformat(timespec="seconds")},
        {"item": "adapter", "value": adapter or "missing"},
        {"item": "supported_adapters", "value": ",".join(sorted(SUPPORTED_AUTO_LIVE_ADAPTERS))},
        {"item": "enabled", "value": str(bool(auto.get("enabled", False)))},
        {"item": "allow_broker_submit", "value": str(bool(auto.get("allow_broker_submit", False)))},
        {"item": "allow_mock_adapter", "value": str(bool(auto.get("allow_mock_adapter", False)))},
        {"item": "allow_command_adapter", "value": str(bool(auto.get("allow_command_adapter", False)))},
        {"item": "command_adapter_schema", "value": COMMAND_ADAPTER_SCHEMA},
        {"item": "command_adapter_executable", "value": str(command_adapter_status(config).get("executable_value", ""))},
        {"item": "command_adapter_hmac_required", "value": str(bool(integrity["required"]))},
        {"item": "command_adapter_hmac_key_env", "value": str(integrity["key_env"])},
        {"item": "command_adapter_hmac_key_present", "value": str(bool(integrity["key_present"]))},
        {"item": "command_adapter_hmac_key_bytes", "value": str(int(integrity["key_bytes"]))},
        {"item": "command_adapter_hmac_ready", "value": str(bool(integrity["ready"]))},
        {"item": "command_adapter_file_pinning_required", "value": str(file_integrity_required)},
        {"item": "command_adapter_files_pinned", "value": str(file_integrity_passed)},
        {"item": "command_adapter_file_pinning_status", "value": file_integrity_value},
        {"item": "auto_live_config_sha256", "value": auto_live_config_sha256(config)},
        {"item": "tickets_rows", "value": str(len(tickets))},
        {"item": "tickets_sha256", "value": tickets_sha256(tickets)},
    ]
    return pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_MANIFEST_COLUMNS)


def auto_live_config_sha256(config: dict[str, Any]) -> str:
    return mapping_sha256(config.get("auto_live", {}))


def auto_live_account_snapshot_environment_status(config: dict[str, Any], snapshot: pd.DataFrame) -> tuple[bool, str]:
    if len(snapshot) != 1:
        return False, f"rows={len(snapshot)}"
    environment = str(snapshot.iloc[0].get("environment", "") or "").strip().lower()
    account_id = str(snapshot.iloc[0].get("account_id", "") or "").strip()
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    allow_simulation = parse_bool_like(auto.get("allow_simulated_account_snapshot", False))
    live = environment == "live"
    allowed_simulation = allow_simulation and environment in {"simulation", "paper", "test"}
    passed = bool(account_id) and (live or allowed_simulation)
    return passed, f"environment={environment or 'missing'};account_id={'present' if account_id else 'missing'};simulation_override={allow_simulation}"


def auto_live_account_snapshot_binding_status(config: dict[str, Any], snapshot: pd.DataFrame) -> tuple[bool, str]:
    expected = auto_live_config_sha256(config)
    if len(snapshot) != 1 or "auto_live_config_sha256" not in snapshot.columns:
        return False, f"snapshot=missing;expected={expected[:16]}"
    actual = str(snapshot.iloc[0].get("auto_live_config_sha256", "") or "").strip()
    return actual == expected, f"snapshot={actual[:16] or 'missing'};expected={expected[:16]}"


def auto_live_account_snapshot_freshness_status(
    config: dict[str, Any],
    snapshot: pd.DataFrame,
    now: datetime | None = None,
) -> tuple[bool, str, str]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    try:
        max_age_seconds = max(float(auto.get("account_snapshot_max_age_seconds", 300)), 1.0)
    except (TypeError, ValueError):
        max_age_seconds = 300.0
    threshold = f"-30 <= age_seconds <= {max_age_seconds:g}"
    if len(snapshot) != 1 or "generated_at" not in snapshot.columns:
        return False, "generated_at=missing", threshold
    raw = snapshot.iloc[0].get("generated_at", "")
    generated_at = pd.to_datetime(raw, errors="coerce")
    if pd.isna(generated_at):
        return False, f"generated_at={raw or 'missing'}", threshold
    if now is None:
        current = pd.Timestamp.now(tz=generated_at.tzinfo) if generated_at.tzinfo is not None else pd.Timestamp.now()
    else:
        current = pd.Timestamp(now)
        local_timezone = datetime.now().astimezone().tzinfo
        if generated_at.tzinfo is not None:
            if current.tzinfo is None:
                current = current.tz_localize(local_timezone)
            current = current.tz_convert(generated_at.tzinfo)
        elif current.tzinfo is not None:
            current = current.tz_convert(local_timezone).tz_localize(None)
    age_seconds = float((current - generated_at).total_seconds())
    passed = -30.0 <= age_seconds <= max_age_seconds
    return passed, f"age_seconds={age_seconds:.1f};generated_at={generated_at.isoformat()}", threshold


def tickets_sha256(tickets: pd.DataFrame) -> str:
    return frame_sha256(tickets if not tickets.empty else pd.DataFrame(columns=["ticket_id", "symbol", "side", "quantity", "limit_price", "status"]))


def auto_live_dedupe_key(tickets_hash: str, auto_hash: str) -> str:
    return mapping_sha256({"tickets_sha256": tickets_hash, "auto_live_config_sha256": auto_hash})


def read_auto_live_submission_ledger(output_dir: Path) -> pd.DataFrame:
    frame = read_csv(output_dir / "auto_live_submission_ledger.csv")
    if frame.empty:
        return pd.DataFrame(columns=AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS)
    for column in AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS].copy()


def duplicate_auto_live_submission(output_dir: Path, dedupe_key: str) -> pd.Series | None:
    ledger = read_auto_live_submission_ledger(output_dir)
    if not ledger.empty and "dedupe_key" in ledger.columns:
        matched = ledger[ledger["dedupe_key"].astype(str) == str(dedupe_key)]
        if not matched.empty:
            return matched.iloc[-1]
    intent = read_csv(output_dir / "auto_live_submission_intent.csv")
    if intent.empty or "dedupe_key" not in intent.columns:
        return None
    matched_intent = intent[intent["dedupe_key"].astype(str) == str(dedupe_key)]
    if matched_intent.empty:
        return None
    return matched_intent.iloc[-1]


def append_auto_live_submission_ledger(output_dir: Path, run: pd.DataFrame) -> Path:
    path = output_dir / "auto_live_submission_ledger.csv"
    existing = read_auto_live_submission_ledger(output_dir)
    row = run.iloc[[0]].copy()
    for column in AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS:
        if column not in row.columns:
            row[column] = ""
    updated = pd.concat([existing, row[AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS]], ignore_index=True)
    atomic_to_csv(updated, path, index=False, encoding="utf-8-sig")
    return path


def persist_auto_live_blocked_attempt(
    output_dir: Path,
    run: pd.DataFrame,
    run_path: Path,
    result_path: Path,
    ack_path: Path,
    empty_ack: pd.DataFrame,
) -> Path:
    blocked_attempt_path = output_dir / "auto_live_blocked_attempt.csv"
    atomic_to_csv(run, blocked_attempt_path, index=False, encoding="utf-8-sig")
    if not run_path.exists():
        atomic_to_csv(run, run_path, index=False, encoding="utf-8-sig")
    if not result_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_ORDER_RESULT_COLUMNS), result_path, index=False, encoding="utf-8-sig")
    if not ack_path.exists():
        atomic_to_csv(empty_ack, ack_path, index=False, encoding="utf-8-sig")
    return blocked_attempt_path


def acquire_auto_live_submit_lock(output_dir: Path, run_id: str) -> Path:
    path = output_dir / "auto_live_submit.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(path, flags)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_id": run_id, "created_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False))
    return path


def release_auto_live_submit_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def write_auto_live_submission_intent(
    config: dict[str, Any],
    *,
    run_id: str,
    state: str,
    adapter: str,
    order_count: int,
    tickets_hash: str,
    auto_hash: str,
    dedupe_key: str,
    message: str,
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_submission_intent.csv"
    existing = read_csv(path)
    created_at = datetime.now().isoformat(timespec="seconds")
    if not existing.empty and str(existing.iloc[0].get("run_id", "")) == str(run_id):
        created_at = str(existing.iloc[0].get("created_at", "") or created_at)
    request_file = f"auto_live_command_request_{run_id}.json" if adapter == "command" else ""
    result_file = f"auto_live_command_result_{run_id}.json" if adapter == "command" else ""
    frame = pd.DataFrame(
        [
            {
                "run_id": run_id,
                "state": state,
                "adapter": adapter,
                "order_count": int(order_count),
                "tickets_sha256": tickets_hash,
                "auto_live_config_sha256": auto_hash,
                "dedupe_key": dedupe_key,
                "request_file": request_file,
                "result_file": result_file,
                "created_at": created_at,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "message": message,
            }
        ],
        columns=AUTO_LIVE_SUBMISSION_INTENT_COLUMNS,
    )
    atomic_to_csv(frame, path, index=False, encoding="utf-8-sig")
    return path


def prior_auto_live_submission_status(output_dir: Path) -> tuple[bool, str]:
    intent = read_csv(output_dir / "auto_live_submission_intent.csv")
    if intent.empty:
        return True, "no_prior_submission_intent"
    row = intent.iloc[-1]
    run_id = str(row.get("run_id", "") or "")
    state = str(row.get("state", "") or "").strip().lower()
    expected_value = pd.to_numeric(row.get("order_count", 0), errors="coerce")
    expected = int(expected_value) if not pd.isna(expected_value) and float(expected_value) > 0 else 0
    if not run_id:
        return False, "intent_run_id_missing"

    results = read_csv(output_dir / "auto_live_order_results.csv")
    if not results.empty and "run_id" in results.columns:
        results = results[results["run_id"].astype(str).eq(run_id)].copy()
    else:
        results = pd.DataFrame()
    if not results.empty and "status" in results.columns:
        result_statuses = results["status"].astype(str).str.strip().str.lower()
        if len(results) >= expected > 0 and result_statuses.isin({"rejected", "failed"}).all():
            return True, f"run_id={run_id};state={state};terminal_results={len(results)}/{expected}"

    statuses = read_csv(output_dir / "auto_live_order_status.csv")
    if not statuses.empty and "source_run_id" in statuses.columns:
        statuses = statuses[statuses["source_run_id"].astype(str).eq(run_id)].copy()
    else:
        statuses = pd.DataFrame()
    if not statuses.empty and "broker_status" in statuses.columns:
        broker_statuses = statuses["broker_status"].astype(str).str.strip().str.lower()
        unique_tickets = statuses["ticket_id"].astype(str).nunique() if "ticket_id" in statuses.columns else len(statuses)
        covered = unique_tickets >= expected > 0
        if covered and broker_statuses.isin(BROKER_ORDER_TERMINAL_STATUSES).all():
            return True, f"run_id={run_id};state={state};terminal_broker_statuses={unique_tickets}/{expected}"
        return False, f"run_id={run_id};state={state};terminal_broker_statuses={int(broker_statuses.isin(BROKER_ORDER_TERMINAL_STATUSES).sum())}/{expected}"
    return False, f"run_id={run_id};state={state or 'unknown'};terminal_broker_statuses=0/{expected}"


def write_auto_live_submit_lock_audit(config: dict[str, Any], now: datetime | None = None) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_submit_lock_audit.csv"
    lock_path = output_dir / "auto_live_submit.lock"
    stale_seconds = float(config.get("auto_live", {}).get("submit_lock_stale_seconds", 900) or 900)
    current = pd.Timestamp(now or datetime.now())
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    if not lock_path.exists():
        prior_resolved, prior_value = prior_auto_live_submission_status(output_dir)
        add("submit_lock_clear", True, "clear", "no lock file", "auto_blocker", "", "当前没有自动实盘提交锁。")
        add("submit_lock_metadata_valid", True, "not_required", "valid JSON when lock exists", "auto_blocker", "", "无锁时无需校验锁元数据。")
        add("submit_lock_intent_traceable", True, "not_required", "matching intent when stale", "auto_blocker", "", "无锁时无需恢复提交意图。")
        add("submit_lock_terminal_traceable", True, "not_required", "result or ledger when stale", "auto_blocker", "", "无锁时无需恢复锁对应终态。")
        add(
            "prior_submission_resolved",
            prior_resolved,
            prior_value,
            "all prior broker orders terminal",
            "auto_blocker",
            "运行 python -m ashare_quant auto-live-query；必要时撤单并再次查询，直到全部为 filled/canceled/rejected/failed。",
            "没有锁也必须确认前一批委托已全部进入终态，才能提交下一批。",
        )
        atomic_to_csv(pd.DataFrame(rows, columns=AUTO_LIVE_SUBMIT_LOCK_AUDIT_COLUMNS), path, index=False, encoding="utf-8-sig")
        return path

    payload = read_json_dict(lock_path)
    run_id = str(payload.get("run_id", "") or "")
    created_text = str(payload.get("created_at", "") or "")
    created = pd.to_datetime(created_text, errors="coerce")
    metadata_valid = bool(run_id and created_text and not pd.isna(created))
    try:
        file_age_seconds = max(0.0, current.timestamp() - lock_path.stat().st_mtime)
    except OSError:
        file_age_seconds = float("nan")
    if not pd.isna(created):
        created_ts = pd.Timestamp(created)
        if created_ts.tzinfo is not None and current.tzinfo is None:
            current = current.tz_localize(created_ts.tzinfo)
        elif created_ts.tzinfo is None and current.tzinfo is not None:
            created_ts = created_ts.tz_localize(current.tzinfo)
        age_seconds = max(0.0, float((current - created_ts).total_seconds()))
    else:
        age_seconds = file_age_seconds
    stale = bool(not pd.isna(age_seconds) and age_seconds > stale_seconds)
    state = "stale" if stale else "active_or_recent"
    active_action = "等待当前提交完成并再次运行 auto-live-lock；不要启动第二次提交。"
    stale_action = "先运行 python -m ashare_quant auto-live-query 并在券商客户端按 ticket_id 核对；确认无活动提交后才能人工删除 auto_live_submit.lock。"
    recovery_action = stale_action if stale else active_action
    add("submit_lock_clear", False, f"{state};run_id={run_id or 'unknown'};age_seconds={age_seconds:.1f}", "no lock file", "auto_blocker", recovery_action, "检测到自动实盘提交锁，系统会继续阻止新提交。")
    add("submit_lock_metadata_valid", metadata_valid, "valid" if metadata_valid else "invalid", "run_id and parseable created_at", "auto_blocker", "保留锁文件并核对其内容及关联券商委托。", "有效锁元数据用于定位异常退出的提交批次。")

    intent = read_csv(output_dir / "auto_live_submission_intent.csv")
    intent_match = bool(not intent.empty and "run_id" in intent.columns and intent["run_id"].astype(str).eq(run_id).any())
    intent_state = "missing"
    if intent_match:
        intent_row = intent[intent["run_id"].astype(str).eq(run_id)].iloc[-1]
        intent_state = str(intent_row.get("state", "") or "present")
    intent_ok = bool(not stale or intent_match)
    add("submit_lock_intent_traceable", intent_ok, f"state={intent_state};run_id={run_id or 'unknown'}", "matching intent when stale", "auto_blocker", stale_action, "过期锁必须能关联提交前原子落盘的意图记录。")

    request_exists = bool(run_id and (output_dir / f"auto_live_command_request_{run_id}.json").exists())
    result_exists = bool(run_id and (output_dir / f"auto_live_command_result_{run_id}.json").exists())
    ledger = read_auto_live_submission_ledger(output_dir)
    ledger_match = bool(not ledger.empty and "run_id" in ledger.columns and ledger["run_id"].astype(str).eq(run_id).any())
    terminal_ok = bool(not stale or result_exists or ledger_match)
    add(
        "submit_lock_terminal_traceable",
        terminal_ok,
        f"request={request_exists};result={result_exists};ledger={ledger_match}",
        "result or ledger when stale",
        "auto_blocker",
        stale_action,
        "过期锁若没有结果或提交台账，券商是否接单仍属未知，禁止直接重试。",
    )
    prior_resolved, prior_value = prior_auto_live_submission_status(output_dir)
    add(
        "prior_submission_resolved",
        prior_resolved,
        prior_value,
        "all prior broker orders terminal",
        "auto_blocker",
        recovery_action,
        "当前或前一批委托未闭环时禁止开始新批次。",
    )
    atomic_to_csv(pd.DataFrame(rows, columns=AUTO_LIVE_SUBMIT_LOCK_AUDIT_COLUMNS), path, index=False, encoding="utf-8-sig")
    return path


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def all_passed(frame: pd.DataFrame) -> bool:
    return not frame.empty and "passed" in frame and bool(frame["passed"].map(parse_bool_like).all())


def pass_count(frame: pd.DataFrame) -> str:
    if frame.empty or "passed" not in frame:
        return "missing"
    passed = int(frame["passed"].map(parse_bool_like).sum())
    return f"{passed}/{len(frame)}"


def failed_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "passed" not in frame:
        return frame
    return frame[~frame["passed"].map(parse_bool_like)]


def rows_with_severity(frame: pd.DataFrame, token: str) -> pd.DataFrame:
    if frame.empty or "severity" not in frame:
        return pd.DataFrame(columns=frame.columns)
    return frame[frame["severity"].astype(str).str.contains(token, na=False)]


def check_passed(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False
    matched = frame[frame["check"].astype(str) == check]
    return not matched.empty and parse_bool_like(matched["passed"].iloc[0])


def mode_passed(frame: pd.DataFrame, mode: str) -> bool:
    if frame.empty or not {"mode", "passed"}.issubset(frame.columns):
        return False
    matched = frame[frame["mode"].astype(str) == mode]
    return not matched.empty and parse_bool_like(matched["passed"].iloc[0])


def mode_value(frame: pd.DataFrame, mode: str) -> str:
    if frame.empty or "mode" not in frame:
        return "missing"
    matched = frame[frame["mode"].astype(str) == mode]
    if matched.empty:
        return "missing"
    row = matched.iloc[0]
    status = row.get("status", "")
    blockers = row.get("blockers", "")
    return str(status) + (f" ({blockers})" if str(blockers).strip() else "")


def adapter_supported(adapter: str) -> bool:
    return bool(adapter and adapter in SUPPORTED_AUTO_LIVE_ADAPTERS)


def tickets_ready(frame: pd.DataFrame) -> bool:
    if frame.empty or "status" not in frame:
        return False
    statuses = frame["status"].astype(str).str.lower()
    return bool((statuses == "ready").sum() > 0 and (statuses != "ready").sum() == 0)


def ticket_value(frame: pd.DataFrame) -> str:
    if frame.empty or "status" not in frame:
        return "missing"
    statuses = frame["status"].astype(str).str.lower()
    ready = int((statuses == "ready").sum())
    blocked = int((statuses != "ready").sum())
    return f"ready={ready};blocked={blocked};total={len(frame)}"


def refresh_auto_live_account_snapshot_if_needed(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = str(config.get("auto_live", {}).get("broker_adapter", "") or "").strip().lower()
    refresh_path = output_dir / "auto_live_account_refresh.csv"
    generated_at = datetime.now().isoformat(timespec="seconds")
    account_paths: dict[str, Path] = {}

    if adapter != "command":
        row = {
            "generated_at": generated_at,
            "adapter": adapter or "missing",
            "attempted": False,
            "trigger": "not_command_adapter",
            "status": "not_required",
            "remaining_blockers": "",
            "message": "Broker account auto-refresh is required only for the command adapter.",
        }
        atomic_to_csv(pd.DataFrame([row], columns=AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS), refresh_path, index=False, encoding="utf-8-sig")
        return {"auto_live_account_refresh.csv": refresh_path}

    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    if not parse_bool_like(auto.get("enabled", False)):
        row = {
            "generated_at": generated_at,
            "adapter": adapter,
            "attempted": False,
            "trigger": "auto_live_disabled",
            "status": "deferred",
            "remaining_blockers": "auto_live_enabled",
            "message": "Broker account refresh was deferred because automatic live trading is disabled.",
        }
        atomic_to_csv(pd.DataFrame([row], columns=AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS), refresh_path, index=False, encoding="utf-8-sig")
        return {"auto_live_account_refresh.csv": refresh_path}

    pre_readiness = build_auto_live_readiness(config)
    failed_non_account = []
    if not pre_readiness.empty and {"check", "passed"}.issubset(pre_readiness.columns):
        failed = pre_readiness[~pre_readiness["passed"].map(parse_bool_like)]
        failed_non_account = [check for check in failed["check"].astype(str).tolist() if check not in AUTO_LIVE_ACCOUNT_RUNTIME_CHECKS]
        if "auto_live_risk_audit_passed" in failed_non_account:
            risk_audit = build_auto_live_risk_audit(config)
            failed_risk = set(risk_audit.loc[~risk_audit["passed"].map(parse_bool_like), "check"].astype(str).tolist()) if not risk_audit.empty and "passed" in risk_audit.columns else {"risk_audit_missing"}
            if failed_risk and failed_risk.issubset({"risk_available_cash_buffer"}):
                failed_non_account.remove("auto_live_risk_audit_passed")
    if failed_non_account:
        row = {
            "generated_at": generated_at,
            "adapter": adapter,
            "attempted": False,
            "trigger": "non_account_gates",
            "status": "deferred",
            "remaining_blockers": ";".join(failed_non_account),
            "message": "Broker account refresh was deferred until all non-account auto-live gates pass.",
        }
        atomic_to_csv(pd.DataFrame([row], columns=AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS), refresh_path, index=False, encoding="utf-8-sig")
        return {"auto_live_account_refresh.csv": refresh_path}

    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    account_audit = read_csv(output_dir / "auto_live_broker_account_audit.csv")
    trigger_checks = auto_live_account_snapshot_runtime_blockers(config, snapshot, account_audit)
    if not trigger_checks:
        row = {
            "generated_at": generated_at,
            "adapter": adapter,
            "attempted": False,
            "trigger": "snapshot_valid",
            "status": "reused",
            "remaining_blockers": "",
            "message": "Existing broker account snapshot is current, bound to this config, and audit-passed.",
        }
        atomic_to_csv(pd.DataFrame([row], columns=AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS), refresh_path, index=False, encoding="utf-8-sig")
        return {"auto_live_account_refresh.csv": refresh_path}

    status = "failed"
    message = "Broker account read-only refresh failed."
    try:
        from .autolive_account import query_auto_live_account

        account_paths = query_auto_live_account(config, raise_on_blocked=False)
        refreshed_snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
        refreshed_audit = read_csv(output_dir / "auto_live_broker_account_audit.csv")
        remaining = auto_live_account_snapshot_runtime_blockers(config, refreshed_snapshot, refreshed_audit)
        if not remaining:
            status = "refreshed"
            message = "Broker account snapshot was refreshed and all runtime account checks passed."
        else:
            message = "Broker account snapshot refresh completed but runtime checks still failed."
    except Exception as exc:  # noqa: BLE001 - convert boundary failures into a persistent audit row.
        remaining = ["account_snapshot_refresh_exception"]
        message = f"Broker account read-only refresh raised {type(exc).__name__}: {truncate_text(str(exc), 240)}"

    row = {
        "generated_at": generated_at,
        "adapter": adapter,
        "attempted": True,
        "trigger": ";".join(trigger_checks),
        "status": status,
        "remaining_blockers": ";".join(remaining),
        "message": message,
    }
    atomic_to_csv(pd.DataFrame([row], columns=AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS), refresh_path, index=False, encoding="utf-8-sig")
    return {"auto_live_account_refresh.csv": refresh_path, **account_paths}


def auto_live_account_snapshot_runtime_blockers(
    config: dict[str, Any],
    snapshot: pd.DataFrame,
    account_audit: pd.DataFrame,
) -> list[str]:
    blockers: list[str] = []
    audit_passed = not account_audit.empty and "passed" in account_audit.columns and bool(account_audit["passed"].map(parse_bool_like).all())
    if not audit_passed:
        blockers.append("broker_account_snapshot_audit_passed")
    environment_passed, _ = auto_live_account_snapshot_environment_status(config, snapshot)
    if not environment_passed:
        blockers.append("broker_account_snapshot_environment_live")
    binding_passed, _ = auto_live_account_snapshot_binding_status(config, snapshot)
    if not binding_passed:
        blockers.append("broker_account_snapshot_config_match")
    freshness_passed, _, _ = auto_live_account_snapshot_freshness_status(config, snapshot)
    if not freshness_passed:
        blockers.append("broker_account_snapshot_fresh")
    return blockers


def prepare_auto_live_action_confirmation(
    config: dict[str, Any],
    tickets: pd.DataFrame | None = None,
) -> ActionConfirmation:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    if tickets is None:
        from .autolive_batch import write_auto_live_batch_plan

        batch_paths = write_auto_live_batch_plan(config)
        tickets = read_csv(batch_paths["auto_live_current_batch.csv"])
        if tickets.empty:
            tickets = read_csv(batch_paths["auto_live_active_batch.csv"])
    else:
        tickets = tickets.copy()
    if tickets.empty:
        raise RuntimeError("action confirmation blocked: no_rpa_orders")
    status_paths = write_auto_live_status(config, tickets=tickets)
    summary = read_csv(status_paths["auto_live_summary.csv"])
    if not auto_live_allowed(summary):
        reason = summary_blockers(summary) or "auto_live_not_allowed"
        raise RuntimeError(f"action confirmation blocked: {reason}")
    window_path = write_auto_live_window_audit(config, tickets=tickets)
    window_audit = read_csv(window_path)
    if auto_live_submit_window_enforced(config, dry_run=False) and not auto_live_window_audit_passed(window_audit):
        reason = auto_live_window_blockers(window_audit) or "auto_live_window_audit_failed"
        raise RuntimeError(f"action confirmation blocked: {reason}")
    return issue_action_confirmation(
        config,
        tickets_sha256=tickets_sha256(tickets),
        auto_live_config_sha256=auto_live_config_sha256(config),
    )


def run_auto_live(
    config: dict[str, Any],
    dry_run: bool = False,
    raise_on_blocked: bool = True,
    tickets: pd.DataFrame | None = None,
    confirmation_token: str | None = None,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = str(config.get("auto_live", {}).get("broker_adapter", "") or "").strip()
    batch_paths: dict[str, Path] = {}
    if tickets is None:
        from .autolive_batch import write_auto_live_batch_plan

        batch_paths = write_auto_live_batch_plan(config)
        tickets = read_csv(batch_paths["auto_live_current_batch.csv"])
        if tickets.empty:
            tickets = read_csv(batch_paths["auto_live_active_batch.csv"])
    else:
        tickets = tickets.copy()
    account_refresh_paths = refresh_auto_live_account_snapshot_if_needed(config)
    status_paths = write_auto_live_status(config, tickets=tickets)
    status_paths.update(batch_paths)
    status_paths.update(account_refresh_paths)
    summary = read_csv(status_paths["auto_live_summary.csv"])
    run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    generated_at = datetime.now().isoformat(timespec="seconds")
    tickets_hash = tickets_sha256(tickets)
    auto_hash = auto_live_config_sha256(config)
    dedupe_key = auto_live_dedupe_key(tickets_hash, auto_hash)
    run_path = output_dir / "auto_live_run.csv"
    result_path = output_dir / "auto_live_order_results.csv"
    ack_path = output_dir / "auto_live_ack_audit.csv"
    empty_ack = pd.DataFrame(columns=AUTO_LIVE_ACK_AUDIT_COLUMNS)
    window_path = write_auto_live_window_audit(config, tickets=tickets)
    status_paths["auto_live_window_audit.csv"] = window_path

    allowed = auto_live_allowed(summary)
    if not allowed:
        reason = summary_blockers(summary) or "auto_live_not_allowed"
        run = pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "status": "blocked",
                    "adapter": adapter or "missing",
                    "order_count": len(tickets),
                    "submitted_count": 0,
                    "blocked_reason": reason,
                    "tickets_sha256": tickets_hash,
                    "auto_live_config_sha256": auto_hash,
                    "dedupe_key": dedupe_key,
                    "generated_at": generated_at,
                    "message": "Auto-live gates are not all passed; no broker adapter was called.",
                }
            ],
            columns=AUTO_LIVE_RUN_COLUMNS,
        )
        status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(output_dir, run, run_path, result_path, ack_path, empty_ack)
        if raise_on_blocked:
            raise RuntimeError(f"auto-live blocked: {reason}")
        return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

    if tickets.empty:
        run = pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "status": "blocked",
                    "adapter": adapter or "missing",
                    "order_count": 0,
                    "submitted_count": 0,
                    "blocked_reason": "no_rpa_orders",
                    "tickets_sha256": tickets_hash,
                    "auto_live_config_sha256": auto_hash,
                    "dedupe_key": dedupe_key,
                    "generated_at": generated_at,
                    "message": "No executable RPA orders are available.",
                }
            ],
            columns=AUTO_LIVE_RUN_COLUMNS,
        )
        status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(output_dir, run, run_path, result_path, ack_path, empty_ack)
        if raise_on_blocked:
            raise RuntimeError("auto-live blocked: no_rpa_orders")
        return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

    if auto_live_submit_window_enforced(config, dry_run=dry_run):
        window_audit = read_csv(window_path)
        if not auto_live_window_audit_passed(window_audit):
            reason = auto_live_window_blockers(window_audit) or "auto_live_window_audit_failed"
            run = pd.DataFrame(
                [
                    {
                        "run_id": run_id,
                        "status": "blocked",
                        "adapter": adapter or "missing",
                        "order_count": len(tickets),
                        "submitted_count": 0,
                        "blocked_reason": reason,
                        "tickets_sha256": tickets_hash,
                        "auto_live_config_sha256": auto_hash,
                        "dedupe_key": dedupe_key,
                        "generated_at": generated_at,
                        "message": "Auto-live submit-time window audit failed; no broker adapter was called.",
                    }
                ],
                columns=AUTO_LIVE_RUN_COLUMNS,
            )
            status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(output_dir, run, run_path, result_path, ack_path, empty_ack)
            if raise_on_blocked:
                raise RuntimeError(f"auto-live blocked: {reason}")
            return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

    if not dry_run and action_confirmation_required(config):
        confirmation_valid, confirmation_reason = validate_action_confirmation(
            config,
            confirmation_token,
            tickets_sha256=tickets_hash,
            auto_live_config_sha256=auto_hash,
        )
        if not confirmation_valid:
            run = pd.DataFrame(
                [
                    {
                        "run_id": run_id,
                        "status": "blocked",
                        "adapter": adapter or "missing",
                        "order_count": len(tickets),
                        "submitted_count": 0,
                        "blocked_reason": confirmation_reason,
                        "tickets_sha256": tickets_hash,
                        "auto_live_config_sha256": auto_hash,
                        "dedupe_key": dedupe_key,
                        "generated_at": generated_at,
                        "message": "A valid single-use action confirmation is required; no broker adapter was called.",
                    }
                ],
                columns=AUTO_LIVE_RUN_COLUMNS,
            )
            status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(
                output_dir, run, run_path, result_path, ack_path, empty_ack
            )
            if raise_on_blocked:
                raise RuntimeError(f"auto-live blocked: {confirmation_reason}")
            return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

    lock_path: Path | None = None
    if not dry_run:
        try:
            lock_path = acquire_auto_live_submit_lock(output_dir, run_id)
            status_paths["auto_live_submit_lock_audit.csv"] = write_auto_live_submit_lock_audit(config)
        except FileExistsError:
            status_paths["auto_live_submit_lock_audit.csv"] = write_auto_live_submit_lock_audit(config)
            run = pd.DataFrame(
                [
                    {
                        "run_id": run_id,
                        "status": "blocked",
                        "adapter": adapter,
                        "order_count": len(tickets),
                        "submitted_count": 0,
                        "blocked_reason": "auto_live_submit_lock_exists",
                        "tickets_sha256": tickets_hash,
                        "auto_live_config_sha256": auto_hash,
                        "dedupe_key": dedupe_key,
                        "generated_at": generated_at,
                        "message": "Another auto-live submit appears to be running. Remove auto_live_submit.lock only after verifying no submit process is active.",
                    }
                ],
                columns=AUTO_LIVE_RUN_COLUMNS,
            )
            blocked_attempt_path = output_dir / "auto_live_blocked_attempt.csv"
            atomic_to_csv(run, blocked_attempt_path, index=False, encoding="utf-8-sig")
            if not run_path.exists():
                atomic_to_csv(run, run_path, index=False, encoding="utf-8-sig")
            if not result_path.exists():
                atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_ORDER_RESULT_COLUMNS), result_path, index=False, encoding="utf-8-sig")
            if not ack_path.exists():
                atomic_to_csv(empty_ack, ack_path, index=False, encoding="utf-8-sig")
            status_paths["auto_live_blocked_attempt.csv"] = blocked_attempt_path
            if raise_on_blocked:
                raise RuntimeError("auto-live blocked: auto_live_submit_lock_exists")
            return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

    try:
        if not dry_run:
            duplicate = duplicate_auto_live_submission(output_dir, dedupe_key)
            if duplicate is not None:
                previous_run_id = str(duplicate.get("run_id", ""))
                run = pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "status": "blocked",
                            "adapter": adapter,
                            "order_count": len(tickets),
                            "submitted_count": 0,
                            "blocked_reason": "duplicate_auto_live_submission",
                            "tickets_sha256": tickets_hash,
                            "auto_live_config_sha256": auto_hash,
                            "dedupe_key": dedupe_key,
                            "generated_at": generated_at,
                            "message": f"Same tickets/config were already submitted in run {previous_run_id}. Change tickets/config or manually review auto_live_submission_ledger.csv before retrying.",
                        }
                    ],
                    columns=AUTO_LIVE_RUN_COLUMNS,
                )
                blocked_attempt_path = output_dir / "auto_live_blocked_attempt.csv"
                atomic_to_csv(run, blocked_attempt_path, index=False, encoding="utf-8-sig")
                if not run_path.exists():
                    atomic_to_csv(run, run_path, index=False, encoding="utf-8-sig")
                if not result_path.exists():
                    atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_ORDER_RESULT_COLUMNS), result_path, index=False, encoding="utf-8-sig")
                if not ack_path.exists():
                    atomic_to_csv(empty_ack, ack_path, index=False, encoding="utf-8-sig")
                status_paths["auto_live_blocked_attempt.csv"] = blocked_attempt_path
                if raise_on_blocked:
                    raise RuntimeError("auto-live blocked: duplicate_auto_live_submission")
                return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

        if not dry_run:
            prior_resolved, prior_value = prior_auto_live_submission_status(output_dir)
            if not prior_resolved:
                run = pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "status": "blocked",
                            "adapter": adapter,
                            "order_count": len(tickets),
                            "submitted_count": 0,
                            "blocked_reason": "prior_auto_live_batch_unresolved",
                            "tickets_sha256": tickets_hash,
                            "auto_live_config_sha256": auto_hash,
                            "dedupe_key": dedupe_key,
                            "generated_at": generated_at,
                            "message": f"Previous auto-live batch is unresolved ({prior_value}). Query/cancel/reconcile it before submitting new tickets.",
                        }
                    ],
                    columns=AUTO_LIVE_RUN_COLUMNS,
                )
                blocked_attempt_path = output_dir / "auto_live_blocked_attempt.csv"
                atomic_to_csv(run, blocked_attempt_path, index=False, encoding="utf-8-sig")
                status_paths["auto_live_blocked_attempt.csv"] = blocked_attempt_path
                if raise_on_blocked:
                    raise RuntimeError("auto-live blocked: prior_auto_live_batch_unresolved")
                return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

        if not dry_run:
            risk_recheck_path = write_auto_live_risk_audit(config, tickets=tickets)
            status_paths["auto_live_risk_audit.csv"] = risk_recheck_path
            risk_recheck = read_csv(risk_recheck_path)
            risk_failed = risk_recheck.loc[~risk_recheck["passed"].map(parse_bool_like), "check"].astype(str).tolist() if not risk_recheck.empty and "passed" in risk_recheck.columns else ["risk_audit_missing"]
            if risk_failed:
                run = pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "status": "blocked",
                            "adapter": adapter,
                            "order_count": len(tickets),
                            "submitted_count": 0,
                            "blocked_reason": "auto_live_risk_recheck_failed",
                            "tickets_sha256": tickets_hash,
                            "auto_live_config_sha256": auto_hash,
                            "dedupe_key": dedupe_key,
                            "generated_at": generated_at,
                            "message": f"Independent risk recheck failed after submit lock acquisition: {';'.join(risk_failed)}. Broker adapter was not called.",
                        }
                    ],
                    columns=AUTO_LIVE_RUN_COLUMNS,
                )
                status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(output_dir, run, run_path, result_path, ack_path, empty_ack)
                if raise_on_blocked:
                    raise RuntimeError("auto-live blocked: auto_live_risk_recheck_failed")
                return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

        if not dry_run:
            rollout_paths = write_auto_live_rollout_audit(config, tickets=tickets)
            status_paths.update(rollout_paths)
            rollout_recheck = read_csv(rollout_paths["auto_live_rollout_audit.csv"])
            rollout_failed = rollout_recheck.loc[~rollout_recheck["passed"].map(parse_bool_like), "check"].astype(str).tolist() if not rollout_recheck.empty and "passed" in rollout_recheck.columns else ["rollout_audit_missing"]
            if rollout_failed:
                run = pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "status": "blocked",
                            "adapter": adapter,
                            "order_count": len(tickets),
                            "submitted_count": 0,
                            "blocked_reason": "auto_live_rollout_recheck_failed",
                            "tickets_sha256": tickets_hash,
                            "auto_live_config_sha256": auto_hash,
                            "dedupe_key": dedupe_key,
                            "generated_at": generated_at,
                            "message": f"Rollout authorization recheck failed after submit lock acquisition: {';'.join(rollout_failed)}. Broker adapter was not called.",
                        }
                    ],
                    columns=AUTO_LIVE_RUN_COLUMNS,
                )
                status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(output_dir, run, run_path, result_path, ack_path, empty_ack)
                status_paths.update(send_auto_live_alert(
                    config,
                    event_type="rollout_recheck_failed",
                    severity="critical",
                    message=str(run.iloc[0]["message"]),
                    context={"run_id": run_id, "dedupe_key": dedupe_key, "tickets_sha256": tickets_hash},
                ))
                if raise_on_blocked:
                    raise RuntimeError("auto-live blocked: auto_live_rollout_recheck_failed")
                return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

        if not dry_run:
            try:
                recovery_paths = create_auto_live_recovery_point(
                    config,
                    run_id=run_id,
                    tickets=tickets,
                    tickets_sha256=tickets_hash,
                    auto_live_config_sha256=auto_hash,
                )
                status_paths.update(recovery_paths)
            except Exception as exc:
                run = pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "status": "blocked",
                            "adapter": adapter,
                            "order_count": len(tickets),
                            "submitted_count": 0,
                            "blocked_reason": "pre_submit_recovery_failed",
                            "tickets_sha256": tickets_hash,
                            "auto_live_config_sha256": auto_hash,
                            "dedupe_key": dedupe_key,
                            "generated_at": generated_at,
                            "message": f"Pre-submit recovery point failed: {type(exc).__name__}: {truncate_text(str(exc), 240)}. Broker adapter was not called.",
                        }
                    ],
                    columns=AUTO_LIVE_RUN_COLUMNS,
                )
                status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(output_dir, run, run_path, result_path, ack_path, empty_ack)
                status_paths.update(send_auto_live_alert(
                    config,
                    event_type="pre_submit_recovery_failed",
                    severity="critical",
                    message=str(run.iloc[0]["message"]),
                    context={"run_id": run_id, "dedupe_key": dedupe_key, "tickets_sha256": tickets_hash},
                ))
                if raise_on_blocked:
                    raise RuntimeError("auto-live blocked: pre_submit_recovery_failed") from exc
                return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

        if not dry_run:
            confirmation_consumed, confirmation_reason, confirmation_path = consume_action_confirmation(
                config,
                confirmation_token,
                tickets_sha256=tickets_hash,
                auto_live_config_sha256=auto_hash,
            )
            status_paths["auto_live_action_confirmation.csv"] = confirmation_path
            if not confirmation_consumed:
                run = pd.DataFrame(
                    [
                        {
                            "run_id": run_id,
                            "status": "blocked",
                            "adapter": adapter,
                            "order_count": len(tickets),
                            "submitted_count": 0,
                            "blocked_reason": confirmation_reason,
                            "tickets_sha256": tickets_hash,
                            "auto_live_config_sha256": auto_hash,
                            "dedupe_key": dedupe_key,
                            "generated_at": generated_at,
                            "message": "Action confirmation changed before submission intent; broker adapter was not called.",
                        }
                    ],
                    columns=AUTO_LIVE_RUN_COLUMNS,
                )
                status_paths["auto_live_blocked_attempt.csv"] = persist_auto_live_blocked_attempt(
                    output_dir, run, run_path, result_path, ack_path, empty_ack
                )
                if raise_on_blocked:
                    raise RuntimeError(f"auto-live blocked: {confirmation_reason}")
                return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}

        if not dry_run:
            reservation_path = upsert_auto_live_risk_reservation(
                config,
                tickets,
                run_id=run_id,
                status="reserved",
                tickets_hash=tickets_hash,
                auto_hash=auto_hash,
                dedupe_key=dedupe_key,
                message="Risk capacity reserved before invoking the broker adapter.",
            )
            status_paths["auto_live_risk_reservations.csv"] = reservation_path

        if not dry_run:
            status_paths.update(
                record_auto_live_event(
                    config,
                    "submission_intent",
                    run_id,
                    {
                        "adapter": adapter,
                        "order_count": len(tickets),
                        "tickets_sha256": tickets_hash,
                        "dedupe_key": dedupe_key,
                        "state": "submitting",
                    },
                    strict=True,
                )
            )
            intent_path = write_auto_live_submission_intent(
                config,
                run_id=run_id,
                state="submitting",
                adapter=adapter,
                order_count=len(tickets),
                tickets_hash=tickets_hash,
                auto_hash=auto_hash,
                dedupe_key=dedupe_key,
                message="Submission intent persisted before invoking the broker adapter.",
            )
            status_paths["auto_live_submission_intent.csv"] = intent_path
            in_flight = pd.DataFrame(
                [
                    {
                        "run_id": run_id,
                        "status": "submitting",
                        "adapter": adapter,
                        "order_count": len(tickets),
                        "submitted_count": 0,
                        "blocked_reason": "broker_ack_pending",
                        "tickets_sha256": tickets_hash,
                        "auto_live_config_sha256": auto_hash,
                        "dedupe_key": dedupe_key,
                        "generated_at": generated_at,
                        "message": "Broker adapter invocation is in progress; do not submit these tickets again.",
                    }
                ],
                columns=AUTO_LIVE_RUN_COLUMNS,
            )
            atomic_to_csv(in_flight, run_path, index=False, encoding="utf-8-sig")
            status_paths["auto_live_submit_lock_audit.csv"] = write_auto_live_submit_lock_audit(config)

        results = submit_with_adapter(config, tickets, run_id=run_id, dry_run=dry_run)
        ack_audit = build_auto_live_ack_audit(tickets, results, run_id, dry_run=dry_run)
        ack_passed = all_passed(ack_audit)
        normalized_statuses = results["status"].astype(str).str.lower() if not results.empty and "status" in results else pd.Series(dtype=str)
        submitted = int((normalized_statuses == "submitted").sum())
        uncertain = int((normalized_statuses == "unknown").sum())
        if dry_run:
            status = "dry_run"
            blocked_reason = ""
            message = "Dry-run completed without broker submission."
        elif uncertain > 0:
            status = "submission_uncertain"
            blocked_reason = "broker_submission_state_unknown"
            message = f"Adapter submission state is unknown for {uncertain}/{len(results)} orders. Do not resubmit; run auto-live-query to recover by ticket_id."
        else:
            status = "submitted" if ack_passed else "partial_failed"
            blocked_reason = "" if ack_passed else "broker_ack_audit_failed"
            message = f"Adapter {adapter} returned {submitted}/{len(results)} submitted order acknowledgements; ack_audit_passed={ack_passed}."
        run = pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "status": status,
                    "adapter": adapter,
                    "order_count": len(tickets),
                    "submitted_count": submitted,
                    "blocked_reason": blocked_reason,
                    "tickets_sha256": tickets_hash,
                    "auto_live_config_sha256": auto_hash,
                    "dedupe_key": dedupe_key,
                    "generated_at": generated_at,
                    "message": message,
                }
            ],
            columns=AUTO_LIVE_RUN_COLUMNS,
        )
        atomic_to_csv(run, run_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(results, result_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(ack_audit, ack_path, index=False, encoding="utf-8-sig")
        if not dry_run:
            intent_path = write_auto_live_submission_intent(
                config,
                run_id=run_id,
                state=status,
                adapter=adapter,
                order_count=len(tickets),
                tickets_hash=tickets_hash,
                auto_hash=auto_hash,
                dedupe_key=dedupe_key,
                message=message,
            )
            status_paths["auto_live_submission_intent.csv"] = intent_path
            status_paths["auto_live_risk_reservations.csv"] = upsert_auto_live_risk_reservation(
                config,
                tickets,
                run_id=run_id,
                status=status,
                tickets_hash=tickets_hash,
                auto_hash=auto_hash,
                dedupe_key=dedupe_key,
                message=message,
            )
            append_auto_live_submission_ledger(output_dir, run)
            if status in {"submission_uncertain", "partial_failed"}:
                status_paths.update(send_auto_live_incident_alert(
                    config,
                    event_type=status,
                    incident_key=run_id,
                    severity="critical",
                    message=message,
                    context={"run_id": run_id, "dedupe_key": dedupe_key, "submitted_count": submitted, "uncertain_count": uncertain},
                ))
            status_paths.update(
                record_auto_live_event(
                    config,
                    "submission_result",
                    run_id,
                    {
                        "adapter": adapter,
                        "status": status,
                        "order_count": len(tickets),
                        "submitted_count": submitted,
                        "uncertain_count": uncertain,
                        "tickets_sha256": tickets_hash,
                        "dedupe_key": dedupe_key,
                        "ack_audit_passed": ack_passed,
                    },
                    strict=True,
                )
            )
        return {"auto_live_run.csv": run_path, "auto_live_order_results.csv": result_path, "auto_live_ack_audit.csv": ack_path, **status_paths}
    finally:
        release_auto_live_submit_lock(lock_path)
        status_paths["auto_live_submit_lock_audit.csv"] = write_auto_live_submit_lock_audit(config)
        status_paths["auto_live_risk_audit.csv"] = write_auto_live_risk_audit(config, tickets=tickets)


def query_auto_live_orders(config: dict[str, Any], raise_on_blocked: bool = True) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    source_results = load_auto_live_query_source(output_dir, config=config)
    current_results = read_csv(output_dir / "auto_live_order_results.csv")
    if not source_results.empty and not source_results.equals(current_results):
        atomic_to_csv(source_results, output_dir / "auto_live_order_results.csv", index=False, encoding="utf-8-sig")
    query_run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    adapter = str(config.get("auto_live", {}).get("broker_adapter", "") or "").strip().lower()
    status_path = output_dir / "auto_live_order_status.csv"
    audit_path = output_dir / "auto_live_order_status_audit.csv"
    recovery_path = output_dir / "auto_live_submission_recovery.csv"
    adapter_ready, adapter_value = auto_live_query_adapter_ready(config)

    if not adapter_ready:
        statuses = pd.DataFrame(columns=AUTO_LIVE_ORDER_STATUS_COLUMNS)
    elif adapter == "mock":
        statuses = query_with_mock_adapter(source_results, query_run_id)
    elif adapter == "command":
        statuses = query_with_command_adapter(config, source_results, query_run_id)
    else:
        statuses = pd.DataFrame(columns=AUTO_LIVE_ORDER_STATUS_COLUMNS)

    audit = build_auto_live_order_status_audit(
        source_results,
        statuses,
        query_run_id,
        adapter_ready,
        adapter_value,
        source_integrity_passed=bool(source_results.attrs.get("query_source_integrity_passed", False)),
        source_integrity_value=str(source_results.attrs.get("query_source_integrity_reason", "query_source_integrity_not_evaluated")),
    )
    recovery = build_auto_live_submission_recovery(source_results, statuses)
    atomic_to_csv(statuses, status_path, index=False, encoding="utf-8-sig")
    status_file_sha256 = hashlib.sha256(status_path.read_bytes()).hexdigest()
    audit = pd.concat(
        [
            audit,
            pd.DataFrame(
                [
                    {
                        "check": "order_status_file_sha256",
                        "passed": True,
                        "value": status_file_sha256,
                        "threshold": "must match current auto_live_order_status.csv before cancel",
                        "severity": "ok",
                        "next_action": "",
                        "message": "The persisted broker status file is fingerprinted so cancel cannot reuse a stale audit after the source changes.",
                    }
                ],
                columns=AUTO_LIVE_ORDER_STATUS_AUDIT_COLUMNS,
            ),
        ],
        ignore_index=True,
    )
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(recovery, recovery_path, index=False, encoding="utf-8-sig")
    journal_paths = record_auto_live_event(
        config,
        "order_query",
        query_run_id,
        {
            "source_rows": len(source_results),
            "status_rows": len(statuses),
            "audit_passed": all_passed(audit),
            "broker_statuses": statuses["broker_status"].astype(str).value_counts().to_dict() if not statuses.empty and "broker_status" in statuses.columns else {},
        },
        strict=False,
    )
    if raise_on_blocked and not all_passed(audit):
        failed = ";".join(audit.loc[~audit["passed"].map(parse_bool_like), "check"].astype(str).tolist())
        raise RuntimeError(f"auto-live query blocked: {failed or 'order_status_audit_failed'}")
    return {"auto_live_order_status.csv": status_path, "auto_live_order_status_audit.csv": audit_path, "auto_live_submission_recovery.csv": recovery_path, **journal_paths}


def load_auto_live_query_source(
    output_dir: Path,
    *,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    def tagged(frame: pd.DataFrame, passed: bool, reason: str) -> pd.DataFrame:
        result = frame.copy()
        result.attrs["query_source_integrity_passed"] = bool(passed)
        result.attrs["query_source_integrity_reason"] = reason
        return result

    results = read_csv(output_dir / "auto_live_order_results.csv")
    intent = read_csv(output_dir / "auto_live_submission_intent.csv")
    if intent.empty:
        return tagged(results, False, "submission_intent_missing")
    empty = pd.DataFrame(columns=AUTO_LIVE_ORDER_RESULT_COLUMNS)
    intent_row = intent.iloc[-1]
    run_id = str(intent_row.get("run_id", "") or "")
    if not run_id:
        return tagged(empty, False, "submission_intent_run_id_missing")
    expected_value = pd.to_numeric(intent_row.get("order_count", 0), errors="coerce")
    expected = int(expected_value) if not pd.isna(expected_value) and float(expected_value) > 0 else 0
    current = pd.DataFrame(columns=AUTO_LIVE_ORDER_RESULT_COLUMNS)
    if run_id and not results.empty and "run_id" in results.columns:
        current = results[results["run_id"].astype(str).eq(run_id)].copy()
    adapter = str(intent_row.get("adapter", "") or "").strip().lower()
    if adapter != "command" and expected > 0 and len(current) == expected:
        return tagged(current[AUTO_LIVE_ORDER_RESULT_COLUMNS], True, "noncommand_results_bound_to_intent")

    request_name = str(intent_row.get("request_file", "") or "").strip()
    if not request_name and run_id:
        request_name = f"auto_live_command_request_{run_id}.json"
    request_path = output_dir / Path(request_name).name if request_name else output_dir / "missing-request.json"
    payload = read_json_dict(request_path)
    if not payload or str(payload.get("mode", "")) != "submit_orders" or str(payload.get("run_id", "")) != run_id:
        return tagged(empty, False, "submit_request_missing_or_identity_mismatch")
    request_integrity_valid, request_integrity_value = verify_adapter_request_integrity(payload)
    if not request_integrity_valid:
        return tagged(empty, False, f"submit_request_integrity_failed:{request_integrity_value}")
    if (
        config is not None
        and bool(command_integrity_status(config).get("required", False))
        and request_integrity_value == "integrity_optional_missing"
    ):
        return tagged(empty, False, "submit_request_required_integrity_missing")
    intent_ticket_hash = str(intent_row.get("tickets_sha256", "") or "")
    payload_ticket_hash = str(payload.get("tickets_sha256", "") or "")
    if not intent_ticket_hash or payload_ticket_hash != intent_ticket_hash:
        return tagged(empty, False, "submit_request_ticket_hash_mismatch")
    orders = payload.get("orders", [])
    if not isinstance(orders, list) or not orders or expected <= 0 or len(orders) != expected:
        return tagged(empty, False, "submit_request_order_count_mismatch")
    if not all(isinstance(order, dict) for order in orders):
        return tagged(empty, False, "submit_request_order_rows_invalid")
    if tickets_sha256(pd.DataFrame(orders)) != intent_ticket_hash:
        return tagged(empty, False, "submit_request_order_hash_mismatch")
    if not current.empty:
        if len(current) != expected:
            return tagged(empty, False, "current_result_order_count_mismatch")
        if not query_source_results_match_request(current, orders):
            return tagged(empty, False, "current_result_order_identity_mismatch")
        return tagged(current[AUTO_LIVE_ORDER_RESULT_COLUMNS], True, "command_results_bound_to_request")
    rows = []
    for order in orders:
        if not str(order.get("ticket_id", "") or ""):
            return tagged(empty, False, "submit_request_ticket_id_missing")
        rows.append(
            {
                "run_id": run_id,
                "date": order.get("date", ""),
                "ticket_id": order.get("ticket_id", ""),
                "symbol": order.get("symbol", ""),
                "side": order.get("side", ""),
                "quantity": order.get("quantity", ""),
                "limit_price": order.get("limit_price", ""),
                "status": "unknown",
                "broker_order_id": "",
                "message": "Recovered from the persisted pre-submit request after an interrupted adapter run; broker acceptance is unknown.",
            }
        )
    return tagged(
        pd.DataFrame(rows, columns=AUTO_LIVE_ORDER_RESULT_COLUMNS),
        True,
        "unknown_results_reconstructed_from_bound_request",
    )


def query_source_results_match_request(results: pd.DataFrame, orders: list[dict[str, Any]]) -> bool:
    if "ticket_id" not in results.columns:
        return False
    result_records = results.to_dict("records")
    result_ids = [clean_identifier(record.get("ticket_id")) for record in result_records]
    order_ids = [clean_identifier(record.get("ticket_id")) for record in orders]
    if any(not value for value in result_ids + order_ids):
        return False
    if len(result_ids) != len(set(result_ids)) or len(order_ids) != len(set(order_ids)):
        return False
    if set(result_ids) != set(order_ids):
        return False
    result_by_id = {clean_identifier(record.get("ticket_id")): record for record in result_records}
    for order in orders:
        order_identity = _query_identity(order)
        if (
            order_identity[0] == "000000"
            or not order_identity[1]
            or order_identity[2] is None
            or order_identity[3] is None
        ):
            return False
        result = result_by_id[clean_identifier(order.get("ticket_id"))]
        if _query_identity(result) != order_identity:
            return False
    return True


def _query_identity(record: dict[str, Any]) -> tuple[str, str, int | None, Decimal | None, str]:
    digits = "".join(character for character in clean_identifier(record.get("symbol")) if character.isdigit())
    symbol = digits.zfill(6)
    side = clean_identifier(record.get("side")).lower()
    try:
        quantity = int(Decimal(clean_identifier(record.get("quantity"))))
    except (InvalidOperation, ValueError):
        quantity = None
    try:
        price = Decimal(clean_identifier(record.get("limit_price")))
        if not price.is_finite():
            price = None
    except InvalidOperation:
        price = None
    date = clean_identifier(record.get("date"))
    return symbol, side, quantity, price, date


def command_adapter_account_snapshot_selftest_status(config: dict[str, Any]) -> tuple[bool, str]:
    output_dir = project_path(config["paths"]["output_dir"])
    frame = read_csv(output_dir / "auto_live_adapter_selftest.csv")
    required_checks = [
        "adapter_is_command",
        "command_adapter_guarded",
        "command_adapter_command_configured",
        "command_adapter_executable_found",
        "command_adapter_timeout_valid",
        "command_adapter_hmac_integrity_ready",
        "command_adapter_files_pinned",
        "selftest_invoked",
        "selftest_process_exit_zero",
        "selftest_result_file_written",
        "selftest_result_json",
        "selftest_result_integrity_valid",
        "selftest_schema_version",
        "selftest_run_id_match",
        "selftest_status_ok",
        "selftest_account_snapshot_capability_declared",
        "selftest_config_hash_match",
    ]
    if frame.empty:
        return False, "missing"
    missing = [check for check in required_checks if check not in set(frame.get("check", pd.Series(dtype=str)).astype(str))]
    failed = [check for check in required_checks if check not in missing and not check_passed(frame, check)]
    passed = not missing and not failed
    return passed, f"account_snapshot_only={passed};missing={';'.join(missing)};failed={';'.join(failed)}"


def _auto_live_adapter_ready_for_selftest(
    config: dict[str, Any],
    selftest_status: tuple[bool, str],
) -> tuple[bool, str]:
    auto = config.get("auto_live", {})
    adapter = str(auto.get("broker_adapter", "") or "").strip().lower()
    if adapter == "mock":
        allowed = bool(auto.get("allow_mock_adapter", False))
        return allowed, "mock_ready" if allowed else "mock_disabled"
    if adapter == "command":
        status = command_adapter_status(config)
        required = ["allow_command_adapter", "command_configured", "executable_found", "timeout_valid"]
        selftest_passed, selftest_value = selftest_status
        integrity = command_integrity_status(config)
        file_integrity_passed, file_integrity_value, _ = command_adapter_file_status(config)
        ready = all(bool(status[key]) for key in required) and selftest_passed and bool(integrity["ready"]) and file_integrity_passed
        value = f"command_ready={ready};selftest={selftest_value};hmac_required={integrity['required']};hmac_ready={integrity['ready']};files={file_integrity_value}"
        return ready, value
    return False, adapter or "missing"


def auto_live_query_adapter_ready(config: dict[str, Any]) -> tuple[bool, str]:
    return _auto_live_adapter_ready_for_selftest(config, command_adapter_selftest_status(config))


def auto_live_account_adapter_ready(config: dict[str, Any]) -> tuple[bool, str]:
    return _auto_live_adapter_ready_for_selftest(config, command_adapter_account_snapshot_selftest_status(config))


def queryable_order_results(results: pd.DataFrame) -> pd.DataFrame:
    if results.empty:
        return pd.DataFrame(columns=AUTO_LIVE_ORDER_RESULT_COLUMNS)
    frame = results.copy()
    for column in AUTO_LIVE_ORDER_RESULT_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    statuses = frame["status"].astype(str).str.lower()
    broker_ids = frame["broker_order_id"].fillna("").astype(str).str.strip()
    queryable = ((statuses == "submitted") & (broker_ids != "")) | (statuses == "unknown")
    return frame[queryable][AUTO_LIVE_ORDER_RESULT_COLUMNS].copy()


def query_with_mock_adapter(source_results: pd.DataFrame, query_run_id: str) -> pd.DataFrame:
    rows = []
    for row in queryable_order_results(source_results).itertuples(index=False):
        rows.append(order_status_row(row, query_run_id, "accepted", 0, 0.0, 0.0, 0.0, "Mock adapter reports accepted status only."))
    return pd.DataFrame(rows, columns=AUTO_LIVE_ORDER_STATUS_COLUMNS)


def query_with_command_adapter(config: dict[str, Any], source_results: pd.DataFrame, query_run_id: str) -> pd.DataFrame:
    source = queryable_order_results(source_results)
    if source.empty:
        return pd.DataFrame(columns=AUTO_LIVE_ORDER_STATUS_COLUMNS)
    output_dir = project_path(config["paths"]["output_dir"])
    request_path = output_dir / f"auto_live_query_request_{query_run_id}.json"
    result_path = output_dir / f"auto_live_query_result_{query_run_id}.json"
    write_command_adapter_query_request(config, source, query_run_id, request_path, result_path)
    argv = build_command_adapter_invocation(config, request_path, result_path, query_run_id)
    adapter_cfg = config.get("auto_live", {}).get("command_adapter", {})
    timeout = float(adapter_cfg.get("timeout_seconds", 30))
    cwd_value = adapter_cfg.get("working_dir", "")
    cwd = project_path(cwd_value) if cwd_value else None
    try:
        completed = subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        return command_query_error_statuses(source, query_run_id, "unknown", f"Command adapter query timed out after {timeout:g}s.")
    except OSError as exc:
        return command_query_error_statuses(source, query_run_id, "unknown", f"Command adapter query failed to start: {exc}")
    if completed.returncode != 0:
        message = truncate_text((completed.stderr or completed.stdout or "").strip(), 300)
        return command_query_error_statuses(source, query_run_id, "failed", f"Command adapter query exited {completed.returncode}: {message}")
    if not result_path.exists():
        message = truncate_text((completed.stdout or "").strip(), 300)
        return command_query_error_statuses(source, query_run_id, "unknown", f"Command adapter query did not write result file. {message}")
    integrity_passed, integrity_value = command_result_integrity_status(config, request_path, result_path)
    if not integrity_passed:
        return command_query_error_statuses(source, query_run_id, "unknown", f"Command adapter query result integrity failed: {integrity_value}")
    return normalize_command_adapter_order_statuses(source, query_run_id, result_path)


def write_command_adapter_query_request(config: dict[str, Any], source: pd.DataFrame, query_run_id: str, request_path: Path, result_path: Path) -> None:
    source_run_id = str(source["run_id"].iloc[0]) if not source.empty and "run_id" in source.columns else ""
    payload = {
        "schema_version": COMMAND_ADAPTER_SCHEMA,
        "mode": "query_orders",
        "run_id": query_run_id,
        "source_run_id": source_run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result_path": str(result_path),
        "auto_live_config_sha256": auto_live_config_sha256(config),
        "orders": dataframe_records(source),
    }
    payload = attach_request_integrity(config, payload)
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_command_adapter_order_statuses(source: pd.DataFrame, query_run_id: str, result_path: Path) -> pd.DataFrame:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return command_query_error_statuses(source, query_run_id, "unknown", f"Command adapter query result is not valid JSON: {exc}")
    raw_results = payload.get("results", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_results, list):
        return command_query_error_statuses(source, query_run_id, "unknown", "Command adapter query result must be a list or {'results': [...]}.")
    seen_tickets: set[str] = set()
    seen_broker_ids: set[str] = set()
    duplicate_tickets: set[str] = set()
    duplicate_broker_ids: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        ticket_id = str(item.get("ticket_id", "")).strip()
        broker_order_id = str(item.get("broker_order_id", "")).strip()
        if ticket_id:
            if ticket_id in seen_tickets:
                duplicate_tickets.add(ticket_id)
            seen_tickets.add(ticket_id)
        if broker_order_id:
            if broker_order_id in seen_broker_ids:
                duplicate_broker_ids.add(broker_order_id)
            seen_broker_ids.add(broker_order_id)
    if duplicate_tickets or duplicate_broker_ids:
        details: list[str] = []
        if duplicate_tickets:
            details.append("ticket_id=" + ",".join(sorted(duplicate_tickets)))
        if duplicate_broker_ids:
            details.append("broker_order_id=" + ",".join(sorted(duplicate_broker_ids)))
        return command_query_error_statuses(
            source,
            query_run_id,
            "unknown",
            "Command adapter query result contains duplicate identifiers: " + ";".join(details),
        )
    by_ticket: dict[str, dict[str, object]] = {}
    by_broker_id: dict[str, dict[str, object]] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        ticket_id = str(item.get("ticket_id", ""))
        broker_order_id = str(item.get("broker_order_id", ""))
        if ticket_id:
            by_ticket[ticket_id] = item
        if broker_order_id:
            by_broker_id[broker_order_id] = item
    rows = []
    for source_row in source.itertuples(index=False):
        ticket_id = str(getattr(source_row, "ticket_id", ""))
        broker_order_id = str(getattr(source_row, "broker_order_id", ""))
        item = by_ticket.get(ticket_id) or by_broker_id.get(broker_order_id)
        if item is None:
            rows.append(order_status_row(source_row, query_run_id, "unknown", 0, 0.0, 0.0, 0.0, "Command adapter omitted this broker order."))
            continue
        broker_status = str(item.get("broker_status", item.get("status", "unknown")) or "unknown").lower()
        if broker_status not in BROKER_ORDER_STATUSES:
            broker_status = "unknown"
        rows.append(
            order_status_row(
                source_row,
                query_run_id,
                broker_status,
                item.get("filled_quantity", 0),
                item.get("avg_fill_price", 0.0),
                item.get("filled_amount", 0.0),
                item.get("fee", 0.0),
                "" if item.get("message") is None else str(item.get("message")),
                item.get("trade_date", item.get("date")),
                item.get("broker_order_id", broker_order_id),
                item.get("fee_status"),
                item.get("fee_source", ""),
            )
        )
    return pd.DataFrame(rows, columns=AUTO_LIVE_ORDER_STATUS_COLUMNS)


def command_query_error_statuses(source: pd.DataFrame, query_run_id: str, status: str, message: str) -> pd.DataFrame:
    rows = [order_status_row(row, query_run_id, status, 0, 0.0, 0.0, 0.0, message) for row in source.itertuples(index=False)]
    return pd.DataFrame(rows, columns=AUTO_LIVE_ORDER_STATUS_COLUMNS)


def order_status_row(
    source_row: Any, query_run_id: str, broker_status: str, filled_quantity: object,
    avg_fill_price: object, filled_amount: object, fee: object, message: str,
    trade_date: object | None = None, resolved_broker_order_id: object | None = None,
    fee_status: object | None = None, fee_source: object = "",
) -> dict[str, object]:
    resolved_trade_date = getattr(source_row, "date", "") if trade_date is None else trade_date
    broker_order_id = getattr(source_row, "broker_order_id", "") if resolved_broker_order_id is None else resolved_broker_order_id
    parsed_filled = pd.to_numeric(filled_quantity, errors="coerce")
    has_fill = not pd.isna(parsed_filled) and float(parsed_filled) > 0
    resolved_fee_status = str(fee_status or "").strip().lower()
    if not resolved_fee_status:
        resolved_fee_status = "unverified" if has_fill else "not_applicable"
    resolved_fee_source = str(fee_source or "").strip() or ("adapter_unspecified" if has_fill else "none")
    return {
        "query_run_id": query_run_id,
        "source_run_id": getattr(source_row, "run_id", ""),
        "trade_date": resolved_trade_date,
        "ticket_id": getattr(source_row, "ticket_id", ""),
        "symbol": getattr(source_row, "symbol", ""),
        "side": getattr(source_row, "side", ""),
        "quantity": getattr(source_row, "quantity", ""),
        "limit_price": getattr(source_row, "limit_price", ""),
        "broker_order_id": broker_order_id,
        "broker_status": broker_status,
        "filled_quantity": filled_quantity,
        "avg_fill_price": avg_fill_price,
        "filled_amount": filled_amount,
        "fee": fee,
        "fee_status": resolved_fee_status,
        "fee_source": resolved_fee_source,
        "message": message,
    }


def build_auto_live_order_status_audit(
    source_results: pd.DataFrame,
    statuses: pd.DataFrame,
    query_run_id: str,
    adapter_ready: bool,
    adapter_value: str,
    *,
    source_integrity_passed: bool = True,
    source_integrity_value: str = "query_source_integrity_not_evaluated",
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "severity": "ok" if passed else severity, "next_action": "" if passed else next_action, "message": message})

    source = queryable_order_results(source_results)
    add(
        "query_source_integrity",
        source_integrity_passed,
        source_integrity_value,
        "submission intent, request hash and order identities bound",
        "query_blocker",
        "检查提交意图、原始请求和当前结果是否属于同一批订单。",
        "查询恢复源必须绑定原始提交请求；任何请求篡改、结果串批或身份不一致都会阻断。",
    )
    add("query_adapter_ready", adapter_ready, adapter_value, "adapter query ready", "query_blocker", "先通过 command self-test 并确认 query_orders 能力。", "查询委托状态必须使用已审计适配器。")
    add("source_submitted_orders_present", len(source) > 0, len(source), ">0 submitted broker orders", "query_blocker", "先运行 auto-live-run 并获得券商委托号。", "只有已提交且有券商委托号的订单才能查询状态。")
    add("query_rows_match_submitted_orders", len(statuses) == len(source), f"status_rows={len(statuses)};source_rows={len(source)}", "one status per submitted order", "query_blocker", "检查适配器 query_orders 是否漏回或多回。", "每个券商委托都必须有一条状态回报。")

    source_tickets = set(source["ticket_id"].astype(str)) if "ticket_id" in source.columns else set()
    status_tickets = set(statuses["ticket_id"].astype(str)) if "ticket_id" in statuses.columns else set()
    add("query_ticket_ids_match_submitted", status_tickets == source_tickets, f"matched={len(status_tickets & source_tickets)};source={len(source_tickets)}", "same ticket_id set", "query_blocker", "检查 query_orders 返回 ticket_id。", "查询回报必须能映射到原始订单。")

    source_id_by_ticket = {str(row.ticket_id): clean_identifier(row.broker_order_id) for row in source.itertuples(index=False)} if {"ticket_id", "broker_order_id"}.issubset(source.columns) else {}
    status_id_by_ticket = {str(row.ticket_id): clean_identifier(row.broker_order_id) for row in statuses.itertuples(index=False)} if {"ticket_id", "broker_order_id"}.issubset(statuses.columns) else {}
    broker_ids_bound = bool(source_id_by_ticket) and all(
        bool(status_id_by_ticket.get(ticket_id, "")) and (not source_id or status_id_by_ticket.get(ticket_id, "") == source_id)
        for ticket_id, source_id in source_id_by_ticket.items()
    )
    recovered_ids = sum(1 for ticket_id, source_id in source_id_by_ticket.items() if not source_id and bool(status_id_by_ticket.get(ticket_id, "")))
    add("query_broker_order_ids_match_submitted", broker_ids_bound, f"bound={sum(1 for ticket_id in source_id_by_ticket if status_id_by_ticket.get(ticket_id, ''))};recovered={recovered_ids};source={len(source_id_by_ticket)}", "preserve known IDs and recover missing IDs", "query_blocker", "检查 query_orders 是否按 ticket_id 找回券商委托号。", "查询回报必须保留已有券商委托号，并为提交状态不确定的订单找回委托号。")

    query_ids = statuses["query_run_id"].astype(str).tolist() if "query_run_id" in statuses.columns else []
    add("query_run_id_match", bool(query_ids) and all(value == str(query_run_id) for value in query_ids), ",".join(sorted(set(query_ids))) if query_ids else "missing", query_run_id, "query_blocker", "确认查询结果来自本次 query_run_id。", "查询回报必须绑定本次查询运行号。")

    broker_statuses = statuses["broker_status"].astype(str).str.lower() if "broker_status" in statuses.columns else pd.Series(dtype=str)
    status_counts = broker_statuses.value_counts().to_dict() if not broker_statuses.empty else {}
    status_text = ";".join(f"{key}={value}" for key, value in sorted(status_counts.items())) or "missing"
    add("broker_status_values_valid", set(broker_statuses).issubset(BROKER_ORDER_STATUSES) and not broker_statuses.empty, status_text, ",".join(sorted(BROKER_ORDER_STATUSES)), "query_blocker", "修正适配器 broker_status 映射。", "券商委托状态必须归一化到系统支持的状态集合。")
    add("broker_statuses_actionable", set(broker_statuses).issubset(BROKER_ORDER_ACTIONABLE_STATUSES) and not broker_statuses.empty, status_text, ",".join(sorted(BROKER_ORDER_ACTIONABLE_STATUSES)), "query_blocker", "处理 rejected/failed/unknown 委托状态，必要时人工核对券商端。", "自动实盘查询不能存在未知、失败或拒单状态。")

    quantities = pd.to_numeric(statuses.get("quantity", pd.Series(dtype=float)), errors="coerce").fillna(0)
    filled = pd.to_numeric(statuses.get("filled_quantity", pd.Series(dtype=float)), errors="coerce").fillna(-1)
    valid_filled = len(statuses) > 0 and bool(((filled >= 0) & (filled <= quantities)).all())
    add("filled_quantity_within_order_quantity", valid_filled, f"max_filled={float(filled.max()) if len(filled) else 0:g};max_order={float(quantities.max()) if len(quantities) else 0:g}", "0 <= filled_quantity <= quantity", "query_blocker", "检查适配器成交数量或订单数量映射。", "成交数量不能为负，也不能超过原始委托数量。")

    prices = pd.to_numeric(statuses.get("avg_fill_price", pd.Series(dtype=float)), errors="coerce").fillna(0)
    amounts = pd.to_numeric(statuses.get("filled_amount", pd.Series(dtype=float)), errors="coerce").fillna(0)
    fees = pd.to_numeric(statuses.get("fee", pd.Series(dtype=float)), errors="coerce").fillna(0)
    valid_money = len(statuses) > 0 and bool(((prices >= 0) & (amounts >= 0) & (fees >= 0)).all())
    add("fill_money_fields_non_negative", valid_money, f"min_price={float(prices.min()) if len(prices) else 0:g};min_amount={float(amounts.min()) if len(amounts) else 0:g};min_fee={float(fees.min()) if len(fees) else 0:g}", ">=0", "query_blocker", "检查适配器成交价格、金额和费用字段。", "成交价格、金额和费用字段必须非负，才能用于后续账户同步。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_ORDER_STATUS_AUDIT_COLUMNS)


def build_auto_live_submission_recovery(source_results: pd.DataFrame, statuses: pd.DataFrame) -> pd.DataFrame:
    source = queryable_order_results(source_results)
    if source.empty:
        return pd.DataFrame(columns=AUTO_LIVE_SUBMISSION_RECOVERY_COLUMNS)
    status_by_ticket = {str(row.ticket_id): row for row in statuses.itertuples(index=False)} if "ticket_id" in statuses.columns else {}
    rows: list[dict[str, object]] = []
    for source_row in source.itertuples(index=False):
        ticket_id = str(getattr(source_row, "ticket_id", ""))
        submission_status = str(getattr(source_row, "status", "")).lower()
        status_row = status_by_ticket.get(ticket_id)
        broker_order_id = clean_identifier(getattr(status_row, "broker_order_id", getattr(source_row, "broker_order_id", "")))
        broker_status = str(getattr(status_row, "broker_status", "unknown") or "unknown").lower()
        resolved = bool(broker_order_id) and broker_status in BROKER_ORDER_ACTIONABLE_STATUSES
        if resolved:
            action = "continue_query_cancel_or_settlement"
        elif broker_status in {"rejected", "failed"}:
            action = "manual_review_rejection_no_resubmit"
        else:
            action = "manual_broker_review_no_resubmit"
        message = str(getattr(status_row, "message", "No broker query result.")) if status_row is not None else "No broker query result."
        rows.append(
            {
                "source_run_id": getattr(source_row, "run_id", ""),
                "ticket_id": ticket_id,
                "submission_status": submission_status,
                "broker_order_id": broker_order_id,
                "broker_status": broker_status,
                "resolved": resolved,
                "action": action,
                "message": message,
            }
        )
    return pd.DataFrame(rows, columns=AUTO_LIVE_SUBMISSION_RECOVERY_COLUMNS)


def submit_with_adapter(config: dict[str, Any], tickets: pd.DataFrame, run_id: str, dry_run: bool = False) -> pd.DataFrame:
    adapter = str(config.get("auto_live", {}).get("broker_adapter", "") or "").strip().lower()
    if dry_run:
        return adapter_results(tickets, run_id, "dry_run", "", "Dry-run: broker adapter was not called.")
    stop_active, stop_value = auto_live_emergency_stop_status(config)
    if stop_active:
        return adapter_results(tickets, run_id, "failed", "", f"Emergency stop active immediately before adapter invocation ({stop_value}); broker adapter was not called.")
    if adapter == "mock":
        if not bool(config.get("auto_live", {}).get("allow_mock_adapter", False)):
            raise RuntimeError("mock broker adapter requires auto_live.allow_mock_adapter=true")
        rows = []
        for row in tickets.itertuples(index=False):
            ticket = getattr(row, "ticket_id", "")
            rows.append(adapter_result_row(row, run_id, "submitted", f"mock-{ticket}", "Mock adapter accepted the order."))
        return pd.DataFrame(rows, columns=AUTO_LIVE_ORDER_RESULT_COLUMNS)
    if adapter == "command":
        return submit_with_command_adapter(config, tickets, run_id)
    raise RuntimeError(f"unsupported broker adapter for auto-live submit: {adapter or 'missing'}")


def build_auto_live_ack_audit(tickets: pd.DataFrame, results: pd.DataFrame, run_id: str, dry_run: bool = False) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    result_count = len(results)
    ticket_count = len(tickets)
    add("result_rows_match_orders", result_count == ticket_count, f"results={result_count};orders={ticket_count}", "one result per order", "ack_blocker", "检查外部适配器是否漏回或重复回报。", "每张执行票据必须有一条归一化券商回报。")

    expected_ticket_ids = tickets["ticket_id"].astype(str).tolist() if "ticket_id" in tickets.columns else []
    result_ticket_ids = results["ticket_id"].astype(str).tolist() if "ticket_id" in results.columns else []
    duplicate_expected = len(expected_ticket_ids) - len(set(expected_ticket_ids))
    duplicate_results = len(result_ticket_ids) - len(set(result_ticket_ids))
    add("order_ticket_ids_unique", duplicate_expected == 0, duplicate_expected, 0, "ack_blocker", "重新生成 RPA 工单，确保 ticket_id 唯一。", "提交前订单 ticket_id 必须唯一。")
    add("result_ticket_ids_unique", duplicate_results == 0, duplicate_results, 0, "ack_blocker", "检查适配器返回结果，确保 ticket_id 唯一。", "适配器回报 ticket_id 必须唯一。")
    add("result_ticket_ids_match_orders", set(result_ticket_ids) == set(expected_ticket_ids), f"matched={len(set(result_ticket_ids) & set(expected_ticket_ids))};orders={len(set(expected_ticket_ids))}", "same ticket_id set", "ack_blocker", "检查适配器返回是否遗漏订单或返回未知 ticket_id。", "券商回报必须能逐票对应本次提交订单。")

    run_ids = results["run_id"].astype(str).tolist() if "run_id" in results.columns else []
    run_id_match = bool(run_ids) and all(value == str(run_id) for value in run_ids)
    add("result_run_id_match", run_id_match, ",".join(sorted(set(run_ids))) if run_ids else "missing", run_id, "ack_blocker", "检查适配器结果是否来自本次 run_id。", "run_id 绑定防止读取旧回报文件。")

    statuses = results["status"].astype(str).str.lower() if "status" in results.columns else pd.Series(dtype=str)
    status_counts = statuses.value_counts().to_dict() if not statuses.empty else {}
    status_text = ";".join(f"{key}={value}" for key, value in sorted(status_counts.items())) or "missing"
    allowed_statuses = {"submitted", "failed", "rejected", "unknown", "dry_run"}
    add("result_status_values_valid", set(statuses).issubset(allowed_statuses) and not statuses.empty, status_text, ",".join(sorted(allowed_statuses)), "ack_blocker", "修正适配器逐票 status。", "适配器逐票状态必须在系统支持范围内。")

    if dry_run:
        add("dry_run_results_all_dry_run", result_count == ticket_count and bool((statuses == "dry_run").all()), status_text, "all dry_run", "ack_blocker", "dry-run 不应返回真实 submitted 状态。", "dry-run 结果只能表示演练，不应被误认为真实提交。")
        return pd.DataFrame(rows, columns=AUTO_LIVE_ACK_AUDIT_COLUMNS)

    all_submitted = result_count == ticket_count and bool((statuses == "submitted").all())
    add("broker_status_all_submitted", all_submitted, status_text, "all submitted", "ack_blocker", "检查券商拒单、适配器失败或未知状态原因。", "自动实盘提交必须获得每笔订单的 submitted 回报。")

    submitted_rows = results[statuses == "submitted"].copy() if not results.empty and "status" in results.columns else pd.DataFrame(columns=results.columns)
    broker_ids = submitted_rows["broker_order_id"].fillna("").astype(str).str.strip() if "broker_order_id" in submitted_rows.columns else pd.Series(dtype=str)
    missing_broker_ids = int((broker_ids == "").sum()) if not broker_ids.empty else ticket_count
    add("submitted_broker_order_ids_present", missing_broker_ids == 0 and len(broker_ids) == ticket_count, f"missing={missing_broker_ids};submitted={len(broker_ids)};orders={ticket_count}", "all submitted rows have broker_order_id", "ack_blocker", "适配器必须返回券商委托号；无法确认委托号时不要重试。", "真实提交回报必须包含券商委托号，便于撤单、查委托和成交对账。")

    non_empty_ids = broker_ids[broker_ids != ""] if not broker_ids.empty else pd.Series(dtype=str)
    duplicate_broker_ids = int(non_empty_ids.duplicated().sum()) if not non_empty_ids.empty else 0
    add("broker_order_ids_unique", duplicate_broker_ids == 0 and len(non_empty_ids) == len(set(non_empty_ids)), duplicate_broker_ids, 0, "ack_blocker", "检查适配器是否把多笔订单映射到同一券商委托号。", "券商委托号必须唯一，避免撤单和成交回报串票。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_ACK_AUDIT_COLUMNS)


def command_adapter_status(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {})
    adapter_cfg = auto.get("command_adapter", {}) if isinstance(auto.get("command_adapter", {}), dict) else {}
    command = adapter_cfg.get("command", [])
    argv = command_adapter_argv(command)
    executable = argv[0] if argv else ""
    executable_path = resolve_executable(executable) if executable else None
    timeout_raw = adapter_cfg.get("timeout_seconds", 30)
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = 0.0
    return {
        "allow_command_adapter": bool(auto.get("allow_command_adapter", False)),
        "command_configured": bool(argv),
        "command_value": " ".join(argv) if argv else "missing",
        "executable_found": executable_path is not None,
        "executable_value": str(executable_path) if executable_path is not None else (executable or "missing"),
        "timeout_valid": timeout_seconds > 0,
        "timeout_seconds": timeout_seconds,
    }


def command_adapter_argv(command: object) -> list[str]:
    if isinstance(command, list):
        return [str(part) for part in command if str(part).strip()]
    if isinstance(command, str) and command.strip():
        return shlex.split(command, posix=False)
    return []


def resolve_executable(executable: str) -> Path | None:
    if not executable:
        return None
    candidate = project_path(executable)
    if candidate.exists():
        return candidate.resolve()
    found = shutil.which(executable)
    return Path(found).resolve() if found else None


def write_auto_live_adapter_selftest(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_adapter_selftest.csv"
    atomic_to_csv(build_auto_live_adapter_selftest(config), path, index=False, encoding="utf-8-sig")
    return path


def build_auto_live_adapter_selftest(config: dict[str, Any]) -> pd.DataFrame:
    auto = config.get("auto_live", {})
    adapter = str(auto.get("broker_adapter", "") or "").strip().lower()
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    request_path = output_dir / f"auto_live_adapter_selftest_request_{run_id}.json"
    result_path = output_dir / f"auto_live_adapter_selftest_result_{run_id}.json"
    status = command_adapter_status(config)
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    add("adapter_is_command", adapter == "command", adapter or "missing", "command", "auto_blocker", "配置 auto_live.broker_adapter=command。", "self-test 只适用于 command 外部适配器。")
    add("command_adapter_guarded", bool(status["allow_command_adapter"]), status["allow_command_adapter"], True, "auto_blocker", "设置 auto_live.allow_command_adapter=true。", "外部命令适配器必须显式开启。")
    add("command_adapter_command_configured", bool(status["command_configured"]), status["command_value"], "configured command", "auto_blocker", "配置 auto_live.command_adapter.command。", "self-test 需要可执行命令。")
    add("command_adapter_executable_found", bool(status["executable_found"]), status["executable_value"], "resolvable executable", "auto_blocker", "修正 command 可执行入口。", "self-test 前必须能解析外部适配器入口。")
    add("command_adapter_timeout_valid", bool(status["timeout_valid"]), status["timeout_seconds"], ">0 seconds", "auto_blocker", "设置 timeout_seconds 为正数。", "self-test 必须有超时保护。")
    integrity = command_integrity_status(config)
    add("command_adapter_hmac_integrity_ready", bool(integrity["ready"]), f"required={integrity['required']};key_env={integrity['key_env']};key_present={integrity['key_present']};key_bytes={integrity['key_bytes']}", "optional or HMAC key >= 32 bytes", "auto_blocker", f"在环境变量 {integrity['key_env']} 中配置至少 32 字节随机密钥。", "self-test 不会在必需 HMAC 密钥缺失或过短时调用外部程序。")
    file_integrity_passed, file_integrity_value, file_integrity_required = command_adapter_file_status(config)
    add("command_adapter_files_pinned", file_integrity_passed, file_integrity_value, "all configured files match SHA-256" if file_integrity_required else "optional", "auto_blocker", "核对桥接文件并更新 pinned_files。", "self-test 在桥接文件哈希不匹配时不会启动外部程序。")

    if adapter != "command" or not all(bool(status[key]) for key in ["allow_command_adapter", "command_configured", "executable_found", "timeout_valid"]) or not bool(integrity["ready"]) or not file_integrity_passed:
        add("selftest_invoked", False, "not_invoked", "command adapter config ready", "auto_blocker", "先修复 command 适配器配置。", "配置未通过时不会调用外部程序。")
        return pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_SELFTEST_COLUMNS)

    write_command_adapter_selftest_request(config, run_id, request_path, result_path)
    argv = build_command_adapter_invocation(config, request_path, result_path, run_id)
    adapter_cfg = auto.get("command_adapter", {}) if isinstance(auto.get("command_adapter", {}), dict) else {}
    cwd_value = adapter_cfg.get("working_dir", "")
    cwd = project_path(cwd_value) if cwd_value else None
    timeout = float(status["timeout_seconds"])
    add("selftest_invoked", True, request_path.name, "request file written", "auto_blocker", "", "已写入无订单 self-test 请求。")
    try:
        completed = subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout, capture_output=True, text=True, check=False)
        add("selftest_process_exit_zero", completed.returncode == 0, completed.returncode, 0, "auto_blocker", "查看 self-test 桥接程序 stderr/stdout。", truncate_text((completed.stderr or completed.stdout or "process completed").strip(), 300) or "process completed")
    except subprocess.TimeoutExpired:
        add("selftest_process_exit_zero", False, f"timeout>{timeout:g}s", 0, "auto_blocker", "检查外部适配器 self-test 是否会阻塞。", "self-test 调用超时。")
        return pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_SELFTEST_COLUMNS)
    except OSError as exc:
        add("selftest_process_exit_zero", False, type(exc).__name__, 0, "auto_blocker", "检查外部适配器命令是否可启动。", str(exc))
        return pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_SELFTEST_COLUMNS)

    add("selftest_result_file_written", result_path.exists(), result_path.name if result_path.exists() else "missing", "result file exists", "auto_blocker", "桥接程序必须写入 result_path 指定文件。", "self-test 结果文件必须存在。")
    payload = read_json_dict(result_path) if result_path.exists() else {}
    add("selftest_result_json", bool(payload), "ok" if payload else "invalid_or_empty", "valid JSON object", "auto_blocker", "确认 self-test 返回 JSON object。", "self-test 返回必须是 JSON object。")
    integrity_passed, integrity_value = command_result_integrity_status(config, request_path, result_path)
    add("selftest_result_integrity_valid", integrity_passed, integrity_value, "valid request-bound HMAC when required", "auto_blocker", "检查桥接器结果签名和进程环境密钥。", "self-test 结果必须与本次请求绑定，启用 HMAC 时签名必须有效。")
    schema = str(payload.get("schema_version", "")) if payload else ""
    add("selftest_schema_version", schema == COMMAND_ADAPTER_SCHEMA, schema or "missing", COMMAND_ADAPTER_SCHEMA, "auto_blocker", "返回 schema_version=ashare_quant.command_adapter.v1。", "适配器必须声明支持的请求/结果契约版本。")
    returned_run_id = str(payload.get("run_id", "")) if payload else ""
    add("selftest_run_id_match", returned_run_id == run_id, returned_run_id or "missing", run_id, "auto_blocker", "返回与请求一致的 run_id。", "run_id 绑定用于防止读取旧结果。")
    result_status = str(payload.get("status", "") or "").lower() if payload else ""
    add("selftest_status_ok", result_status in COMMAND_ADAPTER_SELFTEST_STATUS, result_status or "missing", ",".join(sorted(COMMAND_ADAPTER_SELFTEST_STATUS)), "auto_blocker", "返回 status=ok/ready/dry_run。", "self-test 必须明确返回可用状态。")
    capabilities = payload.get("capabilities", []) if payload else []
    capabilities = capabilities if isinstance(capabilities, list) else []
    capability_set = {str(item) for item in capabilities}
    add("selftest_submit_capability_declared", "submit_orders" in capability_set, ",".join(str(item) for item in capabilities) or "missing", "submit_orders", "auto_blocker", "返回 capabilities 包含 submit_orders。", "自动实盘适配器必须明确声明提交订单能力。")
    add("selftest_query_capability_declared", "query_orders" in capability_set, ",".join(str(item) for item in capabilities) or "missing", "query_orders", "auto_blocker", "返回 capabilities 包含 query_orders。", "自动实盘适配器必须支持委托状态查询，用于成交同步和异常恢复。")
    add("selftest_cancel_capability_declared", "cancel_orders" in capability_set, ",".join(str(item) for item in capabilities) or "missing", "cancel_orders", "auto_blocker", "返回 capabilities 包含 cancel_orders。", "自动实盘适配器必须支持撤销未成交或部分成交委托，用于异常恢复。")
    add("selftest_account_snapshot_capability_declared", "account_snapshot" in capability_set, ",".join(str(item) for item in capabilities) or "missing", "account_snapshot", "auto_blocker", "返回 capabilities 包含 account_snapshot。", "自动实盘适配器必须支持提交前读取券商账户现金和持仓快照。")
    add("selftest_required_capabilities_declared", COMMAND_ADAPTER_REQUIRED_CAPABILITIES.issubset(capability_set), ",".join(sorted(capability_set)) or "missing", ",".join(sorted(COMMAND_ADAPTER_REQUIRED_CAPABILITIES)), "auto_blocker", "返回全部必需 capabilities。", "自动实盘适配器必须同时支持提交订单、查询委托状态、撤单和账户快照。")
    add("selftest_config_hash_match", str(payload.get("auto_live_config_sha256", "")) == auto_live_config_sha256(config), str(payload.get("auto_live_config_sha256", ""))[:16] or "missing", auto_live_config_sha256(config)[:16], "auto_blocker", "返回当前 auto_live_config_sha256。", "self-test 结果必须绑定当前配置指纹。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_SELFTEST_COLUMNS)


def command_adapter_selftest_status(config: dict[str, Any]) -> tuple[bool, str]:
    output_dir = project_path(config["paths"]["output_dir"])
    frame = read_csv(output_dir / "auto_live_adapter_selftest.csv")
    if frame.empty or "passed" not in frame.columns:
        return False, "missing"
    passed = frame["passed"].map(parse_bool_like)
    config_check = check_passed(frame, "selftest_config_hash_match")
    value = f"{int(passed.sum())}/{len(frame)}; config_hash_match={config_check}"
    return bool(len(frame) > 0 and passed.all() and config_check), value


def command_adapter_replay_status(config: dict[str, Any]) -> tuple[bool, str, bool]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    required = parse_bool_like(auto.get("require_adapter_replay_certification", False))
    summary = read_csv(project_path(config["paths"]["output_dir"]) / "auto_live_adapter_replay_summary.csv")
    status = str(summary.iloc[-1].get("status", "missing")) if not summary.empty else "missing"
    fingerprint = str(summary.iloc[-1].get("auto_live_config_sha256", "")) if not summary.empty else ""
    config_match = fingerprint == auto_live_config_sha256(config)
    certified_live = status == "certified_live" and config_match
    passed = certified_live or not required
    return passed, f"required={required};status={status};config_match={config_match}", required


def write_command_adapter_selftest_request(config: dict[str, Any], run_id: str, request_path: Path, result_path: Path) -> None:
    payload = {
        "schema_version": COMMAND_ADAPTER_SCHEMA,
        "mode": "self_test",
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result_path": str(result_path),
        "auto_live_config_sha256": auto_live_config_sha256(config),
        "orders": [],
        "expected_response": {
            "schema_version": COMMAND_ADAPTER_SCHEMA,
            "run_id": run_id,
            "status": "ok",
            "capabilities": sorted(COMMAND_ADAPTER_REQUIRED_CAPABILITIES),
            "auto_live_config_sha256": auto_live_config_sha256(config),
        },
    }
    payload = attach_request_integrity(config, payload)
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def command_result_integrity_status(config: dict[str, Any], request_path: Path, result_path: Path) -> tuple[bool, str]:
    request = read_json_dict(request_path)
    response = read_json_dict(result_path)
    if not request:
        return False, "request_json_invalid_or_missing"
    if not response:
        return False, "result_json_invalid_or_missing"
    return verify_adapter_result_integrity(config, request, response)


def submit_with_command_adapter(config: dict[str, Any], tickets: pd.DataFrame, run_id: str) -> pd.DataFrame:
    status = command_adapter_status(config)
    required = ["allow_command_adapter", "command_configured", "executable_found", "timeout_valid"]
    file_integrity_passed, file_integrity_value, _ = command_adapter_file_status(config)
    hmac_integrity = command_integrity_status(config)
    if not all(bool(status[key]) for key in required) or not file_integrity_passed or not bool(hmac_integrity["ready"]):
        return adapter_results(tickets, run_id, "failed", "", f"Command adapter not ready: command={status};files={file_integrity_value};hmac_ready={hmac_integrity['ready']}")
    output_dir = project_path(config["paths"]["output_dir"])
    request_path = output_dir / f"auto_live_command_request_{run_id}.json"
    result_path = output_dir / f"auto_live_command_result_{run_id}.json"
    write_command_adapter_request(config, tickets, run_id, request_path, result_path)
    argv = build_command_adapter_invocation(config, request_path, result_path, run_id)
    adapter_cfg = config.get("auto_live", {}).get("command_adapter", {})
    timeout = float(adapter_cfg.get("timeout_seconds", 30))
    cwd_value = adapter_cfg.get("working_dir", "")
    cwd = project_path(cwd_value) if cwd_value else None
    stop_active, stop_value = auto_live_emergency_stop_status(config)
    if stop_active:
        return adapter_results(tickets, run_id, "failed", "", f"Emergency stop active before command process start ({stop_value}); broker adapter was not called.")
    try:
        completed = subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        return adapter_results(tickets, run_id, "unknown", "", f"Command adapter timed out after {timeout:g}s; broker acceptance is unknown. Do not resubmit.")
    except OSError as exc:
        return adapter_results(tickets, run_id, "failed", "", f"Command adapter failed to start: {exc}")
    if completed.returncode != 0:
        message = truncate_text((completed.stderr or completed.stdout or "").strip(), 300)
        return adapter_results(tickets, run_id, "unknown", "", f"Command adapter exited {completed.returncode} after start: {message}. Broker acceptance is unknown; do not resubmit.")
    if not result_path.exists():
        message = truncate_text((completed.stdout or "").strip(), 300)
        return adapter_results(tickets, run_id, "unknown", "", f"Command adapter did not write result file. {message} Broker acceptance is unknown; do not resubmit.")
    integrity_passed, integrity_value = command_result_integrity_status(config, request_path, result_path)
    if not integrity_passed:
        return adapter_results(tickets, run_id, "unknown", "", f"Command adapter result integrity failed ({integrity_value}); broker acceptance is unknown. Do not resubmit.")
    return normalize_command_adapter_results(tickets, run_id, result_path)


def build_command_adapter_invocation(config: dict[str, Any], request_path: Path, result_path: Path, run_id: str) -> list[str]:
    adapter_cfg = config.get("auto_live", {}).get("command_adapter", {})
    argv = command_adapter_argv(adapter_cfg.get("command", []))
    replacements = {
        "{request}": str(request_path),
        "{result}": str(result_path),
        "{run_id}": run_id,
    }
    has_placeholder = any(any(token in part for token in replacements) for part in argv)
    rendered = []
    for part in argv:
        value = part
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        rendered.append(value)
    if not has_placeholder:
        rendered.extend([str(request_path), str(result_path)])
    return rendered


def write_command_adapter_request(config: dict[str, Any], tickets: pd.DataFrame, run_id: str, request_path: Path, result_path: Path) -> None:
    payload = {
        "schema_version": COMMAND_ADAPTER_SCHEMA,
        "mode": "submit_orders",
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result_path": str(result_path),
        "auto_live_config_sha256": auto_live_config_sha256(config),
        "tickets_sha256": tickets_sha256(tickets),
        "emergency_stop_path": str(request_path.parent / "auto_live.stop"),
        "orders": dataframe_records(tickets),
    }
    payload = attach_request_integrity(config, payload)
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_command_adapter_results(tickets: pd.DataFrame, run_id: str, result_path: Path) -> pd.DataFrame:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return adapter_results(tickets, run_id, "unknown", "", f"Command adapter result is not valid JSON: {exc}. Broker acceptance is unknown; do not resubmit.")
    raw_results = payload.get("results", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_results, list):
        return adapter_results(tickets, run_id, "unknown", "", "Command adapter result must be a list or {'results': [...]}. Broker acceptance is unknown; do not resubmit.")
    by_ticket: dict[str, dict[str, object]] = {}
    for item in raw_results:
        if isinstance(item, dict):
            ticket_id = str(item.get("ticket_id", ""))
            if ticket_id:
                by_ticket[ticket_id] = item
    rows = []
    for row in tickets.itertuples(index=False):
        ticket_id = str(getattr(row, "ticket_id", ""))
        result = by_ticket.get(ticket_id)
        if result is None:
            rows.append(adapter_result_row(row, run_id, "unknown", "", "Command adapter omitted this ticket; broker acceptance is unknown. Do not resubmit."))
            continue
        status = str(result.get("status", "failed") or "failed").lower()
        if status not in {"submitted", "failed", "rejected", "unknown", "dry_run"}:
            status = "unknown"
        broker_order_id = "" if result.get("broker_order_id") is None else str(result.get("broker_order_id"))
        message = "" if result.get("message") is None else str(result.get("message"))
        rows.append(adapter_result_row(row, run_id, status, broker_order_id, message))
    return pd.DataFrame(rows, columns=AUTO_LIVE_ORDER_RESULT_COLUMNS)


def clean_identifier(value: object) -> str:
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value or "").strip()


def dataframe_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for record in frame.to_dict(orient="records"):
        clean: dict[str, object] = {}
        for key, value in record.items():
            if pd.isna(value):
                clean[str(key)] = None
            elif hasattr(value, "isoformat"):
                clean[str(key)] = value.isoformat()
            else:
                clean[str(key)] = value.item() if hasattr(value, "item") else value
        records.append(clean)
    return records


def truncate_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def adapter_results(tickets: pd.DataFrame, run_id: str, status: str, broker_order_id: str, message: str) -> pd.DataFrame:
    return pd.DataFrame(
        [adapter_result_row(row, run_id, status, broker_order_id, message) for row in tickets.itertuples(index=False)],
        columns=AUTO_LIVE_ORDER_RESULT_COLUMNS,
    )


def adapter_result_row(row: Any, run_id: str, status: str, broker_order_id: str, message: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "date": getattr(row, "date", ""),
        "ticket_id": getattr(row, "ticket_id", ""),
        "symbol": getattr(row, "symbol", ""),
        "side": getattr(row, "side", ""),
        "quantity": getattr(row, "quantity", ""),
        "limit_price": getattr(row, "limit_price", ""),
        "status": status,
        "broker_order_id": broker_order_id,
        "message": message,
    }


def auto_live_allowed(summary: pd.DataFrame) -> bool:
    if summary.empty or "auto_live_allowed" not in summary.columns:
        return False
    return parse_bool_like(summary["auto_live_allowed"].iloc[0])


def summary_blockers(summary: pd.DataFrame) -> str:
    if summary.empty:
        return "auto_live_summary_missing"
    column = "blocker_details" if "blocker_details" in summary.columns and str(summary["blocker_details"].iloc[0]).strip() else "blockers"
    if column not in summary.columns:
        return "auto_live_summary_missing"
    value = summary[column].iloc[0]
    return "" if pd.isna(value) else str(value)
