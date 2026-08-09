import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import build_auto_live_readiness
from ashare_quant.autolive_alerts import (
    auto_live_alert_status,
    build_auto_live_alert_audit,
    retry_auto_live_alerts,
    send_auto_live_alert,
    write_auto_live_alert_audit,
    write_auto_live_alert_selftest,
)


class AutoLiveAlertTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        shutil.rmtree(self.output, ignore_errors=True)
        self.output.mkdir(parents=True)

    def config(self, command=None, *, required=True, require_live=True):
        return {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "alerting": {
                    "enabled": command is not None,
                    "required_before_submit": required,
                    "require_live_environment": require_live,
                    "require_hmac": True,
                    "hmac_key_env": "TEST_ALERT_KEY",
                    "command": command or [],
                    "timeout_seconds": 10,
                    "selftest_max_age_seconds": 300,
                    "pinned_files": [],
                }
            },
        }

    def write_live_bridge(self, *, fail_delivery=False):
        bridge = self.output / "alert_bridge.py"
        delivery = "failed" if fail_delivery else "delivered"
        bridge.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path.cwd()))\n"
            "from ashare_quant.autolive_alerts import finalize_alert_response, verify_alert_request\n"
            "req=json.load(open(sys.argv[1], encoding='utf-8'))\n"
            "ok, reason=verify_alert_request(req)\n"
            f"status='ok' if req.get('mode')=='self_test' else '{delivery}'\n"
            "result={'status': status if ok else 'failed', 'environment':'live', 'capabilities':['alert'], 'message': reason}\n"
            "json.dump(finalize_alert_response(req,result),open(sys.argv[2],'w',encoding='utf-8'))\n",
            encoding="utf-8",
        )
        return bridge

    def test_disabled_optional_alerting_is_ready_and_writes_empty_outbox(self):
        config = self.config(required=False)
        config["auto_live"]["alerting"]["require_hmac"] = False
        paths = write_auto_live_alert_audit(config)
        audit = pd.read_csv(paths["auto_live_alert_audit.csv"])
        outbox = pd.read_csv(paths["auto_live_alert_outbox.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertTrue(outbox.empty)

    def test_required_missing_command_and_key_are_blocked(self):
        config = self.config(command=None, required=True)
        audit = build_auto_live_alert_audit(config).set_index("check")
        self.assertFalse(bool(audit.loc["alerting_enabled_when_required", "passed"]))
        self.assertFalse(bool(audit.loc["alert_command_configured", "passed"]))
        self.assertFalse(bool(audit.loc["alert_hmac_key_ready", "passed"]))

    def test_required_alert_channel_is_auto_live_readiness_blocker(self):
        config = self.config(command=None, required=True)
        readiness = build_auto_live_readiness(config).set_index("check")
        self.assertFalse(bool(readiness.loc["auto_live_alert_channel", "passed"]))
        self.assertEqual(readiness.loc["auto_live_alert_channel", "severity"], "alert_blocker")

    def test_simulation_bridge_cannot_certify_live_alerting(self):
        command = [sys.executable, "scripts/alert_bridge_template.py", "--simulate"]
        config = self.config(command)
        config["auto_live"]["alerting"]["pinned_files"] = [
            {"path": "scripts/alert_bridge_template.py", "sha256": file_sha256(Path("scripts/alert_bridge_template.py"))}
        ]
        with patch.dict(os.environ, {"TEST_ALERT_KEY": "a" * 40}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "did not certify"):
                write_auto_live_alert_selftest(config)
            audit = build_auto_live_alert_audit(config).set_index("check")
        self.assertFalse(bool(audit.loc["alert_environment_live", "passed"]))

    def test_live_signed_selftest_and_delivery_clear_outbox(self):
        bridge = self.write_live_bridge()
        config = self.config([sys.executable, str(bridge)])
        config["auto_live"]["alerting"]["pinned_files"] = [{"path": str(bridge), "sha256": file_sha256(bridge)}]
        with patch.dict(os.environ, {"TEST_ALERT_KEY": "b" * 40}, clear=False):
            write_auto_live_alert_selftest(config)
            paths = send_auto_live_alert(
                config,
                event_type="service_failure",
                severity="critical",
                message="cycle failed",
                context={"cycle": 3},
                strict=True,
            )
            passed, _, required = auto_live_alert_status(config)
        outbox = pd.read_csv(paths["auto_live_alert_outbox.csv"])
        self.assertTrue(required)
        self.assertTrue(passed)
        self.assertEqual(outbox.loc[0, "status"], "delivered")
        self.assertEqual(outbox.loc[0, "environment"], "live")

    def test_failed_delivery_stays_pending_and_retry_increments_attempts(self):
        bridge = self.write_live_bridge(fail_delivery=True)
        config = self.config([sys.executable, str(bridge)])
        config["auto_live"]["alerting"]["pinned_files"] = [{"path": str(bridge), "sha256": file_sha256(bridge)}]
        with patch.dict(os.environ, {"TEST_ALERT_KEY": "c" * 40}, clear=False):
            write_auto_live_alert_selftest(config)
            paths = send_auto_live_alert(config, event_type="uncertain", severity="critical", message="unknown")
            retry_auto_live_alerts(config)
        outbox = pd.read_csv(paths["auto_live_alert_outbox.csv"])
        self.assertEqual(outbox.loc[0, "status"], "pending")
        self.assertEqual(int(outbox.loc[0, "attempts"]), 2)
        audit = pd.read_csv(paths["auto_live_alert_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["alert_outbox_clear", "passed"]))


def file_sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
