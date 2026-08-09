import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.stress import run_cost_stress, stress_data_hashes


class StressTests(unittest.TestCase):
    def test_disabled_stress_returns_empty_frame(self):
        result = run_cost_stress({"stress_test": {"enabled": False}})
        self.assertTrue(result.empty)

    def test_cost_stress_uses_lightweight_backtest_outputs(self):
        output = Path.cwd() / "test_outputs" / "stress_lightweight"
        output.mkdir(parents=True, exist_ok=True)
        captured = []
        config = {
            "paths": {"output_dir": str(output), "cache_dir": str(output / "cache"), "state_db": str(output / "state.sqlite3")},
            "stress_test": {"enabled": True, "cost_scenarios": [{"name": "base", "slippage_bps": 5, "commission_rate": 0.0003}]},
            "trading": {"slippage_bps": 5, "commission_rate": 0.0003},
        }

        def fake_backtest(scenario_config):
            captured.append(scenario_config)
            scenario_output = Path(scenario_config["paths"]["output_dir"])
            scenario_output.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"metric": "total_return", "value": 0.1},
                    {"metric": "max_drawdown", "value": -0.1},
                    {"metric": "excess_total_return", "value": 0.02},
                    {"metric": "information_ratio", "value": 0.5},
                ]
            ).to_csv(scenario_output / "performance_metrics.csv", index=False)
            return {}

        with (
            patch("ashare_quant.stress.run_backtest", side_effect=fake_backtest),
            patch("ashare_quant.stress.stress_data_hashes", return_value={}),
        ):
            result = run_cost_stress(config)

        self.assertEqual(result["total_return"].iloc[0], 0.1)
        self.assertTrue(captured[0]["runtime"]["skip_expensive_diagnostics"])


    def test_stress_hashes_use_committed_fetch_scope(self):
        stocks = pd.DataFrame([{"symbol": "600000"}, {"symbol": "300001"}])
        prices = pd.DataFrame(
            [
                {"symbol": "600000", "date": pd.Timestamp("2026-01-02").date(), "close": 10},
                {"symbol": "300001", "date": pd.Timestamp("2026-01-02").date(), "close": 12},
            ]
        )
        fundamentals = pd.DataFrame([{"symbol": "600000"}, {"symbol": "300001"}])
        benchmark = pd.DataFrame(
            [{"date": pd.Timestamp("2026-01-02").date(), "close": 100}]
        )

        class Store:
            def load_market_data(self):
                return stocks, prices, fundamentals

            def load_benchmark(self, required=False):
                return benchmark

        scoped = (
            stocks.iloc[[0]].copy(),
            prices.iloc[[0]].copy(),
            fundamentals.iloc[[0]].copy(),
            True,
            "complete",
        )
        config = {
            "paths": {"cache_dir": "unused"},
            "backtest": {"start": "2026-01-01", "end": "2026-01-02"},
        }
        with (
            patch("ashare_quant.stress.DataStore.from_config", return_value=Store()),
            patch(
                "ashare_quant.stress.scope_market_frames_to_committed_fetch",
                return_value=scoped,
            ) as scope,
            patch(
                "ashare_quant.stress.filter_complete_market_data",
                side_effect=lambda price_frame, benchmark_frame, cfg: (
                    price_frame,
                    benchmark_frame,
                ),
            ),
        ):
            hashes = stress_data_hashes(config)

        scope.assert_called_once()
        self.assertTrue(all(hashes.values()))


if __name__ == "__main__":
    unittest.main()
