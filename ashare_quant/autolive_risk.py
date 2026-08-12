from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .capital_budget import SYSTEM_MAX_TOTAL_CAPITAL, capital_budget_limit
from .config import project_path
from .io_utils import atomic_to_csv

AUTO_LIVE_RISK_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_RISK_RESERVATION_COLUMNS = [
    "run_id",
    "trade_date",
    "status",
    "order_count",
    "buy_amount",
    "sell_amount",
    "gross_amount",
    "tickets_sha256",
    "auto_live_config_sha256",
    "dedupe_key",
    "created_at",
    "updated_at",
    "message",
]
AUTO_LIVE_STOP_AUDIT_COLUMNS = ["action", "passed", "value", "message", "generated_at"]
AUTO_LIVE_STOP_FILE = "auto_live.stop"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (EmptyDataError, OSError):
        return pd.DataFrame()


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def finite_float(value: object, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return float(default)
    number = float(parsed)
    return number if pd.notna(number) and abs(number) != float("inf") else float(default)


def read_auto_live_risk_reservations(output_dir: Path) -> pd.DataFrame:
    frame = read_csv(output_dir / "auto_live_risk_reservations.csv")
    if frame.empty:
        return pd.DataFrame(columns=AUTO_LIVE_RISK_RESERVATION_COLUMNS)
    for column in AUTO_LIVE_RISK_RESERVATION_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[AUTO_LIVE_RISK_RESERVATION_COLUMNS].copy()


def ticket_risk_values(tickets: pd.DataFrame) -> dict[str, object]:
    if tickets.empty:
        return {
            "amounts": pd.Series(dtype=float),
            "quantities": pd.Series(dtype=float),
            "prices": pd.Series(dtype=float),
            "sides": pd.Series(dtype=str),
            "dates": [],
            "buy_amount": 0.0,
            "sell_amount": 0.0,
            "gross_amount": 0.0,
            "values_valid": False,
            "amounts_consistent": False,
        }
    quantities = pd.to_numeric(tickets.get("quantity", pd.Series(index=tickets.index, dtype=float)), errors="coerce")
    prices = pd.to_numeric(tickets.get("limit_price", pd.Series(index=tickets.index, dtype=float)), errors="coerce")
    computed = quantities * prices
    estimated = pd.to_numeric(tickets.get("estimated_amount", computed), errors="coerce")
    estimated = estimated.where(estimated > 0, computed)
    amounts = pd.concat([computed.rename("computed"), estimated.rename("estimated")], axis=1).max(axis=1)
    sides = tickets.get("side", pd.Series(index=tickets.index, dtype=str)).astype(str).str.strip().str.lower()
    date_values = pd.to_datetime(tickets.get("date", pd.Series(index=tickets.index, dtype=object)), errors="coerce")
    dates = sorted({item.date().isoformat() for item in date_values.dropna()})
    values_valid = bool(
        len(tickets) > 0
        and quantities.notna().all()
        and prices.notna().all()
        and amounts.notna().all()
        and (quantities > 0).all()
        and (prices > 0).all()
        and (amounts > 0).all()
    )
    denominator = computed.abs().clip(lower=1.0)
    relative_difference = (estimated - computed).abs() / denominator
    amounts_consistent = bool(relative_difference.fillna(float("inf")).le(0.01).all())
    buy_amount = float(amounts[sides.eq("buy")].sum())
    sell_amount = float(amounts[sides.eq("sell")].sum())
    return {
        "amounts": amounts,
        "quantities": quantities,
        "prices": prices,
        "sides": sides,
        "dates": dates,
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "gross_amount": buy_amount + sell_amount,
        "values_valid": values_valid,
        "amounts_consistent": amounts_consistent,
    }


def auto_live_risk_limits(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {})
    risk = auto.get("risk_limits", {}) if isinstance(auto.get("risk_limits", {}), dict) else {}
    rpa = config.get("rpa", {})
    trading = config.get("trading", {})
    rollout = auto.get("rollout", {}) if isinstance(auto.get("rollout", {}), dict) else {}
    rollout_stage = str(rollout.get("stage", "") or "").strip().lower()
    rollout_stages = rollout.get("stages", {}) if isinstance(rollout.get("stages", {}), dict) else {}
    rollout_stage_limits = rollout_stages.get(rollout_stage, {}) if isinstance(rollout_stages.get(rollout_stage, {}), dict) else {}
    stage_cash_defaults = {
        "canary": {"amount": 500.0, "ratio": 0.10},
        "limited": {"amount": 2000.0, "ratio": 0.10},
        "full": {"amount": 10000.0, "ratio": 0.05},
    }
    stage_cash = stage_cash_defaults.get(
        rollout_stage,
        {"amount": 10000.0, "ratio": finite_float(trading.get("reserve_cash_ratio", 0.01), 0.01)},
    )
    stage_cash_amount = finite_float(rollout_stage_limits.get("min_cash_buffer_amount", stage_cash["amount"]), stage_cash["amount"])
    stage_cash_ratio = finite_float(rollout_stage_limits.get("min_cash_buffer_ratio", stage_cash["ratio"]), stage_cash["ratio"])
    cash_policy_source = "risk_limits" if "min_cash_buffer_amount" in risk or "min_cash_buffer_ratio" in risk else (f"rollout:{rollout_stage}" if rollout_stage in stage_cash_defaults else "default")
    capital_limit = capital_budget_limit(config) or SYSTEM_MAX_TOTAL_CAPITAL
    max_single = min(
        finite_float(risk.get("max_single_order_amount", rpa.get("max_single_order_amount", 100000)), 100000),
        capital_limit,
    )
    max_batch_buy = min(
        finite_float(risk.get("max_batch_buy_amount", rpa.get("max_daily_order_amount", 200000)), 200000),
        capital_limit,
    )
    max_daily_buy = min(
        finite_float(risk.get("max_daily_buy_amount", rpa.get("max_daily_order_amount", 200000)), 200000),
        capital_limit,
    )
    max_gross = capital_limit * 2
    return {
        "enabled": parse_bool(risk.get("enabled", True)),
        "max_orders_per_batch": int(max(0, finite_float(risk.get("max_orders_per_batch", 50), 50))),
        "max_single_order_amount": max_single,
        "max_batch_buy_amount": max_batch_buy,
        "max_batch_gross_amount": min(finite_float(risk.get("max_batch_gross_amount", max_batch_buy * 2), max_batch_buy * 2), max_gross),
        "max_daily_buy_amount": max_daily_buy,
        "max_daily_gross_amount": min(finite_float(risk.get("max_daily_gross_amount", max_daily_buy * 2), max_daily_buy * 2), max_gross),
        "min_cash_buffer_ratio": max(0.0, finite_float(risk.get("min_cash_buffer_ratio", stage_cash_ratio), stage_cash_ratio)),
        "min_cash_buffer_amount": max(0.0, finite_float(risk.get("min_cash_buffer_amount", stage_cash_amount), stage_cash_amount)),
        "cash_buffer_policy_source": cash_policy_source,
        "cash_buffer_rollout_stage": rollout_stage or "disabled",
        "allowed_sides": risk.get("allowed_sides", ["buy", "sell"]),
    }


def auto_live_emergency_stop_status(config: dict[str, Any]) -> tuple[bool, str]:
    output_dir = project_path(config["paths"]["output_dir"])
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    config_stop = parse_bool(auto.get("emergency_stop", False))
    file_stop = (output_dir / AUTO_LIVE_STOP_FILE).exists()
    return bool(config_stop or file_stop), f"config={config_stop};file={file_stop}"


def build_auto_live_risk_audit(
    config: dict[str, Any],
    tickets: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    tickets = read_csv(output_dir / "rpa_orders.csv") if tickets is None else tickets.copy()
    limits = auto_live_risk_limits(config)
    values = ticket_risk_values(tickets)
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

    auto = config.get("auto_live", {})
    stop_active, stop_value = auto_live_emergency_stop_status(config)
    add("risk_cash_buffer_policy", True, f"source={limits['cash_buffer_policy_source']};stage={limits['cash_buffer_rollout_stage']};amount={limits['min_cash_buffer_amount']:.2f};ratio={limits['min_cash_buffer_ratio']:.4f}", "explicit risk limits or current rollout stage", "info", "", "现金缓冲策略必须明确绑定到风险配置或当前发布阶段。")
    add("risk_limits_enabled", bool(limits["enabled"]), limits["enabled"], True, "safety_blocker", "设置 auto_live.risk_limits.enabled=true。", "自动实盘风险限额不能关闭。")
    add(
        "emergency_stop_inactive",
        not stop_active,
        stop_value,
        "config=False and auto_live.stop absent",
        "safety_blocker",
        "核对停机原因后，使用 auto-live-resume --confirm 显式恢复。",
        "配置停机开关或本地停机文件任一存在时，禁止券商提交。",
    )

    required_columns = {"ticket_id", "date", "symbol", "side", "quantity", "limit_price", "status"}
    schema_ok = required_columns.issubset(tickets.columns)
    add("risk_ticket_schema_valid", schema_ok, ",".join(map(str, tickets.columns)), ",".join(sorted(required_columns)), "safety_blocker", "重新导出 rpa_orders.csv。", "风险审计必须读取完整工单字段。")
    ready_ok = bool(not tickets.empty and "status" in tickets.columns and tickets["status"].astype(str).str.lower().eq("ready").all())
    add("risk_tickets_all_ready", ready_ok, len(tickets) if ready_ok else "not_all_ready", ">0 and all ready", "safety_blocker", "移除 blocked 工单并重新导出。", "自动实盘风险审计只接受全部 ready 的工单。")
    add("risk_ticket_values_valid", bool(values["values_valid"]), f"rows={len(tickets)}", "positive finite quantity/price/amount", "safety_blocker", "修正数量、限价和预计金额。", "数量、限价和金额必须为正数且可解析。")
    add("risk_ticket_amounts_consistent", bool(values["amounts_consistent"]), "within_1pct" if values["amounts_consistent"] else "mismatch", "estimated ~= quantity*limit_price", "safety_blocker", "重新生成工单，禁止手工缩小预计金额。", "风险金额取预计金额与数量乘限价的较大值，并核对二者一致性。")

    dates = values["dates"]
    single_day = len(dates) == 1
    trade_date = dates[0] if single_day else (dates[-1] if dates else (now or datetime.now()).date().isoformat())
    add("risk_ticket_single_trade_date", single_day, ",".join(dates) or "missing", "one trade date", "safety_blocker", "每批只保留同一交易日工单。", "单批跨交易日会破坏当日累计额度。")

    allowed_raw = limits["allowed_sides"]
    allowed = {str(item).strip().lower() for item in allowed_raw} if isinstance(allowed_raw, list) else set()
    sides = {str(item).strip().lower() for item in values["sides"].tolist() if str(item).strip() and str(item).strip().lower() != "nan"}
    add("risk_sides_allowed", bool(sides and sides.issubset(allowed)), ",".join(sorted(sides)) or "missing", ",".join(sorted(allowed)) or "missing", "safety_blocker", "修正 allowed_sides 或工单方向。", "提交方向必须在自动实盘白名单内。")

    numeric_limits = [
        limits["max_orders_per_batch"],
        limits["max_single_order_amount"],
        limits["max_batch_buy_amount"],
        limits["max_batch_gross_amount"],
        limits["max_daily_buy_amount"],
        limits["max_daily_gross_amount"],
    ]
    limits_valid = all(float(value) > 0 for value in numeric_limits)
    add("risk_limits_valid", limits_valid, ",".join(f"{float(value):g}" for value in numeric_limits), "all limits > 0", "safety_blocker", "修正 auto_live.risk_limits。", "风险上限必须为正数。")

    amounts = values["amounts"]
    max_order = float(amounts.max()) if isinstance(amounts, pd.Series) and not amounts.empty else 0.0
    buy_amount = float(values["buy_amount"])
    sell_amount = float(values["sell_amount"])
    gross_amount = float(values["gross_amount"])
    add("risk_order_count_limit", len(tickets) <= int(limits["max_orders_per_batch"]), len(tickets), limits["max_orders_per_batch"], "safety_blocker", "减少单批订单数量。", "单批订单数量必须受硬上限约束。")
    add("risk_single_order_amount_limit", max_order <= float(limits["max_single_order_amount"]), round(max_order, 2), limits["max_single_order_amount"], "safety_blocker", "拆分或取消超限订单。", "任何单笔订单金额不得超过自动实盘上限。")
    add("risk_batch_buy_amount_limit", buy_amount <= float(limits["max_batch_buy_amount"]), round(buy_amount, 2), limits["max_batch_buy_amount"], "safety_blocker", "降低本批买入金额。", "本批买入金额不得超过硬上限。")
    add("risk_batch_gross_amount_limit", gross_amount <= float(limits["max_batch_gross_amount"]), round(gross_amount, 2), limits["max_batch_gross_amount"], "safety_blocker", "降低本批买卖总额。", "本批买卖总金额不得超过硬上限。")

    reservations = read_auto_live_risk_reservations(output_dir)
    if not reservations.empty:
        active = reservations[
            reservations["trade_date"].astype(str).eq(trade_date)
            & ~reservations["status"].astype(str).str.lower().eq("released")
        ].copy()
    else:
        active = reservations
    prior_buy = float(pd.to_numeric(active.get("buy_amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    prior_gross = float(pd.to_numeric(active.get("gross_amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    projected_buy = prior_buy + buy_amount
    projected_gross = prior_gross + gross_amount
    add("risk_daily_buy_amount_limit", projected_buy <= float(limits["max_daily_buy_amount"]), f"prior={prior_buy:.2f};projected={projected_buy:.2f}", limits["max_daily_buy_amount"], "safety_blocker", "等待下一交易日或降低买入金额。", "当日额度包含已提交、状态不明和崩溃前预留的批次。")
    add("risk_daily_gross_amount_limit", projected_gross <= float(limits["max_daily_gross_amount"]), f"prior={prior_gross:.2f};projected={projected_gross:.2f}", limits["max_daily_gross_amount"], "safety_blocker", "等待下一交易日或降低交易总额。", "当日买卖总额按风险预留台账累计。")

    adapter = str(auto.get("broker_adapter", "") or "").strip().lower()
    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    if adapter != "command":
        cash_passed = True
        cash_value = "not_required_for_non_command_adapter"
        cash_threshold = "command adapter only"
    elif snapshot.empty:
        cash_passed = True
        cash_value = "deferred_to_broker_account_snapshot_gate"
        cash_threshold = "fresh broker snapshot before submit"
    else:
        account_row = snapshot.iloc[-1]
        available_cash = finite_float(account_row.get("available_cash", -1), -1)
        total_asset = finite_float(account_row.get("total_asset", -1), -1)
        reserve = max(float(limits["min_cash_buffer_amount"]), max(0.0, total_asset) * float(limits["min_cash_buffer_ratio"]))
        required_cash = buy_amount + reserve
        cash_passed = available_cash >= required_cash and available_cash >= 0 and total_asset > 0
        cash_value = f"available={available_cash:.2f};buy={buy_amount:.2f};reserve={reserve:.2f}"
        cash_threshold = f">={required_cash:.2f}"
    add("risk_available_cash_buffer", cash_passed, cash_value, cash_threshold, "safety_blocker", "减少买入金额或刷新真实券商账户快照。", "买入不得依赖本批卖出回款，并必须保留现金缓冲。")

    return pd.DataFrame(rows, columns=AUTO_LIVE_RISK_AUDIT_COLUMNS)


def write_auto_live_risk_audit(config: dict[str, Any], tickets: pd.DataFrame | None = None, now: datetime | None = None) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_risk_audit.csv"
    atomic_to_csv(build_auto_live_risk_audit(config, tickets=tickets, now=now), path, index=False, encoding="utf-8-sig")
    reservation_path = output_dir / "auto_live_risk_reservations.csv"
    if not reservation_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_RISK_RESERVATION_COLUMNS), reservation_path, index=False, encoding="utf-8-sig")
    stop_audit_path = output_dir / "auto_live_stop_audit.csv"
    if not stop_audit_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_STOP_AUDIT_COLUMNS), stop_audit_path, index=False, encoding="utf-8-sig")
    return path


def upsert_auto_live_risk_reservation(
    config: dict[str, Any],
    tickets: pd.DataFrame,
    *,
    run_id: str,
    status: str,
    tickets_hash: str,
    auto_hash: str,
    dedupe_key: str,
    message: str,
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_risk_reservations.csv"
    existing = read_auto_live_risk_reservations(output_dir)
    values = ticket_risk_values(tickets)
    dates = values["dates"]
    trade_date = dates[-1] if dates else datetime.now().date().isoformat()
    created_at = datetime.now().isoformat(timespec="seconds")
    matched = existing[existing["run_id"].astype(str).eq(str(run_id))] if not existing.empty else existing
    if not matched.empty:
        created_at = str(matched.iloc[-1].get("created_at", "") or created_at)
        existing = existing[~existing["run_id"].astype(str).eq(str(run_id))].copy()
    row = pd.DataFrame(
        [
            {
                "run_id": run_id,
                "trade_date": trade_date,
                "status": status,
                "order_count": len(tickets),
                "buy_amount": round(float(values["buy_amount"]), 2),
                "sell_amount": round(float(values["sell_amount"]), 2),
                "gross_amount": round(float(values["gross_amount"]), 2),
                "tickets_sha256": tickets_hash,
                "auto_live_config_sha256": auto_hash,
                "dedupe_key": dedupe_key,
                "created_at": created_at,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "message": message,
            }
        ],
        columns=AUTO_LIVE_RISK_RESERVATION_COLUMNS,
    )
    updated = pd.concat([existing, row], ignore_index=True)
    atomic_to_csv(updated[AUTO_LIVE_RISK_RESERVATION_COLUMNS], path, index=False, encoding="utf-8-sig")
    return path


def write_stop_audit(output_dir: Path, action: str, passed: bool, value: str, message: str) -> Path:
    path = output_dir / "auto_live_stop_audit.csv"
    row = pd.DataFrame(
        [{"action": action, "passed": bool(passed), "value": value, "message": message, "generated_at": datetime.now().isoformat(timespec="seconds")}],
        columns=AUTO_LIVE_STOP_AUDIT_COLUMNS,
    )
    atomic_to_csv(row, path, index=False, encoding="utf-8-sig")
    return path


def activate_auto_live_stop(config: dict[str, Any], reason: str = "manual_emergency_stop") -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    stop_path = output_dir / AUTO_LIVE_STOP_FILE
    payload = {"active": True, "reason": str(reason or "manual_emergency_stop"), "created_at": datetime.now().isoformat(timespec="seconds")}
    temp_path = stop_path.with_suffix(stop_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, stop_path)
    audit_path = write_stop_audit(output_dir, "activate", True, stop_path.name, f"Emergency stop active: {payload['reason']}")
    risk_path = write_auto_live_risk_audit(config)
    return {"auto_live.stop": stop_path, "auto_live_stop_audit.csv": audit_path, "auto_live_risk_audit.csv": risk_path}


def clear_auto_live_stop(config: dict[str, Any], confirm: bool = False) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    stop_path = output_dir / AUTO_LIVE_STOP_FILE
    if not confirm:
        audit_path = write_stop_audit(output_dir, "clear", False, "confirmation_missing", "Use --confirm after reviewing the stop reason and broker state.")
        raise RuntimeError(f"auto-live resume blocked: confirmation_missing; audit={audit_path.name}")
    if (output_dir / "auto_live_submit.lock").exists():
        audit_path = write_stop_audit(output_dir, "clear", False, "submit_lock_exists", "Cannot resume while an auto-live submit lock exists.")
        raise RuntimeError(f"auto-live resume blocked: submit_lock_exists; audit={audit_path.name}")
    try:
        stop_path.unlink()
        value = "cleared"
    except FileNotFoundError:
        value = "already_clear"
    audit_path = write_stop_audit(output_dir, "clear", True, value, "Emergency stop file is absent; normal gates still apply before any submit.")
    risk_path = write_auto_live_risk_audit(config)
    return {"auto_live_stop_audit.csv": audit_path, "auto_live_risk_audit.csv": risk_path}
