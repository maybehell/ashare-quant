import unittest
from unittest.mock import patch
from datetime import date

import pandas as pd

from ashare_quant.walkforward import apply_candidate, apply_portfolio_returns, estimate_rebalance_cost, overlap_ratio, quantize_rebalance_target, select_candidate, simulate_period, walk_forward_report


class WalkForwardTests(unittest.TestCase):
    def test_apply_candidate_overrides_strategy(self):
        config = {"strategy": {"name": "base", "top_n": 20, "weights": {"momentum": 1}}}
        candidate = {"name": "test", "top_n": 50, "weights": {"low_volatility": 1}}
        out = apply_candidate(config, candidate)
        self.assertEqual(out["strategy"]["name"], "test")
        self.assertEqual(out["strategy"]["top_n"], 50)
        self.assertEqual(out["strategy"]["weights"], {"low_volatility": 1})
        candidate = {"name": "test", "max_industry_weight": 0.4}
        out = apply_candidate({"strategy": {"name": "base", "max_industry_weight": 0.3}}, candidate)
        self.assertEqual(out["strategy"]["max_industry_weight"], 0.4)
        self.assertEqual(config["strategy"]["name"], "base")

    def test_apply_candidate_applies_research_overrides_without_mutating_base(self):
        config = {
            "strategy": {"name": "base", "weights": {"liquidity": 1.0}},
            "risk_overlay": {"enabled": False},
            "breadth_overlay": {"enabled": False},
        }
        candidate = {
            "name": "archived",
            "config_overrides": {
                "risk_overlay": {"enabled": True, "min_exposure": 0.3},
                "breadth_overlay": {"enabled": True, "minimum_exposure": 0.4},
                "backtest": {"rebalance": "quarterly"},
            },
        }

        out = apply_candidate(config, candidate)

        self.assertTrue(out["risk_overlay"]["enabled"])
        self.assertTrue(out["breadth_overlay"]["enabled"])
        self.assertEqual(out["backtest"]["rebalance"], "quarterly")
        self.assertFalse(config["risk_overlay"]["enabled"])
        self.assertFalse(config["breadth_overlay"]["enabled"])

        with self.assertRaisesRegex(ValueError, "config_overrides"):
            apply_candidate(config, {"name": "invalid", "config_overrides": []})
        with self.assertRaisesRegex(ValueError, "only supports rebalance"):
            apply_candidate(config, {"name": "invalid", "config_overrides": {"backtest": {"initial_cash": 1}}})

    def test_portfolio_returns_drift_weights(self):
        period_return, drifted = apply_portfolio_returns(
            pd.Series({"600000": 0.5, "600001": 0.5}),
            pd.Series({"600000": 0.1, "600001": -0.1}),
        )

        self.assertAlmostEqual(period_return, 0.0)
        self.assertAlmostEqual(drifted["600000"], 0.55)
        self.assertAlmostEqual(drifted["600001"], 0.45)

    def test_rebalance_cost_includes_minimum_fees_slippage_and_sell_tax(self):
        config = {
            "backtest": {"initial_cash": 100000},
            "trading": {
                "commission_rate": 0.0003,
                "min_commission": 5,
                "stamp_tax_rate": 0.0005,
                "transfer_fee_rate": 0.00001,
                "slippage_bps": 10,
            },
        }
        buy_cost, buy_turnover = estimate_rebalance_cost(
            pd.Series(dtype=float),
            pd.Series({"600000": 0.5, "600001": 0.5}),
            1.0,
            config,
        )
        sell_cost, sell_turnover = estimate_rebalance_cost(
            pd.Series({"600000": 0.5}),
            pd.Series(dtype=float),
            1.0,
            config,
        )

        self.assertAlmostEqual(buy_turnover, 1.0)
        self.assertAlmostEqual(buy_cost, 0.00131)
        self.assertAlmostEqual(sell_turnover, 0.5)
        self.assertAlmostEqual(sell_cost, 0.000905)

    def test_quantize_rebalance_target_matches_formal_lot_thresholds(self):
        config = {
            "backtest": {"initial_cash": 100_000},
            "trading": {"lot_size": 100},
        }
        current = pd.Series({"600000": 0.10, "600001": 0.01})
        target = pd.Series(
            {
                "600000": 0.11,
                "600001": 0.0,
                "600002": 0.0101,
                "600003": 0.50,
            }
        )
        prices = pd.Series(
            {"600000": 10.0, "600001": 20.0, "600002": 10.0}
        )

        adjusted = quantize_rebalance_target(
            current, target, 1.0, prices, config
        )

        # Exactly one lot is executable and must match the formal simulator.
        self.assertAlmostEqual(adjusted["600000"], 0.11)
        # Sells can clear odd lots down to one share.
        self.assertNotIn("600001", adjusted.index)
        # A 101-share target is rounded down to one 100-share buy lot.
        self.assertAlmostEqual(adjusted["600002"], 0.01)
        self.assertNotIn("600003", adjusted.index)

    def test_empty_walk_forward_when_disabled(self):
        folds, summary = walk_forward_report(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"walk_forward": {"enabled": False}})
        self.assertTrue(folds.empty)
        self.assertTrue(summary.empty)
        self.assertIn("train_days", summary.columns)
        self.assertEqual(overlap_ratio(126, 126), 0.0)
        self.assertEqual(overlap_ratio(126, 63), 0.5)

    def test_research_signal_executes_at_next_open_without_lookahead(self):
        dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
        prices = pd.DataFrame(
            [
                {"date": dates[0], "symbol": "600000", "open": 10.0, "close": 10.0},
                {"date": dates[1], "symbol": "600000", "open": 12.0, "close": 13.0},
                {"date": dates[2], "symbol": "600000", "open": 13.0, "close": 13.0},
            ]
        )
        stocks = pd.DataFrame([{"symbol": "600000"}])
        benchmark = pd.DataFrame(
            [{"date": current, "close": 100.0} for current in dates]
        )
        config = {
            "backtest": {"initial_cash": 1_000_000},
            "trading": {
                "reserve_cash_ratio": 0.0,
                "slippage_bps": 0.0,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
            },
        }

        with (
            patch("ashare_quant.walkforward.eligible_universe", return_value=stocks),
            patch(
                "ashare_quant.walkforward.build_configured_target_portfolio",
                return_value=(pd.Series({"600000": 1.0}), pd.DataFrame()),
            ) as target,
            patch("ashare_quant.walkforward.benchmark_exposure_multiplier", return_value=1.0),
            patch("ashare_quant.walkforward.market_breadth_exposure_multiplier", return_value=1.0),
        ):
            result = simulate_period(
                prices,
                stocks,
                pd.DataFrame(),
                benchmark,
                config,
                dates,
            )

        # The 20% overnight gap belongs to the old cash position. The new
        # position receives only the next-open to close return.
        self.assertAlmostEqual(result["strategy_return"], 13.0 / 12.0 - 1.0)
        self.assertAlmostEqual(result["gross_strategy_return"], 13.0 / 12.0 - 1.0)
        self.assertAlmostEqual(result["transaction_cost_drag"], 0.0)
        self.assertEqual(result["rebalance_count"], 1.0)
        target.assert_called_once()
        self.assertEqual(target.call_args.args[3], dates[0])

    def test_fixed_selection_uses_current_strategy_candidate(self):
        candidates = [{"name": "current"}, {"name": "tempting"}]

        def fake_simulate(prices, stocks, fundamentals, benchmark, config, dates):
            name = config["strategy"]["name"]
            return {"strategy_return": 0.0, "benchmark_return": 0.0, "excess_return": 10.0 if name == "tempting" else 1.0, "max_drawdown": 0.0}

        with patch("ashare_quant.walkforward.simulate_period", side_effect=fake_simulate):
            selected, result = select_candidate(
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
                {"strategy": {"name": "base"}},
                candidates,
                [],
                "fixed",
            )
        self.assertEqual(selected["name"], "current")
        self.assertEqual(result["excess_return"], 1.0)


if __name__ == "__main__":
    unittest.main()
