from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .io_utils import atomic_to_csv


AUTO_LIVE_REMEDIATION_COLUMNS = [
    "priority",
    "area",
    "blocker",
    "status",
    "command",
    "requires_confirmation",
    "safe_default",
    "next_action",
    "note",
]

AUTO_LIVE_VERIFICATION_COLUMNS = [
    "step",
    "area",
    "command",
    "required_before",
    "stop_on_failure",
    "expected_output",
    "success_condition",
    "note",
]


def write_auto_live_remediation_plan(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_remediation_plan.csv"
    atomic_to_csv(build_auto_live_remediation_plan(config), path, index=False, encoding="utf-8-sig")
    return path


def write_auto_live_verification_plan(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_verification_plan.csv"
    atomic_to_csv(build_auto_live_verification_plan(config), path, index=False, encoding="utf-8-sig")
    return path


def build_auto_live_verification_plan(config: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(
        step: int,
        area: str,
        command: str,
        required_before: str,
        stop_on_failure: bool,
        expected_output: str,
        success_condition: str,
        note: str,
    ) -> None:
        rows.append(
            {
                "step": step,
                "area": area,
                "command": command,
                "required_before": required_before,
                "stop_on_failure": bool(stop_on_failure),
                "expected_output": expected_output,
                "success_condition": success_condition,
                "note": note,
            }
        )

    add(
        1,
        "account_audit",
        "python -m ashare_quant account-state",
        "账户导入、补价、重置或成交导入之后",
        True,
        "outputs/account_state_audit.csv; outputs/account_state_missing_prices.csv",
        "account_state_audit 全部通过，且 account_state_missing_prices.csv 为空",
        "这是后续 RPA、执行门禁和自动实盘门禁的账户证据来源。",
    )
    add(
        2,
        "rpa_pre_trade",
        "python -m ashare_quant export-orders",
        "account_state_audit 已通过，且 orders.csv 来自 paper/practical 可执行订单",
        True,
        "outputs/rpa_orders.csv; outputs/rpa_pre_trade_checks.csv",
        "rpa_pre_trade_checks 全部通过，且 rpa_orders.csv 中无 blocked 工单",
        "该命令只导出本地工单，不点击券商软件。",
    )
    add(
        3,
        "rpa_preflight",
        "python -m ashare_quant rpa-preflight",
        "RPA 工单已重新导出",
        True,
        "outputs/rpa_preflight.csv",
        "所有 safety_blocker 和 execution_blocker 通过；click_blocker 只影响模拟点击填字段",
        "这是 RPA 环境、票据和布局安全的汇总审计。",
    )
    add(
        4,
        "order_capacity",
        "python -m ashare_quant order-audit",
        "RPA preflight 阻断项已通过",
        True,
        "outputs/order_liquidity_audit.csv; outputs/order_liquidity_all_audit.csv",
        "所有待执行工单的流动性和容量检查通过",
        "用于防止成交额占比过高、数量异常或流动性不足的订单进入执行链。",
    )
    add(
        5,
        "execution_gate",
        "python -m ashare_quant execution-ready",
        "账户、RPA 工单、订单容量和策略证据已刷新",
        True,
        "outputs/execution_readiness.csv; outputs/execution_modes.csv; outputs/execution_playbook.csv",
        "execution_readiness 全部关键检查通过，且严格实盘模式不再阻断",
        "这是自动实盘前的总执行门禁。",
    )
    add(
        6,
        "auto_live_adapter_selftest",
        "python -m ashare_quant auto-live-adapter-selftest",
        "execution-ready 已通过，且 command 适配器命令已配置",
        True,
        "outputs/auto_live_adapter_selftest.csv",
        "self-test 全部通过，capabilities 包含 submit_orders/query_orders/cancel_orders/account_snapshot，且配置指纹匹配",
        "该步骤不发送订单，只验证外部券商桥接程序契约。",
    )
    add(
        7,
        "auto_live_account_snapshot",
        "python -m ashare_quant auto-live-account",
        "适配器 self-test 已通过，且本地 account_state 已是最新真实账户状态",
        True,
        "outputs/auto_live_broker_account_snapshot.csv; outputs/auto_live_broker_positions.csv; outputs/auto_live_broker_account_audit.csv",
        "券商账户快照审计全部通过，environment=live、配置指纹匹配、快照未超过有效期，且现金、总资产和持仓数量与本地账户状态一致",
        "只读券商账户，不提交订单；这是自动实盘前必须完成的券商端对账。",
    )
    add(
        8,
        "auto_live_gate",
        "python -m ashare_quant auto-live-check",
        "execution-ready、适配器 self-test 和券商账户快照审计均已通过",
        True,
        "outputs/auto_live_summary.csv; outputs/auto_live_blocker_details.csv; outputs/auto_live_adapter_audit.csv",
        "auto_live_allowed=True，且无 execution/evidence/adapter/account 阻断项",
        "只写自动实盘门禁审计，不提交订单。",
    )
    add(
        9,
        "auto_live_submit_window",
        "python -m ashare_quant auto-live-window",
        "auto_live_allowed=True，且 RPA 工单已重新导出",
        True,
        "outputs/auto_live_window_audit.csv",
        "提交时间窗、交易日、工单日期和 ready 状态均通过；正式提交不得依赖绕过项",
        "该步骤只审计当前时钟和工单时效，不提交券商订单。",
    )
    add(
        10,
        "auto_live_risk",
        "python -m ashare_quant auto-live-risk",
        "提交时间窗审计已通过，且当前工单和券商账户快照未变化",
        True,
        "outputs/auto_live_risk_audit.csv; outputs/auto_live_risk_reservations.csv",
        "独立风险审计全部通过，emergency_stop_inactive=True，单笔、单批、单日累计额度和现金缓冲均合格",
        "该步骤只审计，不提交订单；当日状态不明批次仍占用风险额度。",
    )
    add(
        11,
        "auto_live_submit_lock",
        "python -m ashare_quant auto-live-lock",
        "提交时间窗审计已通过，且正式提交前没有未处置的在途批次",
        True,
        "outputs/auto_live_submit_lock_audit.csv",
        "submit_lock_clear=True；若存在过期锁，必须先按 ticket_id 查询券商端并保留恢复证据",
        "该步骤只读取锁、提交意图、请求、结果和台账，不会自动删除锁或提交订单。",
    )
    add(
        12,
        "auto_live_dry_run",
        "python -m ashare_quant run --allow-stale --skip-stress --skip-shadow --auto-live-dry-run",
        "auto_live_allowed=True，且真实券商适配器完成配置审计",
        True,
        "outputs/auto_live_run.csv; outputs/auto_live_order_results.csv",
        "dry-run 订单数量、票据指纹、配置指纹和预期提交结果全部可复核",
        "该一键 dry-run 使用 raise_on_blocked=False；门禁失败时只记录阻断，不会提交券商订单。",
    )
    add(
        13,
        "auto_live_submit",
        "python -m ashare_quant auto-live-run",
        "auto-live dry-run 已通过，订单、账户、适配器和券商端状态已人工复核，且最终提交开关已手动开启",
        True,
        "outputs/auto_live_run.csv; outputs/auto_live_order_results.csv; outputs/auto_live_ack_audit.csv",
        "auto_live_run status=submitted，且 auto_live_ack_audit 全部通过并返回唯一 broker_order_id",
        "这是唯一可能提交真实券商订单的步骤；不应放入无人值守一键链路。",
    )
    add(
        14,
        "auto_live_query",
        "python -m ashare_quant auto-live-query",
        "真实 auto-live-run 已返回 broker_order_id",
        True,
        "outputs/auto_live_order_status.csv; outputs/auto_live_order_status_audit.csv; outputs/auto_live_submission_recovery.csv",
        "auto_live_order_status_audit 全部通过，且不存在 unknown/failed/rejected 状态；submission_uncertain 订单已找回唯一 broker_order_id",
        "查询委托状态是成交同步、撤单和异常恢复的前置证据。",
    )
    add(
        15,
        "auto_live_fills",
        "python -m ashare_quant auto-live-fills",
        "auto-live-query 已通过",
        True,
        "outputs/auto_live_fill_import.csv; outputs/auto_live_fill_progress.csv; outputs/auto_live_fill_sync_audit.csv",
        "成交导入候选只包含 filled/partially_filled 且数量、价格、费用字段有效的回报",
        "该步骤只生成候选 CSV，不写入账户状态。",
    )
    add(
        16,
        "auto_live_cancel",
        "python -m ashare_quant auto-live-cancel",
        "auto-live-query 已通过；若仍有 accepted/submitted/partially_filled 委托则需要撤单",
        True,
        "outputs/auto_live_cancel_results.csv; outputs/auto_live_cancel_audit.csv",
        "auto_live_cancel_audit 全部通过；无可撤委托时应明确记录 no_cancelable_orders",
        "用于收尾未成交或部分成交剩余委托，防止异常恢复后遗留在途订单。",
    )
    add(
        17,
        "auto_live_settle",
        "python -m ashare_quant auto-live-settle",
        "查询、成交候选和必要撤单均已完成",
        True,
        "outputs/auto_live_settlement_audit.csv; outputs/auto_live_fill_import.csv",
        "settlement audit 全部通过；默认不导入账户，只有 --confirm-import 且 allow_account_sync=true 才写账户",
        "该步骤用于复核成交同步闭环，正式账户写入仍需单独确认。",
    )
    add(
        18,
        "reporting",
        "python -m ashare_quant report; python -m ashare_quant dashboard",
        "上述验证链全部完成后",
        False,
        "outputs/report.html; outputs/control_panel.html",
        "报告和控制面板展示最新账户、RPA、执行和自动实盘证据",
        "如果不想使用命令分隔符，可分别执行 report 与 dashboard 两条命令。",
    )
    return pd.DataFrame(rows, columns=AUTO_LIVE_VERIFICATION_COLUMNS)


def build_auto_live_remediation_plan(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    details = read_csv(output_dir / "auto_live_blocker_details.csv")
    evidence = read_csv(output_dir / "evidence_plan.csv")
    account_paths = read_csv(output_dir / "account_path_decision.csv")
    excluded = read_csv(output_dir / "account_repair_excluded_positions.csv", dtype={"symbol": str})
    rows: list[dict[str, object]] = []

    def add(
        priority: int,
        area: str,
        blocker: str,
        status: str,
        command: str,
        requires_confirmation: bool,
        safe_default: bool,
        next_action: str,
        note: str,
    ) -> None:
        key = (area, blocker, command)
        if any((row["area"], row["blocker"], row["command"]) == key for row in rows):
            return
        rows.append(
            {
                "priority": priority,
                "area": area,
                "blocker": blocker,
                "status": status,
                "command": command,
                "requires_confirmation": bool(requires_confirmation),
                "safe_default": bool(safe_default),
                "next_action": next_action,
                "note": note,
            }
        )

    if details.empty:
        add(
            1,
            "auto_live_audit",
            "auto_live_blocker_details_missing",
            "audit_required",
            "python -m ashare_quant auto-live-check",
            False,
            True,
            "先刷新自动实盘门禁和阻断明细，再决定修复路径。",
            "该命令只写审计 CSV，不会提交订单。",
        )
        return pd.DataFrame(rows, columns=AUTO_LIVE_REMEDIATION_COLUMNS)

    account_blocked = has_check(details, "account_state_audit") or evidence_area_blocked(evidence, "account_audit")
    if account_blocked:
        priced = account_path(account_paths, "real_account_priced_only_import")
        excluded_symbols = unique_symbols(excluded)
        excluded_note = ",".join(excluded_symbols) if excluded_symbols else "无"
        priced_status = str(priced.get("status", ""))
        if priced_status.startswith("precheck_passed"):
            add(
                1,
                "account_audit",
                "missing_position_prices",
                "manual_confirmation_required",
                "python -m ashare_quant account-import --file outputs/account_import_priced_only_template.csv --confirm --ack-exclusions",
                True,
                False,
                "人工核对 outputs/account_repair_excluded_positions.csv；确认排除缺价持仓后才执行导入。",
                f"priced-only 模板最近 dry-run 已通过；当前排除证券：{excluded_note}。真实账户使用前必须确认这些证券确实不应计入本地账户状态。",
            )
        else:
            add(
                1,
                "account_audit",
                "missing_position_prices",
                "repair_template_required",
                "python -m ashare_quant account-repair-template",
                False,
                True,
                "生成账户修复模板，先处理缺价持仓或重新导入真实账户现金和持仓。",
                "该命令只生成模板，不覆盖本地账户。",
            )
        add(
            2,
            "account_audit",
            "simulation_account_reset",
            "optional_simulation_only",
            "python -m ashare_quant account-reset --confirm",
            True,
            False,
            "如果这只是本地模拟盘，可重置为现金账户后重新跑 practical；真实账户不要用此路径。",
            "该命令会清空本地 SQLite 持仓、订单、成交和可执行工单。",
        )
        add(
            3,
            "account_audit",
            "account_state_recheck",
            "required_after_account_change",
            "python -m ashare_quant account-state",
            False,
            True,
            "账户导入、补价或重置后重新生成账户审计。",
            "只有 account_state_audit 全部通过，RPA 前置检查才可能通过。",
        )
        add(
            4,
            "account_audit",
            "exclusion_impact_review",
            "review_required_when_exclusions_exist",
            "python -m ashare_quant account-exclusion-audit",
            False,
            True,
            "查看缺价持仓排除对账户权益的成本口径影响，再决定补价、修正模板或人工确认排除。",
            "该命令只生成 account_exclusion_impact.csv 和明细，不修改账户状态。",
        )

    if has_check(details, "rpa_preflight"):
        add(
            9,
            "rpa_pre_trade",
            "rpa_preflight",
            "preflight_failed",
            "python -m ashare_quant rpa-preflight",
            False,
            True,
            "查看 outputs/rpa_preflight.csv，先处理 execution_blocker 和 safety_blocker。",
            "RPA 前置环境审计会检查票据状态、blocked 工单、预检查、容量、布局安全和预览可用性。",
        )

    if has_check(details, "rpa_pre_trade_checks") or evidence_area_blocked(evidence, "rpa_pre_trade"):
        add(
            10,
            "rpa_pre_trade",
            "account_state_audit",
            "waiting_on_account_audit",
            "python -m ashare_quant export-orders",
            False,
            True,
            "账户审计通过后重新导出 RPA 工单和前置检查。",
            "当前 RPA 阻断主要由账户缺价传导；不要在账户审计失败时执行填单。",
        )

    if evidence_area_blocked(evidence, "forward_validation") or has_check(details, "forward_validation"):
        add(
            20,
            "forward_validation",
            "candidate_forward_trading_days",
            "collecting_market_days",
            "python -m ashare_quant observe-candidates --quiet",
            False,
            True,
            "继续累计冻结候选参数后的新增交易日。",
            evidence_note(evidence, "forward_validation") or "前向验证需要真实新增交易日，不能用历史回填替代。",
        )

    if evidence_area_blocked(evidence, "shadow_observation") or has_check(details, "shadow_observation"):
        add(
            21,
            "shadow_observation",
            "shadow_observed_signal_count",
            "collecting_shadow_samples",
            "python -m ashare_quant run --allow-stale",
            False,
            True,
            "每日一键运行，记录当前候选参数的 Shadow 信号并刷新审计。",
            evidence_note(evidence, "shadow_observation") or "只有当前候选参数/当前策略哈希的可评估 Shadow 样本会计入门禁。",
        )

    if has_check(details, "adapter_name_present") or has_check(details, "adapter_supported") or has_check(details, "adapter_submit_capability"):
        add(
            30,
            "broker_adapter",
            "adapter_missing_or_unsupported",
            "config_required_after_gates_pass",
            "manual_config: auto_live.broker_adapter",
            True,
            False,
            "只有账户、RPA、前向和 Shadow 门禁通过后，才配置真实券商适配器。",
            "当前系统只登记 mock 演练适配器；真实券商适配器需要单独实现和审计。",
        )

    if has_check(details, "submit_switch_explicit") or has_check(details, "broker_submit_explicitly_allowed"):
        add(
            31,
            "broker_submit",
            "submit_switch_disabled",
            "final_manual_enable_required",
            "manual_config: auto_live.allow_broker_submit=true",
            True,
            False,
            "所有实盘门禁通过且适配器审计完成后，才显式开启券商提交开关。",
            "该开关是最后一道提交确认，不能由一键运行自动开启。",
        )

    if not rows:
        add(
            1,
            "auto_live",
            "none",
            "ready_for_dry_run",
            "python -m ashare_quant auto-live-run --dry-run",
            False,
            True,
            "自动实盘门禁未发现阻断；先 dry-run 复核订单提交结果。",
            "dry-run 不会提交券商订单。",
        )
    result = pd.DataFrame(rows, columns=AUTO_LIVE_REMEDIATION_COLUMNS)
    return result.sort_values(["priority", "area", "blocker"]).reset_index(drop=True)


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except EmptyDataError:
        return pd.DataFrame()


def has_check(details: pd.DataFrame, check: str) -> bool:
    return not details.empty and "check" in details.columns and bool((details["check"].astype(str) == check).any())


def evidence_area_blocked(evidence: pd.DataFrame, area: str) -> bool:
    if evidence.empty or not {"area", "status"}.issubset(evidence.columns):
        return False
    rows = evidence[evidence["area"].astype(str) == area]
    return not rows.empty and str(rows["status"].iloc[0]) != "passed"


def evidence_note(evidence: pd.DataFrame, area: str) -> str:
    if evidence.empty or not {"area", "next_action"}.issubset(evidence.columns):
        return ""
    rows = evidence[evidence["area"].astype(str) == area]
    if rows.empty:
        return ""
    value = rows["next_action"].iloc[0]
    return "" if pd.isna(value) else str(value)


def account_path(account_paths: pd.DataFrame, path_name: str) -> dict[str, object]:
    if account_paths.empty or "path" not in account_paths.columns:
        return {}
    rows = account_paths[account_paths["path"].astype(str) == path_name]
    return {} if rows.empty else rows.iloc[0].to_dict()


def unique_symbols(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "symbol" not in frame.columns:
        return []
    return sorted({str(value).zfill(6) for value in frame["symbol"].dropna().astype(str)})
