import unittest
from datetime import date, datetime

import pandas as pd

from ashare_quant.validation import (
    cache_age_days,
    complete_market_dates,
    data_freshness_warning,
    dependency_status,
    latest_close_map_asof,
    optional_dependency_status,
    max_lookback_days,
    validate_config,
    validate_market_data,
)


class ValidationTests(unittest.TestCase):
    def test_rejects_unsupported_rebalance_execution_schema(self):
        config = {"trading": {"rebalance_execution_schema_version": 1}}

        errors = validate_config(config)

        self.assertIn("trading.rebalance_execution_schema_version must be 2", errors)

    def test_system_capital_ceiling_cannot_be_disabled(self):
        config = {
            "capital_budget": {"enabled": False, "max_total_capital": 999999},
            "backtest": {"initial_cash": 200001},
            "validation": {"capital_sensitivity": {"cash_levels": [100000, 200001]}},
        }

        errors = validate_config(config)

        self.assertIn("backtest.initial_cash exceeds effective maximum total capital", errors)
        self.assertIn(
            "validation.capital_sensitivity.cash_levels contains a value above "
            "effective maximum total capital",
            errors,
        )

    def test_strategy_family_budget_cannot_raise_system_ceiling(self):
        config = {
            "capital_budget": {
                "enabled": True,
                "intended_account_capital": 100000,
                "max_total_capital": 300000,
            },
            "backtest": {"initial_cash": 100000},
        }

        errors = validate_config(config)

        self.assertIn(
            "capital_budget.max_total_capital exceeds system maximum total capital", errors
        )

    def test_small_capital_budget_rejects_strategy_values_above_ceiling(self):
        config = {
            "capital_budget": {
                "enabled": True,
                "intended_account_capital": 100000,
                "max_total_capital": 200000,
            },
            "backtest": {"initial_cash": 250000},
            "validation": {"capital_sensitivity": {"cash_levels": [50000, 300000]}},
        }

        errors = validate_config(config)

        self.assertIn("backtest.initial_cash exceeds effective maximum total capital", errors)
        self.assertIn(
            "validation.capital_sensitivity.cash_levels contains a value above "
            "effective maximum total capital",
            errors,
        )

    def test_execution_buy_limits_cannot_exceed_effective_capital_limit(self):
        config = {
            "capital_budget": {
                "enabled": True,
                "intended_account_capital": 100000,
                "max_total_capital": 100000,
            },
            "rpa": {"max_single_order_amount": 100001, "max_daily_order_amount": 100001},
            "auto_live": {
                "risk_limits": {
                    "max_batch_buy_amount": 100001,
                    "max_daily_buy_amount": 100001,
                    "max_batch_gross_amount": 200001,
                    "max_daily_gross_amount": 200001,
                },
                "rollout": {
                    "stages": {
                        "full": {"max_buy_amount": 100001, "max_gross_amount": 200001}
                    }
                },
            },
        }

        errors = validate_config(config)

        self.assertIn("rpa.max_single_order_amount exceeds effective maximum total capital", errors)
        self.assertIn(
            "auto_live.risk_limits.max_batch_buy_amount exceeds effective maximum total capital",
            errors,
        )
        self.assertIn(
            "auto_live.rollout.stages.full.max_buy_amount exceeds effective maximum total capital",
            errors,
        )
        self.assertIn(
            "auto_live.risk_limits.max_batch_gross_amount exceeds twice effective maximum total capital",
            errors,
        )

    def test_small_capital_budget_accepts_all_levels_at_or_below_ceiling(self):
        config = {
            "capital_budget": {
                "enabled": True,
                "intended_account_capital": 100000,
                "max_total_capital": 200000,
            },
            "backtest": {"initial_cash": 100000},
            "validation": {
                "capital_sensitivity": {"cash_levels": [30000, 50000, 100000, 200000]}
            },
        }

        errors = validate_config(config)

        self.assertFalse(any("capital_budget" in error for error in errors))

    def test_config_validation_catches_bad_values(self):
        errors = validate_config(
            {
                "paths": {"cache_dir": "data", "output_dir": "outputs", "state_db": "state.sqlite3"},
                "universe": {"boards": ["main"], "min_listing_days": 60, "min_avg_amount": 1, "liquidity_lookback": 20},
                "backtest": {"start": "2024-01-01", "initial_cash": 1000000, "rebalance": "monthly"},
                "strategy": {
                    "top_n": 0,
                    "max_stock_weight": 1.5,
                    "max_industry_weight": 0.3,
                    "lookbacks": {"momentum": 60},
                    "weights": {"momentum": 1},
                    "industry_relative_cap": {
                        "enabled": True,
                        "max_active_weight": 1.5,
                        "max_unknown_weight": 1.5,
                        "drift_tolerance": 0.2,
                        "reference_weighting": "invalid",
                    },
                },
                "trading": {"lot_size": 100, "slippage_bps": 5, "commission_rate": -1, "min_commission": 5, "stamp_tax_rate": 0},
            }
        )
        self.assertIn("strategy.top_n must be positive", errors)
        self.assertIn("strategy.max_stock_weight must be in (0, 1]", errors)
        self.assertIn("trading.commission_rate must be non-negative", errors)
        self.assertIn("strategy.industry_relative_cap.max_active_weight must be in [0, 1]", errors)
        self.assertIn("strategy.industry_relative_cap.max_unknown_weight must be in [0, 1]", errors)
        self.assertIn("strategy.industry_relative_cap.drift_tolerance must be in [0, 0.10]", errors)
        self.assertIn(
            "strategy.industry_relative_cap.reference_weighting must be equal or liquidity_sqrt", errors
        )

    def test_turnover_control_exit_rank_multiplier_is_bounded(self):
        config = {
            "strategy": {
                "top_n": 10,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {},
                "weights": {},
                "turnover_control": {"enabled": True, "exit_rank_multiplier": 2.5},
            }
        }
        self.assertIn(
            "strategy.turnover_control.exit_rank_multiplier must be in [1.0, 2.0]",
            validate_config(config),
        )
    def test_turnover_control_rejects_unsupported_portfolio_builders(self):
        strategy = {
            "top_n": 10,
            "max_stock_weight": 0.1,
            "max_industry_weight": 0.3,
            "lookbacks": {},
            "weights": {},
            "turnover_control": {"enabled": True, "exit_rank_multiplier": 1.25},
            "capital_aware": {"enabled": True},
            "reference_anchor": {"enabled": True},
        }
        errors = validate_config({"strategy": strategy})
        self.assertIn("strategy.turnover_control is incompatible with strategy.capital_aware", errors)
        self.assertIn("strategy.turnover_control is incompatible with strategy.reference_anchor", errors)
    def test_history_fetch_parallelism_must_be_positive(self):
        config = {
            "data": {"max_symbols": 10, "history_workers": 0, "history_retries": 0},
            "strategy": {"top_n": 1, "max_stock_weight": 1.0, "max_industry_weight": 1.0, "lookbacks": {}, "weights": {}},
            "backtest": {"start": "2024-01-01", "initial_cash": 1},
            "trading": {"lot_size": 100, "commission_rate": 0, "stamp_tax_rate": 0},
        }
        errors = validate_config(config)
        self.assertIn("data.history_workers must be at least 1", errors)
        self.assertIn("data.history_retries must be at least 1", errors)

    def test_central_promotion_for_paper_requires_decision_path(self):
        errors = validate_config(
            {"validation": {"require_central_promotion_for_paper": True}}
        )

        self.assertIn(
            "validation.promotion_decision_path is required when "+
            "require_central_promotion_for_paper=true",
            errors,
        )

    def test_residual_reversal_lookbacks_are_validated(self):
        config = {
            "paths": {"cache_dir": "data", "output_dir": "outputs", "state_db": "state.sqlite3"},
            "data": {"max_symbols": 10},
            "universe": {"boards": ["main"], "min_listing_days": 60, "min_avg_amount": 1, "liquidity_lookback": 20},
            "backtest": {"start": "2024-01-01", "initial_cash": 1000000, "rebalance": "monthly"},
            "strategy": {
                "top_n": 10,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {"residual_beta": 10, "residual_reversal": 1},
                "weights": {"residual_reversal": 1.0},
            },
            "trading": {"lot_size": 100, "slippage_bps": 5, "commission_rate": 0, "min_commission": 5, "stamp_tax_rate": 0},
        }
        errors = validate_config(config)
        self.assertIn("strategy.lookbacks.residual_beta must be at least 20", errors)
        self.assertIn("strategy.lookbacks.residual_reversal must be at least 2", errors)

    def test_residual_reversal_factor_requires_explicit_lookbacks(self):
        config = {
            "paths": {"cache_dir": "data", "output_dir": "outputs", "state_db": "state.sqlite3"},
            "data": {"max_symbols": 10},
            "universe": {"boards": ["main"], "min_listing_days": 60, "min_avg_amount": 1, "liquidity_lookback": 20},
            "backtest": {"start": "2024-01-01", "initial_cash": 1000000, "rebalance": "monthly"},
            "strategy": {
                "top_n": 10, "max_stock_weight": 0.1, "max_industry_weight": 0.3,
                "lookbacks": {"momentum": "bad"}, "weights": {"residual_reversal": 1.0},
            },
            "trading": {"lot_size": 100, "slippage_bps": 5, "commission_rate": 0, "min_commission": 5, "stamp_tax_rate": 0},
        }
        errors = validate_config(config)
        self.assertIn("strategy.lookbacks.momentum must be positive", errors)
        self.assertIn("strategy residual_reversal factor requires residual_beta and residual_reversal lookbacks", errors)
        config["strategy"]["weights"]["residual_reversal"] = "bad"
        errors = validate_config(config)
        self.assertIn("strategy residual_reversal weight must be numeric", errors)

    def test_breadth_overlay_config_validation(self):
        base = {
            "paths": {"cache_dir": "data", "output_dir": "outputs", "state_db": "state.sqlite3"},
            "data": {"max_symbols": 10},
            "universe": {"boards": ["main"], "min_listing_days": 60, "min_avg_amount": 1, "liquidity_lookback": 20},
            "backtest": {"start": "2024-01-01", "initial_cash": 1000000, "rebalance": "monthly"},
            "strategy": {"top_n": 10, "max_stock_weight": 0.1, "max_industry_weight": 0.3, "lookbacks": {"momentum": 20}, "weights": {"momentum": 1.0}},
            "trading": {"lot_size": 100, "slippage_bps": 5, "commission_rate": 0, "min_commission": 5, "stamp_tax_rate": 0},
            "breadth_overlay": {"enabled": True, "moving_average_days": 1, "max_stale_trade_days": -1, "critical_threshold": 0.5, "weak_threshold": 0.2, "critical_exposure": -0.1, "weak_exposure": 1.1},
        }
        errors = validate_config(base)
        self.assertIn("breadth_overlay thresholds must satisfy 0 <= critical <= weak <= 1", errors)
        self.assertIn("breadth_overlay.moving_average_days must be at least 2", errors)
        self.assertIn("breadth_overlay.max_stale_trade_days must be at least 0", errors)
        self.assertIn("breadth_overlay.critical_exposure must be in [0, 1]", errors)
        self.assertIn("breadth_overlay.weak_exposure must be in [0, 1]", errors)

    def test_risk_overlay_combination_mode_is_validated(self):
        errors = validate_config(
            {"risk_overlay": {"breadth_combination_mode": "average"}}
        )

        self.assertIn(
            "risk_overlay.breadth_combination_mode must be product or minimum",
            errors,
        )

    def test_market_data_validation_catches_duplicates(self):
        prices = pd.DataFrame(
            [
                {"date": "2024-01-01", "symbol": "600000", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2024-01-01", "symbol": "600000", "open": 1, "close": 1, "volume": 1, "amount": 1},
            ]
        )
        errors = validate_market_data(pd.DataFrame([{"symbol": "600000"}]), prices, pd.DataFrame())
        self.assertTrue(any("duplicated" in error for error in errors))

    def test_max_lookback_days(self):
        self.assertEqual(max_lookback_days({"strategy": {"lookbacks": {"a": 20, "b": 60}}}), 61)

    def test_dependency_status_has_expected_keys(self):
        self.assertIn("pandas", dependency_status())
        self.assertIn("pyautogui", optional_dependency_status())

    def test_data_freshness_warning(self):
        prices = pd.DataFrame([{"date": "2026-01-01", "symbol": "600000", "open": 1, "close": 1, "volume": 1, "amount": 1}])
        self.assertEqual(cache_age_days(prices, today=date(2026, 1, 6)), 5)
        warning = data_freshness_warning(prices, {"data": {"max_cache_age_days": 3}}, today=date(2026, 1, 6))
        self.assertIn("行情缓存已过期", warning)

    def test_config_validation_blocks_unsafe_rpa(self):
        errors = validate_config(
            {
                "paths": {"cache_dir": "data", "output_dir": "outputs", "state_db": "state.sqlite3"},
                "data": {"max_symbols": 10},
                "universe": {"boards": ["main"], "min_listing_days": 60, "min_avg_amount": 1, "liquidity_lookback": 20},
                "backtest": {"start": "2024-01-01", "initial_cash": 1000000, "rebalance": "monthly"},
                "strategy": {
                    "top_n": 10,
                    "max_stock_weight": 0.1,
                    "max_industry_weight": 0.3,
                    "lookbacks": {"momentum": 60},
                    "weights": {"momentum": 1},
                },
                "trading": {"lot_size": 100, "slippage_bps": 5, "commission_rate": 0, "min_commission": 5, "stamp_tax_rate": 0},
                "rpa": {"require_manual_final_confirm": False, "never_click_submit": False, "max_single_order_amount": 1, "max_daily_order_amount": 1},
            }
        )
        self.assertIn("rpa.require_manual_final_confirm must stay true", errors)
        self.assertIn("rpa.never_click_submit must stay true", errors)

    def test_complete_market_dates_drop_partial_latest_day_and_require_benchmark(self):
        prices = pd.DataFrame(
            [
                {"date": "2026-06-15", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-06-15", "symbol": "000002", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-06-16", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-06-16", "symbol": "000002", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-06-17", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"date": "2026-06-15", "close": 100},
                {"date": "2026-06-16", "close": 101},
            ]
        )
        dates = complete_market_dates(
            prices,
            benchmark,
            {"data": {"min_daily_price_coverage_ratio": 0.9, "require_benchmark_on_signal_date": True}},
        )
        self.assertEqual(dates, [date(2026, 6, 15), date(2026, 6, 16)])

    def test_complete_market_dates_support_explicit_candidate_universe_count(self):
        prices = pd.DataFrame(
            [
                *[
                    {"date": "2026-06-15", "symbol": f"{idx:06d}", "close": 1}
                    for idx in range(5)
                ],
                *[
                    {"date": "2026-06-16", "symbol": f"{idx:06d}", "close": 1}
                    for idx in range(3)
                ],
            ]
        )
        dates = complete_market_dates(
            prices,
            None,
            {"data": {"complete_market_min_symbol_count": 3, "require_benchmark_on_signal_date": False}},
        )
        self.assertEqual(dates, [date(2026, 6, 15), date(2026, 6, 16)])

    def test_complete_market_dates_excludes_unfinalized_same_day(self):

        prices = pd.DataFrame(
            [
                {"date": "2026-07-14", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-07-15", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
            ]
        )
        config = {
            "data": {
                "min_daily_price_coverage_ratio": 0.9,
                "require_benchmark_on_signal_date": False,
                "same_day_close_available_after": "16:00",
            }
        }

        before_close = complete_market_dates(prices, None, config, now=datetime(2026, 7, 15, 14, 30))
        after_close = complete_market_dates(prices, None, config, now=datetime(2026, 7, 15, 16, 1))

        self.assertEqual(before_close, [date(2026, 7, 14)])
        self.assertEqual(after_close, [date(2026, 7, 14), date(2026, 7, 15)])

    def test_latest_close_map_carries_forward_missing_symbol_close(self):
        prices = pd.DataFrame(
            [
                {"date": "2026-06-15", "symbol": "000001", "open": 1, "close": 10, "volume": 1, "amount": 1},
                {"date": "2026-06-15", "symbol": "000002", "open": 1, "close": 20, "volume": 1, "amount": 1},
                {"date": "2026-06-16", "symbol": "000002", "open": 1, "close": 21, "volume": 1, "amount": 1},
            ]
        )
        close_map = latest_close_map_asof(prices, date(2026, 6, 16))
        self.assertEqual(close_map["000001"], 10)
        self.assertEqual(close_map["000002"], 21)

    def test_latest_close_map_normalizes_numeric_symbols(self):
        prices = pd.DataFrame(
            [
                {"date": "2026-06-15", "symbol": 1, "open": 1, "close": 10, "volume": 1, "amount": 1},
                {"date": "2026-06-16", "symbol": 2.0, "open": 1, "close": 21, "volume": 1, "amount": 1},
            ]
        )
        close_map = latest_close_map_asof(prices, date(2026, 6, 16))
        self.assertEqual(close_map["000001"], 10)
        self.assertEqual(close_map["000002"], 21)


if __name__ == "__main__":
    unittest.main()


def test_capital_aware_rejects_unknown_planning_schema_version():
    from ashare_quant.config import load_config

    config = load_config("configs/small_capital_100k_v2.yaml")
    config["strategy"]["capital_aware"] = {
        "enabled": True,
        "planning_schema_version": 3,
    }

    errors = validate_config(config)

    assert "strategy.capital_aware.planning_schema_version must be 1 or 2" in errors

def test_capital_aware_requires_schema_for_planning_options():
    from ashare_quant.config import load_config

    config = load_config("configs/small_capital_100k_v2.yaml")
    config["strategy"]["capital_aware"].pop("planning_schema_version")

    errors = validate_config(config)

    assert "strategy.capital_aware.planning_schema_version is required" in errors
def test_target_aware_planning_requires_schema_version_two():
    from ashare_quant.config import load_config

    config = load_config("configs/small_capital_100k_v2.yaml")
    config["strategy"]["capital_aware"]["respect_target_weights"] = True
    config["strategy"]["capital_aware"]["planning_schema_version"] = 1

    errors = validate_config(config)

    assert (
        "strategy.capital_aware.respect_target_weights requires planning_schema_version 2"
        in errors
    )


def test_target_aware_planning_accepts_schema_version_two():
    from ashare_quant.config import load_config

    config = load_config("configs/small_capital_100k_v2.yaml")
    config["strategy"]["capital_aware"]["respect_target_weights"] = True
    config["strategy"]["capital_aware"]["planning_schema_version"] = 2

    errors = validate_config(config)

    assert not any("capital_aware" in error for error in errors)

def test_target_aware_planning_rejects_non_numeric_schema_without_exception():
    from ashare_quant.config import load_config

    config = load_config("configs/small_capital_100k_v2.yaml")
    config["strategy"]["capital_aware"]["respect_target_weights"] = True
    config["strategy"]["capital_aware"]["planning_schema_version"] = "invalid"

    errors = validate_config(config)

    assert "strategy.capital_aware.planning_schema_version must be 1 or 2" in errors
    assert (
        "strategy.capital_aware.respect_target_weights requires planning_schema_version 2"
        in errors
    )

def test_target_aware_planning_rejects_missing_schema_without_exception():
    from ashare_quant.config import load_config

    config = load_config("configs/small_capital_100k_v2.yaml")
    config["strategy"]["capital_aware"]["respect_target_weights"] = True
    config["strategy"]["capital_aware"].pop("planning_schema_version")

    errors = validate_config(config)

    assert "strategy.capital_aware.planning_schema_version is required" in errors
    assert (
        "strategy.capital_aware.respect_target_weights requires planning_schema_version 2"
        in errors
    )

def test_all_repository_strategy_configs_respect_system_capital_ceiling():
    from pathlib import Path

    from ashare_quant.config import load_config

    violations = {}
    for path in sorted(Path("configs").glob("*.yaml")):
        config = load_config(path)
        capital_errors = [
            error
            for error in validate_config(config)
            if "maximum total capital" in error or "system maximum" in error
        ]
        if capital_errors:
            violations[path.name] = capital_errors

    assert violations == {}
