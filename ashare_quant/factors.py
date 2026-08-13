from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
from typing import Any

import numpy as np
import pandas as pd

from .fundamental_policy import assert_fundamental_factor_policy


def winsorize(series: pd.Series, lower: float = 0.05, upper: float = 0.95) -> pd.Series:
    if series.empty:
        return series
    lo = series.quantile(lower)
    hi = series.quantile(upper)
    return series.clip(lo, hi)


def zscore(series: pd.Series) -> pd.Series:
    series = winsorize(pd.to_numeric(series, errors="coerce"))
    mean = series.mean()
    std = series.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return ((series - mean) / std).fillna(0.0)


def latest_fundamentals(fundamentals: pd.DataFrame, asof: date) -> pd.DataFrame:
    if fundamentals.empty:
        return pd.DataFrame(columns=["symbol", "pe", "pb", "roe"])
    usable = fundamentals[fundamentals["date"] <= asof].copy()
    if usable.empty:
        return pd.DataFrame(columns=["symbol", "pe", "pb", "roe"])
    usable = usable.sort_values(["symbol", "date"])
    return usable.groupby("symbol", as_index=False).tail(1)[["symbol", "pe", "pb", "roe"]]


def compute_scores(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    asof: date,
    config: dict[str, Any],
    benchmark: pd.DataFrame | None = None,
) -> pd.DataFrame:
    lookbacks = config["strategy"]["lookbacks"]
    weights = effective_factor_weights(config, benchmark, asof)
    assert_fundamental_factor_policy(config, weights, data_available=not fundamentals.empty)
    factors = compute_factor_values(prices, fundamentals, asof, lookbacks, benchmark)
    if factors.empty:
        return pd.DataFrame(columns=["symbol", "score"])
    return score_factor_values(factors, stocks, config, weights)


def score_factor_values(
    factors: pd.DataFrame,
    stocks: pd.DataFrame,
    config: dict[str, Any],
    weights: dict[str, float],
) -> pd.DataFrame:
    if factors.empty:
        return pd.DataFrame(columns=["symbol", "score"])
    result = factors.copy()
    metadata_columns = [column for column in ("symbol", "industry", "board") if column in stocks.columns]
    if "symbol" in metadata_columns:
        metadata = stocks[metadata_columns].copy()
        metadata["symbol"] = metadata["symbol"].astype(str)
        result = result[result["symbol"].isin(metadata["symbol"])].copy()
        metadata = metadata.drop_duplicates("symbol", keep="last")
        result["symbol"] = result["symbol"].astype(str)
        result = result.drop(columns=[column for column in ("industry", "board") if column in result.columns])
        result = result.merge(metadata, how="left", on="symbol")
    for column in ("industry", "board"):
        if column not in result:
            result[column] = ""
    neutralization = config.get("strategy", {}).get("industry_neutralization", {})
    required_factors = config.get("strategy", {}).get("required_factors", [])
    if isinstance(required_factors, list):
        for name in (str(value) for value in required_factors):
            availability = f"{name}_available"
            if availability not in result.columns:
                return pd.DataFrame(columns=["symbol", "score"])
            result = result[result[availability].fillna(False).astype(bool)].copy()

    if isinstance(neutralization, dict) and bool(neutralization.get("enabled", False)):
        result = neutralize_factors_by_industry(
            result,
            list(weights),
            min_group_size=int(neutralization.get("min_group_size", 5)),
        )
    exposure_neutralization = config.get("strategy", {}).get("cross_sectional_neutralization", {})
    if isinstance(exposure_neutralization, dict) and bool(exposure_neutralization.get("enabled", False)):
        exposures = [str(name) for name in exposure_neutralization.get("exposures", ["liquidity"])]
        configured_targets = exposure_neutralization.get("targets")
        targets = (
            [str(name) for name in configured_targets]
            if isinstance(configured_targets, list)
            else [name for name in weights if name not in exposures]
        )
        result = neutralize_factors_by_exposures(
            result,
            targets,
            exposures,
            min_observations=int(exposure_neutralization.get("min_observations", 50)),
        )
    result["score"] = 0.0
    for name, weight in weights.items():
        result["score"] += float(weight) * result.get(name, 0.0)
    return result.sort_values("score", ascending=False).reset_index(drop=True)


def neutralize_factors_by_industry(
    factors: pd.DataFrame,
    factor_names: list[str],
    *,
    min_group_size: int = 5,
) -> pd.DataFrame:
    if factors.empty or "industry" not in factors.columns:
        return factors.copy()
    result = factors.copy()
    industry = result["industry"].fillna("").astype(str).str.strip()
    known = industry.ne("") & industry.str.lower().ne("unknown")
    counts = industry.where(known).groupby(industry).transform("size")
    eligible = known & counts.ge(max(int(min_group_size), 2))
    for name in factor_names:
        if name not in result.columns:
            continue
        values = pd.to_numeric(result[name], errors="coerce").fillna(0.0)
        means = values.where(eligible).groupby(industry).transform("mean")
        adjusted = values.copy()
        adjusted.loc[eligible] = values.loc[eligible] - means.loc[eligible]
        std = float(adjusted.std(ddof=0))
        result[name] = adjusted / std if std > 0 else 0.0
    return result



def neutralize_factors_by_exposures(
    factors: pd.DataFrame,
    factor_names: list[str],
    exposure_names: list[str],
    *,
    min_observations: int = 50,
) -> pd.DataFrame:
    result = factors.copy()
    exposures = [name for name in exposure_names if name in result.columns]
    if result.empty or not exposures:
        return result
    exposure_frame = result[exposures].apply(pd.to_numeric, errors="coerce")
    usable_exposures = [name for name in exposures if float(exposure_frame[name].std(ddof=0)) > 1e-12]
    if not usable_exposures:
        return result
    exposure_frame = exposure_frame[usable_exposures]
    for name in factor_names:
        if name not in result.columns or name in usable_exposures:
            continue
        values = pd.to_numeric(result[name], errors="coerce")
        valid = values.notna() & exposure_frame.notna().all(axis=1)
        if int(valid.sum()) < max(int(min_observations), len(usable_exposures) + 2):
            continue
        matrix = exposure_frame.loc[valid].to_numpy(dtype=float)
        matrix = np.column_stack([np.ones(len(matrix)), matrix])
        target = values.loc[valid].to_numpy(dtype=float)
        coefficients, _, _, _ = np.linalg.lstsq(matrix, target, rcond=None)
        adjusted = values.copy()
        adjusted.loc[valid] = target - matrix @ coefficients
        result[name] = zscore(adjusted)
    return result


def effective_factor_weights(
    config: dict[str, Any],
    benchmark: pd.DataFrame | None = None,
    asof: date | None = None,
) -> dict[str, float]:
    strategy = config.get("strategy", {})
    base = {str(name): float(weight) for name, weight in strategy.get("weights", {}).items()}
    adaptive = strategy.get("adaptive_weights", {})
    if not adaptive or not bool(adaptive.get("enabled", False)) or benchmark is None or asof is None:
        return base
    if benchmark.empty or "date" not in benchmark or "close" not in benchmark:
        return base

    state = benchmark_regime_flags(benchmark, asof, config)
    if state["risk_on"]:
        target = {str(name): float(weight) for name, weight in adaptive.get("risk_on_weights", {}).items()}
        if target:
            return blend_factor_weights(base, target, float(adaptive.get("risk_on_blend", 1.0)))
    if state["risk_off"]:
        target = {str(name): float(weight) for name, weight in adaptive.get("risk_off_weights", {}).items()}
        if target:
            return blend_factor_weights(base, target, float(adaptive.get("risk_off_blend", 1.0)))
    return base


def benchmark_regime_flags(benchmark: pd.DataFrame, asof: date, config: dict[str, Any]) -> dict[str, bool]:
    adaptive = config.get("strategy", {}).get("adaptive_weights", {})
    risk_cfg = config.get("risk_overlay", {})
    ma_days = int(adaptive.get("benchmark_ma_days", risk_cfg.get("benchmark_ma_days", 200)))
    vol_days = int(adaptive.get("benchmark_vol_days", risk_cfg.get("benchmark_vol_days", 60)))
    high_volatility_threshold = float(
        adaptive.get("high_volatility_threshold", risk_cfg.get("high_volatility_threshold", 0.30))
    )
    history = benchmark[pd.to_datetime(benchmark["date"], errors="coerce").dt.date <= asof].sort_values("date").copy()
    if len(history) < max(ma_days, vol_days) + 1:
        return {"bull_trend": False, "bear_trend": False, "high_volatility": False, "risk_on": False, "risk_off": False}
    close = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(close) < max(ma_days, vol_days) + 1:
        return {"bull_trend": False, "bear_trend": False, "high_volatility": False, "risk_on": False, "risk_off": False}
    latest = float(close.iloc[-1])
    moving_average = float(close.rolling(ma_days).mean().iloc[-1])
    annual_volatility = float(close.pct_change().rolling(vol_days).std().iloc[-1] * (252**0.5))
    bull = latest >= moving_average
    high_vol = annual_volatility > high_volatility_threshold
    condition = str(adaptive.get("risk_on_condition", "bull_not_high_volatility")).lower()
    if condition == "bull":
        risk_on = bull
    elif condition == "not_bear":
        risk_on = not bull is False
    else:
        risk_on = bull and not high_vol
    risk_off = (not bull) or high_vol
    return {
        "bull_trend": bool(bull),
        "bear_trend": not bool(bull),
        "high_volatility": bool(high_vol),
        "risk_on": bool(risk_on),
        "risk_off": bool(risk_off),
    }


def blend_factor_weights(base: dict[str, float], target: dict[str, float], blend: float) -> dict[str, float]:
    blend = max(0.0, min(1.0, float(blend)))
    keys = sorted(set(base) | set(target))
    return {key: (1.0 - blend) * float(base.get(key, 0.0)) + blend * float(target.get(key, 0.0)) for key in keys}


def compute_factor_values(
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    asof: date,
    lookbacks: dict[str, Any],
    benchmark: pd.DataFrame | None = None,
) -> pd.DataFrame:
    history = prices[prices["date"] <= asof].copy()
    if history.empty:
        return pd.DataFrame(columns=["symbol"])

    close = history.pivot(index="date", columns="symbol", values="close").sort_index()
    amount = history.pivot(index="date", columns="symbol", values="amount").sort_index()
    volume = (
        history.pivot(index="date", columns="symbol", values="volume").sort_index()
        if "volume" in history.columns
        else pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    )
    returns = close.pct_change()

    momentum_lookback = int(lookbacks.get("momentum", 60))
    long_momentum_lookback = int(lookbacks.get("long_momentum", max(momentum_lookback * 2, 120)))
    volatility_lookback = int(lookbacks.get("volatility", 60))
    drawdown_lookback = int(lookbacks.get("drawdown", volatility_lookback))
    reversal_lookback = int(lookbacks.get("short_reversal", 20))
    medium_reversal_lookback = int(lookbacks.get("medium_reversal", 60))
    medium_reversal_skip = int(lookbacks.get("medium_reversal_skip", 5))
    residual_reversal_lookback = int(lookbacks.get("residual_reversal", reversal_lookback))
    residual_beta_lookback = int(lookbacks.get("residual_beta", max(residual_reversal_lookback * 6, 120)))
    residual_momentum_lookback = int(lookbacks.get("residual_momentum", max(momentum_lookback * 3, 180)))
    residual_momentum_skip = int(lookbacks.get("residual_momentum_skip", 20))
    idiosyncratic_volatility_lookback = int(lookbacks.get("idiosyncratic_volatility", volatility_lookback))
    liquidity_lookback = int(lookbacks.get("liquidity", 20))
    volume_ma_short = int(lookbacks.get("volume_ma_short", 21))
    volume_ma_long = int(lookbacks.get("volume_ma_long", 200))
    high_52_week_lookback = int(lookbacks.get("high_52_week", 252))

    momentum = close.pct_change(momentum_lookback).iloc[-1]
    long_momentum = close.pct_change(long_momentum_lookback).iloc[-1]
    volatility = returns.rolling(volatility_lookback).std().iloc[-1]
    downside_volatility = returns.clip(upper=0).rolling(volatility_lookback).std().iloc[-1]
    liquidity = amount.rolling(liquidity_lookback).mean().iloc[-1]
    short_reversal = -close.pct_change(reversal_lookback).iloc[-1]
    medium_reversal_requested = (
        "medium_reversal" in lookbacks or "medium_reversal_skip" in lookbacks
    )
    medium_reversal = (
        -(close.shift(medium_reversal_skip).iloc[-1]
          / close.shift(medium_reversal_lookback).iloc[-1] - 1.0)
        if medium_reversal_requested
        else pd.Series(index=close.columns, dtype=float)
    )
    residual_requested = "residual_reversal" in lookbacks
    volume_ma_requested = "volume_ma_short" in lookbacks or "volume_ma_long" in lookbacks
    if volume_ma_requested:
        short_min_periods = max(2, int(volume_ma_short * 0.75))
        long_min_periods = max(2, int(volume_ma_long * 0.75))
        short_volume = volume.rolling(
            volume_ma_short, min_periods=short_min_periods
        ).mean().iloc[-1]
        long_volume = volume.rolling(
            volume_ma_long, min_periods=long_min_periods
        ).mean().iloc[-1]
        volume_ma_distance = short_volume.divide(long_volume.replace(0, pd.NA)) - 1.0
        low_volume_ma_distance = -volume_ma_distance.where(
            short_volume.ge(0) & long_volume.gt(0)
        )
    else:
        low_volume_ma_distance = pd.Series(index=close.columns, dtype=float)
    residual_reversal = (
        residual_reversal_values(
            returns,
            benchmark,
            asof,
            beta_lookback=residual_beta_lookback,
            reversal_lookback=residual_reversal_lookback,
        )
        if residual_requested
        else pd.Series(index=close.columns, dtype=float)
    )
    rolling_high = close.rolling(drawdown_lookback).max()
    residual_cross_section_requested = any(
        name in lookbacks
        for name in ("residual_momentum", "residual_momentum_skip", "idiosyncratic_volatility")
    )
    residual_cross_section = (
        market_residual_factor_values(
            returns,
            benchmark,
            asof,
            beta_lookback=residual_beta_lookback,
            momentum_lookback=residual_momentum_lookback,
            momentum_skip=residual_momentum_skip,
            idiosyncratic_volatility_lookback=idiosyncratic_volatility_lookback,
        )
        if residual_cross_section_requested
        else pd.DataFrame(index=close.columns, columns=["residual_momentum", "low_idiosyncratic_volatility"], dtype=float)
    )
    drawdown = (close / rolling_high - 1).iloc[-1]
    trend_quality = momentum / volatility.replace(0, pd.NA)
    long_trend_quality = long_momentum / volatility.replace(0, pd.NA)
    high_52_week_requested = "high_52_week" in lookbacks
    near_52_week_high = (
        close.iloc[-1].divide(
            close.rolling(high_52_week_lookback, min_periods=high_52_week_lookback)
            .max()
            .iloc[-1]
            .replace(0, pd.NA)
        )
        if high_52_week_requested
        else pd.Series(index=close.columns, dtype=float)
    )

    factors = pd.DataFrame(
        {
            "symbol": close.columns.astype(str),
            "momentum": momentum.reindex(close.columns).values,
            "long_momentum": long_momentum.reindex(close.columns).values,
            "low_volatility": -volatility.reindex(close.columns).values,
            "low_downside_volatility": -downside_volatility.reindex(close.columns).values,
            "liquidity": liquidity.reindex(close.columns).values,
            "avg_amount": liquidity.reindex(close.columns).values,
            "short_reversal": short_reversal.reindex(close.columns).values,
            "medium_reversal": medium_reversal.reindex(close.columns).values,
            "medium_reversal_available": medium_reversal.reindex(close.columns).notna().values,
            "residual_reversal": residual_reversal.reindex(close.columns).values,
            "low_drawdown": drawdown.reindex(close.columns).values,
            "trend_quality": trend_quality.reindex(close.columns).values,
            "long_trend_quality": long_trend_quality.reindex(close.columns).values,
            "near_52_week_high": near_52_week_high.reindex(close.columns).values,
            "near_52_week_high_available": near_52_week_high.reindex(close.columns).notna().values,
            "residual_momentum": residual_cross_section["residual_momentum"].reindex(close.columns).values,
            "low_idiosyncratic_volatility": residual_cross_section["low_idiosyncratic_volatility"].reindex(close.columns).values,
            "residual_momentum_available": residual_cross_section["residual_momentum"].reindex(close.columns).notna().values,
            "low_idiosyncratic_volatility_available": residual_cross_section["low_idiosyncratic_volatility"].reindex(close.columns).notna().values,
            "low_volume_ma_distance": low_volume_ma_distance.reindex(close.columns).values,
            "low_volume_ma_distance_available": low_volume_ma_distance.reindex(close.columns).notna().values,
        }
    )
    latest = latest_fundamentals(fundamentals, asof)
    factors = factors.merge(latest, how="left", on="symbol")
    factors["valuation"] = -zscore(factors["pe"]) * 0.5 + -zscore(factors["pb"]) * 0.5
    factors["quality"] = zscore(factors["roe"])
    factors["momentum"] = zscore(factors["momentum"])
    factors["long_momentum"] = zscore(factors["long_momentum"])
    factors["low_volatility"] = zscore(factors["low_volatility"])
    factors["low_downside_volatility"] = zscore(factors["low_downside_volatility"])
    factors["liquidity"] = zscore(factors["liquidity"])
    factors["short_reversal"] = zscore(factors["short_reversal"])
    factors["medium_reversal"] = zscore(factors["medium_reversal"])
    factors["residual_reversal"] = zscore(factors["residual_reversal"])
    factors["low_drawdown"] = zscore(factors["low_drawdown"])
    factors["trend_quality"] = zscore(factors["trend_quality"])
    factors["long_trend_quality"] = zscore(factors["long_trend_quality"])
    factors["near_52_week_high"] = zscore(factors["near_52_week_high"])
    factors["residual_momentum"] = zscore(factors["residual_momentum"])
    factors["low_volume_ma_distance"] = zscore(factors["low_volume_ma_distance"])
    factors["low_idiosyncratic_volatility"] = zscore(factors["low_idiosyncratic_volatility"])
    return factors.reset_index(drop=True)


def residual_reversal_values(
    returns: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    asof: date,
    *,
    beta_lookback: int,
    reversal_lookback: int,
) -> pd.Series:
    neutral = pd.Series(index=returns.columns, dtype=float)
    if benchmark is None or benchmark.empty or not {"date", "close"}.issubset(benchmark.columns):
        return neutral
    market = benchmark.copy()
    market["date"] = pd.to_datetime(market["date"], errors="coerce").dt.date
    market = market.dropna(subset=["date"]).loc[lambda frame: frame["date"] <= asof]
    market = market.drop_duplicates("date", keep="last").sort_values("date")
    market_close = pd.to_numeric(market.set_index("date")["close"], errors="coerce")
    market_returns = market_close.pct_change().reindex(returns.index)
    valid_market_dates = market_returns.dropna().index
    if len(valid_market_dates) < max(20, reversal_lookback):
        return neutral

    beta_dates = valid_market_dates[-max(int(beta_lookback), 20):]
    stock_window = returns.reindex(beta_dates)
    market_window = market_returns.reindex(beta_dates)
    market_frame = pd.DataFrame(
        {symbol: market_window for symbol in returns.columns},
        index=beta_dates,
    )
    valid = stock_window.notna() & market_frame.notna()
    stock_mean = stock_window.where(valid).mean()
    market_mean = market_frame.where(valid).mean()
    stock_centered = stock_window.sub(stock_mean, axis="columns")
    market_centered = market_frame.sub(market_mean, axis="columns")
    covariance = (stock_centered * market_centered).where(valid).sum()
    market_variance = market_centered.pow(2).where(valid).sum()
    observations = valid.sum()
    min_beta_observations = max(20, int(len(beta_dates) * 0.50))
    beta = (covariance / market_variance.replace(0, pd.NA)).where(observations >= min_beta_observations)
    alpha = stock_mean - beta * market_mean

    recent_dates = valid_market_dates[-max(int(reversal_lookback), 2):]
    recent_stock = returns.reindex(recent_dates)
    recent_market = pd.DataFrame(
        {symbol: market_returns.reindex(recent_dates) for symbol in returns.columns},
        index=recent_dates,
    )
    residual = recent_stock.sub(alpha, axis="columns") - recent_market.mul(beta, axis="columns")
    min_recent_observations = max(2, int(len(recent_dates) * 0.60))
    residual_return = (1.0 + residual.clip(lower=-0.999)).prod(min_count=min_recent_observations) - 1.0
    residual_return = residual_return.where(residual.notna().sum() >= min_recent_observations)
    return -residual_return.reindex(returns.columns)


def market_residual_factor_values(
    returns: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    asof: date,
    *,
    beta_lookback: int,
    momentum_lookback: int,
    momentum_skip: int,
    idiosyncratic_volatility_lookback: int,
) -> pd.DataFrame:
    columns = ["residual_momentum", "low_idiosyncratic_volatility"]
    neutral = pd.DataFrame(index=returns.columns, columns=columns, dtype=float)
    if benchmark is None or benchmark.empty or not {"date", "close"}.issubset(benchmark.columns):
        return neutral

    market = benchmark.copy()
    market["date"] = pd.to_datetime(market["date"], errors="coerce").dt.date
    market = market.dropna(subset=["date"]).loc[lambda frame: frame["date"] <= asof]
    market = market.drop_duplicates("date", keep="last").sort_values("date")
    market_close = pd.to_numeric(market.set_index("date")["close"], errors="coerce")
    market_returns = market_close.pct_change().reindex(returns.index)
    valid_market_dates = market_returns.dropna().index
    if len(valid_market_dates) < 20:
        return neutral

    beta_dates = valid_market_dates[-max(int(beta_lookback), 20):]
    stock_window = returns.reindex(beta_dates)
    market_window = market_returns.reindex(beta_dates)
    market_frame = pd.DataFrame(
        {symbol: market_window for symbol in returns.columns},
        index=beta_dates,
    )
    valid = stock_window.notna() & market_frame.notna()
    observations = valid.sum()
    min_beta_observations = max(20, int(len(beta_dates) * 0.75))
    stock_mean = stock_window.where(valid).mean()
    market_mean = market_frame.where(valid).mean()
    stock_centered = stock_window.sub(stock_mean, axis="columns")
    market_centered = market_frame.sub(market_mean, axis="columns")
    covariance = (stock_centered * market_centered).where(valid).sum()
    market_variance = market_centered.pow(2).where(valid).sum()
    beta = (covariance / market_variance.replace(0, pd.NA)).where(observations >= min_beta_observations)
    alpha = stock_mean - beta * market_mean
    residual = stock_window.sub(alpha, axis="columns") - market_frame.mul(beta, axis="columns")

    skip = max(int(momentum_skip), 0)
    eligible_momentum_dates = beta_dates[:-skip] if skip else beta_dates
    momentum_dates = eligible_momentum_dates[-max(int(momentum_lookback), 20):]
    if len(momentum_dates) >= 20:
        momentum_residual = residual.reindex(momentum_dates)
        min_momentum_observations = max(20, int(len(momentum_dates) * 0.75))
        momentum_count = momentum_residual.notna().sum()
        compounded = (1.0 + momentum_residual.clip(lower=-0.999)).prod(
            min_count=min_momentum_observations
        ) - 1.0
        residual_scale = momentum_residual.std(ddof=0).replace(0, pd.NA)
        residual_momentum = (compounded / residual_scale).where(
            momentum_count >= min_momentum_observations
        )
        neutral["residual_momentum"] = residual_momentum.reindex(returns.columns)

    volatility_dates = beta_dates[-max(int(idiosyncratic_volatility_lookback), 20):]
    volatility_residual = residual.reindex(volatility_dates)
    min_volatility_observations = max(20, int(len(volatility_dates) * 0.75))
    volatility_count = volatility_residual.notna().sum()
    idiosyncratic_volatility = volatility_residual.std(ddof=0).where(
        volatility_count >= min_volatility_observations
    )
    neutral["low_idiosyncratic_volatility"] = -idiosyncratic_volatility.reindex(returns.columns)
    return neutral


def strategy_member_configs(config: dict[str, Any]) -> list[tuple[float, dict[str, Any]]]:
    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    raw_members = strategy.get("ensemble_members", [])
    if not isinstance(raw_members, list) or not raw_members:
        return [(1.0, config)]
    prepared: list[tuple[float, dict[str, Any]]] = []
    for raw in raw_members:
        if not isinstance(raw, dict):
            continue
        blend_weight = max(float(raw.get("blend_weight", 0.0)), 0.0)
        if blend_weight <= 0:
            continue
        member_config = deepcopy(config)
        member_strategy = deepcopy(strategy)
        member_strategy.pop("ensemble_members", None)
        for key in (
            "name",
            "top_n",
            "max_stock_weight",
            "max_industry_weight",
            "weighting",
            "lookbacks",
            "weights",
            "adaptive_weights",
            "industry_neutralization",
            "cross_sectional_neutralization",
            "industry_relative_cap",
        ):
            if key in raw:
                member_strategy[key] = deepcopy(raw[key])
        if "adaptive_weights" not in raw:
            member_strategy.pop("adaptive_weights", None)
        member_config["strategy"] = member_strategy
        prepared.append((blend_weight, member_config))
    total = sum(weight for weight, _ in prepared)
    if total <= 0:
        return [(1.0, config)]
    return [(weight / total, member) for weight, member in prepared]


def reference_anchor_weights(
    scores: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[float, pd.Series]:
    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    policy = strategy.get("reference_anchor", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)) or scores.empty:
        return 0.0, pd.Series(dtype=float)

    anchor_weight = min(max(float(policy.get("weight", 0.0)), 0.0), 1.0)
    if anchor_weight <= 0:
        return 0.0, pd.Series(dtype=float)

    anchor_config = deepcopy(config)
    anchor_strategy = deepcopy(strategy)
    anchor_strategy.pop("ensemble_members", None)
    anchor_strategy.pop("reference_anchor", None)
    anchor_strategy["top_n"] = max(int(policy.get("top_n", strategy.get("top_n", 1))), 1)
    anchor_strategy["max_stock_weight"] = float(
        policy.get("max_stock_weight", strategy.get("max_stock_weight", 1.0))
    )
    anchor_strategy["weighting"] = str(policy.get("weighting", "liquidity_sqrt"))
    anchor_config["strategy"] = anchor_strategy

    anchor_scores = scores.copy()
    if "avg_amount" in anchor_scores.columns:
        anchor_scores["_anchor_rank"] = pd.to_numeric(
            anchor_scores["avg_amount"], errors="coerce"
        ).fillna(0.0)
        anchor_scores = anchor_scores.sort_values(
            ["_anchor_rank", "symbol"], ascending=[False, True]
        ).drop(columns="_anchor_rank")
    else:
        anchor_scores = anchor_scores.sort_values("symbol")
    return anchor_weight, build_target_weights(anchor_scores, anchor_config)

def decompose_reference_anchor_target(
    scores: pd.DataFrame,
    target_weights: pd.Series,
    config: dict[str, Any],
) -> tuple[pd.Series, pd.Series]:
    """Split final target weights into anchor and satellite contributions."""
    target = pd.to_numeric(target_weights, errors="coerce").fillna(0.0)
    target.index = target.index.astype(str)
    target = target[target > 0]
    if target.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    if len(strategy_member_configs(config)) != 1:
        return pd.Series(0.0, index=target.index), target.copy()

    anchor_share, anchor_raw = reference_anchor_weights(scores, config)
    if anchor_share <= 0 or anchor_raw.empty:
        return pd.Series(0.0, index=target.index), target.copy()

    satellite_raw = build_target_weights(scores, config)
    anchor_raw.index = anchor_raw.index.astype(str)
    satellite_raw.index = satellite_raw.index.astype(str)
    raw = satellite_raw.mul(1.0 - anchor_share).add(
        anchor_raw.mul(anchor_share), fill_value=0.0
    )
    raw = raw.reindex(target.index).fillna(0.0)
    scale = target.div(raw.where(raw > 0)).replace([float("inf"), -float("inf")], 0.0).fillna(0.0)
    anchor = anchor_raw.reindex(target.index).fillna(0.0).mul(anchor_share).mul(scale)
    anchor = anchor.clip(lower=0.0).where(anchor <= target, target)
    satellite = (target - anchor).clip(lower=0.0)
    return anchor.astype(float), satellite.astype(float)


def build_configured_target_portfolio(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    asof: date,
    config: dict[str, Any],
    benchmark: pd.DataFrame | None = None,
    current_symbols: set[str] | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    members = strategy_member_configs(config)
    combined_weights = pd.Series(dtype=float)
    combined_scores = pd.Series(dtype=float)
    single_scores = pd.DataFrame()
    stock_symbols = stocks["symbol"].astype(str) if "symbol" in stocks else pd.Series(dtype=str)
    reference_scores = pd.DataFrame()
    for blend_weight, member_config in members:
        scores = compute_scores(prices, stocks, fundamentals, asof, member_config, benchmark)
        if len(members) == 1:
            single_scores = scores.copy()
        eligible_scores = scores[scores["symbol"].astype(str).isin(stock_symbols)].copy()
        if reference_scores.empty:
            reference_scores = eligible_scores.copy()
        selection_scores = apply_holding_rank_buffer(
            eligible_scores, current_symbols, member_config
        )
        weights = build_target_weights(selection_scores, member_config)
        combined_weights = combined_weights.add(weights * blend_weight, fill_value=0.0)
        if not eligible_scores.empty:
            member_rank = pd.to_numeric(eligible_scores["score"], errors="coerce").rank(
                method="average", pct=True
            )
            ranked = pd.Series(member_rank.values, index=eligible_scores["symbol"].astype(str), dtype=float)
            combined_scores = combined_scores.add(ranked * blend_weight, fill_value=0.0)
    max_weight = float(config.get("strategy", {}).get("max_stock_weight", 1.0))
    anchor_weight, anchor = reference_anchor_weights(reference_scores, config)
    if anchor_weight > 0 and not anchor.empty:
        combined_weights = combined_weights.mul(1.0 - anchor_weight).add(
            anchor.mul(anchor_weight), fill_value=0.0
        )
    target_weights = cap_and_normalize(combined_weights, max_weight)
    if len(members) == 1:
        return target_weights, single_scores
    metadata_columns = [column for column in ("symbol", "industry", "board") if column in stocks]
    signal_scores = pd.DataFrame(
        {"symbol": combined_scores.index.astype(str), "score": combined_scores.values}
    )
    if metadata_columns:
        metadata = stocks[metadata_columns].copy()
        metadata["symbol"] = metadata["symbol"].astype(str)
        metadata = metadata.drop_duplicates("symbol", keep="last")
        signal_scores = signal_scores.merge(metadata, how="left", on="symbol")
    for column in ("industry", "board"):
        if column not in signal_scores:
            signal_scores[column] = ""
    signal_scores = signal_scores.sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)
    return target_weights, signal_scores


def build_configured_target_weights(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    asof: date,
    config: dict[str, Any],
    benchmark: pd.DataFrame | None = None,
    current_symbols: set[str] | None = None,
) -> pd.Series:
    weights, _ = build_configured_target_portfolio(
        prices, stocks, fundamentals, asof, config, benchmark, current_symbols
    )
    return weights


def apply_holding_rank_buffer(
    scores: pd.DataFrame,
    current_symbols: set[str] | None,
    config: dict[str, Any],
) -> pd.DataFrame:
    """Prioritize eligible holdings until they fall through a wider exit rank."""
    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    policy = strategy.get("turnover_control", {})
    if (
        scores.empty
        or not current_symbols
        or not isinstance(policy, dict)
        or not bool(policy.get("enabled", False))
    ):
        return scores

    top_n = max(int(strategy.get("top_n", 1)), 1)
    exit_multiplier = max(float(policy.get("exit_rank_multiplier", 1.25)), 1.0)
    exit_rank = max(top_n, int(math.ceil(top_n * exit_multiplier)))
    ranked = scores.drop_duplicates("symbol", keep="first").copy()
    ranked["symbol"] = ranked["symbol"].astype(str)
    retained = set(ranked.head(exit_rank)["symbol"]) & {str(value) for value in current_symbols}
    if not retained:
        return scores

    retained_rows = ranked[ranked["symbol"].isin(retained)]
    remaining_rows = ranked[~ranked["symbol"].isin(retained)]
    return pd.concat([retained_rows, remaining_rows], ignore_index=True)

def industry_bucket(
    symbol: object,
    industry: object,
    *,
    aggregate_unknown: bool = False,
) -> str:
    value = str(industry or "").strip()
    if value and value.casefold() != "unknown":
        return value
    return "unknown" if aggregate_unknown else f"unknown:{symbol}"


def industry_relative_group_caps(
    scores: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, float]:
    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    policy = strategy.get("industry_relative_cap", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)) or scores.empty:
        return {}

    aggregate_unknown = bool(policy.get("aggregate_unknown", True))
    frame = scores.drop_duplicates("symbol", keep="first").copy()
    frame["symbol"] = frame["symbol"].astype(str)
    industries = frame["industry"] if "industry" in frame else pd.Series("unknown", index=frame.index)
    frame["_industry"] = [
        industry_bucket(symbol, industry, aggregate_unknown=aggregate_unknown)
        for symbol, industry in zip(frame["symbol"], industries)
    ]
    reference_weighting = str(policy.get("reference_weighting", "liquidity_sqrt")).lower()
    if reference_weighting == "liquidity_sqrt" and "avg_amount" in frame:
        raw = pd.to_numeric(frame["avg_amount"], errors="coerce").fillna(0.0).clip(lower=0.0) ** 0.5
    else:
        raw = pd.Series(1.0, index=frame.index, dtype=float)
    if float(raw.sum()) <= 0:
        raw = pd.Series(1.0, index=frame.index, dtype=float)
    raw.index = frame["symbol"]
    reference_stock_cap = float(policy.get("reference_stock_cap", strategy.get("max_stock_weight", 1.0)))
    reference = cap_and_normalize(raw, reference_stock_cap)
    groups = frame.set_index("symbol")["_industry"].reindex(reference.index)
    reference_groups = reference.groupby(groups).sum()
    max_industry = float(strategy.get("max_industry_weight", 1.0))
    max_active = max(float(policy.get("max_active_weight", 0.10)), 0.0)
    caps = {
        str(group): min(max_industry, float(weight) + max_active)
        for group, weight in reference_groups.items()
    }
    if aggregate_unknown and "unknown" in caps and "max_unknown_weight" in policy:
        caps["unknown"] = min(caps["unknown"], float(policy["max_unknown_weight"]))
    return caps


def build_target_weights(
    scores: pd.DataFrame,
    config: dict[str, Any],
) -> pd.Series:
    if scores.empty:
        return pd.Series(dtype=float)
    top_n = int(config["strategy"]["top_n"])
    max_stock_weight = float(config["strategy"]["max_stock_weight"])
    max_industry_weight = float(config["strategy"]["max_industry_weight"])
    relative_caps = industry_relative_group_caps(scores, config)
    relative_policy = config["strategy"].get("industry_relative_cap", {})
    aggregate_unknown = bool(relative_caps) and bool(relative_policy.get("aggregate_unknown", True))
    selected = scores.head(max(top_n * 3, top_n)).copy()
    chosen = []
    industry_weights: dict[str, float] = {}
    base_weight = min(1.0 / max(1, top_n), max_stock_weight)
    for row in selected.itertuples(index=False):
        if len(chosen) >= top_n:
            break
        industry = industry_bucket(
            row.symbol,
            getattr(row, "industry", "unknown"),
            aggregate_unknown=aggregate_unknown,
        )
        industry_cap = relative_caps.get(industry, max_industry_weight)
        used = industry_weights.get(industry, 0.0)
        if used + base_weight > industry_cap + 1e-9:
            continue
        chosen.append(row)
        industry_weights[industry] = used + base_weight
    if not chosen:
        return pd.Series(dtype=float)
    weighting = str(config["strategy"].get("weighting", "equal")).lower()
    if weighting == "liquidity_sqrt":
        raw = pd.Series(
            {
                str(row.symbol): max(float(getattr(row, "avg_amount", 0.0) or 0.0), 0.0) ** 0.5
                for row in chosen
            },
            dtype=float,
        )
        if raw.sum() <= 0:
            raw = pd.Series({str(row.symbol): 1.0 for row in chosen}, dtype=float)
    else:
        raw = pd.Series({str(row.symbol): base_weight for row in chosen}, dtype=float)
        if not relative_caps:
            return raw
    if relative_caps:
        groups = pd.Series(
            {
                str(row.symbol): industry_bucket(
                    row.symbol,
                    getattr(row, "industry", "unknown"),
                    aggregate_unknown=aggregate_unknown,
                )
                for row in chosen
            },
            dtype=str,
        )
        return cap_and_normalize_by_group(
            raw,
            max_stock_weight=max_stock_weight,
            groups=groups,
            group_caps=relative_caps,
        )
    return cap_and_normalize(raw, max_stock_weight)


def allocate_with_caps(
    raw: pd.Series,
    caps: pd.Series,
    total: float,
) -> pd.Series:
    values = pd.to_numeric(raw, errors="coerce").fillna(0.0).clip(lower=0.0)
    limits = pd.to_numeric(caps.reindex(values.index), errors="coerce").fillna(0.0).clip(lower=0.0)
    result = pd.Series(0.0, index=values.index, dtype=float)
    remaining = list(values.index)
    remaining_total = min(max(float(total), 0.0), float(limits.sum()))
    while remaining and remaining_total > 1e-12:
        active = pd.Index(remaining)
        active_raw = values.reindex(active)
        if float(active_raw.sum()) <= 0:
            active_raw = pd.Series(1.0, index=active, dtype=float)
        proposal = active_raw / float(active_raw.sum()) * remaining_total
        active_caps = limits.reindex(active)
        saturated = proposal >= active_caps - 1e-12
        if not bool(saturated.any()):
            result.loc[active] = proposal
            break
        fixed = active[saturated]
        result.loc[fixed] = active_caps.loc[fixed]
        remaining_total -= float(active_caps.loc[fixed].sum())
        fixed_set = set(fixed)
        remaining = [item for item in remaining if item not in fixed_set]
    return result


def cap_and_normalize_by_group(
    raw: pd.Series,
    *,
    max_stock_weight: float,
    groups: pd.Series,
    group_caps: dict[str, float],
) -> pd.Series:
    values = pd.to_numeric(raw, errors="coerce").fillna(0.0).clip(lower=0.0)
    values.index = values.index.astype(str)
    if float(values.sum()) <= 0:
        return pd.Series(dtype=float)
    fallback_groups = pd.Series(
        {symbol: f"unknown:{symbol}" for symbol in values.index},
        dtype=str,
    )
    aligned_groups = groups.reindex(values.index).fillna(fallback_groups).astype(str)
    group_raw = values.groupby(aligned_groups).sum()
    group_capacity = pd.Series(
        {
            group: min(
                max(float(group_caps.get(str(group), 1.0)), 0.0),
                int((aligned_groups == group).sum()) * max_stock_weight,
            )
            for group in group_raw.index
        },
        dtype=float,
    )
    group_targets = allocate_with_caps(group_raw, group_capacity, 1.0)
    result = pd.Series(0.0, index=values.index, dtype=float)
    for group, target in group_targets.items():
        members = aligned_groups[aligned_groups == group].index
        stock_caps = pd.Series(max_stock_weight, index=members, dtype=float)
        result.loc[members] = allocate_with_caps(
            values.reindex(members),
            stock_caps,
            float(target),
        )
    return result[result > 1e-12]


def cap_and_normalize(raw: pd.Series, max_weight: float) -> pd.Series:
    raw = pd.to_numeric(raw, errors="coerce").fillna(0.0).clip(lower=0)
    if raw.sum() <= 0:
        return pd.Series(dtype=float)
    weights = raw / raw.sum()
    for _ in range(20):
        over = weights > max_weight
        if not over.any():
            break
        capped = pd.Series(max_weight, index=weights[over].index, dtype=float)
        remaining_raw = raw[~over]
        remaining_total = max(0.0, 1.0 - float(capped.sum()))
        if remaining_raw.sum() <= 0 or remaining_total <= 0:
            weights = capped
            break
        redistributed = remaining_raw / remaining_raw.sum() * remaining_total
        weights = pd.concat([capped, redistributed]).reindex(raw.index).fillna(0.0)
    total = weights.sum()
    return weights / total if total else weights
