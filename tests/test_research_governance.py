import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.research_governance import (
    assert_research_candidates_allowed,
    candidate_hashes,
    gate_snapshot,
    write_research_gate,
)


class FakeStore:
    def __init__(self, dates):
        self.dates = dates

    def read_frame(self, name, required=False):
        if name != "prices":
            return pd.DataFrame()
        return pd.DataFrame({"date": self.dates, "symbol": "600000", "close": 10.0})

    def load_benchmark(self, required=False):
        return pd.DataFrame()


class ResearchGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / "research_governance"
        self.output = self.root / "outputs"
        self.output.mkdir(parents=True, exist_ok=True)
        self.config = {
            "paths": {"output_dir": str(self.output)},
            "validation": {
                "research_governance_state_path": str(self.root / "state.json"),
                "research_min_new_trading_days": 3,
                "min_anti_overfit_candidates": 2,
            },
            "strategy": {"name": "formal", "weights": {"liquidity": 1.0}},
        }
        self.candidates = [{"name": "formal", "weights": {"liquidity": 1.0}}]
        hashes = candidate_hashes(self.config, self.candidates)
        pd.DataFrame(
            [{"name": "formal", "strategy_hash": hashes["formal"], "anti_overfit_passed": False}]
        ).to_csv(self.output / "strategy_research.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [{"item": "prices_last_date", "value": "2026-01-02"}]
        ).to_csv(self.output / "strategy_research_manifest.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(
            [{"item": "strategy_family_selection_ready", "passed": False}]
        ).to_csv(self.output / "strategy_family_selection.csv", index=False, encoding="utf-8-sig")
        (self.root / "state.json").write_text(
            json.dumps(
                {
                    "last_research_market_date": "2026-01-02",
                    "approved_strategy_hashes": hashes,
                }
            ),
            encoding="utf-8",
        )

    def test_gate_stops_new_candidates_without_new_sample(self):
        dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
        changed = [{"name": "new_candidate", "weights": {"low_volatility": 1.0}}]
        with patch("ashare_quant.research_governance.DataStore.from_config", return_value=FakeStore(dates)):
            snapshot = gate_snapshot(self.config, changed)
            with self.assertRaisesRegex(RuntimeError, "new_candidate"):
                assert_research_candidates_allowed(self.config, changed)

        self.assertEqual(snapshot["status"], "stop_new_candidates")
        self.assertEqual(snapshot["new_trading_days"], 1)
        self.assertEqual(snapshot["new_candidates"], ["new_candidate"])

    def test_existing_candidates_can_refresh_while_gate_is_stopped(self):
        dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
        with patch("ashare_quant.research_governance.DataStore.from_config", return_value=FakeStore(dates)):
            assert_research_candidates_allowed(self.config, self.candidates)
            path = write_research_gate(self.config, self.candidates, commit=True)

        gate = pd.read_csv(path, encoding="utf-8-sig")
        row = gate[gate["item"] == "research_candidate_gate"].iloc[0]
        self.assertEqual(row["value"], "stop_new_candidates")
        state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_research_market_date"], "2026-01-02")

    def test_new_sample_unlocks_one_research_cycle(self):
        dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"])
        changed = [{"name": "new_candidate", "weights": {"low_volatility": 1.0}}]
        with patch("ashare_quant.research_governance.DataStore.from_config", return_value=FakeStore(dates)):
            snapshot = gate_snapshot(self.config, changed)
            assert_research_candidates_allowed(self.config, changed)
            write_research_gate(self.config, changed, commit=True)

        self.assertEqual(snapshot["status"], "open")
        state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_research_market_date"], "2026-01-07")
        self.assertIn("new_candidate", state["approved_strategy_hashes"])

    def test_partial_latest_market_day_does_not_unlock_research(self):
        class PartialStore:
            def read_frame(self, name, required=False):
                return pd.DataFrame(
                    [
                        {"date": "2026-01-02", "symbol": "600000", "close": 10},
                        {"date": "2026-01-02", "symbol": "600001", "close": 10},
                        {"date": "2026-01-05", "symbol": "600000", "close": 10},
                    ]
                )

            def load_benchmark(self, required=False):
                return pd.DataFrame(
                    [
                        {"date": "2026-01-02", "close": 100},
                        {"date": "2026-01-05", "close": 101},
                    ]
                )

        self.config["data"] = {
            "min_daily_price_coverage_ratio": 0.9,
            "require_benchmark_on_signal_date": True,
            "same_day_close_available_after": "00:00",
        }
        with patch("ashare_quant.research_governance.DataStore.from_config", return_value=PartialStore()):
            snapshot = gate_snapshot(self.config, self.candidates)

        self.assertEqual(snapshot["latest_market_date"], "2026-01-02")
        self.assertEqual(snapshot["new_trading_days"], 0)
        self.assertEqual(snapshot["status"], "stop_new_candidates")


if __name__ == "__main__":
    unittest.main()
