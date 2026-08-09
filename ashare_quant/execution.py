from __future__ import annotations

from typing import Any

import pandas as pd

from .strategy_health import strategy_health_allows_paper
from .validation import optional_dependency_status


EXECUTION_READINESS_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
EXECUTION_ROADMAP_COLUMNS = ["priority", "area", "status", "blocker", "command", "next_action", "note"]
EXECUTION_MODE_COLUMNS = ["mode", "passed", "status", "blockers", "next_action", "note"]
EXECUTION_PLAYBOOK_COLUMNS = ["route", "step", "status", "command", "destructive", "gate", "next_action", "note"]


def build_execution_readiness(
    config: dict[str, Any],
    practical_readiness: pd.DataFrame,
    live_readiness: pd.DataFrame,
    order_source: pd.DataFrame,
    account_reconciliation: pd.DataFrame,
    account_state_audit: pd.DataFrame,
    rpa_pre_trade_checks: pd.DataFrame,
    rpa_orders: pd.DataFrame,
    orders: pd.DataFrame | None = None,
    optional_dependencies: dict[str, bool] | None = None,
    strategy_decision: pd.DataFrame | None = None,
    strategy_health: pd.DataFrame | None = None,
    rpa_preflight: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Summarize whether the current outputs can move into an execution workflow."""

    optional_dependencies = optional_dependency_status() if optional_dependencies is None else optional_dependencies
    orders = pd.DataFrame() if orders is None else orders
    rpa = config.get("rpa", {})
    execution = config.get("execution", {})
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

    practical_passed, practical_value = all_checks_passed(practical_readiness)
    add(
        "practical_readiness",
        practical_passed,
        practical_value,
        "all checks passed",
        "execution_blocker",
        "运行 `python -m ashare_quant practical` 或查看 practical_readiness.csv 的未通过项。",
        "实用日常流程必须先通过，才应生成可人工核对的模拟盘订单。",
    )

    live_passed, live_value = all_checks_passed(live_readiness)
    add(
        "strict_live_readiness",
        live_passed,
        live_value,
        "all checks passed",
        "live_blocker",
        "继续冻结参数后的前向验证和 shadow 观察；严格实盘前不要自动下单。",
        "严格实盘门禁必须通过，才可把系统结论视为实盘级证据。",
    )

    if strategy_decision is not None:
        current_strategy = str(config.get("strategy", {}).get("name", ""))
        decision_passed, decision_value, decision_action = strategy_decision_gate(strategy_decision, current_strategy)
        add(
            "strategy_promotion_decision",
            decision_passed,
            decision_value,
            "can_promote=True and candidate=current strategy",
            "live_blocker",
            decision_action,
            "严格实盘执行必须先通过策略晋级决策；observe_only、paper_trade_only 或缺失决策时不得进入实盘执行。",
        )

    if strategy_health is not None:
        health_passed, health_value = strategy_health_allows_paper(strategy_health)
        add(
            "strategy_health_guard",
            health_passed,
            health_value,
            "paper_orders_allowed=True",
            "execution_blocker",
            "运行 `python -m ashare_quant strategy-health` 并处理未通过的 paper_blocker。",
            "策略健康/降级监控必须允许 paper/RPA 工单链路继续；若近期滚动、前向或 Shadow 证据确认退化，应暂停可执行工单。",
        )


    if rpa_preflight is not None:
        preflight_passed, preflight_value = blocking_preflight_checks_passed(rpa_preflight)
        add(
            "rpa_preflight",
            preflight_passed,
            preflight_value,
            "all safety/execution preflight checks passed",
            "execution_blocker",
            "运行 `python -m ashare_quant rpa-preflight`，并处理 rpa_preflight.csv 中的 safety_blocker/execution_blocker。",
            "RPA 前置审计必须确认票据、布局安全策略、预览和必要依赖满足执行要求。",
        )

    pyautogui_ok = bool(optional_dependencies.get("pyautogui", False))
    require_click_dependency = bool(execution.get("require_rpa_click_dependency", False))
    pyautogui_passed = pyautogui_ok or not require_click_dependency
    pyautogui_value = "installed" if pyautogui_ok else "missing_required" if require_click_dependency else "missing_optional_for_preview"
    add(
        "rpa_dependency_pyautogui",
        pyautogui_passed,
        pyautogui_value,
        "installed only when execution.require_rpa_click_dependency=true",
        "execution_blocker" if require_click_dependency else "click_blocker",
        "在虚拟环境中运行 `pip install -r requirements-rpa.txt` 后再使用 `rpa-fill --execute` 模拟点击填字段。",
        "导出/预览工单和 dry-run 不需要 pyautogui；只有启用点击填单执行时才需要该依赖。",
    )

    final_confirm = bool(rpa.get("require_manual_final_confirm", True))
    never_submit = bool(rpa.get("never_click_submit", True))
    add(
        "rpa_safety_policy",
        final_confirm and never_submit,
        f"manual_confirm={final_confirm}, never_submit={never_submit}",
        "manual_confirm=True and never_submit=True",
        "safety_blocker",
        "恢复 rpa.require_manual_final_confirm=true 且 rpa.never_click_submit=true。",
        "RPA 只能辅助填字段，最终提交必须人工确认。",
    )

    account_state_passed, account_state_value = all_checks_passed(account_state_audit)
    add(
        "account_state_audit",
        account_state_passed,
        account_state_value,
        "all checks passed",
        "execution_blocker",
        "运行 `python -m ashare_quant account-state`；若是模拟盘可重置账户，若是真实账户请先生成修复模板、dry-run 校验，再导入当前现金和持仓。",
        "执行前必须能用最新行情对 SQLite 账户状态、T+1 锁定数量、现金和持仓估值完成审计。",
    )

    reconciliation_passed, reconciliation_value = all_checks_passed(account_reconciliation)
    add(
        "account_reconciliation",
        reconciliation_passed,
        reconciliation_value,
        "all checks passed",
        "execution_blocker",
        "先运行 paper/practical 生成订单并完成账户、持仓、订单来源和状态库对账。",
        "可执行订单必须先完成账户与订单来源对账。",
    )

    order_exportable = check_passed(order_source, "rpa_exportable")
    order_context = check_value(order_source, "orders_file_context") or "missing"
    add(
        "order_source_verified",
        order_exportable,
        order_context,
        "paper_executable",
        "execution_blocker",
        "运行 `python -m ashare_quant paper --practical` 重新生成经过门禁的次日订单。",
        "orders.csv 必须来自通过门禁的 paper 流程，回测/研究输出不能直接导出到券商客户端。",
    )

    pre_trade_passed, pre_trade_value = all_checks_passed(rpa_pre_trade_checks)
    add(
        "rpa_pre_trade_checks",
        pre_trade_passed,
        pre_trade_value,
        "all checks passed",
        "execution_blocker",
        "运行 `python -m ashare_quant export-orders` 生成 RPA 工单并查看 rpa_pre_trade_checks.csv。",
        "RPA 填单前检查必须全部通过。",
    )

    order_count = len(orders)
    ticket_count = len(rpa_orders)
    ready_count = count_status(rpa_orders, "ready")
    blocked_count = ticket_count - ready_count if ticket_count else 0
    no_action_day = order_count == 0
    tickets_ready = no_action_day or (ticket_count > 0 and ready_count > 0 and blocked_count == 0)
    add(
        "rpa_ticket_status",
        tickets_ready,
        "no orders" if no_action_day else f"orders={order_count}, tickets={ticket_count}, ready={ready_count}, blocked={blocked_count}",
        "no orders or ready>0 and blocked=0",
        "execution_blocker",
        "先处理 blocked 工单原因；若工单缺失，运行 `python -m ashare_quant export-orders`。",
        "有订单时，RPA 工单必须存在且不能包含被风控拦截的工单。",
    )

    return pd.DataFrame(rows, columns=EXECUTION_READINESS_COLUMNS)


def strategy_decision_gate(frame: pd.DataFrame, current_strategy: str = "") -> tuple[bool, str, str]:
    if frame.empty:
        return False, "missing", "python -m ashare_quant promotion-decision"
    row = frame.iloc[0].to_dict()
    can_promote = parse_bool_like(row.get("can_promote"))
    decision = str(row.get("decision", "") or "missing")
    candidate = str(row.get("candidate_strategy_name", "") or "")
    displayed_candidate = candidate or "-"
    displayed_current = current_strategy or str(row.get("current_strategy_name", "") or "-")
    strategy_match = bool(candidate and current_strategy and candidate == current_strategy)
    passed = can_promote and strategy_match
    value = (
        f"{decision}; candidate={displayed_candidate}; current={displayed_current}; "
        f"can_promote={can_promote}; strategy_match={strategy_match}"
    )
    if can_promote and not strategy_match:
        action = "使用候选策略配置重新生成订单，然后运行 python -m ashare_quant execution-ready"
    else:
        action = str(row.get("required_action", "") or "python -m ashare_quant promotion-decision")
    return passed, value, action



def blocking_preflight_checks_passed(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or "passed" not in frame.columns:
        return False, "missing"
    data = frame.copy()
    blocking_checks = {
        "rpa_safety_policy",
        "orders_file_present",
        "rpa_ticket_file_present",
        "rpa_ticket_status",
        "pre_trade_checks_present",
        "pre_trade_checks_passed",
        "order_liquidity_audit",
        "pyautogui_dependency",
        "layout_safety_policy",
        "preview_renderable",
    }
    if "check" in data.columns:
        data = data[data["check"].astype(str).isin(blocking_checks)]
    if "severity" in frame.columns:
        severity = frame["severity"].astype(str)
        severity_blockers = frame[severity.str.contains("safety_blocker|execution_blocker", regex=True, na=False)]
        data = pd.concat([data, severity_blockers]).drop_duplicates()
    if data.empty:
        return True, "0/0 blocking checks"
    passed = data["passed"].map(parse_bool_like)
    return bool(passed.all()), f"{int(passed.sum())}/{len(data)}"

def all_checks_passed(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or "passed" not in frame:
        return False, "missing"
    passed = frame["passed"].map(parse_bool_like)
    count = int(passed.sum())
    total = int(len(frame))
    return bool(total > 0 and count == total), f"{count}/{total}"


def check_passed(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or "check" not in frame or "passed" not in frame:
        return False
    matched = frame[frame["check"].astype(str) == check]
    if matched.empty:
        return False
    return parse_bool_like(matched["passed"].iloc[0])


def check_value(frame: pd.DataFrame, check: str) -> str:
    if frame.empty or "check" not in frame or "value" not in frame:
        return ""
    matched = frame[frame["check"].astype(str) == check]
    if matched.empty:
        return ""
    value = matched["value"].iloc[0]
    return "" if pd.isna(value) else str(value)


def count_status(frame: pd.DataFrame, status: str) -> int:
    if frame.empty or "status" not in frame:
        return 0
    return int((frame["status"].astype(str).str.lower() == status).sum())


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def execution_readiness_level(frame: pd.DataFrame) -> str:
    if frame.empty or "passed" not in frame:
        return "未评估"
    failed = failed_readiness_rows(frame)
    if failed.empty:
        return "执行就绪"
    safety = rows_with_severity(failed, "safety")
    if not safety.empty:
        return f"安全阻断 {len(failed)} 项"
    execution_blockers = rows_with_severity(failed, "execution")
    live = rows_with_severity(failed, "live")
    if not execution_blockers.empty:
        suffix = f"；严格待验证 {len(live)} 项" if not live.empty else ""
        return f"人工工单阻断 {len(execution_blockers)} 项{suffix}"
    if not live.empty:
        return f"人工工单就绪；严格待验证 {len(live)} 项"
    return f"待处理 {len(failed)} 项"


def build_execution_modes(execution_readiness: pd.DataFrame) -> pd.DataFrame:
    if execution_readiness.empty or "check" not in execution_readiness:
        return pd.DataFrame(
            [
                mode_row(
                    "manual_ticket_review",
                    False,
                    "missing_evidence",
                    "execution_readiness_missing",
                    "python -m ashare_quant execution-ready",
                    "先生成执行就绪明细。",
                ),
                mode_row(
                    "rpa_click_fill",
                    False,
                    "missing_evidence",
                    "execution_readiness_missing",
                    "python -m ashare_quant execution-ready",
                    "点击填单必须先通过执行就绪明细。",
                ),
                mode_row(
                    "strict_live_execution",
                    False,
                    "missing_evidence",
                    "execution_readiness_missing",
                    "python -m ashare_quant execution-ready",
                    "严格实盘执行必须先通过全部门禁。",
                ),
            ],
            columns=EXECUTION_MODE_COLUMNS,
        )

    failed = failed_readiness_rows(execution_readiness)
    safety = rows_with_severity(failed, "safety")
    manual = pd.concat([safety, rows_with_severity(failed, "execution")]).drop_duplicates()
    live = rows_with_severity(failed, "live")
    pyautogui_value = check_value(execution_readiness, "rpa_dependency_pyautogui")
    pyautogui_installed = pyautogui_value == "installed"

    manual_passed = manual.empty
    click_passed = manual_passed and pyautogui_installed
    strict_passed = failed.empty

    rows = [
        mode_row(
            "manual_ticket_review",
            manual_passed,
            "ready" if manual_passed else "blocked",
            blocker_names(manual),
            "python -m ashare_quant rpa-preview" if manual_passed else first_next_action(manual),
            "允许导出/预览人工核对工单；不代表严格实盘策略证据。",
        ),
        mode_row(
            "rpa_click_fill",
            click_passed,
            "ready" if click_passed else "blocked",
            "" if click_passed else blocker_names(manual) or "rpa_dependency_pyautogui",
            "python -m ashare_quant rpa-fill --execute" if click_passed else first_next_action(manual) or "python -m pip install -r requirements-rpa.txt",
            "点击填单只负责填字段，最终提交仍必须人工确认。",
        ),
        mode_row(
            "strict_live_execution",
            strict_passed,
            "ready" if strict_passed else "blocked",
            blocker_names(failed),
            "python -m ashare_quant rpa-preview" if strict_passed else first_next_action(live) or first_next_action(failed),
            "严格实盘执行要求人工工单、账户、订单、风控和冻结后前向证据全部通过。",
        ),
    ]
    return pd.DataFrame(rows, columns=EXECUTION_MODE_COLUMNS)


def mode_row(mode: str, passed: bool, status: str, blockers: str, next_action: str, note: str) -> dict[str, object]:
    return {
        "mode": mode,
        "passed": bool(passed),
        "status": status,
        "blockers": blockers,
        "next_action": next_action,
        "note": note,
    }


def blocker_names(frame: pd.DataFrame) -> str:
    if frame.empty or "check" not in frame:
        return ""
    return ";".join(frame["check"].astype(str).tolist())


def first_next_action(frame: pd.DataFrame) -> str:
    if frame.empty or "next_action" not in frame:
        return ""
    values = frame["next_action"].dropna().astype(str)
    values = values[values.str.len() > 0]
    return "" if values.empty else values.iloc[0]


def failed_readiness_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "passed" not in frame:
        return frame
    return frame[~frame["passed"].map(parse_bool_like)]


def rows_with_severity(frame: pd.DataFrame, severity_token: str) -> pd.DataFrame:
    if frame.empty or "severity" not in frame:
        return pd.DataFrame(columns=frame.columns)
    return frame[frame["severity"].astype(str).str.contains(severity_token, na=False)]


def build_execution_roadmap(execution_readiness: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(priority: int, area: str, status: str, blocker: str, command: str, next_action: str, note: str) -> None:
        rows.append(
            {
                "priority": priority,
                "area": area,
                "status": status,
                "blocker": blocker,
                "command": command,
                "next_action": next_action,
                "note": note,
            }
        )

    if execution_readiness.empty or "check" not in execution_readiness:
        add(
            1,
            "证据生成",
            "action_required",
            "execution_readiness_missing",
            "python -m ashare_quant execution-ready",
            "先生成执行就绪检查。",
            "该命令只读取和刷新诊断 CSV，不会重置账户、生成订单或点击券商客户端。",
        )
        return pd.DataFrame(rows, columns=EXECUTION_ROADMAP_COLUMNS)

    if check_failed(execution_readiness, "rpa_safety_policy"):
        add(
            1,
            "安全配置",
            "blocked",
            "rpa_safety_policy",
            "编辑 configs/default.yaml",
            "恢复 require_manual_final_confirm=true 且 never_click_submit=true。",
            "系统不应自动点击最终提交按钮；这是一条硬安全边界。",
        )

    if check_failed(execution_readiness, "account_state_audit"):
        add(
            2,
            "模拟账户路径",
            "action_required",
            "account_state_audit",
            "python -m ashare_quant account-reset --confirm",
            "如果只做本地模拟盘，重置为现金账户后再运行 practical。",
            "该命令会清空本地 SQLite 模拟持仓、订单和成交；只适用于模拟盘。",
        )
        add(
            2,
            "真实账户路径",
            "action_required",
            "account_state_audit",
            "python -m ashare_quant account-repair-template",
            "如果要贴近真实账户，先生成预填审阅模板，核对当前现金和持仓后运行 account-import-check。",
            "修复模板会标记缺价持仓；导入真实账户后，系统才能做持仓估值、T+1 锁定和订单对账。",
        )
        add(
            3,
            "真实账户导入校验",
            "action_required",
            "account_import_check",
            "python -m ashare_quant account-import-check --file outputs/account_import_from_state_template.csv",
            "导入前先 dry-run 校验；全部通过后才运行 account-import --confirm。",
            "该命令只写校验 CSV，不覆盖 SQLite 账户状态。",
        )

    if check_failed(execution_readiness, "practical_readiness"):
        add(
            3,
            "实用门禁",
            "action_required",
            "practical_readiness",
            "python -m ashare_quant ready",
            "刷新实用门禁；若未通过，先处理 practical_readiness.csv 中的失败项。",
            "实用门禁通过只表示可以模拟运行和人工核对，不代表严格实盘级。",
        )

    if check_failed(execution_readiness, "strategy_health_guard"):
        add(
            4,
            "策略健康门禁",
            "action_required",
            "strategy_health_guard",
            "python -m ashare_quant strategy-health",
            "先处理 strategy_health.csv 中的 paper_blocker，再生成或导出可执行工单。",
            "该门禁用于在策略近期明显退化时自动降级，避免把失效策略继续推向 RPA 或人工工单。",
        )

    if any(check_failed(execution_readiness, check) for check in ["account_reconciliation", "order_source_verified"]):
        add(
            4,
            "订单生成",
            "action_required",
            "paper_order_source",
            "python -m ashare_quant practical",
            "用 practical 流程生成经过门禁的 paper 订单，并完成账户/订单对账。",
            "回测或研究订单不能直接导出到券商客户端。",
        )

    if check_failed(execution_readiness, "rpa_dependency_pyautogui"):
        add(
            5,
            "RPA 依赖",
            "optional_for_dry_run",
            "rpa_dependency_pyautogui",
            "python -m pip install -r requirements-rpa.txt",
            "只有需要实际模拟点击填字段时才安装；预览工单和 dry-run 不需要。",
            "安装依赖会修改本地虚拟环境；如暂不做点击填单，可以先跳过。",
        )

    if any(check_failed(execution_readiness, check) for check in ["rpa_pre_trade_checks", "rpa_ticket_status"]):
        add(
            6,
            "RPA 工单",
            "action_required",
            "rpa_pre_trade_checks",
            "python -m ashare_quant export-orders",
            "导出 RPA 工单，然后运行 rpa-preview 检查 ready/blocked 数量。",
            "只有 paper 来源、账户对账和风控检查都通过的订单才应进入 RPA 工单。",
        )

    if check_failed(execution_readiness, "strict_live_readiness"):
        add(
            7,
            "严格实盘验证",
            "collecting_evidence",
            "strict_live_readiness",
            "python -m ashare_quant observe --quiet",
            "继续冻结参数后的前向验证和 shadow 观察，直到严格实盘门禁通过。",
            "这是防止过拟合的核心证据；未通过前只适合模拟盘或人工观察。",
        )

    if not rows:
        add(
            1,
            "执行",
            "passed",
            "none",
            "python -m ashare_quant rpa-preview",
            "执行就绪检查已通过，下一步仍需人工核对每一笔工单。",
            "系统不会点击最终提交；实际下单前必须人工确认账户、价格、数量和风险。",
        )
    return pd.DataFrame(rows, columns=EXECUTION_ROADMAP_COLUMNS)


def build_execution_playbook(execution_readiness: pd.DataFrame, account_paths: pd.DataFrame | None = None) -> pd.DataFrame:
    account_paths = pd.DataFrame() if account_paths is None else account_paths
    rows: list[dict[str, object]] = []

    def add(route: str, step: int, status: str, command: str, destructive: str, gate: str, next_action: str, note: str) -> None:
        rows.append(
            {
                "route": route,
                "step": int(step),
                "status": status,
                "command": command,
                "destructive": destructive,
                "gate": gate,
                "next_action": next_action,
                "note": note,
            }
        )

    if execution_readiness.empty or "check" not in execution_readiness:
        add(
            "状态生成",
            1,
            "action_required",
            "python -m ashare_quant execution-ready",
            "no",
            "execution_readiness_missing",
            "先生成执行准备状态，再选择模拟盘、真实账户或严格实盘路径。",
            "该命令只刷新诊断 CSV，不会重置账户、生成订单或点击券商客户端。",
        )
        return pd.DataFrame(rows, columns=EXECUTION_PLAYBOOK_COLUMNS)

    failed = failed_readiness_rows(execution_readiness)
    safety = rows_with_severity(failed, "safety")
    execution_blockers = rows_with_severity(failed, "execution")
    live_blockers = rows_with_severity(failed, "live")
    manual_ready = safety.empty and execution_blockers.empty
    strict_ready = failed.empty
    account_blocked = check_failed(execution_readiness, "account_state_audit")
    order_chain_blocked = any(
        check_failed(execution_readiness, check)
        for check in ["strategy_health_guard", "account_reconciliation", "order_source_verified", "rpa_pre_trade_checks", "rpa_ticket_status"]
    )

    if not safety.empty:
        simulation_status = "blocked_safety"
        simulation_action = first_next_action(safety)
    elif manual_ready:
        simulation_status = "ready"
        simulation_action = "运行 rpa-preview 做人工工单复核；仍不代表严格实盘策略证据通过。"
    elif account_blocked:
        simulation_status = "available_with_confirmation"
        simulation_action = "仅在确认可清空本地 SQLite 模拟账户时运行；运行后刷新 execution-ready 和 dashboard。"
    else:
        simulation_status = "action_required"
        simulation_action = first_next_action(execution_blockers) or "先处理执行门禁失败项。"

    add(
        "模拟盘闭环",
        1,
        simulation_status,
        "python -m ashare_quant practical --reset-account --confirm-account-reset",
        "clears_local_paper_account",
        "practical_guard + paper provenance + account reconciliation",
        simulation_action,
        "最快跑通本地模拟账户、paper 订单、账户对账和 RPA 工单；不保留当前 SQLite 持仓/成交。",
    )
    add(
        "模拟盘闭环",
        2,
        "ready" if manual_ready else "waiting_for_verified_orders",
        "python -m ashare_quant rpa-preview",
        "no",
        "manual_ticket_review",
        "确认 ready/blocked 工单数量；任何 blocked 都不应填入券商客户端。" if manual_ready else "先完成 practical 流程并通过账户/订单/RPA 前检查。",
        "预览只读，不点击券商客户端。",
    )

    current_status = account_path_status(account_paths, "current_account_state")
    full_status = account_path_status(account_paths, "real_account_full_import")
    priced_status = account_path_status(account_paths, "real_account_priced_only_import")
    missing_prices = full_status in {"blocked_missing_prices", "missing_template"}
    real_base_status = "passed" if current_status == "ready" else "action_required"
    add(
        "真实账户闭环",
        1,
        real_base_status,
        "python -m ashare_quant account-repair-template",
        "no",
        "account_state_audit",
        "生成并人工核对真实现金、持仓、可卖数量和缺价证券。" if real_base_status != "passed" else "当前账户审计已通过，可继续 practical。",
        "该步骤只生成模板和审阅 CSV，不覆盖 SQLite 账户状态。",
    )
    add(
        "真实账户闭环",
        2,
        "action_required" if missing_prices else "optional",
        "python -m ashare_quant account-price-template",
        "no",
        "missing_position_prices",
        "为缺价持仓生成补价模板；也可以刷新行情或在导入模板中剔除无价证券。" if missing_prices else "当前完整导入未显示缺价阻断；可跳过。",
        "手工补价只用于账户估值和导入校验，不参与策略回测。",
    )
    add(
        "真实账户闭环",
        3,
        "action_required" if missing_prices else "optional",
        "python -m ashare_quant account-price-check",
        "no",
        "price_override_hash_binding",
        "填写 account_price_overrides.csv 后先做哈希绑定校验；通过后重新运行 account-state。" if missing_prices else "没有补价文件时可跳过。",
        "系统只会使用通过校验且哈希匹配的补价文件。",
    )
    add(
        "真实账户闭环",
        4,
        "action_required" if full_status != "precheck_passed" else "passed",
        "python -m ashare_quant account-import-check --file outputs/account_import_from_state_template.csv",
        "no",
        "account_import_precheck",
        "先 dry-run 校验完整真实账户模板；全部通过后才允许确认导入。",
        f"当前完整导入路径状态：{full_status or 'unknown'}；可估值持仓候选路径状态：{priced_status or 'unknown'}。",
    )
    add(
        "真实账户闭环",
        5,
        "blocked_until_precheck" if full_status != "precheck_passed" else "available_with_confirmation",
        "python -m ashare_quant account-import --file outputs/account_import_from_state_template.csv --confirm",
        "overwrites_local_account_state",
        "latest_import_precheck_same_hash",
        "只有最近一次 account-import-check 对同一文件哈希全部通过后才执行。",
        "确认导入会覆盖本地 SQLite 账户状态并清除旧订单/RPA 工单。",
    )

    evidence_status = "passed" if live_blockers.empty else "collecting_evidence"
    add(
        "严格实盘观察",
        1,
        evidence_status,
        "python -m ashare_quant observe-candidates --quiet",
        "no",
        "anti_overfit_forward_shadow",
        "继续冻结参数后的前向验证、Shadow 观察和候选策略晋级。" if not live_blockers.empty else "严格证据门禁已通过；仍需执行门禁通过。",
        "这是防止过拟合的核心路径，不生成可执行订单。",
    )
    add(
        "严格实盘观察",
        2,
        "ready" if strict_ready else "blocked",
        "python -m ashare_quant execution-ready",
        "no",
        "strict_live_readiness + strategy_promotion_decision",
        "严格实盘前必须让所有 execution_readiness 检查通过。" if not strict_ready else "全部门禁通过后仍需人工核对每笔工单。",
        blocker_names(failed) if not strict_ready else "无阻断项。",
    )
    return pd.DataFrame(rows, columns=EXECUTION_PLAYBOOK_COLUMNS)


def account_path_status(account_paths: pd.DataFrame, path: str) -> str:
    if account_paths.empty or not {"path", "status"}.issubset(account_paths.columns):
        return ""
    matched = account_paths[account_paths["path"].astype(str) == path]
    if matched.empty:
        return ""
    return "" if pd.isna(matched["status"].iloc[0]) else str(matched["status"].iloc[0])


def check_failed(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or "check" not in frame or "passed" not in frame:
        return True
    matched = frame[frame["check"].astype(str) == check]
    if matched.empty:
        return True
    return not parse_bool_like(matched["passed"].iloc[0])
