import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive_alerts import send_auto_live_incident_alert


class AutoLiveIncidentAlertTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        shutil.rmtree(self.output, ignore_errors=True)
        self.output.mkdir(parents=True)
        self.config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "alerting": {
                    "enabled": True,
                    "required_before_submit": False,
                    "require_live_environment": True,
                }
            },
        }

    def test_delivered_incident_is_suppressed_on_repeat(self):
        delivered = {
            "status": "delivered",
            "environment": "live",
            "capabilities": ["alert"],
            "message": "ok",
        }
        with patch("ashare_quant.autolive_alerts._invoke", return_value=delivered) as invoke:
            send_auto_live_incident_alert(
                self.config,
                event_type="cancel_verification_pending",
                incident_key="cancel-1",
                severity="critical",
                message="pending",
            )
            send_auto_live_incident_alert(
                self.config,
                event_type="cancel_verification_pending",
                incident_key="cancel-1",
                severity="critical",
                message="pending",
            )

        outbox = pd.read_csv(self.output / "auto_live_alert_outbox.csv")
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.loc[0, "status"], "delivered")
        self.assertEqual(int(outbox.loc[0, "attempts"]), 1)

    def test_pending_incident_retries_same_outbox_record(self):
        pending = {
            "status": "failed",
            "environment": "live",
            "capabilities": ["alert"],
            "message": "temporary failure",
        }
        with patch("ashare_quant.autolive_alerts._invoke", return_value=pending) as invoke:
            for _ in range(2):
                send_auto_live_incident_alert(
                    self.config,
                    event_type="fill_import_failed",
                    incident_key="query-1",
                    severity="critical",
                    message="failed",
                )

        outbox = pd.read_csv(self.output / "auto_live_alert_outbox.csv")
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox.loc[0, "status"], "pending")
        self.assertEqual(int(outbox.loc[0, "attempts"]), 2)


if __name__ == "__main__":
    unittest.main()
