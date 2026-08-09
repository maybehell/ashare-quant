from pathlib import Path
import hashlib
import json
import unittest

import pandas as pd

from ashare_quant.audit import backtest_config_sha256, mapping_sha256
from ashare_quant.local_operator import (
    _broker_deployment_roadmap_status,
    _broker_refresh_status,
    _challenger_observation_set_status,
    _formal_candidate_observation_status,
    _observation_daemon_status,
    _observation_startup_status,
    _recovery_candidate_capital_status,
    _recovery_candidate_status,
    _scheduled_observation_status,
    _specific_item_status,
    strategy_operating_mode,
    write_local_operator_status,
)


class LocalOperatorTests(unittest.TestCase):

    def test_observation_startup_status_requires_current_safe_launcher(self):
        output = Path("test_outputs") / "observation_startup_status"
        output.mkdir(parents=True, exist_ok=True)
        launcher = output / "AshareQuantDailyObservation.cmd"
        expected = (Path.cwd() / "scripts" / "observation_supervisor.ps1").resolve()
        launcher.unlink(missing_ok=True)

        missing_ok, missing_value, _ = _observation_startup_status(
            required=True,
            startup_path=launcher,
        )
        self.assertFalse(missing_ok)
        self.assertIn("missing:", missing_value)

        launcher.write_text(
            f'@echo off\nstart "" /min powershell.exe -File "{expected}"\n',
            encoding="ascii",
        )
        installed_ok, installed_value, _ = _observation_startup_status(
            required=True,
            startup_path=launcher,
        )
        self.assertTrue(installed_ok)
        self.assertIn("installed:", installed_value)

        launcher.write_text(
            f'@echo off\nstart "" /min powershell.exe -File "{expected}" auto-live-run\n',
            encoding="ascii",
        )
        unsafe_ok, unsafe_value, _ = _observation_startup_status(
            required=True,
            startup_path=launcher,
        )
        self.assertFalse(unsafe_ok)
        self.assertIn("invalid:", unsafe_value)
    def test_scheduled_observation_distinguishes_heartbeat_from_failed_run(self):
        failed = pd.DataFrame([{
            "status": "failed",
            "exit_code": 1,
            "finished_at": "2026-07-31T16:11:57+08:00",
        }])
        completed = pd.DataFrame([{
            "status": "completed",
            "exit_code": 0,
            "finished_at": "2026-07-31T18:40:00+08:00",
        }])

        failed_ok, failed_value, failed_message = _scheduled_observation_status(failed, required=True)
        completed_ok, completed_value, _ = _scheduled_observation_status(completed, required=True)

        self.assertFalse(failed_ok)
        self.assertIn("status=failed", failed_value)
        self.assertIn("守护进程", failed_message)
        self.assertTrue(completed_ok)
        self.assertIn("status=completed", completed_value)
    def test_trading_module_not_loaded_has_actionable_status(self):
        passed, value, message = _broker_refresh_status(
            pd.DataFrame([{
                "status": "failed",
                "stage": "desktop_snapshot",
                "desktop_stage": "trading_module_ui_not_loaded",
                "message": "trading page unavailable",
            }])
        )

        self.assertFalse(passed)
        self.assertIn("trading_module_ui_not_loaded", value)
        self.assertIn("交易模块", message)
        self.assertIn("资金股票", message)

    def test_broker_authentication_required_is_explicit_and_manual(self):
        passed, value, message = _broker_refresh_status(
            pd.DataFrame([{
                "status": "failed",
                "stage": "desktop_snapshot",
                "desktop_stage": "broker_authentication_required",
                "message": "login controls detected",
            }])
        )

        self.assertFalse(passed)
        self.assertIn("broker_authentication_required", value)
        self.assertIn("手动", message)
        self.assertIn("不会自动填写凭据", message)

    def test_formal_candidate_daemon_and_broker_stages_are_visible(self):
        candidate = pd.DataFrame(
            [
                {
                    "candidate_id": "candidate-1",
                    "status": "observed",
                    "max_observed_trading_days": 0,
                    "required_forward_trading_days": 60,
                    "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "frozen_input_evidence_passed": True,
                    "membership_evidence_passed": True,
                    "benchmark_mapping_evidence_passed": True,
                    "shadow_evaluation_evidence_passed": True,
                    "forward_risk_evaluation_evidence_passed": True,
                }
            ]
        )
        daemon = {
            "status": "waiting",
            "mode": "observation_only_daemon",
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "script_sha256": hashlib.sha256((Path.cwd() / "scripts" / "observation_daemon.ps1").read_bytes()).hexdigest(),
            "state": {"last_success_date": "2026-07-28", "attempt_count": 1, "last_exit_code": 0},
        }
        roadmap = pd.DataFrame(
            [
                {"status": "completed"},
                {"status": "actionable_read_only"},
                {"status": "authorization_required"},
                {"status": "future_canary_only"},
            ]
        )

        candidate_ok, candidate_value, _ = _formal_candidate_observation_status(candidate)
        daemon_ok, daemon_value, _ = _observation_daemon_status(daemon, max_age_seconds=300)
        roadmap_ok, roadmap_value, _ = _broker_deployment_roadmap_status(roadmap)

        self.assertTrue(candidate_ok)
        self.assertIn("candidate=candidate-1", candidate_value)
        self.assertIn("forward=0/60", candidate_value)
        self.assertTrue(daemon_ok)
        self.assertIn("last_success=2026-07-28", daemon_value)
        self.assertIn("exit=0", daemon_value)
        self.assertIn("script=current", daemon_value)
        stale_ok, stale_value, _ = _observation_daemon_status({**daemon, "script_sha256": "0" * 64}, max_age_seconds=300)
        self.assertFalse(stale_ok)
        self.assertIn("script=stale_or_missing", stale_value)
        self.assertFalse(roadmap_ok)
        self.assertIn("read_only=1", roadmap_value)
        self.assertIn("ledger_auth=1", roadmap_value)
        self.assertIn("future_canary=1", roadmap_value)

    def test_operating_mode_routes_paused_strategy_to_successor_observation(self):
        health = pd.DataFrame(
            [
                {"check": "paper_orders_allowed", "value": False},
                {"check": "strict_live_allowed", "value": False},
            ]
        )
        promotion = pd.DataFrame(
            [{"candidate_strategy_name": "robust", "decision": "research_only"}]
        )

        mode, message = strategy_operating_mode(health, promotion, True)

        self.assertEqual(mode, "successor_observation")
        self.assertIn("robust", message)
        self.assertIn("不得生成工单", message)

    def test_formal_candidates_replace_quarantined_legacy_candidate_as_observation_only(self):
        health = pd.DataFrame([
            {"check": "paper_orders_allowed", "value": False},
            {"check": "strict_live_allowed", "value": False},
        ])
        promotion = pd.DataFrame([{
            "candidate_strategy_name": "legacy",
            "decision": "do_not_promote_quarantined_dependency",
        }])
        formal = pd.DataFrame([
            {"candidate_id": "frozen-a", "candidate_name": "frozen_a", "max_observed_trading_days": 2, "required_forward_trading_days": 60},
            {"candidate_id": "frozen-b", "candidate_name": "frozen_b", "max_observed_trading_days": 1, "required_forward_trading_days": 60},
        ])

        mode, message = strategy_operating_mode(
            health,
            promotion,
            False,
            formal_candidate_observation=formal,
            formal_candidate_observation_ok=True,
        )

        self.assertEqual(mode, "successor_observation")
        self.assertIn("frozen_a", message)
        self.assertIn("frozen_b", message)
        self.assertIn("1/60", message)
        self.assertIn("不得生成工单", message)

    def test_formal_candidate_status_requires_every_registered_candidate(self):
        now = pd.Timestamp.now(tz="UTC").isoformat()
        rows = [
            {
                "candidate_id": candidate_id,
                "candidate_config": candidate_config,
                "status": "observed",
                "max_observed_trading_days": observed,
                "required_forward_trading_days": 60,
                "frozen_input_evidence_passed": True,
                "membership_evidence_passed": True,
                "benchmark_mapping_evidence_passed": True,
                "shadow_evaluation_evidence_passed": True,
                "forward_risk_evaluation_evidence_passed": True,
                "generated_at": now,
            }
            for candidate_id, candidate_config, observed in (
                ("frozen-a", "configs/a.yaml", 2),
                ("frozen-b", "configs/b.yaml", 1),
            )
        ]

        expected = ["configs/a.yaml", "configs/b.yaml"]
        passed, value, _ = _formal_candidate_observation_status(
            pd.DataFrame(rows), expected_configs=expected
        )
        missing, missing_value, _ = _formal_candidate_observation_status(
            pd.DataFrame(rows[:1]), expected_configs=expected
        )
        rows[0]["membership_evidence_passed"] = False
        failed, failed_value, _ = _formal_candidate_observation_status(
            pd.DataFrame(rows), expected_configs=expected
        )

        self.assertTrue(passed)
        self.assertIn("candidate_count=2", value)
        self.assertIn("forward=1/60", value)
        self.assertIn("coverage=2/2", value)
        self.assertFalse(missing)
        self.assertIn("coverage=1/2", missing_value)
        self.assertIn("coverage_ok=False", missing_value)
        self.assertFalse(failed)
        self.assertIn("evidence=False", failed_value)
    def test_selection_bias_status_is_read_from_candidate_stability(self):
        frame = pd.DataFrame([
            {"item": "strategy_selection_bias_ready", "value": "review_required", "passed": False},
        ])
        passed, value = _specific_item_status(frame, "strategy_selection_bias_ready")
        self.assertFalse(passed)
        self.assertEqual(value, "review_required")
    def test_registered_challenger_set_requires_every_fresh_observation(self):
        now = pd.Timestamp.now(tz="UTC").isoformat()
        frame = pd.DataFrame(
            [
                {
                    "status": "observed",
                    "candidate_config": "configs/a.yaml",
                    "candidate_name": "a",
                    "backtest_mode": "reused",
                    "generated_at": now,
                },
                {
                    "status": "observed",
                    "candidate_config": "configs/b.yaml",
                    "candidate_name": "b",
                    "backtest_mode": "reused",
                    "generated_at": now,
                },
            ]
        )

        passed, value, _ = _challenger_observation_set_status(
            frame,
            expected_configs=["configs/a.yaml", "configs/b.yaml"],
            max_age_seconds=300,
        )
        missing_passed, missing_value, _ = _challenger_observation_set_status(
            frame.iloc[:1],
            expected_configs=["configs/a.yaml", "configs/b.yaml"],
            max_age_seconds=300,
        )

        self.assertTrue(passed)
        self.assertIn("observed=2/2", value)
        self.assertFalse(missing_passed)
        self.assertIn("observed=1/2", missing_value)
    def test_recovery_candidate_observation_and_capital_are_visible(self):
        candidate_output = self.root / "recovery_candidate"
        candidate_output.mkdir(parents=True, exist_ok=True)
        self.write_csv(
            candidate_output / "capital_sensitivity_audit.csv",
            [
                {
                    "check": "capital_sensitivity_minimum_passing_capital",
                    "passed": True,
                    "value": 600000.0,
                }
            ],
        )
        decision = pd.DataFrame(
            [
                {
                    "decision": "observe_only",
                    "candidate_strategy_name": "robust_recovery",
                    "candidate_output_dir": str(candidate_output),
                    "forward_days": 3,
                    "required_forward_days": 60,
                    "forward_excess_return": -0.0149,
                }
            ]
        )

        recovery_ok, recovery_value, _ = _recovery_candidate_status(decision)
        capital_ok, capital_value, _ = _recovery_candidate_capital_status(decision)

        self.assertTrue(recovery_ok)
        self.assertIn("forward=3/60", recovery_value)
        self.assertIn("excess=-1.49%", recovery_value)
        self.assertTrue(capital_ok)
        self.assertIn("600000.0", capital_value)


    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / "local_operator"
        self.strategy = self.root / "strategy"
        self.live = self.root / "live"
        self.strategy.mkdir(parents=True, exist_ok=True)
        self.live.mkdir(parents=True, exist_ok=True)
        runtime = self.strategy / "local_run_runtime.json"
        if runtime.exists():
            runtime.unlink()
        refresh = self.live / "huatai_read_only_refresh.csv"
        if refresh.exists():
            refresh.unlink()
        deployment = self.live / "live_strategy_evidence_sync_summary.csv"
        if deployment.exists():
            deployment.unlink()
        preview = self.live / "broker_account_import_preview_audit.csv"
        if preview.exists():
            preview.unlink()
        order_preview = self.live / "broker_order_preview_audit.csv"
        if order_preview.exists():
            order_preview.unlink()
        initialization = self.live / "broker_account_initialization_audit.csv"
        if initialization.exists():
            initialization.unlink()
        initialization_provenance = self.live / "broker_account_initialization_provenance.csv"
        if initialization_provenance.exists():
            initialization_provenance.unlink()
        binding = self.live / "auto_live_account_binding_audit.csv"
        if binding.exists():
            binding.unlink()
        capital = self.live / "broker_capital_feasibility_audit.csv"
        if capital.exists():
            capital.unlink()
        canary_capital = self.live / "broker_canary_capital_audit.csv"
        if canary_capital.exists():
            canary_capital.unlink()

    def write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

    def write_fresh_snapshot(self):
        self.write_csv(self.live / "auto_live_broker_account_snapshot.csv", [{
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "account_id": "huatai-test",
        }])

    def configs(self):
        strategy = {"paths": {"output_dir": str(self.strategy)}}
        live = {
            "paths": {"output_dir": str(self.live)},
            "auto_live": {
                "allow_broker_submit": False,
                "emergency_stop": True,
                "desktop_canary_certification": {
                    "certification_path": str(self.live / "cert.json")
                },
            },
        }
        return strategy, live

    def test_required_live_evidence_sync_rejects_missing_or_stale_manifest(self):
        strategy, live = self.configs()
        live["auto_live"]["require_strategy_evidence_sync"] = True
        missing_paths = write_local_operator_status(strategy, live)
        missing = pd.read_csv(missing_paths["local_operator_status.csv"]).set_index("item")
        self.assertFalse(bool(missing.loc["live_strategy_evidence_sync", "passed"]))

        self.write_csv(self.live / "live_strategy_evidence_sync_summary.csv", [{
            "status": "ok",
            "source_config_sha256": mapping_sha256(strategy),
            "live_config_sha256": mapping_sha256(live),
            "backtest_config_sha256": backtest_config_sha256(live),
            "orders_copied": False,
            "positions_copied": False,
            "account_state_copied": False,
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }])
        current_paths = write_local_operator_status(strategy, live)
        current = pd.read_csv(current_paths["local_operator_status.csv"]).set_index("item")
        self.assertTrue(bool(current.loc["live_strategy_evidence_sync", "passed"]))

    def test_status_separates_strategy_and_broker_outputs(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "paper_only_observe"},
        ])
        self.write_csv(self.strategy / "strategy_reliability.csv", [{"item": "reliability_score", "value": 90}])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        self.write_csv(self.live / "huatai_desktop_canary_audit.csv", [
            {"check": "submit", "passed": False},
            {"check": "stop", "passed": False},
            {"check": "evidence", "passed": True},
        ])
        strategy, live = self.configs()
        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")
        self.assertEqual(status.loc["overall_status", "value"], "paper_only")
        self.assertEqual(status.loc["broker_account_audit", "value"], "1/1")
        self.assertEqual(status.loc["broker_order_preview", "value"], "missing")
        self.assertEqual(status.loc["broker_account_initialization", "value"], "missing")
        self.assertEqual(status.loc["canary_arming_audit", "value"], "1/3")
        self.assertTrue(bool(status.loc["safe_default_state", "passed"]))
        self.assertIn("无需 AI", paths["local_operator_status.html"].read_text(encoding="utf-8"))

    def test_status_prioritizes_successor_observation_over_broker_blockers(self):
        candidate_config = "configs/robust.yaml"
        self.write_csv(
            self.strategy / "practical_readiness.csv",
            [{"check": "all", "passed": True}],
        )
        self.write_csv(
            self.strategy / "strategy_health.csv",
            [
                {"check": "paper_orders_allowed", "value": False},
                {"check": "strict_live_allowed", "value": False},
                {"check": "strategy_health_status", "value": "pause_paper_orders"},
            ],
        )
        self.write_csv(
            self.strategy / "strategy_promotion_decision.csv",
            [
                {
                    "candidate_strategy_name": "robust",
                    "decision": "research_only",
                    "candidate_output_dir": str(self.strategy / "candidate"),
                }
            ],
        )
        self.write_csv(
            self.strategy / "primary_challenger_observation.csv",
            [
                {
                    "status": "observed",
                    "candidate_config": candidate_config,
                    "candidate_name": "robust",
                    "backtest_mode": "reused",
                    "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                }
            ],
        )
        strategy, live = self.configs()
        strategy["validation"] = {"primary_challenger_config": candidate_config}
        live["auto_live"]["require_strategy_evidence_sync"] = True

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertEqual(status.loc["strategy_phase", "value"], "workflow_completed")
        self.assertEqual(
            status.loc["strategy_operating_mode", "value"],
            "successor_observation",
        )
        self.assertEqual(status.loc["overall_status", "value"], "observation_only")
        self.assertTrue(bool(status.loc["overall_status", "passed"]))
        self.assertFalse(bool(status.loc["live_strategy_evidence_sync", "passed"]))
        self.assertIn("robust", status.loc["next_action", "value"])
    def test_failed_primary_challenger_is_visible_without_disabling_paper_strategy(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "paper_only_observe"},
        ])
        self.write_csv(self.strategy / "strategy_reliability.csv", [{"item": "reliability_score", "value": 90}])
        self.write_csv(self.strategy / "primary_challenger_observation.csv", [{
            "status": "failed",
            "candidate_name": "candidate",
            "elapsed_seconds": 3.5,
            "message": "candidate backtest failed",
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        strategy, live = self.configs()
        strategy["validation"] = {"primary_challenger_config": "configs/candidate.yaml"}

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertEqual(status.loc["overall_status", "value"], "observation_degraded")
        self.assertFalse(bool(status.loc["primary_challenger_observation", "passed"]))
        self.assertIn("candidate backtest failed", status.loc["primary_challenger_observation", "message"])
        self.assertTrue(bool(status.loc["strategy_health_status", "passed"]))

    def test_current_challenger_with_naive_local_timestamp_is_fresh(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "paper_only_observe"},
        ])
        self.write_csv(self.strategy / "strategy_reliability.csv", [{"item": "reliability_score", "value": 90}])
        self.write_csv(self.strategy / "primary_challenger_observation.csv", [{
            "status": "observed",
            "candidate_config": "configs/current.yaml",
            "candidate_name": "current",
            "backtest_mode": "reused",
            "generated_at": pd.Timestamp.now().isoformat(),
        }])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        strategy, live = self.configs()
        strategy["validation"] = {"primary_challenger_config": "configs/current.yaml"}

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertEqual(status.loc["overall_status", "value"], "paper_only")
        self.assertTrue(bool(status.loc["primary_challenger_observation", "passed"]))
        self.assertIn("backtest=reused", status.loc["primary_challenger_observation", "value"])

    def test_observed_result_for_previous_challenger_is_rejected(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "paper_only_observe"},
        ])
        self.write_csv(self.strategy / "strategy_reliability.csv", [{"item": "reliability_score", "value": 90}])
        self.write_csv(self.strategy / "primary_challenger_observation.csv", [{
            "status": "observed",
            "candidate_config": "configs/previous.yaml",
            "candidate_name": "previous",
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        strategy, live = self.configs()
        strategy["validation"] = {"primary_challenger_config": "configs/current.yaml"}

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertEqual(status.loc["overall_status", "value"], "observation_degraded")
        self.assertIn("config_mismatch", status.loc["primary_challenger_observation", "value"])
        self.assertIn("配置已变更", status.loc["primary_challenger_observation", "message"])

    def test_last_broker_refresh_failure_persists_in_regenerated_status(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "paper_only_observe"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        self.write_csv(self.live / "huatai_read_only_refresh.csv", [{
            "status": "failed",
            "stage": "desktop_snapshot",
            "desktop_stage": "client_process_missing",
            "message": "client is not running",
        }])
        strategy, live = self.configs()

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertEqual(status.loc["overall_status", "value"], "blocked")
        self.assertFalse(bool(status.loc["huatai_read_only_refresh", "passed"]))
        self.assertIn("waiting_for_client", status.loc["huatai_read_only_refresh", "value"])
        self.assertIn("client_process_missing", status.loc["huatai_read_only_refresh", "value"])
        self.assertIn("broker-refresh", status.loc["next_action", "value"])

    def test_trading_page_blocker_is_prioritized_over_halted_strategy(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "cache", "passed": False}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": False},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "pause_paper_orders"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        self.write_csv(self.live / "huatai_client_status.csv", [{
            "status": "trading_process_ready",
            "hexin_process_count": 1,
            "xiadan_process_count": 1,
        }])
        self.write_csv(self.live / "huatai_read_only_refresh.csv", [{
            "status": "failed",
            "stage": "desktop_snapshot",
            "desktop_stage": "trading_page_unavailable",
            "message": "funds grid not visible",
        }])
        strategy, live = self.configs()
        live["auto_live"]["require_strategy_evidence_sync"] = True

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertFalse(bool(status.loc["live_strategy_evidence_sync", "passed"]))
        self.assertIn(status.loc["strategy_operating_mode", "value"], {"strategy_halted", "successor_observation"})
        self.assertIn("资金股票", status.loc["next_action", "value"])
        self.assertIn("broker-refresh", status.loc["next_action", "value"])

    def test_failed_scheduled_observation_is_visible_and_prioritized(self):
        daemon_hash = hashlib.sha256((Path.cwd() / "scripts" / "observation_daemon.ps1").read_bytes()).hexdigest()
        (self.strategy / "observation_daemon_status.json").write_text(json.dumps({
            "status": "waiting",
            "mode": "observation_only_daemon",
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "script_sha256": daemon_hash,
            "state": {},
        }), encoding="utf-8")
        self.write_csv(self.strategy / "daily_observation_summary.csv", [{
            "status": "failed",
            "exit_code": 1,
            "finished_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        strategy, live = self.configs()
        strategy["observation_task"] = {"required": True}

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertFalse(bool(status.loc["scheduled_observation_run", "passed"]))
        self.assertIn("status=failed", status.loc["scheduled_observation_run", "value"])
        self.assertIn("最近一次自动观察任务失败", status.loc["next_action", "value"])
    def test_strategy_blocker_never_becomes_live_ready(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "cache", "passed": False}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": False},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "pause_paper_orders"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        strategy, live = self.configs()
        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")
        self.assertEqual(status.loc["overall_status", "value"], "observation_only")
        self.assertFalse(bool(status.loc["strategy_health_status", "passed"]))

    def test_ready_initialization_preview_requires_separate_authorization(self):
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": True},
            {"check": "strategy_health_status", "value": "strict_live_ready"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "cash", "passed": False}])
        self.write_fresh_snapshot()
        self.write_csv(self.live / "broker_account_initialization_audit.csv", [
            {"check": "broker_account_initialization_ready", "passed": True, "value": "ready_for_explicit_commit"},
        ])
        self.write_csv(self.live / "broker_account_initialization_provenance.csv", [
            {"item": "snapshot_run_id", "value": "snapshot-1"},
            {"item": "account_id", "value": "masked-account"},
        ])
        self.write_csv(self.live / "broker_capital_feasibility_audit.csv", [
            {"check": "broker_capital_feasibility_ready", "passed": True, "value": "coverage=100.00%"},
        ])
        strategy, live = self.configs()
        live["paths"]["state_db"] = str(self.live / "live.sqlite3")

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertEqual(status.loc["broker_account_initialization", "value"], "ready_for_explicit_commit")
        self.assertFalse(bool(status.loc["broker_account_initialization", "passed"]))
        self.assertNotIn("broker-account-init", status.loc["next_action", "value"])
        self.assertIn("不执行账本初始化", status.loc["next_action", "value"])

    def test_authorized_initialization_is_only_shown_after_strategy_and_capital_gates(self):
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": True},
            {"check": "strategy_health_status", "value": "strict_live_ready"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "cash", "passed": False}])
        self.write_fresh_snapshot()
        self.write_csv(self.live / "broker_account_initialization_audit.csv", [
            {"check": "broker_account_initialization_ready", "passed": True, "value": "ready_for_explicit_commit"},
        ])
        self.write_csv(self.live / "broker_account_initialization_provenance.csv", [
            {"item": "snapshot_run_id", "value": "snapshot-1"},
            {"item": "account_id", "value": "masked-account"},
        ])
        self.write_csv(self.live / "broker_capital_feasibility_audit.csv", [
            {"check": "broker_capital_feasibility_ready", "passed": True, "value": "coverage=100.00%"},
        ])
        strategy, live = self.configs()
        live["paths"]["state_db"] = str(self.live / "live.sqlite3")
        live["auto_live"]["allow_live_ledger_initialization"] = True

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertIn("broker-account-init", status.loc["next_action", "value"])
        self.assertIn("--snapshot-run-id snapshot-1", status.loc["next_action", "value"])
        self.assertIn("--account-id masked-account", status.loc["next_action", "value"])
    def test_committed_ledger_with_required_bad_binding_points_to_rebind(self):
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": True},
            {"check": "strategy_health_status", "value": "strict_live_ready"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        self.write_csv(self.live / "broker_account_initialization_audit.csv", [
            {"check": "broker_account_initialization_ready", "passed": True, "value": "ready"},
            {"check": "broker_account_initialization_committed", "passed": True, "value": "snapshot-1"},
        ])
        self.write_csv(self.live / "auto_live_account_binding_audit.csv", [
            {"check": "account_binding_config_match", "passed": False},
        ])
        self.write_csv(self.live / "broker_capital_feasibility_audit.csv", [
            {"check": "broker_capital_feasibility_ready", "passed": True, "value": "coverage=100.00%"},
        ])
        strategy, live = self.configs()
        live["paths"]["state_db"] = str(self.live / "live.sqlite3")
        live["auto_live"]["account_binding"] = {"required": True}

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertFalse(bool(status.loc["broker_account_binding", "passed"]))
        self.assertIn("auto-live-bind-account --confirm", status.loc["next_action", "value"])

    def test_committed_bound_live_ledger_with_insufficient_capital_stays_read_only(self):
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        self.write_csv(self.live / "broker_account_initialization_audit.csv", [
            {"check": "broker_account_initialization_committed", "passed": True, "value": "snapshot-1"},
        ])
        self.write_csv(self.live / "auto_live_account_binding_audit.csv", [
            {"check": "account_binding_config_match", "passed": True},
        ])
        self.write_csv(self.live / "broker_capital_feasibility_audit.csv", [
            {
                "check": "broker_capital_feasibility_ready",
                "passed": False,
                "value": "coverage=0.00%;minimum_equity=200000.00",
            },
        ])
        self.write_csv(self.live / "broker_canary_capital_audit.csv", [
            {
                "check": "broker_canary_capital_ready",
                "passed": True,
                "value": "execution_probe_affordable=True;strategy_portfolio_ready=False",
            },
        ])
        strategy, live = self.configs()
        live["paths"]["state_db"] = str(self.live / "live.sqlite3")
        live["auto_live"]["account_binding"] = {"required": True}

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertFalse(bool(status.loc["broker_capital_feasibility", "passed"]))
        self.assertTrue(bool(status.loc["broker_canary_capital", "passed"]))
        self.assertEqual(status.loc["overall_status", "value"], "blocked")
        self.assertIn("100", status.loc["next_action", "value"])
        self.assertIn("不得生成真实可执行订单", status.loc["next_action", "value"])

    def test_phase_error_blocks_local_runtime(self):
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        strategy, live = self.configs()
        paths = write_local_operator_status(strategy, live, phase_errors={"strategy": "fetch failed"})
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")
        self.assertEqual(status.loc["overall_status", "value"], "blocked")


    def test_stale_broker_snapshot_blocks_runtime_even_when_old_audit_passed(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "paper_only_observe"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.live / "auto_live_broker_account_snapshot.csv", [{
            "generated_at": "2026-01-01T00:00:00+00:00",
            "account_id": "huatai-test",
        }])
        self.write_csv(self.strategy / "capital_mode_assessment.csv", [{
            "mode": "canary_review_only",
            "mode_label_cn": "仅可复核 Canary",
            "snapshot_fresh": True,
            "canary_ready": True,
        }])
        strategy, live = self.configs()
        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")
        self.assertEqual(status.loc["overall_status", "value"], "blocked")
        self.assertFalse(bool(status.loc["broker_snapshot_fresh", "passed"]))
        self.assertEqual(status.loc["capital_operating_mode", "value"], "refresh_required: 需刷新账户")
        self.assertFalse(bool(status.loc["capital_operating_mode", "passed"]))


    def test_failed_launcher_heartbeat_blocks_runtime_status(self):
        self.write_csv(self.strategy / "practical_readiness.csv", [{"check": "all", "passed": True}])
        self.write_csv(self.strategy / "strategy_health.csv", [
            {"check": "paper_orders_allowed", "value": True},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "paper_only_observe"},
        ])
        self.write_csv(self.live / "auto_live_broker_account_audit.csv", [{"check": "all", "passed": True}])
        self.write_fresh_snapshot()
        (self.strategy / "local_run_runtime.json").write_text(
            json.dumps({"status": "failed", "updated_at": pd.Timestamp.now(tz="UTC").isoformat(), "exit_code": 7}),
            encoding="utf-8",
        )
        strategy, live = self.configs()

        paths = write_local_operator_status(strategy, live)
        status = pd.read_csv(paths["local_operator_status.csv"]).set_index("item")

        self.assertEqual(status.loc["overall_status", "value"], "blocked")
        self.assertEqual(status.loc["local_launcher_state", "value"], "failed")
        self.assertFalse(bool(status.loc["local_launcher_state", "passed"]))


if __name__ == "__main__":
    unittest.main()
