import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256
from ashare_quant.autolive_recovery_drill import write_auto_live_recovery_drill


class AutoLiveRecoveryDrillTests(unittest.TestCase):
    def setUp(self):
        self.output = (
            Path.cwd()
            / "test_outputs"
            / "auto_live_recovery_drill"
            / self._testMethodName
        )
        shutil.rmtree(self.output, ignore_errors=True)
        self.key_env = "TEST_AUTO_LIVE_RECOVERY_DRILL_KEY"
        self.config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "emergency_stop": True,
                "allow_broker_submit": False,
                "command_adapter": {
                    "require_hmac_integrity": True,
                    "integrity_key_env": self.key_env,
                },
            },
        }

    def tearDown(self):
        os.environ.pop(self.key_env, None)

    def test_drill_proves_unknown_recovery_without_broker_or_live_state(self):
        with patch.dict(os.environ, {self.key_env: "r" * 64}, clear=False):
            paths = write_auto_live_recovery_drill(self.config)

        audit = pd.read_csv(paths["auto_live_recovery_drill_audit.csv"])
        summary = pd.read_csv(paths["auto_live_recovery_drill_summary.csv"]).iloc[0]
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(
            summary["auto_live_config_sha256"],
            auto_live_config_sha256(self.config),
        )
        self.assertFalse(bool(summary["broker_adapter_invoked"]))
        self.assertTrue(bool(summary["isolated_workspace_removed"]))
        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            {
                "auto_live_recovery_drill_audit.csv",
                "auto_live_recovery_drill_summary.csv",
            },
        )
        self.assertFalse((self.output / "auto_live_submission_intent.csv").exists())
        self.assertFalse((self.output / "auto_live_order_status.csv").exists())
        self.assertFalse((self.output / "auto_live_state.sqlite3").exists())

    def test_missing_required_hmac_key_fails_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(self.key_env, None)
            paths = write_auto_live_recovery_drill(self.config)

        audit = pd.read_csv(paths["auto_live_recovery_drill_audit.csv"])
        summary = pd.read_csv(paths["auto_live_recovery_drill_summary.csv"]).iloc[0]
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(bool(summary["broker_adapter_invoked"]))
        failed = audit.loc[audit["check"] == "recovery_drill_completed"].iloc[0]
        self.assertFalse(bool(failed["passed"]))
        self.assertIn("HMAC key missing", str(failed["value"]))


if __name__ == "__main__":
    unittest.main()
