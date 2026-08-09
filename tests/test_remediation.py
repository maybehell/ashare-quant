import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.remediation import build_auto_live_remediation_plan, build_auto_live_verification_plan, write_auto_live_remediation_plan, write_auto_live_verification_plan


class AutoLiveRemediationTests(unittest.TestCase):
    def test_remediation_plan_prioritizes_priced_only_account_ack(self):
        output = Path.cwd() / "test_outputs" / "remediation_priced_only"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        pd.DataFrame(
            [
                {"source": "execution_readiness", "check": "account_state_audit", "severity": "execution_blocker", "value": "5/7", "next_action": "repair", "message": "need account"},
                {"source": "execution_readiness", "check": "rpa_pre_trade_checks", "severity": "execution_blocker", "value": "12/13", "next_action": "repair", "message": "need rpa"},
                {"source": "evidence_plan", "check": "shadow_observation", "severity": "live_blocker", "value": "1", "next_action": "observe", "message": "shadow_observed_signal_count"},
                {"source": "adapter_audit", "check": "adapter_name_present", "severity": "auto_blocker", "value": "missing", "next_action": "config", "message": "need adapter"},
                {"source": "adapter_audit", "check": "submit_switch_explicit", "severity": "auto_blocker", "value": "False", "next_action": "confirm", "message": "need submit switch"},
            ]
        ).to_csv(output / "auto_live_blocker_details.csv", index=False)
        pd.DataFrame(
            [
                {"area": "account_audit", "status": "blocked", "current": 1, "blocker": "missing_position_prices", "next_action": "confirm exclusions"},
                {"area": "shadow_observation", "status": "collecting", "current": 1, "blocker": "shadow_observed_signal_count", "next_action": "collect shadow"},
            ]
        ).to_csv(output / "evidence_plan.csv", index=False)
        pd.DataFrame(
            [
                {
                    "path": "real_account_priced_only_import",
                    "available": True,
                    "status": "precheck_passed_with_exclusions",
                    "blockers": "",
                    "command": "account-import-check",
                    "next_action": "ack exclusions",
                    "note": "ok",
                }
            ]
        ).to_csv(output / "account_path_decision.csv", index=False)
        pd.DataFrame([{"symbol": "600002", "quantity": 5000}]).to_csv(output / "account_repair_excluded_positions.csv", index=False)

        plan = build_auto_live_remediation_plan(config)
        first = plan.iloc[0]
        commands = "\n".join(plan["command"].astype(str))

        self.assertEqual(first["area"], "account_audit")
        self.assertIn("--ack-exclusions", first["command"])
        self.assertTrue(bool(first["requires_confirmation"]))
        self.assertFalse(bool(first["safe_default"]))
        self.assertIn("600002", first["note"])
        self.assertIn("python -m ashare_quant run --allow-stale", commands)
        self.assertIn("manual_config: auto_live.broker_adapter", commands)
        self.assertIn("manual_config: auto_live.allow_broker_submit=true", commands)


    def test_verification_plan_orders_safe_post_remediation_chain(self):
        output = Path.cwd() / "test_outputs" / "verification_plan"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}

        path = write_auto_live_verification_plan(config)
        plan = pd.read_csv(path)
        built = build_auto_live_verification_plan(config)
        commands = " ".join(plan["command"].astype(str))

        self.assertEqual(plan["step"].tolist(), list(range(1, 19)))
        self.assertEqual(plan["command"].iloc[0], "python -m ashare_quant account-state")
        self.assertIn("python -m ashare_quant export-orders", commands)
        self.assertIn("python -m ashare_quant rpa-preflight", commands)
        self.assertIn("python -m ashare_quant order-audit", commands)
        self.assertIn("python -m ashare_quant auto-live-check", commands)
        self.assertIn("python -m ashare_quant auto-live-adapter-selftest", commands)
        self.assertIn("python -m ashare_quant auto-live-account", commands)
        self.assertIn("python -m ashare_quant auto-live-window", commands)
        self.assertIn("python -m ashare_quant auto-live-risk", commands)
        self.assertIn("python -m ashare_quant auto-live-lock", commands)
        self.assertIn("--auto-live-dry-run", commands)
        self.assertIn("python -m ashare_quant auto-live-run", commands)
        self.assertIn("python -m ashare_quant auto-live-query", commands)
        self.assertIn("python -m ashare_quant auto-live-fills", commands)
        self.assertIn("python -m ashare_quant auto-live-cancel", commands)
        self.assertIn("python -m ashare_quant auto-live-settle", commands)
        self.assertFalse(plan["command"].astype(str).str.contains("allow_broker_submit=true", regex=False).any())
        self.assertTrue(bool(plan["stop_on_failure"].iloc[0]))
        self.assertEqual(len(built), len(plan))

    def test_remediation_plan_bootstraps_missing_auto_live_audit(self):
        output = Path.cwd() / "test_outputs" / "remediation_missing_audit"
        output.mkdir(parents=True, exist_ok=True)
        path = write_auto_live_remediation_plan({"paths": {"output_dir": str(output)}})
        plan = pd.read_csv(path)

        self.assertEqual(plan["blocker"].iloc[0], "auto_live_blocker_details_missing")
        self.assertIn("auto-live-check", plan["command"].iloc[0])


if __name__ == "__main__":
    unittest.main()
