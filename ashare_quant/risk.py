from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def exposure_combination_mode(config: dict[str, Any]) -> str:
    cfg = config.get("risk_overlay", {}) if isinstance(config.get("risk_overlay"), dict) else {}
    return str(cfg.get("breadth_combination_mode", "product")).strip().lower()


def combine_exposure_multipliers(
    benchmark_exposure: float,
    breadth_exposure: float,
    config: dict[str, Any],
) -> float:
    benchmark = max(0.0, min(1.0, float(benchmark_exposure)))
    breadth = max(0.0, min(1.0, float(breadth_exposure)))
    mode = exposure_combination_mode(config)
    if mode == "product":
        exposure = benchmark * breadth
    elif mode == "minimum":
        exposure = min(benchmark, breadth)
    else:
        raise ValueError(
            "risk_overlay.breadth_combination_mode must be product or minimum"
        )
    return max(0.0, min(1.0, exposure))


def benchmark_exposure_multiplier(benchmark: pd.DataFrame, asof: date, config: dict[str, Any]) -> float:
    return float(benchmark_exposure_state(benchmark, asof, config)["exposure"])


def market_breadth_exposure_multiplier(prices: pd.DataFrame, asof: date, config: dict[str, Any]) -> float:
    return float(market_breadth_exposure_state(prices, asof, config)["exposure"])


def market_breadth_exposure_state(prices: pd.DataFrame, asof: date, config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("breadth_overlay", {}) if isinstance(config.get("breadth_overlay"), dict) else {}
    enabled = bool(cfg.get("enabled", False))
    neutral = {
        "breadth_enabled": enabled,
        "breadth_exposure": 1.0,
        "breadth_ratio": None,
        "breadth_ma_days": int(cfg.get("moving_average_days", 60)),
        "breadth_symbol_count": 0,
        "breadth_stale_symbol_count": 0,
        "breadth_latest_date": None,
        "breadth_state": "disabled" if not enabled else "insufficient_data",
    }
    if not enabled or prices.empty or not {"date", "symbol", "close"}.issubset(prices.columns):
        return {"exposure": 1.0, **neutral}
    ma_days = max(int(cfg.get("moving_average_days", 60)), 2)
    max_stale_trade_days = max(int(cfg.get("max_stale_trade_days", 5)), 0)
    min_history_ratio = max(0.1, min(1.0, float(cfg.get("min_history_ratio", 0.75))))
    min_symbols = max(int(cfg.get("min_symbols", 80)), 1)
    history = prices[pd.to_datetime(prices["date"], errors="coerce").dt.date <= asof].copy()
    if history.empty:
        return {"exposure": 1.0, **neutral}
    history["date"] = pd.to_datetime(history["date"], errors="coerce").dt.date
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    close = history.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    if close.empty:
        return {"exposure": 1.0, **neutral}
    moving_average = close.rolling(ma_days, min_periods=max(2, int(ma_days * min_history_ratio))).mean().iloc[-1]
    all_latest = close.ffill().iloc[-1]
    latest = close.iloc[-1] if max_stale_trade_days == 0 else close.ffill(limit=max_stale_trade_days).iloc[-1]
    stale = all_latest.notna() & latest.isna() & moving_average.notna()
    latest_date = close.index[-1]
    valid = latest.notna() & moving_average.notna() & latest.gt(0) & moving_average.gt(0)
    symbol_count = int(valid.sum())
    state_fields = {
        "breadth_symbol_count": symbol_count,
        "breadth_stale_symbol_count": int(stale.sum()),
        "breadth_latest_date": latest_date,
    }
    if symbol_count < min_symbols:
        return {"exposure": 1.0, **{**neutral, **state_fields}}
    ratio = float((latest[valid] >= moving_average[valid]).mean())
    weak_threshold = float(cfg.get("weak_threshold", 0.35))
    critical_threshold = float(cfg.get("critical_threshold", 0.20))
    if not 0 <= critical_threshold <= weak_threshold <= 1:
        return {"exposure": 1.0, **{**neutral, **state_fields, "breadth_ratio": ratio, "breadth_state": "invalid_thresholds"}}
    if ratio <= critical_threshold:
        exposure = float(cfg.get("critical_exposure", 0.40))
        state = "critical"
    elif ratio <= weak_threshold:
        exposure = float(cfg.get("weak_exposure", 0.70))
        state = "weak"
    else:
        exposure = 1.0
        state = "healthy"
    exposure = max(0.0, min(1.0, exposure))
    return {
        "exposure": exposure,
        "breadth_enabled": True,
        "breadth_exposure": exposure,
        "breadth_ratio": ratio,
        "breadth_ma_days": ma_days,
        "breadth_symbol_count": symbol_count,
        "breadth_stale_symbol_count": int(stale.sum()),
        "breadth_latest_date": latest_date,
        "breadth_state": state,
    }


def strategy_health_exposure_state(health: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    """Return the operational exposure overlay without changing strategy evidence."""
    cfg = config.get("strategy_health", {}) if isinstance(config.get("strategy_health"), dict) else {}
    warning_exposure = float(cfg.get("early_forward_warning_exposure", 0.50))
    warning_exposure = max(0.0, min(1.0, warning_exposure))
    warning_active = False
    paper_allowed: bool | None = None
    evidence = "missing"
    if not health.empty and "check" in health.columns:
        paper_rows = health[health["check"].astype(str) == "paper_orders_allowed"]
        if not paper_rows.empty:
            paper_row = paper_rows.iloc[-1]
            paper_allowed = _bool_like(paper_row.get("passed", paper_row.get("value", False)))
        selected = health[health["check"].astype(str) == "forward_early_warning_clear"]
        if not selected.empty:
            evidence = "present"
            row = selected.iloc[-1]
            warning_active = not _bool_like(row.get("passed", True))
    paused = paper_allowed is False
    exposure = 0.0 if paused else (warning_exposure if warning_active else 1.0)
    reason = "strategy_health_paused" if paused else ("early_forward_warning" if warning_active else "no_early_forward_warning")
    return {
        "enabled": True,
        "warning_active": warning_active,
        "paper_allowed": paper_allowed,
        "exposure": exposure,
        "warning_exposure": warning_exposure,
        "evidence": evidence,
        "reason": reason,
    }


def benchmark_exposure_state(benchmark: pd.DataFrame, asof: date, config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("risk_overlay", {})
    if not cfg.get("enabled", False) or benchmark.empty:
        return {
            "date": asof,
            "enabled": bool(cfg.get("enabled", False)),
            "exposure": 1.0,
            "benchmark_close": None,
            "moving_average": None,
            "annual_volatility": None,
            "bear_market": False,
            "high_volatility": False,
        }
    history = benchmark[benchmark["date"] <= asof].sort_values("date").copy()
    ma_days = int(cfg.get("benchmark_ma_days", 200))
    vol_days = int(cfg.get("benchmark_vol_days", 60))
    if len(history) < max(ma_days, vol_days) + 1:
        return {
            "date": asof,
            "enabled": True,
            "exposure": float(cfg.get("max_exposure", 1.0)),
            "benchmark_close": float(history["close"].iloc[-1]) if not history.empty else None,
            "moving_average": None,
            "annual_volatility": None,
            "bear_market": False,
            "high_volatility": False,
        }

    close = pd.to_numeric(history["close"], errors="coerce")
    latest = float(close.iloc[-1])
    moving_average = float(close.rolling(ma_days).mean().iloc[-1])
    returns = close.pct_change()
    annual_vol = float(returns.rolling(vol_days).std().iloc[-1] * (252**0.5))

    exposure = float(cfg.get("max_exposure", 1.0))
    bear_market = latest < moving_average
    if bear_market:
        exposure *= float(cfg.get("bear_market_exposure", 0.6))
    high_vol_threshold = float(cfg.get("high_volatility_threshold", 0.30))
    high_volatility = annual_vol > high_vol_threshold
    if high_volatility:
        exposure *= float(cfg.get("high_volatility_exposure", 0.7))

    min_exposure = float(cfg.get("min_exposure", 0.3))
    max_exposure = float(cfg.get("max_exposure", 1.0))
    exposure = max(min_exposure, min(max_exposure, exposure))
    return {
        "date": asof,
        "enabled": True,
        "exposure": exposure,
        "benchmark_close": latest,
        "moving_average": moving_average,
        "annual_volatility": annual_vol,
        "bear_market": bear_market,
        "high_volatility": high_volatility,
    }


def _bool_like(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
