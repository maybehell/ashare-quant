from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .account import (
    build_import_positions_snapshot,
    file_sha256,
    normalize_account_import,
    read_account_import_csv,
    resolve_import_pricing,
    validate_import_positions,
)
from .config import project_path
from .data import DataStore
from .io_utils import atomic_to_csv


ACCOUNT_READINESS_PLAN_COLUMNS = [
    "priority",
    "path",
    "status",
    "blocker",
    "command",
    "next_action",
    "note",
]

ACCOUNT_PATH_DECISION_COLUMNS = [
    "path",
    "available",
    "status",
    "blockers",
    "command",
    "next_action",
    "note",
]


def build_account_readiness_plan(
    config: dict[str, Any],
    account_state_audit: pd.DataFrame | None = None,
    missing_prices: pd.DataFrame | None = None,
    account_snapshot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    audit = read_csv(output_dir / "account_state_audit.csv") if account_state_audit is None else account_state_audit
    missing = read_csv(output_dir / "account_state_missing_prices.csv") if missing_prices is None else missing_prices
    snapshot = read_csv(output_dir / "account_state_snapshot.csv") if account_snapshot is None else account_snapshot
    rows: list[dict[str, object]] = []

    def add(
        priority: int,
        path: str,
        status: str,
        blocker: str,
        command: str,
        next_action: str,
        note: str,
    ) -> None:
        rows.append(
            {
                "priority": priority,
                "path": path,
                "status": status,
                "blocker": blocker,
                "command": command,
                "next_action": next_action,
                "note": note,
            }
        )

    if audit.empty or "passed" not in audit.columns:
        add(
            1,
            "账户状态检查",
            "action_required",
            "account_state_audit_missing",
            "python -m ashare_quant account-state",
            "先导出当前 SQLite 账户状态，生成估值、缺价持仓和审计结果。",
            "该命令只读取和标记本地账户，不会重置账户、生成订单或点击券商客户端。",
        )
        add(
            2,
            "真实账户路径",
            "optional",
            "account_state_unknown",
            "python -m ashare_quant account-template",
            "如果要贴近真实账户，先生成模板，填入现金和持仓后再执行 account-import。",
            "真实账户导入会覆盖本地 SQLite 账户状态，执行前必须人工核对模板。",
        )
        return pd.DataFrame(rows, columns=ACCOUNT_READINESS_PLAN_COLUMNS)

    passed = audit["passed"].map(parse_bool_like)
    failed = audit.loc[~passed].copy()
    missing_count = int(len(missing)) if not missing.empty else 0
    missing_symbol_count = count_unique_symbols(missing)
    missing_without_price_cache = count_false_by_symbol(missing, "symbol_in_price_cache")
    missing_without_stock_cache = count_false_by_symbol(missing, "symbol_in_stock_cache")
    position_count = snapshot_value(snapshot, "position_count")
    cash = snapshot_value(snapshot, "cash")
    marked_equity = snapshot_value(snapshot, "marked_equity")

    if failed.empty:
        add(
            1,
            "账户状态",
            "passed",
            "none",
            "python -m ashare_quant practical",
            "账户估值和本地状态审计已通过，可以进入实用模拟盘订单生成。",
            f"当前持仓数={position_count or '-'}，现金={cash or '-'}，估值权益={marked_equity or '-'}。",
        )
        add(
            2,
            "订单与成交闭环",
            "next_step",
            "fills_not_imported",
            "python -m ashare_quant fill-template",
            "真实或手工成交后，用成交模板导入回报，保持现金、持仓和 T+1 锁定状态同步。",
            "成交回写是连续运行的关键；否则下一日订单会基于过期账户状态。",
        )
        return pd.DataFrame(rows, columns=ACCOUNT_READINESS_PLAN_COLUMNS)

    failed_checks = ", ".join(str(value) for value in failed["check"].head(5)) if "check" in failed else "account_state_audit"
    add(
        1,
        "账户状态",
        "blocked",
        failed_checks,
        "python -m ashare_quant account-state",
        "先查看 account_state_audit.csv 和 account_state_missing_prices.csv，确认失败原因。",
        f"当前账户审计未通过：{int(passed.sum())}/{len(audit)} 项通过。",
    )
    add(
        2,
        "模拟账户路径",
        "action_required",
        "account_state_audit_failed",
        "python -m ashare_quant account-reset --confirm",
        "如果只做本地模拟盘，重置为现金账户，然后运行 python -m ashare_quant practical。",
        "该命令会清空本地 SQLite 模拟持仓、订单、成交和可执行工单；不适用于保留真实持仓。",
    )
    add(
        2,
        "真实账户路径",
        "action_required",
        "account_state_audit_failed",
        "python -m ashare_quant account-repair-template",
        "先生成预填审阅模板，核对真实现金和持仓，再运行 account-import-check；全部通过后才运行 account-import --confirm。",
        "修复模板会标记缺价持仓；真实账户导入后，系统才能做估值、卖出可用数量、T+1 锁定和订单对账。",
    )
    if missing_count:
        coverage_note = (
            f"当前缺价记录 {missing_count} 行，涉及 {missing_symbol_count} 只股票；"
            f"其中 {missing_without_price_cache} 只不在价格缓存，{missing_without_stock_cache} 只不在股票列表。"
        )
        price_audit = read_csv(output_dir / "account_price_override_audit.csv")
        price_effective = read_csv(output_dir / "account_price_override_effective.csv")
        override_file = output_dir / "account_price_overrides.csv"
        price_check_passed = not price_audit.empty and "passed" in price_audit.columns and bool(price_audit["passed"].map(parse_bool_like).all())
        effective_count = int(len(price_effective)) if not price_effective.empty else 0
        if price_check_passed:
            price_check_status = "passed"
            price_check_blocker = "none"
            price_check_action = "补价文件校验已通过；重新运行 account-state，让补价进入账户估值审计。"
            price_check_note = f"当前生效补价 {effective_count} 条；补价文件哈希已由 account-price-check 绑定。"
        elif override_file.exists():
            price_check_status = "precheck_required"
            price_check_blocker = "price_override_check_missing_or_failed"
            price_check_action = "补价文件存在但未通过最新校验；先运行 account-price-check，全部通过后再 account-state。"
            price_check_note = "系统只会使用通过校验且哈希匹配的 account_price_overrides.csv。"
        else:
            price_check_status = "template_required"
            price_check_blocker = "price_override_file_missing"
            price_check_action = "先生成补价模板，人工填写来源和价格后另存为 outputs/account_price_overrides.csv，再运行 account-price-check。"
            price_check_note = "手工补价只用于账户估值和导入校验，不参与策略回测。"
        add(
            3,
            "缺价持仓处理",
            "review_required",
            "missing_position_prices",
            "python -m ashare_quant account-price-template",
            "先生成缺价补价模板，人工核对价格来源；也可刷新缓存补齐缺价，或在真实账户导入中剔除无价证券。",
            f"{coverage_note} 明细见 outputs/account_state_missing_prices.csv。",
        )
        add(
            4,
            "补价文件校验",
            price_check_status,
            price_check_blocker,
            "python -m ashare_quant account-price-check",
            price_check_action,
            price_check_note,
        )
        add(
            5,
            "可估值候选导入",
            "optional",
            "priced_only_template",
            "python -m ashare_quant account-import-check --file outputs/account_import_priced_only_template.csv",
            "如果只想让本地系统先跑通账户闭环，可 dry-run 校验可估值持仓模板；导入前必须重新校验该目标文件。",
            "该模板会排除缺价持仓，排除明细见 outputs/account_repair_excluded_positions.csv；真实账户使用前必须人工确认。",
        )
    add(
        6,
        "导入前校验",
        "next_step",
        "account_import_check",
        "python -m ashare_quant account-import-check --file outputs/account_import_from_state_template.csv",
        "编辑并保存导入模板后先做 dry-run 校验；导入前必须让目标文件成为最近一次通过校验的文件。",
        "只有 account_import_check_audit.csv 全部通过后，才应运行 account-import --confirm。",
    )
    add(
        7,
        "复核",
        "next_step",
        "account_state_recheck",
        "python -m ashare_quant account-state",
        "完成重置或导入后重新导出账户状态；所有审计项通过后再生成订单。",
        "不要把未通过账户审计时生成的 orders.csv 当作可执行订单。",
    )
    return pd.DataFrame(rows, columns=ACCOUNT_READINESS_PLAN_COLUMNS)


def write_account_readiness_plan(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "account_readiness_plan.csv"
    atomic_to_csv(build_account_readiness_plan(config), path, index=False, encoding="utf-8-sig")
    return path


def build_account_path_decision(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    audit = read_csv(output_dir / "account_state_audit.csv")
    full_template = output_dir / "account_import_from_state_template.csv"
    priced_template = output_dir / "account_import_priced_only_template.csv"
    excluded = read_csv(output_dir / "account_repair_excluded_positions.csv")
    rows: list[dict[str, object]] = []

    def add(path: str, available: bool, status: str, blockers: str, command: str, next_action: str, note: str) -> None:
        rows.append(
            {
                "path": path,
                "available": bool(available),
                "status": status,
                "blockers": blockers,
                "command": command,
                "next_action": next_action,
                "note": note,
            }
        )

    account_ready = not audit.empty and "passed" in audit.columns and bool(audit["passed"].map(parse_bool_like).all())
    failed_checks = failed_check_names(audit)
    add(
        "current_account_state",
        account_ready,
        "ready" if account_ready else "blocked",
        "" if account_ready else failed_checks or "account_state_audit_missing",
        "python -m ashare_quant account-state",
        "当前账户审计通过后，才能直接进入 practical 生成可人工核对订单。" if account_ready else "先刷新账户状态审计，确认是否仍存在缺价或状态不一致。",
        "该路径不修改账户状态，只说明当前 SQLite 账户是否可直接继续使用。",
    )

    add(
        "simulation_reset",
        True,
        "available_with_confirmation",
        "confirmation_required",
        "python -m ashare_quant account-reset --confirm",
        "只做本地模拟盘时可选择该路径；执行前确认会清空本地模拟账户、订单、成交和工单。",
        "这是最快跑通 paper/practical 和工单链路的路径，但不保留当前真实/模拟持仓。",
    )

    full = evaluate_import_template(config, full_template)
    add(
        "real_account_full_import",
        bool(full["available"]),
        str(full["status"]),
        str(full["blockers"]),
        "python -m ashare_quant account-import-check --file outputs/account_import_from_state_template.csv",
        str(full["next_action"]),
        str(full["note"]),
    )

    priced = evaluate_import_template(config, priced_template)
    excluded_count = int(len(excluded)) if not excluded.empty else 0
    priced_status = str(priced["status"])
    priced_next_action = str(priced["next_action"])
    if bool(priced["available"]) and excluded_count > 0:
        priced_status = "precheck_passed_with_exclusions" if priced_status == "precheck_passed" else "available_with_exclusions"
        priced_next_action = (
            "先人工核对 outputs/account_repair_excluded_positions.csv；"
            "如果确认排除这些缺价持仓，执行 python -m ashare_quant account-import "
            "--file outputs/account_import_priced_only_template.csv --confirm --ack-exclusions。"
        )
    add(
        "real_account_priced_only_import",
        bool(priced["available"]),
        priced_status,
        str(priced["blockers"]),
        "python -m ashare_quant account-import-check --file outputs/account_import_priced_only_template.csv",
        priced_next_action,
        f"{priced['note']} 排除缺价持仓 {excluded_count} 行；真实账户使用前必须人工确认排除清单；确认导入需加 --ack-exclusions。",
    )
    return pd.DataFrame(rows, columns=ACCOUNT_PATH_DECISION_COLUMNS)


def write_account_path_decision(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "account_path_decision.csv"
    atomic_to_csv(build_account_path_decision(config), path, index=False, encoding="utf-8-sig")
    return path


def evaluate_import_template(config: dict[str, Any], template_path: Path) -> dict[str, object]:
    if not template_path.exists():
        return {
            "available": False,
            "status": "missing_template",
            "blockers": "template_missing",
            "next_action": "先运行 python -m ashare_quant account-repair-template 生成账户导入模板。",
            "note": f"未找到 {template_path.name}。",
        }
    try:
        raw = read_account_import_csv(template_path)
        cash, positions = normalize_account_import(raw)
        position_valid = validate_import_positions(positions)
        if cash < 0:
            return blocked_template_result("cash_negative", "现金不能为负。")
        if not bool(position_valid["passed"]):
            return blocked_template_result("position_rows_invalid", f"持仓行无效数量：{position_valid['value']}。")
        if positions.empty:
            return {
                "available": True,
                "status": "available_cash_only",
                "blockers": "",
                "next_action": f"运行 account-import-check 校验 {template_path.name}，通过后再 account-import --confirm。",
                "note": "模板只有现金，无持仓估值风险。",
            }
        store = DataStore.from_config(config)
        _, price_map = resolve_import_pricing(store, config, None)
        priced = build_import_positions_snapshot(positions, price_map)
        missing = priced[priced["close"] <= 0]["symbol"].astype(str).tolist() if not priced.empty else []
        if missing:
            return {
                "available": False,
                "status": "blocked_missing_prices",
                "blockers": f"missing_prices:{len(missing)}",
                "next_action": "先刷新行情或处理缺价持仓，再重新运行 account-import-check。",
                "note": f"模板 {template_path.name} 中 {len(missing)}/{len(positions)} 只持仓缺少缓存价格。",
            }
        equity = cash + float(priced["market_value"].sum())
        precheck = latest_import_precheck_for_template(config, template_path)
        if precheck["matched"] and precheck["passed"]:
            return {
                "available": True,
                "status": "precheck_passed",
                "blockers": "",
                "next_action": f"人工核对 {template_path.name} 后，可执行 account-import --confirm 导入该模板。",
                "note": (
                    f"模板 {template_path.name} 可估值持仓 {len(positions)} 只，预估权益 {round(equity, 2)}。"
                    f" 最近一次 dry-run 预检已通过：{precheck['note']}"
                ),
            }
        if precheck["matched"] and not precheck["passed"]:
            return {
                "available": False,
                "status": "precheck_failed",
                "blockers": str(precheck["blockers"]),
                "next_action": f"查看 account_import_check_audit.csv，修复后重新校验 {template_path.name}。",
                "note": (
                    f"模板 {template_path.name} 可估值持仓 {len(positions)} 只，预估权益 {round(equity, 2)}。"
                    f" 最近一次 dry-run 预检未通过：{precheck['note']}"
                ),
            }
        return {
            "available": True,
            "status": "available_after_precheck",
            "blockers": "",
            "next_action": f"运行 account-import-check 校验 {template_path.name}，通过后再 account-import --confirm。",
            "note": (
                f"模板 {template_path.name} 可估值持仓 {len(positions)} 只，预估权益 {round(equity, 2)}。"
                f" {precheck['note']}"
            ),
        }
    except Exception as exc:
        return blocked_template_result("template_invalid", str(exc))


def latest_import_precheck_for_template(config: dict[str, Any], template_path: Path) -> dict[str, object]:
    output_dir = project_path(config["paths"]["output_dir"])
    audit = read_csv(output_dir / "account_import_check_audit.csv")
    snapshot = read_csv(output_dir / "account_import_check_snapshot.csv")
    if audit.empty or snapshot.empty:
        return {"matched": False, "passed": False, "blockers": "precheck_missing", "note": "尚未运行该模板的 dry-run 预检。"}
    if not template_path.exists():
        return {"matched": False, "passed": False, "blockers": "template_missing", "note": f"未找到 {template_path.name}。"}
    expected_hash = "" if "source_sha256" not in snapshot.columns or snapshot.empty else str(snapshot["source_sha256"].iloc[0])
    actual_hash = file_sha256(template_path)
    source_file = "" if "source_file" not in snapshot.columns or snapshot.empty else str(snapshot["source_file"].iloc[0])
    if not expected_hash or expected_hash != actual_hash:
        return {
            "matched": False,
            "passed": False,
            "blockers": "precheck_stale_or_other_file",
            "note": f"最近一次 dry-run 预检对应 {Path(source_file).name if source_file else '其他文件'}，不是 {template_path.name}。",
        }
    passed_series = audit["passed"].map(parse_bool_like) if "passed" in audit.columns else pd.Series(False, index=audit.index)
    failed = audit.loc[~passed_series]
    would_write = "would_write_state" in snapshot.columns and parse_bool_like(snapshot["would_write_state"].iloc[0])
    checked_at = "" if "checked_at" not in snapshot.columns or pd.isna(snapshot["checked_at"].iloc[0]) else str(snapshot["checked_at"].iloc[0])
    if failed.empty and would_write:
        return {
            "matched": True,
            "passed": True,
            "blockers": "",
            "note": f"{template_path.name}，checked_at={checked_at or '-'}，source_sha256={actual_hash[:16]}。",
        }
    failed_checks = ";".join(failed["check"].astype(str).tolist()) if "check" in failed.columns and not failed.empty else "would_write_state"
    return {
        "matched": True,
        "passed": False,
        "blockers": failed_checks,
        "note": f"{template_path.name} 预检阻断项：{failed_checks}。",
    }


def blocked_template_result(blocker: str, note: str) -> dict[str, object]:
    return {
        "available": False,
        "status": "blocked",
        "blockers": blocker,
        "next_action": "修正模板后重新运行 account-import-check。",
        "note": note,
    }


def failed_check_names(audit: pd.DataFrame) -> str:
    if audit.empty or "passed" not in audit.columns:
        return ""
    failed = audit.loc[~audit["passed"].map(parse_bool_like)]
    if failed.empty:
        return ""
    if "check" not in failed.columns:
        return "account_state_audit"
    return ";".join(failed["check"].astype(str).tolist())


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def snapshot_value(snapshot: pd.DataFrame, column: str) -> str:
    if snapshot.empty or column not in snapshot.columns:
        return ""
    value = snapshot[column].iloc[0]
    return "" if pd.isna(value) else str(value)


def count_unique_symbols(frame: pd.DataFrame) -> int:
    if frame.empty or "symbol" not in frame.columns:
        return 0
    return int(frame["symbol"].astype(str).nunique())


def count_false_by_symbol(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or "symbol" not in frame.columns or column not in frame.columns:
        return 0
    values = frame[["symbol", column]].copy()
    values[column] = values[column].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    by_symbol = values.groupby("symbol")[column].any()
    return int((~by_symbol).sum())
