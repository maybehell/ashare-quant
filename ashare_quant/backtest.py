from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .capital_aware import build_capital_aware_target_weights, capital_aware_enabled
from .data import DataStore, committed_fetch_market_scope, make_proxy_benchmark
from .factors import build_configured_target_portfolio
from .portfolio import Portfolio, generate_rebalance_orders
from .report import write_outputs
from .risk import (
    benchmark_exposure_state,
    combine_exposure_multipliers,
    exposure_combination_mode,
    market_breadth_exposure_state,
)
from .rules import configured_rebalance_dates, next_trading_day
from .universe import eligible_universe
from .validation import filter_complete_market_data, max_lookback_days, validate_config, validate_market_data


def run_backtest(config: dict[str, Any]) -> dict[str, Path]:
    config_errors = validate_config(config)
    if config_errors:
        raise ValueError("; ".join(config_errors))
    store = DataStore.from_config(config)
    stocks, prices, fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    scoped_prices, fetch_scope_passed, _fetch_scope_value = committed_fetch_market_scope(store, prices)
    if fetch_scope_passed:
        prices = scoped_prices
        scoped_symbols = set(prices["symbol"].astype(str))
        stocks = stocks[stocks["symbol"].astype(str).isin(scoped_symbols)].copy()
        if not fundamentals.empty and "symbol" in fundamentals.columns:
            fundamentals = fundamentals[fundamentals["symbol"].astype(str).isin(scoped_symbols)].copy()
    raw_prices = prices.copy()
    raw_benchmark = benchmark.copy()
    data_errors = validate_market_data(stocks, prices, fundamentals)
    if data_errors:
        raise ValueError("; ".join(data_errors))

    start = pd.to_datetime(config["backtest"]["start"]).date()
    end_value = config["backtest"].get("end")
    end = pd.to_datetime(end_value).date() if end_value else max(prices["date"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()
    if benchmark.empty:
        benchmark = make_proxy_benchmark(prices, config)
    benchmark = benchmark[(benchmark["date"] >= start) & (benchmark["date"] <= end)].copy()
    prices, benchmark = filter_complete_market_data(prices, benchmark, config)
    if prices.empty:
        raise ValueError("No complete market dates after applying price coverage and benchmark filters.")
    dates = sorted(prices["date"].unique())
    warmup_until_idx = max_lookback_days(config)
    usable_dates = dates[warmup_until_idx:]
    rebalance_dates = configured_rebalance_dates(usable_dates, config["backtest"].get("rebalance", "monthly"))
    prices_by_date = {trade_date: frame.copy() for trade_date, frame in prices.groupby("date", sort=False)}
    close_wide = prices.pivot(index="date", columns="symbol", values="close").sort_index().ffill()
    prev_close_wide = close_wide.shift(1)
    portfolio = Portfolio(cash=float(config["backtest"]["initial_cash"]))
    pending_orders: dict[date, list] = {}
    equity_rows = []
    order_rows = []
    risk_rows = []

    for idx, current in enumerate(dates):
        portfolio.unlock_t1()
        today_prices = prices_by_date.get(current, pd.DataFrame())
        prev_close = close_row_map(prev_close_wide, current)
        fills = portfolio.execute_orders(
            pending_orders.pop(current, []),
            current,
            today_prices,
            stocks,
            prev_close,
            config,
        )
        close_prices = close_row_map(close_wide, current)
        equity_value = portfolio.equity(close_prices)
        turnover = sum(abs(fill.amount) for fill in fills) / equity_value if equity_value else 0.0
        if idx >= warmup_until_idx:
            equity_rows.append(
                {
                    "date": current,
                    "cash": round(portfolio.cash, 2),
                    "market_value": round(portfolio.market_value(close_prices), 2),
                    "equity": round(equity_value, 2),
                    "fills": len(fills),
                    "turnover": round(turnover, 6),
                }
            )
        if current in rebalance_dates and idx >= warmup_until_idx:
            universe = eligible_universe(stocks, prices, current, config)
            current_symbols = {
                symbol for symbol, position in portfolio.positions.items() if position.quantity > 0
            }
            weights, scores = build_configured_target_portfolio(
                prices, universe, fundamentals, current, config, benchmark, current_symbols
            )
            risk_state = benchmark_exposure_state(benchmark, current, config)
            benchmark_exposure = float(risk_state["exposure"])
            breadth_state = market_breadth_exposure_state(prices, current, config)
            breadth_exposure = float(breadth_state["exposure"])
            exposure = combine_exposure_multipliers(
                benchmark_exposure, breadth_exposure, config
            )
            if capital_aware_enabled(config):
                weights = build_capital_aware_target_weights(
                    scores, close_prices, equity_value, exposure, config
                )
            else:
                weights = weights * exposure
            risk_rows.append({
                **risk_state,
                **{key: value for key, value in breadth_state.items() if key != "exposure"},
                "benchmark_exposure": benchmark_exposure,
                "exposure_combination_mode": exposure_combination_mode(config),
                "exposure": exposure,
            })
            execution_date = next_trading_day(dates, current)
            if execution_date:
                orders = generate_rebalance_orders(execution_date, weights, portfolio, close_prices, config)
                pending_orders[execution_date] = orders
                order_rows.extend([order.__dict__ for order in orders])

    paths = write_outputs(
        config,
        equity=pd.DataFrame(equity_rows),
        orders=pd.DataFrame(order_rows),
        positions=portfolio.snapshot_positions(
            close_row_map(close_wide, dates[-1])
        ),
        trades=pd.DataFrame([fill.__dict__ for fill in portfolio.fills]),
        rejects=pd.DataFrame(portfolio.rejected_orders),
        stocks=stocks,
        benchmark=benchmark,
        prices=prices,
        fundamentals=fundamentals,
        risk_exposure=pd.DataFrame(risk_rows),
        raw_prices=raw_prices,
        raw_benchmark=raw_benchmark,
        order_output_name="backtest_orders.csv",
    )
    store.log_run("backtest", "ok", f"wrote {len(paths)} output files")
    return paths


def close_row_map(close_wide: pd.DataFrame, trade_date: date) -> dict[str, float]:
    if close_wide.empty or trade_date not in close_wide.index:
        return {}
    row = pd.to_numeric(close_wide.loc[trade_date], errors="coerce").dropna()
    return {str(symbol): float(value) for symbol, value in row.items()}
