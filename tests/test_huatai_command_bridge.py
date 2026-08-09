import importlib.util
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256, build_auto_live_adapter_audit, query_auto_live_orders, run_auto_live, write_auto_live_adapter_selftest
from ashare_quant.autolive_cancel import cancel_auto_live_orders
from ashare_quant.autolive_account import query_auto_live_account
from ashare_quant.autolive_replay import write_auto_live_adapter_replay_audit
from ashare_quant.autolive_integrity import attach_request_integrity, verify_adapter_result_integrity

SCRIPT = Path.cwd() / "scripts" / "huatai_command_bridge.py"
CONFIG = Path.cwd() / "configs" / "huatai_bridge_template.yaml"
SCHEMA_VERSION = "ashare_quant.command_adapter.v1"
BRIDGE_SPEC = importlib.util.spec_from_file_location("huatai_command_bridge_test_module", SCRIPT)
BRIDGE_MODULE = importlib.util.module_from_spec(BRIDGE_SPEC)
assert BRIDGE_SPEC.loader is not None
BRIDGE_SPEC.loader.exec_module(BRIDGE_MODULE)


class HuataiCommandBridgeTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        if self.output.exists():
            for child in self.output.glob("*"):
                if child.is_file():
                    child.unlink()
        self.output.mkdir(parents=True, exist_ok=True)

    def run_bridge(self, payload, *extra_args):
        request = self.output / "request.json"
        result = self.output / "result.json"
        payload = dict(payload)
        payload.setdefault("result_path", str(result))
        request.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = subprocess.run([sys.executable, str(SCRIPT), "--config", str(CONFIG), *extra_args, str(request), str(result)], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        return json.loads(result.read_text(encoding="utf-8"))

    def run_bridge_with_config(self, payload, config_path, *extra_args):
        request = self.output / "request-custom.json"
        result = self.output / "result-custom.json"
        payload = dict(payload)
        payload.setdefault("result_path", str(result))
        request.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(config_path), *extra_args, str(request), str(result)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        return json.loads(result.read_text(encoding="utf-8"))

    def test_desktop_canary_bridge_requires_bound_certification_before_driver(self):
        config_path = self.output / "desktop-canary.json"
        config_path.write_text(
            json.dumps(
                {
                    "huatai": {
                        "broker": "huatai",
                        "simulate": False,
                        "live_backend": "desktop_canary",
                        "desktop_canary": {
                            "enabled": True,
                            "allow_submit": True,
                            "max_orders": 1,
                            "max_order_amount": 1000,
                            "certification_path": str(self.output / "missing-certification.json"),
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "submit_orders",
            "run_id": "desktop-canary-blocked",
            "auto_live_config_sha256": "config-a",
            "orders": [
                {
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 9.99,
                }
            ],
        }
        result = self.run_bridge_with_config(payload, config_path)

        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertIn("certification", result["results"][0]["message"])
        self.assertEqual(result["results"][0]["broker_order_id"], "")

    def test_armed_desktop_canary_is_consumed_once_but_allows_query_and_cancel(self):
        certification = self.output / "certification.json"
        certification.write_text(
            json.dumps(
                {
                    "schema_version": "ashare_quant.huatai_desktop_canary_certification.v1",
                    "status": "armed",
                    "auto_live_config_sha256": "config-a",
                    "submit_form_preview": True,
                    "empty_order_monitor": True,
                    "account_snapshot_live": True,
                    "operator_confirmation_required": True,
                    "max_orders": 1,
                    "max_order_amount": 1000,
                    "submit_contract": True,
                    "query_contract": True,
                    "cancel_contract": True,
                    "nonempty_order_parser": True,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        config = {
            "huatai": {
                "live_backend": "desktop_canary",
                "desktop_canary": {
                    "enabled": True,
                    "allow_submit": True,
                    "max_orders": 1,
                    "max_order_amount": 1000,
                    "certification_path": str(certification),
                    "required_certification_stage": "armed",
                },
            }
        }

        class Driver:
            def runtime_selftest(self):
                return True, "ready"

            def submit(self, order, *, run_id):
                from ashare_quant.huatai_canary import BrokerSubmission

                return BrokerSubmission("submitted", "A123", "accepted")

            def query(self, order):
                return {
                    "broker_order_id": "A123",
                    "broker_status": "partially_filled",
                    "filled_quantity": 40,
                    "avg_fill_price": 10.0,
                    "filled_amount": 401.25,
                    "fee": 2.5,
                    "fee_status": "exact",
                    "fee_source": "huatai_trade_grid_total_fee",
                    "trade_date": "2026-07-15",
                    "message": "partially filled",
                }

            def cancel(self, order):
                return {"cancel_status": "cancel_requested", "message": "requested"}

        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": "run-live",
            "auto_live_config_sha256": "config-a",
            "orders": [
                {
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 9.99,
                    "broker_order_id": "A123",
                }
            ],
        }
        submit = BRIDGE_MODULE.submit_response(payload, False, config, driver_factory=Driver)
        query = BRIDGE_MODULE.query_response(payload, False, config, driver_factory=Driver)
        cancel = BRIDGE_MODULE.cancel_response(payload, False, config, driver_factory=Driver)

        self.assertEqual(submit["results"][0]["status"], "submitted")
        self.assertEqual(submit["results"][0]["broker_order_id"], "A123")
        self.assertEqual(query["results"][0]["broker_status"], "partially_filled")
        self.assertEqual(query["results"][0]["filled_amount"], 401.25)
        self.assertEqual(query["results"][0]["fee_status"], "exact")
        self.assertEqual(query["results"][0]["trade_date"], "2026-07-15")
        self.assertEqual(cancel["results"][0]["cancel_status"], "cancel_requested")
        consumed = json.loads(certification.read_text(encoding="utf-8"))
        self.assertEqual(consumed["status"], "consumed")
        self.assertEqual(consumed["consumed_run_id"], "run-live")
        second = BRIDGE_MODULE.submit_response(payload, False, config, driver_factory=Driver)
        self.assertEqual(second["results"][0]["status"], "failed")
        self.assertIn("already_consumed", second["results"][0]["message"])

    def test_desktop_canary_runtime_selftest_blocks_without_consuming_arm(self):
        certification = self.output / "certification-selftest.json"
        certification.write_text(
            json.dumps(
                {
                    "schema_version": "ashare_quant.huatai_desktop_canary_certification.v1",
                    "status": "armed",
                    "auto_live_config_sha256": "config-a",
                    "submit_form_preview": True,
                    "empty_order_monitor": True,
                    "account_snapshot_live": True,
                    "operator_confirmation_required": True,
                    "max_orders": 1,
                    "max_order_amount": 1000,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        config = {
            "huatai": {
                "live_backend": "desktop_canary",
                "desktop_canary": {
                    "enabled": True,
                    "allow_submit": True,
                    "max_orders": 1,
                    "max_order_amount": 1000,
                    "certification_path": str(certification),
                    "required_certification_stage": "armed",
                },
            }
        }

        class Driver:
            def runtime_selftest(self):
                return False, "order page contract changed"

            def submit(self, order, *, run_id):
                raise AssertionError("submit must not be called")

        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": "run-preflight-failed",
            "auto_live_config_sha256": "config-a",
            "orders": [
                {
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 9.99,
                }
            ],
        }

        result = BRIDGE_MODULE.submit_response(payload, False, config, driver_factory=Driver)

        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertIn("runtime self-test failed before arm consumption", result["results"][0]["message"])
        self.assertEqual(json.loads(certification.read_text(encoding="utf-8"))["status"], "armed")

    def test_live_fill_fee_provenance_fails_closed_until_exact(self):
        unavailable = BRIDGE_MODULE.normalized_fee_fields({}, 100)
        exact = BRIDGE_MODULE.normalized_fee_fields(
            {"fee": 5.0, "fee_status": "exact", "fee_source": "broker_trade_ledger"}, 100
        )
        no_fill = BRIDGE_MODULE.normalized_fee_fields({}, 0)

        self.assertEqual(unavailable, (0.0, "unavailable", "huatai_order_grid"))
        self.assertEqual(exact, (5.0, "exact", "broker_trade_ledger"))
        self.assertEqual(no_fill, (0.0, "not_applicable", "none"))

    def test_default_selftest_fails_closed(self):
        result = self.run_bridge({"schema_version": SCHEMA_VERSION, "mode": "self_test", "run_id": "h1", "auto_live_config_sha256": "abc", "orders": []})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["broker"], "huatai")
        self.assertNotIn("submit_orders", result.get("capabilities", []))

    def test_unarmed_desktop_canary_selftest_keeps_only_read_only_capability(self):
        config_path = self.output / "desktop-canary.yaml"
        config_path.write_text(
            json.dumps({
                "huatai": {
                    "broker": "huatai",
                    "live_backend": "desktop_canary",
                    "desktop_snapshot_path": str(self.output / "snapshot.json"),
                    "desktop_canary": {
                        "enabled": True,
                        "allow_submit": True,
                        "certification_path": str(self.output / "missing-certification.json"),
                        "required_certification_stage": "armed",
                    },
                }
            }),
            encoding="utf-8",
        )
        result = self.run_bridge_with_config(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": "self_test",
                "run_id": "desktop-unarmed",
                "auto_live_config_sha256": "config-a",
                "orders": [],
            },
            config_path,
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["capabilities"], ["account_snapshot"])
        self.assertNotIn("submit_orders", result["capabilities"])
        self.assertIn("trading capabilities remain disabled", result["message"])

    def test_simulated_selftest_declares_submit_query_and_cancel(self):
        result = self.run_bridge({"schema_version": SCHEMA_VERSION, "mode": "self_test", "run_id": "h2", "auto_live_config_sha256": "abc", "orders": []}, "--simulate")

        self.assertEqual(result["status"], "ok")
        self.assertIn("submit_orders", result["capabilities"])
        self.assertIn("query_orders", result["capabilities"])
        self.assertIn("cancel_orders", result["capabilities"])
        self.assertIn("account_snapshot", result["capabilities"])

    def test_simulated_bridge_verifies_and_returns_request_bound_hmac(self):
        config = {
            "auto_live": {
                "command_adapter": {
                    "require_hmac_integrity": True,
                    "integrity_key_env": "TEST_HUATAI_HMAC_KEY",
                }
            }
        }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "self_test",
            "run_id": "hmac-selftest",
            "auto_live_config_sha256": "abc",
            "result_path": str(self.output / "result.json"),
            "orders": [],
        }
        with patch.dict(os.environ, {"TEST_HUATAI_HMAC_KEY": "h" * 40}, clear=False):
            request = attach_request_integrity(config, payload)
            result = self.run_bridge(request, "--simulate")
            passed, reason = verify_adapter_result_integrity(config, request, result)

        self.assertTrue(passed, reason)
        self.assertTrue(result["integrity"]["hmac_sha256"])

    def test_simulated_submit_query_and_cancel(self):
        submit = self.run_bridge(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": "submit_orders",
                "run_id": "h3",
                "auto_live_config_sha256": "abc",
                "orders": [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0}],
            },
            "--simulate",
        )
        query = self.run_bridge(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": "query_orders",
                "run_id": "h4",
                "auto_live_config_sha256": "abc",
                "orders": [{"ticket_id": "t1", "broker_order_id": submit["results"][0]["broker_order_id"]}],
            },
            "--simulate",
        )

        self.assertEqual(submit["results"][0]["status"], "submitted")
        self.assertEqual(submit["results"][0]["broker_order_id"], "huatai-sim-t1")
        account = self.run_bridge(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": "account_snapshot",
                "run_id": "h-account",
                "auto_live_config_sha256": "abc",
                "orders": [],
            },
            "--simulate",
        )
        cancel = self.run_bridge(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": "cancel_orders",
                "run_id": "h5",
                "auto_live_config_sha256": "abc",
                "orders": [{"ticket_id": "t1", "broker_order_id": submit["results"][0]["broker_order_id"]}],
            },
            "--simulate",
        )

        self.assertEqual(account["account"]["cash"], 1000000.0)
        self.assertEqual(account["environment"], "simulation")
        self.assertEqual(query["results"][0]["broker_status"], "accepted")
        self.assertEqual(cancel["results"][0]["cancel_status"], "cancel_requested")

    def test_simulated_submit_honors_request_emergency_stop_path(self):
        stop_path = self.output / "auto_live.stop"
        stop_path.write_text("active", encoding="utf-8")
        result = self.run_bridge(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": "submit_orders",
                "run_id": "h-stop",
                "auto_live_config_sha256": "abc",
                "emergency_stop_path": str(stop_path),
                "orders": [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0}],
            },
            "--simulate",
        )
        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertEqual(result["results"][0]["broker_order_id"], "")
        self.assertIn("Emergency stop", result["results"][0]["message"])

    def test_system_selftest_and_query_can_use_huatai_simulation(self):
        config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "allow_simulated_account_snapshot": True,
                "allow_outside_trading_window": True,
                "allow_stale_tickets": True,
                "command_adapter": {"command": [sys.executable, str(SCRIPT), "--config", str(CONFIG), "--simulate"], "timeout_seconds": 10},
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(self.output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(self.output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]).to_csv(self.output / "rpa_orders.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "severity": "ok", "next_action": "", "message": "ok"}]).to_csv(self.output / "auto_live_broker_account_audit.csv", index=False)
        pd.DataFrame([{"snapshot_run_id": "huatai-test", "broker": "huatai", "account_id": "huatai-sim-account", "environment": "simulation", "available_cash": 1000000.0, "total_asset": 1000000.0, "generated_at": datetime.now().isoformat(timespec="seconds"), "auto_live_config_sha256": auto_live_config_sha256(config)}]).to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)

        selftest_path = write_auto_live_adapter_selftest(config)
        audit = build_auto_live_adapter_audit(config)
        run_auto_live(config)
        query_paths = query_auto_live_orders(config)
        cancel_paths = cancel_auto_live_orders(config)
        query_auto_live_account(config, raise_on_blocked=False)
        replay_paths = write_auto_live_adapter_replay_audit(config)
        selftest = pd.read_csv(selftest_path)
        statuses = pd.read_csv(query_paths["auto_live_order_status.csv"], dtype={"symbol": str})
        status_audit = pd.read_csv(query_paths["auto_live_order_status_audit.csv"])
        cancels = pd.read_csv(cancel_paths["auto_live_cancel_results.csv"], dtype={"symbol": str})
        cancel_audit = pd.read_csv(cancel_paths["auto_live_cancel_audit.csv"])
        replay = pd.read_csv(replay_paths["auto_live_adapter_replay_summary.csv"])

        self.assertTrue(selftest["passed"].astype(bool).all())
        self.assertTrue(bool(audit.loc[audit["check"] == "command_adapter_selftest_passed", "passed"].iloc[0]))
        self.assertEqual(statuses["broker_order_id"].tolist(), ["huatai-sim-t1"])
        self.assertEqual(statuses["broker_status"].tolist(), ["accepted"])
        self.assertTrue(status_audit["passed"].astype(bool).all())
        self.assertEqual(cancels["cancel_status"].tolist(), ["cancel_requested"])
        self.assertTrue(cancel_audit["passed"].astype(bool).all())
        self.assertEqual(replay.loc[0, "status"], "certified_simulation")


if __name__ == "__main__":
    unittest.main()
