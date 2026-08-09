import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.observation_plan import build_observation_schedule, estimate_next_observation_date, write_observation_schedule


class ObservationPlanTests(unittest.TestCase):
    def test_estimate_next_observation_date_skips_weekends(self):
        self.assertEqual(estimate_next_observation_date(None, today=pd.Timestamp("2026-07-04").date()).isoformat(), "2026-07-06")

    def test_schedule_collects_forward_shadow_and_requires_exclusion_ack(self):
        output = Path.cwd() / "test_outputs" / "observation_schedule"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output), "cache_dir": str(output / "cache"), "state_db": str(output / "state.sqlite3")}}
        pd.DataFrame(
            [
                {"area": "forward_validation", "status": "collecting", "current": 9, "required": 60, "remaining": 51, "blocker": "candidate_forward_trading_days", "command": "observe", "next_action": ""},
                {"area": "shadow_observation", "status": "collecting", "current": 1, "required": 20, "remaining": 19, "blocker": "shadow_observed_signal_count", "command": "observe", "next_action": ""},
                {"area": "account_audit", "status": "blocked", "current": 1, "required": 0, "remaining": 1, "blocker": "missing_position_prices", "command": "account-price-template", "next_action": ""},
                {"area": "rpa_pre_trade", "status": "blocked", "current": 1, "required": 0, "remaining": 1, "blocker": "account_state_audit", "command": "account-state", "next_action": ""},
                {"area": "strict_live_gate", "status": "blocked", "current": "blocked", "required": "ready", "remaining": "", "blocker": "strict_live_readiness", "command": "observe", "next_action": ""},
                {"area": "auto_live_config", "status": "blocked", "current": False, "required": True, "remaining": 1, "blocker": "auto_live_enabled", "command": "auto-live-check", "next_action": ""},
            ]
        ).to_csv(output / "evidence_plan.csv", index=False)
        pd.DataFrame([{"path": "real_account_priced_only_import", "status": "precheck_passed_with_exclusions"}]).to_csv(output / "account_path_decision.csv", index=False)

        schedule = build_observation_schedule(config)
        by_area = schedule.set_index("area")

        self.assertEqual(by_area.loc["daily_observation_collection", "status"], "scheduled")
        self.assertIn("forward-daily --refresh --quiet", by_area.loc["daily_observation_collection", "command"])
        self.assertIn("--ack-exclusions", by_area.loc["account_audit", "command"])
        self.assertEqual(by_area.loc["forward_validation", "remaining"], 51)

        path = write_observation_schedule(config)
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
