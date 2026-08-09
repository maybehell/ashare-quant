import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.config import load_config
from ashare_quant.selected_candidate_refresh import refresh_selected_candidate_evidence


class SelectedCandidateRefreshTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / "selected_candidate_refresh" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.base_output = self.root / "base-output"
        self.base_output.mkdir()
        self.candidates = []
        for index in (1, 2):
            output = self.root / f"candidate-{index}-output"
            cache = self.root / f"candidate-{index}-cache"
            output.mkdir()
            cache.mkdir()
            (cache / "prices.parquet").write_bytes(f"cache-{index}".encode())
            manifest = self.root / f"candidate-{index}.json"
            config_path = self.root / f"candidate-{index}.yaml"
            config_path.write_text(
                json.dumps(
                    {
                        "paths": {
                            "output_dir": str(output),
                            "cache_dir": str(cache),
                            "state_db": str(self.root / f"candidate-{index}.sqlite3"),
                        },
                        "validation": {
                            "candidate_path": str(manifest),
                            "research_only": True,
                            "experiment_status": "preregistered_forward_only",
                        },
                        "strategy": {"name": f"candidate-{index}"},
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            candidate_id = f"candidate-{index}-id"
            manifest.write_text(
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "strategy_hash": live_strategy_hash(config),
                        "strategy_name": f"candidate-{index}",
                        "freeze_mode": "forward_only_preregistration",
                    }
                ),
                encoding="utf-8",
            )
            self.candidates.append((candidate_id, config_path, output))

        self.base_config = load_config()
        self.base_config["paths"]["output_dir"] = str(self.base_output)
        self.base_config["validation"]["forward_only_candidate_configs"] = [
            str(item[1]) for item in self.candidates
        ]
        self.base_config["validation"]["active_execution_path"] = str(
            self.root / "active-execution.json"
        )

    def write_selection(self, *, ready: bool) -> None:
        pd.DataFrame(
            [
                {
                    "status": "ready" if ready else "collecting_forward_data",
                    "selection_ready": ready,
                    "selected_candidate_id": self.candidates[0][0] if ready else "",
                }
            ]
        ).to_csv(
            self.base_output / "forward_candidate_selection_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )

    @patch("ashare_quant.selected_candidate_refresh.run_backtest")
    def test_waiting_selection_never_runs_expensive_refresh(self, run_backtest):
        self.write_selection(ready=False)

        paths = refresh_selected_candidate_evidence(self.base_config)
        audit = pd.read_csv(paths["selected_candidate_evidence_refresh.csv"]).iloc[0]

        self.assertEqual(audit["status"], "waiting_selection")
        self.assertFalse(bool(audit["refresh_attempted"]))
        run_backtest.assert_not_called()
        self.assertFalse((self.root / "active-execution.json").exists())

    @patch("ashare_quant.selected_candidate_refresh._forward_selection_winner_status", return_value=(True, "winner"))
    @patch("ashare_quant.selected_candidate_refresh.build_live_readiness_from_outputs")
    @patch("ashare_quant.selected_candidate_refresh.run_capital_sensitivity", return_value={})
    @patch("ashare_quant.selected_candidate_refresh.run_cost_stress", return_value=pd.DataFrame([{"scenario": "high_cost"}]))
    @patch("ashare_quant.selected_candidate_refresh.run_backtest", return_value={})
    @patch("ashare_quant.selected_candidate_refresh._candidate_strict_live_readiness_status")
    def test_selected_winner_refreshes_evidence_without_activation_or_orders(
        self,
        strict_status,
        run_backtest,
        run_cost_stress,
        run_capital,
        build_readiness,
        winner_status,
    ):
        self.write_selection(ready=True)
        strict_status.side_effect = [(False, "stale"), (True, "ready")]
        build_readiness.return_value = pd.DataFrame(
            [{"check": "candidate_exists", "passed": True, "value": self.candidates[0][0]}]
        )

        paths = refresh_selected_candidate_evidence(self.base_config)
        audit = pd.read_csv(paths["selected_candidate_evidence_refresh.csv"]).iloc[0]

        self.assertEqual(audit["status"], "refreshed_ready")
        self.assertTrue(bool(audit["strict_ready_after"]))
        self.assertTrue(bool(audit["protected_artifacts_unchanged"]))
        self.assertFalse(bool(audit["broker_submit_enabled"]))
        run_backtest.assert_called_once()
        run_cost_stress.assert_called_once()
        run_capital.assert_called_once()
        self.assertFalse((self.root / "active-execution.json").exists())
        self.assertFalse((self.candidates[0][2] / "orders.csv").exists())

    @patch("ashare_quant.selected_candidate_refresh._forward_selection_winner_status", return_value=(True, "winner"))
    @patch("ashare_quant.selected_candidate_refresh.build_live_readiness_from_outputs", return_value=pd.DataFrame())
    @patch("ashare_quant.selected_candidate_refresh.run_capital_sensitivity", return_value={})
    @patch("ashare_quant.selected_candidate_refresh.run_cost_stress", return_value=pd.DataFrame())
    @patch("ashare_quant.selected_candidate_refresh.run_backtest", return_value={})
    @patch("ashare_quant.selected_candidate_refresh._candidate_strict_live_readiness_status", return_value=(False, "blocked"))
    def test_unchanged_blocked_fingerprint_is_not_recomputed(
        self,
        strict_status,
        run_backtest,
        run_cost_stress,
        run_capital,
        build_readiness,
        winner_status,
    ):
        self.write_selection(ready=True)

        refresh_selected_candidate_evidence(self.base_config)
        paths = refresh_selected_candidate_evidence(self.base_config)
        audit = pd.read_csv(paths["selected_candidate_evidence_refresh.csv"]).iloc[0]

        self.assertEqual(audit["status"], "blocked_evidence_unchanged")
        self.assertFalse(bool(audit["refresh_attempted"]))
        run_backtest.assert_called_once()

    @patch("ashare_quant.selected_candidate_refresh._forward_selection_winner_status", return_value=(True, "winner"))
    @patch("ashare_quant.selected_candidate_refresh.build_live_readiness_from_outputs", return_value=pd.DataFrame())
    @patch("ashare_quant.selected_candidate_refresh.run_capital_sensitivity", return_value={})
    @patch("ashare_quant.selected_candidate_refresh.run_cost_stress", return_value=pd.DataFrame())
    @patch("ashare_quant.selected_candidate_refresh._candidate_strict_live_readiness_status", return_value=(False, "blocked"))
    def test_order_artifact_side_effect_fails_safety_audit(
        self,
        strict_status,
        run_cost_stress,
        run_capital,
        build_readiness,
        winner_status,
    ):
        self.write_selection(ready=True)

        def create_forbidden_order(_config):
            (self.candidates[0][2] / "orders.csv").write_text("forbidden", encoding="utf-8")
            return {}

        with patch("ashare_quant.selected_candidate_refresh.run_backtest", side_effect=create_forbidden_order):
            paths = refresh_selected_candidate_evidence(self.base_config)
        audit = pd.read_csv(paths["selected_candidate_evidence_refresh.csv"]).iloc[0]

        self.assertEqual(audit["status"], "safety_violation_recovered")
        self.assertFalse(bool(audit["protected_artifacts_unchanged"]))
        self.assertFalse(bool(audit["strict_ready_after"]))
        self.assertFalse((self.candidates[0][2] / "orders.csv").exists())

    @patch("ashare_quant.selected_candidate_refresh._forward_selection_winner_status", return_value=(True, "winner"))
    @patch("ashare_quant.selected_candidate_refresh.run_backtest")
    @patch("ashare_quant.selected_candidate_refresh._candidate_strict_live_readiness_status", return_value=(False, "blocked"))
    def test_preexisting_protected_artifact_refuses_refresh(
        self,
        strict_status,
        run_backtest,
        winner_status,
    ):
        self.write_selection(ready=True)
        active_path = self.root / "active-execution.json"
        active_path.write_text("existing", encoding="utf-8")

        paths = refresh_selected_candidate_evidence(self.base_config)
        audit = pd.read_csv(paths["selected_candidate_evidence_refresh.csv"]).iloc[0]

        self.assertEqual(audit["status"], "protected_artifacts_present")
        self.assertFalse(bool(audit["refresh_attempted"]))
        run_backtest.assert_not_called()
        self.assertEqual(active_path.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
