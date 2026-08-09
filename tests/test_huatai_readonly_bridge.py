from datetime import datetime
import json
import subprocess
import sys
from pathlib import Path
import unittest


SCRIPT = Path.cwd() / "scripts" / "huatai_command_bridge.py"
SCHEMA = "ashare_quant.command_adapter.v1"


class HuataiReadOnlyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.output = Path("test_outputs") / self._testMethodName
        self.output.mkdir(parents=True, exist_ok=True)
        self.snapshot = self.output / "snapshot.json"
        self.config = self.output / "bridge.json"
        self.snapshot.write_text(json.dumps({
            "schema_version": "ashare_quant.huatai_desktop_snapshot.v1",
            "status": "ok",
            "environment": "live",
            "broker": "huatai",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "broker_time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "account": {"account_id": "huatai-hash", "cash": 1000, "available_cash": 900, "market_value": 0, "total_asset": 1000, "frozen_cash": 0},
            "positions": [],
        }), encoding="utf-8")
        self.config.write_text(json.dumps({"huatai": {
            "broker": "huatai",
            "live_backend": "desktop_read_only",
            "desktop_snapshot_path": str(self.snapshot.resolve()),
            "desktop_snapshot_max_age_seconds": 300,
        }}), encoding="utf-8")

    def call(self, mode):
        request = self.output / f"{mode}_request.json"
        result = self.output / f"{mode}_result.json"
        request.write_text(json.dumps({
            "schema_version": SCHEMA,
            "mode": mode,
            "run_id": mode,
            "auto_live_config_sha256": "config-hash",
            "orders": [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100}],
        }), encoding="utf-8")
        completed = subprocess.run([sys.executable, str(SCRIPT), "--config", str(self.config), str(request), str(result)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(result.read_text(encoding="utf-8"))

    def test_selftest_declares_only_account_snapshot(self):
        result = self.call("self_test")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["capabilities"], ["account_snapshot"])

    def test_account_snapshot_is_live_and_submission_is_blocked(self):
        snapshot = self.call("account_snapshot")
        submit = self.call("submit_orders")
        self.assertEqual(snapshot["environment"], "live")
        self.assertEqual(snapshot["account"]["account_id"], "huatai-hash")
        self.assertEqual(snapshot["positions"], [])
        self.assertEqual(submit["results"][0]["status"], "failed")
        self.assertEqual(submit["results"][0]["broker_order_id"], "")


if __name__ == "__main__":
    unittest.main()
