import json
from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.audit import mapping_sha256
from ashare_quant.autolive import auto_live_config_sha256
from ashare_quant.candidate import forward_risk_evaluation_identity, live_strategy_hash
from ashare_quant.config import load_config
from ashare_quant.deployment_gate import (
    _broker_snapshot_freshness,
    _execution_strategy_identity,
    _forward_candidate_selection_evidence,
    _pending_attribution_timing_evidence,
    _strict_readiness_observation_rows,
    forward_candidate_cache_isolation,
    write_deployment_gate,
)
from ashare_quant.forward_selection import (
    candidate_set_records,
    candidate_set_sha256,
    write_forward_candidate_selection,
)
from ashare_quant.shadow_policy import shadow_evaluation_identity


class DeploymentGateTests(unittest.TestCase):
    def test_invalidated_snapshot_never_passes_freshness(self):
        frame = pd.DataFrame(
            [{
                "snapshot_run_id": "invalid-20260801120000",
                "broker": "huatai",
                "account_id": "",
                "environment": "unknown",
                "cash": -1.0,
                "available_cash": -1.0,
                "market_value": -1.0,
                "total_asset": -1.0,
                "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
            }]
        )
        passed, value = _broker_snapshot_freshness(frame, 300)
        self.assertFalse(passed)
        self.assertIn("snapshot_run_id_invalid", value)
        self.assertIn("total_asset_invalid", value)
    def test_strict_readiness_evaluates_only_frozen_winner_after_selection(self):
        rows = [
            pd.Series({"candidate_id": "candidate-a"}),
            pd.Series({"candidate_id": "candidate-b"}),
        ]
        selection = pd.Series({"selected_candidate_id": "candidate-b"})

        selected = _strict_readiness_observation_rows(rows, selection, True)
        collecting = _strict_readiness_observation_rows(rows, selection, False)

        self.assertEqual([row["candidate_id"] for row in selected], ["candidate-b"])
        self.assertEqual([row["candidate_id"] for row in collecting], ["candidate-a", "candidate-b"])
    def test_pending_attribution_timing_requires_bound_close_boundary(self):
        valid = pd.Series({
            "pending_signal_count": 2,
            "next_pending_execution_date": "2026-07-28",
            "first_attribution_requires_close_after": "2026-07-28",
        })
        missing = valid.drop(labels=["first_attribution_requires_close_after"])
        mismatch = valid.copy()
        mismatch["first_attribution_requires_close_after"] = "2026-07-29"

        self.assertTrue(_pending_attribution_timing_evidence(valid)[0])
        self.assertFalse(_pending_attribution_timing_evidence(missing)[0])
        self.assertFalse(_pending_attribution_timing_evidence(mismatch)[0])
        self.assertEqual(
            _pending_attribution_timing_evidence(pd.Series({"pending_signal_count": 0})),
            (True, "no_pending_signals"),
        )


    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / "deployment_gate" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.strategy = self.root / "strategy"
        self.live = self.root / "live"
        self.strategy.mkdir(parents=True, exist_ok=True)
        self.live.mkdir(parents=True, exist_ok=True)
        self.candidate_output = self.root / "candidate-output"
        self.candidate_output.mkdir(parents=True, exist_ok=True)
        self.candidate_config = self.root / "candidate.yaml"
        self.candidate_manifest = self.root / "candidate-manifest.json"
        candidate_definition = {
            "paths": {"output_dir": str(self.candidate_output), "cache_dir": str(self.root / "candidate-cache")},
            "validation": {
                "candidate_path": str(self.candidate_manifest),
                "research_only": True,
                "experiment_status": "preregistered_forward_only",
            },
            "strategy": {"name": "candidate-strategy"},
        }
        self.candidate_config.write_text(
            json.dumps(candidate_definition),
            encoding="utf-8",
        )
        candidate_config = load_config(self.candidate_config)
        self.candidate_manifest.write_text(
            json.dumps({
                "candidate_id": "candidate-1",
                "strategy_hash": live_strategy_hash(candidate_config),
                "strategy_name": "candidate-strategy",
                "freeze_mode": "forward_only_preregistration",
                "promotion_eligible_at_freeze": False,
            }),
            encoding="utf-8",
        )
        self.live_db = self.root / "isolated_live.sqlite3"
        self.hmac_env = "TEST_DEPLOYMENT_GATE_HMAC_KEY"
        os.environ[self.hmac_env] = "h" * 64
        self.bridge_path = self.root / "bridge.json"
        self.certification_path = self.live / "arming.json"
        self.bridge_path.write_text(
            json.dumps({"huatai": {
                "live_backend": "desktop_canary",
                "desktop_canary": {
                    "enabled": True,
                    "allow_submit": True,
                    "max_orders": 1,
                    "max_order_amount": 1000,
                    "certification_path": str(self.certification_path),
                    "required_certification_stage": "armed",
                    "require_hmac": True,
                    "hmac_key_env": self.hmac_env,
                },
            }}),
            encoding="utf-8",
        )
        self.canary_evidence = {
            name: str(self.live / f"{name}.json")
            for name in (
                "buy_preview", "sell_preview", "orders_monitor", "trades_monitor",
                "cancels_monitor", "account_snapshot", "sandbox_summary",
            )
        }
        self.strategy_config = load_config(self.candidate_config)
        self.strategy_config["paths"]["output_dir"] = str(self.strategy)
        self.strategy_config["paths"]["cache_dir"] = str(self.root / "base-cache")
        self.strategy_config["validation"]["forward_only_candidate_configs"] = [
            str(self.candidate_config)
        ]
        self.live_config = {
            "paths": {"output_dir": str(self.live), "state_db": str(self.live_db)},
            "auto_live": {
                "emergency_stop": True,
                "allow_broker_submit": False,
                "account_snapshot_max_age_seconds": 300,
                "action_confirmation": {"required_before_submit": True},
                "command_adapter": {
                    "command": ["python", "bridge.py", "--config", str(self.bridge_path)],
                    "pinned_files": [{
                        "path": str(self.bridge_path),
                        "sha256": sha256(self.bridge_path.read_bytes()).hexdigest(),
                    }],
                },
                "service": {"enabled": False, "allow_submit": False},
                "rollout": {
                    "stage": "canary",
                    "stages": {"canary": {"max_orders": 1, "max_single_order_amount": 1000}},
                },
                "desktop_canary_certification": {
                    "bridge_config": str(self.bridge_path),
                    "preview_max_age_seconds": 3600,
                    "monitor_max_age_seconds": 3600,
                    "account_snapshot_max_age_seconds": 300,
                    "certification_path": str(self.certification_path),
                    "evidence": self.canary_evidence,
                },
            },
        }
        self.readiness_patcher = patch(
            "ashare_quant.deployment_gate.build_live_readiness_from_outputs",
            return_value=pd.DataFrame(
                [
                    {"check": "candidate_exists", "passed": True, "value": "candidate-1"},
                    {"check": "strict_strategy_evidence", "passed": True, "value": "current"},
                ]
            ),
        )
        self.mock_live_readiness = self.readiness_patcher.start()

    def tearDown(self):
        self.readiness_patcher.stop()
        os.environ.pop(self.hmac_env, None)

    def write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


    def write_canary_evidence(self, generated_at: str) -> None:
        for side in ("buy", "sell"):
            Path(self.canary_evidence[f"{side}_preview"]).write_text(
                json.dumps({
                    "status": "preview_verified",
                    "environment": "live",
                    "side": side,
                    "form_readback_verified": True,
                    "form_restore_verified": True,
                    "submit_invoked": False,
                    "cancel_invoked": False,
                    "generated_at": generated_at,
                }),
                encoding="utf-8",
            )
        for name in ("orders", "trades", "cancels"):
            Path(self.canary_evidence[f"{name}_monitor"]).write_text(
                json.dumps({
                    "status": "empty_verified",
                    "environment": "live",
                    "image_sha256": sha256(name.encode("utf-8")).hexdigest(),
                    "submit_invoked": False,
                    "cancel_invoked": False,
                    "generated_at": generated_at,
                }),
                encoding="utf-8",
            )
        Path(self.canary_evidence["account_snapshot"]).write_text(
            json.dumps({
                "status": "ok",
                "environment": "live",
                "account": {"account_id": "huatai-masked", "available_cash": 10000},
                "generated_at": generated_at,
            }),
            encoding="utf-8",
        )
        Path(self.canary_evidence["sandbox_summary"]).write_text(
            json.dumps({
                "status": "sandbox_certified",
                "buy_form_preview": True,
                "sell_form_preview": True,
                "orders_empty_verified": True,
                "submit_invoked": False,
                "cancel_invoked": False,
                "auto_live_config_sha256": auto_live_config_sha256(self.live_config),
                "generated_at": generated_at,
            }),
            encoding="utf-8",
        )
    def write_complete_evidence(self) -> None:
        now = pd.Timestamp.now(tz="UTC").isoformat()
        self.write_csv(
            self.strategy / "forward_only_candidate_observation.csv",
            [
                {
                    "candidate_config": str(self.candidate_config),
                    "candidate_name": "candidate-strategy",
                    "candidate_id": "candidate-1",
                    "generated_at": now,
                    "candidate_output_dir": str(self.candidate_output),
                    "status": "observed",
                    "frozen_input_evidence_passed": True,
                    "membership_evidence_passed": True,
                    "benchmark_mapping_evidence_passed": True,
                    "shadow_evaluation_evidence_passed": True,
                    "shadow_evaluation_sha256": mapping_sha256(
                        shadow_evaluation_identity(load_config(self.candidate_config))
                    ),
                    "forward_risk_evaluation_evidence_passed": True,
                    "forward_risk_evaluation_sha256": mapping_sha256(
                        forward_risk_evaluation_identity(load_config(self.candidate_config))
                    ),
                    "market_data_current": True,
                    "expected_market_date": "2026-07-28",
                    "complete_market_end": "2026-07-28",
                    "benchmark_end": "2026-07-28",
                    "trade_calendar_covered": True,
                    "max_observed_trading_days": 60,
                    "required_forward_trading_days": 60,
                    "remaining_forward_trading_days": 0,
                    "forward_observation_status": "shadow_window_complete",
                }
            ],
        )
        self.write_csv(
            self.candidate_output / "live_candidate_validation.csv",
            [
                {
                    "check": "candidate_exists",
                    "passed": True,
                    "value": "candidate-1",
                },
                {
                    "check": "forward_validation_status",
                    "passed": True,
                    "value": "validated",
                },
            ],
        )
        self.write_csv(
            self.live / "huatai_client_status.csv",
            [
                {
                    "status": "trading_process_ready",
                    "installed": True,
                    "sha256_match": True,
                    "hexin_process_count": 1,
                    "xiadan_process_count": 1,
                }
            ],
        )
        self.write_csv(self.live / "huatai_read_only_refresh.csv", [{"status": "ok"}])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "account", "passed": True}])
        self.write_csv(
            self.live / "auto_live_broker_account_snapshot.csv",
            [{
                "snapshot_run_id": "snapshot-1",
                "broker": "huatai",
                "account_id": "huatai-masked",
                "environment": "live",
                "cash": 10000.0,
                "available_cash": 10000.0,
                "market_value": 0.0,
                "total_asset": 10000.0,
                "generated_at": now,
            }],
        )
        self.write_csv(
            self.live / "huatai_read_only_history_audit.csv",
            [
                {"check": "broker_readonly_history_integrity", "passed": True},
                {"check": "broker_readonly_history_depth", "passed": True},
            ],
        )
        self.write_csv(self.live / "auto_live_account_binding_audit.csv", [{"check": "binding", "passed": True}])
        self.write_csv(self.live / "auto_live_adapter_file_audit.csv", [{"check": "files", "passed": True}])
        self.write_csv(
            self.live / "auto_live_adapter_replay_summary.csv",
            [{"status": "certified_live", "auto_live_config_sha256": auto_live_config_sha256(self.live_config)}],
        )
        self.write_csv(
            self.live / "auto_live_recovery_drill_summary.csv",
            [{
                "status": "passed",
                "schema_version": 1,
                "auto_live_config_sha256": auto_live_config_sha256(self.live_config),
                "broker_adapter_invoked": False,
                "isolated_workspace_removed": True,
                "passed_checks": 8,
                "total_checks": 8,
                "generated_at": now,
            }],
        )
        self.write_csv(self.live / "auto_live_alert_audit.csv", [{"check": "alert", "passed": True}])
        self.write_csv(self.live / "broker_canary_capital_audit.csv", [{"check": "broker_canary_capital_ready", "passed": True}])
        self.write_csv(
            self.live / "broker_capital_transition.csv",
            [{
                "status": "ready_for_full_strategy_review",
                "snapshot_fresh": True,
                "current_total_asset": 600000,
                "minimum_full_strategy_equity": 500000,
                "full_strategy_capital_gap": 0,
                "full_strategy_ready": True,
                "strategy_identity_preserved": True,
            }],
        )
        canary_checks = [
            "action_confirmation_required",
            "unattended_service_disabled",
            "canary_hard_limits",
            "canary_bridge_config",
            "certification_path_match",
            "command_uses_canary_bridge",
            "canary_bridge_hash_pinned",
            "certification_hmac_ready",
            "sandbox_evidence_config_match",
            "sandbox_evidence_certified",
        ]
        self.write_csv(
            self.live / "huatai_desktop_canary_audit.csv",
            [{"check": check, "passed": True} for check in canary_checks],
        )
        import sqlite3

        conn = sqlite3.connect(self.live_db)
        try:
            conn.execute("create table if not exists account_snapshots (id integer)")
            conn.execute("delete from account_snapshots")
            conn.execute("insert into account_snapshots values (1)")
            conn.commit()
        finally:
            conn.close()
        self.write_canary_evidence(now)

    def test_two_candidates_cannot_share_the_same_dedicated_cache(self):
        second_config = self.root / "candidate-second.yaml"
        second = json.loads(self.candidate_config.read_text(encoding="utf-8"))
        second["paths"]["output_dir"] = str(self.root / "candidate-second-output")
        second_config.write_text(json.dumps(second), encoding="utf-8")

        passed, detail = forward_candidate_cache_isolation(
            self.strategy_config,
            [str(self.candidate_config), str(second_config)],
        )

        self.assertFalse(passed)
        self.assertIn("shared_with:", detail)

    def test_shared_candidate_cache_blocks_strategy_gate(self):
        self.write_complete_evidence()
        candidate = json.loads(self.candidate_config.read_text(encoding="utf-8"))
        candidate["paths"]["cache_dir"] = self.strategy_config["paths"]["cache_dir"]
        self.candidate_config.write_text(json.dumps(candidate), encoding="utf-8")

        outputs = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(outputs["deployment_gate.csv"])
        row = gate[gate["check"] == "candidate_cache_isolation"].iloc[0]

        self.assertFalse(bool(row["passed"]))
        self.assertIn("shared_with_base", str(row["value"]))
        self.assertFalse(bool(gate.loc[gate["check"] == "strategy_ready", "passed"].iloc[0]))

    def test_gate_passes_only_when_strategy_broker_and_safety_are_ready(self):
        self.write_complete_evidence()

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["strategy_ready", "passed"]))
        self.assertTrue(bool(gate.loc["broker_ready", "passed"]))
        self.assertTrue(bool(gate.loc["safe_default", "passed"]))
        self.assertTrue(bool(gate.loc["ready_to_request_canary", "passed"]))
        self.assertIn("只读审计", paths["deployment_gate.html"].read_text(encoding="utf-8"))

        self.assertTrue(bool(gate.loc["forward_validation_passed", "passed"]))
        self.assertTrue(bool(gate.loc["strict_live_readiness", "passed"]))
        self.assertTrue(bool(gate.loc["canary_configuration_integrity", "passed"]))
        self.assertTrue(bool(gate.loc["canary_sandbox_evidence_current", "passed"]))

    def test_strict_live_readiness_blocks_canary_after_other_evidence_passes(self):
        self.write_complete_evidence()
        self.mock_live_readiness.return_value = pd.DataFrame(
            [
                {"check": "candidate_exists", "passed": True, "value": "candidate-1"},
                {"check": "excess_bootstrap_lower_95", "passed": False, "value": -0.20},
            ]
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["forward_validation_passed", "passed"]))
        self.assertFalse(bool(gate.loc["strict_live_readiness", "passed"]))
        self.assertFalse(bool(gate.loc["strategy_ready", "passed"]))
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))
        self.assertIn("excess_bootstrap_lower_95", gate.loc["strict_live_readiness", "value"])

    def test_execution_strategy_must_match_frozen_candidate(self):
        self.write_complete_evidence()
        self.strategy_config["strategy"]["top_n"] = 50

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertFalse(bool(gate.loc["execution_strategy_identity", "passed"]))
        self.assertFalse(bool(gate.loc["strategy_ready", "passed"]))
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))
        self.assertIn("execution_hash=", gate.loc["execution_strategy_identity", "value"])

    def test_forward_collection_remains_next_action_before_strategy_switch(self):
        self.write_complete_evidence()
        self.strategy_config["strategy"]["top_n"] = 50
        observation_path = self.strategy / "forward_only_candidate_observation.csv"
        observation = pd.read_csv(observation_path)
        observation.loc[0, "max_observed_trading_days"] = 2
        observation.loc[0, "remaining_forward_trading_days"] = 58
        observation.loc[0, "forward_observation_status"] = "collecting_forward_data"
        observation.to_csv(observation_path, index=False, encoding="utf-8-sig")
        self.write_csv(
            self.candidate_output / "live_candidate_validation.csv",
            [
                {
                    "check": "candidate_exists",
                    "passed": True,
                    "value": "candidate-1",
                },
                {
                    "check": "forward_validation_status",
                    "passed": False,
                    "value": "collecting_forward_data",
                },
            ],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["execution_strategy_identity", "passed"]))
        self.assertFalse(bool(gate.loc["forward_window_complete", "passed"]))
        self.assertIn("forward-daily", gate.loc["strategy_ready", "next_action"])


    def test_paired_selection_can_name_one_execution_candidate_without_implicit_choice(self):
        second_output = self.root / "candidate-two-output"
        second_output.mkdir(parents=True)
        second_manifest = self.root / "candidate-two-manifest.json"
        second_config = self.root / "candidate-two.json"
        definition = json.loads(self.candidate_config.read_text(encoding="utf-8"))
        definition["paths"]["output_dir"] = str(second_output)
        definition["paths"]["cache_dir"] = str(self.root / "candidate-two-cache")
        definition["validation"]["candidate_path"] = str(second_manifest)
        definition["strategy"]["top_n"] = 99
        second_config.write_text(json.dumps(definition), encoding="utf-8")
        loaded_second = load_config(second_config)
        second_manifest.write_text(
            json.dumps(
                {
                    "candidate_id": "candidate-2",
                    "strategy_hash": live_strategy_hash(loaded_second),
                    "strategy_name": "candidate-two",
                    "freeze_mode": "forward_only_preregistration",
                }
            ),
            encoding="utf-8",
        )
        rows = [
            pd.Series(
                {
                    "candidate_id": "candidate-1",
                    "candidate_name": "candidate-strategy",
                    "candidate_config": str(self.candidate_config),
                    "candidate_output_dir": str(self.candidate_output),
                }
            ),
            pd.Series(
                {
                    "candidate_id": "candidate-2",
                    "candidate_name": "candidate-two",
                    "candidate_config": str(second_config),
                    "candidate_output_dir": str(second_output),
                }
            ),
        ]
        self.strategy_config["validation"]["forward_only_candidate_configs"] = [
            str(self.candidate_config),
            str(second_config),
        ]
        self.strategy_config["validation"]["forward_selection_policy_path"] = str(
            self.root / "selection-policy.json"
        )
        records = candidate_set_records(pd.DataFrame(rows))
        observation = pd.DataFrame(rows).assign(
            required_forward_trading_days=60,
            max_observed_trading_days=61,
        )

        def write_curves(periods: int) -> None:
            dates = pd.bdate_range("2026-01-02", periods=periods)
            for record in records:
                daily = 0.01 if record["candidate_id"] == "candidate-1" else 0.0
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
        write_forward_candidate_selection(self.strategy_config, observation)
        write_curves(61)
        selection_paths = write_forward_candidate_selection(self.strategy_config, observation)
        selection = pd.read_csv(selection_paths["forward_candidate_selection_summary.csv"]).iloc[0]

        self.assertTrue(
            _forward_candidate_selection_evidence(self.strategy_config, selection, rows)[0]
        )
        self.assertIn(
            "observed_days=61;paired_post_policy_days=60/60",
            _forward_candidate_selection_evidence(
                self.strategy_config, selection, rows
            )[1],
        )
        self.assertTrue(_execution_strategy_identity(self.strategy_config, rows, selection)[0])

        selection["selected_candidate_id"] = ""
        self.assertFalse(_forward_candidate_selection_evidence(self.strategy_config, selection, rows)[0])
        self.assertFalse(_execution_strategy_identity(self.strategy_config, rows, selection)[0])
    def test_multiple_candidates_cannot_implicitly_select_execution_strategy(self):
        self.write_complete_evidence()
        self.strategy_config["validation"]["forward_only_candidate_configs"].append(
            str(self.candidate_config)
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertFalse(bool(gate.loc["execution_strategy_identity", "passed"]))
        self.assertIn("candidate_count=2", gate.loc["execution_strategy_identity", "value"])
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))

    def test_gate_fails_closed_without_creating_ledger_or_orders(self):
        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))
        self.assertFalse(bool(gate.loc["forward_window_complete", "passed"]))
        self.assertFalse(bool(gate.loc["isolated_live_ledger_initialized", "passed"]))
        self.assertIn("forward-daily", gate.loc["strategy_ready", "next_action"])
        self.assertIn("华泰客户端", gate.loc["broker_ready", "next_action"])
        self.assertFalse(self.live_db.exists())
        self.assertFalse((self.strategy / "orders.csv").exists())
        self.assertFalse((self.live / "rpa_orders.csv").exists())
        roadmap = pd.read_csv(paths["broker_deployment_roadmap.csv"]).set_index("check")
        self.assertEqual(roadmap.loc["client_process_ready", "status"], "actionable_read_only")
        self.assertTrue(bool(roadmap.loc["client_process_ready", "allowed_in_read_only_run"]))
        self.assertEqual(roadmap.loc["account_binding", "status"], "authorization_required")
        self.assertFalse(bool(roadmap.loc["account_binding", "allowed_in_read_only_run"]))
        self.assertEqual(roadmap.loc["adapter_replay_certification", "status"], "future_canary_only")
        self.assertFalse(bool(roadmap.loc["adapter_replay_certification", "allowed_in_read_only_run"]))

    def test_empty_live_database_file_is_not_an_initialized_ledger(self):
        if self.live_db.exists():
            self.live_db.unlink()
        self.live_db.touch()

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertFalse(bool(gate.loc["isolated_live_ledger_initialized", "passed"]))
        self.assertIn("account_snapshots_table=missing", gate.loc["isolated_live_ledger_initialized", "value"])
    def test_full_observation_window_cannot_bypass_failed_forward_return(self):
        self.write_complete_evidence()
        self.write_csv(
            self.candidate_output / "live_candidate_validation.csv",
            [
                {
                    "check": "candidate_exists",
                    "passed": True,
                    "value": "candidate-1",
                },
                {
                    "check": "forward_validation_status",
                    "passed": False,
                    "value": "failed_forward_excess",
                },
            ],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["forward_window_complete", "passed"]))
        self.assertFalse(bool(gate.loc["forward_validation_passed", "passed"]))
        self.assertFalse(bool(gate.loc["strategy_ready", "passed"]))
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))


    def test_background_processes_do_not_prove_authenticated_session(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "huatai_read_only_refresh.csv",
            [
                {
                    "status": "failed",
                    "desktop_stage": "client_window_absent",
                    "visible_client_windows": 0,
                }
            ],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["client_process_ready", "passed"]))
        self.assertFalse(bool(gate.loc["client_session_ready", "passed"]))
        self.assertFalse(bool(gate.loc["read_only_refresh", "passed"]))
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))
        self.assertIn("重新打开华泰客户端", gate.loc["client_session_ready", "next_action"])


    def test_visible_windows_without_trading_grid_do_not_prove_authenticated_session(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "huatai_read_only_refresh.csv",
            [
                {
                    "status": "failed",
                    "desktop_stage": "trading_page_unavailable",
                    "visible_client_windows": 2,
                    "trading_grid_count": 0,
                }
            ],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["client_process_ready", "passed"]))
        self.assertFalse(bool(gate.loc["client_session_ready", "passed"]))
        self.assertEqual(gate.loc["client_session_ready", "value"], "trading_page_unavailable")
    def test_static_canary_configuration_is_separate_from_stale_sandbox_evidence(self):
        self.write_complete_evidence()
        sandbox_path = Path(self.canary_evidence["sandbox_summary"])
        sandbox = json.loads(sandbox_path.read_text(encoding="utf-8"))
        sandbox["status"] = "blocked"
        sandbox_path.write_text(json.dumps(sandbox), encoding="utf-8")

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["canary_configuration_integrity", "passed"]))
        self.assertFalse(bool(gate.loc["canary_sandbox_evidence_current", "passed"]))
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))

    def test_stale_raw_canary_evidence_cannot_be_hidden_by_green_audit_csv(self):
        self.write_complete_evidence()
        preview_path = Path(self.canary_evidence["buy_preview"])
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        preview["generated_at"] = "2000-01-01T00:00:00+00:00"
        preview_path.write_text(json.dumps(preview), encoding="utf-8")
        self.write_csv(
            self.live / "huatai_desktop_canary_audit.csv",
            [{"check": "stale-csv", "passed": True}],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["canary_configuration_integrity", "passed"]))
        self.assertFalse(bool(gate.loc["canary_sandbox_evidence_current", "passed"]))
        self.assertIn("7/8", gate.loc["canary_sandbox_evidence_current", "value"])
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))

    def test_stale_candidate_market_date_cannot_pass_strategy_gate(self):
        self.write_complete_evidence()
        observation_path = self.strategy / "forward_only_candidate_observation.csv"
        observation = pd.read_csv(observation_path)
        observation.loc[0, "market_data_current"] = False
        observation.loc[0, "complete_market_end"] = "2026-07-27"
        observation.to_csv(observation_path, index=False, encoding="utf-8-sig")

        write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(self.strategy / "deployment_gate.csv").set_index("check")

        self.assertFalse(bool(gate.loc["candidate_market_data_current", "passed"]))
        self.assertTrue(bool(gate.loc["shadow_evaluation_evidence", "passed"]))
        self.assertTrue(bool(gate.loc["index_membership_evidence", "passed"]))
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))
    def test_stale_candidate_observation_cannot_pass_strategy_gate(self):
        self.write_complete_evidence()
        observation_path = self.strategy / "forward_only_candidate_observation.csv"
        observation = pd.read_csv(observation_path)
        observation.loc[0, "generated_at"] = "2000-01-01T00:00:00+00:00"
        observation.to_csv(observation_path, index=False, encoding="utf-8-sig")

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertFalse(bool(gate.loc["strategy_ready", "passed"]))
        self.assertIn("fresh=0/1", gate.loc["candidate_observation_current", "value"])

    def test_candidate_config_change_invalidates_frozen_observation(self):
        self.write_complete_evidence()
        changed = json.loads(self.candidate_config.read_text(encoding="utf-8"))
        changed["strategy"]["top_n"] = 50
        self.candidate_config.write_text(json.dumps(changed), encoding="utf-8")

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertFalse(bool(gate.loc["strategy_ready", "passed"]))
        self.assertIn("identity=0/1", gate.loc["candidate_observation_current", "value"])

    def test_forward_only_governance_change_invalidates_observation(self):
        self.write_complete_evidence()
        changed = json.loads(self.candidate_config.read_text(encoding="utf-8"))
        changed["validation"]["research_only"] = False
        self.candidate_config.write_text(json.dumps(changed), encoding="utf-8")

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertFalse(bool(gate.loc["strategy_ready", "passed"]))
        self.assertIn("identity=0/1", gate.loc["candidate_observation_current", "value"])

    def test_naive_observation_timestamp_uses_local_timezone(self):
        self.write_complete_evidence()
        observation_path = self.strategy / "forward_only_candidate_observation.csv"
        observation = pd.read_csv(observation_path)
        observation.loc[0, "generated_at"] = (
            datetime.now().astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
        )
        observation.to_csv(observation_path, index=False, encoding="utf-8-sig")

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertIn("fresh=1/1", gate.loc["candidate_observation_current", "value"])


    def test_replay_certification_from_old_config_is_rejected(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "auto_live_adapter_replay_summary.csv",
            [{"status": "certified_live", "auto_live_config_sha256": "0" * 64}],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["adapter_replay_certification", "passed"]))
        self.assertIn("config_match=False", gate.loc["adapter_replay_certification", "value"])
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))

    def test_recovery_drill_config_mismatch_blocks_canary(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "auto_live_recovery_drill_summary.csv",
            [{
                "status": "passed",
                "schema_version": 1,
                "auto_live_config_sha256": "stale-config",
                "broker_adapter_invoked": False,
                "isolated_workspace_removed": True,
                "passed_checks": 8,
                "total_checks": 8,
                "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
            }],
        )

        gate = pd.read_csv(
            write_deployment_gate(self.strategy_config, self.live_config)["deployment_gate.csv"]
        ).set_index("check")

        self.assertFalse(bool(gate.loc["submission_recovery_drill", "passed"]))
        self.assertIn("config_match=False", gate.loc["submission_recovery_drill", "value"])
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))

    def test_recovery_drill_missing_explicit_no_broker_marker_blocks_canary(self):
        self.write_complete_evidence()
        path = self.live / "auto_live_recovery_drill_summary.csv"
        summary = pd.read_csv(path).drop(columns=["broker_adapter_invoked"])
        summary.to_csv(path, index=False, encoding="utf-8-sig")

        gate = pd.read_csv(
            write_deployment_gate(self.strategy_config, self.live_config)["deployment_gate.csv"]
        ).set_index("check")

        self.assertFalse(bool(gate.loc["submission_recovery_drill", "passed"]))
        self.assertIn("broker_adapter_invoked=missing", gate.loc["submission_recovery_drill", "value"])

    def test_recovery_drill_that_invoked_broker_blocks_canary(self):
        self.write_complete_evidence()
        path = self.live / "auto_live_recovery_drill_summary.csv"
        summary = pd.read_csv(path)
        summary.loc[0, "broker_adapter_invoked"] = True
        summary.to_csv(path, index=False, encoding="utf-8-sig")

        gate = pd.read_csv(
            write_deployment_gate(self.strategy_config, self.live_config)["deployment_gate.csv"]
        ).set_index("check")

        self.assertFalse(bool(gate.loc["submission_recovery_drill", "passed"]))
        self.assertIn("broker_adapter_invoked=true", gate.loc["submission_recovery_drill", "value"])
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))

    def test_adapter_file_hash_is_recomputed_instead_of_trusting_csv(self):
        adapter_path = self.root / "adapter.py"
        adapter_path.write_text("print('reviewed')\n", encoding="utf-8")
        adapter = self.live_config["auto_live"]["command_adapter"]
        adapter["require_pinned_files"] = True
        adapter["pinned_files"].append(
            {"path": str(adapter_path), "sha256": sha256(adapter_path.read_bytes()).hexdigest()}
        )
        self.write_complete_evidence()
        adapter_path.write_text("print('changed')\n", encoding="utf-8")
        self.write_csv(self.live / "auto_live_adapter_file_audit.csv", [{"check": "stale-csv", "passed": True}])

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["adapter_file_integrity", "passed"]))
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))
    def test_simulation_replay_cannot_pass_broker_gate(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "auto_live_adapter_replay_summary.csv",
            [{"status": "certified_simulation"}],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertFalse(bool(gate.loc["adapter_replay_certification", "passed"]))
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))

    def test_read_only_live_replay_is_visible_but_cannot_pass_broker_gate(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "auto_live_adapter_replay_summary.csv",
            [{
                "status": "certified_read_only_live",
                "auto_live_config_sha256": auto_live_config_sha256(self.live_config),
            }],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertTrue(bool(gate.loc["adapter_read_only_replay_certification", "passed"]))
        self.assertFalse(bool(gate.loc["adapter_read_only_replay_certification", "blocking"]))
        self.assertFalse(bool(gate.loc["adapter_replay_certification", "passed"]))
        self.assertTrue(bool(gate.loc["broker_read_only_ready", "passed"]))
        self.assertFalse(bool(gate.loc["broker_execution_ready", "passed"]))
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))

    def test_local_ledger_failures_do_not_hide_read_only_readiness(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "auto_live_broker_account_audit.csv",
            [
                {"check": "broker_account_snapshot_present", "passed": True},
                {"check": "broker_total_asset_consistent", "passed": True},
                {"check": "local_live_ledger_initialized", "passed": False},
                {"check": "local_vs_broker_cash_match", "passed": False},
            ],
        )
        self.write_csv(
            self.live / "auto_live_adapter_replay_summary.csv",
            [{
                "status": "certified_read_only_live",
                "auto_live_config_sha256": auto_live_config_sha256(self.live_config),
            }],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertTrue(bool(gate.loc["account_snapshot_broker_side_audit", "passed"]))
        self.assertFalse(bool(gate.loc["account_snapshot_audit", "passed"]))
        self.assertTrue(bool(gate.loc["broker_read_only_ready", "passed"]))
        self.assertFalse(bool(gate.loc["broker_execution_ready", "passed"]))
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))

    def test_canary_capital_affordability_blocks_canary_request(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "broker_canary_capital_audit.csv",
            [{"check": "broker_canary_capital_ready", "passed": False}],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertFalse(bool(gate.loc["canary_capital_affordability", "passed"]))
        self.assertFalse(bool(gate.loc["broker_ready", "passed"]))
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))
    def test_canary_affordability_does_not_override_full_strategy_capital_gap(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "broker_capital_transition.csv",
            [{
                "status": "ready_for_execution_canary_review",
                "snapshot_fresh": True,
                "current_total_asset": 2567.44,
                "minimum_full_strategy_equity": 487920.80,
                "full_strategy_capital_gap": 485353.36,
                "full_strategy_ready": False,
                "canary_ready": True,
                "strategy_identity_preserved": True,
            }],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertTrue(bool(gate.loc["canary_capital_affordability", "passed"]))
        self.assertFalse(bool(gate.loc["full_strategy_capital_compatibility", "passed"]))
        self.assertTrue(bool(gate.loc["ready_to_request_canary", "passed"]))
        self.assertFalse(bool(gate.loc["full_strategy_capital_ready", "passed"]))
        self.assertFalse(bool(gate.loc["ready_for_live_strategy_review", "passed"]))
        roadmap = pd.read_csv(paths["broker_deployment_roadmap.csv"]).set_index("check")
        self.assertEqual(
            roadmap.loc["full_strategy_capital_compatibility", "status"],
            "capital_incompatible",
        )
        self.assertFalse(
            bool(roadmap.loc["full_strategy_capital_compatibility", "allowed_in_read_only_run"])
        )

    def test_stale_account_snapshot_invalidates_both_capital_modes(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "auto_live_broker_account_snapshot.csv",
            [{"generated_at": "2000-01-01T00:00:00+00:00", "account_id": "huatai-masked"}],
        )

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertFalse(bool(gate.loc["account_snapshot_fresh", "passed"]))
        self.assertFalse(bool(gate.loc["canary_capital_affordability", "passed"]))
        self.assertFalse(bool(gate.loc["full_strategy_capital_compatibility", "passed"]))
        self.assertIn(
            "account_snapshot=age_seconds=",
            gate.loc["canary_capital_affordability", "value"],
        )

    def test_canary_capital_display_evidence_is_synced_to_strategy_output(self):
        self.write_complete_evidence()
        transition = [{"status": "snapshot_stale", "allowed_mode": "blocked"}]
        candidates = [{"symbol": "600000", "executable": False}]
        self.write_csv(self.live / "broker_capital_transition.csv", transition)
        self.write_csv(self.live / "broker_canary_capital_candidates.csv", candidates)

        paths = write_deployment_gate(self.strategy_config, self.live_config)

        pd.testing.assert_frame_equal(pd.read_csv(paths["broker_capital_transition.csv"]), pd.DataFrame(transition))
        synced = pd.read_csv(paths["broker_canary_capital_candidates.csv"], dtype={"symbol": str})
        self.assertEqual(synced.to_dict("records"), candidates)
    def test_stale_forward_risk_identity_invalidates_observation(self):
        self.write_complete_evidence()
        observation_path = self.strategy / "forward_only_candidate_observation.csv"
        observation = pd.read_csv(observation_path)
        observation.loc[0, "forward_risk_evaluation_sha256"] = "legacy-risk-policy"
        observation.to_csv(observation_path, index=False, encoding="utf-8-sig")

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertFalse(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertTrue(bool(gate.loc["frozen_input_evidence", "passed"]))
        self.assertTrue(bool(gate.loc["shadow_evaluation_evidence", "passed"]))
        self.assertFalse(bool(gate.loc["forward_risk_evaluation_evidence", "passed"]))
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))


    def test_stale_shadow_evaluation_identity_invalidates_observation(self):
        self.write_complete_evidence()
        observation_path = self.strategy / "forward_only_candidate_observation.csv"
        observation = pd.read_csv(observation_path)
        observation.loc[0, "shadow_evaluation_sha256"] = "legacy-policy"
        observation.to_csv(observation_path, index=False, encoding="utf-8-sig")

        paths = write_deployment_gate(self.strategy_config, self.live_config)
        gate = pd.read_csv(paths["deployment_gate.csv"]).set_index("check")

        self.assertFalse(bool(gate.loc["candidate_observation_current", "passed"]))
        self.assertTrue(bool(gate.loc["frozen_input_evidence", "passed"]))
        self.assertTrue(bool(gate.loc["index_membership_evidence", "passed"]))
        self.assertTrue(bool(gate.loc["benchmark_index_mapping_evidence", "passed"]))
        self.assertFalse(bool(gate.loc["shadow_evaluation_evidence", "passed"]))
        self.assertFalse(bool(gate.loc["ready_to_request_canary", "passed"]))
    def test_readonly_history_depth_blocks_execution_but_not_readonly_connection(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "huatai_read_only_history_audit.csv",
            [
                {"check": "broker_readonly_history_integrity", "passed": True},
                {"check": "broker_readonly_history_depth", "passed": False},
            ],
        )

        gate = pd.read_csv(write_deployment_gate(self.strategy_config, self.live_config)["deployment_gate.csv"]).set_index("check")
        self.assertTrue(bool(gate.loc["broker_read_only_ready", "passed"]))
        self.assertFalse(bool(gate.loc["broker_execution_ready", "passed"]))

    def test_readonly_history_integrity_blocks_readonly_connection(self):
        self.write_complete_evidence()
        self.write_csv(
            self.live / "huatai_read_only_history_audit.csv",
            [
                {"check": "broker_readonly_history_integrity", "passed": False},
                {"check": "broker_readonly_history_depth", "passed": False},
            ],
        )

        gate = pd.read_csv(write_deployment_gate(self.strategy_config, self.live_config)["deployment_gate.csv"]).set_index("check")
        self.assertFalse(bool(gate.loc["broker_read_only_ready", "passed"]))
        self.assertFalse(bool(gate.loc["broker_execution_ready", "passed"]))

if __name__ == "__main__":
    unittest.main()
