from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.audit import capital_sensitivity_config_sha256
from ashare_quant.capital_sensitivity import (
    capital_variant_config,
    deployed_cash_ratio,
    reusable_backtest_paths,
    run_capital_sensitivity,
)


class CapitalSensitivityTests(unittest.TestCase):
    def test_deployed_cash_ratio_excludes_pre_trade_warmup(self):
        equity = pd.DataFrame(
            {
                "cash": [100.0, 20.0, 10.0],
                "equity": [100.0, 100.0, 100.0],
                "fills": [0, 1, 0],
            }
        )

        self.assertAlmostEqual(deployed_cash_ratio(equity), 0.15)

    def test_variant_is_isolated_and_forces_safe_runtime(self):
        config = {
            "paths": {"output_dir": "outputs", "state_db": "data/state.sqlite3"},
            "backtest": {"initial_cash": 1000000},
            "auto_live": {"enabled": True, "allow_broker_submit": True, "emergency_stop": False},
        }

        variant = capital_variant_config(config, 300000.0, Path("isolated"))

        self.assertEqual(variant["backtest"]["initial_cash"], 300000.0)
        self.assertIn("cash_300000", variant["paths"]["output_dir"])
        self.assertIn("cash_300000", variant["paths"]["state_db"])
        self.assertTrue(variant["runtime"]["skip_expensive_diagnostics"])
        self.assertFalse(variant["auto_live"]["enabled"])
        self.assertFalse(variant["auto_live"]["allow_broker_submit"])
        self.assertTrue(variant["auto_live"]["emergency_stop"])
        self.assertTrue(config["auto_live"]["enabled"])

    def test_completed_matching_variant_is_reusable_but_stale_hash_is_not(self):
        output = Path.cwd() / "test_outputs" / "capital_resume"
        config = {
            "paths": {"output_dir": str(output)},
            "backtest": {"initial_cash": 2500},
            "strategy": {"top_n": 20},
        }
        output.mkdir(parents=True, exist_ok=True)
        required = ["performance_metrics.csv", "equity_curve.csv", "trades.csv", "positions.csv"]
        for name in required:
            pd.DataFrame([{"ok": True}]).to_csv(output / name, index=False)
        from ashare_quant.audit import backtest_config_sha256
        pd.DataFrame([{"item": "backtest_config_sha256", "value": backtest_config_sha256(config)}]).to_csv(
            output / "run_manifest.csv", index=False
        )

        self.assertTrue(reusable_backtest_paths(config))
        config["strategy"]["top_n"] = 21
        self.assertFalse(reusable_backtest_paths(config))
    def test_evidence_hash_ignores_initial_cash_but_tracks_sensitivity_criteria(self):
        config = {
            "backtest": {"initial_cash": 1_000_000},
            "strategy": {"top_n": 20},
            "validation": {
                "capital_sensitivity": {
                    "cash_levels": [100000, 300000, 1000000],
                    "max_abs_return_drag": 0.02,
                }
            },
        }
        baseline = capital_sensitivity_config_sha256(config)
        different_cash = deepcopy(config)
        different_cash["backtest"]["initial_cash"] = 600000
        different_threshold = deepcopy(config)
        different_threshold["validation"]["capital_sensitivity"]["max_abs_return_drag"] = 0.01

        self.assertEqual(capital_sensitivity_config_sha256(different_cash), baseline)
        self.assertNotEqual(capital_sensitivity_config_sha256(different_threshold), baseline)

    def test_capital_sensitivity_identifies_minimum_passing_level(self):
        output = Path.cwd() / "test_outputs" / "capital_sensitivity"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output), "state_db": str(output / "main.sqlite3")},
            "backtest": {"initial_cash": 1000000},
            "validation": {
                "capital_sensitivity": {
                    "cash_levels": [100000, 300000, 1000000],
                    "max_abs_return_drag": 0.02,
                    "max_excess_cash_ratio_vs_reference": 0.06,
                    "min_final_positions": 20,
                }
            },
        }

        def fake_backtest(variant):
            cash = int(variant["backtest"]["initial_cash"])
            run_dir = Path(variant["paths"]["output_dir"])
            run_dir.mkdir(parents=True, exist_ok=True)
            returns = {100000: 0.08, 300000: 0.095, 1000000: 0.10}
            cash_ratios = {100000: 0.25, 300000: 0.10, 1000000: 0.05}
            position_counts = {100000: 10, 300000: 25, 1000000: 80}
            frames = {
                "performance_metrics.csv": pd.DataFrame(
                    [
                        {"metric": "total_return", "value": returns[cash]},
                        {"metric": "excess_total_return", "value": returns[cash] - 0.04},
                        {"metric": "max_drawdown", "value": -0.10},
                        {"metric": "annualized_turnover", "value": 2.0},
                    ]
                ),
                "equity_curve.csv": pd.DataFrame(
                    [{"cash": cash * cash_ratios[cash], "equity": cash, "fills": 1}]
                ),
                "trades.csv": pd.DataFrame([{"symbol": "600000"}]),
                "rejected_orders.csv": pd.DataFrame(columns=["symbol"]),
                "positions.csv": pd.DataFrame(
                    [{"symbol": f"{idx:06d}"} for idx in range(position_counts[cash])]
                ),
                "run_manifest.csv": pd.DataFrame(
                    [{"item": key, "value": "same-data"} for key in
                     ["stocks_sha256", "prices_sha256", "fundamentals_sha256", "benchmark_sha256"]]
                ),
            }
            paths = {}
            for name, frame in frames.items():
                path = run_dir / name
                frame.to_csv(path, index=False)
                paths[name] = path
            return paths

        with patch("ashare_quant.capital_sensitivity.run_backtest", side_effect=fake_backtest):
            paths = run_capital_sensitivity(config)

        summary = pd.read_csv(paths["capital_sensitivity.csv"])
        audit = pd.read_csv(paths["capital_sensitivity_audit.csv"]).set_index("check")
        self.assertEqual(summary["initial_cash"].tolist(), [100000.0, 300000.0, 1000000.0])
        self.assertFalse(bool(summary.loc[summary["initial_cash"] == 100000, "passed"].iloc[0]))
        self.assertTrue(bool(summary.loc[summary["initial_cash"] == 300000, "passed"].iloc[0]))
