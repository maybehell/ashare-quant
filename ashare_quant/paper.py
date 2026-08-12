from __future__ import annotations

import sqlite3
from datetime import datetime
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import mapping_sha256, orders_frame_sha256, signal_frame_sha256
from .candidate import candidate_freeze_boundary_evidence, live_strategy_hash, load_candidate_manifest, matching_candidate_id
from .capital_aware import build_capital_aware_target_weights, capital_aware_enabled
from .capital_budget import strategy_deployable_equity
from .data import DataStore, scope_market_frames_to_committed_fetch
from .factors import build_configured_target_portfolio, decompose_reference_anchor_target
from .models import Order, Position
from .order_integrity import attach_order_provenance_integrity
from .portfolio import Portfolio, generate_rebalance_orders
from .reconciliation import build_account_reconciliation
from .readiness import ensure_live_readiness, ensure_practical_readiness
from .config import project_path
from .risk import (
    benchmark_exposure_multiplier,
    benchmark_exposure_state,
    combine_exposure_multipliers,
    market_breadth_exposure_multiplier,
    market_breadth_exposure_state,
    strategy_health_exposure_state,
)
from .rules import next_business_day, next_trading_day
from .shadow import archive_shadow_outputs, write_shadow_component_outputs
from .strategy_activation import ensure_execution_epoch
from .universe import eligible_universe
from .validation import complete_market_dates, latest_close_map_asof, validate_config, validate_market_data


def load_portfolio(store: DataStore, initial_cash: float) -> Portfolio:
    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        cash_row = conn.execute("select cash from account_snapshots order by date desc limit 1").fetchone()
        cash = float(cash_row[0]) if cash_row else initial_cash
        rows = conn.execute("select symbol, quantity, cost, locked_quantity from positions").fetchall()
    positions = {
        row[0]: Position(symbol=row[0], quantity=int(row[1]), cost=float(row[2]), locked_quantity=int(row[3]))
        for row in rows
        if int(row[1]) > 0
    }
    return Portfolio(cash=cash, positions=positions)


def latest_snapshot_date(store: DataStore) -> date | None:
    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        row = conn.execute("select date from account_snapshots order by date desc limit 1").fetchone()
    return pd.to_datetime(row[0]).date() if row else None


def save_portfolio(store: DataStore, run_date: date, portfolio: Portfolio, close_prices: dict[str, float]) -> None:
    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        conn.execute("delete from positions")
        for pos in portfolio.positions.values():
            if pos.quantity > 0:
                conn.execute(
                    "insert into positions(symbol, quantity, cost, locked_quantity) values (?, ?, ?, ?)",
                    (pos.symbol, pos.quantity, pos.cost, pos.locked_quantity),
                )
        conn.execute(
            "insert or replace into account_snapshots(date, cash, equity) values (?, ?, ?)",
            (run_date.isoformat(), portfolio.cash, portfolio.equity(close_prices)),
        )
        for fill in portfolio.fills:
            conn.execute(
                "insert into trades(date, symbol, side, quantity, price, fee, amount, reason) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fill.date.isoformat(),
                    fill.symbol,
                    fill.side,
                    fill.quantity,
                    fill.price,
                    fill.fee,
                    fill.amount,
                    fill.reason,
                ),
            )


def run_paper(
    config: dict[str, Any],
    run_date: str | None = None,
    allow_stale: bool = False,
    allow_weak_strategy: bool = False,
    practical_mode: bool = False,
    current_date: date | None = None,
) -> dict[str, Path]:
    config_errors = validate_config(config)
    if config_errors:
        raise ValueError("; ".join(config_errors))
    store = DataStore.from_config(config)
    stocks, prices, fundamentals = store.load_market_data()
    stocks, prices, fundamentals, fetch_scope_passed, fetch_scope_value = (
        scope_market_frames_to_committed_fetch(store, stocks, prices, fundamentals)
    )
    if not fetch_scope_passed:
        raise RuntimeError(
            "Signal generation requires a complete committed fetch scope; "
            f"current scope is invalid: {fetch_scope_value}"
        )
    data_errors = validate_market_data(stocks, prices, fundamentals)
    if data_errors:
        raise ValueError("; ".join(data_errors))

    benchmark = store.load_benchmark(required=False)
    if run_date is None:
        if practical_mode:
            ensure_practical_readiness(config, allow_weak_strategy=allow_weak_strategy)
        else:
            ensure_live_readiness(config, allow_weak_strategy=allow_weak_strategy)
    execution_epoch = ensure_execution_epoch(config)

    dates = complete_market_dates(prices, benchmark, config)
    if not dates:
        raise RuntimeError("缺少完整行情日；请刷新行情和基准数据后重试。")
    asof = pd.to_datetime(run_date).date() if run_date else dates[-1]
    if asof not in dates:
        asof = max(dt for dt in dates if dt <= asof)
    if run_date is None and not allow_stale:
        freshness = complete_date_freshness_warning(asof, config)
        if freshness:
            raise RuntimeError(f"{freshness} 如需历史演练，请使用 paper --allow-stale。")
    reference_date = current_date or date.today()
    execution_date = resolve_order_date(dates, asof, current_date=reference_date, roll_forward=practical_mode and run_date is None)
    order_date_adjusted = execution_date != (next_trading_day(dates, asof) or next_business_day(asof))
    portfolio = load_portfolio(store, float(config["backtest"]["initial_cash"]))
    last_snapshot = latest_snapshot_date(store)
    if last_snapshot is None or last_snapshot < asof:
        portfolio.unlock_t1()

    today_prices = prices[prices["date"] == asof]
    prev_prices = prices[prices["date"] < asof].sort_values(["symbol", "date"]).groupby("symbol").tail(1)
    prev_close = dict(zip(prev_prices["symbol"], prev_prices["close"]))
    health_path = project_path(config["paths"]["output_dir"]) / "strategy_health.csv"
    try:
        strategy_health = pd.read_csv(health_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        strategy_health = pd.DataFrame()
    health_exposure = strategy_health_exposure_state(strategy_health, config)
    due_orders = filter_due_orders_for_health(read_due_orders(store, asof), portfolio, asof, health_exposure)
    portfolio.execute_orders(due_orders, asof, today_prices, stocks, prev_close, config)
    close_prices = latest_close_map_asof(prices, asof)

    universe = eligible_universe(stocks, prices, asof, config)
    weights, scores = build_configured_target_portfolio(
        prices,
        universe,
        fundamentals,
        asof,
        config,
        benchmark,
        {symbol for symbol, position in portfolio.positions.items() if position.quantity > 0},
    )
    benchmark_multiplier = benchmark_exposure_multiplier(benchmark, asof, config)
    breadth_multiplier = market_breadth_exposure_multiplier(prices, asof, config)
    market_multiplier = combine_exposure_multipliers(
        benchmark_multiplier, breadth_multiplier, config
    )
    combined_multiplier = market_multiplier * float(health_exposure["exposure"])
    if capital_aware_enabled(config):
        weights = build_capital_aware_target_weights(
            scores, close_prices, strategy_deployable_equity(portfolio.equity(close_prices), config), combined_multiplier, config, weights
        )
    else:
        weights = weights * combined_multiplier
    orders = generate_rebalance_orders(execution_date, weights, portfolio, close_prices, config)
    signal = build_signal_snapshot(scores, weights, asof, execution_date)
    signal_hash = signal_frame_sha256(signal) if not signal.empty else ""
    save_orders(store, orders)
    save_portfolio(store, asof, portfolio, close_prices)
    candidate = load_candidate_manifest(config)

    paths = write_paper_outputs(
        config,
        equity=pd.DataFrame(
            [
                {
                    "date": asof,
                    "cash": round(portfolio.cash, 2),
                    "market_value": round(portfolio.market_value(close_prices), 2),
                    "equity": round(portfolio.equity(close_prices), 2),
                    "fills": 0,
                    "turnover": 0.0,
                }
            ]
        ),
        orders=pd.DataFrame([order.__dict__ for order in orders]),
        positions=portfolio.snapshot_positions(close_prices),
        trades=read_trades(store),
        rejects=pd.DataFrame(portfolio.rejected_orders),
        provenance={
            "source": "paper",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "signal_date": asof.isoformat(),
            "execution_date": execution_date.isoformat(),
            "order_count": len(orders),
            "signal_hash": signal_hash,
            "signal_hash_schema": "actionable_v2",
            "strategy_hash": live_strategy_hash(config),
            "strategy_name": config.get("strategy", {}).get("name", ""),
            "execution_epoch_id": execution_epoch["execution_epoch_id"],
            "execution_mode": execution_epoch["execution_mode"],
            "execution_candidate_id": execution_epoch["execution_candidate_id"],
            "execution_strategy_hash": execution_epoch["execution_strategy_hash"],
            "config_sha256": mapping_sha256(config),
            "candidate_id": matching_candidate_id(config, candidate),
            "allow_weak_strategy": bool(allow_weak_strategy),
            "readiness_mode": "practical" if practical_mode else "strict",
            "order_date_adjusted": bool(order_date_adjusted),
            "signal_age_days": max(0, (reference_date - asof).days),
            "benchmark_exposure_multiplier": benchmark_multiplier,
            "breadth_exposure_multiplier": breadth_multiplier,
            "strategy_health_exposure_multiplier": health_exposure["exposure"],
            "combined_exposure_multiplier": combined_multiplier,
            "committed_fetch_scope_passed": bool(fetch_scope_passed),
            "committed_fetch_scope": fetch_scope_value,
            "strategy_health_exposure_reason": health_exposure["reason"],
        },
        signal=signal,
    )
    store.log_run("paper", "ok", f"generated {len(orders)} orders for {execution_date}")
    return paths


def run_shadow_signals(
    config: dict[str, Any],
    run_date: str | None = None,
    allow_stale: bool = False,
) -> dict[str, Path]:
    config_errors = validate_config(config)
    if config_errors:
        raise ValueError("; ".join(config_errors))
    store = DataStore.from_config(config)
    stocks, prices, fundamentals = store.load_market_data()
    stocks, prices, fundamentals, fetch_scope_passed, fetch_scope_value = (
        scope_market_frames_to_committed_fetch(store, stocks, prices, fundamentals)
    )
    if not fetch_scope_passed:
        raise RuntimeError(
            "Signal generation requires a complete committed fetch scope; "
            f"current scope is invalid: {fetch_scope_value}"
        )
    data_errors = validate_market_data(stocks, prices, fundamentals)
    if data_errors:
        raise ValueError("; ".join(data_errors))

    benchmark = store.load_benchmark(required=False)

    dates = complete_market_dates(prices, benchmark, config)
    if not dates:
        raise RuntimeError("缺少完整行情日；请刷新行情和基准数据后重试。")
    asof = pd.to_datetime(run_date).date() if run_date else dates[-1]
    if asof not in dates:
        asof = max(dt for dt in dates if dt <= asof)
    if run_date is None and not allow_stale:
        freshness = complete_date_freshness_warning(asof, config)
        if freshness:
            raise RuntimeError(f"{freshness} 如需历史演练，请使用 shadow --allow-stale。")
    execution_date = resolve_order_date(dates, asof)
    candidate = load_candidate_manifest(config)
    if str(candidate.get("freeze_mode", "")) == "forward_only_preregistration":
        boundary_ok, freeze_date, _ = candidate_freeze_boundary_evidence(candidate)
        freeze_boundary = pd.to_datetime(freeze_date, errors="coerce")
        if not boundary_ok or pd.isna(freeze_boundary):
            raise RuntimeError("Forward-only candidate freeze boundary is unsafe.")
        if execution_date <= freeze_boundary.date():
            paths = write_shadow_waiting_outputs(
                config, candidate, asof, execution_date
            )
            store.log_run(
                "shadow",
                "waiting",
                f"execution={execution_date};freeze={freeze_boundary.date()}",
            )
            return paths
    portfolio = load_portfolio(store, float(config["backtest"]["initial_cash"]))
    today_prices = prices[prices["date"] == asof]
    close_prices = latest_close_map_asof(prices, asof)

    universe = eligible_universe(stocks, prices, asof, config)
    base_weights, scores = build_configured_target_portfolio(
        prices,
        universe,
        fundamentals,
        asof,
        config,
        benchmark,
        {symbol for symbol, position in portfolio.positions.items() if position.quantity > 0},
    )
    benchmark_exposure = benchmark_exposure_state(benchmark, asof, config)
    breadth_exposure = market_breadth_exposure_state(prices, asof, config)
    exposure_multiplier = combine_exposure_multipliers(
        float(benchmark_exposure["exposure"]),
        float(breadth_exposure["exposure"]),
        config,
    )
    anchor_weights, satellite_weights = decompose_reference_anchor_target(
        scores, base_weights, config
    )
    if capital_aware_enabled(config):
        weights = build_capital_aware_target_weights(
            scores, close_prices, strategy_deployable_equity(portfolio.equity(close_prices), config), exposure_multiplier, config, base_weights
        )
    else:
        weights = base_weights * exposure_multiplier
    anchor_weights = anchor_weights * exposure_multiplier
    satellite_weights = satellite_weights * exposure_multiplier
    orders = generate_rebalance_orders(execution_date, weights, portfolio, close_prices, config)
    signal = build_signal_snapshot(scores, weights, asof, execution_date)
    configured_max_weight = float(config.get("strategy", {}).get("max_stock_weight", 1.0))
    max_target_weight = (
        float(pd.to_numeric(signal["target_weight"], errors="coerce").max())
        if not signal.empty
        else 0.0
    )
    if max_target_weight > configured_max_weight + 1e-10:
        raise RuntimeError("Shadow signal violates strategy.max_stock_weight; evidence was not archived.")
    candidate = load_candidate_manifest(config)
    provenance = {
        "source": "shadow",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "signal_date": asof.isoformat(),
        "execution_date": execution_date.isoformat(),
        "order_count": len(orders),
        "signal_hash": signal_frame_sha256(signal) if not signal.empty else "",
        "signal_hash_schema": "actionable_v2",
        "strategy_hash": live_strategy_hash(config),
        "strategy_name": config.get("strategy", {}).get("name", ""),
        "config_sha256": mapping_sha256(config),
        "candidate_id": matching_candidate_id(config, candidate),
        "allow_weak_strategy": True,
        "max_target_weight": max_target_weight,
        "configured_max_stock_weight": configured_max_weight,
        "weight_cap_passed": True,
        "benchmark_exposure_multiplier": benchmark_exposure["exposure"],
        "benchmark_bear_market": benchmark_exposure["bear_market"],
        "benchmark_high_volatility": benchmark_exposure["high_volatility"],
        "breadth_exposure_multiplier": breadth_exposure["exposure"],
        "breadth_ratio": breadth_exposure["breadth_ratio"],
        "breadth_state": breadth_exposure["breadth_state"],
        "breadth_symbol_count": breadth_exposure["breadth_symbol_count"],
        "combined_exposure_multiplier": exposure_multiplier,
        "committed_fetch_scope_passed": bool(fetch_scope_passed),
        "committed_fetch_scope": fetch_scope_value,
    }
    component_index = weights.index.astype(str)
    components = pd.DataFrame(
        {
            "signal_date": asof.isoformat(),
            "execution_date": execution_date.isoformat(),
            "symbol": component_index,
            "target_weight": weights.reindex(component_index).to_numpy(dtype=float),
            "anchor_weight": anchor_weights.reindex(component_index).fillna(0.0).to_numpy(dtype=float),
            "satellite_weight": satellite_weights.reindex(component_index).fillna(0.0).to_numpy(dtype=float),
            "signal_hash": provenance["signal_hash"],
            "strategy_hash": provenance["strategy_hash"],
            "candidate_id": provenance["candidate_id"],
        }
    )
    paths = write_shadow_outputs(
        config,
        orders=pd.DataFrame([order.__dict__ for order in orders]),
        signal=signal,
        provenance=provenance,
    )
    paths.update(write_shadow_component_outputs(config, components))
    store.log_run("shadow", "ok", f"recorded {len(orders)} shadow orders for {execution_date}")
    return paths


def write_paper_outputs(
    config: dict[str, Any],
    equity: pd.DataFrame,
    orders: pd.DataFrame,
    positions: pd.DataFrame,
    trades: pd.DataFrame,
    rejects: pd.DataFrame | None = None,
    provenance: dict[str, Any] | None = None,
    signal: pd.DataFrame | None = None,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "paper_equity_snapshot.csv": (equity, ["date", "cash", "market_value", "equity", "fills", "turnover"]),
        "orders.csv": (orders, ["date", "symbol", "side", "quantity", "reason"]),
        "positions.csv": (positions, ["symbol", "quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"]),
        "trades.csv": (trades, ["date", "symbol", "side", "quantity", "price", "fee", "amount", "reason"]),
        "paper_signal_snapshot.csv": (
            signal if signal is not None else pd.DataFrame(),
            ["signal_date", "execution_date", "rank", "symbol", "score", "target_weight", "industry", "board"],
        ),
    }
    if rejects is not None:
        frames["rejected_orders.csv"] = (rejects, ["date", "symbol", "side", "quantity", "reason"])
    provenance = dict(provenance or {"source": "paper", "generated_at": datetime.now().isoformat(timespec="seconds")})
    provenance["orders_sha256"] = orders_frame_sha256(orders)
    provenance = attach_order_provenance_integrity(config, provenance)
    provenance_frame = pd.DataFrame(
        [{"item": key, "value": value} for key, value in provenance.items()]
    )
    frames["orders_provenance.csv"] = (provenance_frame, ["item", "value"])
    reconciliation = build_account_reconciliation(config, equity, orders, positions, trades, provenance)
    frames["account_reconciliation.csv"] = (
        reconciliation,
        ["check", "passed", "value", "threshold", "message"],
    )
    paths: dict[str, Path] = {}
    for name, (frame, columns) in frames.items():
        if frame.empty:
            frame = pd.DataFrame(columns=columns)
        path = output_dir / name
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        paths[name] = path
    if signal is not None and provenance is not None:
        log_path = append_signal_log(output_dir / "signal_log.csv", provenance)
        paths["signal_log.csv"] = log_path
    return paths


def write_shadow_waiting_outputs(
    config: dict[str, Any],
    candidate: dict[str, Any],
    signal_date: date,
    execution_date: date,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_id = matching_candidate_id(config, candidate)
    generated_at = datetime.now().isoformat(timespec="seconds")
    status = {
        "status": "waiting_for_post_freeze_market_data",
        "candidate_id": candidate_id,
        "freeze_price_end": candidate.get("freeze_price_end", ""),
        "latest_complete_signal_date": signal_date.isoformat(),
        "proposed_execution_date": execution_date.isoformat(),
        "generated_at": generated_at,
        "message": "冻结边界之后尚无可执行交易日；未生成影子信号或影子订单。",
    }
    paths = {
        "shadow_orders.csv": output_dir / "shadow_orders.csv",
        "shadow_signal_snapshot.csv": output_dir / "shadow_signal_snapshot.csv",
        "shadow_provenance.csv": output_dir / "shadow_provenance.csv",
        "shadow_observation_status.csv": output_dir / "shadow_observation_status.csv",
    }
    pd.DataFrame(columns=["date", "symbol", "side", "quantity", "reason"]).to_csv(
        paths["shadow_orders.csv"], index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(
        columns=[
            "signal_date",
            "execution_date",
            "rank",
            "symbol",
            "score",
            "target_weight",
            "industry",
            "board",
        ]
    ).to_csv(paths["shadow_signal_snapshot.csv"], index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [{"item": key, "value": value} for key, value in {"source": "shadow_waiting", **status}.items()]
    ).to_csv(paths["shadow_provenance.csv"], index=False, encoding="utf-8-sig")
    pd.DataFrame([status]).to_csv(
        paths["shadow_observation_status.csv"], index=False, encoding="utf-8-sig"
    )
    paths.update(write_shadow_component_outputs(config, pd.DataFrame()))
    return paths

def write_shadow_outputs(
    config: dict[str, Any],
    orders: pd.DataFrame,
    signal: pd.DataFrame,
    provenance: dict[str, Any],
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    provenance_frame = pd.DataFrame([{"item": key, "value": value} for key, value in provenance.items()])
    frames = {
        "shadow_orders.csv": (orders, ["date", "symbol", "side", "quantity", "reason"]),
        "shadow_signal_snapshot.csv": (signal, ["signal_date", "execution_date", "rank", "symbol", "score", "target_weight", "industry", "board"]),
        "shadow_provenance.csv": (provenance_frame, ["item", "value"]),
    }
    paths: dict[str, Path] = {}
    for name, (frame, columns) in frames.items():
        if frame.empty:
            frame = pd.DataFrame(columns=columns)
        path = output_dir / name
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        paths[name] = path
    paths["shadow_signal_log.csv"] = append_signal_log(output_dir / "shadow_signal_log.csv", provenance)
    paths.update(
        archive_shadow_outputs(
            output_dir / "shadow_archive",
            str(provenance.get("signal_date", "")),
            orders,
            signal,
            provenance_frame,
        )
    )
    return paths


def build_signal_snapshot(scores: pd.DataFrame, weights: pd.Series, signal_date: date, execution_date: date) -> pd.DataFrame:
    columns = ["signal_date", "execution_date", "rank", "symbol", "score", "target_weight", "industry", "board"]
    if scores.empty or weights.empty:
        return pd.DataFrame(columns=columns)
    selected = scores[scores["symbol"].astype(str).isin(weights.index.astype(str))].copy()
    selected["target_weight"] = selected["symbol"].astype(str).map(weights.astype(float)).fillna(0.0)
    selected = selected[selected["target_weight"] > 0].sort_values(["target_weight", "score"], ascending=[False, False]).reset_index(drop=True)
    selected["rank"] = range(1, len(selected) + 1)
    selected["signal_date"] = signal_date.isoformat()
    selected["execution_date"] = execution_date.isoformat()
    for column in ["industry", "board"]:
        if column not in selected:
            selected[column] = ""
    return selected[columns]


def append_signal_log(path: Path, provenance: dict[str, Any]) -> Path:
    row = {
        "generated_at": provenance.get("generated_at", ""),
        "signal_date": provenance.get("signal_date", ""),
        "execution_date": provenance.get("execution_date", ""),
        "order_count": provenance.get("order_count", 0),
        "signal_hash": provenance.get("signal_hash", ""),
        "signal_hash_schema": provenance.get("signal_hash_schema", ""),
        "strategy_hash": provenance.get("strategy_hash", ""),
        "strategy_name": provenance.get("strategy_name", ""),
        "config_sha256": provenance.get("config_sha256", ""),
        "candidate_id": provenance.get("candidate_id", ""),
        "allow_weak_strategy": provenance.get("allow_weak_strategy", False),
    }
    current = pd.DataFrame([row])
    if path.exists():
        previous = pd.read_csv(path)
        if "signal_date" in previous.columns:
            same_evidence = previous["signal_date"].astype(str) == str(row["signal_date"])
            if str(row["candidate_id"]):
                previous_candidate = (
                    previous["candidate_id"].fillna("").astype(str)
                    if "candidate_id" in previous.columns
                    else pd.Series("", index=previous.index)
                )
                same_evidence &= previous_candidate == str(row["candidate_id"])
            elif str(row["strategy_hash"]) and "strategy_hash" in previous.columns:
                same_evidence &= previous["strategy_hash"].fillna("").astype(str) == str(row["strategy_hash"])
            previous = previous[~same_evidence]
        current = pd.concat([previous, current], ignore_index=True)
    current.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def filter_due_orders_for_health(
    orders: list[Order],
    portfolio: Portfolio,
    trade_date: date,
    health_exposure: dict[str, Any],
) -> list[Order]:
    if float(health_exposure.get("exposure", 1.0)) > 0:
        return orders
    allowed: list[Order] = []
    for order in orders:
        if str(order.side).lower() == "buy":
            portfolio.reject(order, trade_date, "strategy_health_paused_buy")
        else:
            allowed.append(order)
    return allowed


def save_orders(store: DataStore, orders: list[Order]) -> None:
    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        conn.execute("delete from orders")
        for order in orders:
            conn.execute(
                "insert into orders(date, symbol, side, quantity, reason) values (?, ?, ?, ?, ?)",
                (order.date.isoformat(), order.symbol, order.side, order.quantity, order.reason),
            )


def resolve_order_date(
    dates: list[date],
    asof: date,
    current_date: date | None = None,
    roll_forward: bool = False,
) -> date:
    candidate = next_trading_day(dates, asof) or next_business_day(asof)
    if roll_forward and current_date is not None and candidate < current_date:
        return next_business_day(current_date)
    return candidate


def read_due_orders(store: DataStore, asof: date) -> list[Order]:
    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        rows = conn.execute(
            "select date, symbol, side, quantity, reason from orders where date <= ? order by date, symbol",
            (asof.isoformat(),),
        ).fetchall()
    return [
        Order(
            date=pd.to_datetime(row[0]).date(),
            symbol=str(row[1]).zfill(6),
            side=row[2],
            quantity=int(row[3]),
            reason=row[4],
        )
        for row in rows
    ]


def read_trades(store: DataStore) -> pd.DataFrame:
    store.init_db()
    with sqlite3.connect(store.state_db) as conn:
        return pd.read_sql_query("select * from trades", conn)


def complete_date_freshness_warning(asof: date, config: dict[str, Any], today: date | None = None) -> str | None:
    reference = today or date.today()
    age = max(0, (reference - asof).days)
    max_age = int(config.get("data", {}).get("max_cache_age_days", 10))
    if age > max_age:
        return f"完整行情缓存已过期：最新完整行情日期 {asof}，距今天 {age} 天，超过允许的 {max_age} 天。请先刷新行情池。"
    return None
