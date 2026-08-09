from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import build_auto_live_readiness
from ashare_quant.autolive_rollout import (
    approve_auto_live_rollout,
    auto_live_rollout_status,
    build_auto_live_rollout_audit,
    revoke_auto_live_rollout,
)


class AutoLiveRolloutTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        shutil.rmtree(self.output, ignore_errors=True)
        self.output.mkdir(parents=True)
        self.config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "rollout": {
                    "required_before_submit": True,
                    "stage": "canary",
                    "approval_ttl_hours": 24,
                    "require_hmac": True,
                    "hmac_key_env": "TEST_ROLLOUT_KEY",
                    "stages": {"canary": {
                        "max_orders": 1,
                        "max_single_order_amount": 20000,
                        "max_buy_amount": 20000,
                        "max_gross_amount": 20000,
                    }},
                }
            },
        }
        self.binding = {
            "binding_id": "binding-1", "active": True, "environment": "live",
            "broker": "broker-a", "account_id": "acct-masked",
        }
        (self.output / "auto_live_account_binding.json").write_text(json.dumps(self.binding), encoding="utf-8")
        self.write_evidence()
        self.ticket = pd.DataFrame([{
            "ticket_id": "t1", "symbol": "600000", "side": "buy",
            "quantity": 100, "limit_price": 100.0, "estimated_amount": 10000.0,
        }])

    def write_evidence(self):
        for name in [
            "auto_live_account_binding_audit.csv",
            "auto_live_alert_audit.csv",
            "auto_live_adapter_file_audit.csv",
        ]:
            pd.DataFrame([{"check": "ok", "passed": True}]).to_csv(self.output / name, index=False)
        pd.DataFrame([{"status": "certified_live"}]).to_csv(self.output / "auto_live_adapter_replay_summary.csv", index=False)
        pd.DataFrame([{"status": "verified"}]).to_csv(self.output / "auto_live_journal_summary.csv", index=False)
        pd.DataFrame(columns=["state"]).to_csv(self.output / "auto_live_submission_intent.csv", index=False)

    def approve(self):
        return approve_auto_live_rollout(self.config, stage="canary", confirm=True, reason="controlled_test")

    def test_required_rollout_without_approval_is_blocked(self):
        passed, value, required = auto_live_rollout_status(self.config, self.ticket)
        self.assertTrue(required)
        self.assertFalse(passed)
        self.assertIn("rollout_approval_present", value)

    def test_required_rollout_is_auto_live_readiness_blocker(self):
        readiness = build_auto_live_readiness(self.config, tickets=self.ticket).set_index("check")
        self.assertFalse(bool(readiness.loc["auto_live_rollout_authorization", "passed"]))
        self.assertEqual(readiness.loc["auto_live_rollout_authorization", "severity"], "rollout_blocker")

    def test_approval_requires_confirmation_and_certified_evidence(self):
        with self.assertRaisesRegex(RuntimeError, "explicit --confirm"):
            approve_auto_live_rollout(self.config, stage="canary")
        (self.output / "auto_live_adapter_replay_summary.csv").unlink()
        with patch.dict(os.environ, {"TEST_ROLLOUT_KEY": "r" * 40}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "evidence incomplete"):
                self.approve()

    def test_signed_canary_approval_passes_one_small_order(self):
        with patch.dict(os.environ, {"TEST_ROLLOUT_KEY": "s" * 40}, clear=False):
            paths = self.approve()
            audit = build_auto_live_rollout_audit(self.config, tickets=self.ticket)
        self.assertTrue(audit["passed"].astype(bool).all())
        approval_path = Path(paths["auto_live_rollout_approval.json"])
        self.assertTrue(approval_path.exists())
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        self.assertEqual(float(approval["limits"]["min_cash_buffer_amount"]), 500.0)
        self.assertEqual(float(approval["limits"]["min_cash_buffer_ratio"]), 0.10)

    def test_canary_blocks_second_order_and_amount_overrun(self):
        with patch.dict(os.environ, {"TEST_ROLLOUT_KEY": "c" * 40}, clear=False):
            self.approve()
            two = pd.concat(
                [self.ticket, self.ticket.assign(ticket_id="t2", estimated_amount=15000.0)],
                ignore_index=True,
            )
            audit = build_auto_live_rollout_audit(self.config, tickets=two).set_index("check")
        self.assertFalse(bool(audit.loc["rollout_order_count_limit", "passed"]))
        self.assertFalse(bool(audit.loc["rollout_buy_amount_limit", "passed"]))
        self.assertFalse(bool(audit.loc["rollout_gross_amount_limit", "passed"]))

    def test_tamper_account_switch_and_expiry_are_detected(self):
        with patch.dict(os.environ, {"TEST_ROLLOUT_KEY": "t" * 40}, clear=False):
            paths = self.approve()
            future = datetime.now(timezone.utc) + timedelta(days=2)
            expired = build_auto_live_rollout_audit(self.config, tickets=self.ticket, now=future).set_index("check")
            self.binding["account_id"] = "different"
            (self.output / "auto_live_account_binding.json").write_text(json.dumps(self.binding), encoding="utf-8")
            switched = build_auto_live_rollout_audit(self.config, tickets=self.ticket).set_index("check")
            approval_path = Path(paths["auto_live_rollout_approval.json"])
            payload = json.loads(approval_path.read_text(encoding="utf-8"))
            payload["limits"]["max_orders"] = 100
            approval_path.write_text(json.dumps(payload), encoding="utf-8")
            tampered = build_auto_live_rollout_audit(self.config, tickets=self.ticket).set_index("check")
        self.assertFalse(bool(expired.loc["rollout_approval_not_expired", "passed"]))
        self.assertFalse(bool(switched.loc["rollout_approval_account_match", "passed"]))
        self.assertFalse(bool(tampered.loc["rollout_approval_hash_valid", "passed"]))
        self.assertFalse(bool(tampered.loc["rollout_approval_hmac_valid", "passed"]))

    def test_revoke_requires_confirmation_and_removes_approval(self):
        with patch.dict(os.environ, {"TEST_ROLLOUT_KEY": "v" * 40}, clear=False):
            self.approve()
            with self.assertRaisesRegex(RuntimeError, "explicit --confirm"):
                revoke_auto_live_rollout(self.config)
            paths = revoke_auto_live_rollout(self.config, confirm=True, reason="stop")
        self.assertFalse((self.output / "auto_live_rollout_approval.json").exists())
        history = pd.read_csv(paths["auto_live_rollout_history.csv"])
        self.assertEqual(history["action"].tolist(), ["approve", "revoke"])


if __name__ == "__main__":
    unittest.main()
