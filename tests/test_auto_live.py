import sys
import sqlite3
import shutil
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256, build_auto_live_adapter_audit, build_auto_live_blocker_details, build_auto_live_readiness, build_auto_live_summary, normalize_command_adapter_order_statuses, query_auto_live_orders, run_auto_live, tickets_sha256, write_auto_live_adapter_selftest, write_auto_live_status
from ashare_quant.autolive_confirmation import issue_action_confirmation
from ashare_quant.autolive_cancel import cancel_auto_live_orders


class AutoLiveTests(unittest.TestCase):
    def fresh_output(self, name):
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def write_broker_account_audit(self, output, config):
        pd.DataFrame([{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "severity": "ok", "next_action": "", "message": "ok"}]).to_csv(output / "auto_live_broker_account_audit.csv", index=False)
        pd.DataFrame(
            [
                {
                    "snapshot_run_id": "test-account",
                    "broker": "test-broker",
                    "account_id": "acct-test",
                    "environment": "live",
                    "cash": 1000000.0,
                    "available_cash": 1000000.0,
                    "market_value": 0.0,
                    "total_asset": 1000000.0,
                    "frozen_cash": 0.0,
                    "positions_count": 0,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "auto_live_config_sha256": auto_live_config_sha256(config),
                    "message": "test snapshot",
                }
            ]
        ).to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)

    def test_required_action_confirmation_blocks_missing_token_and_consumes_valid_token(self):
        output = self.fresh_output("auto_live_action_confirmation")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
                "action_confirmation": {"required_before_submit": True, "ttl_seconds": 600},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        tickets = pd.DataFrame(
            [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-13"}]
        )

        with self.assertRaisesRegex(RuntimeError, "action_confirmation_token_missing"):
            run_auto_live(config, tickets=tickets)
        blocked = pd.read_csv(output / "auto_live_run.csv")
        self.assertEqual(blocked.loc[0, "blocked_reason"], "action_confirmation_token_missing")

        confirmation = issue_action_confirmation(
            config,
            tickets_sha256=tickets_sha256(tickets),
            auto_live_config_sha256=auto_live_config_sha256(config),
        )
        paths = run_auto_live(config, tickets=tickets, confirmation_token=confirmation.token)
        run = pd.read_csv(paths["auto_live_run.csv"])
        persisted = pd.read_csv(confirmation.path, dtype=str, keep_default_na=False)
        self.assertEqual(run.loc[0, "status"], "submitted")
        self.assertEqual(persisted.loc[0, "status"], "consumed")

        with self.assertRaisesRegex(RuntimeError, "action_confirmation_already_consumed"):
            run_auto_live(config, tickets=tickets, confirmation_token=confirmation.token)

    def test_blocks_when_execution_and_forward_evidence_are_missing(self):
        output = self.fresh_output("auto_live_blocked")
        config = {"paths": {"output_dir": str(output)}}
        pd.DataFrame(
            [
                {"check": "strict_live_readiness", "passed": False, "severity": "live_blocker"},
                {"check": "account_state_audit", "passed": False, "severity": "execution_blocker"},
            ]
        ).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": False, "status": "blocked", "blockers": "account_state_audit"},
                {"mode": "strict_live_execution", "passed": False, "status": "blocked", "blockers": "strict_live_readiness"},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        readiness = build_auto_live_readiness(config)
        summary = build_auto_live_summary(config, readiness)

        self.assertEqual(summary["status"].iloc[0], "blocked_execution_and_evidence")
        self.assertFalse(bool(summary["auto_live_allowed"].iloc[0]))
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("execution_readiness_all_passed", failed)
        self.assertIn("strict_live_execution_mode", failed)

    def test_manual_ready_still_requires_explicit_auto_live_adapter(self):
        output = self.fresh_output("auto_live_manual")
        config = {"paths": {"output_dir": str(output)}}
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        paths = write_auto_live_status(config)
        summary = pd.read_csv(paths["auto_live_summary.csv"])

        self.assertEqual(summary["status"].iloc[0], "ready_for_manual_review")
        self.assertTrue(bool(summary["manual_ticket_ready"].iloc[0]))
        self.assertFalse(bool(summary["auto_live_allowed"].iloc[0]))

    def test_auto_live_ready_requires_all_gates_and_broker_adapter(self):
        output = self.fresh_output("auto_live_ready")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "mock", "allow_mock_adapter": True, "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "estimated_amount": 1000.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        readiness = build_auto_live_readiness(config)
        summary = build_auto_live_summary(config, readiness)

        self.assertTrue(bool(readiness["passed"].all()))
        self.assertEqual(summary["status"].iloc[0], "auto_live_ready")
        self.assertTrue(bool(summary["auto_live_allowed"].iloc[0]))


    def test_mock_adapter_requires_explicit_mock_allowance(self):
        output = self.fresh_output("auto_live_mock_not_allowed")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "mock", "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        readiness = build_auto_live_readiness(config)
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("mock_adapter_explicitly_allowed", failed)
        summary = build_auto_live_summary(config, readiness)
        self.assertFalse(bool(summary["auto_live_allowed"].iloc[0]))

    def test_unknown_adapter_is_blocked_before_submit(self):
        output = self.fresh_output("auto_live_unknown_adapter")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "unknown", "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        readiness = build_auto_live_readiness(config)
        failed = set(readiness.loc[~readiness["passed"], "check"])
        self.assertIn("broker_adapter_supported", failed)
        with self.assertRaisesRegex(RuntimeError, "auto-live blocked"):
            run_auto_live(config)


    def test_adapter_audit_blocks_missing_or_unknown_real_adapter(self):
        output = self.fresh_output("auto_live_adapter_audit")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "unknown", "allow_broker_submit": True},
        }

        audit = build_auto_live_adapter_audit(config)

        failed = set(audit.loc[~audit["passed"], "check"])
        self.assertIn("adapter_supported", failed)
        self.assertIn("adapter_submit_capability", failed)
        self.assertIn("adapter_config_fingerprint", set(audit["check"]))

    def test_write_auto_live_status_records_adapter_manifest(self):
        output = self.fresh_output("auto_live_manifest")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "mock", "allow_mock_adapter": True, "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        paths = write_auto_live_status(config)
        manifest = pd.read_csv(paths["auto_live_adapter_manifest.csv"])

        self.assertIn("auto_live_adapter_audit.csv", paths)
        self.assertIn("tickets_sha256", set(manifest["item"]))
        self.assertIn("auto_live_config_sha256", set(manifest["item"]))


    def test_blocker_details_include_underlying_execution_failures(self):
        output = self.fresh_output("auto_live_blocker_details")
        config = {"paths": {"output_dir": str(output)}}
        pd.DataFrame(
            [
                {"check": "account_state_audit", "passed": False, "severity": "execution_blocker", "value": "5/7", "next_action": "repair account"},
                {"check": "rpa_pre_trade_checks", "passed": False, "severity": "execution_blocker", "value": "12/13", "next_action": "repair rpa"},
            ]
        ).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": False, "status": "blocked", "blockers": "account_state_audit"},
                {"mode": "rpa_click_fill", "passed": False, "status": "blocked", "blockers": "rpa_dependency_pyautogui"},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        paths = run_auto_live(config, dry_run=True, raise_on_blocked=False)
        details = pd.read_csv(paths["auto_live_blocker_details.csv"])
        summary = pd.read_csv(paths["auto_live_summary.csv"])
        run = pd.read_csv(paths["auto_live_run.csv"])

        self.assertIn("account_state_audit", set(details["check"]))
        self.assertIn("rpa_pre_trade_checks", set(details["check"]))
        self.assertNotIn("rpa_click_fill", set(details["check"]))
        self.assertIn("account_state_audit", summary["blocker_details"].iloc[0])
        self.assertIn("account_state_audit", run["blocked_reason"].iloc[0])

    def test_auto_live_run_blocks_and_writes_audit_when_gates_fail(self):
        output = self.fresh_output("auto_live_run_blocked")
        config = {"paths": {"output_dir": str(output)}}
        pd.DataFrame([{"check": "account_state_audit", "passed": False, "severity": "execution_blocker"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame([{"mode": "manual_ticket_review", "passed": False, "status": "blocked", "blockers": "account_state_audit"}]).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        with self.assertRaisesRegex(RuntimeError, "auto-live blocked"):
            run_auto_live(config)
        run = pd.read_csv(output / "auto_live_run.csv")
        self.assertEqual(run["status"].iloc[0], "blocked")
        self.assertEqual(int(run["submitted_count"].iloc[0]), 0)
        self.assertTrue(str(run["tickets_sha256"].iloc[0]))
        self.assertTrue(str(run["auto_live_config_sha256"].iloc[0]))

    def test_auto_live_run_submits_through_mock_adapter_after_gates_pass(self):
        output = self.fresh_output("auto_live_run_mock")
        state_db = output / "state.sqlite3"
        with sqlite3.connect(state_db) as conn:
            conn.execute("create table account_snapshots(date text primary key, cash real, equity real)")
        config = {
            "paths": {"output_dir": str(output), "state_db": str(state_db)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
                "recovery": {"enabled": True, "required_before_submit": True, "require_hmac": False},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame(
            [
                {"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"},
                {"ticket_id": "t2", "symbol": "000001", "side": "sell", "quantity": 200, "limit_price": 12.0, "status": "ready", "date": "2026-07-10"},
            ]
        ).to_csv(output / "rpa_orders.csv", index=False)

        paths = run_auto_live(config)
        run = pd.read_csv(paths["auto_live_run.csv"])
        results = pd.read_csv(paths["auto_live_order_results.csv"], dtype={"symbol": str})
        ack = pd.read_csv(paths["auto_live_ack_audit.csv"])
        self.assertEqual(run["status"].iloc[0], "submitted")
        self.assertEqual(int(run["submitted_count"].iloc[0]), 2)
        self.assertTrue(str(run["tickets_sha256"].iloc[0]))
        self.assertTrue(str(run["auto_live_config_sha256"].iloc[0]))
        self.assertTrue((results["status"] == "submitted").all())
        self.assertTrue(results["broker_order_id"].str.startswith("mock-").all())
        self.assertTrue(ack["passed"].astype(bool).all())
        ledger = pd.read_csv(output / "auto_live_submission_ledger.csv")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger["status"].iloc[0], "submitted")
        self.assertEqual(ledger["dedupe_key"].iloc[0], run["dedupe_key"].iloc[0])
        recovery = pd.read_csv(output / "auto_live_recovery_points.csv")
        recovery_audit = pd.read_csv(output / "auto_live_recovery_audit.csv")
        self.assertEqual(len(recovery), 1)
        self.assertTrue(recovery_audit["passed"].astype(bool).all())


    def test_recovery_failure_blocks_before_intent_reservation_and_adapter(self):
        output = self.fresh_output("auto_live_recovery_failure")
        state_db = output / "state.sqlite3"
        with sqlite3.connect(state_db) as conn:
            conn.execute("create table account_snapshots(date text primary key, cash real, equity real)")
        config = {
            "paths": {"output_dir": str(output), "state_db": str(state_db)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
                "recovery": {"enabled": True, "required_before_submit": True, "require_hmac": False},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame([
            {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
            {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
        ]).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([
            {"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"},
        ]).to_csv(output / "rpa_orders.csv", index=False)

        with patch("ashare_quant.autolive.create_auto_live_recovery_point", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(RuntimeError, "pre_submit_recovery_failed"):
                run_auto_live(config)

        run = pd.read_csv(output / "auto_live_run.csv")
        intent = pd.read_csv(output / "auto_live_submission_intent.csv")
        reservations = pd.read_csv(output / "auto_live_risk_reservations.csv")
        self.assertEqual(run.loc[0, "blocked_reason"], "pre_submit_recovery_failed")
        self.assertEqual(int(run.loc[0, "submitted_count"]), 0)
        self.assertTrue(intent.empty)
        self.assertTrue(reservations.empty)
        self.assertFalse((output / "auto_live_submission_ledger.csv").exists())

    def test_rollout_recheck_blocks_changed_authorization_before_recovery_and_intent(self):
        output = self.fresh_output("auto_live_rollout_recheck")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "mock", "allow_mock_adapter": True, "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame([
            {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
            {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
        ]).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([
            {"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"},
        ]).to_csv(output / "rpa_orders.csv", index=False)
        calls = []

        def rollout_audit(cfg, tickets=None):
            calls.append(1)
            passed = len(calls) == 1
            audit_path = output / "auto_live_rollout_audit.csv"
            history_path = output / "auto_live_rollout_history.csv"
            pd.DataFrame([{"check": "rollout_approval_unchanged", "passed": passed}]).to_csv(audit_path, index=False)
            if not history_path.exists():
                pd.DataFrame(columns=["action"]).to_csv(history_path, index=False)
            return {"auto_live_rollout_audit.csv": audit_path, "auto_live_rollout_history.csv": history_path}

        with patch("ashare_quant.autolive.write_auto_live_rollout_audit", side_effect=rollout_audit):
            with self.assertRaisesRegex(RuntimeError, "auto_live_rollout_recheck_failed"):
                run_auto_live(config)

        run = pd.read_csv(output / "auto_live_run.csv")
        intent = pd.read_csv(output / "auto_live_submission_intent.csv")
        reservations = pd.read_csv(output / "auto_live_risk_reservations.csv")
        alerts = pd.read_csv(output / "auto_live_alert_outbox.csv")
        self.assertEqual(len(calls), 2)
        self.assertEqual(run.loc[0, "blocked_reason"], "auto_live_rollout_recheck_failed")
        self.assertTrue(intent.empty)
        self.assertTrue(reservations.empty)
        self.assertFalse((output / "auto_live_recovery_points.csv").exists() and not pd.read_csv(output / "auto_live_recovery_points.csv").empty)
        self.assertFalse((output / "auto_live_submission_ledger.csv").exists())
        self.assertIn("rollout_recheck_failed", alerts["event_type"].tolist())

    def test_auto_live_blocks_duplicate_submit_for_same_tickets_and_config(self):
        output = self.fresh_output("auto_live_duplicate_submit")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "mock", "allow_mock_adapter": True, "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        first_paths = run_auto_live(config)
        first_run = pd.read_csv(first_paths["auto_live_run.csv"])
        with self.assertRaisesRegex(RuntimeError, "duplicate_auto_live_submission"):
            run_auto_live(config)
        second_run = pd.read_csv(output / "auto_live_run.csv")
        second_results = pd.read_csv(output / "auto_live_order_results.csv")
        blocked_attempt = pd.read_csv(output / "auto_live_blocked_attempt.csv")
        ledger = pd.read_csv(output / "auto_live_submission_ledger.csv")

        self.assertEqual(first_run["status"].iloc[0], "submitted")
        self.assertEqual(second_run["status"].iloc[0], "submitted")
        self.assertTrue((second_results["status"] == "submitted").all())
        self.assertEqual(blocked_attempt["status"].iloc[0], "blocked")
        self.assertEqual(blocked_attempt["blocked_reason"].iloc[0], "duplicate_auto_live_submission")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger["dedupe_key"].iloc[0], first_run["dedupe_key"].iloc[0])

    def test_auto_live_submit_lock_blocks_concurrent_submit(self):
        output = self.fresh_output("auto_live_submit_lock")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "mock", "allow_mock_adapter": True, "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)
        (output / "auto_live_submit.lock").write_text("manual lock", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "auto_live_submit_lock_exists"):
            run_auto_live(config)
        run = pd.read_csv(output / "auto_live_run.csv")
        results = pd.read_csv(output / "auto_live_order_results.csv")

        self.assertEqual(run["status"].iloc[0], "blocked")
        self.assertEqual(run["blocked_reason"].iloc[0], "auto_live_submit_lock_exists")
        self.assertTrue(results.empty)
        self.assertFalse((output / "auto_live_submission_ledger.csv").exists())
        self.assertTrue((output / "auto_live_submit.lock").exists())

    def test_auto_live_ack_audit_blocks_missing_broker_order_id(self):
        output = self.fresh_output("auto_live_missing_broker_order_id")
        bridge = output / "broker_bridge_missing_id.py"
        bridge.write_text(
            """import json, sys
"""
            """request_path, result_path = sys.argv[1], sys.argv[2]
"""
            """payload = json.load(open(request_path, encoding='utf-8'))
"""
            """if payload.get('mode') == 'self_test':
"""
            """    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}
"""
            """else:
"""
            """    result = {'results': [{'ticket_id': order['ticket_id'], 'status': 'submitted', 'broker_order_id': '', 'message': 'accepted_without_id'} for order in payload['orders']]}
"""
            """json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)
""",
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "allow_outside_trading_window": True,
                "allow_stale_tickets": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        write_auto_live_adapter_selftest(config)
        self.write_broker_account_audit(output, config)
        paths = run_auto_live(config)
        run = pd.read_csv(paths["auto_live_run.csv"])
        ack = pd.read_csv(paths["auto_live_ack_audit.csv"])
        ledger = pd.read_csv(output / "auto_live_submission_ledger.csv")

        self.assertEqual(run["status"].iloc[0], "partial_failed")
        self.assertEqual(run["blocked_reason"].iloc[0], "broker_ack_audit_failed")
        failed = set(ack.loc[~ack["passed"].astype(bool), "check"])
        self.assertIn("submitted_broker_order_ids_present", failed)
        self.assertEqual(ledger["status"].iloc[0], "partial_failed")

    def test_auto_live_ack_audit_blocks_duplicate_broker_order_id(self):
        output = self.fresh_output("auto_live_duplicate_broker_order_id")
        bridge = output / "broker_bridge_duplicate_id.py"
        bridge.write_text(
            """import json, sys
"""
            """request_path, result_path = sys.argv[1], sys.argv[2]
"""
            """payload = json.load(open(request_path, encoding='utf-8'))
"""
            """if payload.get('mode') == 'self_test':
"""
            """    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}
"""
            """else:
"""
            """    result = {'results': [{'ticket_id': order['ticket_id'], 'status': 'submitted', 'broker_order_id': 'same-id', 'message': 'accepted'} for order in payload['orders']]}
"""
            """json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)
""",
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "allow_outside_trading_window": True,
                "allow_stale_tickets": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame(
            [
                {"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"},
                {"ticket_id": "t2", "symbol": "000001", "side": "sell", "quantity": 100, "limit_price": 11.0, "status": "ready", "date": "2026-07-10"},
            ]
        ).to_csv(output / "rpa_orders.csv", index=False)

        write_auto_live_adapter_selftest(config)
        self.write_broker_account_audit(output, config)
        paths = run_auto_live(config)
        run = pd.read_csv(paths["auto_live_run.csv"])
        ack = pd.read_csv(paths["auto_live_ack_audit.csv"])

        self.assertEqual(run["status"].iloc[0], "partial_failed")
        self.assertEqual(run["blocked_reason"].iloc[0], "broker_ack_audit_failed")
        failed = set(ack.loc[~ack["passed"].astype(bool), "check"])
        self.assertIn("broker_order_ids_unique", failed)

    def test_command_adapter_requires_explicit_allowance_and_command(self):
        output = self.fresh_output("auto_live_command_guard")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {"enabled": True, "broker_adapter": "command", "allow_broker_submit": True},
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        readiness = build_auto_live_readiness(config)
        audit = build_auto_live_adapter_audit(config)
        failed_readiness = set(readiness.loc[~readiness["passed"], "check"])
        failed_audit = set(audit.loc[~audit["passed"], "check"])

        self.assertIn("command_adapter_explicitly_allowed", failed_readiness)
        self.assertIn("command_adapter_command_configured", failed_readiness)
        self.assertIn("command_adapter_selftest_passed", failed_readiness)
        self.assertIn("command_adapter_guarded", failed_audit)
        self.assertIn("command_adapter_selftest_passed", failed_audit)
        self.assertIn("adapter_submit_capability", failed_audit)

    def test_command_adapter_selftest_passes_and_binds_current_config(self):
        output = self.fresh_output("auto_live_command_selftest")
        bridge = output / "broker_bridge_selftest.py"
        bridge.write_text(
            """import json, sys\n"""
            """request_path, result_path = sys.argv[1], sys.argv[2]\n"""
            """payload = json.load(open(request_path, encoding='utf-8'))\n"""
            """result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}\n"""
            """json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n""",
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "allow_outside_trading_window": True,
                "allow_stale_tickets": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
            },
        }

        path = write_auto_live_adapter_selftest(config)
        selftest = pd.read_csv(path)
        audit = build_auto_live_adapter_audit(config)

        self.assertTrue(selftest["passed"].astype(bool).all())
        self.assertTrue(bool(audit.loc[audit["check"] == "command_adapter_selftest_passed", "passed"].iloc[0]))
        self.assertTrue(bool(audit.loc[audit["check"] == "adapter_submit_capability", "passed"].iloc[0]))

    def test_auto_live_run_submits_through_command_adapter_after_gates_pass(self):
        output = self.fresh_output("auto_live_run_command")
        bridge = output / "broker_bridge.py"
        bridge.write_text(
            """import json, sys\n"""
            """request_path, result_path = sys.argv[1], sys.argv[2]\n"""
            """payload = json.load(open(request_path, encoding='utf-8'))\n"""
            """if payload.get('mode') == 'self_test':\n"""
            """    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}\n"""
            """else:\n"""
            """    result = {'results': []}\n"""
            """    for order in payload['orders']:\n"""
            """        result['results'].append({'ticket_id': order['ticket_id'], 'status': 'submitted', 'broker_order_id': 'cmd-' + order['ticket_id'], 'message': payload['schema_version']})\n"""
            """json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n""",
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "allow_outside_trading_window": True,
                "allow_stale_tickets": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame(
            [
                {"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"},
                {"ticket_id": "t2", "symbol": "000001", "side": "sell", "quantity": 200, "limit_price": 12.0, "status": "ready", "date": "2026-07-10"},
            ]
        ).to_csv(output / "rpa_orders.csv", index=False)

        write_auto_live_adapter_selftest(config)
        self.write_broker_account_audit(output, config)
        readiness = build_auto_live_readiness(config)
        self.assertTrue(bool(readiness["passed"].all()))
        paths = run_auto_live(config)
        run = pd.read_csv(paths["auto_live_run.csv"])
        results = pd.read_csv(paths["auto_live_order_results.csv"], dtype={"symbol": str})
        ack = pd.read_csv(paths["auto_live_ack_audit.csv"])
        request_files = list(output.glob("auto_live_command_request_*.json"))

        self.assertEqual(run["status"].iloc[0], "submitted")
        self.assertEqual(run["adapter"].iloc[0], "command")
        self.assertEqual(int(run["submitted_count"].iloc[0]), 2)
        self.assertEqual(results["broker_order_id"].tolist(), ["cmd-t2", "cmd-t1"])
        self.assertEqual(results["side"].tolist(), ["sell", "buy"])
        self.assertTrue(request_files)
        self.assertTrue(ack["passed"].astype(bool).all())
        self.assertTrue(results["message"].str.contains("command_adapter.v1", regex=False).all())

    def test_command_submit_timeout_is_uncertain_recoverable_and_not_retryable(self):
        output = self.fresh_output("auto_live_uncertain_recovery")
        bridge = output / "broker_bridge_uncertain.py"
        bridge.write_text(
            "import json, sys, time\n"
            "request_path, result_path = sys.argv[1], sys.argv[2]\n"
            "payload = json.load(open(request_path, encoding='utf-8'))\n"
            "mode = payload.get('mode')\n"
            "if mode == 'self_test':\n"
            "    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}\n"
            "elif mode == 'query_orders':\n"
            "    result = {'results': [{'ticket_id': order['ticket_id'], 'broker_order_id': 'recovered-' + order['ticket_id'], 'broker_status': 'accepted', 'filled_quantity': 0, 'avg_fill_price': 0, 'filled_amount': 0, 'fee': 0, 'message': 'recovered-by-ticket-id'} for order in payload['orders']]}\n"
            "elif mode == 'account_snapshot':\n"
            "    result = {'broker': 'test-broker', 'environment': 'live', 'auto_live_config_sha256': payload['auto_live_config_sha256'], 'account': {'account_id': 'acct-test', 'cash': 1000000, 'available_cash': 1000000, 'market_value': 0, 'total_asset': 1000000, 'frozen_cash': 0}, 'positions': []}\n"
            "else:\n"
            "    time.sleep(2)\n"
            "    result = {'results': []}\n"
            "json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n",
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "allow_outside_trading_window": True,
                "allow_stale_tickets": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 0.5},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)
        write_auto_live_adapter_selftest(config)
        self.write_broker_account_audit(output, config)

        first_paths = run_auto_live(config)
        first_run = pd.read_csv(first_paths["auto_live_run.csv"])
        first_results = pd.read_csv(first_paths["auto_live_order_results.csv"])
        ledger = pd.read_csv(output / "auto_live_submission_ledger.csv")

        self.assertEqual(first_run["status"].iloc[0], "submission_uncertain")
        self.assertEqual(first_run["blocked_reason"].iloc[0], "broker_submission_state_unknown")
        self.assertEqual(first_results["status"].tolist(), ["unknown"])
        self.assertEqual(ledger["status"].tolist(), ["submission_uncertain"])

        query_paths = query_auto_live_orders(config)
        statuses = pd.read_csv(query_paths["auto_live_order_status.csv"], dtype={"symbol": str})
        query_audit = pd.read_csv(query_paths["auto_live_order_status_audit.csv"])
        recovery = pd.read_csv(query_paths["auto_live_submission_recovery.csv"])

        self.assertEqual(statuses["broker_order_id"].tolist(), ["recovered-t1"])
        self.assertEqual(statuses["broker_status"].tolist(), ["accepted"])
        self.assertTrue(query_audit["passed"].astype(bool).all())
        self.assertTrue(recovery["resolved"].astype(bool).all())
        self.assertEqual(recovery["submission_status"].tolist(), ["unknown"])

        with self.assertRaisesRegex(RuntimeError, "duplicate_auto_live_submission"):
            run_auto_live(config)

    def test_auto_live_query_orders_through_command_adapter_after_submit(self):
        output = self.fresh_output("auto_live_query_command")
        bridge = output / "broker_bridge_query.py"
        bridge.write_text(
            """import json, sys
"""
            """request_path, result_path = sys.argv[1], sys.argv[2]
"""
            """payload = json.load(open(request_path, encoding='utf-8'))
"""
            """if payload.get('mode') == 'self_test':
"""
            """    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}
"""
            """elif payload.get('mode') == 'query_orders':
"""
            """    result = {'results': []}
"""
            """    for order in payload['orders']:
"""
            """        result['results'].append({'ticket_id': order['ticket_id'], 'broker_order_id': order['broker_order_id'], 'broker_status': 'accepted', 'filled_quantity': 0, 'avg_fill_price': 0, 'filled_amount': 0, 'fee': 0, 'message': 'query-ok'})
"""
            """else:
"""
            """    result = {'results': []}
"""
            """    for order in payload['orders']:
"""
            """        result['results'].append({'ticket_id': order['ticket_id'], 'status': 'submitted', 'broker_order_id': 'cmd-' + order['ticket_id'], 'message': 'submit-ok'})
"""
            """json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)
""",
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "allow_outside_trading_window": True,
                "allow_stale_tickets": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(output / "rpa_orders.csv", index=False)

        write_auto_live_adapter_selftest(config)
        self.write_broker_account_audit(output, config)
        run_auto_live(config)
        paths = query_auto_live_orders(config)
        statuses = pd.read_csv(paths["auto_live_order_status.csv"], dtype={"symbol": str})
        audit = pd.read_csv(paths["auto_live_order_status_audit.csv"])

        self.assertEqual(statuses["broker_status"].tolist(), ["accepted"])
        self.assertEqual(statuses["broker_order_id"].tolist(), ["cmd-t1"])
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertTrue(list(output.glob("auto_live_query_request_*.json")))

    def test_command_query_duplicate_identifiers_fail_closed(self):
        output = self.fresh_output("auto_live_query_duplicate_identifiers")
        result_path = output / "result.json"
        result_path.write_text(
            '{"results": ['
            '{"ticket_id": "t1", "broker_order_id": "A1", "broker_status": "accepted"},'
            '{"ticket_id": "t1", "broker_order_id": "A2", "broker_status": "accepted"}'
            ']}',
            encoding="utf-8",
        )
        source = pd.DataFrame(
            [{"run_id": "run-1", "ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "unknown", "broker_order_id": ""}]
        )

        statuses = normalize_command_adapter_order_statuses(source, "query-1", result_path)

        self.assertEqual(statuses["broker_status"].tolist(), ["unknown"])
        self.assertIn("duplicate identifiers", statuses.loc[0, "message"])
        self.assertEqual(statuses.loc[0, "broker_order_id"], "")


if __name__ == "__main__":
    unittest.main()
