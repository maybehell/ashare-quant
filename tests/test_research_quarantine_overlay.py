from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.research import refresh_strategy_research_metadata


class ResearchQuarantineOverlayTests(unittest.TestCase):
    def test_forward_failure_overrides_historical_research_label(self):
        root = Path.cwd() / "test_outputs" / self._testMethodName
        root.mkdir(parents=True, exist_ok=True)
        research_path = root / "strategy_research.csv"
        failure_path = root / "candidate_failure_history.csv"
        config = {
            "paths": {"output_dir": str(root)},
            "validation": {"candidate_failure_history_path": str(failure_path)},
            "benchmark": {"symbol": "000300", "name": "沪深300"},
            "strategy": {
                "name": "historical-winner",
                "top_n": 100,
                "max_stock_weight": 0.05,
                "weights": {"liquidity": 0.6, "momentum": 0.4},
            },
            "walk_forward": {"candidates": []},
        }
        strategy_hash = live_strategy_hash(config)
        pd.DataFrame(
            [
                {
                    "rank": 1,
                    "name": "historical-winner",
                    "strategy_hash": strategy_hash,
                    "research_origin": "formal",
                    "source_config": "active_config",
                    "research_only": False,
                    "experiment_status": "formal",
                    "experiment_reason": "",
                    "config_overrides": "{}",
                }
            ]
        ).to_csv(research_path, index=False)
        pd.DataFrame(
            [
                {
                    "candidate_id": "failed-1",
                    "strategy_hash": strategy_hash,
                    "behavior_hash": "behavior-1",
                    "strategy_name": "historical-winner",
                    "status": "early_forward_degradation",
                    "freeze_price_end": "2026-06-30",
                    "observed_forward_trading_days": 8,
                    "forward_excess_return": -0.15,
                    "recorded_at": "2026-07-10T00:00:00+08:00",
                }
            ]
        ).to_csv(failure_path, index=False)

        with patch("ashare_quant.research.write_research_gate"):
            refresh_strategy_research_metadata(config, research_path)

        refreshed = pd.read_csv(research_path)
        self.assertEqual(
            refreshed.loc[0, "experiment_status"], "quarantined_forward_failure"
        )
        self.assertIn("early_forward_degradation", refreshed.loc[0, "experiment_reason"])
        self.assertIn("observed_days=8", refreshed.loc[0, "experiment_reason"])
        self.assertIn("excess_return=-0.15", refreshed.loc[0, "experiment_reason"])


if __name__ == "__main__":
    unittest.main()
