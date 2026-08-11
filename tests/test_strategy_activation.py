import json
import shutil
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from ashare_quant.audit import mapping_sha256, orders_frame_sha256
from ashare_quant.candidate import (
    candidate_benchmark_data_identity,
    forward_risk_evaluation_identity,
    live_strategy_hash,
    record_candidate_failure,
)
from ashare_quant.config import apply_active_execution, load_config
from ashare_quant.index_symbols import benchmark_index_identity
from ashare_quant.forward_selection import (
    candidate_set_sha256,
    write_forward_candidate_selection,
)
from ashare_quant.shadow_policy import shadow_evaluation_identity
from ashare_quant.rpa import validate_paper_order_source
from ashare_quant.strategy_activation import (
    activate_forward_candidate,
    active_execution_path,
    build_candidate_activation_audit,
    current_execution_epoch,
    deactivate_execution_candidate,
    ensure_execution_epoch,
    require_active_execution_consistency,
    write_active_execution_status,
    _forward_selection_winner_status,
)


class StrategyActivationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / "strategy_activation" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_output = self.root / "base-output"
        self.candidate_output = self.root / "candidate-output"
        self.base_output.mkdir()
        self.candidate_output.mkdir()
        self.candidate_path = self.root / "candidate.yaml"
        self.manifest_path = self.root / "candidate.json"
        self.activation_path = self.root / "active-execution.json"
        self.base_path = self.root / "base.yaml"

        candidate_override = {
            "paths": {"output_dir": str(self.candidate_output)},
            "validation": {
                "candidate_path": str(self.manifest_path),
                "research_only": True,
                "experiment_status": "preregistered_forward_only",
                "require_frozen_input_evidence": False,
            },
            "strategy": {
                "name": "validated-forward-candidate",
                "top_n": 50,
                "benchmark_membership": {"enabled": False},
            },
        }
        self.candidate_path.write_text(
            json.dumps(candidate_override),
            encoding="utf-8",
        )
        candidate_config = load_config(self.candidate_path)
        self.candidate_id = "candidate-activation-1"
        forward_risk_identity = forward_risk_evaluation_identity(candidate_config)
        self.manifest_path.write_text(
            json.dumps(
                {
                    "candidate_id": self.candidate_id,
                    "strategy_hash": live_strategy_hash(candidate_config),
                    "strategy_name": "validated-forward-candidate",
                    "freeze_mode": "forward_only_preregistration",
                    "freeze_boundary_schema_version": 2,
                    "freeze_known_data_end": "2024-01-01",
                    "freeze_price_end": "2024-01-01",
                    "required_forward_trading_days": 2,
                    "required_forward_excess_return": 0.0,
                    "min_early_forward_trading_days": 5,
                    "min_early_forward_excess_return": -0.05,
                    **benchmark_index_identity("000300"),
                    **candidate_benchmark_data_identity(
                        pd.DataFrame([{"date": "2024-01-01", "symbol": "000300"}]),
                        "2024-01-01",
                    ),
                    **shadow_evaluation_identity(candidate_config),
                    **forward_risk_identity,
                    "forward_risk_evaluation_sha256": mapping_sha256(forward_risk_identity),
                }
            ),
            encoding="utf-8",
        )
        self.write_shadow(days=2)

        base_override = {
            "paths": {"output_dir": str(self.base_output)},
            "validation": {
                "forward_only_candidate_configs": [str(self.candidate_path)],
                "active_execution_path": str(self.activation_path),
                "execution_epoch_path": str(self.root / "execution-epoch.json"),
            },
        }
        self.base_path.write_text(json.dumps(base_override), encoding="utf-8")
        self.base_config = load_config(self.base_path)
        self.readiness_patcher = patch(
            "ashare_quant.strategy_activation.build_live_readiness_from_outputs",
            return_value=pd.DataFrame(
                [
                    {"check": "candidate_exists", "passed": True, "value": self.candidate_id},
                    {"check": "strict_strategy_evidence", "passed": True, "value": "current"},
                ]
            ),
        )
        self.mock_readiness = self.readiness_patcher.start()
        self.addCleanup(self.readiness_patcher.stop)

    def write_shadow(self, *, days: int) -> None:
        pd.DataFrame(
            [
                {
                    "candidate_id": self.candidate_id,
                    "status": "ok",
                    "observed_trading_days": days,
                    "portfolio_return": 0.02,
                    "benchmark_return": 0.01,
                    "active_max_drawdown": 0.02,
                    "tracking_error": 0.10,
                    "information_ratio": 0.50,
                    "signal_date": "2024-01-01",
                    "execution_date": "2024-01-02",
                    "latest_date": "2024-01-04",
                }
            ]
        ).to_csv(
            self.candidate_output / "shadow_forward_audit.csv",
            index=False,
            encoding="utf-8-sig",
        )

    def test_current_validated_candidate_passes_activation_audit(self):
        audit = build_candidate_activation_audit(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
        ).set_index("check")

        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertEqual(
            audit.loc["candidate_validation_current", "value"],
            "status=validated;missing=;failed=",
        )

    def test_quarantined_candidate_cannot_recover_into_activation(self):
        candidate_config = load_config(self.candidate_path)
        record_candidate_failure(
            candidate_config,
            pd.DataFrame(
                [
                    {
                        "candidate_id": self.candidate_id,
                        "status": "early_forward_degradation",
                        "observed_forward_trading_days": 5,
                        "forward_excess_return": -0.06,
                    }
                ]
            ),
        )

        audit = build_candidate_activation_audit(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
        ).set_index("check")

        self.assertFalse(bool(audit.loc["candidate_validation_current", "passed"]))
        self.assertIn(
            "candidate_quarantine_clear",
            audit.loc["candidate_validation_current", "value"],
        )
        self.assertFalse(self.activation_path.exists())

    def test_strict_live_readiness_failure_blocks_candidate_activation(self):
        self.mock_readiness.return_value = pd.DataFrame(
            [
                {"check": "candidate_exists", "passed": True, "value": self.candidate_id},
                {"check": "cost_stress_excess_return", "passed": False, "value": -0.01},
            ]
        )

        audit = build_candidate_activation_audit(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
        ).set_index("check")

        self.assertFalse(bool(audit.loc["candidate_strict_live_readiness", "passed"]))
        self.assertIn("cost_stress_excess_return", audit.loc["candidate_strict_live_readiness", "value"])
    def make_multi_candidate_selection(self) -> tuple[dict, Path, str]:
        second_output = self.root / "candidate-output-2"
        second_output.mkdir()
        second_manifest = self.root / "candidate-2.json"
        second_path = self.root / "candidate-2.yaml"
        second_path.write_text(
            json.dumps(
                {
                    "paths": {"output_dir": str(second_output)},
                    "validation": {
                        "candidate_path": str(second_manifest),
                        "research_only": True,
                        "experiment_status": "preregistered_forward_only",
                    },
                    "strategy": {"name": "second-forward-candidate", "top_n": 60},
                }
            ),
            encoding="utf-8",
        )
        second_config = load_config(second_path)
        second_id = "candidate-activation-2"
        second_manifest.write_text(
            json.dumps(
                {
                    "candidate_id": second_id,
                    "strategy_hash": live_strategy_hash(second_config),
                    "strategy_name": "second-forward-candidate",
                    "freeze_mode": "forward_only_preregistration",
                }
            ),
            encoding="utf-8",
        )
        multi_base = json.loads(json.dumps(self.base_config))
        policy_path = self.root / "selection-policy.json"
        multi_base["validation"]["forward_only_candidate_configs"] = [
            str(self.candidate_path),
            str(second_path),
        ]
        multi_base["validation"]["forward_selection_policy_path"] = str(policy_path)
        first_config = load_config(self.candidate_path)
        records = sorted(
            [
                {
                    "candidate_id": self.candidate_id,
                    "candidate_name": "validated-forward-candidate",
                    "candidate_config": str(self.candidate_path.resolve()).replace(chr(92), "/"),
                    "candidate_output_dir": str(self.candidate_output.resolve()),
                    "strategy_hash": live_strategy_hash(first_config),
                },
                {
                    "candidate_id": second_id,
                    "candidate_name": "second-forward-candidate",
                    "candidate_config": str(second_path.resolve()).replace(chr(92), "/"),
                    "candidate_output_dir": str(second_output.resolve()),
                    "strategy_hash": live_strategy_hash(second_config),
                },
            ],
            key=lambda item: item["candidate_id"],
        )
        observation = pd.DataFrame(records).assign(
            required_forward_trading_days=60,
            max_observed_trading_days=61,
        )

        def write_curves(periods: int) -> None:
            dates = pd.bdate_range("2026-01-02", periods=periods)
            for record in records:
                daily = 0.01 if record["candidate_id"] == self.candidate_id else 0.0
                portfolio_returns = [0.0] + [daily] * (periods - 1)
                benchmark_returns = [0.0] + [0.001] * (periods - 1)
                pd.DataFrame(
                    {
                        "date": dates.date.astype(str),
                        "candidate_id": record["candidate_id"],
                        "strategy_hash": record["strategy_hash"],
                        "portfolio_nav": pd.Series(portfolio_returns).add(1.0).cumprod(),
                        "benchmark_nav": pd.Series(benchmark_returns).add(1.0).cumprod(),
                    }
                ).to_csv(
                    Path(record["candidate_output_dir"]) / "shadow_continuous_nav.csv",
                    index=False,
                )

        write_curves(1)
        write_forward_candidate_selection(multi_base, observation)
        write_curves(61)
        write_forward_candidate_selection(multi_base, observation)
        return multi_base, second_path.resolve(), second_id

    def test_multi_candidate_activation_requires_frozen_selected_winner(self):
        multi_base, second_path, second_id = self.make_multi_candidate_selection()
        registered = [self.candidate_path.resolve(), second_path]

        winner_ok, winner_value = _forward_selection_winner_status(
            multi_base, registered, self.candidate_id
        )
        loser_ok, _ = _forward_selection_winner_status(
            multi_base, registered, second_id
        )

        self.assertTrue(winner_ok, winner_value)
        self.assertFalse(loser_ok)

    def test_multi_candidate_activation_rejects_summary_winner_not_supported_by_pairs(self):
        multi_base, second_path, second_id = self.make_multi_candidate_selection()
        summary_path = self.base_output / "forward_candidate_selection_summary.csv"
        summary = pd.read_csv(summary_path)
        summary.loc[0, "selected_candidate_id"] = second_id
        summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

        passed, value = _forward_selection_winner_status(
            multi_base,
            [self.candidate_path.resolve(), second_path],
            second_id,
        )

        self.assertFalse(passed)
        self.assertIn(f"winner={self.candidate_id}", value)
    def test_multi_candidate_activation_rejects_tampered_candidate_set_hash(self):
        multi_base, second_path, _ = self.make_multi_candidate_selection()
        summary_path = self.base_output / "forward_candidate_selection_summary.csv"
        summary = pd.read_csv(summary_path)
        summary.loc[0, "candidate_set_sha256"] = "tampered"
        summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

        passed, value = _forward_selection_winner_status(
            multi_base,
            [self.candidate_path.resolve(), second_path],
            self.candidate_id,
        )

        self.assertFalse(passed)
        self.assertIn("set_match=False", value)
    def test_multi_candidate_activation_rejects_tampered_curve_ledger_hash(self):
        multi_base, second_path, _ = self.make_multi_candidate_selection()
        summary_path = self.base_output / "forward_candidate_selection_summary.csv"
        summary = pd.read_csv(summary_path)
        summary.loc[0, "curve_ledger_sha256"] = "tampered"
        summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

        passed, value = _forward_selection_winner_status(
            multi_base,
            [self.candidate_path.resolve(), second_path],
            self.candidate_id,
        )

        self.assertFalse(passed)
        self.assertIn("hash_match=False", value)

    def test_multi_candidate_activation_rejects_changed_selection_threshold(self):
        multi_base, second_path, _ = self.make_multi_candidate_selection()
        multi_base["validation"]["forward_selection"]["min_superiority_probability"] = 0.80

        passed, value = _forward_selection_winner_status(
            multi_base,
            [self.candidate_path.resolve(), second_path],
            self.candidate_id,
        )

        self.assertFalse(passed)
        self.assertIn("policy=False", value)

    def test_incomplete_forward_window_cannot_create_activation_record(self):
        self.write_shadow(days=1)

        with self.assertRaisesRegex(RuntimeError, "candidate_validation_current"):
            activate_forward_candidate(
                self.base_config,
                self.candidate_path,
                expected_candidate_id=self.candidate_id,
                confirm=True,
            )

        self.assertFalse(self.activation_path.exists())
        audit = pd.read_csv(self.base_output / "candidate_activation_audit.csv").set_index("check")
        self.assertFalse(bool(audit.loc["candidate_validation_current", "passed"]))

    def test_activation_atomically_selects_behavior_without_operational_paths(self):
        paths = activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )

        self.assertEqual(paths["active_execution_strategy.json"], self.activation_path)
        payload = json.loads(self.activation_path.read_text(encoding="utf-8"))
        self.assertFalse(payload["broker_submit_enabled"])
        activated = apply_active_execution(self.base_config)
        candidate = load_config(self.candidate_path)
        self.assertEqual(live_strategy_hash(activated), live_strategy_hash(candidate))
        self.assertEqual(activated["paths"]["output_dir"], str(self.base_output))
        self.assertEqual(
            activated["_active_execution"]["candidate_id"],
            self.candidate_id,
        )
        self.assertFalse((self.base_output / "orders.csv").exists())
        self.assertFalse((self.base_output / "rpa_orders.csv").exists())

    def test_tampered_activation_override_fails_closed(self):
        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        payload = json.loads(self.activation_path.read_text(encoding="utf-8"))
        payload["execution_override"]["strategy"]["top_n"] = 99
        self.activation_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "hash does not match"):
            apply_active_execution(self.base_config)

    def test_non_mapping_activation_record_fails_closed(self):
        self.activation_path.write_text("[]", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "must be a mapping"):
            apply_active_execution(self.base_config)

        self.assertFalse((self.base_output / "orders.csv").exists())

    def test_exact_candidate_id_is_required_for_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "current audit"):
            activate_forward_candidate(
                self.base_config,
                self.candidate_path,
                expected_candidate_id="wrong-candidate",
                confirm=True,
            )

        self.assertFalse(active_execution_path(self.base_config).exists())

    def test_active_candidate_passes_runtime_identity_preflight(self):
        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        activated = apply_active_execution(self.base_config)

        status_path = require_active_execution_consistency(activated)

        status = pd.read_csv(status_path).set_index("check")
        self.assertTrue(status["passed"].astype(bool).all())
        self.assertEqual(
            status.loc["active_loader_identity", "expected"],
            f"candidate={self.candidate_id};hash={live_strategy_hash(activated)[:16]}",
        )

    def test_explicit_config_cannot_bypass_active_candidate(self):
        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )

        with self.assertRaisesRegex(RuntimeError, "active_loader_identity"):
            require_active_execution_consistency(self.base_config)

        status = pd.read_csv(self.base_output / "active_execution_status.csv").set_index("check")
        self.assertFalse(bool(status.loc["active_loader_identity", "passed"]))
        self.assertFalse((self.base_output / "rpa_orders.csv").exists())

    def test_deactivation_without_confirmation_only_writes_audit(self):
        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        activated = apply_active_execution(self.base_config)

        paths = deactivate_execution_candidate(
            activated,
            expected_candidate_id=self.candidate_id,
            confirm=False,
        )

        self.assertTrue(self.activation_path.exists())
        self.assertIn("candidate_deactivation_audit.csv", paths)
        audit = pd.read_csv(paths["candidate_deactivation_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())

    def test_deactivation_requires_exact_active_candidate_id(self):
        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        activated = apply_active_execution(self.base_config)

        with self.assertRaisesRegex(RuntimeError, "expected_candidate_id"):
            deactivate_execution_candidate(
                activated,
                expected_candidate_id="wrong-candidate",
                confirm=True,
            )

        self.assertTrue(self.activation_path.exists())

    def test_confirmed_deactivation_archives_record_and_restores_default(self):
        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        activated = apply_active_execution(self.base_config)

        paths = deactivate_execution_candidate(
            activated,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )

        self.assertFalse(self.activation_path.exists())
        archive_path = paths["archived_execution_strategy.json"]
        self.assertTrue(archive_path.exists())
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
        self.assertEqual(archive["event"], "candidate_deactivated")
        self.assertEqual(archive["candidate_id"], self.candidate_id)
        self.assertEqual(archive["active_record"]["candidate_id"], self.candidate_id)
        self.assertFalse(archive["broker_submit_enabled"])
        restored = apply_active_execution(self.base_config)
        self.assertNotIn("_active_execution", restored)
        self.assertNotEqual(live_strategy_hash(restored), live_strategy_hash(activated))
        self.assertFalse((self.base_output / "rpa_orders.csv").exists())

    def test_default_runtime_status_is_safe_when_no_candidate_is_active(self):
        path = write_active_execution_status(self.base_config)

        status = pd.read_csv(path)
        self.assertTrue(status["passed"].astype(bool).all())
        self.assertEqual(status.iloc[0]["value"], "absent")

    def test_activation_and_rollback_permanently_invalidate_old_default_orders(self):
        old_epoch = ensure_execution_epoch(self.base_config)
        orders = pd.DataFrame(
            [
                {
                    "date": "2024-01-05",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "reason": "rebalance",
                }
            ]
        )
        orders_path = self.base_output / "orders.csv"
        orders.to_csv(orders_path, index=False)
        provenance = {
            "source": "paper",
            "allow_weak_strategy": False,
            "strategy_name": str(self.base_config.get("strategy", {}).get("name", "")),
            "strategy_hash": live_strategy_hash(self.base_config),
            "config_sha256": mapping_sha256(self.base_config),
            "execution_date": "2024-01-05",
            "order_count": 1,
            "orders_sha256": orders_frame_sha256(orders),
            **old_epoch,
        }
        pd.DataFrame(
            [{"item": key, "value": value} for key, value in provenance.items()]
        ).to_csv(self.base_output / "orders_provenance.csv", index=False)

        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        activated = apply_active_execution(self.base_config)
        active_epoch = current_execution_epoch(activated)
        self.assertNotEqual(active_epoch["execution_epoch_id"], old_epoch["execution_epoch_id"])
        deactivate_execution_candidate(
            activated,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        restored = apply_active_execution(self.base_config)
        restored_epoch = current_execution_epoch(restored)
        self.assertNotEqual(restored_epoch["execution_epoch_id"], old_epoch["execution_epoch_id"])
        self.assertEqual(
            mapping_sha256(restored),
            mapping_sha256(self.base_config),
            "The regression must prove epoch invalidation even after config identity returns.",
        )

        with self.assertRaisesRegex(RuntimeError, "执行策略纪元已失效"):
            validate_paper_order_source(restored, orders_path)

    def test_controlled_live_config_applies_active_strategy_without_replacing_paths(self):
        activate_forward_candidate(
            self.base_config,
            self.candidate_path,
            expected_candidate_id=self.candidate_id,
            confirm=True,
        )
        payload = json.loads(self.activation_path.read_text(encoding="utf-8"))
        live_output = self.root / "live-output"
        live_path = self.root / "live.yaml"
        live_path.write_text(
            json.dumps(
                {
                    "paths": {
                        "output_dir": str(live_output),
                        "state_db": str(self.root / "live.sqlite3"),
                    },
                    "validation": {
                        "active_execution_path": str(self.activation_path),
                        "apply_active_execution": True,
                    },
                    "auto_live": {
                        "emergency_stop": True,
                        "allow_broker_submit": False,
                    },
                }
            ),
            encoding="utf-8",
        )

        live_config = load_config(live_path)

        self.assertEqual(live_config["paths"]["output_dir"], str(live_output))
        self.assertEqual(live_strategy_hash(live_config), payload["strategy_hash"])
        self.assertEqual(live_config["_active_execution"]["candidate_id"], self.candidate_id)
        self.assertEqual(live_config["_active_execution"]["activation_id"], payload["activation_id"])
        self.assertEqual(
            current_execution_epoch(live_config)["execution_epoch_id"],
            payload["activation_id"],
        )
        self.assertFalse(live_config["auto_live"]["allow_broker_submit"])


if __name__ == "__main__":
    unittest.main()
