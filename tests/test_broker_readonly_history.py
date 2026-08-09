import unittest

import pandas as pd

from ashare_quant.broker_readonly_history import (
    append_history_frame,
    build_history_audit,
)


def snapshot(run_id: str, generated_at: str, account_id: str = "huatai-masked") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "snapshot_run_id": run_id,
                "broker": "huatai",
                "account_id": account_id,
                "environment": "live",
                "cash": "2567.44",
                "available_cash": "2567.44",
                "market_value": "0",
                "total_asset": "2567.44",
                "frozen_cash": "0",
                "unclassified_asset_value": "0",
                "positions_count": "0",
                "broker_time": "",
                "generated_at": generated_at,
                "auto_live_config_sha256": "config-hash",
            }
        ]
    )


class BrokerReadonlyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.positions = pd.DataFrame(
            columns=[
                "snapshot_run_id",
                "symbol",
                "quantity",
                "sellable_quantity",
                "cost",
                "market_value",
                "last_price",
                "message",
            ]
        )

    def test_history_is_idempotent_for_same_snapshot_run(self):
        current = snapshot("run-1", "2026-08-01T11:00:00+08:00")
        history = append_history_frame(pd.DataFrame(), current, self.positions, recorded_at="2026-08-01T11:01:00+08:00")
        repeated = append_history_frame(history, current, self.positions, recorded_at="2026-08-01T11:02:00+08:00")

        self.assertEqual(len(repeated), 1)
        audit = build_history_audit(repeated, current, self.positions)
        integrity = audit.set_index("check").loc["broker_readonly_history_integrity"]
        self.assertTrue(bool(integrity["passed"]))

    def test_depth_requires_three_samples_across_two_dates(self):
        history = pd.DataFrame()
        observations = [
            snapshot("run-1", "2026-08-01T11:00:00+08:00"),
            snapshot("run-2", "2026-08-01T12:00:00+08:00"),
            snapshot("run-3", "2026-08-03T09:40:00+08:00"),
        ]
        for current in observations:
            history = append_history_frame(history, current, self.positions)

        audit = build_history_audit(history, observations[-1], self.positions).set_index("check")
        self.assertTrue(bool(audit.loc["broker_readonly_history_integrity", "passed"]))
        self.assertTrue(bool(audit.loc["broker_readonly_history_depth", "passed"]))

    def test_tampering_breaks_hash_chain(self):
        current = snapshot("run-1", "2026-08-01T11:00:00+08:00")
        history = append_history_frame(pd.DataFrame(), current, self.positions)
        history.loc[0, "cash"] = "9999.99"

        audit = build_history_audit(history, current, self.positions).set_index("check")
        self.assertFalse(bool(audit.loc["hash_chain_valid", "passed"]))
        self.assertFalse(bool(audit.loc["broker_readonly_history_integrity", "passed"]))

    def test_account_identity_change_is_rejected(self):
        first = snapshot("run-1", "2026-08-01T11:00:00+08:00")
        second = snapshot("run-2", "2026-08-03T11:00:00+08:00", account_id="huatai-other")
        history = append_history_frame(pd.DataFrame(), first, self.positions)
        history = append_history_frame(history, second, self.positions)

        audit = build_history_audit(history, second, self.positions).set_index("check")
        self.assertFalse(bool(audit.loc["account_identity_stable", "passed"]))
        self.assertFalse(bool(audit.loc["broker_readonly_history_integrity", "passed"]))


if __name__ == "__main__":
    unittest.main()
