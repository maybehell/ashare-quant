import hashlib
import os
import shutil
import sys
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd
from unittest.mock import patch

from ashare_quant.autolive import auto_live_config_sha256, run_auto_live, write_auto_live_adapter_selftest
from ashare_quant.autolive_integrity import (
    attach_request_integrity,
    build_command_adapter_file_audit,
    command_adapter_file_status,
    command_integrity_status,
    finalize_adapter_response,
    verify_adapter_request_integrity,
    verify_adapter_result_integrity,
)


class AutoLiveIntegrityTests(unittest.TestCase):
    def config(self, required: bool = True) -> dict:
        return {
            "auto_live": {
                "command_adapter": {
                    "require_hmac_integrity": required,
                    "integrity_key_env": "TEST_ADAPTER_HMAC_KEY",
                }
            }
        }

    def test_signed_request_and_result_round_trip(self):
        config = self.config()
        env = {"TEST_ADAPTER_HMAC_KEY": "k" * 40}
        with patch.dict(os.environ, env, clear=False):
            request = attach_request_integrity(config, {"mode": "submit_orders", "run_id": "r1", "orders": []})
            request_ok, _ = verify_adapter_request_integrity(request)
            response = finalize_adapter_response(request, {"run_id": "r1", "results": []})
            response_ok, _ = verify_adapter_result_integrity(config, request, response)

        self.assertTrue(request_ok)
        self.assertTrue(response_ok)
        self.assertTrue(request["integrity"]["hmac_sha256"])
        self.assertTrue(response["integrity"]["hmac_sha256"])

    def test_request_payload_tampering_is_detected(self):
        config = self.config()
        with patch.dict(os.environ, {"TEST_ADAPTER_HMAC_KEY": "s" * 40}, clear=False):
            request = attach_request_integrity(config, {"mode": "submit_orders", "orders": [{"quantity": 100}]})
            request["orders"][0]["quantity"] = 10000
            passed, reason = verify_adapter_request_integrity(request)

        self.assertFalse(passed)
        self.assertEqual(reason, "request_payload_sha256_mismatch")

    def test_result_payload_and_request_binding_tampering_are_detected(self):
        config = self.config()
        with patch.dict(os.environ, {"TEST_ADAPTER_HMAC_KEY": "x" * 40}, clear=False):
            request = attach_request_integrity(config, {"mode": "query_orders", "run_id": "q1", "orders": []})
            response = finalize_adapter_response(request, {"run_id": "q1", "results": [{"broker_status": "accepted"}]})
            response["results"][0]["broker_status"] = "filled"
            passed, reason = verify_adapter_result_integrity(config, request, response)

        self.assertFalse(passed)
        self.assertEqual(reason, "result_payload_sha256_mismatch")

    def test_required_key_must_be_present_and_at_least_32_bytes(self):
        config = self.config()
        with patch.dict(os.environ, {"TEST_ADAPTER_HMAC_KEY": "short"}, clear=False):
            status = command_integrity_status(config)
            with self.assertRaisesRegex(RuntimeError, "shorter than 32 bytes"):
                attach_request_integrity(config, {"mode": "self_test"})

        self.assertFalse(status["ready"])
        self.assertFalse(status["key_strong"])

    def test_optional_integrity_allows_legacy_unsigned_result(self):
        config = self.config(required=False)
        passed, reason = verify_adapter_result_integrity(config, {"mode": "self_test"}, {"status": "ok"})

        self.assertTrue(passed)
        self.assertEqual(reason, "integrity_optional_missing")


    def test_tampered_signed_submit_result_becomes_submission_uncertain(self):
        output = Path.cwd() / "test_outputs" / "integrity_tampered_submit"
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        bridge = output / "tampering_bridge.py"
        project_root = str(Path.cwd())
        bridge.write_text(
            "import json, sys\n"
            f"sys.path.insert(0, {project_root!r})\n"
            "from ashare_quant.autolive_integrity import finalize_adapter_response\n"
            "request_path, result_path = sys.argv[1], sys.argv[2]\n"
            "request = json.load(open(request_path, encoding='utf-8'))\n"
            "if request.get('mode') == 'self_test':\n"
            "    response = {'schema_version': request['schema_version'], 'run_id': request['run_id'], 'status': 'ok', 'capabilities': ['submit_orders','query_orders','cancel_orders','account_snapshot'], 'auto_live_config_sha256': request['auto_live_config_sha256']}\n"
            "else:\n"
            "    response = {'schema_version': request['schema_version'], 'run_id': request['run_id'], 'auto_live_config_sha256': request['auto_live_config_sha256'], 'results': [{'ticket_id': item['ticket_id'], 'status': 'submitted', 'broker_order_id': 'signed-' + item['ticket_id']} for item in request.get('orders', [])]}\n"
            "response = finalize_adapter_response(request, response)\n"
            "if request.get('mode') == 'submit_orders':\n"
            "    response['results'][0]['broker_order_id'] = 'tampered-id'\n"
            "json.dump(response, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n",
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
                "command_adapter": {
                    "command": [sys.executable, str(bridge)],
                    "timeout_seconds": 10,
                    "require_hmac_integrity": True,
                    "integrity_key_env": "TEST_ADAPTER_HMAC_KEY",
                },
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
            [{"ticket_id": "secure-t1", "date": "2026-07-11", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "estimated_amount": 1000.0, "status": "ready"}]
        ).to_csv(output / "rpa_orders.csv", index=False)
        fingerprint = auto_live_config_sha256(config)
        pd.DataFrame([{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "severity": "ok", "next_action": "", "message": "ok"}]).to_csv(output / "auto_live_broker_account_audit.csv", index=False)
        pd.DataFrame(
            [{"snapshot_run_id": "secure-account", "broker": "test", "account_id": "masked", "environment": "live", "cash": 100000, "available_cash": 100000, "market_value": 0, "total_asset": 100000, "frozen_cash": 0, "positions_count": 0, "generated_at": datetime.now().isoformat(timespec="seconds"), "auto_live_config_sha256": fingerprint, "message": ""}]
        ).to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)

        with patch.dict(os.environ, {"TEST_ADAPTER_HMAC_KEY": "z" * 40}, clear=False):
            selftest = pd.read_csv(write_auto_live_adapter_selftest(config))
            paths = run_auto_live(config)

        run = pd.read_csv(paths["auto_live_run.csv"])
        results = pd.read_csv(paths["auto_live_order_results.csv"])
        self.assertTrue(selftest["passed"].astype(bool).all())
        self.assertEqual(run.loc[0, "status"], "submission_uncertain")
        self.assertEqual(results.loc[0, "status"], "unknown")
        self.assertIn("integrity failed", results.loc[0, "message"])
        self.assertNotEqual(results.loc[0, "broker_order_id"], "tampered-id")


    def test_required_pinned_bridge_file_detects_replacement(self):
        output = Path.cwd() / "test_outputs" / "integrity_pinned_file"
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        bridge = output / "bridge.py"
        bridge.write_text("print('trusted')\n", encoding="utf-8")
        expected = hashlib.sha256(bridge.read_bytes()).hexdigest()
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "command_adapter": {
                    "require_pinned_files": True,
                    "pinned_files": [{"path": str(bridge), "sha256": expected}],
                }
            },
        }

        passed_before, _, required = command_adapter_file_status(config)
        bridge.write_text("print('replaced')\n", encoding="utf-8")
        passed_after, value_after, _ = command_adapter_file_status(config)
        audit = build_command_adapter_file_audit(config)

        self.assertTrue(required)
        self.assertTrue(passed_before)
        self.assertFalse(passed_after)
        self.assertIn("hash_match", value_after)
        self.assertFalse(bool(audit.loc[audit["check"] == "adapter_pinned_file_1_hash_match", "passed"].iloc[0]))

    def test_complete_pin_manifest_blocks_missing_dependency_and_duplicate_paths(self):
        root = Path.cwd() / "test_outputs" / "integrity_complete_manifest"
        root.mkdir(parents=True, exist_ok=True)
        bridge = root / "bridge.py"
        dependency = root / "dependency.py"
        bridge.write_text("print('bridge')\n", encoding="utf-8")
        dependency.write_text("VALUE = 1\n", encoding="utf-8")
        bridge_hash = hashlib.sha256(bridge.read_bytes()).hexdigest()
        config = {
            "paths": {"output_dir": str(root)},
            "auto_live": {"command_adapter": {
                "require_pinned_files": True,
                "require_complete_pin_manifest": True,
                "required_pinned_paths": [str(bridge), str(dependency)],
                "pinned_files": [{"path": str(bridge), "sha256": bridge_hash}],
            }},
        }

        missing = build_command_adapter_file_audit(config).set_index("check")
        self.assertFalse(bool(missing.at["adapter_required_pinned_paths_complete", "passed"]))
        config["auto_live"]["command_adapter"]["pinned_files"].append({"path": str(bridge), "sha256": bridge_hash})
        duplicate = build_command_adapter_file_audit(config).set_index("check")
        self.assertFalse(bool(duplicate.at["adapter_pinned_file_paths_unique", "passed"]))

    def test_file_pinning_is_optional_when_no_entries_are_configured(self):
        config = {"paths": {"output_dir": str(Path.cwd() / "test_outputs" / "integrity_optional_pin")}, "auto_live": {"command_adapter": {}}}
        passed, value, required = command_adapter_file_status(config)

        self.assertTrue(passed)
        self.assertFalse(required)
        self.assertIn("required=False", value)


if __name__ == "__main__":
    unittest.main()
