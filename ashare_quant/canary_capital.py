from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .autolive import (
    auto_live_account_snapshot_binding_status,
    auto_live_account_snapshot_environment_status,
    auto_live_account_snapshot_freshness_status,
    parse_bool_like,
    read_csv,
)
from .broker_preview import BROKER_PREVIEW_AUDIT_COLUMNS, check_passed, file_state, number
from .config import load_config, project_path
from .data import DataStore, committed_fetch_market_scope
from .io_utils import atomic_to_csv
from .rules import calc_fee
from .universe import eligible_universe
from .validation import complete_market_dates, validate_market_data


BROKER_CAPITAL_TRANSITION_COLUMNS = [
    "status",
    "allowed_mode",
    "snapshot_fresh",
    "current_total_asset",
    "available_cash",
    "minimum_full_strategy_equity",
    "full_strategy_capital_gap",
    "current_to_required_ratio",
    "full_strategy_ready",
    "canary_ready",
    "cheapest_canary_total_cost",
    "strategy_identity_preserved",
    "message",
]


BROKER_CANARY_CAPITAL_COLUMNS = [
    "asof",
    "rank",
    "symbol",
    "name",
    "board",
    "reference_price",
    "lot_size",
    "one_lot_amount",
    "estimated_buy_fee",
    "estimated_total_cost",
    "available_cash",
    "canary_notional_limit",
    "avg_amount",
    "limit_affordable",
    "cash_affordable",
    "affordable",
    "executable",
    "scope",
]


def broker_canary_capital_limits(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live"), dict) else {}
    rollout = auto.get("rollout", {}) if isinstance(auto.get("rollout"), dict) else {}
    stage = str(rollout.get("stage", "") or "").strip().lower()
    stages = rollout.get("stages", {}) if isinstance(rollout.get("stages"), dict) else {}
    canary = stages.get("canary", {}) if isinstance(stages.get("canary"), dict) else {}
    max_orders = int(number(canary.get("max_orders", 0)))
    limits = [number(canary.get(key, 0.0)) for key in ("max_single_order_amount", "max_buy_amount", "max_gross_amount")]
    limits = [value for value in limits if value > 0]

    bridge_required = False
    bridge_ok = True
    bridge_value = "not_configured"
    cert = auto.get("desktop_canary_certification", {}) if isinstance(auto.get("desktop_canary_certification"), dict) else {}
    bridge_path_value = str(cert.get("bridge_config", "") or "").strip()
    if bridge_path_value:
        bridge_required = True
        try:
            bridge = load_config(project_path(bridge_path_value))
            huatai = bridge.get("huatai", {}) if isinstance(bridge.get("huatai"), dict) else {}
            policy = huatai.get("desktop_canary", {}) if isinstance(huatai.get("desktop_canary"), dict) else {}
            bridge_amount = number(policy.get("max_order_amount", 0.0))
            bridge_orders = int(number(policy.get("max_orders", 0)))
            bridge_ok = bool(policy.get("enabled", False)) and bridge_orders == 1 and 0 < bridge_amount <= 1000.0
            bridge_value = f"max_orders={bridge_orders};max_order_amount={bridge_amount:.2f}"
            if bridge_amount > 0:
                limits.append(bridge_amount)
        except Exception as exc:
            bridge_ok = False
            bridge_value = f"{type(exc).__name__}: {exc}"

    notional_limit = min(limits) if limits else 0.0
    ready = stage == "canary" and max_orders == 1 and 0 < notional_limit <= 1000.0 and (bridge_ok or not bridge_required)
    return {
        "ready": ready,
        "notional_limit": notional_limit,
        "value": f"stage={stage or 'missing'};max_orders={max_orders};notional_limit={notional_limit:.2f};bridge={bridge_value}",
    }


def estimated_canary_buy_fee(amount: float, config: dict[str, Any]) -> float:
    trading = config.get("trading", {}) if isinstance(config.get("trading"), dict) else {}
    try:
        return max(float(calc_fee("buy", amount, trading)), 0.0)
    except (KeyError, TypeError, ValueError):
        commission = max(amount * number(trading.get("commission_rate", 0.0003)), number(trading.get("min_commission", 5.0)))
        transfer = amount * number(trading.get("transfer_fee_rate", 0.00001))
        return max(commission + transfer, 0.0)


def audit_value(frame: pd.DataFrame, check: str) -> str:
    if frame.empty or not {"check", "value"}.issubset(frame.columns):
        return "missing"
    selected = frame[frame["check"].astype(str) == check]
    if selected.empty:
        return "missing"
    value = selected.iloc[-1].get("value", "")
    return "missing" if pd.isna(value) or str(value).strip() == "" else str(value).strip()


def _canary_certification_path(config: dict[str, Any], output_dir: Path) -> Path:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live"), dict) else {}
    cert = auto.get("desktop_canary_certification", {}) if isinstance(auto.get("desktop_canary_certification"), dict) else {}
    value = str(cert.get("certification_path", "") or "").strip()
    return project_path(value) if value else output_dir / "huatai_desktop_canary_certification.json"


def write_broker_canary_capital_feasibility(config: dict[str, Any]) -> dict[str, Path]:
    """Audit one-board-lot Canary affordability without creating executable artifacts."""
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "broker_canary_capital_audit.csv"
    candidates_path = output_dir / "broker_canary_capital_candidates.csv"
    transition_path = output_dir / "broker_capital_transition.csv"
    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    strategy_audit = read_csv(output_dir / "broker_capital_feasibility_audit.csv")
    state_db_value = str(config["paths"].get("state_db", "") or "").strip()
    protected_paths = [
        output_dir / "orders.csv",
        output_dir / "rpa_orders.csv",
        output_dir / "auto_live_submission_intent.csv",
        _canary_certification_path(config, output_dir),
    ]
    if state_db_value:
        protected_paths.insert(0, project_path(state_db_value))
    before_state = {str(path): file_state(path) for path in protected_paths}
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    snapshot_present = len(snapshot) == 1
    environment_ok, environment_value = auto_live_account_snapshot_environment_status(config, snapshot)
    binding_ok, binding_value = auto_live_account_snapshot_binding_status(config, snapshot)
    freshness_ok, freshness_value, freshness_threshold = auto_live_account_snapshot_freshness_status(config, snapshot)
    add("canary_snapshot_present", snapshot_present, len(snapshot), 1, "Execution Canary affordability requires exactly one broker snapshot.")
    add("canary_snapshot_environment_live", environment_ok, environment_value, "live", "Simulation funds cannot certify a live execution probe.")
    add("canary_snapshot_config_match", binding_ok, binding_value, "current auto_live config", "The snapshot must be bound to the current guarded configuration.")
    add("canary_snapshot_fresh", freshness_ok, freshness_value, freshness_threshold, "Available cash must be re-read immediately before any future Canary review.")

    account = snapshot.iloc[0] if snapshot_present else pd.Series(dtype=object)
    cash = max(number(account.get("cash", 0.0)), 0.0)
    available_cash = max(number(account.get("available_cash", 0.0)), 0.0)
    available_cash_valid = snapshot_present and available_cash <= cash + 1e-6
    add(
        "canary_available_cash_consistent",
        available_cash_valid,
        f"cash={cash:.2f};available_cash={available_cash:.2f}",
        "0 <= available_cash <= cash",
        "Only broker-reported available cash is spendable; total cash is never used as a fallback.",
    )
    add(
        "canary_available_cash_positive",
        snapshot_present and available_cash > 0,
        round(available_cash, 2),
        "> 0",
        "A zero available balance blocks the Canary even when total cash or assets are positive.",
    )

    limits = broker_canary_capital_limits(config)
    add(
        "canary_one_order_limit_contract",
        bool(limits["ready"]),
        str(limits["value"]),
        "stage=canary;max_orders=1;0 < notional_limit <= 1000",
        "The execution probe inherits the strictest main-system and desktop-bridge notional limit.",
    )

    strategy_gate_present = not strategy_audit.empty and "check" in strategy_audit.columns and bool(
        (strategy_audit["check"].astype(str) == "broker_capital_feasibility_ready").any()
    )
    strategy_ready = check_passed(strategy_audit, "broker_capital_feasibility_ready")
    minimum_capital = audit_value(strategy_audit, "capital_minimum_equity_for_integer_tracking")
    if minimum_capital == "missing":
        minimum_capital = audit_value(strategy_audit, "capital_minimum_equity_for_coverage")
    add(
        "canary_strategy_capital_gate_separate",
        strategy_gate_present,
        f"strategy_ready={strategy_ready};minimum_equity={minimum_capital}",
        "full-strategy capital audit remains independently recorded",
        "Canary affordability never satisfies, overrides, or promotes the full strategy portfolio capital gate.",
    )
    add(
        "canary_scope_execution_only",
        True,
        "execution_probe_only",
        "not strategy deployment",
        "This audit only estimates one board lot and cannot authorize paper trading, live promotion, or submission.",
    )

    candidates = pd.DataFrame(columns=BROKER_CANARY_CAPITAL_COLUMNS)
    market_ready = False
    market_current = False
    market_scope_ready = False
    market_scope_value = "manifest_not_checked"
    market_value = "missing"
    market_current_value = "asof=missing;benchmark_latest=missing"
    asof: date | None = None
    if snapshot_present and environment_ok and binding_ok and freshness_ok and bool(limits["ready"]):
        try:
            store = DataStore.from_config(config)
            stocks, prices, fundamentals = store.load_market_data()
            benchmark = store.load_benchmark(required=False)
            benchmark_dates = pd.to_datetime(
                benchmark.get("date", pd.Series(dtype=object)), errors="coerce"
            ).dropna()
            benchmark_latest = (
                benchmark_dates.max().date() if not benchmark_dates.empty else None
            )
            data_errors = validate_market_data(stocks, prices, fundamentals)
            if data_errors:
                raise RuntimeError("; ".join(data_errors))
            scoped_prices, market_scope_ready, market_scope_value = committed_fetch_market_scope(
                store, prices
            )
            if not market_scope_ready:
                raise RuntimeError(f"committed fetch universe invalid: {market_scope_value}")
            dates = complete_market_dates(scoped_prices, benchmark, config)
            if not dates:
                raise RuntimeError("no complete market date is available")
            asof = dates[-1]
            universe = eligible_universe(stocks, scoped_prices, asof, config).copy()
            if universe.empty:
                raise RuntimeError("eligible universe is empty")
            universe["symbol"] = universe["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
            daily = scoped_prices.copy()
            daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.date
            daily["symbol"] = daily["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
            daily = daily[daily["date"] == asof].copy()
            for column in ("close", "volume", "amount"):
                if column not in daily.columns:
                    daily[column] = 0.0
                daily[column] = pd.to_numeric(daily[column], errors="coerce").fillna(0.0)
            daily = daily[(daily["close"] > 0) & (daily["volume"] > 0) & (daily["amount"] > 0)]
            universe_columns = [column for column in ("symbol", "name", "board", "avg_amount") if column in universe.columns]
            frame = universe[universe_columns].drop_duplicates("symbol").merge(
                daily[["symbol", "close", "amount"]].drop_duplicates("symbol"),
                on="symbol",
                how="inner",
            )
            if frame.empty:
                raise RuntimeError("no eligible stock traded on the latest complete market date")
            lot_size = max(int(config.get("trading", {}).get("lot_size", 100)), 1)
            notional_limit = float(limits["notional_limit"])
            frame["reference_price"] = frame["close"]
            frame["one_lot_amount"] = frame["reference_price"] * lot_size
            frame["estimated_buy_fee"] = frame["one_lot_amount"].map(lambda value: estimated_canary_buy_fee(float(value), config))
            frame["estimated_total_cost"] = frame["one_lot_amount"] + frame["estimated_buy_fee"]
            frame["limit_affordable"] = frame["one_lot_amount"] <= notional_limit + 1e-6
            frame["cash_affordable"] = frame["estimated_total_cost"] <= available_cash + 1e-6
            frame["affordable"] = frame["limit_affordable"] & frame["cash_affordable"]
            fallback_amount = frame["amount"] if "amount" in frame.columns else pd.Series([0.0] * len(frame), index=frame.index)
            frame["avg_amount"] = pd.to_numeric(frame.get("avg_amount", fallback_amount), errors="coerce").fillna(fallback_amount)
            frame = frame.sort_values(["affordable", "avg_amount", "one_lot_amount", "symbol"], ascending=[False, False, True, True])
            canary_cfg = config.get("auto_live", {}).get("canary_capital", {})
            if not isinstance(canary_cfg, dict):
                canary_cfg = {}
            max_rows = max(int(canary_cfg.get("max_candidate_rows", 50)), 1)
            frame = frame.head(max_rows).reset_index(drop=True)
            frame["asof"] = asof.isoformat()
            frame["rank"] = frame.index + 1
            frame["lot_size"] = lot_size
            frame["available_cash"] = available_cash
            frame["canary_notional_limit"] = notional_limit
            frame["executable"] = False
            frame["scope"] = "execution_probe_only"
            for column, default in (("name", ""), ("board", "")):
                if column not in frame.columns:
                    frame[column] = default
            candidates = frame[BROKER_CANARY_CAPITAL_COLUMNS].copy()
            market_ready = True
            market_value = f"asof={asof};eligible_traded={len(frame)}"
            market_current = bool(benchmark_latest is not None and asof >= benchmark_latest)
            market_current_value = (
                f"asof={asof};benchmark_latest="
                f"{benchmark_latest if benchmark_latest is not None else 'missing'}"
            )
        except Exception as exc:
            market_value = f"{type(exc).__name__}: {exc}"

    add(
        "canary_market_universe_bound",
        market_scope_ready,
        market_scope_value,
        "complete fetch; requested=fetched=bound symbols; failed=0",
        "Canary completeness must use the latest committed fetch universe rather than unrelated symbols retained in historical cache.",
    )
    add(
        "canary_market_evidence_ready",
        market_ready,
        market_value,
        "latest complete market date with eligible traded stocks",
        "Reference closes are diagnostic only; execution-day suspension and price limits remain mandatory rechecks.",
    )
    add(
        "canary_market_evidence_current",
        market_ready and market_current,
        market_current_value,
        "candidate asof >= latest cached benchmark session",
        "A stale stock-universe close cannot certify current one-lot affordability even when the broker cash snapshot is fresh.",
    )
    affordable_count = int(candidates["affordable"].map(parse_bool_like).sum()) if not candidates.empty else 0
    cheapest_series = pd.to_numeric(candidates.get("estimated_total_cost", pd.Series(dtype=float)), errors="coerce")
    cheapest = float(cheapest_series.min()) if not cheapest_series.empty else float("nan")
    cheapest_text = "missing" if pd.isna(cheapest) else f"{cheapest:.2f}"
    add(
        "canary_one_lot_candidate_affordable",
        market_ready and market_current and affordable_count > 0 and available_cash > 0,
        f"affordable={affordable_count};cheapest_total={cheapest_text};available_cash={available_cash:.2f}",
        ">=1 eligible 100-share lot within cash and Canary notional limits",
        "Candidate presence proves estimated affordability only and never selects or submits an order.",
    )
    candidates_non_executable = candidates.empty or not bool(candidates["executable"].map(parse_bool_like).any())
    add("canary_candidates_non_executable", candidates_non_executable, False, False, "Every candidate row is explicitly non-executable.")
    after_state = {str(path): file_state(path) for path in protected_paths}
    protected_unchanged = before_state == after_state
    add(
        "canary_execution_artifacts_unchanged",
        protected_unchanged,
        str(after_state),
        str(before_state),
        "The audit must not create or modify the live ledger, orders, RPA tickets, submit intent, or Canary certificate.",
    )
    ready = bool(
        snapshot_present
        and environment_ok
        and binding_ok
        and freshness_ok
        and available_cash_valid
        and available_cash > 0
        and limits["ready"]
        and strategy_gate_present
        and market_scope_ready
        and market_ready
        and market_current
        and affordable_count > 0
        and candidates_non_executable
        and protected_unchanged
    )
    add(
        "broker_canary_capital_ready",
        ready,
        f"execution_probe_affordable={ready};strategy_portfolio_ready={strategy_ready}",
        "all execution-only affordability checks passed",
        "Passing permits human review of a future one-lot Canary only; all strategy, account, risk, session, confirmation and submission gates stay closed.",
    )
    total_asset = max(number(account.get("total_asset", cash)), 0.0)
    minimum_equity = number(minimum_capital) if minimum_capital != "missing" else float("nan")
    capital_gap = (
        max(minimum_equity - total_asset, 0.0)
        if not pd.isna(minimum_equity) and minimum_equity > 0
        else float("nan")
    )
    capital_ratio = (
        total_asset / minimum_equity
        if not pd.isna(minimum_equity) and minimum_equity > 0
        else float("nan")
    )
    allowed_mode = (
        "full_strategy_review"
        if strategy_ready and freshness_ok
        else "execution_canary_review_only"
        if ready
        else "blocked"
    )
    transition_status = (
        "ready_for_full_strategy_review"
        if allowed_mode == "full_strategy_review"
        else "ready_for_execution_canary_review"
        if allowed_mode == "execution_canary_review_only"
        else "snapshot_stale"
        if snapshot_present and not freshness_ok
        else "market_evidence_stale"
        if market_ready and not market_current
        else "insufficient_available_cash"
        if snapshot_present and available_cash <= 0
        else "insufficient_capital"
    )
    transition = pd.DataFrame(
        [
            {
                "status": transition_status,
                "allowed_mode": allowed_mode,
                "snapshot_fresh": freshness_ok,
                "current_total_asset": total_asset,
                "available_cash": available_cash,
                "minimum_full_strategy_equity": minimum_equity,
                "full_strategy_capital_gap": capital_gap,
                "current_to_required_ratio": capital_ratio,
                "full_strategy_ready": strategy_ready,
                "canary_ready": ready,
                "cheapest_canary_total_cost": cheapest,
                "strategy_identity_preserved": True,
                "message": (
                    "Execution Canary affordability is isolated from full-strategy replication; "
                    "neither mode changes the frozen strategy identity."
                ),
            }
        ],
        columns=BROKER_CAPITAL_TRANSITION_COLUMNS,
    )
    atomic_to_csv(candidates, candidates_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(pd.DataFrame(rows, columns=BROKER_PREVIEW_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(transition, transition_path, index=False, encoding="utf-8-sig")
    return {
        "broker_canary_capital_audit.csv": audit_path,
        "broker_canary_capital_candidates.csv": candidates_path,
        "broker_capital_transition.csv": transition_path,
    }
