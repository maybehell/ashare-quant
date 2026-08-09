from __future__ import annotations

import unittest

import pandas as pd

from ashare_quant.diagnostics import data_quality_report
from ashare_quant.fundamental_policy import (
    assert_fundamental_factor_policy,
    fundamental_factor_policy,
)
from ashare_quant.readiness import build_live_readiness, build_practical_readiness
from ashare_quant.validation import validate_config


class FundamentalPolicyTests(unittest.TestCase):
    def base_config(self) -> dict:
        return {
            "paths": {"cache_dir": "data", "output_dir": "outputs", "state_db": "state.sqlite3"},
            "data": {"max_symbols": 10, "max_cache_age_days": 10},
            "fundamentals": {"source": "price_proxy", "point_in_time": False},
            "universe": {
                "scope": "curated",
                "boards": ["main"],
                "min_listing_days": 60,
                "min_avg_amount": 1,
                "liquidity_lookback": 20,
            },
            "backtest": {
                "start": "2024-01-01",
                "initial_cash": 1_000_000,
                "rebalance": "monthly",
            },
            "strategy": {
                "top_n": 10,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {"momentum": 20},
                "weights": {"momentum": 1.0, "valuation": 0.0, "quality": 0.0},
            },
            "trading": {
                "lot_size": 100,
                "slippage_bps": 5,
                "commission_rate": 0.0,
                "min_commission": 5,
                "stamp_tax_rate": 0.0,
            },
        }

    def test_proxy_fundamentals_are_safe_only_while_factors_are_inactive(self):
        config = self.base_config()
        policy = fundamental_factor_policy(config)
        self.assertFalse(policy.active)
        self.assertTrue(policy.usable)

        config["strategy"]["ensemble_members"] = [
            {"weights": {"momentum": 0.8, "quality": 0.2}},
        ]
        policy = fundamental_factor_policy(config)
        self.assertEqual(policy.active_factors, ("quality",))
        self.assertFalse(policy.usable)
        self.assertIn(
            "strategy fundamental factors (quality) require fundamentals.point_in_time=true",
            validate_config(config),
        )

    def test_adaptive_fundamental_weights_are_detected(self):
        config = self.base_config()
        config["strategy"]["adaptive_weights"] = {
            "enabled": True,
            "risk_on_weights": {"momentum": 0.7, "valuation": 0.3},
        }
        policy = fundamental_factor_policy(config)
        self.assertEqual(policy.active_factors, ("valuation",))
        self.assertAlmostEqual(policy.max_abs_weight, 0.3)

    def test_runtime_guard_rejects_proxy_or_missing_fundamentals(self):
        config = self.base_config()
        with self.assertRaisesRegex(ValueError, "point-in-time"):
            assert_fundamental_factor_policy(
                config,
                {"valuation": 0.2},
                data_available=True,
            )

        config["fundamentals"] = {"source": "vendor_pit", "point_in_time": True}
        with self.assertRaisesRegex(ValueError, "non-empty"):
            assert_fundamental_factor_policy(
                config,
                {"quality": 0.2},
                data_available=False,
            )

        assert_fundamental_factor_policy(
            config,
            {"quality": 0.2},
            data_available=True,
        )

    def test_data_quality_and_readiness_expose_proxy_guard(self):
        config = self.base_config()
        quality = data_quality_report(
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            config,
        )
        values = dict(zip(quality["item"], quality["value"]))
        self.assertEqual(values["fundamental_source"], "price_proxy")
        self.assertEqual(values["fundamental_factor_status"], "inactive")

        config["live_guard"] = {
            "enabled": True,
            "require_fundamental_provenance": True,
            "require_industry_metadata": False,
            "require_listing_metadata": False,
        }
        strict = build_live_readiness(
            config,
            {},
            quality,
            pd.DataFrame(),
            pd.DataFrame(),
        )
        strict_row = strict[strict["check"] == "fundamental_factor_provenance"].iloc[0]
        self.assertTrue(bool(strict_row["passed"]))

        config["practical_guard"] = {
            "enabled": True,
            "require_fundamental_provenance": True,
            "require_metadata": False,
        }
        practical = build_practical_readiness(
            config,
            {},
            quality,
            pd.DataFrame(),
            pd.DataFrame(),
        )
        practical_row = practical[practical["check"] == "fundamental_factor_provenance"].iloc[0]
        self.assertTrue(bool(practical_row["passed"]))

        blocked = quality.copy()
        blocked.loc[
            blocked["item"] == "fundamental_factor_status",
            "value",
        ] = "blocked_non_point_in_time"
        strict = build_live_readiness(
            config,
            {},
            blocked,
            pd.DataFrame(),
            pd.DataFrame(),
        )
        strict_row = strict[strict["check"] == "fundamental_factor_provenance"].iloc[0]
        self.assertFalse(bool(strict_row["passed"]))


if __name__ == "__main__":
    unittest.main()
