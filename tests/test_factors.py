from datetime import date
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.factors import apply_holding_rank_buffer, build_configured_target_portfolio, build_configured_target_weights, build_target_weights, cap_and_normalize, compute_factor_values, compute_scores, effective_factor_weights, neutralize_factors_by_exposures, neutralize_factors_by_industry, strategy_member_configs


class FactorTests(unittest.TestCase):
    def test_factor_scores_and_weight_caps(self):
        dates = pd.date_range("2024-01-01", periods=90, freq="B").date
        rows = []
        stocks = []
        fundamentals = []
        for i in range(5):
            symbol = f"60000{i}"
            stocks.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "board": "main",
                    "industry": "tech" if i < 3 else "bank",
                    "list_date": date(2020, 1, 1),
                    "is_st": False,
                }
            )
            fundamentals.append({"date": dates[-1], "symbol": symbol, "pe": 10 + i, "pb": 1 + i * 0.1, "roe": 0.1 + i * 0.01})
            for t, dt in enumerate(dates):
                close = 10 + i + t * (0.01 + i * 0.001)
                rows.append({"date": dt, "symbol": symbol, "open": close, "high": close, "low": close, "close": close, "volume": 1000000, "amount": close * 1000000})
        config = {
            "strategy": {
                "top_n": 4,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
                "lookbacks": {"momentum": 20, "volatility": 20, "liquidity": 20},
                "weights": {"momentum": 0.3, "valuation": 0.2, "quality": 0.2, "low_volatility": 0.15, "liquidity": 0.15},
            }
        }
        scores = compute_scores(pd.DataFrame(rows), pd.DataFrame(stocks), pd.DataFrame(fundamentals), dates[-1], config)
        weights = build_target_weights(scores, config)
        self.assertFalse(scores.empty)
        self.assertLessEqual(weights.max(), 0.1)
        self.assertLessEqual(len(weights), 4)
        self.assertIn("trend_quality", scores.columns)
        self.assertIn("avg_amount", scores.columns)

    def test_residual_reversal_removes_market_beta_before_ranking(self):
        dates = pd.date_range("2025-01-01", periods=160, freq="B").date
        market_returns = pd.Series([0.0] + [0.001] * (len(dates) - 1), index=dates)
        stock_returns = {
            "600000": market_returns * 1.2,
            "600001": market_returns * 0.8,
            "600002": market_returns * 2.0,
        }
        stock_returns["600000"].iloc[-10:] += 0.01
        stock_returns["600001"].iloc[-10:] -= 0.01
        rows = []
        for symbol, values in stock_returns.items():
            closes = (1.0 + values).cumprod() * 10.0
            for dt, close in closes.items():
                rows.append({"date": dt, "symbol": symbol, "close": close, "amount": 100000000.0})
        benchmark = pd.DataFrame({"date": dates, "close": (1.0 + market_returns).cumprod().values * 100.0})
        factors = compute_factor_values(
            pd.DataFrame(rows),
            pd.DataFrame(),
            dates[-1],
            {"momentum": 20, "volatility": 20, "drawdown": 20, "short_reversal": 10, "residual_reversal": 10, "residual_beta": 120, "liquidity": 20},
            benchmark,
        ).set_index("symbol")
        self.assertGreater(factors.loc["600001", "residual_reversal"], factors.loc["600002", "residual_reversal"])
        self.assertGreater(factors.loc["600002", "residual_reversal"], factors.loc["600000", "residual_reversal"])

    def test_residual_reversal_is_not_computed_unless_explicitly_configured(self):
        dates = pd.date_range("2025-01-01", periods=40, freq="B").date
        prices = pd.DataFrame([
            {"date": dt, "symbol": symbol, "close": 10.0 + index * 0.01, "amount": 1000000.0}
            for symbol in ("600000", "600001") for index, dt in enumerate(dates)
        ])
        benchmark = pd.DataFrame({"date": dates, "close": [100.0 + index for index in range(len(dates))]})
        factors = compute_factor_values(prices, pd.DataFrame(), dates[-1], {"momentum": 20, "volatility": 20, "liquidity": 20}, benchmark)
        self.assertTrue((factors["residual_reversal"] == 0.0).all())

    def test_residual_reversal_is_neutral_without_benchmark(self):
        dates = pd.date_range("2025-01-01", periods=40, freq="B").date
        prices = pd.DataFrame([
            {"date": dt, "symbol": symbol, "close": 10.0 + index * 0.01, "amount": 1000000.0}
            for symbol in ("600000", "600001") for index, dt in enumerate(dates)
        ])
        factors = compute_factor_values(prices, pd.DataFrame(), dates[-1], {"momentum": 20, "volatility": 20, "liquidity": 20})
        self.assertTrue((factors["residual_reversal"] == 0.0).all())

    def test_holding_rank_buffer_retains_only_holdings_inside_exit_rank(self):
        scores = pd.DataFrame(
            [
                {"symbol": f"s{rank}", "score": 10 - rank, "industry": "test"}
                for rank in range(1, 7)
            ]
        )
        config = {
            "strategy": {
                "top_n": 3,
                "turnover_control": {"enabled": True, "exit_rank_multiplier": 1.5},
            }
        }

        buffered = apply_holding_rank_buffer(scores, {"s5", "s6"}, config)

        self.assertEqual(buffered.head(3)["symbol"].tolist(), ["s5", "s1", "s2"])
        self.assertEqual(buffered.iloc[-1]["symbol"], "s6")

    def test_holding_rank_buffer_is_inert_when_disabled(self):
        scores = pd.DataFrame([{"symbol": "s1", "score": 1.0}, {"symbol": "s2", "score": 0.0}])
        buffered = apply_holding_rank_buffer(
            scores, {"s2"}, {"strategy": {"top_n": 1, "turnover_control": {"enabled": False}}}
        )
        pd.testing.assert_frame_equal(buffered, scores)
    def test_unknown_industry_does_not_share_one_cap_bucket(self):
        scores = pd.DataFrame(
            [
                {"symbol": f"600{i:03d}", "score": 100 - i, "industry": "unknown", "board": "main"}
                for i in range(10)
            ]
        )
        config = {
            "strategy": {
                "top_n": 10,
                "max_stock_weight": 0.1,
                "max_industry_weight": 0.3,
            }
        }
        weights = build_target_weights(scores, config)
        self.assertEqual(len(weights), 10)
        self.assertAlmostEqual(weights.sum(), 1.0)

    def test_industry_relative_cap_limits_active_industry_weight(self):
        scores = pd.DataFrame(
            [
                {
                    "symbol": f"t{index}",
                    "score": 100 - index,
                    "industry": "tech",
                    "avg_amount": 10_000_000_000.0,
                }
                for index in range(4)
            ]
            + [
                {
                    "symbol": f"b{index}",
                    "score": 50 - index,
                    "industry": "bank",
                    "avg_amount": 100_000_000.0,
                }
                for index in range(4)
            ]
        )
        config = {
            "strategy": {
                "top_n": 4,
                "max_stock_weight": 0.4,
                "max_industry_weight": 0.8,
                "weighting": "liquidity_sqrt",
                "industry_relative_cap": {
                    "enabled": True,
                    "reference_weighting": "equal",
                    "max_active_weight": 0.10,
                },
            }
        }

        weights = build_target_weights(scores, config)
        industries = scores.set_index("symbol")["industry"]
        tech_weight = float(weights[industries.reindex(weights.index) == "tech"].sum())

        self.assertAlmostEqual(float(weights.sum()), 1.0)
        self.assertLessEqual(float(weights.max()), 0.4 + 1e-12)
        self.assertLessEqual(tech_weight, 0.6 + 1e-12)
        self.assertGreater(tech_weight, 0.5)

    def test_industry_relative_cap_aggregates_unknown_industry(self):
        scores = pd.DataFrame(
            [
                {
                    "symbol": f"u{index}",
                    "score": 100 - index,
                    "industry": "unknown",
                    "avg_amount": 10_000_000_000.0,
                }
                for index in range(4)
            ]
            + [
                {
                    "symbol": f"t{index}",
                    "score": 50 - index,
                    "industry": "tech",
                    "avg_amount": 100_000_000.0,
                }
                for index in range(4)
            ]
        )
        config = {
            "strategy": {
                "top_n": 4,
                "max_stock_weight": 0.4,
                "max_industry_weight": 0.8,
                "weighting": "liquidity_sqrt",
                "industry_relative_cap": {
                    "enabled": True,
                    "reference_weighting": "equal",
                    "max_active_weight": 0.20,
                    "aggregate_unknown": True,
                    "max_unknown_weight": 0.25,
                },
            }
        }

        weights = build_target_weights(scores, config)
        industries = scores.set_index("symbol")["industry"]
        unknown_weight = float(weights[industries.reindex(weights.index) == "unknown"].sum())

        self.assertAlmostEqual(float(weights.sum()), 0.95)
        self.assertLessEqual(unknown_weight, 0.25 + 1e-12)
        self.assertGreater(unknown_weight, 0.0)

    def test_liquidity_weighting_caps_single_stock(self):
        raw = pd.Series({"a": 1000.0, "b": 1.0, "c": 1.0})
        weights = cap_and_normalize(raw, 0.6)
        self.assertLessEqual(weights.max(), 0.6)
        self.assertAlmostEqual(weights.sum(), 1.0)

    def test_industry_neutralization_removes_group_means_only_for_sufficient_groups(self):
        factors = pd.DataFrame(
            [
                {"symbol": "a", "industry": "tech", "momentum": 3.0},
                {"symbol": "b", "industry": "tech", "momentum": 1.0},
                {"symbol": "c", "industry": "bank", "momentum": -1.0},
                {"symbol": "d", "industry": "bank", "momentum": -3.0},
                {"symbol": "e", "industry": "unknown", "momentum": 5.0},
            ]
        )
        result = neutralize_factors_by_industry(factors, ["momentum"], min_group_size=2)
        known = result[result["industry"].isin(["tech", "bank"])]
        means = known.groupby("industry")["momentum"].mean()
        self.assertTrue((means.abs() < 1e-12).all())
        self.assertNotEqual(float(result.loc[result["industry"] == "unknown", "momentum"].iloc[0]), 0.0)

    def test_cross_sectional_neutralization_removes_liquidity_exposure(self):
        liquidity = pd.Series([float(index) for index in range(100)])
        noise = pd.Series([float((index % 7) - 3) for index in range(100)])
        factors = pd.DataFrame(
            {
                "symbol": [f"s{index:03d}" for index in range(100)],
                "liquidity": liquidity,
                "momentum": 2.5 * liquidity + noise,
            }
        )

        result = neutralize_factors_by_exposures(
            factors,
            ["momentum"],
            ["liquidity"],
            min_observations=50,
        )

        correlation = pd.to_numeric(result["momentum"]).corr(pd.to_numeric(result["liquidity"]))
        self.assertLess(abs(float(correlation)), 0.05)
        self.assertTrue(result["liquidity"].equals(factors["liquidity"]))

    def test_adaptive_factor_weights_shift_only_in_bull_low_volatility(self):
        dates = pd.date_range("2024-01-01", periods=260, freq="B").date
        benchmark = pd.DataFrame({"date": dates, "close": [100 + i * 0.1 for i in range(len(dates))]})
        config = {
            "strategy": {
                "weights": {"momentum": 0.10, "low_volatility": 0.30, "liquidity": 0.60},
                "adaptive_weights": {
                    "enabled": True,
                    "risk_on_blend": 0.5,
                    "benchmark_ma_days": 20,
                    "benchmark_vol_days": 20,
                    "high_volatility_threshold": 1.0,
                    "risk_on_weights": {"momentum": 0.30, "trend_quality": 0.20, "low_volatility": 0.10, "liquidity": 0.40},
                },
            }
        }

        weights = effective_factor_weights(config, benchmark, dates[-1])

        self.assertGreater(weights["momentum"], 0.10)
        self.assertGreater(weights["trend_quality"], 0.0)
        self.assertLess(weights["liquidity"], 0.60)

    def test_adaptive_factor_weights_keep_base_when_history_insufficient(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="B").date
        benchmark = pd.DataFrame({"date": dates, "close": [100 + i for i in range(len(dates))]})
        config = {
            "strategy": {
                "weights": {"momentum": 0.10, "low_volatility": 0.30, "liquidity": 0.60},
                "adaptive_weights": {
                    "enabled": True,
                    "benchmark_ma_days": 20,
                    "benchmark_vol_days": 20,
                    "risk_on_weights": {"momentum": 1.0},
                },
            }
        }

        self.assertEqual(effective_factor_weights(config, benchmark, dates[-1]), config["strategy"]["weights"])


    def test_ensemble_target_portfolio_returns_blended_signal_scores(self):
        config = {
            "strategy": {
                "top_n": 1,
                "max_stock_weight": 1.0,
                "max_industry_weight": 1.0,
                "lookbacks": {},
                "weights": {"momentum": 1.0},
                "ensemble_members": [
                    {"blend_weight": 3.0, "name": "member_a", "weights": {"momentum": 1.0}},
                    {"blend_weight": 1.0, "name": "member_b", "weights": {"low_volatility": 1.0}},
                ],
            }
        }
        stocks = pd.DataFrame(
            [
                {"symbol": "a", "industry": "x", "board": "main"},
                {"symbol": "b", "industry": "y", "board": "main"},
            ]
        )
        score_frames = [
            pd.DataFrame(
                [
                    {"symbol": "a", "score": 2.0, "industry": "x", "board": "main"},
                    {"symbol": "b", "score": 1.0, "industry": "y", "board": "main"},
                ]
            ),
            pd.DataFrame(
                [
                    {"symbol": "b", "score": 2.0, "industry": "y", "board": "main"},
                    {"symbol": "a", "score": 1.0, "industry": "x", "board": "main"},
                ]
            ),
        ]

        with patch("ashare_quant.factors.compute_scores", side_effect=score_frames):
            weights, scores = build_configured_target_portfolio(
                pd.DataFrame(), stocks, pd.DataFrame(), date(2026, 1, 1), config
            )

        self.assertAlmostEqual(float(weights["a"]), 0.75)
        self.assertAlmostEqual(float(weights["b"]), 0.25)
        score_map = scores.set_index("symbol")["score"].to_dict()
        self.assertGreater(score_map["a"], score_map["b"])
        self.assertEqual(scores.loc[0, "symbol"], "a")

    def test_ensemble_blends_member_target_portfolios(self):
        config = {
            "strategy": {
                "top_n": 2,
                "max_stock_weight": 0.6,
                "max_industry_weight": 1.0,
                "lookbacks": {},
                "weights": {"momentum": 1.0},
                "ensemble_members": [
                    {"blend_weight": 1.0, "name": "momentum", "weights": {"momentum": 1.0}},
                    {"blend_weight": 1.0, "name": "defensive", "weights": {"low_volatility": 1.0}},
                ],
            }
        }
        stocks = pd.DataFrame([
            {"symbol": "a", "industry": "x", "board": "main"},
            {"symbol": "b", "industry": "x", "board": "main"},
            {"symbol": "c", "industry": "y", "board": "main"},
            {"symbol": "d", "industry": "y", "board": "main"},
        ])
        score_frames = [
            pd.DataFrame([
                {"symbol": "a", "score": 4, "industry": "x", "board": "main"},
                {"symbol": "b", "score": 3, "industry": "x", "board": "main"},
            ]),
            pd.DataFrame([
                {"symbol": "c", "score": 4, "industry": "y", "board": "main"},
                {"symbol": "d", "score": 3, "industry": "y", "board": "main"},
            ]),
        ]
        with patch("ashare_quant.factors.compute_scores", side_effect=score_frames):
            weights = build_configured_target_weights(pd.DataFrame(), stocks, pd.DataFrame(), date(2026, 1, 1), config)
        self.assertEqual(set(weights.index), {"a", "b", "c", "d"})
        self.assertAlmostEqual(weights.sum(), 1.0)
        self.assertTrue((weights == 0.25).all())
        members = strategy_member_configs(config)
        self.assertEqual([round(weight, 2) for weight, _ in members], [0.5, 0.5])


if __name__ == "__main__":
    unittest.main()
