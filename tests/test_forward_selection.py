import json
from pathlib import Path
import shutil
import unittest

import numpy as np
import pandas as pd

from ashare_quant.audit import mapping_sha256
from ashare_quant.forward_selection import (
    expand_forward_selection_policy_before_evidence,
    upgrade_forward_selection_policy_before_evidence,
    write_forward_candidate_selection,
)


class ForwardSelectionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / "forward_selection" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.base_output = self.root / "base-output"
        self.policy_path = self.root / "selection-policy.json"
        self.config = {
            "paths": {"output_dir": str(self.base_output)},
            "validation": {
                "forward_selection_policy_path": str(self.policy_path),
                "forward_selection": {
                    "min_common_trading_days": 20,
                    "block_days": 5,
                    "bootstrap_samples": 500,
                    "bootstrap_seed": 17,
                    "min_superiority_probability": 0.80,
                    "min_cumulative_advantage": 0.0,
                },
            },
        }
        self.records = [
            self.make_candidate("candidate-a", "strategy-a", "alpha"),
            self.make_candidate("candidate-b", "strategy-b", "beta"),
        ]
        self.observation = pd.DataFrame(self.records)

    def make_candidate(self, candidate_id: str, strategy_hash: str, name: str) -> dict:
        output = self.root / f"output-{candidate_id}"
        output.mkdir(parents=True)
        manifest = self.root / f"manifest-{candidate_id}.json"
        manifest.write_text(
            json.dumps({"candidate_id": candidate_id, "strategy_hash": strategy_hash, "freeze_price_end": "2026-01-08"}),
            encoding="utf-8",
        )
        config_path = self.root / f"config-{candidate_id}.json"
        config_path.write_text(
            json.dumps(
                {
                    "paths": {
                        "output_dir": str(output),
                        "cache_dir": str(self.root / f"cache-{candidate_id}"),
                        "state_db": str(self.root / f"state-{candidate_id}.sqlite3"),
                    },
                    "validation": {
                        "candidate_path": str(manifest),
                        "research_only": True,
                        "experiment_status": "preregistered_forward_only",
                    },
                    "strategy": {
                        "name": name,
                        "top_n": 50 if candidate_id == "candidate-a" else 100,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.write_curve(output, candidate_id, strategy_hash, future=False)
        return {
            "status": "observed",
            "candidate_id": candidate_id,
            "candidate_name": name,
            "candidate_config": str(config_path),
            "candidate_output_dir": str(output),
            "required_forward_trading_days": 20,
            "max_observed_trading_days": 4,
        }

    def write_curve(
        self,
        output: Path,
        candidate_id: str,
        strategy_hash: str,
        *,
        future: bool,
        benchmark_shift: float = 0.0,
    ) -> None:
        periods = 24 if future else 4
        dates = pd.bdate_range("2026-01-05", periods=periods)
        candidate_return = 0.01 if candidate_id == "candidate-a" else 0.0
        candidate_returns = np.full(periods, candidate_return)
        candidate_returns[0] = 0.0
        benchmark_returns = np.full(periods, 0.001 + benchmark_shift)
        benchmark_returns[0] = 0.0
        frame = pd.DataFrame(
            {
                "date": dates.date.astype(str),
                "candidate_id": candidate_id,
                "strategy_hash": strategy_hash,
                "portfolio_nav": np.cumprod(1.0 + candidate_returns),
                "benchmark_nav": np.cumprod(1.0 + benchmark_returns),
            }
        )
        frame.to_csv(output / "shadow_continuous_nav.csv", index=False)

    def test_policy_freezes_before_pairwise_evidence_and_later_selects_dominant_candidate(self):
        first = write_forward_candidate_selection(self.config, self.observation)
        first_summary = pd.read_csv(first["forward_candidate_selection_summary.csv"]).iloc[0]
        frozen_policy = self.policy_path.read_bytes()

        self.assertEqual(first_summary["status"], "collecting_forward_data")
        self.assertEqual(int(first_summary["minimum_observed_forward_trading_days"]), 4)
        self.assertEqual(int(first_summary["minimum_common_trading_days"]), 0)
        self.assertFalse(bool(first_summary["selection_ready"]))

        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        second = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(second["forward_candidate_selection_summary.csv"]).iloc[0]
        pairs = pd.read_csv(second["forward_candidate_selection.csv"])

        self.assertEqual(self.policy_path.read_bytes(), frozen_policy)
        self.assertEqual(summary["status"], "ready")
        self.assertTrue(bool(summary["selection_ready"]))
        self.assertEqual(summary["selected_candidate_id"], "candidate-a")
        self.assertEqual(int(summary["minimum_observed_forward_trading_days"]), 4)
        self.assertEqual(int(summary["minimum_common_trading_days"]), 20)
        self.assertEqual(pairs.loc[0, "status"], "dominance_ready")
        self.assertEqual(pairs.loc[0, "favored_candidate_id"], "candidate-a")
        self.assertTrue(bool(summary["curve_ledger_valid"]))
        self.assertEqual(int(summary["minimum_curve_ledger_rows"]), 20)
        ledger = json.loads(second["forward_candidate_curve_ledger.json"].read_text(encoding="utf-8"))
        self.assertEqual(
            {int(row["post_policy_rows"]) for row in ledger["candidates"].values()},
            {20},
        )

    def test_fixed_horizon_result_does_not_change_after_more_observations(self):
        write_forward_candidate_selection(self.config, self.observation)
        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        first_paths = write_forward_candidate_selection(self.config, self.observation)
        first_pair = pd.read_csv(first_paths["forward_candidate_selection.csv"]).iloc[0]

        extra_dates = pd.bdate_range("2026-02-06", periods=10)
        for record in self.records:
            curve_path = Path(record["candidate_output_dir"]) / "shadow_continuous_nav.csv"
            prior = pd.read_csv(curve_path)
            candidate_daily = -0.20 if record["candidate_id"] == "candidate-a" else 0.20
            extra = pd.DataFrame(
                {
                    "date": extra_dates.date.astype(str),
                    "candidate_id": record["candidate_id"],
                    "strategy_hash": "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                    "portfolio_nav": float(prior.iloc[-1]["portfolio_nav"])
                    * np.cumprod(np.full(10, 1.0 + candidate_daily)),
                    "benchmark_nav": float(prior.iloc[-1]["benchmark_nav"])
                    * np.cumprod(np.full(10, 1.001)),
                }
            )
            extra.to_csv(curve_path, mode="a", header=False, index=False)

        second_paths = write_forward_candidate_selection(self.config, self.observation)
        second_summary = pd.read_csv(second_paths["forward_candidate_selection_summary.csv"]).iloc[0]
        second_pair = pd.read_csv(second_paths["forward_candidate_selection.csv"]).iloc[0]

        self.assertEqual(second_summary["status"], "ready")
        self.assertEqual(second_summary["selected_candidate_id"], "candidate-a")
        self.assertEqual(int(second_pair["common_trading_days"]), 30)
        self.assertEqual(int(second_pair["evaluated_trading_days"]), 20)
        self.assertAlmostEqual(second_pair["candidate_a_return"], first_pair["candidate_a_return"])
        self.assertAlmostEqual(
            second_pair["probability_candidate_a_superior"],
            first_pair["probability_candidate_a_superior"],
        )

    def test_schema_upgrade_is_allowed_only_before_forward_evidence(self):
        paths = write_forward_candidate_selection(self.config, self.observation)
        policy_path = paths["forward_candidate_selection_policy.json"]
        ledger_path = paths["forward_candidate_curve_ledger.json"]
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        old_settings = dict(policy["settings"])
        old_settings["schema_version"] = 1
        old_settings.pop("evaluation_mode")
        old_hash = mapping_sha256(old_settings)
        policy["schema_version"] = 1
        policy["settings"] = old_settings
        policy["policy_sha256"] = old_hash
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["policy_sha256"] = old_hash
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

        upgraded_paths = upgrade_forward_selection_policy_before_evidence(
            self.config,
            self.observation,
        )
        upgraded = json.loads(policy_path.read_text(encoding="utf-8"))

        self.assertEqual(upgraded["schema_version"], 2)
        self.assertEqual(upgraded["settings"]["evaluation_mode"], "fixed_horizon")
        self.assertTrue(upgraded_paths["forward_candidate_selection_policy_archive.json"].exists())
        self.assertTrue(upgraded_paths["forward_candidate_curve_ledger_archive.json"].exists())

        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        upgraded["schema_version"] = 1
        upgraded["settings"] = old_settings
        upgraded["policy_sha256"] = old_hash
        policy_path.write_text(json.dumps(upgraded), encoding="utf-8")
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["policy_sha256"] = old_hash
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Post-policy return evidence exists"):
            upgrade_forward_selection_policy_before_evidence(self.config, self.observation)

    def test_benchmark_mismatch_fails_closed_after_policy_freeze(self):
        write_forward_candidate_selection(self.config, self.observation)
        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
                benchmark_shift=0.0001 if record["candidate_id"] == "candidate-b" else 0.0,
            )

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]
        pairs = pd.read_csv(paths["forward_candidate_selection.csv"])

        self.assertEqual(summary["status"], "invalid_evidence")
        self.assertFalse(bool(summary["selection_ready"]))
        self.assertEqual(pairs.loc[0, "status"], "invalid_evidence")

    def test_asymmetric_post_policy_date_gap_fails_closed(self):
        write_forward_candidate_selection(self.config, self.observation)
        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        second_curve = Path(self.records[1]["candidate_output_dir"]) / "shadow_continuous_nav.csv"
        frame = pd.read_csv(second_curve)
        frame.drop(index=frame.index[-3]).to_csv(second_curve, index=False)

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]
        pairs = pd.read_csv(paths["forward_candidate_selection.csv"])

        self.assertEqual(summary["status"], "invalid_evidence")
        self.assertFalse(bool(summary["selection_ready"]))
        self.assertEqual(pairs.loc[0, "status"], "invalid_evidence")
        self.assertIn("identical post-policy dates", pairs.loc[0, "message"])

    def test_non_finite_nav_fails_closed_without_crashing(self):
        write_forward_candidate_selection(self.config, self.observation)
        curve_path = Path(self.records[1]["candidate_output_dir"]) / "shadow_continuous_nav.csv"
        frame = pd.read_csv(curve_path)
        frame.loc[frame.index[-1], "portfolio_nav"] = np.inf
        frame.to_csv(curve_path, index=False)

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]
        pairs = pd.read_csv(paths["forward_candidate_selection.csv"])

        self.assertEqual(summary["status"], "invalid_evidence")
        self.assertFalse(bool(summary["selection_ready"]))
        self.assertIn("curve_nav_non_finite", summary["message"])
        self.assertTrue(pairs.empty)

    def test_curve_ledger_rejects_historical_rewrite(self):
        write_forward_candidate_selection(self.config, self.observation)
        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        accepted = write_forward_candidate_selection(self.config, self.observation)
        ledger_path = accepted["forward_candidate_curve_ledger.json"]
        frozen_ledger = ledger_path.read_bytes()
        curve_path = Path(self.records[0]["candidate_output_dir"]) / "shadow_continuous_nav.csv"
        frame = pd.read_csv(curve_path)
        frame.loc[frame.index[-4], "portfolio_nav"] *= 0.99
        frame.to_csv(curve_path, index=False)

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]

        self.assertEqual(summary["status"], "invalid_evidence")
        self.assertFalse(bool(summary["curve_ledger_valid"]))
        self.assertIn("curve_ledger_history_rewrite", summary["message"])
        self.assertEqual(ledger_path.read_bytes(), frozen_ledger)

    def test_curve_ledger_rejects_history_rollback(self):
        write_forward_candidate_selection(self.config, self.observation)
        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        accepted = write_forward_candidate_selection(self.config, self.observation)
        ledger_path = accepted["forward_candidate_curve_ledger.json"]
        frozen_ledger = ledger_path.read_bytes()
        for record in self.records:
            curve_path = Path(record["candidate_output_dir"]) / "shadow_continuous_nav.csv"
            pd.read_csv(curve_path).iloc[:-1].to_csv(curve_path, index=False)

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]

        self.assertEqual(summary["status"], "invalid_evidence")
        self.assertIn("curve_ledger_history_rollback", summary["message"])
        self.assertEqual(ledger_path.read_bytes(), frozen_ledger)

    def test_curve_ledger_cannot_be_recreated_after_evidence(self):
        write_forward_candidate_selection(self.config, self.observation)
        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        accepted = write_forward_candidate_selection(self.config, self.observation)
        ledger_path = accepted["forward_candidate_curve_ledger.json"]
        ledger_path.unlink()

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]

        self.assertEqual(summary["status"], "invalid_evidence")
        self.assertIn("curve_ledger_missing_after_evidence", summary["message"])
        self.assertFalse(ledger_path.exists())


    def test_deleted_policy_fails_closed_instead_of_resetting_evidence_window(self):
        write_forward_candidate_selection(self.config, self.observation)
        self.policy_path.unlink()

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]

        self.assertEqual(summary["status"], "invalid_evidence")
        self.assertFalse(bool(summary["policy_valid"]))
        self.assertFalse(self.policy_path.exists())
        self.assertIn("missing_after_initialization", summary["message"])
    def test_candidate_set_change_does_not_rewrite_frozen_policy(self):
        write_forward_candidate_selection(self.config, self.observation)
        frozen_policy = self.policy_path.read_bytes()
        changed = self.observation.copy()
        changed.loc[0, "candidate_id"] = "candidate-replaced"

        paths = write_forward_candidate_selection(self.config, changed)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]

        self.assertEqual(summary["status"], "invalid_candidate_set")
        self.assertFalse(bool(summary["policy_valid"]))
        self.assertEqual(self.policy_path.read_bytes(), frozen_policy)


    def test_policy_can_only_expand_before_post_policy_evidence(self):
        write_forward_candidate_selection(self.config, self.observation)
        old_policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        third = self.make_candidate("candidate-c", "strategy-c", "gamma")
        third_curve = Path(third["candidate_output_dir"]) / "shadow_continuous_nav.csv"
        pd.read_csv(third_curve).iloc[0:0].to_csv(third_curve, index=False)
        expanded_observation = pd.concat([self.observation, pd.DataFrame([third])], ignore_index=True)

        paths = expand_forward_selection_policy_before_evidence(self.config, expanded_observation)
        expanded = json.loads(self.policy_path.read_text(encoding="utf-8"))
        archived = json.loads(paths["forward_candidate_selection_policy_archive.json"].read_text(encoding="utf-8"))

        self.assertEqual(len(expanded["candidates"]), 3)
        self.assertEqual(archived, old_policy)
        self.assertEqual(expanded["evidence_after"], old_policy["evidence_after"])
        self.assertEqual(expanded["expanded_from_candidate_set_sha256"], old_policy["candidate_set_sha256"])

        for record in self.records:
            self.write_curve(
                Path(record["candidate_output_dir"]),
                record["candidate_id"],
                "strategy-a" if record["candidate_id"] == "candidate-a" else "strategy-b",
                future=True,
            )
        fourth = self.make_candidate("candidate-d", "strategy-d", "delta")
        second_expansion = pd.concat([expanded_observation, pd.DataFrame([fourth])], ignore_index=True)
        with self.assertRaisesRegex(RuntimeError, "Post-policy return evidence"):
            expand_forward_selection_policy_before_evidence(self.config, second_expansion)

    def test_expanded_candidate_with_header_only_curve_collects_without_invalid_evidence(self):
        write_forward_candidate_selection(self.config, self.observation)
        third = self.make_candidate("candidate-c", "strategy-c", "gamma")
        third_curve = Path(third["candidate_output_dir"]) / "shadow_continuous_nav.csv"
        pd.read_csv(third_curve).iloc[0:0].to_csv(third_curve, index=False)
        expanded_observation = pd.concat([self.observation, pd.DataFrame([third])], ignore_index=True)

        expand_forward_selection_policy_before_evidence(self.config, expanded_observation)
        paths = write_forward_candidate_selection(self.config, expanded_observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]
        pairs = pd.read_csv(paths["forward_candidate_selection.csv"])

        self.assertEqual(summary["status"], "collecting_forward_data")
        self.assertTrue(bool(summary["policy_valid"]))
        self.assertEqual(int(summary["minimum_common_trading_days"]), 0)
        self.assertEqual(len(pairs), 3)
        self.assertTrue((pairs["status"] == "collecting_forward_data").all())
    def test_structurally_redundant_candidates_cannot_be_selected(self):
        second_config = Path(self.records[1]["candidate_config"])
        payload = json.loads(second_config.read_text(encoding="utf-8"))
        payload["strategy"]["top_n"] = 50
        second_config.write_text(json.dumps(payload), encoding="utf-8")

        paths = write_forward_candidate_selection(self.config, self.observation)
        summary = pd.read_csv(paths["forward_candidate_selection_summary.csv"]).iloc[0]
        families = pd.read_csv(paths["forward_candidate_structural_families.csv"])

        self.assertEqual(summary["status"], "structurally_redundant_candidate_set")
        self.assertFalse(bool(summary["selection_ready"]))
        self.assertFalse(bool(summary["structural_diversity_ready"]))
        self.assertEqual(int(summary["structural_family_count"]), 1)
        self.assertEqual(families["structural_family_sha256"].nunique(), 1)

if __name__ == "__main__":
    unittest.main()
