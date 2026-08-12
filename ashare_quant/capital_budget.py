from __future__ import annotations

import math
from typing import Any


def capital_budget_errors(config: dict[str, Any]) -> list[str]:
    """Validate an opt-in hard capital ceiling for a research strategy family."""
    policy = config.get("capital_budget", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return []

    errors: list[str] = []
    limit = _positive_number(policy.get("max_total_capital"))
    if limit is None:
        return ["capital_budget.max_total_capital must be a positive finite number"]

    intended = _positive_number(policy.get("intended_account_capital"))
    if intended is None:
        errors.append("capital_budget.intended_account_capital must be a positive finite number")
    elif intended > limit:
        errors.append("capital_budget.intended_account_capital must not exceed max_total_capital")

    initial_cash = _positive_number(config.get("backtest", {}).get("initial_cash"))
    if initial_cash is not None and initial_cash > limit:
        errors.append("backtest.initial_cash exceeds capital_budget.max_total_capital")

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
                    "capital_budget.max_total_capital"
                )
                break
    return errors


def capital_budget_limit(config: dict[str, Any]) -> float | None:
    policy = config.get("capital_budget", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return None
    return _positive_number(policy.get("max_total_capital"))


def _positive_number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed
