from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .audit import mapping_sha256, orders_frame_sha256
from .account import export_account_state
from .config import _load_mapping, project_path
from .io_utils import atomic_to_csv
from .data import DataStore
from .order_audit import build_order_liquidity_audit, order_liquidity_audit_passed
from .order_integrity import (
    RPA_TICKET_INTEGRITY_FILE,
    verify_order_provenance_integrity,
    verify_rpa_ticket_integrity_manifest,
    write_rpa_ticket_integrity_manifest,
)
from .reconciliation import parse_passed_series, read_reconciliation, reconciliation_passed
from .strategy_health import strategy_health_allows_paper
from .strategy_activation import current_execution_epoch
from .validation import optional_dependency_status

RPA_COLUMNS = [
    "ticket_id",
    "date",
    "symbol",
    "side",
    "quantity",
    "limit_price",
    "estimated_amount",
    "status",
    "message",
]

PRE_TRADE_COLUMNS = ["check", "passed", "value", "threshold", "message"]
RPA_PREFLIGHT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
RPA_EXECUTABLE_FILE = "rpa_orders.csv"
RPA_ALL_FILE = "rpa_orders_all.csv"
RPA_BLOCKED_FILE = "rpa_blocked_orders.csv"
ORDER_LIQUIDITY_AUDIT_FILE = "order_liquidity_audit.csv"
ORDER_LIQUIDITY_ALL_AUDIT_FILE = "order_liquidity_all_audit.csv"




def write_rpa_preflight(
    config: dict[str, Any],
    ticket_path: str | Path | None = None,
    layout_path: str | Path = "configs/rpa_template.yaml",
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "rpa_preflight.csv"
    atomic_to_csv(build_rpa_preflight(config, ticket_path=ticket_path, layout_path=layout_path), path, index=False, encoding="utf-8-sig")
    return path


def build_rpa_preflight(
    config: dict[str, Any],
    ticket_path: str | Path | None = None,
    layout_path: str | Path = "configs/rpa_template.yaml",
    optional_dependencies: dict[str, bool] | None = None,
) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    tickets_path = Path(ticket_path) if ticket_path else output_dir / RPA_EXECUTABLE_FILE
    all_path = tickets_path.with_name(RPA_ALL_FILE)
    blocked_path = tickets_path.with_name(RPA_BLOCKED_FILE)
    pre_trade_path = tickets_path.with_name("rpa_pre_trade_checks.csv") if ticket_path else output_dir / "rpa_pre_trade_checks.csv"
    orders_path = output_dir / "orders.csv"
    layout_file = Path(layout_path) if Path(layout_path).is_absolute() else project_path(layout_path)

    tickets = read_csv_safely(tickets_path, dtype={"symbol": str})
    all_tickets = read_csv_safely(all_path, dtype={"symbol": str})
    blocked = read_csv_safely(blocked_path, dtype={"symbol": str})
    pre_trade = read_csv_safely(pre_trade_path)
    orders = read_orders(orders_path) if orders_path.exists() else pd.DataFrame()
    optional_dependencies = optional_dependency_status() if optional_dependencies is None else optional_dependencies

    rpa = config.get("rpa", {})
    execution = config.get("execution", {})
    require_click_dependency = bool(execution.get("require_rpa_click_dependency", False))
    orders_present = orders_path.exists()
    no_action_day = orders_present and orders.empty
    ready_count = count_ready_tickets(tickets)
    executable_blocked = count_blocked_tickets(tickets)
    omitted_blocked = len(blocked)
    source_count = len(all_tickets) if not all_tickets.empty else len(tickets)
    ticket_integrity_ok, ticket_integrity_reason = verify_rpa_ticket_integrity_manifest(
        config, tickets, tickets_path.with_name(RPA_TICKET_INTEGRITY_FILE)
    )
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

    final_confirm = bool(rpa.get("require_manual_final_confirm", True))
    never_submit = bool(rpa.get("never_click_submit", True))
    add(
        "rpa_safety_policy",
        final_confirm and never_submit,
        f"manual_confirm={final_confirm}, never_submit={never_submit}",
        "manual_confirm=True and never_submit=True",
        "safety_blocker",
        "恢复 rpa.require_manual_final_confirm=true 且 rpa.never_click_submit=true。",
        "RPA 只允许辅助填字段，禁止自动点击最终提交。",
    )
    add(
        "orders_file_present",
        orders_present,
        str(orders_path) if orders_present else "missing",
        "outputs/orders.csv exists",
        "execution_blocker",
        "先运行 python -m ashare_quant run --allow-stale 生成当日订单。",
        "RPA preflight 必须知道当前是否有待执行订单；空订单日可通过，但缺少订单文件不可通过。",
    )
    add(
        "rpa_ticket_file_present",
        no_action_day or tickets_path.exists(),
        "no_action_day" if no_action_day else str(tickets_path) if tickets_path.exists() else "missing",
        "rpa_orders.csv exists unless no-action day",
        "execution_blocker",
        "运行 python -m ashare_quant export-orders 重新导出 RPA 工单。",
        "有订单时必须生成 RPA 可执行工单文件。",
    )
    add(
        "rpa_ticket_integrity",
        no_action_day or ticket_integrity_ok,
        "no_action_day" if no_action_day else ticket_integrity_reason,
        "valid hash; HMAC required by live config",
        "execution_blocker",
        "重新运行 export-orders 生成与当前配置和订单一致的签名工单。",
        "自动实盘只能使用导出后未被修改的 RPA 工单。",
    )
    add(
        "rpa_ticket_status",
        no_action_day or (ready_count > 0 and executable_blocked == 0),
        "no_action_day" if no_action_day else f"source={source_count}, ready={ready_count}, executable_blocked={executable_blocked}",
        "no orders or ready>0 and executable_blocked=0",
        "execution_blocker",
        "处理 blocked 工单原因后重新运行 export-orders。",
        "进入填单前，当前可执行工单必须全部 ready。",
    )
    add(
        "blocked_ticket_file_reviewed",
        no_action_day or omitted_blocked == 0,
        "no_action_day" if no_action_day else omitted_blocked,
        0,
        "review_warning",
        "查看 outputs/rpa_blocked_orders.csv，确认这些订单是因金额、流动性或交易规则被有意省略。",
        "被风控省略的工单不会进入 rpa_orders.csv；该项提示复核，不默认阻断 ready 工单执行。",
    )
    pre_trade_present = not pre_trade.empty
    add(
        "pre_trade_checks_present",
        no_action_day or pre_trade_present,
        "no_action_day" if no_action_day else f"{len(pre_trade)} checks" if pre_trade_present else "missing",
        "rpa_pre_trade_checks.csv exists",
        "execution_blocker",
        "运行 python -m ashare_quant export-orders 生成下单前检查。",
        "RPA 下单前检查是账户、来源和风控证据的汇总。",
    )
    add(
        "pre_trade_checks_passed",
        no_action_day or pre_trade_checks_passed(pre_trade),
        "no_action_day" if no_action_day else readiness_count(pre_trade),
        "all checks passed",
        "execution_blocker",
        "查看 outputs/rpa_pre_trade_checks.csv 的失败项并修复。",
        "所有 RPA 下单前检查必须通过。",
    )
    if bool(execution.get("require_order_liquidity_audit", False)):
        audit = read_order_liquidity_audit(config) if "paths" in config and "output_dir" in config.get("paths", {}) else pd.DataFrame()
        passed, value = order_liquidity_audit_passed(audit)
        add(
            "order_liquidity_audit",
            no_action_day or passed,
            "no_action_day" if no_action_day else value,
            "all tickets within participation limits",
            "execution_blocker",
            "运行 python -m ashare_quant order-audit 并处理容量失败的订单。",
            "实盘执行前必须确认订单金额/成交量参与率没有超过上限。",
        )

    pyautogui_ok = bool(optional_dependencies.get("pyautogui", False))
    add(
        "pyautogui_dependency",
        pyautogui_ok or not require_click_dependency,
        "installed" if pyautogui_ok else "missing_optional" if not require_click_dependency else "missing_required",
        "installed when execution.require_rpa_click_dependency=true",
        "execution_blocker" if require_click_dependency else "click_blocker",
        "如需要模拟点击填单，运行 pip install -r requirements-rpa.txt。",
        "预览和审计不需要 pyautogui；只有启用点击填字段时才需要。",
    )

    layout_exists = layout_file.exists()
    layout_severity = "execution_blocker" if require_click_dependency else "click_blocker"
    add(
        "layout_file_exists",
        layout_exists,
        str(layout_file) if layout_exists else "missing",
        "layout yaml exists",
        layout_severity,
        "复制并校准 configs/rpa_template.yaml 中的券商客户端坐标。",
        "模拟点击填单需要经过人工校准的布局文件。",
    )
    layout: dict[str, Any] = {}
    layout_loaded = False
    if layout_exists:
        try:
            layout = _load_mapping(layout_file)
            layout_loaded = True
        except Exception as exc:  # noqa: BLE001 - convert parser errors into audit rows.
            add(
                "layout_parseable",
                False,
                type(exc).__name__,
                "valid JSON/YAML mapping",
                layout_severity,
                "修复 RPA 布局文件格式后重新运行 rpa-preflight。",
                "布局文件必须能被系统解析。",
            )
    if layout_exists and layout_loaded:
        add("layout_parseable", True, "ok", "valid JSON/YAML mapping", layout_severity, "", "布局文件可解析。")
        safety = layout.get("safety", {}) if isinstance(layout.get("safety", {}), dict) else {}
        layout_manual = bool(safety.get("require_manual_final_confirm", True))
        layout_never_submit = bool(safety.get("never_click_submit", True))
        add(
            "layout_safety_policy",
            layout_manual and layout_never_submit,
            f"manual_confirm={layout_manual}, never_submit={layout_never_submit}",
            "manual_confirm=True and never_submit=True",
            "safety_blocker",
            "恢复布局文件 safety.require_manual_final_confirm=true 且 never_click_submit=true。",
            "布局文件也必须禁止最终提交自动化。",
        )
        fields = layout.get("fields", {}) if isinstance(layout.get("fields", {}), dict) else {}
        required_fields = ["buy_tab", "sell_tab", "symbol", "price", "quantity"]
        missing_fields = [name for name in required_fields if name not in fields]
        coords_ok = not missing_fields and all(point_has_xy(fields.get(name)) for name in required_fields)
        add(
            "layout_required_fields",
            coords_ok,
            "ok" if coords_ok else ",".join(missing_fields) or "missing x/y",
            "buy_tab,sell_tab,symbol,price,quantity with x/y",
            layout_severity,
            "补齐布局文件 fields 下所有必要坐标。",
            "模拟点击填单需要买入页、卖出页、代码、价格、数量字段坐标。",
        )
        calibrated = coords_ok and all(point_is_calibrated(fields.get(name)) for name in required_fields)
        add(
            "layout_fields_calibrated",
            calibrated,
            "calibrated" if calibrated else "placeholder_or_zero_coordinates",
            "all required fields have non-zero calibrated coordinates",
            layout_severity,
            "用券商客户端实际坐标替换 configs/rpa_template.yaml 中的 0,0 占位值。",
            "默认模板坐标不能用于真实点击填单。",
        )
        broker = layout.get("broker", {}) if isinstance(layout.get("broker", {}), dict) else {}
        window_title = str(broker.get("window_title", "") or "").strip()
        add(
            "broker_window_title_configured",
            bool(window_title) and not looks_like_placeholder(window_title),
            window_title or "missing",
            "real broker window title keyword",
            layout_severity,
            "在布局文件 broker.window_title 中填写真实券商客户端窗口标题关键词。",
            "窗口标题用于人工校验当前点击目标是否为正确券商客户端。",
        )
    elif layout_exists:
        for check in ["layout_safety_policy", "layout_required_fields", "layout_fields_calibrated", "broker_window_title_configured"]:
            add(check, False, "layout_unparseable", "valid calibrated layout", layout_severity, "先修复布局文件解析错误。", "布局不可解析时无法继续检查。")

    try:
        preview = "no_action_day" if no_action_day else preview_rpa_orders(config, tickets_path)
        preview_passed = no_action_day or bool(preview.strip())
        preview_value = "no_action_day" if no_action_day else preview.splitlines()[0] if preview else "empty"
    except Exception as exc:  # noqa: BLE001 - audit should not raise for preview failures.
        preview_passed = False
        preview_value = type(exc).__name__
    add(
        "preview_renderable",
        preview_passed,
        preview_value,
        "rpa-preview returns a summary",
        "execution_blocker",
        "运行 python -m ashare_quant rpa-preview 查看具体错误。",
        "进入执行链前，工单必须能被预览和人工复核。",
    )
    return pd.DataFrame(rows, columns=RPA_PREFLIGHT_COLUMNS)


def export_rpa_orders(
    config: dict[str, Any],
    orders_path: str | Path | None = None,
    latest_only: bool = True,
    require_paper_source: bool = True,
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Path(orders_path) if orders_path else output_dir / "orders.csv"
    provenance: dict[str, str] = {}
    if require_paper_source:
        provenance = validate_paper_order_source(config, source)
    else:
        provenance = read_orders_provenance(output_dir)
    orders = read_orders(source)
    all_tickets = build_rpa_tickets(config, orders, latest_only=latest_only)
    if bool(config.get("rpa", {}).get("require_strategy_health", False)):
        health_passed, _ = strategy_health_allows_paper(read_strategy_health(config))
        if not health_passed and not all_tickets.empty:
            all_tickets = all_tickets.copy()
            all_tickets.loc[:, "status"] = "blocked"
            all_tickets.loc[:, "message"] = "strategy_health_paused"
    executable_tickets = executable_rpa_tickets(config, all_tickets)
    blocked = blocked_rpa_tickets(all_tickets)

    all_path = output_dir / RPA_ALL_FILE
    blocked_path = output_dir / RPA_BLOCKED_FILE
    path = output_dir / RPA_EXECUTABLE_FILE
    all_tickets.to_csv(all_path, index=False, encoding="utf-8-sig")
    blocked.to_csv(blocked_path, index=False, encoding="utf-8-sig")
    executable_tickets.to_csv(path, index=False, encoding="utf-8-sig")
    write_rpa_ticket_integrity_manifest(config, executable_tickets, provenance)

    if bool(config.get("execution", {}).get("require_order_liquidity_audit", False)):
        audit_all = build_order_liquidity_audit(config, all_tickets)
        audit_all.to_csv(output_dir / ORDER_LIQUIDITY_ALL_AUDIT_FILE, index=False, encoding="utf-8-sig")
        audit = build_order_liquidity_audit(config, executable_tickets)
        audit.to_csv(output_dir / ORDER_LIQUIDITY_AUDIT_FILE, index=False, encoding="utf-8-sig")
    if bool(config.get("rpa", {}).get("require_account_state_audit", False)):
        export_account_state(config)
    checks = build_pre_trade_checks(
        config,
        executable_tickets,
        provenance,
        require_paper_source=require_paper_source,
        source_ticket_count=len(all_tickets),
    )
    checks.to_csv(output_dir / "rpa_pre_trade_checks.csv", index=False, encoding="utf-8-sig")
    return path




def read_csv_safely(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except EmptyDataError:
        return pd.DataFrame()


def readiness_count(frame: pd.DataFrame) -> str:
    if frame.empty or "passed" not in frame.columns:
        return "missing"
    passed = parse_passed_series(frame["passed"])
    return f"{int(passed.sum())}/{len(frame)}"


def count_ready_tickets(tickets: pd.DataFrame) -> int:
    if tickets.empty or "status" not in tickets.columns:
        return 0
    return int((tickets["status"].astype(str).str.lower() == "ready").sum())


def count_blocked_tickets(tickets: pd.DataFrame) -> int:
    if tickets.empty or "status" not in tickets.columns:
        return 0
    return int((tickets["status"].astype(str).str.lower() != "ready").sum())


def point_has_xy(point: object) -> bool:
    if not isinstance(point, dict):
        return False
    return pd.notna(pd.to_numeric(point.get("x"), errors="coerce")) and pd.notna(pd.to_numeric(point.get("y"), errors="coerce"))


def point_is_calibrated(point: object) -> bool:
    if not point_has_xy(point):
        return False
    assert isinstance(point, dict)
    x = float(pd.to_numeric(point.get("x"), errors="coerce"))
    y = float(pd.to_numeric(point.get("y"), errors="coerce"))
    return x > 0 and y > 0


def looks_like_placeholder(value: str) -> bool:
    text = value.strip().lower()
    if not text:
        return True
    markers = ["keyword", "placeholder", "title", "关键词", "标题", "示例", "鍏抽敭", "鏍囬", "鍒稿晢"]
    return any(marker.lower() in text for marker in markers)

def validate_paper_order_source(config: dict[str, Any], orders_path: Path) -> dict[str, str]:
    output_dir = project_path(config["paths"]["output_dir"])
    provenance_path = output_dir / "orders_provenance.csv"
    if not provenance_path.exists():
        raise RuntimeError("缺少 orders_provenance.csv；拒绝导出 RPA 工单。请先运行 paper 生成经过门禁的次日订单。")
    provenance = pd.read_csv(provenance_path)
    values = provenance_values(provenance)
    integrity_ok, integrity_reason = verify_order_provenance_integrity(config, values)
    if not integrity_ok:
        raise RuntimeError(f"orders_provenance.csv 完整性校验失败：{integrity_reason}；请重新运行 paper。")
    if values.get("source") != "paper":
        raise RuntimeError("订单来源不是 paper；拒绝导出 RPA 工单。")
    if parse_provenance_bool(values.get("allow_weak_strategy")) is not False:
        raise RuntimeError("订单由弱门禁/研究模式生成或来源标记不完整；拒绝导出 RPA 工单。请等待实盘门禁通过后重新运行 paper。")
    expected_config_hash = mapping_sha256(config)
    provenance_config_hash = values.get("config_sha256", "")
    if not provenance_config_hash or provenance_config_hash != expected_config_hash:
        raise RuntimeError("orders.csv 不是由当前配置生成；请重新运行 paper/practical 后再导出 RPA 工单。")
    try:
        execution_epoch = current_execution_epoch(config)
    except RuntimeError as exc:
        raise RuntimeError(f"当前执行策略纪元不可验证：{exc}") from exc
    epoch_fields = {
        "execution_epoch_id": execution_epoch["execution_epoch_id"],
        "execution_mode": execution_epoch["execution_mode"],
        "execution_candidate_id": execution_epoch["execution_candidate_id"],
        "execution_strategy_hash": execution_epoch["execution_strategy_hash"],
    }
    mismatched_epoch_fields = [
        name
        for name, expected in epoch_fields.items()
        if (
            (name != "execution_candidate_id" and not values.get(name))
            or values.get(name, "") != expected
        )
    ]
    if mismatched_epoch_fields:
        raise RuntimeError(
            "orders.csv 的执行策略纪元已失效："
            + ",".join(mismatched_epoch_fields)
            + "；请使用当前策略重新运行 paper/practical。"
        )
    current_strategy = str(config.get("strategy", {}).get("name", ""))
    provenance_strategy = values.get("strategy_name", "")
    if not provenance_strategy or provenance_strategy != current_strategy:
        raise RuntimeError("orders.csv 的策略名与当前配置不一致；请使用当前策略重新运行 paper/practical。")
    if orders_path.resolve() != (output_dir / "orders.csv").resolve():
        expected = str(output_dir / "orders.csv")
        raise RuntimeError(f"当前 provenance 只对应默认订单文件 {expected}；如需导出自定义订单源，请使用显式绕过参数。")
    orders = read_orders(orders_path)
    expected_count = int(values.get("order_count", "0") or 0)
    if len(orders) != expected_count:
        raise RuntimeError("orders.csv 与 orders_provenance.csv 的订单数量不一致；请重新运行 paper。")
    expected_hash = values.get("orders_sha256", "")
    actual_hash = orders_frame_sha256(orders)
    if not expected_hash or expected_hash != actual_hash:
        raise RuntimeError("orders.csv 与 orders_provenance.csv 的订单哈希不一致；请重新运行 paper。")
    if not orders.empty:
        execution_date = values.get("execution_date", "")
        latest_order_date = max(orders["date"]).isoformat()
        if execution_date and latest_order_date != execution_date:
            raise RuntimeError("orders.csv 与 orders_provenance.csv 的执行日期不一致；请重新运行 paper。")
    return values


def read_orders_provenance(output_dir: Path) -> dict[str, str]:
    provenance_path = output_dir / "orders_provenance.csv"
    if not provenance_path.exists():
        return {}
    return provenance_values(pd.read_csv(provenance_path))


def provenance_values(provenance: pd.DataFrame) -> dict[str, str]:
    if provenance.empty or not {"item", "value"}.issubset(provenance.columns):
        return {}
    return {str(row.item): "" if pd.isna(row.value) else str(row.value) for row in provenance.itertuples(index=False)}


def parse_provenance_bool(value: str | None) -> bool | None:
    text = "" if value is None else str(value).strip().lower()
    if text in {"false", "0", "no"}:
        return False
    if text in {"true", "1", "yes"}:
        return True
    return None


def read_orders(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "symbol", "side", "quantity", "reason"])
    orders = pd.read_csv(path, dtype={"symbol": str})
    if orders.empty:
        return orders
    orders["date"] = pd.to_datetime(orders["date"]).dt.date
    orders["symbol"] = orders["symbol"].astype(str).str.zfill(6)
    orders["side"] = orders["side"].astype(str).str.lower()
    orders["quantity"] = pd.to_numeric(orders["quantity"], errors="coerce").fillna(0).astype(int)
    return orders


def build_rpa_tickets(config: dict[str, Any], orders: pd.DataFrame, latest_only: bool = True) -> pd.DataFrame:
    if orders.empty:
        return pd.DataFrame(columns=RPA_COLUMNS)
    usable = orders.copy()
    if latest_only:
        latest_date = max(usable["date"])
        usable = usable[usable["date"] == latest_date].copy()

    store = DataStore.from_config(config)
    _, prices, _ = store.load_market_data()
    price_map = latest_close_by_symbol(prices)
    latest_market_date = max(prices["date"]) if not prices.empty else None
    rows = []
    for order in usable.itertuples(index=False):
        price = float(price_map.get(str(order.symbol), 0.0))
        amount = price * int(order.quantity)
        status, message = validate_rpa_order(
            config,
            str(order.side),
            int(order.quantity),
            price,
            amount,
            order_date=order.date,
            latest_market_date=latest_market_date,
            current_date=date.today(),
        )
        rows.append(
            {
                "ticket_id": ticket_id(str(order.date), str(order.symbol), str(order.side), int(order.quantity), price),
                "date": order.date,
                "symbol": str(order.symbol).zfill(6),
                "side": str(order.side).lower(),
                "quantity": int(order.quantity),
                "limit_price": round(price, 2),
                "estimated_amount": round(amount, 2),
                "status": status,
                "message": message,
            }
        )
    tickets = pd.DataFrame(rows, columns=RPA_COLUMNS)
    return enforce_daily_amount_limit(config, tickets)


def executable_rpa_tickets(config: dict[str, Any], tickets: pd.DataFrame) -> pd.DataFrame:
    if tickets.empty:
        return pd.DataFrame(columns=RPA_COLUMNS)
    if not bool(config.get("rpa", {}).get("export_ready_only", True)):
        return tickets.copy().reset_index(drop=True)
    if "status" not in tickets.columns:
        return pd.DataFrame(columns=RPA_COLUMNS)
    return tickets[tickets["status"].astype(str).str.lower() == "ready"].copy().reset_index(drop=True)


def blocked_rpa_tickets(tickets: pd.DataFrame) -> pd.DataFrame:
    if tickets.empty or "status" not in tickets.columns:
        return pd.DataFrame(columns=RPA_COLUMNS)
    return tickets[tickets["status"].astype(str).str.lower() != "ready"].copy().reset_index(drop=True)


def enforce_daily_amount_limit(config: dict[str, Any], tickets: pd.DataFrame) -> pd.DataFrame:
    if tickets.empty:
        return tickets
    max_daily = float(config.get("rpa", {}).get("max_daily_order_amount", 500000))
    out = tickets.copy()
    if max_daily <= 0:
        mask = (out["status"] == "ready") & (out["side"] == "buy")
        out.loc[mask, "status"] = "blocked"
        out.loc[mask, "message"] = "exceeds_daily_order_amount_limit"
        return out
    for _, group in out.groupby("date", sort=False):
        used = 0.0
        ready_buy = group[(group["status"] == "ready") & (group["side"] == "buy")]
        for idx, row in ready_buy.iterrows():
            amount = float(pd.to_numeric(row.get("estimated_amount", 0.0), errors="coerce") or 0.0)
            if used + amount > max_daily:
                out.loc[idx, "status"] = "blocked"
                out.loc[idx, "message"] = "exceeds_daily_order_amount_limit"
            else:
                used += amount
    return out


def build_pre_trade_checks(
    config: dict[str, Any],
    tickets: pd.DataFrame,
    provenance: dict[str, str] | None = None,
    require_paper_source: bool = True,
    source_ticket_count: int | None = None,
) -> pd.DataFrame:
    provenance = provenance or {}
    rpa = config.get("rpa", {})
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    source = provenance.get("source", "")
    allow_weak = parse_provenance_bool(provenance.get("allow_weak_strategy"))
    execution_epoch_required = "paths" in config and "output_dir" in config.get("paths", {})
    try:
        execution_epoch = current_execution_epoch(config)
        epoch_fields = {
            "execution_epoch_id": execution_epoch["execution_epoch_id"],
            "execution_mode": execution_epoch["execution_mode"],
            "execution_candidate_id": execution_epoch["execution_candidate_id"],
            "execution_strategy_hash": execution_epoch["execution_strategy_hash"],
        }
        epoch_mismatches = [
            name
            for name, expected in epoch_fields.items()
            if (
                (name != "execution_candidate_id" and not provenance.get(name))
                or provenance.get(name, "") != expected
            )
        ]
        execution_epoch_ok = not epoch_mismatches
        execution_epoch_value = "matched" if execution_epoch_ok else ",".join(epoch_mismatches)
    except RuntimeError as exc:
        execution_epoch_ok = not execution_epoch_required
        execution_epoch_value = f"{type(exc).__name__}:{exc}"
    provenance_integrity_ok, provenance_integrity_reason = verify_order_provenance_integrity(config, provenance)
    ticket_integrity_ok, ticket_integrity_reason = (
        verify_rpa_ticket_integrity_manifest(config, tickets)
        if "paths" in config and "output_dir" in config.get("paths", {})
        else (not bool(rpa.get("require_order_provenance_hmac", False)), "output_dir_missing")
    )
    parsed_order_count = pd.to_numeric(provenance.get("order_count", 0), errors="coerce")
    order_count = 0 if pd.isna(parsed_order_count) else int(parsed_order_count)
    ready_count = int((tickets["status"] == "ready").sum()) if not tickets.empty and "status" in tickets else 0
    blocked_count = int((tickets["status"] != "ready").sum()) if not tickets.empty and "status" in tickets else 0
    ticket_source_count = len(tickets) if source_ticket_count is None else int(source_ticket_count)
    max_single = float(rpa.get("max_single_order_amount", 100000))
    max_daily = float(rpa.get("max_daily_order_amount", 500000))
    max_ticket_amount = max_numeric(tickets, "estimated_amount")
    ready_buy_amount = ready_buy_amount_by_day(tickets)

    if bool(rpa.get("require_account_reconciliation", False)):
        reconciliation = read_reconciliation(config) if "paths" in config and "output_dir" in config.get("paths", {}) else pd.DataFrame()
        passed = reconciliation_passed(reconciliation)
        value = "missing" if reconciliation.empty else f"{int(parse_passed_series(reconciliation['passed']).sum())}/{len(reconciliation)}"
        add("account_reconciliation", passed, value, "all checks passed", "账户、持仓、订单来源和 SQLite 状态库对账必须全部通过。")

    if bool(rpa.get("require_account_state_audit", False)):
        audit = read_account_state_audit(config) if "paths" in config and "output_dir" in config.get("paths", {}) else pd.DataFrame()
        passed = account_state_audit_passed(audit)
        value = "missing" if audit.empty else f"{int(parse_passed_series(audit['passed']).sum())}/{len(audit)}"
        add("account_state_audit", passed, value, "all checks passed", "SQLite 当前账户状态、T+1 锁定数量、现金和估值覆盖必须先通过审计。")

    if bool(rpa.get("require_strategy_health", False)):
        health = read_strategy_health(config) if "paths" in config and "output_dir" in config.get("paths", {}) else pd.DataFrame()
        passed, value = strategy_health_allows_paper(health)
        add("strategy_health_guard", passed, value, "paper_orders_allowed=True", "策略健康监控必须允许 paper/RPA 工单链路继续；若策略退化，先暂停导出。")

    if bool(config.get("execution", {}).get("require_order_liquidity_audit", False)):
        audit = read_order_liquidity_audit(config) if "paths" in config and "output_dir" in config.get("paths", {}) else pd.DataFrame()
        passed, value = order_liquidity_audit_passed(audit)
        add("order_liquidity_audit", passed, value, "all tickets within participation limits", "每张工单必须通过历史成交量/成交额容量审计，避免相对流动性过大的订单。")

    add("rpa_manual_final_confirm", bool(rpa.get("require_manual_final_confirm", True)), rpa.get("require_manual_final_confirm", True), True, "RPA 只能辅助填字段，最终提交必须人工确认。")
    add("rpa_never_click_submit", bool(rpa.get("never_click_submit", True)), rpa.get("never_click_submit", True), True, "RPA 配置必须禁止点击最终提交按钮。")
    add("paper_order_source", source == "paper", source or "missing", "paper", "RPA 工单必须来自经过门禁的 paper 订单。")
    add("orders_provenance_integrity", provenance_integrity_ok, provenance_integrity_reason, "valid hash; HMAC required by live config", "真实提交配置必须验证订单来源 HMAC；模拟配置至少校验来源哈希。")
    add("execution_epoch_current", execution_epoch_ok, execution_epoch_value, "matched", "订单必须属于当前策略激活/回退纪元；旧纪元订单不可填单。")
    add("rpa_ticket_integrity", ticket_integrity_ok, ticket_integrity_reason, "valid hash; HMAC required by live config", "导出后的 RPA 工单必须与签名清单、当前配置和订单来源完全一致。")
    add("strong_strategy_provenance", allow_weak is False, "" if allow_weak is None else allow_weak, False, "弱门禁或研究模式生成的订单不能执行。")
    add("orders_provenance_count", order_count == ticket_source_count, f"{ticket_source_count}/{order_count}", "source tickets/order_count match", "RPA 候选工单数量必须与订单来源记录一致；可执行文件可以只保留 ready 工单。")
    add("ready_ticket_count", ready_count > 0, ready_count, ">0", "至少需要一条可执行工单。")
    add("blocked_ticket_count", blocked_count == 0, blocked_count, 0, "存在被风控拦截的工单时，不允许执行 RPA 填单。")
    add("max_single_ticket_amount", max_ticket_amount <= max_single, max_ticket_amount, max_single, "单笔工单金额必须不超过 RPA 单笔上限。")
    add("max_daily_ready_buy_amount", ready_buy_amount <= max_daily, ready_buy_amount, max_daily, "当日可执行买入金额必须不超过 RPA 单日上限。")
    if not require_paper_source:
        add("verified_export_mode", False, "allow_unverified_source", "verified paper source", "当前工单使用了研究/调试绕过参数，不能执行 RPA 填单。")
    return pd.DataFrame(rows, columns=PRE_TRADE_COLUMNS)


def max_numeric(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return float(values.max()) if not values.empty else 0.0


def ready_buy_amount_by_day(tickets: pd.DataFrame) -> float:
    if tickets.empty or not {"status", "side", "estimated_amount"}.issubset(tickets.columns):
        return 0.0
    ready_buys = tickets[(tickets["status"] == "ready") & (tickets["side"].astype(str).str.lower() == "buy")]
    return float(pd.to_numeric(ready_buys["estimated_amount"], errors="coerce").fillna(0.0).sum())


def read_account_state_audit(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    path = output_dir / "account_state_audit.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def read_strategy_health(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    path = output_dir / "strategy_health.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def read_order_liquidity_audit(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    path = output_dir / ORDER_LIQUIDITY_AUDIT_FILE
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def account_state_audit_passed(frame: pd.DataFrame) -> bool:
    return not frame.empty and "passed" in frame and bool(parse_passed_series(frame["passed"]).all())


def pre_trade_checks_passed(checks: pd.DataFrame) -> bool:
    return not checks.empty and bool(checks["passed"].astype(bool).all())


def failed_pre_trade_reasons(checks: pd.DataFrame) -> str:
    failed = checks[~checks["passed"].astype(bool)] if not checks.empty and "passed" in checks else checks
    if failed.empty:
        return ""
    return "; ".join(f"{row.check}: {row.message} (value={row.value}, threshold={row.threshold})" for row in failed.itertuples(index=False))


def latest_close_by_symbol(prices: pd.DataFrame) -> dict[str, float]:
    if prices.empty:
        return {}
    latest = prices.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)
    return dict(zip(latest["symbol"].astype(str), pd.to_numeric(latest["close"], errors="coerce")))


def validate_rpa_order(
    config: dict[str, Any],
    side: str,
    quantity: int,
    price: float,
    amount: float,
    order_date: Any | None = None,
    latest_market_date: Any | None = None,
    current_date: Any | None = None,
) -> tuple[str, str]:
    cfg = config.get("rpa", {})
    if order_date is not None and current_date is not None:
        if pd.to_datetime(order_date).date() < pd.to_datetime(current_date).date():
            return "blocked", "stale_order_date"
    if order_date is not None and latest_market_date is not None:
        if pd.to_datetime(order_date).date() < pd.to_datetime(latest_market_date).date():
            return "blocked", "stale_order_date"
    if side not in {"buy", "sell"}:
        return "blocked", "unsupported_side"
    if quantity <= 0:
        return "blocked", "non_positive_quantity"
    if price <= 0:
        return "blocked", "missing_reference_price"
    if side == "buy" and quantity % int(config["trading"]["lot_size"]) != 0:
        return "blocked", "buy_quantity_not_lot_size"
    max_single = float(cfg.get("max_single_order_amount", 100000))
    if amount > max_single:
        return "blocked", "exceeds_single_order_amount_limit"
    return "ready", "manual_confirm_required"


def ticket_id(date: str, symbol: str, side: str, quantity: int, price: float) -> str:
    payload = f"{date}|{symbol}|{side}|{quantity}|{price:.4f}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def preview_rpa_orders(config: dict[str, Any], ticket_path: str | Path | None = None) -> str:
    output_dir = project_path(config["paths"]["output_dir"])
    path = Path(ticket_path) if ticket_path else output_dir / RPA_EXECUTABLE_FILE
    tickets = pd.read_csv(path, dtype={"symbol": str}) if path.exists() else pd.DataFrame(columns=RPA_COLUMNS)
    blocked_path = path.with_name(RPA_BLOCKED_FILE)
    omitted = pd.read_csv(blocked_path, dtype={"symbol": str}) if blocked_path.exists() else pd.DataFrame(columns=RPA_COLUMNS)
    if tickets.empty:
        lines = ["No executable RPA tickets found. Run `aq export-orders` first."]
        if not omitted.empty:
            lines.append(blocked_reason_summary(omitted, prefix="Blocked/omitted tickets"))
        return "\n".join(lines)
    ready = int((tickets["status"] == "ready").sum())
    blocked = int((tickets["status"] != "ready").sum())
    total_amount = float(pd.to_numeric(tickets["estimated_amount"], errors="coerce").fillna(0).sum())
    lines = [f"Executable RPA tickets: {len(tickets)} ready={ready} blocked={blocked} estimated_amount={total_amount:.2f}"]
    if not omitted.empty:
        omitted_amount = float(pd.to_numeric(omitted.get("estimated_amount", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        lines.append(f"Omitted blocked tickets: {len(omitted)} estimated_amount={omitted_amount:.2f}")
    checks_path = path.with_name("rpa_pre_trade_checks.csv") if ticket_path else output_dir / "rpa_pre_trade_checks.csv"
    if checks_path.exists():
        checks = pd.read_csv(checks_path)
        passed = int(checks["passed"].astype(bool).sum()) if not checks.empty and "passed" in checks else 0
        failed = int(len(checks) - passed)
        lines.append(f"Pre-trade checks: passed={passed} failed={failed}")
        if failed:
            lines.append(f"Pre-trade failed: {failed_pre_trade_reasons(checks)}")
    if blocked:
        lines.append(blocked_reason_summary(tickets, prefix="Blocked reasons"))
    if not omitted.empty:
        lines.append(blocked_reason_summary(omitted, prefix="Omitted reasons"))
    if ready == 0:
        lines.append("No ready tickets. Run `aq paper` after refreshing data if you need current next-day orders.")
    lines.append(tickets.head(20).to_string(index=False))
    return "\n".join(lines)


def blocked_reason_summary(tickets: pd.DataFrame, prefix: str = "Blocked reasons") -> str:
    if tickets.empty or "message" not in tickets:
        return f"{prefix}: none"
    reason_counts = tickets["message"].value_counts().to_dict()
    reason_text = ", ".join(f"{reason}={count}" for reason, count in reason_counts.items())
    return f"{prefix}: {reason_text}"


def fill_rpa_ticket(
    config: dict[str, Any],
    ticket_path: str | Path | None,
    ticket_id_value: str | None,
    index: int,
    layout_path: str | Path,
    execute: bool = False,
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    path = Path(ticket_path) if ticket_path else output_dir / "rpa_orders.csv"
    tickets = pd.read_csv(path, dtype={"symbol": str})
    if execute:
        if bool(config.get("execution", {}).get("require_order_liquidity_audit", False)):
            audit = build_order_liquidity_audit(config, tickets)
            audit.to_csv(output_dir / ORDER_LIQUIDITY_AUDIT_FILE, index=False, encoding="utf-8-sig")
        if bool(config.get("rpa", {}).get("require_account_state_audit", False)):
            export_account_state(config)
        checks = build_pre_trade_checks(
            config,
            tickets,
            read_orders_provenance(output_dir),
            require_paper_source=True,
            source_ticket_count=source_ticket_count_for_precheck(output_dir, tickets),
        )
        checks.to_csv(output_dir / "rpa_pre_trade_checks.csv", index=False, encoding="utf-8-sig")
        if not pre_trade_checks_passed(checks):
            raise RuntimeError(f"RPA 下单前检查未通过，已阻止填单。{failed_pre_trade_reasons(checks)}")
    ticket = select_ticket(tickets, ticket_id_value, index)
    layout = _load_mapping(Path(layout_path))
    log_path = output_dir / "rpa_action_log.csv"
    if ticket["status"] != "ready":
        raise RuntimeError(f"Ticket is not ready: {ticket['message']}")
    if execute:
        execute_fill(ticket, layout)
        action = "filled_fields"
    else:
        action = "dry_run"
    append_action_log(log_path, ticket, action)
    return log_path


def source_ticket_count_for_precheck(output_dir: Path, fallback_tickets: pd.DataFrame) -> int:
    all_path = output_dir / RPA_ALL_FILE
    if not all_path.exists():
        return len(fallback_tickets)
    try:
        return len(pd.read_csv(all_path, dtype={"symbol": str}))
    except EmptyDataError:
        return 0


def select_ticket(tickets: pd.DataFrame, ticket_id_value: str | None, index: int) -> dict[str, Any]:
    if ticket_id_value:
        selected = tickets[tickets["ticket_id"] == ticket_id_value]
        if selected.empty:
            raise ValueError(f"Unknown ticket_id: {ticket_id_value}")
        row = selected.iloc[0]
    else:
        row = tickets.iloc[index]
    return row.to_dict()


def execute_fill(ticket: dict[str, Any], layout: dict[str, Any]) -> None:
    try:
        import pyautogui  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("pyautogui is required for --execute. Install it in .venv first.") from exc

    safety = layout.get("safety", {})
    if not safety.get("require_manual_final_confirm", True) or not safety.get("never_click_submit", True):
        raise RuntimeError("Unsafe RPA layout: final submit automation is not allowed.")
    fields = layout["fields"]
    pause = float(safety.get("pause_seconds", 0.2))

    tab = fields["buy_tab"] if ticket["side"] == "buy" else fields["sell_tab"]
    click_point(pyautogui, tab, pause)
    click_and_type(pyautogui, fields["symbol"], str(ticket["symbol"]).zfill(6), pause)
    click_and_type(pyautogui, fields["price"], f"{float(ticket['limit_price']):.2f}", pause)
    click_and_type(pyautogui, fields["quantity"], str(int(ticket["quantity"])), pause)


def click_point(pyautogui: Any, point: dict[str, Any], pause: float) -> None:
    pyautogui.click(int(point["x"]), int(point["y"]))
    pyautogui.sleep(pause)


def click_and_type(pyautogui: Any, point: dict[str, Any], value: str, pause: float) -> None:
    click_point(pyautogui, point, pause)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.write(value, interval=0.01)
    pyautogui.sleep(pause)


def append_action_log(path: Path, ticket: dict[str, Any], action: str) -> None:
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ticket_id": ticket["ticket_id"],
        "symbol": str(ticket["symbol"]).zfill(6),
        "side": ticket["side"],
        "quantity": int(ticket["quantity"]),
        "limit_price": float(ticket["limit_price"]),
        "action": action,
        "manual_final_confirm_required": True,
    }
    frame = pd.DataFrame([row])
    if path.exists():
        previous = pd.read_csv(path, dtype={"symbol": str})
        frame = pd.concat([previous, frame], ignore_index=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
