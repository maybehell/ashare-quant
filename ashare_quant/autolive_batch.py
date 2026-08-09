from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import mapping_sha256
from .autolive import (
    BROKER_ORDER_TERMINAL_STATUSES,
    read_auto_live_submission_ledger,
    read_csv,
    tickets_sha256,
)
from .autolive_risk import (
    auto_live_risk_limits,
    read_auto_live_risk_reservations,
    ticket_risk_values,
)
from .config import project_path
from .io_utils import atomic_to_csv
from .order_integrity import verify_rpa_ticket_integrity_manifest


AUTO_LIVE_BATCH_PLAN_COLUMNS = [
    "plan_id",
    "source_tickets_sha256",
    "batch_no",
    "batch_id",
    "status",
    "selected",
    "order_count",
    "sell_count",
    "buy_count",
    "sell_amount",
    "buy_amount",
    "gross_amount",
    "tickets_sha256",
    "source_run_id",
    "message",
    "generated_at",
]
AUTO_LIVE_BATCH_ORDER_COLUMNS = [
    "plan_id",
    "source_tickets_sha256",
    "source_order",
    "batch_order",
    "batch_no",
    "batch_id",
    "batch_status",
    "selected",
    "ticket_id",
    "date",
    "symbol",
    "side",
    "quantity",
    "limit_price",
    "estimated_amount",
    "status",
    "planning_status",
    "planning_reason",
    "generated_at",
]
AUTO_LIVE_BATCH_SUMMARY_COLUMNS = [
    "plan_id",
    "status",
    "source_order_count",
    "planned_order_count",
    "blocked_order_count",
    "batch_count",
    "completed_batch_count",
    "active_batch_count",
    "selected_batch_no",
    "selected_order_count",
    "queued_order_count",
    "message",
    "generated_at",
]


def write_auto_live_batch_plan(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "auto_live_batch_plan.csv": output_dir / "auto_live_batch_plan.csv",
        "auto_live_batch_orders.csv": output_dir / "auto_live_batch_orders.csv",
        "auto_live_current_batch.csv": output_dir / "auto_live_current_batch.csv",
        "auto_live_active_batch.csv": output_dir / "auto_live_active_batch.csv",
        "auto_live_batch_summary.csv": output_dir / "auto_live_batch_summary.csv",
    }
    source = read_csv(output_dir / "rpa_orders.csv")
    integrity_ok, integrity_reason = verify_rpa_ticket_integrity_manifest(config, source)
    if integrity_ok:
        plan, orders, current, summary = build_auto_live_batch_plan(config, source)
        active = _orders_to_source_batch(source, orders, "active")
    else:
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        plan = pd.DataFrame(columns=AUTO_LIVE_BATCH_PLAN_COLUMNS)
        orders = pd.DataFrame(columns=AUTO_LIVE_BATCH_ORDER_COLUMNS)
        current = source.iloc[0:0].copy()
        active = source.iloc[0:0].copy()
        summary = pd.DataFrame([{
            "plan_id": "",
            "status": "blocked_ticket_integrity",
            "source_order_count": len(source),
            "planned_order_count": 0,
            "blocked_order_count": len(source),
            "batch_count": 0,
            "completed_batch_count": 0,
            "active_batch_count": 0,
            "selected_batch_no": 0,
            "selected_order_count": 0,
            "queued_order_count": 0,
            "message": integrity_reason,
            "generated_at": generated_at,
        }], columns=AUTO_LIVE_BATCH_SUMMARY_COLUMNS)
    atomic_to_csv(plan, paths["auto_live_batch_plan.csv"], index=False, encoding="utf-8-sig")
    atomic_to_csv(orders, paths["auto_live_batch_orders.csv"], index=False, encoding="utf-8-sig")
    atomic_to_csv(current, paths["auto_live_current_batch.csv"], index=False, encoding="utf-8-sig")
    atomic_to_csv(active, paths["auto_live_active_batch.csv"], index=False, encoding="utf-8-sig")
    atomic_to_csv(summary, paths["auto_live_batch_summary.csv"], index=False, encoding="utf-8-sig")
    return paths


def build_auto_live_batch_plan(
    config: dict[str, Any],
    source: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    generated_at = (now or datetime.now()).isoformat(timespec="seconds")
    limits = auto_live_risk_limits(config)
    source_hash = tickets_sha256(source)
    plan_id = mapping_sha256({"source_tickets_sha256": source_hash, "risk_limits": limits})[:20]
    plan_rows: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []

    if source.empty:
        return _empty_result(source, plan_id, source_hash, generated_at, "no_source_orders", "没有可规划的自动实盘工单。")

    working = source.copy().reset_index(drop=True)
    working["_source_order"] = range(1, len(working) + 1)
    values = ticket_risk_values(working)
    working["_risk_amount"] = values["amounts"].reindex(working.index).fillna(0.0)
    side_values = working.get("side", pd.Series("", index=working.index, dtype=str))
    status_values = working.get("status", pd.Series("", index=working.index, dtype=str))
    working["_side"] = side_values.astype(str).str.strip().str.lower()
    working["_status"] = status_values.astype(str).str.strip().str.lower()
    working["_priority"] = working["_side"].map({"sell": 0, "buy": 1}).fillna(2)
    working = working.sort_values(["_priority", "_source_order"], kind="stable").reset_index(drop=True)

    max_orders = int(limits["max_orders_per_batch"])
    max_buy = float(limits["max_batch_buy_amount"])
    max_gross = float(limits["max_batch_gross_amount"])
    max_single = float(limits["max_single_order_amount"])
    batches: list[list[int]] = []
    blocked: dict[int, str] = {}
    current_indices: list[int] = []
    current_buy = 0.0
    current_gross = 0.0

    for index, row in working.iterrows():
        amount = float(row["_risk_amount"])
        side = str(row["_side"])
        if str(row["_status"]) != "ready":
            blocked[index] = "ticket_not_ready"
            continue
        if side not in {"buy", "sell"}:
            blocked[index] = "side_not_supported"
            continue
        if not pd.notna(amount) or amount <= 0:
            blocked[index] = "invalid_order_amount"
            continue
        if amount > max_single:
            blocked[index] = "single_order_amount_exceeds_limit"
            continue

        next_buy = current_buy + (amount if side == "buy" else 0.0)
        next_gross = current_gross + amount
        fits = len(current_indices) < max_orders and next_buy <= max_buy and next_gross <= max_gross
        if current_indices and not fits:
            batches.append(current_indices)
            current_indices = []
            current_buy = 0.0
            current_gross = 0.0
            next_buy = amount if side == "buy" else 0.0
            next_gross = amount
            fits = max_orders > 0 and next_buy <= max_buy and next_gross <= max_gross
        if not fits:
            blocked[index] = "order_cannot_fit_empty_batch"
            continue
        current_indices.append(index)
        current_buy = next_buy
        current_gross = next_gross
    if current_indices:
        batches.append(current_indices)

    ledger = read_auto_live_submission_ledger(project_path(config["paths"]["output_dir"]))
    reservations = read_auto_live_risk_reservations(project_path(config["paths"]["output_dir"]))
    selected_batch_no = 0
    active_seen = False
    completed_count = 0
    active_count = 0
    batch_metadata: dict[int, dict[str, object]] = {}
    prior_buy, prior_gross = _active_daily_amounts(reservations, working)

    for batch_no, indices in enumerate(batches, start=1):
        batch = working.loc[indices].copy()
        batch_tickets = _source_columns(batch, source.columns)
        batch_hash = tickets_sha256(batch_tickets)
        batch_id = f"{plan_id}-{batch_no:03d}"
        match = _latest_ledger_match(ledger, batch_hash)
        run_id = str(match.get("run_id", "") or "") if match is not None else ""
        if match is not None and _run_is_terminal(project_path(config["paths"]["output_dir"]), run_id, len(batch)):
            batch_status = "completed"
            completed_count += 1
        elif match is not None:
            batch_status = "active"
            active_seen = True
            active_count += 1
        else:
            batch_values = ticket_risk_values(batch_tickets)
            projected_buy = prior_buy + float(batch_values["buy_amount"])
            projected_gross = prior_gross + float(batch_values["gross_amount"])
            within_daily = projected_buy <= float(limits["max_daily_buy_amount"]) and projected_gross <= float(limits["max_daily_gross_amount"])
            if active_seen:
                batch_status = "queued"
            elif selected_batch_no == 0 and within_daily:
                batch_status = "selected"
                selected_batch_no = batch_no
            elif not within_daily:
                batch_status = "deferred_daily_limit"
            else:
                batch_status = "queued"

        batch_values = ticket_risk_values(batch_tickets)
        selected = batch_status == "selected"
        metadata = {
            "plan_id": plan_id,
            "source_tickets_sha256": source_hash,
            "batch_no": batch_no,
            "batch_id": batch_id,
            "status": batch_status,
            "selected": selected,
            "order_count": len(batch),
            "sell_count": int(batch["_side"].eq("sell").sum()),
            "buy_count": int(batch["_side"].eq("buy").sum()),
            "sell_amount": round(float(batch_values["sell_amount"]), 2),
            "buy_amount": round(float(batch_values["buy_amount"]), 2),
            "gross_amount": round(float(batch_values["gross_amount"]), 2),
            "tickets_sha256": batch_hash,
            "source_run_id": run_id,
            "message": _batch_message(batch_status),
            "generated_at": generated_at,
        }
        batch_metadata[batch_no] = metadata
        plan_rows.append(metadata)

    index_to_batch = {index: batch_no for batch_no, indices in enumerate(batches, start=1) for index in indices}
    index_to_batch_order = {index: order for indices in batches for order, index in enumerate(indices, start=1)}
    for index, row in working.iterrows():
        batch_no = index_to_batch.get(index, 0)
        metadata = batch_metadata.get(batch_no, {})
        reason = blocked.get(index, "")
        order_rows.append(
            {
                "plan_id": plan_id,
                "source_tickets_sha256": source_hash,
                "source_order": int(row["_source_order"]),
                "batch_order": index_to_batch_order.get(index, 0),
                "batch_no": batch_no,
                "batch_id": metadata.get("batch_id", ""),
                "batch_status": metadata.get("status", "blocked"),
                "selected": bool(metadata.get("selected", False)),
                "ticket_id": row.get("ticket_id", ""),
                "date": row.get("date", ""),
                "symbol": row.get("symbol", ""),
                "side": row.get("side", ""),
                "quantity": row.get("quantity", ""),
                "limit_price": row.get("limit_price", ""),
                "estimated_amount": row.get("estimated_amount", row["_risk_amount"]),
                "status": row.get("status", ""),
                "planning_status": "blocked" if reason else "planned",
                "planning_reason": reason,
                "generated_at": generated_at,
            }
        )

    plan = pd.DataFrame(plan_rows, columns=AUTO_LIVE_BATCH_PLAN_COLUMNS)
    orders = pd.DataFrame(order_rows, columns=AUTO_LIVE_BATCH_ORDER_COLUMNS).sort_values("source_order", kind="stable")
    selected_indices = batches[selected_batch_no - 1] if selected_batch_no else []
    current = _source_columns(working.loc[selected_indices].copy(), source.columns)
    blocked_count = len(blocked)
    planned_count = sum(len(indices) for indices in batches)
    if blocked_count:
        summary_status = "blocked_invalid_orders"
        message = f"{blocked_count} 笔工单无法安全分批；为防止静默丢单，本周期禁止提交。"
        current = source.iloc[0:0].copy()
        selected_batch_no = 0
        if not plan.empty:
            plan.loc[plan["status"].eq("selected"), ["status", "selected", "message"]] = [
                "blocked_by_invalid_orders", False, "计划包含无法安全分批的工单，整批禁止提交。"
            ]
        if not orders.empty:
            orders.loc[orders["batch_status"].eq("selected"), ["batch_status", "selected"]] = [
                "blocked_by_invalid_orders", False
            ]
    elif active_count:
        summary_status = "waiting_active_batch"
        message = "前一批委托尚未全部终态，已保留后续批次且禁止推进。"
        current = source.iloc[0:0].copy()
        selected_batch_no = 0
    elif not batches:
        summary_status = "no_planned_orders"
        message = "没有可提交的批次。"
    elif selected_batch_no:
        summary_status = "ready"
        message = f"已选择第 {selected_batch_no}/{len(batches)} 批，共 {len(current)} 笔；其余工单保持排队。"
    elif completed_count == len(batches):
        summary_status = "completed"
        message = "当前计划的全部批次均已进入终态。"
    else:
        summary_status = "deferred_daily_limit"
        message = "剩余批次超过当日累计风险额度，已顺延且未丢弃。"

    queued_count = int(orders["batch_status"].isin(["queued", "deferred_daily_limit"]).sum()) if not orders.empty else 0
    summary = pd.DataFrame(
        [{
            "plan_id": plan_id,
            "status": summary_status,
            "source_order_count": len(source),
            "planned_order_count": planned_count,
            "blocked_order_count": blocked_count,
            "batch_count": len(batches),
            "completed_batch_count": completed_count,
            "active_batch_count": active_count,
            "selected_batch_no": selected_batch_no,
            "selected_order_count": len(current),
            "queued_order_count": queued_count,
            "message": message,
            "generated_at": generated_at,
        }],
        columns=AUTO_LIVE_BATCH_SUMMARY_COLUMNS,
    )
    return plan, orders, current, summary


def _empty_result(source: pd.DataFrame, plan_id: str, source_hash: str, generated_at: str, status: str, message: str):
    summary = pd.DataFrame([{
        "plan_id": plan_id, "status": status, "source_order_count": len(source), "planned_order_count": 0,
        "blocked_order_count": 0, "batch_count": 0, "completed_batch_count": 0, "active_batch_count": 0,
        "selected_batch_no": 0, "selected_order_count": 0, "queued_order_count": 0, "message": message,
        "generated_at": generated_at,
    }], columns=AUTO_LIVE_BATCH_SUMMARY_COLUMNS)
    return (
        pd.DataFrame(columns=AUTO_LIVE_BATCH_PLAN_COLUMNS),
        pd.DataFrame(columns=AUTO_LIVE_BATCH_ORDER_COLUMNS),
        source.copy(),
        summary,
    )


def _orders_to_source_batch(source: pd.DataFrame, orders: pd.DataFrame, status: str) -> pd.DataFrame:
    if source.empty or orders.empty or not {"batch_status", "source_order", "batch_order"}.issubset(orders.columns):
        return source.iloc[0:0].copy()
    selected = orders[orders["batch_status"].astype(str).eq(status)].sort_values(
        ["batch_no", "batch_order"], kind="stable"
    )
    positions = pd.to_numeric(selected["source_order"], errors="coerce").dropna().astype(int) - 1
    positions = positions[(positions >= 0) & (positions < len(source))]
    return source.iloc[positions.tolist()].copy().reset_index(drop=True)


def _source_columns(frame: pd.DataFrame, columns: pd.Index) -> pd.DataFrame:
    return frame[[column for column in columns if column in frame.columns]].copy().reset_index(drop=True)


def _latest_ledger_match(ledger: pd.DataFrame, batch_hash: str) -> pd.Series | None:
    if ledger.empty or "tickets_sha256" not in ledger.columns:
        return None
    matched = ledger[ledger["tickets_sha256"].astype(str).eq(batch_hash)]
    return matched.iloc[-1] if not matched.empty else None


def _run_is_terminal(output_dir: Path, run_id: str, expected: int) -> bool:
    if not run_id or expected <= 0:
        return False
    statuses = read_csv(output_dir / "auto_live_order_status.csv")
    if statuses.empty or "source_run_id" not in statuses.columns or "broker_status" not in statuses.columns:
        return False
    matched = statuses[statuses["source_run_id"].astype(str).eq(run_id)]
    covered = matched["ticket_id"].astype(str).nunique() >= expected if "ticket_id" in matched.columns else len(matched) >= expected
    return bool(covered and matched["broker_status"].astype(str).str.lower().isin(BROKER_ORDER_TERMINAL_STATUSES).all())


def _active_daily_amounts(reservations: pd.DataFrame, tickets: pd.DataFrame) -> tuple[float, float]:
    dates = ticket_risk_values(_source_columns(tickets, tickets.columns))["dates"]
    trade_date = dates[0] if len(dates) == 1 else ""
    if reservations.empty or not trade_date:
        return 0.0, 0.0
    active = reservations[
        reservations["trade_date"].astype(str).eq(trade_date)
        & ~reservations["status"].astype(str).str.lower().eq("released")
    ]
    buy = float(pd.to_numeric(active.get("buy_amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    gross = float(pd.to_numeric(active.get("gross_amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    return buy, gross


def _batch_message(status: str) -> str:
    return {
        "selected": "当前周期唯一允许提交的批次。",
        "queued": "等待前序批次全部终态后再放行。",
        "active": "该批次已提交，正在等待全部委托进入终态。",
        "completed": "该批次已提交且全部委托已进入终态。",
        "deferred_daily_limit": "超过当日累计风险额度，顺延至后续交易日重新评估。",
    }.get(status, status)
