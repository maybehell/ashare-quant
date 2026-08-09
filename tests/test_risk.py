from datetime import date, timedelta
import unittest

import pandas as pd

from ashare_quant.risk import (
    benchmark_exposure_multiplier,
    combine_exposure_multipliers,
    market_breadth_exposure_state,
    strategy_health_exposure_state,
)


class RiskOverlayTests(unittest.TestCase):
    def test_exposure_combination_preserves_legacy_product_default(self):
        self.assertAlmostEqual(
            combine_exposure_multipliers(0.65, 0.40, {}),
            0.26,
        )

    def test_exposure_combination_can_use_more_conservative_signal(self):
        config = {"risk_overlay": {"breadth_combination_mode": "minimum"}}
        self.assertAlmostEqual(
            combine_exposure_multipliers(0.65, 0.40, config),
            0.40,
        )

    def test_exposure_combination_clamps_inputs_and_rejects_unknown_mode(self):
        self.assertEqual(combine_exposure_multipliers(2.0, -1.0, {}), 0.0)
        with self.assertRaisesRegex(ValueError, "product or minimum"):
            combine_exposure_multipliers(
                1.0,
                1.0,
                {"risk_overlay": {"breadth_combination_mode": "average"}},
            )

    def test_bear_market_reduces_exposure(self):
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(260)]
        close = [100 - i * 0.1 for i in range(260)]
        benchmark = pd.DataFrame({"date": days, "close": close})
        config = {
            "risk_overlay": {
                "enabled": True,
                "benchmark_ma_days": 20,
                "benchmark_vol_days": 20,
                "max_exposure": 1.0,
                "min_exposure": 0.3,
                "bear_market_exposure": 0.5,
                "high_volatility_threshold": 1.0,
                "high_volatility_exposure": 0.7,
            }
        }
        self.assertLess(benchmark_exposure_multiplier(benchmark, days[-1], config), 1.0)

    def test_disabled_overlay_keeps_full_exposure(self):
        benchmark = pd.DataFrame({"date": [date(2024, 1, 1)], "close": [100]})
        self.assertEqual(benchmark_exposure_multiplier(benchmark, date(2024, 1, 1), {"risk_overlay": {"enabled": False}}), 1.0)

    def test_market_breadth_overlay_reduces_exposure_without_future_data(self):
        days = pd.date_range("2025-01-01", periods=80, freq="B").date
        rows = []
        for index in range(100):
            symbol = f"60{index:04d}"
            for offset, dt in enumerate(days):
                close = 100.0 - offset if index < 90 else 100.0 + offset
                rows.append({"date": dt, "symbol": symbol, "close": close})
        prices = pd.DataFrame(rows)
        config = {"breadth_overlay": {"enabled": True, "moving_average_days": 60, "min_symbols": 80, "weak_threshold": 0.35, "critical_threshold": 0.20, "weak_exposure": 0.70, "critical_exposure": 0.40}}
        state = market_breadth_exposure_state(prices, days[-1], config)
        self.assertEqual(state["breadth_state"], "critical")
        self.assertAlmostEqual(state["breadth_ratio"], 0.10)
        self.assertEqual(state["exposure"], 0.40)
        future = prices.copy()
        future.loc[len(future)] = {"date": date(2030, 1, 1), "symbol": "600000", "close": 100000.0}
        self.assertEqual(market_breadth_exposure_state(future, days[-1], config)["exposure"], 0.40)

    def test_market_breadth_overlay_is_neutral_when_disabled_or_insufficient(self):
        prices = pd.DataFrame([{"date": date(2026, 1, 1), "symbol": "600000", "close": 10.0}])
        self.assertEqual(market_breadth_exposure_state(prices, date(2026, 1, 1), {})["exposure"], 1.0)
        config = {"breadth_overlay": {"enabled": True, "moving_average_days": 60, "min_symbols": 80}}
        self.assertEqual(market_breadth_exposure_state(prices, date(2026, 1, 1), config)["breadth_state"], "insufficient_data")

    def test_market_breadth_overlay_excludes_prices_stale_beyond_configured_trade_days(self):
        days = pd.bdate_range("2025-09-01", periods=80).date
        rows = []
        for idx in range(80):
            for symbol_num in range(100):
                if symbol_num >= 90 and idx >= 70:
                    continue
                rows.append({"date": days[idx], "symbol": f"{symbol_num:06d}", "close": 100.0 + idx})
        prices = pd.DataFrame(rows)
        config = {
            "breadth_overlay": {
                "enabled": True,
                "moving_average_days": 60,
                "min_symbols": 80,
                "max_stale_trade_days": 5,
            }
        }

        state = market_breadth_exposure_state(prices, days[-1], config)

        self.assertEqual(state["breadth_symbol_count"], 90)
        self.assertEqual(state["breadth_stale_symbol_count"], 10)
        self.assertEqual(state["breadth_latest_date"], days[-1])

    def test_early_forward_warning_reduces_operational_exposure(self):
        health = pd.DataFrame([
            {"check": "forward_early_warning_clear", "passed": False, "severity": "warning"},
            {"check": "paper_orders_allowed", "passed": True, "value": True},
        ])
        state = strategy_health_exposure_state(
            health,
            {"strategy_health": {"early_forward_warning_exposure": 0.50}},
        )
        self.assertTrue(state["warning_active"])
        self.assertEqual(state["exposure"], 0.50)
        self.assertEqual(state["reason"], "early_forward_warning")

    def test_failed_paper_health_forces_zero_exposure(self):
        health = pd.DataFrame([
            {"check": "forward_early_warning_clear", "passed": True},
            {"check": "paper_orders_allowed", "passed": False, "value": False},
        ])
        state = strategy_health_exposure_state(health, {})
        self.assertEqual(state["exposure"], 0.0)
        self.assertFalse(state["paper_allowed"])
        self.assertEqual(state["reason"], "strategy_health_paused")

    def test_health_summary_can_use_value_when_passed_column_is_absent(self):
        health = pd.DataFrame([{"check": "paper_orders_allowed", "value": True}])
        state = strategy_health_exposure_state(health, {})
        self.assertEqual(state["exposure"], 1.0)
        self.assertTrue(state["paper_allowed"])

    def test_health_exposure_is_full_without_warning_and_clamped(self):
        healthy = pd.DataFrame([{"check": "forward_early_warning_clear", "passed": True}])
        self.assertEqual(
            strategy_health_exposure_state(healthy, {"strategy_health": {"early_forward_warning_exposure": 2.0}})["exposure"],
            1.0,
        )
        warning = pd.DataFrame([{"check": "forward_early_warning_clear", "passed": False}])
        self.assertEqual(
            strategy_health_exposure_state(warning, {"strategy_health": {"early_forward_warning_exposure": -1.0}})["exposure"],
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
