from __future__ import annotations

from typing import Any


SHADOW_EVALUATION_SCHEMA_VERSION = 4
SHADOW_EVALUATION_FIELDS = (
    "shadow_evaluation_schema_version",
    "shadow_entry_price_basis",
    "shadow_return_basis",
    "shadow_benchmark_basis",
    "shadow_entry_cost_rate",
    "shadow_candidate_return_basis",
    "shadow_candidate_rebalance_rule",
    "shadow_candidate_execution_price_basis",
    "shadow_candidate_benchmark_basis",
    "shadow_candidate_buy_cost_rate",
    "shadow_candidate_sell_cost_rate",
)


def shadow_evaluation_identity(config: dict[str, Any]) -> dict[str, object]:
    trading = config.get("trading", {}) if isinstance(config.get("trading"), dict) else {}
    slippage = float(trading.get("slippage_bps", 0.0)) / 10000.0
    commission = float(trading.get("commission_rate", 0.0))
    transfer = float(trading.get("transfer_fee_rate", 0.0))
    stamp = float(trading.get("stamp_tax_rate", 0.0))
    buy_cost = slippage + commission + transfer
    return {
        "shadow_evaluation_schema_version": SHADOW_EVALUATION_SCHEMA_VERSION,
        "shadow_entry_price_basis": "execution_close_plus_proportional_entry_cost",
        "shadow_return_basis": "total_portfolio_including_cash",
        "shadow_benchmark_basis": "close_to_close",
        "shadow_entry_cost_rate": buy_cost,
        "shadow_candidate_return_basis": "continuous_monthly_rebalanced_total_portfolio_including_cash",
        "shadow_candidate_rebalance_rule": "first_post_freeze_signal_then_first_execution_each_month",
        "shadow_candidate_execution_price_basis": "next_session_open_with_side_specific_costs",
        "shadow_candidate_benchmark_basis": "signal_close_to_close",
        "shadow_candidate_buy_cost_rate": buy_cost,
        "shadow_candidate_sell_cost_rate": buy_cost + stamp,
    }


def shadow_evaluation_evidence(
    config: dict[str, Any], manifest: dict[str, Any]
) -> tuple[bool, dict[str, object], dict[str, object]]:
    current = shadow_evaluation_identity(config)
    frozen = {key: manifest.get(key, "") for key in SHADOW_EVALUATION_FIELDS}
    passed = all(str(frozen.get(key, "")) == str(value) for key, value in current.items())
    return passed, current, frozen