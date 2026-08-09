import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.audit import mapping_sha256
from ashare_quant.autolive import build_auto_live_readiness
from ashare_quant.autolive_binding import (
    auto_live_account_binding_status,
    bind_auto_live_account,
    build_auto_live_account_binding_audit,
    unbind_auto_live_account,
)


class AutoLiveAccountBindingTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        shutil.rmtree(self.output, ignore_errors=True)
        self.output.mkdir(parents=True)
        self.config = {
            "paths": {
                "output_dir": str(self.output),
                "state_db": str(self.output / "paper.sqlite3"),
            },
            "auto_live": {
                "broker_adapter": "command",
                "account_binding": {
                    "required": True,
                    "require_hmac": True,
                    "hmac_key_env": "TEST_BINDING_KEY",
                },
            },
        }

    def write_valid_evidence(self, account_id="acct-masked"):
        pd.DataFrame([{
            "snapshot_run_id": "snapshot-1",
            "broker": "broker-a",
            "account_id": account_id,
            "environment": "live",
            "auto_live_config_sha256": mapping_sha256(self.config["auto_live"]),
        }]).to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)
        pd.DataFrame([{"check": "reconciled", "passed": True}]).to_csv(
            self.output / "auto_live_broker_account_audit.csv", index=False
        )

    def test_required_binding_missing_is_blocked(self):
        passed, value, required = auto_live_account_binding_status(self.config)
        self.assertTrue(required)
        self.assertFalse(passed)
        self.assertIn("account_binding_file_present", value)

    def test_required_binding_is_an_auto_live_readiness_blocker(self):
        readiness = build_auto_live_readiness(self.config)
        row = readiness.set_index("check").loc["auto_live_account_binding"]
        self.assertFalse(bool(row["passed"]))
        self.assertEqual(row["severity"], "account_binding_blocker")

    def test_simulation_snapshot_cannot_be_bound(self):
        self.write_valid_evidence()
        snapshot = pd.read_csv(self.output / "auto_live_broker_account_snapshot.csv")
        snapshot.loc[0, "environment"] = "simulation"
        snapshot.to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)
        with self.assertRaisesRegex(RuntimeError, "environment=live"):
            bind_auto_live_account(self.config, confirm=True)

    def test_binding_requires_explicit_confirmation(self):
        self.write_valid_evidence()
        with self.assertRaisesRegex(RuntimeError, "explicit --confirm"):
            bind_auto_live_account(self.config)

    def test_signed_binding_verifies_and_detects_account_switch(self):
        self.write_valid_evidence()
        with patch.dict(os.environ, {"TEST_BINDING_KEY": "k" * 40}, clear=False):
            paths = bind_auto_live_account(self.config, confirm=True)
            passed, _, _ = auto_live_account_binding_status(self.config)
            self.assertTrue(passed)
            snapshot = pd.read_csv(self.output / "auto_live_broker_account_snapshot.csv")
            snapshot.loc[0, "account_id"] = "different-account"
            snapshot.to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)
            audit = build_auto_live_account_binding_audit(self.config).set_index("check")
        self.assertFalse(bool(audit.loc["account_binding_account_id_match", "passed"]))
        self.assertTrue(Path(paths["auto_live_account_binding.json"]).exists())

    def test_tampered_binding_fails_hash_and_hmac(self):
        self.write_valid_evidence()
        with patch.dict(os.environ, {"TEST_BINDING_KEY": "z" * 40}, clear=False):
            paths = bind_auto_live_account(self.config, confirm=True)
            path = Path(paths["auto_live_account_binding.json"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["account_id"] = "tampered"
            path.write_text(json.dumps(payload), encoding="utf-8")
            audit = build_auto_live_account_binding_audit(self.config).set_index("check")
        self.assertFalse(bool(audit.loc["account_binding_payload_hash_valid", "passed"]))
        self.assertFalse(bool(audit.loc["account_binding_hmac_valid", "passed"]))

    def test_unbind_requires_confirmation_and_removes_binding(self):
        self.write_valid_evidence()
        with patch.dict(os.environ, {"TEST_BINDING_KEY": "u" * 40}, clear=False):
            bind_auto_live_account(self.config, confirm=True)
            with self.assertRaisesRegex(RuntimeError, "explicit --confirm"):
                unbind_auto_live_account(self.config)
            paths = unbind_auto_live_account(self.config, confirm=True)
        self.assertNotIn("auto_live_account_binding.json", paths)
        self.assertFalse((self.output / "auto_live_account_binding.json").exists())


if __name__ == "__main__":
    unittest.main()
