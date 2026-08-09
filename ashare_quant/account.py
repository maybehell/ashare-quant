from __future__ import annotations

import sqlite3
import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .data import DataStore, committed_fetch_market_scope, normalize_symbol
from .io_utils import atomic_to_csv
from .validation import complete_market_dates, latest_close_map_asof, latest_data_date


ACCOUNT_IMPORT_COLUMNS = ["cash", "symbol", "quantity", "sellable_quantity", "cost"]
ACCOUNT_REPAIR_REVIEW_COLUMNS = [
    "cash",
    "symbol",
    "quantity",
    "sellable_quantity",
    "cost",
    "include_in_import",
    "close",
    "market_value",
    "price_status",
    "issue",
    "suggested_action",
    "note",
]
ACCOUNT_REPAIR_EXCLUDED_COLUMNS = ACCOUNT_REPAIR_REVIEW_COLUMNS
ACCOUNT_IMPORT_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]
ACCOUNT_IMPORT_POSITION_COLUMNS = ["symbol", "quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"]
ACCOUNT_IMPORT_CHECK_SNAPSHOT_COLUMNS = [
    "date",
    "cash",
    "market_value",
    "equity",
    "would_write_state",
    "source_file",
    "source_sha256",
    "checked_at",
]
ACCOUNT_STATE_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]
ACCOUNT_STATE_SNAPSHOT_COLUMNS = ["snapshot_date", "mark_date", "cash", "recorded_equity", "market_value", "marked_equity", "position_count", "locked_quantity", "sellable_quantity"]
ACCOUNT_STATE_POSITION_COLUMNS = ["symbol", "quantity", "locked_quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"]
ACCOUNT_STATE_MISSING_PRICE_COLUMNS = [
    "price_context",
    "symbol",
    "quantity",
    "cost",
    "symbol_in_stock_cache",
    "symbol_in_price_cache",
    "first_price_date",
    "last_price_date",
    "likely_reason",
    "suggested_action",
    "message",
]
ACCOUNT_PRICE_OVERRIDE_COLUMNS = ["price_context", "symbol", "date", "close", "source", "note"]
ACCOUNT_PRICE_OVERRIDE_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]
ACCOUNT_PRICE_OVERRIDE_EFFECTIVE_COLUMNS = ["symbol", "date", "close", "source", "note"]
STALE_EXECUTION_ARTIFACTS = [
    "orders_provenance.csv",
    "paper_equity_snapshot.csv",
    "paper_signal_snapshot.csv",
    "signal_log.csv",
    "account_reconciliation.csv",
    "auto_live_post_trade_reconciliation.csv",
    "auto_live_post_trade_manifest.csv",
    "rpa_orders.csv",
    "rpa_orders_integrity.json",
    "order_liquidity_all_audit.csv",
    "order_liquidity_audit.csv",
    "rpa_blocked_orders.csv",
    "rpa_orders_all.csv",
    "rpa_pre_trade_checks.csv",
    "rpa_action_log.csv",
    "fill_import_audit.csv",
    "fill_import_trades.csv",
    "fill_order_match.csv",
    "fill_import_positions.csv",
    "fill_import_snapshot.csv",
    "account_state_audit.csv",
    "account_state_snapshot.csv",
    "account_state_positions.csv",
    "account_state_missing_prices.csv",
    "account_reset_audit.csv",
    "account_repair_review.csv",
    "account_repair_excluded_positions.csv",
    "account_import_from_state_template.csv",
    "account_import_priced_only_template.csv",
    "account_import_check_audit.csv",
    "account_import_check_positions.csv",
    "account_import_check_snapshot.csv",
    "account_exclusion_impact.csv",
    "account_exclusion_impact_detail.csv",
    "account_readiness_plan.csv",
    "account_path_decision.csv",
]


def write_account_import_template(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "account_import_template.csv"
    atomic_to_csv(pd.DataFrame(columns=ACCOUNT_IMPORT_COLUMNS), path, index=False, encoding="utf-8-sig")
    return path


def account_price_override_path(config: dict[str, Any]) -> Path:
    configured = config.get("account", {}).get("price_override_file")
    if configured:
        return project_path(configured)
    return project_path(config["paths"]["output_dir"]) / "account_price_overrides.csv"


def write_account_price_override_template(config: dict[str, Any], asof_date: str | date | None = None) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    missing_path = output_dir / "account_state_missing_prices.csv"
    snapshot_path = output_dir / "account_state_snapshot.csv"
    if not missing_path.exists() or not snapshot_path.exists():
        export_account_state(config, asof_date=asof_date)
    missing = read_csv(missing_path, dtype={"symbol": str})
    snapshot = read_csv(snapshot_path)
    snapshot_date = first_text_value(snapshot, "snapshot_date")
    mark_date = first_text_value(snapshot, "mark_date")
    rows: list[dict[str, object]] = []
    if not missing.empty and {"price_context", "symbol"}.issubset(missing.columns):
        for row in missing.drop_duplicates(["price_context", "symbol"]).itertuples(index=False):
            context = str(getattr(row, "price_context", ""))
            rows.append(
                {
                    "price_context": context,
                    "symbol": normalize_symbol(getattr(row, "symbol")),
                    "date": snapshot_date if context == "snapshot" else mark_date,
                    "close": "",
                    "source": "",
                    "note": "填入人工核对价格后，另存或复制为 account_price_overrides.csv；该价格只用于账户估值/导入校验，不参与策略回测。",
                }
            )
    path = output_dir / "account_price_overrides_template.csv"
    atomic_to_csv(pd.DataFrame(rows, columns=ACCOUNT_PRICE_OVERRIDE_COLUMNS), path, index=False, encoding="utf-8-sig")
    return path


def check_account_price_overrides(config: dict[str, Any], asof_date: str | date | None = None) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    override_path = account_price_override_path(config)
    audit_path = output_dir / "account_price_override_audit.csv"
    effective_path = output_dir / "account_price_override_effective.csv"
    audit_rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        audit_rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    if not override_path.exists():
        add("override_file_exists", False, str(override_path), "account_price_overrides.csv exists", "手工价格覆盖文件不存在；系统不会应用任何补价。")
        atomic_to_csv(pd.DataFrame(audit_rows, columns=ACCOUNT_PRICE_OVERRIDE_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(pd.DataFrame(columns=ACCOUNT_PRICE_OVERRIDE_EFFECTIVE_COLUMNS), effective_path, index=False, encoding="utf-8-sig")
        return {"account_price_override_audit.csv": audit_path, "account_price_override_effective.csv": effective_path}

    raw = read_csv(override_path, dtype={"symbol": str})
    override_hash = file_sha256(override_path)
    add("override_file_exists", True, str(override_path), "account_price_overrides.csv exists", "已找到手工价格覆盖文件。")
    add("source_sha256", bool(override_hash), override_hash, "present", "The price override file hash is recorded for use-time binding.")
    required = set(ACCOUNT_PRICE_OVERRIDE_COLUMNS) - {"price_context", "note"}
    missing_columns = sorted(required - set(raw.columns))
    add("required_columns", not missing_columns, ",".join(missing_columns) if missing_columns else "ok", ",".join(sorted(required)), "补价文件必须包含 symbol/date/close/source。")
    if missing_columns:
        atomic_to_csv(pd.DataFrame(audit_rows, columns=ACCOUNT_PRICE_OVERRIDE_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(pd.DataFrame(columns=ACCOUNT_PRICE_OVERRIDE_EFFECTIVE_COLUMNS), effective_path, index=False, encoding="utf-8-sig")
        return {"account_price_override_audit.csv": audit_path, "account_price_override_effective.csv": effective_path}

    normalized = normalize_price_override_frame(raw)
    valid_symbol = normalized["symbol"] != "000000"
    valid_date = normalized["date"].notna()
    positive_close = normalized["close"] > 0
    source_present = normalized["source"] != ""
    add("valid_symbols", bool(valid_symbol.all()), int(valid_symbol.sum()), len(normalized), "股票代码必须可标准化为 6 位代码。")
    add("valid_dates", bool(valid_date.all()), int(valid_date.sum()), len(normalized), "补价日期必须可解析。")
    add("positive_close", bool(positive_close.all()), int(positive_close.sum()), len(normalized), "补价 close 必须大于 0。")
    add("source_present", bool(source_present.all()), int(source_present.sum()), len(normalized), "每条补价必须填写来源。")

    target_date = resolve_price_override_check_date(config, asof_date)
    valid = normalized[valid_symbol & valid_date & positive_close & source_present & (normalized["date"].dt.date <= target_date)].copy()
    if valid.empty:
        effective = pd.DataFrame(columns=ACCOUNT_PRICE_OVERRIDE_EFFECTIVE_COLUMNS)
    else:
        effective = valid.sort_values(["symbol", "date"]).groupby("symbol").tail(1)
        effective["date"] = effective["date"].dt.date.map(lambda value: value.isoformat())
        effective = effective[ACCOUNT_PRICE_OVERRIDE_EFFECTIVE_COLUMNS].reset_index(drop=True)
    add("effective_override_count", len(effective) > 0, len(effective), ">0", f"截至 {target_date.isoformat()} 实际会生效的补价数量。")

    missing_symbols = current_missing_price_symbols(output_dir)
    if missing_symbols:
        covered = set(effective["symbol"].astype(str)) & missing_symbols if not effective.empty else set()
        add("missing_symbols_covered", covered == missing_symbols, f"{len(covered)}/{len(missing_symbols)}", "all current missing price symbols", "当前缺价持仓应全部被补价或通过刷新行情解决。")
    else:
        add("missing_symbols_covered", True, "not_required", "all current missing price symbols", "当前没有缺价持仓或缺价清单未生成。")

    atomic_to_csv(pd.DataFrame(audit_rows, columns=ACCOUNT_PRICE_OVERRIDE_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(effective, effective_path, index=False, encoding="utf-8-sig")
    return {"account_price_override_audit.csv": audit_path, "account_price_override_effective.csv": effective_path}


def write_account_repair_template(config: dict[str, Any], asof_date: str | date | None = None) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    state_paths = export_account_state(config, asof_date=asof_date)
    snapshot = read_csv(state_paths["account_state_snapshot.csv"])
    positions = read_csv(state_paths["account_state_positions.csv"], dtype={"symbol": str})
    missing = read_csv(state_paths["account_state_missing_prices.csv"], dtype={"symbol": str})
    cash = first_snapshot_value(snapshot, "cash")

    review = build_account_repair_review(cash, positions, missing)
    import_template = build_account_import_from_state_template(cash, positions)
    priced_only_template = build_account_import_priced_only_template(cash, review)
    excluded_positions = build_account_repair_excluded_positions(review)
    import_path = output_dir / "account_import_from_state_template.csv"
    priced_only_path = output_dir / "account_import_priced_only_template.csv"
    excluded_path = output_dir / "account_repair_excluded_positions.csv"
    review_path = output_dir / "account_repair_review.csv"
    atomic_to_csv(import_template, import_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(priced_only_template, priced_only_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(excluded_positions, excluded_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(review, review_path, index=False, encoding="utf-8-sig")
    return {
        "account_import_from_state_template.csv": import_path,
        "account_import_priced_only_template.csv": priced_only_path,
        "account_repair_excluded_positions.csv": excluded_path,
        "account_repair_review.csv": review_path,
        **state_paths,
    }


def check_account_import_file(
    config: dict[str, Any],
    source_path: str | Path,
    asof_date: str | date | None = None,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "account_import_check_audit.csv"
    positions_path = output_dir / "account_import_check_positions.csv"
    snapshot_path = output_dir / "account_import_check_snapshot.csv"
    audit_rows: list[dict[str, object]] = []
    priced = pd.DataFrame(columns=ACCOUNT_IMPORT_POSITION_COLUMNS)
    snapshot = pd.DataFrame(columns=ACCOUNT_IMPORT_CHECK_SNAPSHOT_COLUMNS)
    source = Path(source_path)
    source_hash = file_sha256(source) if source.exists() else ""
    checked_at = datetime.now().isoformat(timespec="seconds")

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        audit_rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    try:
        raw = read_account_import_csv(source)
        add("import_file_readable", True, str(source), "readable CSV with required columns", "The account import CSV was parsed.")
        add("source_sha256", bool(source_hash), source_hash[:16], "present", "The import file hash is recorded for confirm-time binding.")
    except Exception as exc:
        add("import_file_readable", False, str(source), "readable CSV with required columns", str(exc))
        return write_account_import_check_outputs(audit_rows, priced, snapshot, audit_path, positions_path, snapshot_path)

    try:
        cash, positions = normalize_account_import(raw)
        add("cash_present", True, cash, "present", "Import file includes a cash value.")
        add("cash_non_negative", cash >= 0, cash, ">= 0", "Cash cannot be negative.")
        position_valid = validate_import_positions(positions)
        add(
            "position_rows_valid",
            position_valid["passed"],
            position_valid["value"],
            "quantity>=0, 0<=sellable<=quantity, cost>=0",
            "Position quantities, sellable quantities, and costs must be internally consistent.",
        )
    except Exception as exc:
        add("import_content_valid", False, str(source), "cash and valid position rows", str(exc))
        return write_account_import_check_outputs(audit_rows, priced, snapshot, audit_path, positions_path, snapshot_path)

    if cash < 0 or not bool(position_valid["passed"]):
        add("would_write_state", False, "blocked", "all validation checks passed", "The import file is not eligible to overwrite local account state.")
        return write_account_import_check_outputs(audit_rows, priced, snapshot, audit_path, positions_path, snapshot_path)

    try:
        store = DataStore.from_config(config)
        if positions.empty:
            asof = pd.to_datetime(asof_date).date() if asof_date is not None else date.today()
            price_map = {}
        else:
            asof, price_map = resolve_import_pricing(store, config, asof_date)
        add("pricing_available", True, asof.isoformat(), "cached prices available", "Cached prices were resolved for import valuation.")
        priced = build_import_positions_snapshot(positions, price_map)
        missing_prices = priced[priced["close"] <= 0]["symbol"].astype(str).tolist() if not priced.empty else []
        add("symbols_priced", not missing_prices, len(positions) - len(missing_prices), len(positions), "All imported positions need cached prices.")
        equity = cash + float(priced["market_value"].sum()) if not priced.empty else cash
        snapshot = pd.DataFrame(
            [
                {
                    "date": asof.isoformat(),
                    "cash": round(cash, 2),
                    "market_value": round(equity - cash, 2),
                    "equity": round(equity, 2),
                    "would_write_state": not missing_prices,
                    "source_file": str(source),
                    "source_sha256": source_hash,
                    "checked_at": checked_at,
                }
            ],
            columns=ACCOUNT_IMPORT_CHECK_SNAPSHOT_COLUMNS,
        )
        add(
            "would_write_state",
            not missing_prices,
            "ready" if not missing_prices else "blocked",
            "all validation checks passed",
            "This dry-run does not write SQLite state. Run account-import --confirm only after all checks pass.",
        )
    except Exception as exc:
        add("pricing_available", False, str(source), "cached prices available", str(exc))
        add("would_write_state", False, "blocked", "all validation checks passed", "The import file is not eligible to overwrite local account state.")
    return write_account_import_check_outputs(audit_rows, priced, snapshot, audit_path, positions_path, snapshot_path)


def validate_account_import_precheck(config: dict[str, Any], source_path: str | Path) -> dict[str, object]:
    if not bool(config.get("account", {}).get("require_import_check_before_confirm", True)):
        return {"passed": True, "value": "not_required", "message": "Precheck binding is not required by config."}
    output_dir = project_path(config["paths"]["output_dir"])
    audit = read_csv(output_dir / "account_import_check_audit.csv")
    snapshot = read_csv(output_dir / "account_import_check_snapshot.csv")
    if audit.empty or snapshot.empty:
        return {"passed": False, "value": "missing", "message": "Run account-import-check for the same file before account-import --confirm."}
    audit_passed = "passed" in audit.columns and bool(audit["passed"].map(parse_bool_like).all())
    if not audit_passed:
        return {"passed": False, "value": "audit_failed", "message": "The latest account-import-check has failed checks."}
    if "would_write_state" not in snapshot.columns or not parse_bool_like(snapshot["would_write_state"].iloc[0]):
        return {"passed": False, "value": "not_ready", "message": "The latest account-import-check snapshot is not marked ready to write state."}
    expected_hash = "" if "source_sha256" not in snapshot.columns or pd.isna(snapshot["source_sha256"].iloc[0]) else str(snapshot["source_sha256"].iloc[0])
    actual_hash = file_sha256(Path(source_path)) if Path(source_path).exists() else ""
    if not expected_hash or expected_hash != actual_hash:
        return {
            "passed": False,
            "value": (actual_hash[:16] if actual_hash else "missing"),
            "message": "The import file differs from the file that passed account-import-check.",
        }
    return {"passed": True, "value": expected_hash[:16], "message": "The import file matches the latest passed account-import-check."}


def reset_account_state(
    config: dict[str, Any],
    asof_date: str | date | None = None,
    cash: float | None = None,
    confirm: bool = False,
) -> dict[str, Path]:
    if not confirm:
        raise RuntimeError("account reset clears local paper positions, orders, trades, and snapshots; rerun with --confirm after checking.")

    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    store = DataStore.from_config(config)
    asof = resolve_account_reset_date(store, config, asof_date)
    reset_cash = float(cash if cash is not None else config.get("backtest", {}).get("initial_cash", 1000000))
    if reset_cash < 0:
        raise ValueError("reset cash cannot be negative")

    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        conn.execute("delete from positions")
        conn.execute("delete from orders")
        conn.execute("delete from trades")
        conn.execute("delete from account_snapshots")
        conn.execute(
            "insert into account_snapshots(date, cash, equity) values (?, ?, ?)",
            (asof.isoformat(), reset_cash, reset_cash),
        )
    clear_stale_execution_artifacts(output_dir)
    paths = export_account_state(config, asof_date=asof)
    audit_path = output_dir / "account_reset_audit.csv"
    reset_audit = pd.DataFrame(
        [
            {
                "check": "account_reset",
                "passed": True,
                "value": f"{asof.isoformat()} cash={reset_cash:.2f}",
                "threshold": "confirmed local paper reset",
                "message": "Local paper account was reset to cash-only state; old positions, orders, trades, and executable artifacts were cleared.",
            }
        ],
        columns=ACCOUNT_STATE_AUDIT_COLUMNS,
    )
    atomic_to_csv(reset_audit, audit_path, index=False, encoding="utf-8-sig")
    return {"account_reset_audit.csv": audit_path, **paths}


def resolve_account_reset_date(
    store: DataStore,
    config: dict[str, Any],
    asof_date: str | date | None = None,
) -> date:
    if asof_date is not None:
        return pd.to_datetime(asof_date).date()
    try:
        _, prices, _ = store.load_market_data()
    except FileNotFoundError:
        return date.today()
    if prices.empty:
        return date.today()
    benchmark = store.load_benchmark(required=False)
    scoped_prices, _scope_ok, _scope_value = committed_fetch_market_scope(store, prices)
    dates = complete_market_dates(scoped_prices, benchmark, config)
    return dates[-1] if dates else latest_data_date(prices) or date.today()


def export_account_state(config: dict[str, Any], asof_date: str | date | None = None) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "account_state_snapshot.csv"
    positions_path = output_dir / "account_state_positions.csv"
    missing_prices_path = output_dir / "account_state_missing_prices.csv"
    audit_path = output_dir / "account_state_audit.csv"
    audit_rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        audit_rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    store = DataStore.from_config(config)
    store.init_db()
    state = read_sqlite_account_state(store)
    snapshot = state["snapshot"]
    positions = state["positions"]
    add("state_db_readable", True, str(store.state_db), "readable SQLite state", "Local account state database is readable.")
    has_snapshot = snapshot is not None
    add("account_snapshot_present", has_snapshot, "present" if has_snapshot else "missing", "present", "A cash/equity account snapshot must exist before live operation.")
    if snapshot is None:
        empty_snapshot = pd.DataFrame(columns=ACCOUNT_STATE_SNAPSHOT_COLUMNS)
        empty_positions = pd.DataFrame(columns=ACCOUNT_STATE_POSITION_COLUMNS)
        empty_missing = pd.DataFrame(columns=ACCOUNT_STATE_MISSING_PRICE_COLUMNS)
        audit = pd.DataFrame(audit_rows, columns=ACCOUNT_STATE_AUDIT_COLUMNS)
        atomic_to_csv(empty_snapshot, snapshot_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(empty_positions, positions_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(empty_missing, missing_prices_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
        return {
            "account_state_snapshot.csv": snapshot_path,
            "account_state_positions.csv": positions_path,
            "account_state_missing_prices.csv": missing_prices_path,
            "account_state_audit.csv": audit_path,
        }

    snapshot_date = pd.to_datetime(snapshot["date"]).date()
    mark_date, mark_prices = resolve_account_mark_prices(store, config, asof_date)
    snapshot_prices = resolve_price_map_for_date(store, snapshot_date, config)
    position_valid = validate_state_positions(positions)
    add(
        "position_rows_valid",
        position_valid["passed"],
        position_valid["value"],
        "quantity>=0, 0<=locked<=quantity, cost>=0",
        "SQLite positions must have valid quantities, locked quantities, and costs.",
    )
    cash = float(snapshot["cash"])
    recorded_equity = float(snapshot["equity"])
    add("cash_non_negative", cash >= 0, cash, ">= 0", "Cash cannot be negative.")
    snapshot_priced_positions = build_state_positions_snapshot(positions, snapshot_prices)
    missing_snapshot_prices = snapshot_priced_positions[snapshot_priced_positions["close"] <= 0]["symbol"].astype(str).tolist() if not snapshot_priced_positions.empty else []
    add("snapshot_positions_priced", not missing_snapshot_prices, len(positions) - len(missing_snapshot_prices), len(positions), "Positions must have prices on or before the recorded snapshot date.")
    snapshot_market_value = float(snapshot_priced_positions["market_value"].sum()) if not snapshot_priced_positions.empty else 0.0
    tolerance = float(config.get("reconciliation", {}).get("cash_tolerance", 1.0))
    recorded_diff = abs(recorded_equity - cash - snapshot_market_value)
    add("recorded_equity_reconciled", recorded_diff <= tolerance, round(recorded_diff, 4), f"<= {tolerance}", "Recorded equity must equal cash plus position value at the snapshot date.")

    marked_positions = build_state_positions_snapshot(positions, mark_prices)
    missing_mark_prices = marked_positions[marked_positions["close"] <= 0]["symbol"].astype(str).tolist() if not marked_positions.empty else []
    add("mark_positions_priced", not missing_mark_prices, len(positions) - len(missing_mark_prices), len(positions), "Positions must have latest mark prices.")
    market_value = float(marked_positions["market_value"].sum()) if not marked_positions.empty else 0.0
    locked_quantity = int(pd.to_numeric(positions["locked_quantity"], errors="coerce").fillna(0).sum()) if not positions.empty else 0
    total_quantity = int(pd.to_numeric(positions["quantity"], errors="coerce").fillna(0).sum()) if not positions.empty else 0
    row = {
        "snapshot_date": snapshot_date.isoformat(),
        "mark_date": mark_date.isoformat(),
        "cash": round(cash, 2),
        "recorded_equity": round(recorded_equity, 2),
        "market_value": round(market_value, 2),
        "marked_equity": round(cash + market_value, 2),
        "position_count": int(len(positions)),
        "locked_quantity": locked_quantity,
        "sellable_quantity": total_quantity - locked_quantity,
    }
    atomic_to_csv(pd.DataFrame([row], columns=ACCOUNT_STATE_SNAPSHOT_COLUMNS), snapshot_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(marked_positions, positions_path, index=False, encoding="utf-8-sig")
    missing_prices = build_missing_price_rows(
        positions,
        {"snapshot": missing_snapshot_prices, "mark": missing_mark_prices},
        price_diagnostics=load_price_diagnostics(store),
    )
    atomic_to_csv(missing_prices, missing_prices_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(pd.DataFrame(audit_rows, columns=ACCOUNT_STATE_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    return {
        "account_state_snapshot.csv": snapshot_path,
        "account_state_positions.csv": positions_path,
        "account_state_missing_prices.csv": missing_prices_path,
        "account_state_audit.csv": audit_path,
    }


def import_account_state(
    config: dict[str, Any],
    source_path: str | Path,
    asof_date: str | date | None = None,
    confirm: bool = False,
    ack_exclusions: bool = False,
) -> dict[str, Path]:
    if not confirm:
        raise RuntimeError("account import overwrites local account state; rerun with --confirm after checking the CSV")

    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "account_import_audit.csv"
    positions_path = output_dir / "account_import_positions.csv"
    snapshot_path = output_dir / "account_import_snapshot.csv"

    audit_rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        audit_rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    try:
        raw = read_account_import_csv(Path(source_path))
        add("import_file_readable", True, str(source_path), "readable CSV with required columns", "The account import CSV was parsed.")
        precheck = validate_account_import_precheck(config, source_path)
        add(
            "import_precheck_matched",
            bool(precheck["passed"]),
            precheck["value"],
            "latest account-import-check passed for same source_sha256",
            str(precheck["message"]),
        )
        if not bool(precheck["passed"]):
            raise RuntimeError(str(precheck["message"]))
        exclusion_ack = validate_priced_only_exclusion_ack(config, source_path, ack_exclusions)
        add(
            "priced_only_exclusions_acknowledged",
            bool(exclusion_ack["passed"]),
            exclusion_ack["value"],
            exclusion_ack["threshold"],
            exclusion_ack["message"],
        )
        if not bool(exclusion_ack["passed"]):
            raise RuntimeError(str(exclusion_ack["message"]))
        cash, positions = normalize_account_import(raw)
        add("cash_present", True, cash, "present", "Import file must include one cash value.")
        add("cash_non_negative", cash >= 0, cash, ">= 0", "Cash cannot be negative.")
        position_valid = validate_import_positions(positions)
        add(
            "position_rows_valid",
            position_valid["passed"],
            position_valid["value"],
            "quantity>=0, 0<=sellable<=quantity, cost>=0",
            "Position quantities, sellable quantities, and costs must be internally consistent.",
        )
        if not cash >= 0 or not position_valid["passed"]:
            raise ValueError(failed_audit_message(audit_rows))

        store = DataStore.from_config(config)
        if positions.empty:
            asof = pd.to_datetime(asof_date).date() if asof_date is not None else date.today()
            price_map = {}
        else:
            asof, price_map = resolve_import_pricing(store, config, asof_date)
        priced = build_import_positions_snapshot(positions, price_map)
        missing_prices = priced[priced["close"] <= 0]["symbol"].astype(str).tolist()
        add("symbols_priced", not missing_prices, len(positions) - len(missing_prices), len(positions), "All imported positions need cached prices.")
        if missing_prices:
            raise ValueError(f"missing cached prices for imported positions: {', '.join(missing_prices)}")

        equity = cash + float(priced["market_value"].sum()) if not priced.empty else cash
        write_state(store, asof, cash, equity, positions)
        cleared = clear_stale_execution_artifacts(output_dir)
        add("state_written", True, asof.isoformat(), "sqlite account state updated", "Imported account state was written to SQLite.")
        add("old_execution_artifacts_cleared", True, cleared, "stale paper/RPA artifacts removed", "Old executable order artifacts were invalidated.")

        snapshot = pd.DataFrame([{"date": asof.isoformat(), "cash": round(cash, 2), "market_value": round(equity - cash, 2), "equity": round(equity, 2)}])
        atomic_to_csv(snapshot, snapshot_path, index=False, encoding="utf-8-sig")
        atomic_to_csv(priced, positions_path, index=False, encoding="utf-8-sig")
        audit = pd.DataFrame(audit_rows, columns=ACCOUNT_IMPORT_AUDIT_COLUMNS)
        atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
        paths = {
            "account_import_audit.csv": audit_path,
            "account_import_positions.csv": positions_path,
            "account_import_snapshot.csv": snapshot_path,
        }
        paths.update(export_account_state(config, asof_date=asof))
        return paths
    except Exception:
        audit = pd.DataFrame(audit_rows, columns=ACCOUNT_IMPORT_AUDIT_COLUMNS)
        if audit.empty:
            audit = pd.DataFrame(
                [
                    {
                        "check": "import_file_readable",
                        "passed": False,
                        "value": str(source_path),
                        "threshold": "readable CSV with required columns",
                        "message": "The account import file could not be parsed.",
                    }
                ],
                columns=ACCOUNT_IMPORT_AUDIT_COLUMNS,
            )
        atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
        raise


def validate_priced_only_exclusion_ack(config: dict[str, Any], source_path: str | Path, ack_exclusions: bool) -> dict[str, object]:
    output_dir = project_path(config["paths"]["output_dir"])
    source = Path(source_path)
    priced_template = output_dir / "account_import_priced_only_template.csv"
    try:
        is_priced_only = source.resolve() == priced_template.resolve()
    except FileNotFoundError:
        is_priced_only = source == priced_template
    if not is_priced_only:
        return {
            "passed": True,
            "value": "not_applicable",
            "threshold": "ack required only for priced-only import template",
            "message": "Import source is not the priced-only exclusion template.",
        }
    excluded = read_csv(output_dir / "account_repair_excluded_positions.csv", dtype={"symbol": str})
    excluded_count = int(len(excluded)) if not excluded.empty else 0
    if excluded_count == 0:
        return {
            "passed": True,
            "value": 0,
            "threshold": "no excluded positions",
            "message": "Priced-only template does not currently exclude any positions.",
        }
    return {
        "passed": bool(ack_exclusions),
        "value": excluded_count,
        "threshold": "--ack-exclusions required when priced-only import excludes positions",
        "message": (
            f"Priced-only import excludes {excluded_count} position rows from account_repair_excluded_positions.csv; "
            "rerun with --ack-exclusions only after manually confirming the exclusions."
        ),
    }


def read_sqlite_account_state(store: DataStore) -> dict[str, object]:
    with sqlite3.connect(store.state_db) as conn:
        snapshot_row = conn.execute("select date, cash, equity from account_snapshots order by date desc limit 1").fetchone()
        positions = pd.read_sql_query("select symbol, quantity, cost, locked_quantity from positions", conn)
    snapshot = None
    if snapshot_row is not None:
        snapshot = {"date": snapshot_row[0], "cash": float(snapshot_row[1]), "equity": float(snapshot_row[2])}
    if positions.empty:
        positions = pd.DataFrame(columns=["symbol", "quantity", "cost", "locked_quantity"])
    else:
        positions["symbol"] = positions["symbol"].map(normalize_symbol)
        positions["quantity"] = pd.to_numeric(positions["quantity"], errors="coerce").fillna(0).astype(int)
        positions["cost"] = pd.to_numeric(positions["cost"], errors="coerce").fillna(0.0).astype(float)
        positions["locked_quantity"] = pd.to_numeric(positions["locked_quantity"], errors="coerce").fillna(0).astype(int)
        positions = positions[positions["quantity"] > 0].copy().reset_index(drop=True)
    return {"snapshot": snapshot, "positions": positions}


def resolve_account_mark_prices(
    store: DataStore,
    config: dict[str, Any],
    asof_date: str | date | None,
) -> tuple[date, dict[str, float]]:
    if asof_date is not None:
        mark_date = pd.to_datetime(asof_date).date()
        return mark_date, resolve_price_map_for_date(store, mark_date, config)
    try:
        _, prices, _ = store.load_market_data()
    except FileNotFoundError as exc:
        raise RuntimeError("cached market data is required to mark account state") from exc
    if prices.empty:
        raise RuntimeError("cached prices are empty; refresh market data before marking account state")
    benchmark = store.load_benchmark(required=False)
    scoped_prices, _scope_ok, _scope_value = committed_fetch_market_scope(store, prices)
    dates = complete_market_dates(scoped_prices, benchmark, config)
    mark_date = dates[-1] if dates else latest_data_date(prices)
    if mark_date is None:
        raise RuntimeError("cannot resolve latest mark date from cached prices")
    return mark_date, apply_manual_price_overrides(config, latest_close_map_asof(prices, mark_date), mark_date)


def resolve_price_map_for_date(store: DataStore, asof: date, config: dict[str, Any] | None = None) -> dict[str, float]:
    try:
        _, prices, _ = store.load_market_data()
    except FileNotFoundError as exc:
        raise RuntimeError("cached market data is required to mark account state") from exc
    if prices.empty:
        base: dict[str, float] = {}
    else:
        base = latest_close_map_asof(prices, asof)
    return apply_manual_price_overrides(config, base, asof) if config is not None else base


def apply_manual_price_overrides(config: dict[str, Any] | None, price_map: dict[str, float], asof: date) -> dict[str, float]:
    if config is None:
        return price_map
    overrides = load_manual_price_overrides(config, asof)
    if not overrides:
        return price_map
    merged = dict(price_map)
    merged.update(overrides)
    return merged


def load_manual_price_overrides(config: dict[str, Any], asof: date) -> dict[str, float]:
    path = account_price_override_path(config)
    if bool(config.get("account", {}).get("require_price_override_check_before_use", True)):
        precheck = validate_price_override_precheck(config, path)
        if not bool(precheck["passed"]):
            return {}
        frame = read_csv(project_path(config["paths"]["output_dir"]) / "account_price_override_effective.csv", dtype={"symbol": str})
    else:
        frame = read_csv(path, dtype={"symbol": str})
    if frame.empty or not {"symbol", "date", "close", "source"}.issubset(frame.columns):
        return {}
    data = normalize_price_override_frame(frame)
    data = data.dropna(subset=["date", "close"])
    data = data[(data["date"].dt.date <= asof) & (data["close"] > 0) & (data["source"] != "")]
    if data.empty:
        return {}
    latest = data.sort_values(["symbol", "date"]).groupby("symbol").tail(1)
    return {str(row.symbol): float(row.close) for row in latest.itertuples(index=False)}


def validate_price_override_precheck(config: dict[str, Any], override_path: Path | None = None) -> dict[str, object]:
    if not bool(config.get("account", {}).get("require_price_override_check_before_use", True)):
        return {"passed": True, "value": "not_required", "message": "Price override precheck binding is not required by config."}
    output_dir = project_path(config["paths"]["output_dir"])
    audit = read_csv(output_dir / "account_price_override_audit.csv")
    effective = read_csv(output_dir / "account_price_override_effective.csv", dtype={"symbol": str})
    if audit.empty or effective.empty:
        return {
            "passed": False,
            "value": "missing",
            "message": "Run account-price-check before using account_price_overrides.csv.",
        }
    if "passed" not in audit.columns or not audit["passed"].map(parse_bool_like).all():
        return {
            "passed": False,
            "value": "audit_failed",
            "message": "The latest account-price-check has failed checks.",
        }
    source_hash_rows = audit[audit["check"].astype(str) == "source_sha256"] if "check" in audit.columns else pd.DataFrame()
    expected_hash = "" if source_hash_rows.empty else str(source_hash_rows["value"].iloc[0])
    path = override_path or account_price_override_path(config)
    actual_hash = file_sha256(path) if path.exists() else ""
    if not expected_hash or expected_hash != actual_hash:
        return {
            "passed": False,
            "value": actual_hash[:16] if actual_hash else "missing",
            "message": "The price override file differs from the file that passed account-price-check.",
        }
    return {
        "passed": True,
        "value": expected_hash[:16],
        "message": "The price override file matches the latest passed account-price-check.",
    }


def normalize_price_override_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    for column in ACCOUNT_PRICE_OVERRIDE_COLUMNS:
        if column not in data.columns:
            data[column] = ""
    data["symbol"] = data["symbol"].map(normalize_symbol)
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["source"] = data["source"].fillna("").astype(str).str.strip()
    data["note"] = data["note"].fillna("").astype(str)
    return data


def resolve_price_override_check_date(config: dict[str, Any], asof_date: str | date | None) -> date:
    if asof_date is not None:
        return pd.to_datetime(asof_date).date()
    snapshot = read_csv(project_path(config["paths"]["output_dir"]) / "account_state_snapshot.csv")
    mark_date = first_text_value(snapshot, "mark_date")
    if mark_date:
        return pd.to_datetime(mark_date).date()
    return date.today()


def current_missing_price_symbols(output_dir: Path) -> set[str]:
    missing = read_csv(output_dir / "account_state_missing_prices.csv", dtype={"symbol": str})
    if missing.empty or "symbol" not in missing.columns:
        return set()
    return set(missing["symbol"].dropna().map(normalize_symbol))


def validate_state_positions(positions: pd.DataFrame) -> dict[str, object]:
    if positions.empty:
        return {"passed": True, "value": "empty"}
    invalid = positions["quantity"].isna() | positions["cost"].isna() | positions["locked_quantity"].isna()
    invalid |= positions["quantity"] < 0
    invalid |= positions["cost"] < 0
    invalid |= positions["locked_quantity"] < 0
    invalid |= positions["locked_quantity"] > positions["quantity"]
    invalid |= positions["symbol"].astype(str).str.len() != 6
    return {"passed": not bool(invalid.any()), "value": int(invalid.sum())}


def build_state_positions_snapshot(positions: pd.DataFrame, price_map: dict[str, float]) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame(columns=ACCOUNT_STATE_POSITION_COLUMNS)
    rows = []
    for row in positions.itertuples(index=False):
        symbol = normalize_symbol(row.symbol)
        quantity = int(row.quantity)
        locked_quantity = int(row.locked_quantity)
        cost = float(row.cost)
        close = float(price_map.get(symbol, 0.0))
        rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "locked_quantity": locked_quantity,
                "sellable_quantity": max(0, quantity - locked_quantity),
                "cost": round(cost, 4),
                "close": round(close, 4),
                "market_value": round(quantity * close, 2),
                "pnl": round(quantity * (close - cost), 2),
            }
        )
    return pd.DataFrame(rows, columns=ACCOUNT_STATE_POSITION_COLUMNS)


def build_missing_price_rows(
    positions: pd.DataFrame,
    missing_by_context: dict[str, list[str]],
    price_diagnostics: dict[str, dict[str, object]] | None = None,
) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame(columns=ACCOUNT_STATE_MISSING_PRICE_COLUMNS)
    price_diagnostics = price_diagnostics or {}
    by_symbol = {normalize_symbol(row.symbol): row for row in positions.itertuples(index=False)}
    rows = []
    messages = {
        "snapshot": "持仓在账户快照日期之前没有可用缓存价格。",
        "mark": "持仓在最新估值日期之前没有可用缓存价格。",
    }
    for context, symbols in missing_by_context.items():
        for symbol in sorted(set(symbols)):
            row = by_symbol.get(normalize_symbol(symbol))
            detail = missing_price_detail(normalize_symbol(symbol), price_diagnostics)
            rows.append(
                {
                    "price_context": context,
                    "symbol": normalize_symbol(symbol),
                    "quantity": int(row.quantity) if row is not None else 0,
                    "cost": round(float(row.cost), 4) if row is not None else 0.0,
                    "symbol_in_stock_cache": detail["symbol_in_stock_cache"],
                    "symbol_in_price_cache": detail["symbol_in_price_cache"],
                    "first_price_date": detail["first_price_date"],
                    "last_price_date": detail["last_price_date"],
                    "likely_reason": detail["likely_reason"],
                    "suggested_action": detail["suggested_action"],
                    "message": messages.get(context, "缺少缓存价格。"),
                }
            )
    return pd.DataFrame(rows, columns=ACCOUNT_STATE_MISSING_PRICE_COLUMNS)


def load_price_diagnostics(store: DataStore) -> dict[str, dict[str, object]]:
    try:
        stocks, prices, _ = store.load_market_data()
    except FileNotFoundError:
        return {}
    stock_symbols = set(stocks["symbol"].astype(str)) if not stocks.empty and "symbol" in stocks else set()
    diagnostics: dict[str, dict[str, object]] = {}
    for symbol in stock_symbols:
        diagnostics[normalize_symbol(symbol)] = {
            "symbol_in_stock_cache": True,
            "symbol_in_price_cache": False,
            "first_price_date": "",
            "last_price_date": "",
        }
    if not prices.empty and {"symbol", "date"}.issubset(prices.columns):
        grouped = prices.groupby("symbol")["date"].agg(["min", "max"]).reset_index()
        for row in grouped.itertuples(index=False):
            symbol = normalize_symbol(row.symbol)
            diagnostics.setdefault(
                symbol,
                {
                    "symbol_in_stock_cache": symbol in stock_symbols,
                    "symbol_in_price_cache": False,
                    "first_price_date": "",
                    "last_price_date": "",
                },
            )
            diagnostics[symbol].update(
                {
                    "symbol_in_stock_cache": symbol in stock_symbols,
                    "symbol_in_price_cache": True,
                    "first_price_date": date_to_text(row.min),
                    "last_price_date": date_to_text(row.max),
                }
            )
    return diagnostics


def missing_price_detail(symbol: str, diagnostics: dict[str, dict[str, object]]) -> dict[str, object]:
    detail = diagnostics.get(normalize_symbol(symbol), {})
    in_stock = bool(detail.get("symbol_in_stock_cache", False))
    in_price = bool(detail.get("symbol_in_price_cache", False))
    first_date = str(detail.get("first_price_date", "") or "")
    last_date = str(detail.get("last_price_date", "") or "")
    if in_price:
        reason = "缓存中有该股票历史行情，但目标估值日期前没有可用价格。"
        action = "检查账户快照日期是否早于行情缓存起点，或刷新更早历史行情后重跑 account-state。"
    elif in_stock:
        reason = "股票在股票列表中，但不在当前行情缓存覆盖范围内。"
        action = "刷新包含该股票的行情缓存；若该持仓不属于当前精选股票池，真实账户导入时应先人工处理。"
    else:
        reason = "股票不在当前股票列表和行情缓存中，可能是旧模拟持仓、代码错误、退市证券或非当前 A 股股票池标的。"
        action = "核对代码；模拟盘可重置账户，真实账户应先在导入模板中修正或剔除该证券。"
    return {
        "symbol_in_stock_cache": in_stock,
        "symbol_in_price_cache": in_price,
        "first_price_date": first_date,
        "last_price_date": last_date,
        "likely_reason": reason,
        "suggested_action": action,
    }


def date_to_text(value: object) -> str:
    if pd.isna(value):
        return ""
    try:
        return pd.to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return str(value)


def build_account_import_from_state_template(cash: float, positions: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = [{"cash": round(float(cash), 2), "symbol": "", "quantity": "", "sellable_quantity": "", "cost": ""}]
    if not positions.empty:
        for row in positions.itertuples(index=False):
            rows.append(
                {
                    "cash": "",
                    "symbol": normalize_symbol(row.symbol),
                    "quantity": int(row.quantity),
                    "sellable_quantity": int(row.sellable_quantity),
                    "cost": round(float(row.cost), 4),
                }
            )
    return pd.DataFrame(rows, columns=ACCOUNT_IMPORT_COLUMNS)


def build_account_import_priced_only_template(cash: float, repair_review: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = [{"cash": round(float(cash), 2), "symbol": "", "quantity": "", "sellable_quantity": "", "cost": ""}]
    if repair_review.empty:
        return pd.DataFrame(rows, columns=ACCOUNT_IMPORT_COLUMNS)
    position_rows = repair_review[repair_review["symbol"].notna() & (repair_review["symbol"].astype(str).str.strip() != "")].copy()
    for row in position_rows.itertuples(index=False):
        if not parse_bool_like(getattr(row, "include_in_import", False)):
            continue
        rows.append(
            {
                "cash": "",
                "symbol": normalize_symbol(row.symbol),
                "quantity": int(float(row.quantity)),
                "sellable_quantity": int(float(row.sellable_quantity)),
                "cost": round(float(row.cost), 4),
            }
        )
    return pd.DataFrame(rows, columns=ACCOUNT_IMPORT_COLUMNS)


def build_account_repair_excluded_positions(repair_review: pd.DataFrame) -> pd.DataFrame:
    if repair_review.empty:
        return pd.DataFrame(columns=ACCOUNT_REPAIR_EXCLUDED_COLUMNS)
    position_rows = repair_review[repair_review["symbol"].notna() & (repair_review["symbol"].astype(str).str.strip() != "")].copy()
    excluded = position_rows[~position_rows["include_in_import"].map(parse_bool_like)].copy()
    return excluded[ACCOUNT_REPAIR_EXCLUDED_COLUMNS].reset_index(drop=True) if not excluded.empty else pd.DataFrame(columns=ACCOUNT_REPAIR_EXCLUDED_COLUMNS)


def write_account_import_check_outputs(
    audit_rows: list[dict[str, object]],
    positions: pd.DataFrame,
    snapshot: pd.DataFrame,
    audit_path: Path,
    positions_path: Path,
    snapshot_path: Path,
) -> dict[str, Path]:
    audit = pd.DataFrame(audit_rows, columns=ACCOUNT_IMPORT_AUDIT_COLUMNS)
    if positions.empty:
        positions = pd.DataFrame(columns=ACCOUNT_IMPORT_POSITION_COLUMNS)
    if snapshot.empty:
        snapshot = pd.DataFrame(columns=ACCOUNT_IMPORT_CHECK_SNAPSHOT_COLUMNS)
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(positions, positions_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(snapshot, snapshot_path, index=False, encoding="utf-8-sig")
    return {
        "account_import_check_audit.csv": audit_path,
        "account_import_check_positions.csv": positions_path,
        "account_import_check_snapshot.csv": snapshot_path,
    }


def build_account_repair_review(cash: float, positions: pd.DataFrame, missing_prices: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = [
        {
            "cash": round(float(cash), 2),
            "symbol": "",
            "quantity": "",
            "sellable_quantity": "",
            "cost": "",
            "include_in_import": "",
            "close": "",
            "market_value": "",
            "price_status": "cash",
            "issue": "",
            "suggested_action": "核对现金后再使用 account_import_from_state_template.csv 导入。",
            "note": "现金行；account-import 会读取第一列 cash 的首个有效值。",
        }
    ]
    missing_by_symbol = summarize_missing_prices(missing_prices)
    if not positions.empty:
        for row in positions.itertuples(index=False):
            symbol = normalize_symbol(row.symbol)
            issue = missing_by_symbol.get(symbol, {})
            close = float(row.close)
            price_ok = close > 0 and not issue
            rows.append(
                {
                    "cash": "",
                    "symbol": symbol,
                    "quantity": int(row.quantity),
                    "sellable_quantity": int(row.sellable_quantity),
                    "cost": round(float(row.cost), 4),
                    "include_in_import": price_ok,
                    "close": round(close, 4),
                    "market_value": round(float(row.market_value), 2),
                    "price_status": "priced" if price_ok else "missing_price",
                    "issue": issue.get("likely_reason", "") if issue else "",
                    "suggested_action": issue.get("suggested_action", "") if issue else "可导入；仍需人工核对真实账户数量和成本。",
                    "note": "默认可导入" if price_ok else "导入前必须先补齐价格、扩展股票池，或从模板中人工处理该持仓。",
                }
            )
    return pd.DataFrame(rows, columns=ACCOUNT_REPAIR_REVIEW_COLUMNS)


def summarize_missing_prices(missing_prices: pd.DataFrame) -> dict[str, dict[str, str]]:
    if missing_prices.empty or "symbol" not in missing_prices.columns:
        return {}
    result: dict[str, dict[str, str]] = {}
    for symbol, group in missing_prices.groupby(missing_prices["symbol"].map(normalize_symbol)):
        reason = first_non_empty(group, "likely_reason") or first_non_empty(group, "message")
        action = first_non_empty(group, "suggested_action") or "刷新行情缓存或人工核对该证券。"
        result[normalize_symbol(symbol)] = {"likely_reason": reason, "suggested_action": action}
    return result


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def first_non_empty(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    for value in frame[column]:
        if pd.notna(value) and str(value).strip():
            return str(value)
    return ""


def first_snapshot_value(snapshot: pd.DataFrame, column: str) -> float:
    if snapshot.empty or column not in snapshot.columns:
        return 0.0
    value = pd.to_numeric(snapshot[column], errors="coerce").dropna()
    return float(value.iloc[0]) if not value.empty else 0.0


def first_text_value(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    value = frame[column].dropna()
    return "" if value.empty else str(value.iloc[0])


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except EmptyDataError:
        return pd.DataFrame()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_account_import_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        frame = pd.read_csv(path, dtype={"symbol": str})
    except EmptyDataError:
        frame = pd.DataFrame(columns=ACCOUNT_IMPORT_COLUMNS)
    missing = set(ACCOUNT_IMPORT_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"account import missing columns: {sorted(missing)}")
    return frame[ACCOUNT_IMPORT_COLUMNS].copy()


def normalize_account_import(frame: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    if frame.empty:
        raise ValueError("account import is empty; at least cash is required")
    cash_values = pd.to_numeric(frame["cash"], errors="coerce").dropna()
    if cash_values.empty:
        raise ValueError("account import requires a cash value")
    cash = float(cash_values.iloc[0])

    rows = frame[frame["symbol"].notna() & (frame["symbol"].astype(str).str.strip() != "")].copy()
    if rows.empty:
        return cash, pd.DataFrame(columns=["symbol", "quantity", "sellable_quantity", "cost", "locked_quantity"])
    rows["symbol"] = rows["symbol"].map(normalize_symbol)
    rows["quantity"] = pd.to_numeric(rows["quantity"], errors="coerce")
    rows["sellable_quantity"] = pd.to_numeric(rows["sellable_quantity"], errors="coerce")
    rows["cost"] = pd.to_numeric(rows["cost"], errors="coerce")
    rows = rows.groupby("symbol", as_index=False).agg(
        quantity=("quantity", "sum"),
        sellable_quantity=("sellable_quantity", "sum"),
        cost=("cost", "mean"),
    )
    rows["quantity"] = rows["quantity"].fillna(0).astype(int)
    rows["sellable_quantity"] = rows["sellable_quantity"].fillna(0).astype(int)
    rows["cost"] = rows["cost"].fillna(0.0).astype(float)
    rows = rows[rows["quantity"] > 0].copy()
    rows["locked_quantity"] = rows["quantity"] - rows["sellable_quantity"]
    return cash, rows[["symbol", "quantity", "sellable_quantity", "cost", "locked_quantity"]].reset_index(drop=True)


def validate_import_positions(positions: pd.DataFrame) -> dict[str, object]:
    if positions.empty:
        return {"passed": True, "value": "empty"}
    invalid = positions["quantity"].isna() | positions["sellable_quantity"].isna() | positions["cost"].isna()
    invalid |= positions["quantity"] < 0
    invalid |= positions["sellable_quantity"] < 0
    invalid |= positions["sellable_quantity"] > positions["quantity"]
    invalid |= positions["cost"] < 0
    invalid |= positions["symbol"].astype(str).str.len() != 6
    return {"passed": not bool(invalid.any()), "value": int(invalid.sum())}


def resolve_import_pricing(
    store: DataStore,
    config: dict[str, Any],
    asof_date: str | date | None,
) -> tuple[date, dict[str, float]]:
    try:
        _, prices, _ = store.load_market_data()
    except FileNotFoundError as exc:
        raise RuntimeError("cached market data is required before importing non-cash account positions") from exc
    if prices.empty:
        raise RuntimeError("cached prices are empty; refresh market data before importing positions")
    benchmark = store.load_benchmark(required=False)
    if asof_date is not None:
        asof = pd.to_datetime(asof_date).date()
    else:
        scoped_prices, _scope_ok, _scope_value = committed_fetch_market_scope(store, prices)
        dates = complete_market_dates(scoped_prices, benchmark, config)
        asof = dates[-1] if dates else latest_data_date(prices)
    if asof is None:
        raise RuntimeError("cannot resolve account import date from cached prices")
    return asof, apply_manual_price_overrides(config, latest_close_map_asof(prices, asof), asof)


def build_import_positions_snapshot(positions: pd.DataFrame, price_map: dict[str, float]) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame(columns=ACCOUNT_IMPORT_POSITION_COLUMNS)
    rows = []
    for row in positions.itertuples(index=False):
        close = float(price_map.get(str(row.symbol), 0.0))
        market_value = int(row.quantity) * close
        rows.append(
            {
                "symbol": str(row.symbol),
                "quantity": int(row.quantity),
                "sellable_quantity": int(row.sellable_quantity),
                "cost": round(float(row.cost), 4),
                "close": round(close, 4),
                "market_value": round(market_value, 2),
                "pnl": round(int(row.quantity) * (close - float(row.cost)), 2),
            }
        )
    return pd.DataFrame(rows, columns=ACCOUNT_IMPORT_POSITION_COLUMNS)


def write_state(store: DataStore, asof: date, cash: float, equity: float, positions: pd.DataFrame) -> None:
    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        conn.execute("delete from positions")
        conn.execute("delete from orders")
        for row in positions.itertuples(index=False):
            conn.execute(
                "insert into positions(symbol, quantity, cost, locked_quantity) values (?, ?, ?, ?)",
                (str(row.symbol), int(row.quantity), float(row.cost), int(row.locked_quantity)),
            )
        conn.execute(
            "insert or replace into account_snapshots(date, cash, equity) values (?, ?, ?)",
            (asof.isoformat(), float(cash), float(equity)),
        )


def clear_stale_execution_artifacts(output_dir: Path) -> int:
    cleared = 0
    for name in STALE_EXECUTION_ARTIFACTS:
        path = output_dir / name
        if path.exists():
            path.unlink()
            cleared += 1
    return cleared


def failed_audit_message(rows: list[dict[str, object]]) -> str:
    failed = [row for row in rows if not bool(row.get("passed"))]
    if not failed:
        return "account import validation failed"
    return "; ".join(f"{row['check']}: {row['message']} (value={row['value']}, threshold={row['threshold']})" for row in failed)
