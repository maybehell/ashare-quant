from pathlib import Path
import shutil
import unittest

import pandas as pd

from ashare_quant.account_impact import build_account_exclusion_impact


class AccountExclusionImpactTests(unittest.TestCase):
    def test_ignores_stale_exclusions_when_current_account_is_clean(self):
        root = Path.cwd() / "test_outputs" / "account_exclusion_stale_ignored"
        if root.exists():
            shutil.rmtree(root)
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}

        pd.DataFrame(
            [
                {
                    "symbol": "600002",
                    "quantity": 5000,
                    "sellable_quantity": 5000,
                    "cost": 9.8,
                    "price_status": "missing_price",
                    "issue": "old repair file",
                    "suggested_action": "review",
                }
            ]
        ).to_csv(output / "account_repair_excluded_positions.csv", index=False)
        pd.DataFrame(
            [
                {
                    "snapshot_date": "2026-06-16",
                    "mark_date": "2026-06-16",
                    "cash": 1000000.0,
                    "recorded_equity": 1000000.0,
                    "market_value": 0.0,
                    "marked_equity": 1000000.0,
                    "position_count": 0,
                }
            ]
        ).to_csv(output / "account_state_snapshot.csv", index=False)
        pd.DataFrame(
            [
                {"check": "state_db_readable", "passed": True, "value": "ok", "threshold": "ok", "message": "ok"},
                {"check": "account_snapshot_present", "passed": True, "value": "present", "threshold": "present", "message": "ok"},
            ]
        ).to_csv(output / "account_state_audit.csv", index=False)
        pd.DataFrame(columns=["price_context", "symbol", "quantity", "cost"]).to_csv(output / "account_state_missing_prices.csv", index=False)

        summary, detail = build_account_exclusion_impact(config)
        by_check = summary.set_index("check")

        self.assertEqual(by_check.loc["stale_exclusion_file_ignored", "value"], "yes")
        self.assertTrue(bool(by_check.loc["excluded_position_count", "passed"]))
        self.assertTrue(bool(by_check.loc["priced_only_template_available", "passed"]))
        self.assertTrue(bool(by_check.loc["priced_only_precheck_passed", "passed"]))
        self.assertTrue(bool(by_check.loc["manual_ack_required", "passed"]))
        self.assertTrue(detail.empty)

    def test_requires_priced_only_precheck_when_exclusions_exist(self):
        root = Path.cwd() / "test_outputs" / "account_exclusion_requires_precheck"
        if root.exists():
            shutil.rmtree(root)
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}

        pd.DataFrame(
            [
                {
                    "symbol": "600002",
                    "quantity": 5000,
                    "sellable_quantity": 5000,
                    "cost": 9.8,
                    "price_status": "missing_price",
                    "issue": "missing price",
                    "suggested_action": "review",
                }
            ]
        ).to_csv(output / "account_repair_excluded_positions.csv", index=False)
        pd.DataFrame([{"marked_equity": 1000000.0, "cash": 1000000.0}]).to_csv(output / "account_state_snapshot.csv", index=False)
        pd.DataFrame([{"check": "mark_positions_priced", "passed": False, "value": 0, "threshold": 1, "message": "missing"}]).to_csv(
            output / "account_state_audit.csv",
            index=False,
        )
        pd.DataFrame([{"price_context": "mark", "symbol": "600002", "quantity": 5000, "cost": 9.8}]).to_csv(
            output / "account_state_missing_prices.csv",
            index=False,
        )

        summary, detail = build_account_exclusion_impact(config)
        by_check = summary.set_index("check")

        self.assertFalse(bool(by_check.loc["excluded_position_count", "passed"]))
        self.assertFalse(bool(by_check.loc["priced_only_template_available", "passed"]))
        self.assertFalse(bool(by_check.loc["priced_only_precheck_passed", "passed"]))
        self.assertEqual(len(detail), 1)


if __name__ == "__main__":
    unittest.main()
