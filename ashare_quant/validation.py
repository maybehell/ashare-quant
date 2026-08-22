from __future__ import annotations

import importlib.util
import math

from .index_symbols import canonical_index_symbol
from datetime import date, datetime, time as dt_time
from typing import Any

import pandas as pd

from .capital_budget import capital_budget_errors
from .data import normalize_symbol
from .fundamental_policy import fundamental_factor_policy


REQUIRED_CONFIG = {
    "paths": ["cache_dir", "output_dir", "state_db"],
    "data": ["max_symbols"],
    "universe": ["boards", "min_listing_days", "min_avg_amount", "liquidity_lookback"],
    "backtest": ["start", "initial_cash", "rebalance"],
    "strategy": ["top_n", "max_stock_weight", "max_industry_weight", "lookbacks", "weights"],
    "trading": ["lot_size", "slippage_bps", "commission_rate", "min_commission", "stamp_tax_rate"],
}

OPTIONAL_DEPENDENCIES = {
    "pyautogui": "RPA field-fill execution",
}


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for section, keys in REQUIRED_CONFIG.items():
        if section not in config or not isinstance(config[section], dict):
            errors.append(f"Missing config section: {section}")
            continue
        for key in keys:
            if key not in config[section]:
                errors.append(f"Missing config key: {section}.{key}")
    rebalance = str(config.get("backtest", {}).get("rebalance", "") or "").strip().lower()
    if rebalance not in {"monthly", "quarterly"}:
        errors.append("backtest.rebalance must be monthly or quarterly")

    trading = config.get("trading", {})
    if isinstance(trading, dict) and "rebalance_execution_schema_version" in trading:
        try:
            execution_schema = int(trading["rebalance_execution_schema_version"])
        except (TypeError, ValueError):
            execution_schema = 0
        if execution_schema != 2:
            errors.append("trading.rebalance_execution_schema_version must be 2")

    if "strategy" in config:
        top_n = int(config["strategy"].get("top_n", 0))
        max_stock_weight = float(config["strategy"].get("max_stock_weight", 0))
        max_industry_weight = float(config["strategy"].get("max_industry_weight", 0))
        if top_n <= 0:
            errors.append("strategy.top_n must be positive")
        if not 0 < max_stock_weight <= 1:
            errors.append("strategy.max_stock_weight must be in (0, 1]")
        if not 0 < max_industry_weight <= 1:
            errors.append("strategy.max_industry_weight must be in (0, 1]")
        capital_aware = config["strategy"].get("capital_aware", {})
        if capital_aware and not isinstance(capital_aware, dict):
            errors.append("strategy.capital_aware must be a mapping")
        elif isinstance(capital_aware, dict) and bool(capital_aware.get("enabled", False)):
            planning_keys = {
                "planning_equity_step",
                "planning_commission_rate",
                "planning_min_commission",
                "planning_transfer_fee_rate",
            }
            configured_planning = planning_keys.intersection(capital_aware)
            if configured_planning and "planning_schema_version" not in capital_aware:
                errors.append("strategy.capital_aware.planning_schema_version is required")
            schema_version = 0
            if "planning_schema_version" in capital_aware:
                try:
                    schema_version = int(capital_aware["planning_schema_version"])
                except (TypeError, ValueError):
                    schema_version = 0
                if schema_version not in {1, 2}:
                    errors.append("strategy.capital_aware.planning_schema_version must be 1 or 2")
            if bool(capital_aware.get("respect_target_weights", False)):
                if schema_version != 2:
                    errors.append(
                        "strategy.capital_aware.respect_target_weights requires planning_schema_version 2"
                    )
            for key in (
                "planning_equity_step",
                "planning_commission_rate",
                "planning_min_commission",
                "planning_transfer_fee_rate",
            ):
                if key in capital_aware:
                    try:
                        value = float(capital_aware[key])
                    except (TypeError, ValueError):
                        value = -1.0
                    if value < 0:
                        errors.append(f"strategy.capital_aware.{key} must be non-negative")
        relative_cap = config["strategy"].get("industry_relative_cap", {})
        if relative_cap and not isinstance(relative_cap, dict):
            errors.append("strategy.industry_relative_cap must be a mapping")
        elif isinstance(relative_cap, dict) and bool(relative_cap.get("enabled", False)):
            max_active = float(relative_cap.get("max_active_weight", 0.10))
            if not 0 <= max_active <= 1:
                errors.append("strategy.industry_relative_cap.max_active_weight must be in [0, 1]")
            if "max_unknown_weight" in relative_cap:
                max_unknown = float(relative_cap["max_unknown_weight"])
                if not 0 <= max_unknown <= 1:
                    errors.append("strategy.industry_relative_cap.max_unknown_weight must be in [0, 1]")
            reference_weighting = str(relative_cap.get("reference_weighting", "liquidity_sqrt")).lower()
            if reference_weighting not in {"equal", "liquidity_sqrt"}:
                errors.append("strategy.industry_relative_cap.reference_weighting must be equal or liquidity_sqrt")
            reference_stock_cap = float(relative_cap.get("reference_stock_cap", max_stock_weight))
            if not 0 < reference_stock_cap <= 1:
                errors.append("strategy.industry_relative_cap.reference_stock_cap must be in (0, 1]")
            drift_tolerance = float(relative_cap.get("drift_tolerance", 0.015))
            if not 0 <= drift_tolerance <= 0.10:
                errors.append("strategy.industry_relative_cap.drift_tolerance must be in [0, 0.10]")
        turnover_control = config["strategy"].get("turnover_control", {})
        if turnover_control and not isinstance(turnover_control, dict):
            errors.append("strategy.turnover_control must be a mapping")
        elif isinstance(turnover_control, dict) and bool(turnover_control.get("enabled", False)):
            try:
                exit_multiplier = float(turnover_control.get("exit_rank_multiplier", 1.25))
            except (TypeError, ValueError):
                exit_multiplier = 0.0
            if not 1.0 <= exit_multiplier <= 2.0:
                errors.append("strategy.turnover_control.exit_rank_multiplier must be in [1.0, 2.0]")
            capital_aware = config["strategy"].get("capital_aware", {})
            if isinstance(capital_aware, dict) and bool(capital_aware.get("enabled", False)):
                errors.append("strategy.turnover_control is incompatible with strategy.capital_aware")
            reference_anchor = config["strategy"].get("reference_anchor", {})
            if isinstance(reference_anchor, dict) and bool(reference_anchor.get("enabled", False)):
                errors.append("strategy.turnover_control is incompatible with strategy.reference_anchor")
        membership = config["strategy"].get("benchmark_membership", {})
        if membership and not isinstance(membership, dict):
            errors.append("strategy.benchmark_membership must be a mapping")
        elif isinstance(membership, dict) and bool(membership.get("enabled", False)):
            index_symbol = canonical_index_symbol(
                membership.get("index_symbol", config.get("benchmark", {}).get("symbol", ""))
            )
            if index_symbol != "000300":
                errors.append("strategy.benchmark_membership currently supports index_symbol 000300 only")
            try:
                max_staleness = int(membership.get("max_staleness_days", 120))
            except (TypeError, ValueError):
                max_staleness = 0
            if max_staleness <= 0:
                errors.append("strategy.benchmark_membership.max_staleness_days must be positive")
        lookbacks = config["strategy"].get("lookbacks", {})
        if isinstance(lookbacks, dict):
            parsed_lookbacks: dict[str, int | None] = {}
            for name, value in lookbacks.items():
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    parsed = None
                parsed_lookbacks[str(name)] = parsed
                if parsed is None or parsed <= 0:
                    errors.append(f"strategy.lookbacks.{name} must be positive")
            residual_beta = parsed_lookbacks.get("residual_beta")
            medium_reversal_window = parsed_lookbacks.get("medium_reversal")
            medium_reversal_skip = parsed_lookbacks.get("medium_reversal_skip")
            if medium_reversal_window is not None and medium_reversal_window < 20:
                errors.append("strategy.lookbacks.medium_reversal must be at least 20")
            if medium_reversal_skip is not None and medium_reversal_skip < 1:
                errors.append("strategy.lookbacks.medium_reversal_skip must be at least 1")
            if (
                medium_reversal_window is not None
                and medium_reversal_skip is not None
                and medium_reversal_skip >= medium_reversal_window
            ):
                errors.append(
                    "strategy.lookbacks.medium_reversal_skip must be less than medium_reversal"
                )
            residual_window = parsed_lookbacks.get("residual_reversal")
            if residual_beta is not None and residual_beta < 20:
                errors.append("strategy.lookbacks.residual_beta must be at least 20")
            if residual_window is not None and residual_window < 2:
                errors.append("strategy.lookbacks.residual_reversal must be at least 2")
            residual_momentum_window = parsed_lookbacks.get("residual_momentum")
            residual_momentum_skip = parsed_lookbacks.get("residual_momentum_skip")
            idiosyncratic_window = parsed_lookbacks.get("idiosyncratic_volatility")
            if residual_momentum_window is not None and residual_momentum_window < 20:
                errors.append("strategy.lookbacks.residual_momentum must be at least 20")
            if residual_momentum_skip is not None and residual_momentum_skip < 1:
                errors.append("strategy.lookbacks.residual_momentum_skip must be at least 1")
            if idiosyncratic_window is not None and idiosyncratic_window < 20:
                errors.append("strategy.lookbacks.idiosyncratic_volatility must be at least 20")
            volume_ma_short = parsed_lookbacks.get("volume_ma_short")
            volume_ma_long = parsed_lookbacks.get("volume_ma_long")
            if volume_ma_short is not None and volume_ma_short < 5:
                errors.append("strategy.lookbacks.volume_ma_short must be at least 5")
            if volume_ma_long is not None and volume_ma_long < 20:
                errors.append("strategy.lookbacks.volume_ma_long must be at least 20")
            if volume_ma_short is not None and volume_ma_long is not None and volume_ma_short >= volume_ma_long:
                errors.append("strategy.lookbacks.volume_ma_short must be less than volume_ma_long")
            turnover_window = parsed_lookbacks.get("turnover")
            turnover_min_observations = parsed_lookbacks.get("turnover_min_observations")
            if turnover_window is not None and turnover_window < 20:
                errors.append("strategy.lookbacks.turnover must be at least 20")
            if turnover_min_observations is not None and turnover_min_observations < 20:
                errors.append("strategy.lookbacks.turnover_min_observations must be at least 20")
            if (
                turnover_window is not None
                and turnover_min_observations is not None
                and turnover_min_observations > turnover_window
            ):
                errors.append(
                    "strategy.lookbacks.turnover_min_observations must not exceed turnover"
                )
            active_weights = [config["strategy"].get("weights", {})]
            members = config["strategy"].get("ensemble_members", [])
            if isinstance(members, list):
                active_weights.extend(member.get("weights", {}) for member in members if isinstance(member, dict))
            residual_active = False
            for weights in active_weights:
                if not isinstance(weights, dict) or "residual_reversal" not in weights:
                    continue
                try:
                    residual_weight = float(weights.get("residual_reversal", 0.0))
                except (TypeError, ValueError):
                    errors.append("strategy residual_reversal weight must be numeric")
                    continue
                residual_active = residual_active or abs(residual_weight) > 1e-12
            medium_reversal_active = False
            for weights in active_weights:
                if not isinstance(weights, dict) or "medium_reversal" not in weights:
                    continue
                try:
                    medium_reversal_weight = float(weights.get("medium_reversal", 0.0))
                except (TypeError, ValueError):
                    errors.append("strategy medium_reversal weight must be numeric")
                    continue
                medium_reversal_active = (
                    medium_reversal_active or abs(medium_reversal_weight) > 1e-12
                )
            if medium_reversal_active and not {
                "medium_reversal",
                "medium_reversal_skip",
            }.issubset(lookbacks):
                errors.append(
                    "strategy medium_reversal factor requires medium_reversal and "
                    "medium_reversal_skip lookbacks"
                )
            if residual_active and not {"residual_beta", "residual_reversal"}.issubset(lookbacks):
                errors.append("strategy residual_reversal factor requires residual_beta and residual_reversal lookbacks")
            residual_factor_requirements = {
                "residual_momentum": {"residual_beta", "residual_momentum", "residual_momentum_skip"},
                "low_idiosyncratic_volatility": {"residual_beta", "idiosyncratic_volatility"},
            }
            for factor_name, required in residual_factor_requirements.items():
                active = False
                for weights in active_weights:
                    if not isinstance(weights, dict) or factor_name not in weights:
                        continue
                    try:
                        factor_weight = float(weights.get(factor_name, 0.0))
                    except (TypeError, ValueError):
                        errors.append(f"strategy {factor_name} weight must be numeric")
                        continue
                    active = active or abs(factor_weight) > 1e-12
                if active and not required.issubset(lookbacks):
                    errors.append(
                        f"strategy {factor_name} factor requires "
                        + ", ".join(sorted(required))
                        + " lookbacks"
                    )
            required_factors = config["strategy"].get("required_factors", [])
            volume_ma_active = False
            for weights in active_weights:
                if not isinstance(weights, dict) or "low_volume_ma_distance" not in weights:
                    continue
                try:
                    volume_ma_weight = float(weights.get("low_volume_ma_distance", 0.0))
                except (TypeError, ValueError):
                    errors.append("strategy low_volume_ma_distance weight must be numeric")
                    continue
                volume_ma_active = volume_ma_active or abs(volume_ma_weight) > 1e-12
            if volume_ma_active and not {"volume_ma_short", "volume_ma_long"}.issubset(lookbacks):
                errors.append(
                    "strategy low_volume_ma_distance factor requires volume_ma_short and volume_ma_long lookbacks"
                )
            low_turnover_active = False
            for weights in active_weights:
                if not isinstance(weights, dict) or "low_turnover" not in weights:
                    continue
                try:
                    turnover_weight = float(weights.get("low_turnover", 0.0))
                except (TypeError, ValueError):
                    errors.append("strategy low_turnover weight must be numeric")
                    continue
                low_turnover_active = low_turnover_active or abs(turnover_weight) > 1e-12
            if low_turnover_active and not {
                "turnover",
                "turnover_min_observations",
            }.issubset(lookbacks):
                errors.append(
                    "strategy low_turnover factor requires turnover and "
                    "turnover_min_observations lookbacks"
                )
            high_52_week_active = False
            for weights in active_weights:
                if not isinstance(weights, dict) or "near_52_week_high" not in weights:
                    continue
                try:
                    high_52_week_weight = float(weights.get("near_52_week_high", 0.0))
                except (TypeError, ValueError):
                    errors.append("strategy near_52_week_high weight must be numeric")
                    continue
                high_52_week_active = high_52_week_active or abs(high_52_week_weight) > 1e-12
            if high_52_week_active and "high_52_week" not in lookbacks:
                errors.append(
                    "strategy near_52_week_high factor requires high_52_week lookback"
                )
            if not isinstance(required_factors, list):
                errors.append("strategy.required_factors must be a list")
            else:
                configured_weights = config["strategy"].get("weights", {})
                for factor_name in (str(value) for value in required_factors):
                    if not isinstance(configured_weights, dict) or factor_name not in configured_weights:
                        errors.append(
                            f"strategy.required_factors contains unweighted factor: {factor_name}"
                        )

    fundamental_policy = fundamental_factor_policy(config)
    if fundamental_policy.explicit and fundamental_policy.active and not fundamental_policy.point_in_time:
        factors = ", ".join(fundamental_policy.active_factors)
        errors.append(
            f"strategy fundamental factors ({factors}) require fundamentals.point_in_time=true"
        )

    if "breadth_overlay" in config:
        breadth = config["breadth_overlay"] if isinstance(config["breadth_overlay"], dict) else {}
        weak = float(breadth.get("weak_threshold", 0.35))
        critical = float(breadth.get("critical_threshold", 0.20))
        if not 0 <= critical <= weak <= 1:
            errors.append("breadth_overlay thresholds must satisfy 0 <= critical <= weak <= 1")
        if int(breadth.get("moving_average_days", 60)) < 2:
            errors.append("breadth_overlay.moving_average_days must be at least 2")
        if int(breadth.get("max_stale_trade_days", 5)) < 0:
            errors.append("breadth_overlay.max_stale_trade_days must be at least 0")
        for name in ("weak_exposure", "critical_exposure"):
            if not 0 <= float(breadth.get(name, 0.7 if name == "weak_exposure" else 0.4)) <= 1:
                errors.append(f"breadth_overlay.{name} must be in [0, 1]")

    risk_overlay = config.get("risk_overlay", {})
    if isinstance(risk_overlay, dict):
        combination_mode = str(
            risk_overlay.get("breadth_combination_mode", "product")
        ).strip().lower()
        if combination_mode not in {"product", "minimum"}:
            errors.append(
                "risk_overlay.breadth_combination_mode must be product or minimum"
            )

    if "trading" in config:
        if int(config["trading"].get("lot_size", 0)) <= 0:
            errors.append("trading.lot_size must be positive")
        for key in ["commission_rate", "stamp_tax_rate"]:
            if float(config["trading"].get(key, -1)) < 0:
                errors.append(f"trading.{key} must be non-negative")
    if "data" in config and int(config["data"].get("max_symbols", 0)) <= 0:
        errors.append("data.max_symbols must be positive")
    if "data" in config and int(config["data"].get("history_workers", 1)) < 1:
        errors.append("data.history_workers must be at least 1")
    if "data" in config and int(config["data"].get("history_retries", 1)) < 1:
        errors.append("data.history_retries must be at least 1")
    if "data" in config and str(config["data"].get("metadata_source", "baostock")).lower() not in {"baostock", "akshare"}:
        errors.append("data.metadata_source must be baostock or akshare")

    if "validation" in config:
        validation = config["validation"] if isinstance(config["validation"], dict) else {}
        if bool(validation.get("require_central_promotion_for_paper", False)) and not str(
            validation.get("promotion_decision_path", "")
        ).strip():
            errors.append(
                "validation.promotion_decision_path is required when "
                "require_central_promotion_for_paper=true"
            )
        if int(validation.get("min_execution_reconciliation_candidates", 3)) < 2:
            errors.append("validation.min_execution_reconciliation_candidates must be at least 2")
        max_gap = float(validation.get("max_execution_excess_gap", 0.10))
        if not 0 <= max_gap <= 1:
            errors.append("validation.max_execution_excess_gap must be in [0, 1]")
        min_correlation = float(validation.get("min_execution_rank_correlation", 0.50))
        if not -1 <= min_correlation <= 1:
            errors.append("validation.min_execution_rank_correlation must be in [-1, 1]")
        min_agreement = float(validation.get("min_execution_sign_agreement_ratio", 0.80))
        if not 0 <= min_agreement <= 1:
            errors.append("validation.min_execution_sign_agreement_ratio must be in [0, 1]")
    if "auto_live" in config:
        auto_live = config["auto_live"] if isinstance(config["auto_live"], dict) else {}
        confirmation = auto_live.get("action_confirmation", {})
        if confirmation and not isinstance(confirmation, dict):
            errors.append("auto_live.action_confirmation must be a mapping")
        elif isinstance(confirmation, dict) and confirmation:
            ttl_seconds = int(confirmation.get("ttl_seconds", 600))
            if ttl_seconds < 30 or ttl_seconds > 900:
                errors.append("auto_live.action_confirmation.ttl_seconds must be between 30 and 900")

    errors.extend(capital_budget_errors(config))

    if "rpa" in config:
        if not bool(config["rpa"].get("require_manual_final_confirm", True)):
            errors.append("rpa.require_manual_final_confirm must stay true")
        if not bool(config["rpa"].get("never_click_submit", True)):
            errors.append("rpa.never_click_submit must stay true")
        if float(config["rpa"].get("max_single_order_amount", 0)) <= 0:
            errors.append("rpa.max_single_order_amount must be positive")
        if float(config["rpa"].get("max_daily_order_amount", 0)) <= 0:
            errors.append("rpa.max_daily_order_amount must be positive")

    return errors


def validate_market_data(stocks: pd.DataFrame, prices: pd.DataFrame, fundamentals: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    if stocks.empty:
        errors.append("stocks cache is empty")
    if prices.empty:
        errors.append("prices cache is empty")
    if not prices.empty:
        duplicated = prices.duplicated(["date", "symbol"]).sum()
        if duplicated:
            errors.append(f"prices contains {duplicated} duplicated date/symbol rows")
        if (prices[["open", "close", "volume", "amount"]] < 0).any().any():
            errors.append("prices contains negative open/close/volume/amount values")
        if prices["close"].isna().any():
            errors.append("prices contains missing close values")
    if not fundamentals.empty and fundamentals.duplicated(["date", "symbol"]).any():
        errors.append("fundamentals contains duplicated date/symbol rows")
    return errors


def latest_data_date(prices: pd.DataFrame) -> date | None:
    if prices.empty or "date" not in prices.columns:
        return None
    dates = pd.to_datetime(prices["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().date()


def complete_market_dates(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    config: dict[str, Any],
    now: datetime | None = None,
) -> list[date]:
    if prices.empty or "date" not in prices or "symbol" not in prices:
        return []
    data_cfg = config.get("data", {})
    min_ratio = float(data_cfg.get("min_daily_price_coverage_ratio", 0.9))
    counts = prices.copy()
    counts["date"] = pd.to_datetime(counts["date"], errors="coerce").dt.date
    daily_counts = counts.dropna(subset=["date"]).groupby("date")["symbol"].nunique()
    if daily_counts.empty:
        return []
    configured_min_count = int(data_cfg.get("complete_market_min_symbol_count", 0) or 0)
    threshold = (
        max(1, configured_min_count) if configured_min_count > 0
        else max(1, math.ceil(float(daily_counts.max()) * min_ratio))
    )
    valid = set(daily_counts[daily_counts >= threshold].index)
    if bool(data_cfg.get("require_benchmark_on_signal_date", True)) and benchmark is not None and not benchmark.empty and "date" in benchmark:
        benchmark_dates = set(pd.to_datetime(benchmark["date"], errors="coerce").dt.date.dropna())
        valid &= benchmark_dates

    current = now or datetime.now()
    cutoff = parse_market_data_cutoff(data_cfg.get("same_day_close_available_after", "16:00"))
    latest_allowed = current.date() if current.time() >= cutoff else current.date() - pd.Timedelta(days=1)
    valid = {value for value in valid if value <= latest_allowed}
    return sorted(valid)


def parse_market_data_cutoff(value: object) -> dt_time:
    try:
        hour_text, minute_text = str(value).strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return dt_time(hour, minute)
    except (TypeError, ValueError):
        return dt_time(16, 0)


def latest_complete_market_date(prices: pd.DataFrame, benchmark: pd.DataFrame | None, config: dict[str, Any]) -> date | None:
    dates = complete_market_dates(prices, benchmark, config)
    return dates[-1] if dates else None


def filter_complete_market_data(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    dates = set(complete_market_dates(prices, benchmark, config))
    if not dates:
        return prices.iloc[0:0].copy(), benchmark.iloc[0:0].copy() if benchmark is not None else benchmark
    out_prices = prices[pd.to_datetime(prices["date"], errors="coerce").dt.date.isin(dates)].copy()
    if benchmark is None or benchmark.empty:
        return out_prices, benchmark
    out_benchmark = benchmark[pd.to_datetime(benchmark["date"], errors="coerce").dt.date.isin(dates)].copy()
    return out_prices, out_benchmark


def latest_close_map_asof(prices: pd.DataFrame, asof: date) -> dict[str, float]:
    if prices.empty:
        return {}
    data = prices[pd.to_datetime(prices["date"], errors="coerce").dt.date <= asof].copy()
    if data.empty:
        return {}
    data["symbol"] = data["symbol"].map(normalize_symbol)
    latest = data.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)
    return dict(zip(latest["symbol"].astype(str), pd.to_numeric(latest["close"], errors="coerce").fillna(0.0)))


def cache_age_days(prices: pd.DataFrame, today: date | None = None) -> int | None:
    latest = latest_data_date(prices)
    if latest is None:
        return None
    reference = today or date.today()
    return max(0, (reference - latest).days)


def data_freshness_warning(prices: pd.DataFrame, config: dict[str, Any], today: date | None = None) -> str | None:
    age = cache_age_days(prices, today=today)
    if age is None:
        return "行情缓存为空，无法生成模拟盘订单。"
    max_age = int(config.get("data", {}).get("max_cache_age_days", 10))
    if age > max_age:
        latest = latest_data_date(prices)
        return f"行情缓存已过期：最新行情日期 {latest}，距今天 {age} 天，超过允许的 {max_age} 天。请先刷新行情池。"
    return None


def max_lookback_days(config: dict[str, Any]) -> int:
    lookbacks = config.get("strategy", {}).get("lookbacks", {})
    values = [int(value) for value in lookbacks.values()] if lookbacks else [0]
    return max(values) + 1


def dependency_status() -> dict[str, bool]:
    return {
        "pandas": importlib.util.find_spec("pandas") is not None,
        "akshare": importlib.util.find_spec("akshare") is not None,
        "pyarrow": importlib.util.find_spec("pyarrow") is not None,
        "yaml": importlib.util.find_spec("yaml") is not None,
    }


def optional_dependency_status() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in OPTIONAL_DEPENDENCIES}
