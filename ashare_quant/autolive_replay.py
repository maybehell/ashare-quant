from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from .autolive import (
    BROKER_ORDER_STATUSES,
    CANCEL_ORDER_STATUSES,
    COMMAND_ADAPTER_SCHEMA,
    auto_live_config_sha256,
)
from .autolive_integrity import verify_adapter_request_integrity, verify_adapter_result_integrity
from .config import project_path
from .io_utils import atomic_to_csv


AUTO_LIVE_ADAPTER_REPLAY_COLUMNS = [
    "mode",
    "run_id",
    "check",
    "passed",
    "value",
    "threshold",
    "severity",
    "request_file",
    "result_file",
    "next_action",
    "message",
]
AUTO_LIVE_ADAPTER_REPLAY_SUMMARY_COLUMNS = [
    "status",
    "profile",
    "request_count",
    "result_count",
    "submit_count",
    "query_count",
    "cancel_count",
    "account_snapshot_count",
    "ticket_count",
    "passed_checks",
    "total_checks",
    "auto_live_config_sha256",
    "replay_dir",
    "generated_at",
    "message",
]
REPLAY_MODES = {"submit_orders", "query_orders", "cancel_orders", "account_snapshot"}
READ_ONLY_LIFECYCLE_GAPS = {
    "lifecycle_submit_orders_present",
    "lifecycle_query_orders_present",
    "lifecycle_cancel_orders_present",
    "lifecycle_submit_query_overlap",
    "lifecycle_query_broker_ids_stable",
    "lifecycle_submit_cancel_overlap",
    "lifecycle_cancel_broker_ids_stable",
}


def write_auto_live_adapter_replay_audit(
    config: dict[str, Any],
    replay_dir: str | Path | None = None,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = project_path(replay_dir) if replay_dir else output_dir
    audit, summary = build_auto_live_adapter_replay_audit(config, source_dir)
    audit_path = output_dir / "auto_live_adapter_replay_audit.csv"
    summary_path = output_dir / "auto_live_adapter_replay_summary.csv"
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    return {
        "auto_live_adapter_replay_audit.csv": audit_path,
        "auto_live_adapter_replay_summary.csv": summary_path,
    }


def build_auto_live_adapter_replay_audit(
    config: dict[str, Any],
    replay_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    requests: list[tuple[Path, dict[str, Any], Path, dict[str, Any]]] = []
    expected_hash = auto_live_config_sha256(config)
    ignored_historical_requests = 0

    def add(
        mode: str,
        run_id: str,
        check: str,
        passed: bool,
        value: object,
        threshold: object,
        severity: str,
        request_file: str,
        result_file: str,
        next_action: str,
        message: str,
    ) -> None:
        rows.append(
            {
                "mode": mode,
                "run_id": run_id,
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "request_file": request_file,
                "result_file": result_file,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    request_paths = sorted(replay_dir.glob("auto_live_*_request_*.json")) if replay_dir.exists() else []
    for request_path in request_paths:
        request = _read_json(request_path)
        mode = str(request.get("mode", ""))
        if mode not in REPLAY_MODES:
            continue
        run_id = str(request.get("run_id", ""))
        request_hash = str(request.get("auto_live_config_sha256", ""))
        if request_hash != expected_hash:
            ignored_historical_requests += 1
            continue
        result_name = Path(str(request.get("result_path", "") or "missing-result.json")).name
        result_path = replay_dir / result_name
        result = _read_json(result_path)
        requests.append((request_path, request, result_path, result))
        request_name = request_path.name
        add(mode, run_id, "request_json_valid", bool(request), "valid" if request else "invalid", "valid JSON object", "adapter_blocker", request_name, result_name, "修复或重新导出请求 JSON。", "回放请求必须是有效 JSON object。")
        add(mode, run_id, "request_schema_version", request.get("schema_version") == COMMAND_ADAPTER_SCHEMA, request.get("schema_version", "missing"), COMMAND_ADAPTER_SCHEMA, "adapter_blocker", request_name, result_name, "使用当前 command adapter 协议。", "请求协议版本必须匹配。")
        add(mode, run_id, "request_run_id_present", bool(run_id), run_id or "missing", "non-empty", "adapter_blocker", request_name, result_name, "补充稳定 run_id。", "每次券商调用必须有可追踪 run_id。")
        add(mode, run_id, "request_config_hash_match", request_hash == expected_hash, request_hash[:16] or "missing", expected_hash[:16], "adapter_blocker", request_name, result_name, "使用当前配置重新生成认证证据。", "回放证据必须绑定当前自动实盘配置。")
        add(mode, run_id, "result_file_present", result_path.exists(), result_name if result_path.exists() else "missing", "present", "adapter_blocker", request_name, result_name, "补齐对应结果 JSON。", "每个请求必须有对应结果文件。")
        add(mode, run_id, "result_json_valid", bool(result), "valid" if result else "invalid_or_empty", "valid JSON object", "adapter_blocker", request_name, result_name, "修复券商结果 JSON。", "券商回报必须是有效 JSON object。")
        add(mode, run_id, "result_schema_version", result.get("schema_version") == COMMAND_ADAPTER_SCHEMA, result.get("schema_version", "missing"), COMMAND_ADAPTER_SCHEMA, "adapter_blocker", request_name, result_name, "结果返回当前 schema_version。", "结果必须显式声明协议版本。")
        add(mode, run_id, "result_run_id_match", str(result.get("run_id", "")) == run_id, result.get("run_id", "missing"), run_id or "non-empty", "adapter_blocker", request_name, result_name, "结果回传请求 run_id。", "结果必须与请求逐次绑定，禁止误读旧文件。")
        result_hash = str(result.get("auto_live_config_sha256", ""))
        add(mode, run_id, "result_config_hash_match", result_hash == expected_hash, result_hash[:16] or "missing", expected_hash[:16], "adapter_blocker", request_name, result_name, "结果回传当前配置指纹。", "券商桥接器结果必须绑定当前配置。")
        request_integrity_passed, request_integrity_value = verify_adapter_request_integrity(request)
        add(mode, run_id, "request_integrity_valid", request_integrity_passed, request_integrity_value, "valid HMAC when required", "adapter_blocker", request_name, result_name, "提供未篡改且签名有效的请求证据。", "启用 HMAC 时，离线请求也必须通过完整性校验。")
        result_integrity_passed, result_integrity_value = verify_adapter_result_integrity(config, request, result)
        add(mode, run_id, "result_integrity_valid", result_integrity_passed, result_integrity_value, "request-bound valid HMAC when required", "adapter_blocker", request_name, result_name, "提供与请求绑定且签名有效的结果证据。", "启用 HMAC 时，离线结果必须与对应请求绑定且未被修改。")
        if mode in {"submit_orders", "query_orders", "cancel_orders"}:
            _audit_order_result(add, mode, run_id, request_name, result_name, request, result)
        elif mode == "account_snapshot":
            _audit_account_result(add, mode, run_id, request_name, result_name, result)

    modes = [str(request.get("mode", "")) for _, request, _, _ in requests]
    mode_counts = {mode: modes.count(mode) for mode in REPLAY_MODES}
    add(
        "lifecycle",
        "",
        "historical_replay_requests_ignored",
        True,
        ignored_historical_requests,
        "excluded from current-config certification",
        "info",
        "",
        "",
        "",
        "旧配置回放文件保留供审计，但不污染当前配置的认证结论。",
    )

    for mode in sorted(REPLAY_MODES):
        add("lifecycle", "", f"lifecycle_{mode}_present", mode_counts[mode] > 0, mode_counts[mode], ">0", "adapter_blocker", "", "", f"提供至少一组 {mode} 请求与回报。", "完整适配器认证必须覆盖提交、查询、撤单和账户快照。")

    run_ids = [str(request.get("run_id", "")) for _, request, _, _ in requests if request.get("run_id")]
    add("lifecycle", "", "lifecycle_run_ids_unique", len(run_ids) == len(set(run_ids)), f"unique={len(set(run_ids))};total={len(run_ids)}", "all unique", "adapter_blocker", "", "", "为每次桥接调用生成唯一 run_id。", "重复 run_id 会导致结果文件串批。")
    _audit_cross_lifecycle(add, requests)

    audit = pd.DataFrame(rows, columns=AUTO_LIVE_ADAPTER_REPLAY_COLUMNS)
    passed = int(audit["passed"].sum()) if not audit.empty else 0
    total = len(audit)
    environments = {
        str(result.get("environment", "")).strip().lower()
        for _, request, _, result in requests
        if request.get("mode") == "account_snapshot" and result
    }
    profile = "live" if environments == {"live"} else ("simulation" if environments and environments.issubset({"simulation", "paper", "mock"}) else "unknown")
    all_passed = total > 0 and passed == total
    all_modes = all(mode_counts[mode] > 0 for mode in REPLAY_MODES)
    failed_checks = set(audit.loc[~audit["passed"].astype(bool), "check"])
    read_only_live = (
        profile == "live"
        and mode_counts["account_snapshot"] > 0
        and all(mode_counts[mode] == 0 for mode in ("submit_orders", "query_orders", "cancel_orders"))
        and failed_checks.issubset(READ_ONLY_LIFECYCLE_GAPS)
    )
    if all_passed and all_modes and profile == "live":
        status = "certified_live"
        message = "适配器真实环境回放证据完整，协议与订单生命周期审计全部通过。"
    elif all_passed and all_modes and profile == "simulation":
        status = "certified_simulation"
        message = "模拟环境回放证据完整，但不能替代真实券商环境认证。"
    elif read_only_live:
        status = "certified_read_only_live"
        message = "真实账户只读回放已认证；提交、查询和撤单生命周期仍须通过受控 Canary 验证。"
    elif not all_modes:
        status = "incomplete"
        message = "回放证据未覆盖完整的提交、查询、撤单和账户快照生命周期。"
    else:
        status = "failed"
        message = "回放证据存在协议、绑定或业务约束失败项。"
    ticket_ids = {
        str(order.get("ticket_id", ""))
        for _, request, _, _ in requests
        for order in _orders(request)
        if str(order.get("ticket_id", ""))
    }
    summary = pd.DataFrame(
        [
            {
                "status": status,
                "profile": profile,
                "request_count": len(requests),
                "result_count": sum(1 for _, _, path, result in requests if path.exists() and result),
                "submit_count": mode_counts["submit_orders"],
                "query_count": mode_counts["query_orders"],
                "cancel_count": mode_counts["cancel_orders"],
                "account_snapshot_count": mode_counts["account_snapshot"],
                "ticket_count": len(ticket_ids),
                "passed_checks": passed,
                "total_checks": total,
                "auto_live_config_sha256": expected_hash,
                "replay_dir": str(replay_dir.resolve()),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "message": message,
            }
        ],
        columns=AUTO_LIVE_ADAPTER_REPLAY_SUMMARY_COLUMNS,
    )
    return audit, summary


def _audit_order_result(add, mode: str, run_id: str, request_name: str, result_name: str, request: dict[str, Any], result: dict[str, Any]) -> None:
    orders = _orders(request)
    results = _results(result)
    order_ids = [str(item.get("ticket_id", "")) for item in orders]
    result_ids = [str(item.get("ticket_id", "")) for item in results]
    add(mode, run_id, "order_request_nonempty", bool(orders), len(orders), ">0", "adapter_blocker", request_name, result_name, "提供至少一笔脱敏生命周期样本。", "提交、查询和撤单回放不能使用空订单伪造覆盖。")
    add(mode, run_id, "order_ticket_ids_present_unique", bool(order_ids) and all(order_ids) and len(order_ids) == len(set(order_ids)), f"unique={len(set(order_ids))};total={len(order_ids)}", "all present and unique", "adapter_blocker", request_name, result_name, "修正请求 ticket_id。", "客户端工单号必须完整且唯一。")
    add(mode, run_id, "result_rows_match_orders", len(results) == len(orders), f"results={len(results)};orders={len(orders)}", "equal", "adapter_blocker", request_name, result_name, "逐票返回结果。", "券商回报不能漏票或多票。")
    add(mode, run_id, "result_ticket_ids_match", set(result_ids) == set(order_ids) and len(result_ids) == len(set(result_ids)), f"matched={len(set(result_ids) & set(order_ids))};orders={len(set(order_ids))}", "same unique set", "adapter_blocker", request_name, result_name, "按 ticket_id 返回唯一结果。", "回报必须能逐票映射。")
    if mode == "submit_orders":
        statuses = [str(item.get("status", "")).lower() for item in results]
        allowed = {"submitted", "rejected", "failed", "unknown", "dry_run"}
        add(mode, run_id, "submit_statuses_valid", bool(statuses) and set(statuses).issubset(allowed), ",".join(sorted(set(statuses))) or "missing", ",".join(sorted(allowed)), "adapter_blocker", request_name, result_name, "返回受支持提交状态。", "提交状态必须可解释且失败关闭。")
        submitted_ids = [str(item.get("broker_order_id", "")) for item in results if str(item.get("status", "")).lower() == "submitted"]
        add(mode, run_id, "submitted_broker_order_ids_present_unique", all(submitted_ids) and len(submitted_ids) == len(set(submitted_ids)), f"unique={len(set(submitted_ids))};submitted={len(submitted_ids)}", "all submitted IDs present and unique", "adapter_blocker", request_name, result_name, "返回唯一券商委托号。", "已受理委托必须有稳定且唯一的券商委托号。")
    elif mode == "query_orders":
        statuses = [str(item.get("broker_status", "")).lower() for item in results]
        add(mode, run_id, "query_statuses_valid", bool(statuses) and set(statuses).issubset(BROKER_ORDER_STATUSES), ",".join(sorted(set(statuses))) or "missing", ",".join(sorted(BROKER_ORDER_STATUSES)), "adapter_blocker", request_name, result_name, "映射券商原始状态到受支持状态。", "查询状态必须来自固定状态机。")
        quantities = {str(item.get("ticket_id", "")): _number(item.get("quantity", 0)) for item in orders}
        valid_fills = all(0 <= _number(item.get("filled_quantity", 0)) <= quantities.get(str(item.get("ticket_id", "")), -1) for item in results)
        add(mode, run_id, "query_filled_quantity_within_order", valid_fills, "valid" if valid_fills else "out_of_range", "0 <= filled <= quantity", "adapter_blocker", request_name, result_name, "修正成交数量映射。", "累计成交数量不能为负或超过委托数量。")
    elif mode == "cancel_orders":
        statuses = [str(item.get("cancel_status", "")).lower() for item in results]
        add(mode, run_id, "cancel_statuses_valid", bool(statuses) and set(statuses).issubset(CANCEL_ORDER_STATUSES), ",".join(sorted(set(statuses))) or "missing", ",".join(sorted(CANCEL_ORDER_STATUSES)), "adapter_blocker", request_name, result_name, "映射撤单结果到受支持状态。", "撤单回报必须明确成功、失败或状态不明。")


def _audit_account_result(add, mode: str, run_id: str, request_name: str, result_name: str, result: dict[str, Any]) -> None:
    environment = str(result.get("environment", "")).strip().lower()
    account = result.get("account", {}) if isinstance(result.get("account", {}), dict) else {}
    account_id = str(account.get("account_id", "")).strip()
    money = {
        name: _number(account.get(name, -1))
        for name in ("cash", "available_cash", "market_value", "total_asset", "frozen_cash")
    }
    add(mode, run_id, "account_environment_declared", environment in {"live", "simulation", "paper", "mock"}, environment or "missing", "live/simulation/paper/mock", "adapter_blocker", request_name, result_name, "返回明确环境标识。", "环境标识防止把模拟账户误认为真实账户。")
    add(mode, run_id, "account_id_present", bool(account_id), account_id or "missing", "non-empty stable masked id", "adapter_blocker", request_name, result_name, "返回稳定脱敏账户号。", "账户快照必须绑定明确账户。")
    money_valid = all(math.isfinite(value) and value >= 0 for value in money.values())
    add(mode, run_id, "account_values_nonnegative", money_valid, ",".join(f"{name}={value:g}" for name, value in money.items()), "all finite and >= 0", "adapter_blocker", request_name, result_name, "修正资金字段映射。", "现金、资产和冻结金额必须是有限非负数。")
    cash = money["cash"]
    available = money["available_cash"]
    frozen = money["frozen_cash"]
    cash_fields_ok = money_valid and available + frozen <= cash + 1.0
    add(mode, run_id, "account_cash_fields_consistent", cash_fields_ok, f"cash={cash:g};available={available:g};frozen={frozen:g}", "available + frozen <= cash + 1 yuan", "adapter_blocker", request_name, result_name, "核对现金、可用和冻结资金字段。", "可用资金与冻结资金合计不能超过现金余额。")
    total_asset = money["total_asset"]
    market_value = money["market_value"]
    asset_gap = abs(total_asset - (cash + market_value))
    asset_tolerance = max(1.0, abs(total_asset) * 0.005)
    add(mode, run_id, "account_total_asset_consistent", money_valid and asset_gap <= asset_tolerance, f"gap={asset_gap:g}", f"<= {asset_tolerance:g}", "adapter_blocker", request_name, result_name, "核对总资产、现金和持仓市值字段。", "总资产应与现金加持仓市值基本一致。")

    positions = result.get("positions", [])
    positions_is_list = isinstance(positions, list)
    add(mode, run_id, "account_positions_list", positions_is_list, type(positions).__name__, "list", "adapter_blocker", request_name, result_name, "返回 positions 数组。", "空仓也必须返回空数组。")
    position_rows = positions if positions_is_list else []
    rows_are_objects = all(isinstance(item, dict) for item in position_rows)
    add(mode, run_id, "account_position_rows_objects", rows_are_objects, f"rows={len(position_rows)}", "all rows are objects", "adapter_blocker", request_name, result_name, "修正持仓数组元素。", "每个持仓明细必须是结构化对象。")
    normalized_rows = [item for item in position_rows if isinstance(item, dict)]
    symbols = [str(item.get("symbol", "") or "").strip() for item in normalized_rows]
    symbols_valid = rows_are_objects and all(
        len(symbol) == 6 and symbol.isdigit() for symbol in symbols
    ) and len(symbols) == len(set(symbols))
    add(mode, run_id, "account_position_symbols_valid_unique", symbols_valid, f"rows={len(symbols)};unique={len(set(symbols))}", "unique six-digit symbols", "adapter_blocker", request_name, result_name, "修正持仓代码或重复行。", "持仓代码必须是唯一的六位证券代码。")

    quantities_valid = rows_are_objects
    position_money_valid = rows_are_objects
    row_values_consistent = rows_are_objects
    position_total = 0.0
    max_row_gap = 0.0
    for item in normalized_rows:
        quantity = _number(item.get("quantity", -1))
        sellable = _number(item.get("sellable_quantity", -1))
        cost = _number(item.get("cost", -1))
        price = _number(item.get("last_price", -1))
        row_value = _number(item.get("market_value", -1))
        quantities_valid = quantities_valid and all(
            math.isfinite(value) for value in (quantity, sellable)
        ) and quantity > 0 and sellable >= 0 and sellable <= quantity and quantity.is_integer() and sellable.is_integer()
        position_money_valid = position_money_valid and all(
            math.isfinite(value) for value in (cost, price, row_value)
        ) and cost >= 0 and price > 0 and row_value > 0
        row_gap = abs(row_value - quantity * price)
        max_row_gap = max(max_row_gap, row_gap)
        row_values_consistent = row_values_consistent and row_gap <= max(1.0, abs(row_value) * 0.005)
        position_total += row_value
    add(mode, run_id, "account_position_quantities_valid", quantities_valid, f"rows={len(normalized_rows)}", "integer; 0 <= sellable <= quantity", "adapter_blocker", request_name, result_name, "核对持仓股数和可卖股数单位。", "持仓数量必须为正整数，可卖数量必须是范围内整数。")
    add(mode, run_id, "account_position_money_valid", position_money_valid, f"rows={len(normalized_rows)}", "cost >= 0; price and market value > 0", "adapter_blocker", request_name, result_name, "核对持仓成本、现价和市值字段。", "有效持仓必须具有非负成本、正现价和正市值。")
    add(mode, run_id, "account_position_row_values_consistent", row_values_consistent, f"max_gap={max_row_gap:g}", "<= max(1 yuan, 0.5% row market value)", "adapter_blocker", request_name, result_name, "核对股/手和元/万元单位。", "每行市值应与持仓数量乘以现价基本一致。")
    position_gap = abs(position_total - market_value)
    position_tolerance = max(1.0, abs(market_value) * 0.001)
    position_total_ok = rows_are_objects and money_valid and position_gap <= position_tolerance
    add(mode, run_id, "account_position_market_value_reconciles", position_total_ok, f"positions={position_total:g};summary={market_value:g};gap={position_gap:g}", f"<= {position_tolerance:g}", "adapter_blocker", request_name, result_name, "检查持仓是否漏行、重复或单位错误。", "持仓明细市值合计必须与账户汇总市值一致。")
    if "positions_count" in account:
        positions_count = _number(account.get("positions_count"))
        count_ok = math.isfinite(positions_count) and positions_count.is_integer() and int(positions_count) == len(normalized_rows)
    else:
        positions_count = float(len(normalized_rows))
        count_ok = rows_are_objects
    add(mode, run_id, "account_positions_count_consistent", count_ok, f"summary={positions_count:g};rows={len(normalized_rows)}", "summary equals parsed rows", "adapter_blocker", request_name, result_name, "核对持仓汇总行数。", "账户汇总持仓数必须与明细行数一致。")


def _audit_cross_lifecycle(add, requests) -> None:
    submitted: dict[str, str] = {}
    queried: dict[str, str] = {}
    canceled: dict[str, str] = {}
    for request_path, request, result_path, result in requests:
        mode = str(request.get("mode", ""))
        by_ticket = {str(item.get("ticket_id", "")): item for item in _results(result)}
        if mode == "submit_orders":
            for ticket_id, item in by_ticket.items():
                if str(item.get("status", "")).lower() == "submitted":
                    submitted[ticket_id] = str(item.get("broker_order_id", ""))
        elif mode == "query_orders":
            for ticket_id, item in by_ticket.items():
                queried[ticket_id] = str(item.get("broker_order_id", ""))
        elif mode == "cancel_orders":
            for ticket_id, item in by_ticket.items():
                canceled[ticket_id] = str(item.get("broker_order_id", ""))
    query_overlap = set(submitted) & set(queried)
    cancel_overlap = set(submitted) & set(canceled)
    query_stable = bool(query_overlap) and all(submitted[item] and submitted[item] == queried[item] for item in query_overlap)
    cancel_stable = bool(cancel_overlap) and all(submitted[item] and submitted[item] == canceled[item] for item in cancel_overlap)
    add("lifecycle", "", "lifecycle_submit_query_overlap", bool(query_overlap), len(query_overlap), ">0", "adapter_blocker", "", "", "提供同一 ticket_id 的提交与查询证据。", "查询能力必须用真实提交样本证明。")
    add("lifecycle", "", "lifecycle_query_broker_ids_stable", query_stable, f"stable={sum(1 for item in query_overlap if submitted[item] == queried[item])};overlap={len(query_overlap)}", "all overlap stable", "adapter_blocker", "", "", "查询时保留券商委托号。", "提交到查询的券商委托号不能变化。")
    add("lifecycle", "", "lifecycle_submit_cancel_overlap", bool(cancel_overlap), len(cancel_overlap), ">0", "adapter_blocker", "", "", "提供同一 ticket_id 的提交与撤单证据。", "撤单能力必须用已提交样本证明。")
    add("lifecycle", "", "lifecycle_cancel_broker_ids_stable", cancel_stable, f"stable={sum(1 for item in cancel_overlap if submitted[item] == canceled[item])};overlap={len(cancel_overlap)}", "all overlap stable", "adapter_blocker", "", "", "撤单时保留券商委托号。", "提交到撤单的券商委托号不能变化。")


def _orders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("orders", [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("results", [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _number(value: object) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(parsed) if not pd.isna(parsed) else -1.0
