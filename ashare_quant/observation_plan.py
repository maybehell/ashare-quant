from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .data import DataStore
from .evidence import build_evidence_plan
from .io_utils import atomic_to_csv


OBSERVATION_SCHEDULE_COLUMNS = [
    "area",
    "status",
    "current",
    "required",
    "remaining",
    "latest_data_date",
    "estimated_next_run_date",
    "command",
    "blocking_reason",
    "note",
]


def write_observation_schedule(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "observation_schedule.csv"
    atomic_to_csv(build_observation_schedule(config), path, index=False, encoding="utf-8-sig")
    return path


def build_observation_schedule(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    evidence = read_csv(output_dir / "evidence_plan.csv")
    if evidence.empty:
        evidence = build_evidence_plan(config)
    account_paths = read_csv(output_dir / "account_path_decision.csv")
    latest_data = latest_market_data_date(config)
    next_run = estimate_next_observation_date(latest_data)
    rows: list[dict[str, object]] = []

    def add(area: str, status: str, current: object, required: object, remaining: object, command: str, blocking_reason: str, note: str) -> None:
        rows.append(
            {
                "area": area,
                "status": status,
                "current": current,
                "required": required,
                "remaining": remaining,
                "latest_data_date": latest_data.isoformat() if latest_data is not None else "missing",
                "estimated_next_run_date": next_run.isoformat(),
                "command": command,
                "blocking_reason": blocking_reason,
                "note": note,
            }
        )

    forward = evidence_row(evidence, "forward_validation")
    shadow = evidence_row(evidence, "shadow_observation")
    account = evidence_row(evidence, "account_audit")
    rpa = evidence_row(evidence, "rpa_pre_trade")
    strict_gate = evidence_row(evidence, "strict_live_gate")
    auto_cfg = evidence_row(evidence, "auto_live_config")

    observe_command = "python -m ashare_quant forward-daily --refresh --quiet"
    forward_remaining = int_number(forward.get("remaining"), 0)
    shadow_remaining = int_number(shadow.get("remaining"), 0)
    if forward_remaining > 0 or shadow_remaining > 0:
        add(
            "daily_observation_collection",
            "scheduled",
            f"forward_remaining={forward_remaining};shadow_remaining={shadow_remaining}",
            "forward=0 and shadow=0 remaining",
            max(forward_remaining, shadow_remaining),
            observe_command,
            "forward_or_shadow_evidence_incomplete",
            "盘后每日运行一次；该命令只刷新行情、回测、记录 Shadow 和更新报告，不生成实盘委托。",
        )
    else:
        add(
            "daily_observation_collection",
            "not_required",
            "complete",
            "forward=0 and shadow=0 remaining",
            0,
            "python -m ashare_quant report",
            "",
            "前向与 Shadow 数量门槛已满足；仍需其它实盘门控通过后才能自动实盘。",
        )

    add_from_evidence(add, forward, "forward_validation", observe_command, "冻结参数后新增交易日不足时，只能继续用新行情自然累计，不能用历史回填替代。")
    add_from_evidence(add, shadow, "shadow_observation", observe_command, "Shadow 样本必须来自非执行观察信号，并等待足够后续交易日评估收益。")

    account_command = str(account.get("command") or "python -m ashare_quant account-state")
    priced_status = path_status(account_paths, "real_account_priced_only_import")
    if priced_status.startswith("precheck_passed"):
        account_command = "python -m ashare_quant account-import --file outputs/account_import_priced_only_template.csv --confirm --ack-exclusions"
    add_from_evidence(
        add,
        account,
        "account_audit",
        account_command,
        "账户审计必须先通过；如果导入可估值模板排除缺价持仓，必须人工核对排除清单并显式加 --ack-exclusions。",
    )
    add_from_evidence(add, rpa, "rpa_pre_trade", str(rpa.get("command") or "python -m ashare_quant export-orders"), "RPA 前置检查依赖账户审计、订单来源、容量审计和安全配置。")
    add_from_evidence(add, strict_gate, "strict_live_gate", str(strict_gate.get("command") or observe_command), "严格实盘门控必须在前向、Shadow、账户、RPA 和策略晋级全部通过后才放行。")
    add_from_evidence(add, auto_cfg, "auto_live_config", "python -m ashare_quant auto-live-check", "自动实盘还需要显式 auto_live.enabled、broker_adapter 和 allow_broker_submit 配置。")
    return pd.DataFrame(rows, columns=OBSERVATION_SCHEDULE_COLUMNS)


def add_from_evidence(add: Any, row: dict[str, object], area: str, command: str, note: str) -> None:
    status = str(row.get("status") or "missing")
    current = row.get("current", "missing")
    required = row.get("required", "ready")
    remaining = row.get("remaining", 1 if status != "passed" else 0)
    blocker = str(row.get("blocker") or "")
    add(area, status, current, required, remaining, command, blocker, note)


def evidence_row(frame: pd.DataFrame, area: str) -> dict[str, object]:
    if frame.empty or "area" not in frame.columns:
        return {"area": area, "status": "missing", "current": "missing", "required": "ready", "remaining": 1, "blocker": f"{area}_missing"}
    matched = frame[frame["area"].astype(str) == area]
    if matched.empty:
        return {"area": area, "status": "missing", "current": "missing", "required": "ready", "remaining": 1, "blocker": f"{area}_missing"}
    return matched.iloc[0].to_dict()


def latest_market_data_date(config: dict[str, Any]) -> date | None:
    try:
        store = DataStore.from_config(config)
        _, prices, _ = store.load_market_data()
    except Exception:
        return None
    if prices.empty or "date" not in prices.columns:
        return None
    values = pd.to_datetime(prices["date"], errors="coerce").dropna()
    if values.empty:
        return None
    return values.max().date()


def estimate_next_observation_date(latest_data: date | None, today: date | None = None) -> date:
    base = today or date.today()
    if latest_data is not None and latest_data >= base:
        base = latest_data + timedelta(days=1)
    return next_weekday(base)


def next_weekday(value: date) -> date:
    out = value
    while out.weekday() >= 5:
        out += timedelta(days=1)
    return out


def path_status(frame: pd.DataFrame, path: str) -> str:
    if frame.empty or not {"path", "status"}.issubset(frame.columns):
        return ""
    matched = frame[frame["path"].astype(str) == path]
    return "" if matched.empty else str(matched["status"].iloc[0])


def int_number(value: object, default: int = 0) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return int(parsed)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()
