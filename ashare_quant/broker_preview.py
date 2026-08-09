from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .account import build_account_import_from_state_template, check_account_import_file
from .audit import backtest_config_sha256, mapping_sha256, orders_frame_sha256
from .autolive import (
    auto_live_account_snapshot_binding_status,
    auto_live_account_snapshot_environment_status,
    auto_live_account_snapshot_freshness_status,
    parse_bool_like,
    read_csv,
)
from .candidate import live_strategy_hash
from .capital_aware import build_capital_aware_target_weights, capital_aware_enabled
from .config import project_path
from .data import DataStore, scope_market_frames_to_committed_fetch
from .factors import build_configured_target_portfolio
from .io_utils import atomic_to_csv
from .models import Position
from .paper import build_signal_snapshot, resolve_order_date
from .portfolio import Portfolio, generate_rebalance_orders
from .risk import (
    benchmark_exposure_multiplier,
    combine_exposure_multipliers,
    market_breadth_exposure_multiplier,
    strategy_health_exposure_state,
)
from .universe import eligible_universe
from .validation import complete_market_dates, latest_close_map_asof, validate_market_data


BROKER_PREVIEW_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]
BROKER_CAPITAL_DETAIL_COLUMNS = [
    "symbol",
    "target_weight",
    "reference_price",
    "lot_size",
    "one_lot_amount",
    "required_equity_at_target_weight",
    "affordable_at_target_weight",
    "affordable_from_available_cash",
    "risk_lot_affordable_current",
    "allocated_lots_current",
    "allocated_amount_current",
    "actual_weight_current",
    "weight_gap_current",
]


def write_broker_capital_feasibility(config: dict[str, Any]) -> dict[str, Path]:
    """Audit whether the configured target portfolio is representable with broker capital."""
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "broker_capital_feasibility_audit.csv"
    detail_path = output_dir / "broker_capital_feasibility_detail.csv"
    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    snapshot_present = len(snapshot) == 1
    environment_ok, environment_value = auto_live_account_snapshot_environment_status(config, snapshot)
    binding_ok, binding_value = auto_live_account_snapshot_binding_status(config, snapshot)
    freshness_ok, freshness_value, freshness_threshold = auto_live_account_snapshot_freshness_status(config, snapshot)
    add("capital_snapshot_present", snapshot_present, len(snapshot), 1, "Capital feasibility requires exactly one broker snapshot.")
    add("capital_snapshot_environment_live", environment_ok, environment_value, "live", "Simulation capital cannot certify a live portfolio.")
    add("capital_snapshot_config_match", binding_ok, binding_value, "current auto_live config", "The capital audit must use the current live configuration.")
    add("capital_snapshot_fresh", freshness_ok, freshness_value, freshness_threshold, "Capital is rechecked from a fresh broker snapshot.")

    detail = pd.DataFrame(columns=BROKER_CAPITAL_DETAIL_COLUMNS)
    signal_ready = False
    signal_error = ""
    asof: date | None = None
    total_asset = 0.0
    available_cash = 0.0
    if snapshot_present and environment_ok and binding_ok and freshness_ok:
        try:
            store = DataStore.from_config(config)
            stocks, prices, fundamentals = store.load_market_data()
            stocks, prices, fundamentals, _scope_ok, _scope_value = (
                scope_market_frames_to_committed_fetch(store, stocks, prices, fundamentals)
            )
            benchmark = store.load_benchmark(required=False)
            data_errors = validate_market_data(stocks, prices, fundamentals)
            if data_errors:
                raise RuntimeError("; ".join(data_errors))
            dates = complete_market_dates(prices, benchmark, config)
            if not dates:
                raise RuntimeError("no complete market date is available")
            asof = dates[-1]
            universe = eligible_universe(stocks, prices, asof, config)
            weights, _ = build_configured_target_portfolio(prices, universe, fundamentals, asof, config, benchmark)
            closes = latest_close_map_asof(prices, asof)
            lot_size = max(int(config.get("trading", {}).get("lot_size", 100)), 1)
            account = snapshot.iloc[0]
            total_asset = max(number(account.get("total_asset", 0.0)), 0.0)
            available_cash = max(number(account.get("available_cash", 0.0)), 0.0)
            detail_rows: list[dict[str, object]] = []
            for symbol, raw_weight in weights.items():
                weight = max(number(raw_weight), 0.0)
                price = max(number(closes.get(str(symbol), 0.0)), 0.0)
                if weight <= 0 or price <= 0:
                    continue
                lot_amount = price * lot_size
                required_equity = lot_amount / weight
                detail_rows.append(
                    {
                        "symbol": str(symbol).zfill(6),
                        "target_weight": weight,
                        "reference_price": price,
                        "lot_size": lot_size,
                        "one_lot_amount": lot_amount,
                        "required_equity_at_target_weight": required_equity,
                        "affordable_at_target_weight": total_asset + 1e-6 >= required_equity,
                        "affordable_from_available_cash": available_cash + 1e-6 >= lot_amount,
                    }
                )
            detail = pd.DataFrame(detail_rows, columns=BROKER_CAPITAL_DETAIL_COLUMNS)
            signal_ready = not detail.empty
        except Exception as exc:
            signal_error = f"{type(exc).__name__}: {exc}"

    add(
        "capital_strategy_signal_ready",
        signal_ready,
        f"asof={asof};targets={len(detail)}" if signal_ready else signal_error or "missing",
        "current non-executable target portfolio",
        "Capital feasibility computes a target portfolio but never writes orders.",
    )
    target_weight = pd.to_numeric(detail.get("target_weight", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    total_target_weight = float(target_weight.sum())
    affordable_target = detail.get("affordable_at_target_weight", pd.Series(dtype=bool)).map(parse_bool_like) if not detail.empty else pd.Series(dtype=bool)
    covered_weight = float(target_weight[affordable_target].sum()) if len(affordable_target) == len(target_weight) else 0.0
    coverage = covered_weight / total_target_weight if total_target_weight > 0 else 0.0
    capital_cfg = config.get("auto_live", {}).get("capital_feasibility", {})
    if not isinstance(capital_cfg, dict):
        capital_cfg = {}
    min_coverage = min(max(float(capital_cfg.get("min_target_weight_coverage", 0.80)), 0.0), 1.0)
    add(
        "capital_target_weight_coverage",
        signal_ready,
        round(coverage, 6),
        "informational conservative coverage",
        "Conservative coverage requires each lot to fit inside its exact target allocation.",
    )
    max_stock_weight = min(max(number(config.get("strategy", {}).get("max_stock_weight", 0.10)), 0.0001), 1.0)
    allocation = integer_lot_allocation(detail, total_asset, max_stock_weight)
    if not detail.empty and len(allocation["lots"]) == len(detail):
        detail["risk_lot_affordable_current"] = allocation["max_lots"] > 0
        detail["allocated_lots_current"] = allocation["lots"]
        detail["allocated_amount_current"] = allocation["amounts"]
        detail["actual_weight_current"] = allocation["actual_weights"]
        detail["weight_gap_current"] = allocation["actual_weights"] - target_weight.to_numpy(dtype=float)
    integer_coverage = float(allocation["coverage"])
    integer_l1_error = float(allocation["l1_error"])
    integer_invested_ratio = float(allocation["invested_ratio"])
    target_exposure = float(allocation["target_exposure"])
    any_risk_lot = bool((allocation["max_lots"] > 0).any()) if len(allocation["max_lots"]) else False
    max_l1_error = max(float(capital_cfg.get("max_allocation_l1_error", 0.25)), 0.0)
    min_invested_ratio = min(max(float(capital_cfg.get("min_invested_ratio", 0.85)), 0.0), 1.0)
    add(
        "capital_single_lot_within_stock_cap",
        signal_ready and any_risk_lot,
        f"eligible_targets={int((allocation['max_lots'] > 0).sum()) if len(allocation['max_lots']) else 0};max_stock_weight={max_stock_weight:.2%}",
        ">= 1 target lot within stock cap",
        "At least one target board lot must fit without breaching the configured single-stock cap.",
    )
    integer_coverage_ok = signal_ready and integer_coverage + 1e-9 >= min_coverage
    add(
        "capital_integer_target_weight_coverage",
        integer_coverage_ok,
        round(integer_coverage, 6),
        f">= {min_coverage:.2f}",
        "Coverage is measured after deterministic integer-lot allocation under the stock-weight cap.",
    )
    integer_error_ok = signal_ready and integer_l1_error <= max_l1_error + 1e-9
    add(
        "capital_integer_allocation_l1_error",
        integer_error_ok,
        round(integer_l1_error, 6),
        f"<= {max_l1_error:.2f}",
        "L1 error includes both position-weight gaps and residual cash; lower is closer to the target portfolio.",
    )
    required_invested_ratio = target_exposure * min_invested_ratio
    integer_invested_ok = signal_ready and integer_invested_ratio + 1e-9 >= required_invested_ratio
    add(
        "capital_integer_invested_ratio",
        integer_invested_ok,
        round(integer_invested_ratio, 6),
        f">= {required_invested_ratio:.4f} ({min_invested_ratio:.0%} of target exposure)",
        "The integer-lot shadow allocation must deploy enough equity to represent the strategy.",
    )
    cash_affordable = detail.get("affordable_from_available_cash", pd.Series(dtype=bool)).map(parse_bool_like) if not detail.empty else pd.Series(dtype=bool)
    any_cash_lot = bool(cash_affordable.any()) if not cash_affordable.empty else False
    positions_count = int(number(snapshot.iloc[0].get("positions_count", 0))) if snapshot_present else 0
    cash_entry_ok = signal_ready and (positions_count > 0 or any_cash_lot)
    add(
        "capital_any_target_lot_affordable",
        cash_entry_ok,
        f"positions={positions_count};cash_affordable_targets={int(cash_affordable.sum()) if not cash_affordable.empty else 0}",
        "existing position or >=1 target board lot affordable",
        "A cash-only account must be able to buy at least one target board lot without financing.",
    )
    minimum_equity = minimum_equity_for_coverage(detail, min_coverage)
    add(
        "capital_minimum_equity_for_coverage",
        signal_ready,
        "missing" if minimum_equity is None else round(minimum_equity, 2),
        f"informational estimate for {min_coverage:.0%} target-weight coverage",
        "This estimate uses current closes, board lots and configured target weights.",
    )
    integer_minimum_equity = minimum_equity_for_integer_tracking(
        detail,
        max_stock_weight=max_stock_weight,
        min_coverage=min_coverage,
        max_l1_error=max_l1_error,
        min_invested_ratio=min_invested_ratio,
    )
    add(
        "capital_minimum_equity_for_integer_tracking",
        signal_ready,
        "missing" if integer_minimum_equity is None else round(integer_minimum_equity, 2),
        f"estimate for coverage>={min_coverage:.0%};l1<={max_l1_error:.2f}",
        "This is a deterministic board-lot allocation estimate, not a deposit recommendation.",
    )
    ready = bool(
        snapshot_present
        and environment_ok
        and binding_ok
        and freshness_ok
        and signal_ready
        and any_risk_lot
        and integer_coverage_ok
        and integer_error_ok
        and integer_invested_ok
        and cash_entry_ok
    )
    add(
        "broker_capital_feasibility_ready",
        ready,
        (
            f"integer_coverage={integer_coverage:.2%};l1_error={integer_l1_error:.4f};"
            f"minimum_equity={integer_minimum_equity:.2f}"
            if integer_minimum_equity is not None
            else "blocked"
        ),
        "fresh live capital and sufficient board-lot coverage",
        "A strategy that cannot be represented at the broker account size must not enter live execution.",
    )
    atomic_to_csv(detail, detail_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(pd.DataFrame(rows, columns=BROKER_PREVIEW_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    return {
        "broker_capital_feasibility_audit.csv": audit_path,
        "broker_capital_feasibility_detail.csv": detail_path,
    }


def integer_lot_allocation(detail: pd.DataFrame, equity: float, max_stock_weight: float) -> dict[str, Any]:
    size = len(detail)
    empty = pd.Series([0] * size, dtype="int64")
    result: dict[str, Any] = {
        "lots": empty.copy(),
        "max_lots": empty.copy(),
        "amounts": pd.Series([0.0] * size, dtype=float),
        "actual_weights": pd.Series([0.0] * size, dtype=float),
        "coverage": 0.0,
        "l1_error": 2.0 if size else 0.0,
        "invested_ratio": 0.0,
        "target_exposure": 0.0,
    }
    if detail.empty or equity <= 0 or not {"target_weight", "one_lot_amount"}.issubset(detail.columns):
        return result
    weights = pd.to_numeric(detail["target_weight"], errors="coerce").fillna(0.0).clip(lower=0.0).reset_index(drop=True)
    lot_amounts = pd.to_numeric(detail["one_lot_amount"], errors="coerce").fillna(0.0).clip(lower=0.0).reset_index(drop=True)
    cap = min(max(float(max_stock_weight), 0.0), 1.0)
    valid = lot_amounts > 0
    max_lots = pd.Series([0] * size, dtype="int64")
    max_lots.loc[valid] = ((float(equity) * cap + 1e-9) / lot_amounts.loc[valid]).apply(int)
    target_lots = pd.Series([0] * size, dtype="int64")
    target_lots.loc[valid] = ((float(equity) * weights.loc[valid] + 1e-9) / lot_amounts.loc[valid]).apply(int)
    lots = pd.concat([target_lots, max_lots], axis=1).min(axis=1).astype("int64")
    spent = float((lots * lot_amounts).sum())
    residual = max(float(equity) - spent, 0.0)
    while True:
        actual = lots * lot_amounts / float(equity)
        candidates: list[tuple[float, float, int]] = []
        for idx in range(size):
            lot_amount = float(lot_amounts.iloc[idx])
            if lot_amount <= residual + 1e-9 and lots.iloc[idx] < max_lots.iloc[idx]:
                before = abs(float(actual.iloc[idx]) - float(weights.iloc[idx]))
                after = abs(float(actual.iloc[idx]) + lot_amount / float(equity) - float(weights.iloc[idx]))
                improvement = before - after
                if improvement > 1e-12:
                    candidates.append((-improvement, lot_amount, idx))
        if not candidates:
            break
        _, lot_amount, idx = min(candidates)
        lots.iloc[idx] += 1
        residual -= lot_amount
    amounts = lots * lot_amounts
    actual_weights = amounts / float(equity)
    invested_ratio = float(amounts.sum() / float(equity))
    total_target_weight = float(weights.sum())
    coverage = float(weights[lots > 0].sum() / total_target_weight) if total_target_weight > 0 else 0.0
    target_exposure = min(total_target_weight, 1.0)
    target_cash_weight = max(1.0 - target_exposure, 0.0)
    actual_cash_weight = max(1.0 - invested_ratio, 0.0)
    l1_error = float((actual_weights - weights).abs().sum() + abs(actual_cash_weight - target_cash_weight))
    return {
        "lots": lots,
        "max_lots": max_lots,
        "amounts": amounts,
        "actual_weights": actual_weights,
        "coverage": coverage,
        "l1_error": l1_error,
        "invested_ratio": invested_ratio,
        "target_exposure": target_exposure,
    }


def minimum_equity_for_integer_tracking(
    detail: pd.DataFrame,
    *,
    max_stock_weight: float,
    min_coverage: float,
    max_l1_error: float,
    min_invested_ratio: float,
) -> float | None:
    if detail.empty or "one_lot_amount" not in detail.columns:
        return None
    lot_amounts = pd.to_numeric(detail["one_lot_amount"], errors="coerce").dropna()
    lot_amounts = lot_amounts[lot_amounts > 0]
    if lot_amounts.empty or max_stock_weight <= 0:
        return None
    lower = max(float(lot_amounts.min()) / float(max_stock_weight), 1000.0)
    conservative = minimum_equity_for_coverage(detail, min_coverage) or lower
    upper = max(conservative * 2.0, lower * 2.0, 100000.0)

    def passes(equity: float) -> bool:
        allocation = integer_lot_allocation(detail, equity, max_stock_weight)
        return bool(
            allocation["coverage"] + 1e-9 >= min_coverage
            and allocation["l1_error"] <= max_l1_error + 1e-9
            and allocation["invested_ratio"] + 1e-9 >= allocation["target_exposure"] * min_invested_ratio
        )

    while upper <= 100000000.0 and not passes(upper):
        upper *= 2.0
    if upper > 100000000.0 or not passes(upper):
        return None
    coarse_step = max(1000.0, (upper - lower) / 48.0)
    candidate = lower
    previous = lower
    while candidate <= upper:
        if passes(candidate):
            break
        previous = candidate
        candidate += coarse_step
    if candidate > upper:
        candidate = upper
    fine_step = max(100.0, coarse_step / 16.0)
    refined = previous
    while refined <= candidate:
        if passes(refined):
            return round(refined, 2)
        refined += fine_step
    return round(candidate, 2)


def minimum_equity_for_coverage(detail: pd.DataFrame, coverage: float) -> float | None:
    if detail.empty or not {"target_weight", "required_equity_at_target_weight"}.issubset(detail.columns):
        return None
    frame = detail.copy()
    frame["target_weight"] = pd.to_numeric(frame["target_weight"], errors="coerce")
    frame["required_equity_at_target_weight"] = pd.to_numeric(frame["required_equity_at_target_weight"], errors="coerce")
    frame = frame.dropna(subset=["target_weight", "required_equity_at_target_weight"])
    frame = frame[(frame["target_weight"] > 0) & (frame["required_equity_at_target_weight"] > 0)]
    if frame.empty:
        return None
    frame = frame.sort_values("required_equity_at_target_weight")
    target = float(frame["target_weight"].sum()) * min(max(float(coverage), 0.0), 1.0)
    cumulative = frame["target_weight"].cumsum()
    reached = frame[cumulative + 1e-12 >= target]
    if reached.empty:
        return float(frame["required_equity_at_target_weight"].max())
    return float(reached.iloc[0]["required_equity_at_target_weight"])


def write_broker_account_import_preview(config: dict[str, Any]) -> dict[str, Path]:
    """Build a dry-run account import from a verified broker snapshot without touching SQLite."""
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    positions = read_csv(output_dir / "auto_live_broker_positions.csv")
    if "symbol" in positions.columns:
        positions["symbol"] = positions["symbol"].map(lambda value: str(value).split(".")[0].zfill(6))
    broker_audit = read_csv(output_dir / "auto_live_broker_account_audit.csv")
    preview_path = output_dir / "broker_account_import_preview.csv"
    audit_path = output_dir / "broker_account_import_preview_audit.csv"
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "message": message,
            }
        )

    snapshot_present = len(snapshot) == 1
    add("broker_snapshot_present", snapshot_present, len(snapshot), 1, "Exactly one broker account snapshot is required.")
    environment_passed, environment_value = auto_live_account_snapshot_environment_status(config, snapshot)
    add("broker_environment_live", environment_passed, environment_value, "live", "Simulation snapshots cannot stage a real-account import.")
    binding_passed, binding_value = auto_live_account_snapshot_binding_status(config, snapshot)
    add("broker_snapshot_config_match", binding_passed, binding_value, "current auto_live config", "The snapshot must be bound to the current live configuration.")
    freshness_passed, freshness_value, freshness_threshold = auto_live_account_snapshot_freshness_status(config, snapshot)
    add("broker_snapshot_fresh", freshness_passed, freshness_value, freshness_threshold, "The snapshot must be refreshed immediately before account review.")

    broker_only = broker_audit
    if not broker_only.empty and "check" in broker_only.columns:
        broker_only = broker_only[~broker_only["check"].astype(str).str.startswith("local_")]
    broker_only_passed = (
        not broker_only.empty
        and "passed" in broker_only.columns
        and bool(broker_only["passed"].map(parse_bool_like).all())
    )
    failed_broker_checks = (
        broker_only.loc[~broker_only["passed"].map(parse_bool_like), "check"].astype(str).tolist()
        if not broker_only.empty and {"check", "passed"}.issubset(broker_only.columns)
        else []
    )
    add(
        "broker_only_audit_passed",
        broker_only_passed,
        ";".join(failed_broker_checks) if failed_broker_checks else f"{len(broker_only)}/{len(broker_only)}",
        "all broker-side checks passed",
        "Local reconciliation checks are deliberately excluded from this read-only staging gate.",
    )

    state_db = project_path(config["paths"]["state_db"])
    simulation_db = project_path("data/state.sqlite3")
    isolated = state_db.resolve() != simulation_db.resolve()
    add(
        "live_state_db_isolated",
        isolated,
        str(state_db.resolve()),
        f"not {simulation_db.resolve()}",
        "The Huatai live account must never share the paper-account SQLite database.",
    )
    before_state = file_state(state_db)

    preview = pd.DataFrame(columns=["cash", "symbol", "quantity", "sellable_quantity", "cost"])
    content_valid = snapshot_present and positions_content_valid(positions)
    if snapshot_present:
        total_cash = number(snapshot.iloc[0].get("cash", 0.0))
        preview = build_account_import_from_state_template(total_cash, positions)
    atomic_to_csv(preview, preview_path, index=False, encoding="utf-8-sig")
    add(
        "preview_content_valid",
        content_valid,
        f"positions={len(positions)}",
        "integer non-negative quantities and 0 <= sellable <= quantity",
        "The generated import preview preserves broker quantities and uses verified total cash.",
    )
    unclassified = 0.0
    if snapshot_present:
        account = snapshot.iloc[0]
        if "unclassified_asset_value" in snapshot.columns:
            unclassified = number(account.get("unclassified_asset_value", 0.0))
        else:
            unclassified = number(account.get("total_asset", 0.0)) - number(account.get("cash", 0.0)) - number(account.get("market_value", 0.0))
    cash_tolerance = float(config.get("reconciliation", {}).get("cash_tolerance", 1.0))
    add(
        "unclassified_assets_block_initialization",
        snapshot_present and -cash_tolerance <= unclassified <= cash_tolerance,
        f"unclassified={unclassified:.2f}",
        f"abs(value) <= {cash_tolerance:g}",
        "Unclassified broker assets are excluded from cash and must be identified before ledger initialization.",
    )

    precheck_paths = check_account_import_file(config, preview_path)
    precheck = read_csv(precheck_paths["account_import_check_audit.csv"])
    precheck_passed = (
        not precheck.empty
        and "passed" in precheck.columns
        and bool(precheck["passed"].map(parse_bool_like).all())
    )
    failed_precheck = (
        precheck.loc[~precheck["passed"].map(parse_bool_like), "check"].astype(str).tolist()
        if not precheck.empty and {"check", "passed"}.issubset(precheck.columns)
        else []
    )
    add(
        "account_import_precheck_passed",
        precheck_passed,
        ";".join(failed_precheck) if failed_precheck else f"{len(precheck)}/{len(precheck)}",
        "all dry-run import checks passed",
        "This precheck prices every broker position but does not write account state.",
    )
    precheck_snapshot = read_csv(precheck_paths["account_import_check_snapshot.csv"])
    broker_cash = number(snapshot.iloc[0].get("cash", 0.0)) if snapshot_present else 0.0
    preview_cash_values = pd.to_numeric(preview.get("cash", pd.Series(dtype=float)), errors="coerce").dropna()
    preview_cash = float(preview_cash_values.iloc[0]) if len(preview_cash_values) == 1 else float("nan")
    cash_matches = pd.notna(preview_cash) and abs(preview_cash - broker_cash) <= cash_tolerance
    add(
        "preview_uses_total_broker_cash",
        cash_matches,
        f"preview={preview_cash:.2f};broker={broker_cash:.2f}" if pd.notna(preview_cash) else "preview=missing",
        f"gap <= {cash_tolerance:g}",
        "Account-state cash includes frozen cash; order budgeting separately uses available_cash.",
    )
    broker_map = quantity_map(positions)
    preview_map = quantity_map(preview)
    positions_match = broker_map == preview_map
    add(
        "preview_positions_match_broker",
        positions_match,
        position_gap_text(preview_map, broker_map),
        "same symbols and quantities",
        "The account import preview must preserve every broker position quantity.",
    )
    broker_total_asset = number(snapshot.iloc[0].get("total_asset", 0.0)) if snapshot_present else 0.0
    checked_equity_values = pd.to_numeric(precheck_snapshot.get("equity", pd.Series(dtype=float)), errors="coerce").dropna()
    checked_equity = float(checked_equity_values.iloc[0]) if len(checked_equity_values) == 1 else float("nan")
    equity_tolerance = max(cash_tolerance, abs(broker_total_asset) * 0.005)
    equity_matches = pd.notna(checked_equity) and abs(checked_equity - broker_total_asset) <= equity_tolerance
    add(
        "preview_equity_matches_broker",
        equity_matches,
        f"preview={checked_equity:.2f};broker={broker_total_asset:.2f}" if pd.notna(checked_equity) else "preview=missing",
        f"gap <= {equity_tolerance:.2f}",
        "Cached-price valuation must reconcile with broker total assets before local-state initialization.",
    )

    after_state = file_state(state_db)
    state_unchanged = before_state == after_state
    add(
        "live_state_db_unchanged",
        state_unchanged,
        str(after_state),
        str(before_state),
        "Generating a broker account preview must not create or modify SQLite state.",
    )
    ready = all(bool(row["passed"]) for row in rows)
    add(
        "broker_account_preview_ready",
        ready,
        "ready_for_manual_import_review" if ready else "blocked",
        "all preview checks passed",
        "Passing this gate only permits manual account-import review; it never imports or submits automatically.",
    )
    atomic_to_csv(pd.DataFrame(rows, columns=BROKER_PREVIEW_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    return {
        "broker_account_import_preview.csv": preview_path,
        "broker_account_import_preview_audit.csv": audit_path,
        **precheck_paths,
    }


def positions_content_valid(positions: pd.DataFrame) -> bool:
    if positions.empty:
        return True
    required = {"symbol", "quantity", "sellable_quantity", "cost"}
    if not required.issubset(positions.columns):
        return False
    symbols = positions["symbol"].astype(str).str.zfill(6)
    quantity = pd.to_numeric(positions["quantity"], errors="coerce")
    sellable = pd.to_numeric(positions["sellable_quantity"], errors="coerce")
    cost = pd.to_numeric(positions["cost"], errors="coerce")
    numeric = pd.DataFrame({"quantity": quantity, "sellable": sellable, "cost": cost})
    if numeric.isna().any().any():
        return False
    integer_quantities = ((quantity - quantity.round()).abs() < 1e-9) & ((sellable - sellable.round()).abs() < 1e-9)
    return bool(
        symbols.str.fullmatch(r"\d{6}").all()
        and (quantity >= 0).all()
        and (sellable >= 0).all()
        and (sellable <= quantity).all()
        and (cost >= 0).all()
        and integer_quantities.all()
    )


def quantity_map(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty or not {"symbol", "quantity"}.issubset(frame.columns):
        return {}
    result: dict[str, int] = {}
    for row in frame[["symbol", "quantity"]].to_dict(orient="records"):
        symbol = str(row.get("symbol", "") or "").split(".")[0].zfill(6)
        quantity = int(number(row.get("quantity", 0)))
        if symbol != "000000" and quantity > 0:
            result[symbol] = result.get(symbol, 0) + quantity
    return result


def position_gap_text(left: dict[str, int], right: dict[str, int]) -> str:
    symbols = sorted(set(left) | set(right))
    if not symbols:
        return "no_positions"
    gaps = [abs(left.get(symbol, 0) - right.get(symbol, 0)) for symbol in symbols]
    return f"symbols={len(symbols)};max_gap={max(gaps)}"


def file_state(path: Path) -> tuple[bool, int, int]:
    if not path.exists():
        return False, 0, 0
    stat = path.stat()
    return True, stat.st_size, stat.st_mtime_ns


def number(value: object) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else float(parsed)
BROKER_ORDER_PREVIEW_COLUMNS = [
    "date",
    "symbol",
    "side",
    "quantity",
    "reason",
    "reference_price",
    "estimated_amount",
    "current_quantity",
    "sellable_quantity",
    "target_weight",
    "source_snapshot_run_id",
    "executable",
]


def write_broker_order_preview(config: dict[str, Any], current_date: date | None = None) -> dict[str, Path]:
    """Generate a non-executable rebalance preview directly from a broker snapshot."""
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / "broker_order_preview.csv"
    signal_path = output_dir / "broker_order_preview_signal.csv"
    provenance_path = output_dir / "broker_order_preview_provenance.csv"
    audit_path = output_dir / "broker_order_preview_audit.csv"
    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    broker_positions = read_csv(output_dir / "auto_live_broker_positions.csv")
    account_preview_audit = read_csv(output_dir / "broker_account_import_preview_audit.csv")
    practical = read_csv(output_dir / "practical_readiness.csv")
    health = read_csv(output_dir / "strategy_health.csv")
    deployment = read_csv(output_dir / "live_strategy_evidence_sync_summary.csv")
    state_db = project_path(config["paths"]["state_db"])
    before_state = file_state(state_db)
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    account_ready = check_passed(account_preview_audit, "broker_account_preview_ready")
    add("broker_account_preview_ready", account_ready, account_ready, True, "The broker account import preview must pass before order preview generation.")
    snapshot_environment_ok, snapshot_environment = auto_live_account_snapshot_environment_status(config, snapshot)
    add("broker_snapshot_environment_live", snapshot_environment_ok, snapshot_environment, "live", "The order preview cannot use a simulation snapshot.")
    snapshot_binding_ok, snapshot_binding = auto_live_account_snapshot_binding_status(config, snapshot)
    add("broker_snapshot_config_match", snapshot_binding_ok, snapshot_binding, "current auto_live config", "The order preview rechecks the broker snapshot configuration fingerprint.")
    snapshot_fresh, snapshot_age, snapshot_age_limit = auto_live_account_snapshot_freshness_status(config, snapshot)
    add("broker_snapshot_fresh", snapshot_fresh, snapshot_age, snapshot_age_limit, "The order preview rechecks snapshot age instead of trusting an earlier preview audit.")
    snapshot_run_id = str(snapshot.iloc[0].get("snapshot_run_id", "") or "") if len(snapshot) == 1 else ""
    position_run_ids = set(broker_positions.get("snapshot_run_id", pd.Series(dtype=str)).dropna().astype(str))
    position_batch_match = bool(snapshot_run_id) and (broker_positions.empty or position_run_ids == {snapshot_run_id})
    add("broker_positions_snapshot_match", position_batch_match, ";".join(sorted(position_run_ids)) or "empty", snapshot_run_id or "present", "Account and position rows must come from the same broker snapshot batch.")
    deployment_ready = deployment_evidence_matches(config, deployment)
    add("strategy_evidence_current", deployment_ready, deployment_status_value(deployment), "current verified read-only evidence", "The preview must use the strategy evidence published for this live configuration.")
    practical_ready = frame_all_passed(practical)
    add("practical_readiness_passed", practical_ready, passed_count_text(practical), "all practical checks passed", "Historical and cost-stress evidence must remain usable.")
    paper_allowed = strategy_health_value(health, "paper_orders_allowed")
    add("strategy_health_allows_preview", paper_allowed, paper_allowed, True, "A paused strategy cannot generate broker order previews.")

    preview = pd.DataFrame(columns=BROKER_ORDER_PREVIEW_COLUMNS)
    signal = pd.DataFrame(columns=["signal_date", "execution_date", "rank", "symbol", "score", "target_weight", "industry", "board"])
    provenance: dict[str, object] = {
        "source": "broker_snapshot_read_only_preview",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "executable": False,
        "orders_file_written": False,
        "rpa_orders_file_written": False,
    }
    generation_ok = False
    generation_error = ""
    asof: date | None = None
    execution_date: date | None = None
    order_objects = []
    close_prices: dict[str, float] = {}
    target_weights = pd.Series(dtype=float)
    portfolio = Portfolio(cash=0.0)
    try:
        if not (
            account_ready
            and snapshot_environment_ok
            and snapshot_binding_ok
            and snapshot_fresh
            and position_batch_match
            and deployment_ready
            and practical_ready
            and paper_allowed
        ):
            raise RuntimeError("one or more broker preview preconditions failed")
        if len(snapshot) != 1:
            raise RuntimeError("exactly one broker account snapshot is required")
        store = DataStore.from_config(config)
        stocks, prices, fundamentals = store.load_market_data()
        stocks, prices, fundamentals, _scope_ok, _scope_value = (
            scope_market_frames_to_committed_fetch(store, stocks, prices, fundamentals)
        )
        benchmark = store.load_benchmark(required=False)
        data_errors = validate_market_data(stocks, prices, fundamentals)
        if data_errors:
            raise RuntimeError("; ".join(data_errors))
        dates = complete_market_dates(prices, benchmark, config)
        if not dates:
            raise RuntimeError("no complete market date is available")
        asof = dates[-1]
        reference_date = current_date or date.today()
        execution_date = resolve_order_date(dates, asof, current_date=reference_date, roll_forward=True)
        close_prices = latest_close_map_asof(prices, asof)
        portfolio = portfolio_from_broker_snapshot(snapshot, broker_positions)
        universe = eligible_universe(stocks, prices, asof, config)
        current_symbols = {
            symbol for symbol, position in portfolio.positions.items() if position.quantity > 0
        }
        target_weights, scores = build_configured_target_portfolio(
            prices, universe, fundamentals, asof, config, benchmark, current_symbols
        )
        benchmark_multiplier = benchmark_exposure_multiplier(benchmark, asof, config)
        breadth_multiplier = market_breadth_exposure_multiplier(prices, asof, config)
        health_exposure = strategy_health_exposure_state(health, config)
        market_multiplier = combine_exposure_multipliers(
            benchmark_multiplier, breadth_multiplier, config
        )
        combined_multiplier = market_multiplier * float(health_exposure["exposure"])
        if capital_aware_enabled(config):
            target_weights = build_capital_aware_target_weights(
                scores, close_prices, portfolio.equity(close_prices), combined_multiplier, config
            )
        else:
            target_weights = target_weights * combined_multiplier
        order_objects = generate_rebalance_orders(execution_date, target_weights, portfolio, close_prices, config)
        signal = build_signal_snapshot(scores, target_weights, asof, execution_date)
        snapshot_run_id = str(snapshot.iloc[0].get("snapshot_run_id", "") or "")
        position_map = portfolio.positions
        order_rows = []
        for order in order_objects:
            position = position_map.get(order.symbol, Position(order.symbol))
            price = float(close_prices.get(order.symbol, 0.0) or 0.0)
            order_rows.append(
                {
                    "date": order.date.isoformat(),
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": int(order.quantity),
                    "reason": order.reason,
                    "reference_price": round(price, 4),
                    "estimated_amount": round(price * order.quantity, 2),
                    "current_quantity": int(position.quantity),
                    "sellable_quantity": int(position.sellable_quantity),
                    "target_weight": float(target_weights.get(order.symbol, 0.0)),
                    "source_snapshot_run_id": snapshot_run_id,
                    "executable": False,
                }
            )
        preview = pd.DataFrame(order_rows, columns=BROKER_ORDER_PREVIEW_COLUMNS)
        generation_ok = True
        provenance.update(
            {
                "signal_date": asof.isoformat(),
                "execution_date": execution_date.isoformat(),
                "order_count": len(preview),
                "snapshot_run_id": snapshot_run_id,
                "strategy_name": str(config.get("strategy", {}).get("name", "")),
                "strategy_hash": live_strategy_hash(config),
                "config_sha256": mapping_sha256(config),
                "backtest_config_sha256": backtest_config_sha256(config),
                "orders_sha256": orders_frame_sha256(preview[["date", "symbol", "side", "quantity", "reason"]]),
                "benchmark_exposure_multiplier": benchmark_multiplier,
                "breadth_exposure_multiplier": breadth_multiplier,
                "strategy_health_exposure_multiplier": health_exposure["exposure"],
                "combined_exposure_multiplier": combined_multiplier,
                "strategy_health_exposure_reason": health_exposure["reason"],
            }
        )
    except Exception as exc:
        generation_error = f"{type(exc).__name__}: {exc}"
    add("preview_generation_succeeded", generation_ok, "ok" if generation_ok else generation_error, "ok", "Preview generation is fail-closed and records the first failed precondition.")

    signal_age = (current_date or date.today()) - asof if asof is not None else None
    max_age = int(config.get("data", {}).get("max_cache_age_days", 10))
    signal_current = signal_age is not None and 0 <= signal_age.days <= max_age
    add("signal_date_current", signal_current, signal_age.days if signal_age is not None else "missing", f"0..{max_age} days", "The order preview cannot rely on stale market data.")
    prices_valid = preview.empty or bool((pd.to_numeric(preview["reference_price"], errors="coerce") > 0).all())
    add("all_orders_priced", prices_valid, int((pd.to_numeric(preview["reference_price"], errors="coerce") > 0).sum()) if not preview.empty else 0, len(preview), "Every preview order needs a positive reference price.")
    lot_size = int(config.get("trading", {}).get("lot_size", 100))
    buys = preview[preview["side"] == "buy"] if not preview.empty else preview
    buy_lots_valid = buys.empty or bool((pd.to_numeric(buys["quantity"], errors="coerce") % lot_size == 0).all())
    add("buy_quantities_are_board_lots", buy_lots_valid, len(buys), f"quantity % {lot_size} == 0", "A-share buy previews must use board lots.")
    sells = preview[preview["side"] == "sell"] if not preview.empty else preview
    sellable_valid = sells.empty or bool((pd.to_numeric(sells["quantity"], errors="coerce") <= pd.to_numeric(sells["sellable_quantity"], errors="coerce")).all())
    add("sell_quantities_within_broker_sellable", sellable_valid, len(sells), "sell quantity <= broker sellable quantity", "T+1 locked or frozen shares cannot appear in a broker order preview.")
    buy_amount = float(pd.to_numeric(buys["estimated_amount"], errors="coerce").fillna(0).sum()) if not buys.empty else 0.0
    sell_amount = float(pd.to_numeric(sells["estimated_amount"], errors="coerce").fillna(0).sum()) if not sells.empty else 0.0
    available_cash = float(portfolio.cash)
    cash_capacity = buy_amount <= available_cash + sell_amount + 1e-6
    add("estimated_cash_capacity", cash_capacity, f"buy={buy_amount:.2f};cash={available_cash:.2f};sell={sell_amount:.2f}", "buy <= available cash + estimated sells", "The preview cannot assume external financing.")
    non_executable = preview.empty or not bool(preview["executable"].map(parse_bool_like).any())
    add("preview_marked_non_executable", non_executable, False, False, "Preview rows are never accepted by the executable order pipeline.")
    after_state = file_state(state_db)
    state_unchanged = before_state == after_state
    add("live_state_db_unchanged", state_unchanged, str(after_state), str(before_state), "Order preview generation must not create or modify live SQLite state.")
    ready = all(bool(row["passed"]) for row in rows)
    add("broker_order_preview_ready", ready, "ready_for_manual_review" if ready else "blocked", "all preview checks passed", "This status permits visual review only; executable order export remains disabled.")

    atomic_to_csv(preview, preview_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(signal, signal_path, index=False, encoding="utf-8-sig")
    provenance_frame = pd.DataFrame([{"item": key, "value": value} for key, value in provenance.items()])
    atomic_to_csv(provenance_frame, provenance_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(pd.DataFrame(rows, columns=BROKER_PREVIEW_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    return {
        "broker_order_preview.csv": preview_path,
        "broker_order_preview_signal.csv": signal_path,
        "broker_order_preview_provenance.csv": provenance_path,
        "broker_order_preview_audit.csv": audit_path,
    }


def portfolio_from_broker_snapshot(snapshot: pd.DataFrame, positions: pd.DataFrame) -> Portfolio:
    cash = number(snapshot.iloc[0].get("available_cash", 0.0)) if len(snapshot) == 1 else 0.0
    normalized: dict[str, Position] = {}
    for row in positions.to_dict(orient="records"):
        symbol = str(row.get("symbol", "") or "").split(".")[0].zfill(6)
        quantity = max(int(number(row.get("quantity", 0))), 0)
        sellable = min(max(int(number(row.get("sellable_quantity", 0))), 0), quantity)
        if quantity <= 0:
            continue
        normalized[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            cost=max(number(row.get("cost", 0.0)), 0.0),
            locked_quantity=quantity - sellable,
        )
    return Portfolio(cash=cash, positions=normalized)


def check_passed(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False
    selected = frame[frame["check"].astype(str) == check]
    return not selected.empty and parse_bool_like(selected.iloc[-1].get("passed", False))


def frame_all_passed(frame: pd.DataFrame) -> bool:
    return not frame.empty and "passed" in frame.columns and bool(frame["passed"].map(parse_bool_like).all())


def passed_count_text(frame: pd.DataFrame) -> str:
    if frame.empty or "passed" not in frame.columns:
        return "missing"
    passed = frame["passed"].map(parse_bool_like)
    return f"{int(passed.sum())}/{len(frame)}"


def strategy_health_value(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or not {"check", "value"}.issubset(frame.columns):
        return False
    selected = frame[frame["check"].astype(str) == check]
    return not selected.empty and parse_bool_like(selected.iloc[-1].get("value", False))


def deployment_evidence_matches(config: dict[str, Any], frame: pd.DataFrame) -> bool:
    if len(frame) != 1:
        return False
    row = frame.iloc[0]
    generated_at = pd.to_datetime(row.get("generated_at", ""), errors="coerce", utc=True)
    if pd.isna(generated_at):
        return False
    age_seconds = (pd.Timestamp.now(tz="UTC") - generated_at).total_seconds()
    max_age_seconds = int(config.get("auto_live", {}).get("strategy_evidence_sync_max_age_seconds", 7200))
    fresh = -30 <= age_seconds <= max(max_age_seconds, 60)
    return (
        str(row.get("status", "") or "").strip().lower() == "ok"
        and str(row.get("live_config_sha256", "") or "") == mapping_sha256(config)
        and str(row.get("backtest_config_sha256", "") or "") == backtest_config_sha256(config)
        and not any(
            parse_bool_like(row.get(column, False))
            for column in ("orders_copied", "positions_copied", "account_state_copied")
        )
        and fresh
    )


def deployment_status_value(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "missing"
    row = frame.iloc[-1]
    return f"status={row.get('status', '')};generated_at={row.get('generated_at', '')}"
