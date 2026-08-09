
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .io_utils import atomic_to_csv


ACCOUNT_EXCLUSION_IMPACT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
ACCOUNT_EXCLUSION_IMPACT_DETAIL_COLUMNS = [
    "symbol",
    "quantity",
    "sellable_quantity",
    "cost",
    "cost_proxy",
    "cost_proxy_ratio",
    "price_status",
    "issue",
    "suggested_action",
    "note",
]


def write_account_exclusion_impact(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, detail = build_account_exclusion_impact(config)
    summary_path = output_dir / "account_exclusion_impact.csv"
    detail_path = output_dir / "account_exclusion_impact_detail.csv"
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(detail, detail_path, index=False, encoding="utf-8-sig")
    return {"account_exclusion_impact.csv": summary_path, "account_exclusion_impact_detail.csv": detail_path}


def build_account_exclusion_impact(config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    output_dir = project_path(config["paths"]["output_dir"])
    excluded = read_csv(output_dir / "account_repair_excluded_positions.csv", dtype={"symbol": str})
    snapshot = read_csv(output_dir / "account_state_snapshot.csv")
    check_audit = read_csv(output_dir / "account_import_check_audit.csv")
    check_snapshot = read_csv(output_dir / "account_import_check_snapshot.csv")
    priced_template = read_csv(output_dir / "account_import_priced_only_template.csv", dtype={"symbol": str})
    current_state_audit = read_csv(output_dir / "account_state_audit.csv")
    current_missing_prices = read_csv(output_dir / "account_state_missing_prices.csv", dtype={"symbol": str})

    ignore_stale_exclusions = bool(
        not excluded.empty
        and audit_all_passed(current_state_audit)
        and current_missing_prices.empty
    )
    if ignore_stale_exclusions:
        excluded = pd.DataFrame(columns=excluded.columns)

    equity = account_equity(snapshot)
    threshold = float(config.get("account", {}).get("max_excluded_cost_proxy_ratio", 0.01))
    detail = build_exclusion_detail(excluded, equity)
    excluded_count = int(len(detail))
    excluded_cost = float(pd.to_numeric(detail.get("cost_proxy", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not detail.empty else 0.0
    excluded_ratio = excluded_cost / equity if equity > 0 else 0.0
    priced_template_positions = priced_position_count(priced_template)
    check_passed = audit_all_passed(check_audit)
    source_file = first_value(check_snapshot, "source_file")
    checked_priced_only = "account_import_priced_only_template.csv" in source_file
    priced_only_needed = excluded_count > 0

    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold_value: object, severity: str, next_action: str, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold_value,
                "severity": "ok" if passed else severity,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    add(
        "stale_exclusion_file_ignored",
        True,
        "yes" if ignore_stale_exclusions else "not_applicable",
        "account_state_audit passed and account_state_missing_prices empty",
        "info",
        "",
        "历史账户修复排除文件只在当前账户仍存在缺价持仓时生效；账户重置或导入后若审计通过，旧排除文件自动忽略。",
    )
    add(
        "excluded_position_count",
        excluded_count == 0,
        excluded_count,
        0,
        "account_blocker",
        "人工核对 outputs/account_repair_excluded_positions.csv；真实账户导入前必须确认每一条缺价持仓。",
        "priced-only 模板排除了缺价持仓；只要存在排除项，真实账户导入都需要人工确认。",
    )
    add(
        "excluded_cost_proxy_ratio",
        excluded_ratio <= threshold,
        f"{excluded_ratio:.4f}",
        f"<={threshold:.4f}",
        "account_risk",
        "优先补齐缺价持仓价格或确认该证券不应计入当前账户状态。",
        "按 quantity * cost 估算缺价持仓的最大成本口径影响；该数值不代表当前市值。",
    )
    notes_ok = excluded_count == 0 or detail_has_review_notes(detail)
    add(
        "excluded_rows_have_review_notes",
        notes_ok,
        f"{review_note_count(detail)}/{excluded_count}",
        "all excluded rows have issue and suggested_action",
        "account_blocker",
        "重新运行 python -m ashare_quant account-repair-template 生成完整排除说明。",
        "每一条被排除持仓都必须保留原因和建议处理动作，方便人工复核。",
    )
    add(
        "priced_only_template_available",
        (not priced_only_needed) or priced_template_positions > 0,
        "not_required" if not priced_only_needed else priced_template_positions,
        ">0 priced positions when exclusions exist",
        "account_blocker",
        "运行 python -m ashare_quant account-repair-template 生成可估值导入模板。",
        "可估值导入模板仅在存在缺价排除项时必需；没有排除项时不阻断。",
    )
    add(
        "priced_only_precheck_passed",
        (not priced_only_needed) or bool(check_passed and checked_priced_only),
        "not_required" if not priced_only_needed else f"passed={check_passed}, source={source_file or 'missing'}",
        "latest account-import-check passed for account_import_priced_only_template.csv when exclusions exist",
        "account_blocker",
        "运行 python -m ashare_quant account-import-check --file outputs/account_import_priced_only_template.csv。",
        "priced-only dry-run 仅在存在缺价排除项时必需；没有排除项时不阻断。",
    )
    add(
        "manual_ack_required",
        excluded_count == 0,
        "required" if excluded_count else "not_required",
        "no excluded positions or explicit --ack-exclusions",
        "account_blocker",
        "若确认排除清单无误，才执行 account-import --confirm --ack-exclusions；否则先补价或修正模板。",
        "系统不会自动确认排除真实持仓；--ack-exclusions 必须由人工显式执行。",
    )
    return pd.DataFrame(rows, columns=ACCOUNT_EXCLUSION_IMPACT_COLUMNS), detail


def build_exclusion_detail(excluded: pd.DataFrame, equity: float) -> pd.DataFrame:
    if excluded.empty:
        return pd.DataFrame(columns=ACCOUNT_EXCLUSION_IMPACT_DETAIL_COLUMNS)
    rows: list[dict[str, object]] = []
    for row in excluded.itertuples(index=False):
        symbol = str(getattr(row, "symbol", "") or "").zfill(6)
        quantity = numeric(getattr(row, "quantity", 0.0))
        sellable = numeric(getattr(row, "sellable_quantity", 0.0))
        cost = numeric(getattr(row, "cost", 0.0))
        market_value = numeric(getattr(row, "market_value", 0.0))
        cost_proxy = abs(quantity * cost) if quantity and cost else abs(market_value)
        rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "sellable_quantity": sellable,
                "cost": cost,
                "cost_proxy": round(cost_proxy, 2),
                "cost_proxy_ratio": round(cost_proxy / equity, 6) if equity > 0 else 0.0,
                "price_status": str(getattr(row, "price_status", "") or ""),
                "issue": str(getattr(row, "issue", "") or ""),
                "suggested_action": str(getattr(row, "suggested_action", "") or ""),
                "note": str(getattr(row, "note", "") or ""),
            }
        )
    return pd.DataFrame(rows, columns=ACCOUNT_EXCLUSION_IMPACT_DETAIL_COLUMNS)


def account_equity(snapshot: pd.DataFrame) -> float:
    if snapshot.empty:
        return 0.0
    for column in ["marked_equity", "recorded_equity", "equity"]:
        if column in snapshot.columns:
            value = numeric(snapshot[column].iloc[0])
            if value > 0:
                return value
    cash = numeric(snapshot["cash"].iloc[0]) if "cash" in snapshot.columns else 0.0
    market_value = numeric(snapshot["market_value"].iloc[0]) if "market_value" in snapshot.columns else 0.0
    return cash + market_value


def audit_all_passed(frame: pd.DataFrame) -> bool:
    if frame.empty or "passed" not in frame.columns:
        return False
    passed = frame["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    return bool(len(passed) > 0 and passed.all())


def priced_position_count(frame: pd.DataFrame) -> int:
    if frame.empty or "symbol" not in frame.columns:
        return 0
    symbols = frame["symbol"].dropna().astype(str).str.strip()
    return int((symbols != "").sum())


def detail_has_review_notes(detail: pd.DataFrame) -> bool:
    if detail.empty:
        return True
    return bool(((detail["issue"].astype(str).str.strip() != "") & (detail["suggested_action"].astype(str).str.strip() != "")).all())


def review_note_count(detail: pd.DataFrame) -> int:
    if detail.empty:
        return 0
    return int(((detail["issue"].astype(str).str.strip() != "") & (detail["suggested_action"].astype(str).str.strip() != "")).sum())


def first_value(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    value = frame[column].iloc[0]
    return "" if pd.isna(value) else str(value)


def numeric(value: object) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else float(parsed)


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except EmptyDataError:
        return pd.DataFrame()
