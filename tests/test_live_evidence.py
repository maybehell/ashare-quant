from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.audit import backtest_config_sha256
from ashare_quant.live_evidence import (
    REQUIRED_STRATEGY_EVIDENCE_FILES,
    file_sha256,
    sync_live_strategy_evidence,
)


class LiveEvidenceTests(unittest.TestCase):
    def fixture(self, name: str) -> tuple[dict, dict, Path, Path]:
        root = Path.cwd() / "test_outputs" / name
        source = root / "strategy"
        live = root / "live"
        source.mkdir(parents=True, exist_ok=True)
        live.mkdir(parents=True, exist_ok=True)
        strategy = {
            "paths": {"output_dir": str(source)},
            "strategy": {"name": "formal", "top_n": 20},
            "backtest": {"start": "2023-01-01"},
        }
        live_config = {
            "paths": {"output_dir": str(live)},
            "strategy": {"name": "formal", "top_n": 20},
            "backtest": {"start": "2023-01-01"},
            "auto_live": {"enabled": True, "emergency_stop": True},
        }
        for filename in REQUIRED_STRATEGY_EVIDENCE_FILES:
            pd.DataFrame([{"name": filename, "value": 1}]).to_csv(source / filename, index=False)
        pd.DataFrame([{"symbol": "600000", "side": "buy", "quantity": 100}]).to_csv(
            source / "orders.csv", index=False
        )
        return strategy, live_config, source, live

    def test_sync_copies_only_allowlisted_verified_evidence(self):
        strategy, live_config, source, live = self.fixture("live_evidence_sync")
        optional_research = (
            "strategy_research.csv",
            "strategy_research_manifest.csv",
            "strategy_selection_bias.csv",
            "strategy_selection_bias_splits.csv",
            "strategy_candidate_family.csv",
            "strategy_candidate_similarity.csv",
            "strategy_family_selection.csv",
            "strategy_family_selection_splits.csv",
        )
        for filename in optional_research:
            pd.DataFrame([{"name": filename, "value": 1}]).to_csv(source / filename, index=False)
        frames = {
            "stocks": pd.DataFrame([{"symbol": "600000"}]),
            "prices": pd.DataFrame([{"date": "2026-07-10", "symbol": "600000", "close": 10.0}]),
            "fundamentals": pd.DataFrame(),
            "benchmark": pd.DataFrame([{"date": "2026-07-10", "close": 4000.0}]),
        }
        with (
            patch("ashare_quant.live_evidence.readiness_evidence_issues", return_value=[]),
            patch("ashare_quant.live_evidence.current_manifest_frames", return_value=frames),
        ):
            paths = sync_live_strategy_evidence(strategy, live_config)

        self.assertTrue((live / "performance_metrics.csv").exists())
        self.assertEqual(
            file_sha256(source / "performance_metrics.csv"),
            file_sha256(live / "performance_metrics.csv"),
        )
        self.assertTrue((live / "strategy_research.csv").exists())
        self.assertTrue((live / "strategy_selection_bias.csv").exists())
        self.assertEqual(file_sha256(source / "strategy_selection_bias_splits.csv"), file_sha256(live / "strategy_selection_bias_splits.csv"))
        self.assertTrue((live / "strategy_candidate_family.csv").exists())
        self.assertEqual(file_sha256(source / "strategy_candidate_similarity.csv"), file_sha256(live / "strategy_candidate_similarity.csv"))
        self.assertTrue((live / "strategy_family_selection.csv").exists())
        self.assertEqual(file_sha256(source / "strategy_family_selection_splits.csv"), file_sha256(live / "strategy_family_selection_splits.csv"))
        self.assertFalse((live / "orders.csv").exists())
        self.assertTrue((live / "run_manifest.csv").exists())
        summary = pd.read_csv(live / "live_strategy_evidence_sync_summary.csv")
        self.assertEqual(summary.loc[0, "status"], "ok")
        self.assertFalse(bool(summary.loc[0, "orders_copied"]))
        self.assertEqual(summary.loc[0, "backtest_config_sha256"], backtest_config_sha256(strategy))
        self.assertIn("live_strategy_evidence_sync.csv", paths)

    def test_sync_rejects_different_strategy_configuration(self):
        strategy, live_config, _, _ = self.fixture("live_evidence_strategy_mismatch")
        live_config["strategy"]["top_n"] = 30

        with self.assertRaisesRegex(RuntimeError, "backtest-relevant configurations differ"):
            sync_live_strategy_evidence(strategy, live_config)

    def test_sync_rejects_stale_source_manifest(self):
        strategy, live_config, _, _ = self.fixture("live_evidence_stale")

        with patch(
            "ashare_quant.live_evidence.readiness_evidence_issues",
            return_value=["prices_sha256 mismatch"],
        ):
            with self.assertRaisesRegex(RuntimeError, "source strategy evidence is not current"):
                sync_live_strategy_evidence(strategy, live_config)

    def test_sync_rejects_missing_required_evidence(self):
        strategy, live_config, source, _ = self.fixture("live_evidence_missing")
        (source / "cost_stress.csv").unlink()

        with patch("ashare_quant.live_evidence.readiness_evidence_issues", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "cost_stress.csv"):
                sync_live_strategy_evidence(strategy, live_config)


if __name__ == "__main__":
    unittest.main()
