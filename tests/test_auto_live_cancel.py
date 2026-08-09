import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256, query_auto_live_orders, run_auto_live, write_auto_live_adapter_selftest
from ashare_quant.autolive_cancel import (
    acquire_auto_live_cancel_lock,
    cancel_auto_live_orders,
    cancel_verification_needed,
    clear_auto_live_cancel_lock,
    normalize_command_adapter_cancel_results,
    release_auto_live_cancel_lock,
    verify_auto_live_cancellations,
)


class AutoLiveCancelTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        self.output.mkdir(parents=True, exist_ok=True)
        for child in self.output.glob("*"):
            if child.is_file():
                child.unlink()

    def write_bridge(self) -> Path:
        bridge = self.output / "broker_bridge_cancel.py"
        bridge.write_text(
            """import json, sys\n"""
            """from pathlib import Path\n"""
            """request_path, result_path = sys.argv[1], sys.argv[2]\n"""
            """payload = json.load(open(request_path, encoding='utf-8'))\n"""
            """cancel_seen = Path(__file__).with_name('cancel_seen.flag')\n"""
            """if payload.get('mode') == 'self_test':\n"""
            """    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}\n"""
            """elif payload.get('mode') == 'query_orders':\n"""
            """    result = {'results': []}\n"""
            """    for order in payload['orders']:\n"""
            """        broker_status = 'canceled' if cancel_seen.exists() else 'accepted'\n"""
            """        result['results'].append({'ticket_id': order['ticket_id'], 'broker_order_id': order['broker_order_id'], 'broker_status': broker_status, 'filled_quantity': 0, 'avg_fill_price': 0, 'filled_amount': 0, 'fee': 0, 'message': 'query-ok'})\n"""
            """elif payload.get('mode') == 'cancel_orders':\n"""
            """    cancel_seen.write_text('1', encoding='utf-8')\n"""
            """    result = {'results': []}\n"""
            """    for order in payload['orders']:\n"""
            """        result['results'].append({'ticket_id': order['ticket_id'], 'broker_order_id': order['broker_order_id'], 'cancel_status': 'cancel_requested', 'message': 'cancel-ok'})\n"""
            """else:\n"""
            """    result = {'results': []}\n"""
            """    for order in payload['orders']:\n"""
            """        result['results'].append({'ticket_id': order['ticket_id'], 'status': 'submitted', 'broker_order_id': 'cmd-' + order['ticket_id'], 'message': 'submit-ok'})\n"""
            """json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n""",
            encoding="utf-8",
        )
        return bridge

    def config(self, bridge: Path) -> dict:
        return {
            "paths": {"output_dir": str(self.output)},
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

    def write_gates(self, config):
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(self.output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(self.output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(self.output / "rpa_orders.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "severity": "ok", "next_action": "", "message": "ok"}]).to_csv(self.output / "auto_live_broker_account_audit.csv", index=False)
        pd.DataFrame([{"snapshot_run_id": "test-account", "broker": "test-broker", "account_id": "acct-test", "environment": "live", "available_cash": 1000000.0, "total_asset": 1000000.0, "generated_at": datetime.now().isoformat(timespec="seconds"), "auto_live_config_sha256": auto_live_config_sha256(config)}]).to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)

    def test_cancel_orders_through_command_adapter_after_query(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_gates(config)

        write_auto_live_adapter_selftest(config)
        run_auto_live(config)
        query_auto_live_orders(config)
        paths = cancel_auto_live_orders(config, raise_on_blocked=False)
        cancels = pd.read_csv(paths["auto_live_cancel_results.csv"], dtype={"symbol": str})
        audit = pd.read_csv(paths["auto_live_cancel_audit.csv"])

        self.assertEqual(cancels["cancel_status"].tolist(), ["cancel_requested"])
        self.assertEqual(cancels["broker_order_id"].tolist(), ["cmd-t1"])
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertTrue(bool(audit.loc[audit["check"] == "cancel_request_acknowledged", "passed"].iloc[0]))
        self.assertTrue(list(self.output.glob("auto_live_cancel_request_*.json")))
        self.assertTrue(list(self.output.glob("auto_live_cancel_results_*.csv")))

        self.assertTrue(cancel_verification_needed(self.output))
        pending_paths = verify_auto_live_cancellations(config, raise_on_blocked=False)
        pending_audit = pd.read_csv(pending_paths["auto_live_cancel_verification_audit.csv"])
        self.assertFalse(pending_audit["passed"].astype(bool).all())

        second_paths = cancel_auto_live_orders(config, raise_on_blocked=False)
        second_results = pd.read_csv(second_paths["auto_live_cancel_results.csv"])
        second_audit = pd.read_csv(second_paths["auto_live_cancel_audit.csv"])
        source_check = second_audit.loc[second_audit["check"] == "cancel_source_preflight"].iloc[0]
        self.assertEqual(second_results["cancel_status"].tolist(), ["cancel_requested"])
        self.assertFalse(bool(source_check["passed"]))
        self.assertTrue(str(source_check["value"]).startswith("cancel_source_query_already_used:"))

        query_auto_live_orders(config)
        verification_paths = verify_auto_live_cancellations(config)
        verification = pd.read_csv(verification_paths["auto_live_cancel_verification.csv"])
        verification_audit = pd.read_csv(verification_paths["auto_live_cancel_verification_audit.csv"])
        self.assertEqual(verification["broker_status"].tolist(), ["canceled"])
        self.assertTrue(verification["terminal"].astype(bool).all())
        self.assertTrue(verification_audit["passed"].astype(bool).all())
        self.assertFalse(cancel_verification_needed(self.output))

    def test_existing_cancel_lock_prevents_adapter_invocation(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_gates(config)
        write_auto_live_adapter_selftest(config)
        run_auto_live(config)
        query_auto_live_orders(config)
        statuses = pd.read_csv(self.output / "auto_live_order_status.csv")
        source_query_run_id = str(statuses.loc[0, "query_run_id"])
        lock_path = acquire_auto_live_cancel_lock(self.output, "20260801190000000000", source_query_run_id)
        try:
            with patch("ashare_quant.autolive_cancel.cancel_with_command_adapter") as adapter_call:
                paths = cancel_auto_live_orders(config, raise_on_blocked=False)
            adapter_call.assert_not_called()
            audit = pd.read_csv(paths["auto_live_cancel_audit.csv"])
            source_check = audit.loc[audit["check"] == "cancel_source_preflight"].iloc[0]
            self.assertEqual(source_check["value"], "cancel_lock_active")
        finally:
            release_auto_live_cancel_lock(lock_path)

    def test_cancel_lock_clear_requires_confirmation_and_fresh_query(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_gates(config)
        write_auto_live_adapter_selftest(config)
        run_auto_live(config)
        query_auto_live_orders(config)
        lock_path = acquire_auto_live_cancel_lock(
            self.output,
            "20200101000000000000",
            "20191231000000000000",
        )

        with self.assertRaisesRegex(RuntimeError, "explicit --confirm required"):
            clear_auto_live_cancel_lock(config, confirm=False)
        self.assertTrue(lock_path.exists())

        audit_path = clear_auto_live_cancel_lock(config, confirm=True)
        audit = pd.read_csv(audit_path)
        self.assertFalse(lock_path.exists())
        self.assertTrue(audit["passed"].astype(bool).all())

    def test_cancel_lock_clear_blocks_when_persisted_request_is_still_cancelable(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_gates(config)
        write_auto_live_adapter_selftest(config)
        run_auto_live(config)
        query_auto_live_orders(config)
        cancel_run_id = "20200101000000000000"
        lock_path = acquire_auto_live_cancel_lock(
            self.output,
            cancel_run_id,
            "20191231000000000000",
        )
        (self.output / f"auto_live_cancel_request_{cancel_run_id}.json").write_text(
            json.dumps({"mode": "cancel_orders", "run_id": cancel_run_id}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "recovery audit failed"):
            clear_auto_live_cancel_lock(config, confirm=True)

        self.assertTrue(lock_path.exists())
        audit = pd.read_csv(self.output / "auto_live_cancel_lock_audit.csv")
        request_check = audit.loc[audit["check"] == "cancel_lock_request_state_safe"].iloc[0]
        self.assertFalse(bool(request_check["passed"]))
        release_auto_live_cancel_lock(lock_path)

    def test_failed_status_audit_prevents_adapter_invocation(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        pd.DataFrame(
            [
                {
                    "query_run_id": "q1",
                    "source_run_id": "s1",
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "broker_order_id": "b1",
                    "broker_status": "accepted",
                }
            ]
        ).to_csv(self.output / "auto_live_order_status.csv", index=False)
        pd.DataFrame([{"check": "query_source_integrity", "passed": False}]).to_csv(
            self.output / "auto_live_order_status_audit.csv", index=False
        )

        with (
            patch("ashare_quant.autolive_cancel.auto_live_query_adapter_ready", return_value=(True, "ready")),
            patch("ashare_quant.autolive_cancel.cancel_with_command_adapter") as adapter_call,
        ):
            paths = cancel_auto_live_orders(config, raise_on_blocked=False)

        adapter_call.assert_not_called()
        audit = pd.read_csv(paths["auto_live_cancel_audit.csv"])
        source_check = audit.loc[audit["check"] == "cancel_source_preflight"].iloc[0]
        self.assertFalse(bool(source_check["passed"]))

    def test_status_file_tamper_prevents_cancel_adapter_invocation(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_gates(config)
        write_auto_live_adapter_selftest(config)
        run_auto_live(config)
        query_auto_live_orders(config)
        statuses_path = self.output / "auto_live_order_status.csv"
        statuses = pd.read_csv(statuses_path)
        statuses.loc[0, "broker_order_id"] = "tampered-order-id"
        statuses.to_csv(statuses_path, index=False)

        with patch("ashare_quant.autolive_cancel.cancel_with_command_adapter") as adapter_call:
            paths = cancel_auto_live_orders(config, raise_on_blocked=False)

        adapter_call.assert_not_called()
        audit = pd.read_csv(paths["auto_live_cancel_audit.csv"])
        source_check = audit.loc[audit["check"] == "cancel_source_preflight"].iloc[0]
        self.assertFalse(bool(source_check["passed"]))
        self.assertEqual(source_check["value"], "order_status_file_sha256_mismatch")

    def test_duplicate_cancel_result_identifiers_fail_closed(self):
        source = pd.DataFrame(
            [
                {"source_run_id": "s1", "ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "broker_order_id": "b1"},
                {"source_run_id": "s1", "ticket_id": "t2", "symbol": "000001", "side": "buy", "quantity": 100, "broker_order_id": "b2"},
            ]
        )
        result_path = self.output / "duplicate_cancel_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "results": [
                        {"ticket_id": "t1", "broker_order_id": "b1", "cancel_status": "canceled"},
                        {"ticket_id": "t1", "broker_order_id": "b2", "cancel_status": "canceled"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        results = normalize_command_adapter_cancel_results(source, "c1", result_path)

        self.assertEqual(results["cancel_status"].tolist(), ["unknown", "unknown"])
        self.assertTrue(results["message"].str.contains("duplicate identifiers").all())

    def test_cross_wired_cancel_result_identity_fails_closed(self):
        source = pd.DataFrame(
            [
                {"source_run_id": "s1", "ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "broker_order_id": "b1"},
                {"source_run_id": "s1", "ticket_id": "t2", "symbol": "000001", "side": "buy", "quantity": 100, "broker_order_id": "b2"},
            ]
        )
        result_path = self.output / "cross_wired_cancel_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "results": [
                        {"ticket_id": "t1", "broker_order_id": "b2", "cancel_status": "canceled"},
                        {"ticket_id": "t2", "broker_order_id": "b1", "cancel_status": "canceled"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        results = normalize_command_adapter_cancel_results(source, "c1", result_path)

        self.assertEqual(results["cancel_status"].tolist(), ["unknown", "unknown"])
        self.assertTrue(results["message"].str.contains("identities do not match").all())


if __name__ == "__main__":
    unittest.main()
