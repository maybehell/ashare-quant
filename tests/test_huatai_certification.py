from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256
from ashare_quant.huatai_canary import desktop_canary_certification_status
from ashare_quant.huatai_certification import (
    arm_huatai_desktop_canary,
    build_huatai_canary_arming_audit,
    write_huatai_execution_sandbox_summary,
)


class HuataiCertificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / "huatai_certification"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 7, 13, 1, 30, tzinfo=timezone.utc)
        self.key = "h" * 64

    def build_config(self):
        bridge_path = self.root / "bridge.json"
        cert_path = self.root / "certification.json"
        bridge = {
            "huatai": {
                "live_backend": "desktop_canary",
                "desktop_canary": {
                    "enabled": True,
                    "allow_submit": True,
                    "max_orders": 1,
                    "max_order_amount": 1000,
                    "certification_path": str(cert_path),
                    "required_certification_stage": "armed",
                    "require_hmac": True,
                    "hmac_key_env": "TEST_HUATAI_CERT_KEY",
                },
            }
        }
        bridge_path.write_text(json.dumps(bridge), encoding="utf-8")
        evidence = {
            name: str(self.root / f"{name}.json")
            for name in [
                "buy_preview",
                "sell_preview",
                "orders_monitor",
                "trades_monitor",
                "cancels_monitor",
                "account_snapshot",
                "sandbox_summary",
            ]
        }
        config = {
            "paths": {"output_dir": str(self.root)},
            "auto_live": {
                "enabled": True,
                "emergency_stop": False,
                "allow_broker_submit": True,
                "action_confirmation": {"required_before_submit": True, "ttl_seconds": 600},
                "command_adapter": {
                    "command": ["python", "scripts/huatai_command_bridge.py", "--config", str(bridge_path)],
                    "pinned_files": [
                        {
                            "path": str(bridge_path),
                            "sha256": sha256(bridge_path.read_bytes()).hexdigest(),
                        }
                    ],
                },
                "service": {"enabled": False, "allow_submit": False},
                "rollout": {
                    "stage": "canary",
                    "stages": {"canary": {"max_orders": 1, "max_single_order_amount": 1000}},
                },
                "desktop_canary_certification": {
                    "bridge_config": str(bridge_path),
                    "arming_ttl_seconds": 1800,
                    "preview_max_age_seconds": 3600,
                    "monitor_max_age_seconds": 3600,
                    "account_snapshot_max_age_seconds": 300,
                    "audit_path": str(self.root / "audit.csv"),
                    "certification_path": str(cert_path),
                    "evidence": evidence,
                },
            },
        }
        generated = self.now.isoformat()
        for side in ["buy", "sell"]:
            Path(evidence[f"{side}_preview"]).write_text(
                json.dumps(
                    {
                        "status": "preview_verified",
                        "environment": "live",
                        "side": side,
                        "form_readback_verified": True,
                        "form_restore_verified": True,
                        "submit_invoked": False,
                        "cancel_invoked": False,
                        "generated_at": generated,
                    }
                ),
                encoding="utf-8",
            )
        for name in ["orders", "trades", "cancels"]:
            Path(evidence[f"{name}_monitor"]).write_text(
                json.dumps(
                    {
                        "status": "empty_verified",
                        "environment": "live",
                        "image_sha256": name * 16,
                        "submit_invoked": False,
                        "cancel_invoked": False,
                        "generated_at": generated,
                    }
                ),
                encoding="utf-8",
            )
        Path(evidence["account_snapshot"]).write_text(
            json.dumps(
                {
                    "status": "ok",
                    "environment": "live",
                    "account": {"account_id": "huatai-test", "available_cash": 10000},
                    "generated_at": generated,
                }
            ),
            encoding="utf-8",
        )
        Path(evidence["sandbox_summary"]).write_text(
            json.dumps(
                {
                    "status": "sandbox_certified",
                    "buy_form_preview": True,
                    "sell_form_preview": True,
                    "orders_empty_verified": True,
                    "submit_invoked": False,
                    "cancel_invoked": False,
                    "auto_live_config_sha256": auto_live_config_sha256(config),
                    "generated_at": generated,
                }
            ),
            encoding="utf-8",
        )
        return config, bridge

    def test_all_current_evidence_can_issue_hmac_armed_certificate(self):
        config, bridge = self.build_config()
        with patch.dict(os.environ, {"TEST_HUATAI_CERT_KEY": self.key}, clear=False):
            audit = build_huatai_canary_arming_audit(config, now=self.now)
            self.assertTrue(audit["passed"].astype(bool).all(), audit.to_dict("records"))
            paths = arm_huatai_desktop_canary(config, confirm=True, now=self.now)
            passed, stage = desktop_canary_certification_status(
                bridge,
                expected_config_sha256=auto_live_config_sha256(config),
                now=self.now + timedelta(seconds=1),
            )

        self.assertTrue(passed)
        self.assertEqual(stage, "armed")
        payload = json.loads(paths["huatai_desktop_canary_certification.json"].read_text(encoding="utf-8"))
        self.assertFalse(payload["submit_contract"])
        self.assertEqual(payload["max_orders"], 1)
        self.assertLessEqual(payload["max_order_amount"], 1000)
        self.assertEqual(len(payload["hmac_sha256"]), 64)
        payload["max_order_amount"] = 999
        paths["huatai_desktop_canary_certification.json"].write_text(
            json.dumps(payload), encoding="utf-8"
        )
        with patch.dict(os.environ, {"TEST_HUATAI_CERT_KEY": self.key}, clear=False):
            tampered, reason = desktop_canary_certification_status(
                bridge,
                expected_config_sha256=auto_live_config_sha256(config),
                now=self.now + timedelta(seconds=2),
            )
        self.assertFalse(tampered)
        self.assertEqual(reason, "desktop_canary_certification_hmac_invalid")

    def test_arm_requires_confirmation_and_all_green_audit(self):
        config, _ = self.build_config()
        with self.assertRaisesRegex(RuntimeError, "--confirm"):
            arm_huatai_desktop_canary(config, confirm=False, now=self.now)
        config["auto_live"]["emergency_stop"] = True
        with patch.dict(os.environ, {"TEST_HUATAI_CERT_KEY": self.key}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "main_emergency_stop_inactive"):
                arm_huatai_desktop_canary(config, confirm=True, now=self.now)
        self.assertFalse((self.root / "certification.json").exists())

    def test_missing_form_restore_evidence_blocks_arming(self):
        config, _ = self.build_config()
        preview_path = Path(
            config["auto_live"]["desktop_canary_certification"]["evidence"]["buy_preview"]
        )
        payload = json.loads(preview_path.read_text(encoding="utf-8"))
        payload.pop("form_restore_verified")
        preview_path.write_text(json.dumps(payload), encoding="utf-8")

        audit = build_huatai_canary_arming_audit(config, now=self.now).set_index("check")

        self.assertFalse(bool(audit.loc["buy_form_preview_current", "passed"]))

    def test_stale_account_and_changed_config_are_blocked(self):
        config, _ = self.build_config()
        snapshot_path = Path(config["auto_live"]["desktop_canary_certification"]["evidence"]["account_snapshot"])
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload["generated_at"] = (self.now - timedelta(minutes=6)).isoformat()
        snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
        with patch.dict(os.environ, {"TEST_HUATAI_CERT_KEY": self.key}, clear=False):
            audit = build_huatai_canary_arming_audit(config, now=self.now).set_index("check")
        self.assertFalse(bool(audit.loc["live_account_snapshot_current", "passed"]))


    def test_sandbox_summary_is_rebuilt_from_current_evidence(self):
        config, _ = self.build_config()
        sandbox_path = Path(config["auto_live"]["desktop_canary_certification"]["evidence"]["sandbox_summary"])
        sandbox_path.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {"TEST_HUATAI_CERT_KEY": self.key}, clear=False):
            path = write_huatai_execution_sandbox_summary(config, now=self.now)
            audit = build_huatai_canary_arming_audit(config, now=self.now).set_index("check")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "sandbox_certified")
        self.assertEqual(payload["auto_live_config_sha256"], auto_live_config_sha256(config))
        self.assertTrue(all(payload["evidence_sha256"].values()))
        self.assertTrue(bool(audit.loc["sandbox_evidence_config_match", "passed"]))
        self.assertTrue(bool(audit.loc["sandbox_evidence_certified", "passed"]))

    def test_sandbox_summary_fails_closed_on_stale_evidence(self):
        config, _ = self.build_config()
        snapshot_path = Path(config["auto_live"]["desktop_canary_certification"]["evidence"]["account_snapshot"])
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload["generated_at"] = (self.now - timedelta(minutes=6)).isoformat()
        snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
        with patch.dict(os.environ, {"TEST_HUATAI_CERT_KEY": self.key}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "live_account_snapshot_current"):
                write_huatai_execution_sandbox_summary(config, now=self.now)
        sandbox = json.loads(Path(config["auto_live"]["desktop_canary_certification"]["evidence"]["sandbox_summary"]).read_text(encoding="utf-8"))
        audit = build_huatai_canary_arming_audit(config, now=self.now).set_index("check")
        self.assertTrue(bool(audit.loc["sandbox_evidence_config_match", "passed"]))
        self.assertFalse(bool(audit.loc["sandbox_evidence_certified", "passed"]))

        self.assertEqual(sandbox["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
