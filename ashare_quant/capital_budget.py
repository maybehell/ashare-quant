from __future__ import annotations

import math
from typing import Any


SYSTEM_MAX_TOTAL_CAPITAL = 200000.0


def capital_budget_errors(config: dict[str, Any]) -> list[str]:
    """Validate the system ceiling and any stricter strategy-family budget."""
    policy = config.get("capital_budget", {})
    errors: list[str] = []
    limit = SYSTEM_MAX_TOTAL_CAPITAL
    policy_enabled = isinstance(policy, dict) and bool(policy.get("enabled", False))
    if policy_enabled:
        configured_limit = _positive_number(policy.get("max_total_capital"))
        if configured_limit is None:
            errors.append("capital_budget.max_total_capital must be a positive finite number")
        elif configured_limit > SYSTEM_MAX_TOTAL_CAPITAL:
            errors.append("capital_budget.max_total_capital exceeds system maximum total capital")
        else:
            limit = configured_limit

        intended = _positive_number(policy.get("intended_account_capital"))
        if intended is None:
            errors.append("capital_budget.intended_account_capital must be a positive finite number")
        elif intended > limit:
            errors.append("capital_budget.intended_account_capital must not exceed max_total_capital")

    initial_cash = _positive_number(config.get("backtest", {}).get("initial_cash"))
    if initial_cash is not None and initial_cash > limit:
        errors.append("backtest.initial_cash exceeds effective maximum total capital")

    sensitivity = config.get("validation", {}).get("capital_sensitivity", {})
    levels = sensitivity.get("cash_levels", []) if isinstance(sensitivity, dict) else []
    if levels and not isinstance(levels, list):
        errors.append("validation.capital_sensitivity.cash_levels must be a list")
    elif isinstance(levels, list):
        for value in levels:
            parsed = _positive_number(value)
            if parsed is None:
                continue
            if parsed > limit:
                errors.append(
                    "validation.capital_sensitivity.cash_levels contains a value above "
                    "effective maximum total capital"
                )
                break

    rpa = config.get("rpa", {})
    if isinstance(rpa, dict):
        for key in ("max_single_order_amount", "max_daily_order_amount"):
            value = _positive_number(rpa.get(key))
            if value is not None and value > limit:
                errors.append(f"rpa.{key} exceeds effective maximum total capital")

    auto_live = config.get("auto_live", {})
    if isinstance(auto_live, dict):
        risk = auto_live.get("risk_limits", {})
        if isinstance(risk, dict):
            for key in ("max_single_order_amount", "max_batch_buy_amount", "max_daily_buy_amount"):
                value = _positive_number(risk.get(key))
                if value is not None and value > limit:
                    errors.append(f"auto_live.risk_limits.{key} exceeds effective maximum total capital")
            for key in ("max_batch_gross_amount", "max_daily_gross_amount"):
                value = _positive_number(risk.get(key))
                if value is not None and value > limit * 2:
                    errors.append(f"auto_live.risk_limits.{key} exceeds twice effective maximum total capital")
        rollout = auto_live.get("rollout", {})
        stages = rollout.get("stages", {}) if isinstance(rollout, dict) else {}
        if isinstance(stages, dict):
            for stage, stage_limits in stages.items():
                if not isinstance(stage_limits, dict):
                    continue
                for key in ("max_single_order_amount", "max_buy_amount"):
                    value = _positive_number(stage_limits.get(key))
                    if value is not None and value > limit:
                        errors.append(
                            f"auto_live.rollout.stages.{stage}.{key} exceeds effective maximum total capital"
                        )
                gross = _positive_number(stage_limits.get("max_gross_amount"))
                if gross is not None and gross > limit * 2:
                    errors.append(
                        f"auto_live.rollout.stages.{stage}.max_gross_amount exceeds twice effective maximum total capital"
                    )
    return errors


def capital_budget_limit(config: dict[str, Any]) -> float | None:
    policy = config.get("capital_budget", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return SYSTEM_MAX_TOTAL_CAPITAL
    configured = _positive_number(policy.get("max_total_capital"))
    return min(configured, SYSTEM_MAX_TOTAL_CAPITAL) if configured is not None else None


def strategy_deployable_equity(current_equity: object, config: dict[str, Any]) -> float:
    """Return equity the strategy may allocate without exceeding its capital policy."""
    try:
        equity = float(current_equity)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(equity) or equity <= 0:
        return 0.0
    limit = capital_budget_limit(config)
    return min(equity, limit) if limit is not None else 0.0


def _positive_number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed
