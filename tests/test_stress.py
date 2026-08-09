import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.stress import run_cost_stress


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


if __name__ == "__main__":
    unittest.main()
