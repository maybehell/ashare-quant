from __future__ import annotations

from copy import deepcopy
from typing import Any

import pandas as pd

from .capital_aware import build_capital_aware_target_weights, capital_aware_enabled
from .factors import build_configured_target_portfolio
from .risk import (
    benchmark_exposure_multiplier,
    combine_exposure_multipliers,
    market_breadth_exposure_multiplier,
)
from .rules import buy_quantity_for_value, calc_fee, configured_rebalance_dates
from .universe import eligible_universe


def walk_forward_report(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = config.get("walk_forward", {})
    if not cfg.get("enabled", False) or prices.empty or benchmark.empty:
        return empty_folds(), empty_summary()

    dates = sorted(prices["date"].unique())
    train_days = int(cfg.get("train_days", 504))
    test_days = int(cfg.get("test_days", 126))
    step_days = int(cfg.get("step_days", test_days))
    candidates = cfg.get("candidates", [])
    selection_mode = str(cfg.get("selection_mode", "train_best")).lower()
    if selection_mode == "fixed":
        candidates = [current_strategy_candidate(config)]
    if len(dates) < train_days + test_days or not candidates:
        return empty_folds(), empty_summary()

    rows = []
    start = 0
    while start + train_days + test_days <= len(dates):
        train_dates = dates[start : start + train_days]
        test_dates = dates[start + train_days : start + train_days + test_days]
        selected, best_train = select_candidate(prices, stocks, fundamentals, benchmark, config, candidates, train_dates, selection_mode)
        best_name = str(selected.get("name", "candidate"))
        test_result = simulate_period(prices, stocks, fundamentals, benchmark, apply_candidate(config, selected), test_dates)
        rows.append(
            {
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "selected_candidate": best_name,
                "train_excess_return": best_train["excess_return"] if best_train else 0.0,
                "test_strategy_return": test_result["strategy_return"],
                "test_benchmark_return": test_result["benchmark_return"],
                "test_excess_return": test_result["excess_return"],
                "test_max_drawdown": test_result["max_drawdown"],
            }
        )
        start += step_days

    folds = pd.DataFrame(rows)
    if folds.empty:
        return empty_folds(), empty_summary()
    summary = pd.DataFrame(
        [
            {
                "folds": len(folds),
                "train_days": train_days,
                "test_days": test_days,
                "step_days": step_days,
                "selection_mode": selection_mode,
                "test_overlap_ratio": overlap_ratio(test_days, step_days),
                "positive_test_ratio": float((folds["test_excess_return"] > 0).mean()),
                "avg_test_excess_return": float(folds["test_excess_return"].mean()),
                "worst_test_excess_return": float(folds["test_excess_return"].min()),
                "avg_test_max_drawdown": float(folds["test_max_drawdown"].mean()),
            }
        ]
    )
    return folds, summary


def select_candidate(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
    candidates: list[dict[str, Any]],
    train_dates: list,
    selection_mode: str,
) -> tuple[dict[str, Any], dict[str, float]]:
    if selection_mode == "fixed":
        selected = candidates[0]
        return selected, simulate_period(prices, stocks, fundamentals, benchmark, apply_candidate(config, selected), train_dates)

    best_candidate = candidates[0]
    best_train = zero_result()
    best_score = float("-inf")
    for candidate in candidates:
        candidate_config = apply_candidate(config, candidate)
        train_result = simulate_period(prices, stocks, fundamentals, benchmark, candidate_config, train_dates)
        score = float(train_result["excess_return"])
        if score > best_score:
            best_score = score
            best_candidate = candidate
            best_train = train_result
    return best_candidate, best_train


def current_strategy_candidate(config: dict[str, Any]) -> dict[str, Any]:
    strategy = deepcopy(config.get("strategy", {}))
    strategy["name"] = strategy.get("name", "current_strategy")
    return strategy


def apply_candidate(config: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(config)
    out["strategy"] = deepcopy(config["strategy"])
    out["strategy"]["name"] = candidate.get("name", out["strategy"].get("name", "candidate"))
    if "top_n" in candidate:
        out["strategy"]["top_n"] = candidate["top_n"]
    if "weights" in candidate:
        out["strategy"]["weights"] = candidate["weights"]
    if "lookbacks" in candidate:
        out["strategy"]["lookbacks"] = candidate["lookbacks"]
    if "weighting" in candidate:
        out["strategy"]["weighting"] = candidate["weighting"]
    if "max_stock_weight" in candidate:
        out["strategy"]["max_stock_weight"] = candidate["max_stock_weight"]
    if "max_industry_weight" in candidate:
        out["strategy"]["max_industry_weight"] = candidate["max_industry_weight"]
    if "adaptive_weights" in candidate:
        out["strategy"]["adaptive_weights"] = deepcopy(candidate["adaptive_weights"])
    elif "adaptive_weights" in out["strategy"]:
        out["strategy"].pop("adaptive_weights")
    for key in ("industry_neutralization", "cross_sectional_neutralization", "industry_relative_cap", "benchmark_membership", "reference_anchor", "required_factors"):
        if key in candidate:
            out["strategy"][key] = deepcopy(candidate[key])
        elif key in out["strategy"]:
            out["strategy"].pop(key)
    if "ensemble_members" in candidate:
        out["strategy"]["ensemble_members"] = deepcopy(candidate["ensemble_members"])
    elif "ensemble_members" in out["strategy"]:
        out["strategy"].pop("ensemble_members")
    overrides = candidate.get("config_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("candidate config_overrides must be a mapping")
    for section in ("risk_overlay", "breadth_overlay"):
        if section in overrides:
            out[section] = deepcopy(overrides[section])
    if "backtest" in overrides:
        backtest_override = overrides["backtest"]
        if not isinstance(backtest_override, dict):
            raise ValueError("candidate backtest override must be a mapping")
        unexpected = sorted(set(backtest_override) - {"rebalance"})
        if unexpected:
            raise ValueError("candidate backtest override only supports rebalance")
        out.setdefault("backtest", {}).update(deepcopy(backtest_override))
    return out


def simulate_period(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
    dates: list,
) -> dict[str, float]:
    period_prices = prices[prices["date"].isin(dates)].copy()
    if period_prices.empty:
        return zero_result()
    all_prices = prices[prices["date"] <= dates[-1]].copy()
    overnight_returns, intraday_returns = session_return_matrices(all_prices)
    close_prices = all_prices.pivot(index="date", columns="symbol", values="close").sort_index()
    rebalance_dates = configured_rebalance_dates(dates, config.get("backtest", {}).get("rebalance", "monthly"))
    pending_targets: dict[object, tuple[pd.Series, float, float]] = {}
    current_weights = pd.Series(dtype=float)
    nav = []
    value = 1.0
    gross_value = 1.0
    cumulative_cost = 0.0
    total_turnover = 0.0
    rebalance_count = 0
    for idx, dt in enumerate(dates):
        if idx > 0 and not current_weights.empty and dt in overnight_returns.index:
            period_return, current_weights = apply_portfolio_returns(current_weights, overnight_returns.loc[dt])
            value *= 1 + period_return
            gross_value *= 1 + period_return
        if dt in pending_targets:
            weights, cost_fraction, turnover = pending_targets.pop(dt)
            value *= 1.0 - cost_fraction
            cumulative_cost += cost_fraction * value / max(1.0 - cost_fraction, 1e-12)
            total_turnover += turnover
            rebalance_count += 1
            current_weights = weights
        if not current_weights.empty and dt in intraday_returns.index:
            period_return, current_weights = apply_portfolio_returns(current_weights, intraday_returns.loc[dt])
            value *= 1 + period_return
            gross_value *= 1 + period_return
        nav.append({"date": dt, "strategy_nav": value, "gross_strategy_nav": gross_value})
        if dt in rebalance_dates and idx < len(dates) - 1:
            universe = eligible_universe(stocks, prices, dt, config)
            current_symbols = {
                str(symbol)
                for symbol, weight in current_weights.items()
                if float(weight) > 0
            }
            weights, scores = build_configured_target_portfolio(
                prices, universe, fundamentals, dt, config, benchmark, current_symbols
            )
            exposure = combine_exposure_multipliers(
                benchmark_exposure_multiplier(benchmark, dt, config),
                market_breadth_exposure_multiplier(prices, dt, config),
                config,
            )
            signal_prices = (
                close_prices.loc[dt]
                if dt in close_prices.index
                else pd.Series(dtype=float)
            )
            if capital_aware_enabled(config):
                initial_cash = float(config.get("backtest", {}).get("initial_cash", 1_000_000.0))
                weights = build_capital_aware_target_weights(
                    scores, signal_prices, initial_cash * value, exposure, config, weights
                )
            else:
                weights = weights * exposure
            reserve = max(0.0, min(1.0, float(config.get("trading", {}).get("reserve_cash_ratio", 0.0))))
            weights = weights * (1.0 - reserve)
            weights = quantize_rebalance_target(
                current_weights, weights, value, signal_prices, config
            )
            planned_cost, planned_turnover = estimate_rebalance_cost(current_weights, weights, value, config)
            pending_targets[dates[idx + 1]] = (weights, planned_cost, planned_turnover)

    strategy = pd.DataFrame(nav)
    benchmark_slice = benchmark[benchmark["date"].isin(dates)].sort_values("date")
    merged = strategy.merge(benchmark_slice[["date", "close"]], how="inner", on="date")
    if merged.empty:
        return zero_result()
    merged["strategy_nav"] = merged["strategy_nav"] / merged["strategy_nav"].iloc[0]
    merged["gross_strategy_nav"] = merged["gross_strategy_nav"] / merged["gross_strategy_nav"].iloc[0]
    merged["benchmark_nav"] = merged["close"] / merged["close"].iloc[0]
    drawdown = merged["strategy_nav"] / merged["strategy_nav"].cummax() - 1
    strategy_return = float(merged["strategy_nav"].iloc[-1] - 1)
    gross_return = float(merged["gross_strategy_nav"].iloc[-1] - 1)
    benchmark_return = float(merged["benchmark_nav"].iloc[-1] - 1)
    return {
        "strategy_return": strategy_return,
        "gross_strategy_return": gross_return,
        "benchmark_return": benchmark_return,
        "excess_return": strategy_return - benchmark_return,
        "max_drawdown": float(drawdown.min()),
        "transaction_cost_drag": gross_return - strategy_return,
        "estimated_trading_cost": cumulative_cost,
        "annualized_turnover": total_turnover * 252.0 / max(len(dates) - 1, 1),
        "rebalance_count": float(rebalance_count),
    }


def session_return_matrices(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if prices.empty:
        return pd.DataFrame(), pd.DataFrame()
    open_wide = prices.pivot(index="date", columns="symbol", values="open").sort_index()
    close_wide = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    symbols = open_wide.columns.union(close_wide.columns)
    dates = open_wide.index.union(close_wide.index)
    open_wide = open_wide.reindex(index=dates, columns=symbols)
    close_wide = close_wide.reindex(index=dates, columns=symbols)
    previous_close = close_wide.shift(1)
    overnight = open_wide.divide(previous_close).subtract(1.0)
    intraday = close_wide.divide(open_wide).subtract(1.0)
    overnight = overnight.replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    intraday = intraday.replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    return overnight.astype(float), intraday.astype(float)


def apply_portfolio_returns(
    weights: pd.Series,
    asset_returns: pd.Series,
) -> tuple[float, pd.Series]:
    if weights.empty:
        return 0.0, weights.copy()
    aligned_returns = pd.to_numeric(asset_returns.reindex(weights.index), errors="coerce").fillna(0.0)
    period_return = float((weights * aligned_returns).sum())
    denominator = 1.0 + period_return
    if denominator <= 0:
        return -1.0, pd.Series(0.0, index=weights.index)
    drifted = weights * (1.0 + aligned_returns) / denominator
    return period_return, drifted.fillna(0.0).clip(lower=0.0)


def estimate_rebalance_cost(
    current_weights: pd.Series,
    target_weights: pd.Series,
    portfolio_nav: float,
    config: dict[str, Any],
) -> tuple[float, float]:
    symbols = current_weights.index.union(target_weights.index)
    current = current_weights.reindex(symbols).fillna(0.0)
    target = target_weights.reindex(symbols).fillna(0.0)
    deltas = target - current
    turnover = float(deltas.abs().sum())
    initial_cash = float(config.get("backtest", {}).get("initial_cash", 1_000_000.0))
    equity = max(initial_cash * portfolio_nav, 0.0)
    if equity <= 0 or turnover <= 0:
        return 0.0, turnover

    trading = config.get("trading", {})
    slippage_rate = max(float(trading.get("slippage_bps", 0.0)), 0.0) / 10000.0
    total_cost = 0.0
    for delta in deltas:
        if abs(float(delta)) <= 1e-12:
            continue
        amount = abs(float(delta)) * equity
        side = "buy" if delta > 0 else "sell"
        total_cost += calc_fee(side, amount, trading)
        total_cost += amount * slippage_rate
    return min(total_cost / equity, 0.99), turnover


def quantize_rebalance_target(
    current_weights: pd.Series,
    target_weights: pd.Series,
    portfolio_nav: float,
    signal_prices: pd.Series,
    config: dict[str, Any],
) -> pd.Series:
    """Approximate the lot-aware orders the formal simulator creates at signal close."""
    symbols = current_weights.index.union(target_weights.index)
    current = pd.to_numeric(current_weights.reindex(symbols), errors="coerce").fillna(0.0)
    target = pd.to_numeric(target_weights.reindex(symbols), errors="coerce").fillna(0.0)
    prices = pd.to_numeric(signal_prices.reindex(symbols), errors="coerce")
    initial_cash = float(config.get("backtest", {}).get("initial_cash", 1_000_000.0))
    equity = max(initial_cash * float(portfolio_nav), 0.0)
    if equity <= 0:
        return current

    lot_size = max(int(config.get("trading", {}).get("lot_size", 100)), 1)
    adjusted = current.copy()
    for symbol in symbols:
        price = prices.get(symbol)
        if pd.isna(price) or float(price) <= 0:
            continue
        price = float(price)
        delta_value = float(target.get(symbol, 0.0) - current.get(symbol, 0.0)) * equity
        shares = buy_quantity_for_value(delta_value, price, lot_size)
        if shares > 0:
            adjusted.at[symbol] = float(current.get(symbol, 0.0)) + shares * price / equity
        elif delta_value < -price:
            shares = int(abs(delta_value) / price)
            current_value = max(float(current.get(symbol, 0.0)) * equity, 0.0)
            max_shares = int(current_value / price)
            shares = min(shares, max_shares)
            if shares > 0:
                adjusted.at[symbol] = max(
                    0.0,
                    float(current.get(symbol, 0.0)) - shares * price / equity,
                )
    return adjusted[adjusted > 1e-12]

def zero_result() -> dict[str, float]:
    return {
        "strategy_return": 0.0,
        "gross_strategy_return": 0.0,
        "benchmark_return": 0.0,
        "excess_return": 0.0,
        "max_drawdown": 0.0,
        "transaction_cost_drag": 0.0,
        "estimated_trading_cost": 0.0,
        "annualized_turnover": 0.0,
        "rebalance_count": 0.0,
    }


def overlap_ratio(test_days: int, step_days: int) -> float:
    if test_days <= 0:
        return 0.0
    return max(0.0, min(1.0, (test_days - step_days) / test_days))


def empty_folds() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "train_start",
            "train_end",
            "test_start",
            "test_end",
            "selected_candidate",
            "train_excess_return",
            "test_strategy_return",
            "test_benchmark_return",
            "test_excess_return",
            "test_max_drawdown",
        ]
    )


def empty_summary() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "folds",
            "train_days",
            "test_days",
            "step_days",
            "selection_mode",
            "test_overlap_ratio",
            "positive_test_ratio",
            "avg_test_excess_return",
            "worst_test_excess_return",
            "avg_test_max_drawdown",
        ]
    )
