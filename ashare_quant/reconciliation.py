from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path


RECONCILIATION_COLUMNS = ["check", "passed", "value", "threshold", "message"]


def build_account_reconciliation(
    config: dict[str, Any],
    equity: pd.DataFrame,
    orders: pd.DataFrame,
    positions: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    provenance: dict[str, Any] | None = None,
) -> pd.DataFrame:
    tolerance = float(config.get("reconciliation", {}).get("cash_tolerance", 1.0))
    lot_size = int(config.get("trading", {}).get("lot_size", 100))
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    equity = equity.copy()
    orders = orders.copy()
    positions = positions.copy()
    trades = trades.copy() if trades is not None else pd.DataFrame()
    provenance = provenance or {}

    has_equity = not equity.empty and {"date", "cash", "market_value", "equity"}.issubset(equity.columns)
    add("paper_equity_snapshot_present", has_equity, "present" if has_equity else "missing", "present", "必须存在模拟盘权益快照。")

    if has_equity:
        snapshot = equity.iloc[-1]
        cash = to_float(snapshot.get("cash"))
        market_value = to_float(snapshot.get("market_value"))
        total_equity = to_float(snapshot.get("equity"))
        diff = abs(total_equity - cash - market_value)
        add("equity_cash_plus_market_value", diff <= tolerance, round(diff, 4), f"<= {tolerance}", "权益必须等于现金加持仓市值。")
    else:
        cash = market_value = total_equity = 0.0
        snapshot = pd.Series(dtype=object)

    position_checks = validate_positions(positions, tolerance)
    add(
        "position_rows_valid",
        position_checks["valid"],
        position_checks["value"],
        "quantity>=0, 0<=sellable<=quantity, market_value=quantity*close",
        "持仓行必须满足数量、可卖数量和市值一致性约束。",
    )
    position_market_value = sum_numeric(positions, "market_value")
    if has_equity:
        diff = abs(position_market_value - market_value)
        add("positions_market_value_sum", diff <= tolerance, round(diff, 4), f"<= {tolerance}", "持仓市值合计必须等于权益快照中的市值。")
    else:
        add("positions_market_value_sum", False, "missing_equity", f"<= {tolerance}", "缺少权益快照，无法核对持仓市值。")

    order_checks = validate_orders(orders, lot_size)
    add("orders_valid", order_checks["valid"], order_checks["value"], "valid side/quantity/lot", "订单方向、数量和买入整手规则必须有效。")
    add_order_provenance_checks(add, orders, provenance)

    trades_valid = validate_trades(trades)
    add("trades_valid", trades_valid["valid"], trades_valid["value"], "valid side/quantity/amount", "历史成交方向、数量、金额和费用字段必须有效。")

    add_state_checks(add, config, snapshot, positions, tolerance)
    return pd.DataFrame(rows, columns=RECONCILIATION_COLUMNS)


def reconcile_outputs(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    reconciliation = build_account_reconciliation(
        config,
        read_csv(output_dir / "paper_equity_snapshot.csv"),
        read_csv(output_dir / "orders.csv", dtype={"symbol": str}),
        read_csv(output_dir / "positions.csv", dtype={"symbol": str}),
        read_csv(output_dir / "trades.csv", dtype={"symbol": str}),
        read_provenance(output_dir / "orders_provenance.csv"),
    )
    path = output_dir / "account_reconciliation.csv"
    reconciliation.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def reconciliation_passed(frame: pd.DataFrame) -> bool:
    return not frame.empty and "passed" in frame and bool(parse_passed_series(frame["passed"]).all())


def parse_passed_series(values: pd.Series) -> pd.Series:
    return values.map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})


def read_reconciliation(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    return read_csv(output_dir / "account_reconciliation.csv")


def add_order_provenance_checks(add, orders: pd.DataFrame, provenance: dict[str, Any]) -> None:
    expected_count = to_int(provenance.get("order_count"), default=len(orders))
    add(
        "orders_provenance_count",
        expected_count == len(orders),
        f"{len(orders)}/{expected_count}",
        "orders/order_count match",
        "订单数量必须与来源记录一致。",
    )
    execution_date = str(provenance.get("execution_date", "") or "")
    if orders.empty:
        add("orders_provenance_execution_date", True, "no_orders", execution_date or "n/a", "无订单时不要求执行日期匹配。")
        return
    if "date" not in orders:
        add("orders_provenance_execution_date", False, "missing_order_date", execution_date or "present", "订单必须包含执行日期。")
        return
    order_dates = sorted({str(pd.to_datetime(value).date()) for value in orders["date"].dropna()})
    add(
        "orders_provenance_execution_date",
        bool(execution_date) and order_dates == [execution_date],
        ",".join(order_dates),
        execution_date or "present",
        "orders.csv 的执行日期必须与 orders_provenance.csv 一致。",
    )


def add_state_checks(add, config: dict[str, Any], snapshot: pd.Series, positions: pd.DataFrame, tolerance: float) -> None:
    state_db_value = config.get("paths", {}).get("state_db")
    if not state_db_value:
        add("state_snapshot_match", True, "skipped", "state_db configured", "未配置 state_db，跳过 SQLite 快照核对。")
        add("state_positions_match", True, "skipped", "state_db configured", "未配置 state_db，跳过 SQLite 持仓核对。")
        return
    state_db = project_path(state_db_value)
    if not state_db.exists():
        add("state_snapshot_match", False, "missing_state_db", state_db, "SQLite 状态库必须存在。")
        add("state_positions_match", False, "missing_state_db", state_db, "SQLite 状态库必须存在。")
        return
    try:
        with sqlite3.connect(state_db) as conn:
            state_snapshot = pd.read_sql_query("select date, cash, equity from account_snapshots order by date desc limit 1", conn)
            state_positions = pd.read_sql_query("select symbol, quantity, cost, locked_quantity from positions", conn)
    except Exception as exc:
        add("state_snapshot_match", False, str(exc), "readable state_db", "无法读取 SQLite 状态库。")
        add("state_positions_match", False, str(exc), "readable state_db", "无法读取 SQLite 状态库。")
        return

    if state_snapshot.empty or snapshot.empty:
        add("state_snapshot_match", False, "missing_snapshot", "both sqlite and csv snapshot", "SQLite 与 CSV 都必须有权益快照。")
    else:
        row = state_snapshot.iloc[0]
        cash_diff = abs(to_float(row.get("cash")) - to_float(snapshot.get("cash")))
        equity_diff = abs(to_float(row.get("equity")) - to_float(snapshot.get("equity")))
        date_match = str(pd.to_datetime(row.get("date")).date()) == str(pd.to_datetime(snapshot.get("date")).date())
        passed = date_match and cash_diff <= tolerance and equity_diff <= tolerance
        add(
            "state_snapshot_match",
            passed,
            f"date_match={date_match}, cash_diff={cash_diff:.4f}, equity_diff={equity_diff:.4f}",
            f"date match and diff <= {tolerance}",
            "SQLite 最新账户快照必须与 CSV 权益快照一致。",
        )

    expected_positions = positions.copy()
    if expected_positions.empty:
        expected = pd.DataFrame(columns=["symbol", "quantity", "locked_quantity"])
    else:
        expected = expected_positions[["symbol", "quantity", "sellable_quantity"]].copy()
        expected["symbol"] = expected["symbol"].astype(str).str.zfill(6)
        expected["quantity"] = pd.to_numeric(expected["quantity"], errors="coerce").fillna(0).astype(int)
        expected["sellable_quantity"] = pd.to_numeric(expected["sellable_quantity"], errors="coerce").fillna(0).astype(int)
        expected["locked_quantity"] = expected["quantity"] - expected["sellable_quantity"]
        expected = expected[["symbol", "quantity", "locked_quantity"]]
    actual = state_positions.copy()
    if not actual.empty:
        actual["symbol"] = actual["symbol"].astype(str).str.zfill(6)
        actual["quantity"] = pd.to_numeric(actual["quantity"], errors="coerce").fillna(0).astype(int)
        actual["locked_quantity"] = pd.to_numeric(actual["locked_quantity"], errors="coerce").fillna(0).astype(int)
        actual = actual[["symbol", "quantity", "locked_quantity"]]
    merged = expected.merge(actual, how="outer", on="symbol", suffixes=("_csv", "_db")).fillna(0)
    if merged.empty:
        passed = True
        mismatch_count = 0
    else:
        mismatch = (merged["quantity_csv"].astype(int) != merged["quantity_db"].astype(int)) | (
            merged["locked_quantity_csv"].astype(int) != merged["locked_quantity_db"].astype(int)
        )
        mismatch_count = int(mismatch.sum())
        passed = mismatch_count == 0
    add("state_positions_match", passed, mismatch_count, 0, "SQLite 持仓必须与 positions.csv 数量和锁定数量一致。")


def validate_positions(positions: pd.DataFrame, tolerance: float) -> dict[str, object]:
    if positions.empty:
        return {"valid": True, "value": "empty"}
    required = {"symbol", "quantity", "sellable_quantity", "close", "market_value"}
    if not required.issubset(positions.columns):
        return {"valid": False, "value": f"missing:{sorted(required - set(positions.columns))}"}
    data = positions.copy()
    quantity = pd.to_numeric(data["quantity"], errors="coerce")
    sellable = pd.to_numeric(data["sellable_quantity"], errors="coerce")
    close = pd.to_numeric(data["close"], errors="coerce")
    market_value = pd.to_numeric(data["market_value"], errors="coerce")
    expected_value = quantity * close
    invalid = quantity.isna() | sellable.isna() | close.isna() | market_value.isna()
    invalid |= quantity < 0
    invalid |= sellable < 0
    invalid |= sellable > quantity
    invalid |= close < 0
    invalid |= (market_value - expected_value).abs() > tolerance
    return {"valid": not bool(invalid.any()), "value": int(invalid.sum())}


def validate_orders(orders: pd.DataFrame, lot_size: int) -> dict[str, object]:
    if orders.empty:
        return {"valid": True, "value": "empty"}
    required = {"date", "symbol", "side", "quantity"}
    if not required.issubset(orders.columns):
        return {"valid": False, "value": f"missing:{sorted(required - set(orders.columns))}"}
    side = orders["side"].astype(str).str.lower()
    quantity = pd.to_numeric(orders["quantity"], errors="coerce")
    invalid = quantity.isna() | (quantity <= 0) | ~side.isin({"buy", "sell"})
    invalid |= (side == "buy") & (quantity % lot_size != 0)
    return {"valid": not bool(invalid.any()), "value": int(invalid.sum())}


def validate_trades(trades: pd.DataFrame) -> dict[str, object]:
    if trades.empty:
        return {"valid": True, "value": "empty"}
    required = {"date", "symbol", "side", "quantity", "price", "fee", "amount"}
    if not required.issubset(trades.columns):
        return {"valid": False, "value": f"missing:{sorted(required - set(trades.columns))}"}
    side = trades["side"].astype(str).str.lower()
    quantity = pd.to_numeric(trades["quantity"], errors="coerce")
    price = pd.to_numeric(trades["price"], errors="coerce")
    fee = pd.to_numeric(trades["fee"], errors="coerce")
    amount = pd.to_numeric(trades["amount"], errors="coerce")
    invalid = quantity.isna() | price.isna() | fee.isna() | amount.isna()
    invalid |= ~side.isin({"buy", "sell"})
    invalid |= quantity <= 0
    invalid |= price <= 0
    invalid |= fee < 0
    invalid |= amount <= 0
    return {"valid": not bool(invalid.any()), "value": int(invalid.sum())}


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except EmptyDataError:
        return pd.DataFrame()


def read_provenance(path: Path) -> dict[str, str]:
    frame = read_csv(path)
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return {}
    return {str(row.item): "" if pd.isna(row.value) else str(row.value) for row in frame.itertuples(index=False)}


def sum_numeric(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


def to_float(value: object, default: float = 0.0) -> float:
    result = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(result) else float(result)


def to_int(value: object, default: int = 0) -> int:
    result = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(result) else int(result)
