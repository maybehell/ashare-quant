import json
import subprocess
import sys
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import build_auto_live_adapter_audit, write_auto_live_adapter_selftest

SCRIPT = Path.cwd() / "scripts" / "command_broker_bridge_template.py"
SCHEMA_VERSION = "ashare_quant.command_adapter.v1"


class CommandBrokerBridgeTemplateTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        self.output.mkdir(parents=True, exist_ok=True)

    def run_bridge(self, payload, *extra_args):
        request = self.output / "request.json"
        result = self.output / "result.json"
        payload = dict(payload)
        payload.setdefault("result_path", str(result))
        request.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *extra_args, str(request), str(result)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        return json.loads(result.read_text(encoding="utf-8"))

    def test_default_selftest_reports_not_connected(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "self_test",
            "run_id": "selftest-1",
            "auto_live_config_sha256": "abc123",
            "orders": [],
        }

        result = self.run_bridge(payload)

        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        self.assertEqual(result["run_id"], "selftest-1")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("submit_orders", result.get("capabilities", []))
        self.assertIn("not connected", result["message"])

    def test_simulated_selftest_declares_required_capabilities(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "self_test",
            "run_id": "selftest-2",
            "auto_live_config_sha256": "def456",
            "orders": [],
        }

        result = self.run_bridge(payload, "--simulate-submit")

        self.assertEqual(result["status"], "ok")
        self.assertIn("submit_orders", result["capabilities"])
        self.assertIn("query_orders", result["capabilities"])
        self.assertIn("cancel_orders", result["capabilities"])
        self.assertIn("account_snapshot", result["capabilities"])
        self.assertEqual(result["auto_live_config_sha256"], "def456")

    def test_simulated_submit_returns_per_ticket_results(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "submit_orders",
            "run_id": "submit-1",
            "auto_live_config_sha256": "abc123",
            "orders": [
                {"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0},
                {"ticket_id": "t2", "symbol": "000001", "side": "sell", "quantity": 200, "limit_price": 12.0},
            ],
        }

        result = self.run_bridge(payload, "--simulate-submit")

        self.assertEqual(result["run_id"], "submit-1")
        self.assertEqual([item["status"] for item in result["results"]], ["submitted", "submitted"])
        self.assertEqual([item["broker_order_id"] for item in result["results"]], ["sim-t1", "sim-t2"])
        self.assertTrue(all("Simulation only" in item["message"] for item in result["results"]))

    def test_simulated_submit_honors_request_emergency_stop_path(self):
        stop_path = self.output / "auto_live.stop"
        stop_path.write_text("active", encoding="utf-8")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "submit_orders",
            "run_id": "submit-stop",
            "auto_live_config_sha256": "abc123",
            "emergency_stop_path": str(stop_path),
            "orders": [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0}],
        }

        result = self.run_bridge(payload, "--simulate-submit")
        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertEqual(result["results"][0]["broker_order_id"], "")
        self.assertIn("Emergency stop", result["results"][0]["message"])

    def test_simulated_query_returns_per_ticket_statuses(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "query_orders",
            "run_id": "query-1",
            "auto_live_config_sha256": "abc123",
            "orders": [
                {"ticket_id": "t1", "broker_order_id": "sim-t1"},
                {"ticket_id": "t2", "broker_order_id": "sim-t2"},
            ],
        }

        result = self.run_bridge(payload, "--simulate-submit")

        self.assertEqual(result["run_id"], "query-1")
        self.assertEqual([item["broker_status"] for item in result["results"]], ["accepted", "accepted"])
        self.assertEqual([item["broker_order_id"] for item in result["results"]], ["sim-t1", "sim-t2"])
        self.assertTrue(all(item["filled_quantity"] == 0 for item in result["results"]))


    def test_simulated_cancel_returns_per_ticket_statuses(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "cancel_orders",
            "run_id": "cancel-1",
            "auto_live_config_sha256": "abc123",
            "orders": [
                {"ticket_id": "t1", "broker_order_id": "sim-t1"},
                {"ticket_id": "t2", "broker_order_id": "sim-t2"},
            ],
        }

        result = self.run_bridge(payload, "--simulate-submit")

        self.assertEqual(result["run_id"], "cancel-1")
        self.assertEqual([item["cancel_status"] for item in result["results"]], ["cancel_requested", "cancel_requested"])
        self.assertEqual([item["broker_order_id"] for item in result["results"]], ["sim-t1", "sim-t2"])


    def test_simulated_account_snapshot_returns_cash_and_positions(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "account_snapshot",
            "run_id": "account-1",
            "auto_live_config_sha256": "abc123",
            "orders": [],
        }

        result = self.run_bridge(payload, "--simulate-submit")

        self.assertEqual(result["run_id"], "account-1")
        self.assertEqual(result["account"]["cash"], 1000000.0)
        self.assertEqual(result["environment"], "simulation")
        self.assertEqual(result["positions"], [])

    def test_default_submit_fails_each_ticket(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": "submit_orders",
            "run_id": "submit-2",
            "auto_live_config_sha256": "abc123",
            "orders": [{"ticket_id": "t1"}],
        }

        result = self.run_bridge(payload)

        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertEqual(result["results"][0]["broker_order_id"], "")
        self.assertIn("not connected", result["results"][0]["message"])

    def test_template_can_pass_system_command_adapter_selftest_in_simulation_mode(self):
        config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "command_adapter": {
                    "command": [sys.executable, str(SCRIPT), "--simulate-submit"],
                    "timeout_seconds": 10,
                },
            },
        }

        path = write_auto_live_adapter_selftest(config)
        selftest = pd.read_csv(path)
        audit = build_auto_live_adapter_audit(config)

        self.assertTrue(selftest["passed"].astype(bool).all())
        self.assertTrue(bool(audit.loc[audit["check"] == "command_adapter_selftest_passed", "passed"].iloc[0]))
        self.assertTrue(bool(audit.loc[audit["check"] == "adapter_submit_capability", "passed"].iloc[0]))


if __name__ == "__main__":
    unittest.main()
