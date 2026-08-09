import hashlib
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.autolive_fills import (
    build_auto_live_fill_import_frame,
    build_auto_live_fill_sync_audit,
    build_auto_live_incremental_fills,
    load_auto_live_imported_fill_progress,
    settle_auto_live_fills,
    write_auto_live_fill_sync,
)


class AutoLiveFillSyncTests(unittest.TestCase):
    def test_builds_fill_import_rows_from_filled_status(self):
        statuses = pd.DataFrame(
            [
                {
                    "query_run_id": "q1",
                    "source_run_id": "r1",
                    "trade_date": "2026-07-04",
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "broker_order_id": "b1",
                    "broker_status": "filled",
                    "filled_quantity": 100,
                    "avg_fill_price": 10.2,
                    "filled_amount": 1020.0,
                    "fee": 1.0,
                    "message": "filled",
                },
                {
                    "query_run_id": "q1",
                    "source_run_id": "r1",
                    "trade_date": "2026-07-04",
                    "ticket_id": "t2",
                    "symbol": "000001",
                    "side": "sell",
                    "quantity": 200,
                    "limit_price": 12.0,
                    "broker_order_id": "b2",
                    "broker_status": "accepted",
                    "filled_quantity": 0,
                    "avg_fill_price": 0,
                    "filled_amount": 0,
                    "fee": 0,
                    "message": "accepted",
                },
            ]
        )

        fills = build_auto_live_fill_import_frame(statuses)

        self.assertEqual(fills.columns.tolist(), ["date", "symbol", "side", "quantity", "price", "fee", "amount", "reason"])
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills["date"].iloc[0], "2026-07-04")
        self.assertEqual(fills["reason"].iloc[0], "auto_live:r1:b1:cum100")

    def test_cumulative_partial_fill_only_imports_increment_and_replay_is_idempotent(self):
        statuses = pd.DataFrame([
            {
                "query_run_id": "q2", "source_run_id": "r1", "trade_date": "2026-07-04",
                "ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 200,
                "limit_price": 11.0, "broker_order_id": "b1", "broker_status": "filled",
                "filled_quantity": 200, "avg_fill_price": 10.5,
                "filled_amount": 2100.0, "fee": 2.0, "message": "filled",
            }
        ])
        first_partial = {"auto_live:r1:b1": {"quantity": 100, "amount": 1000.0, "fee": 1.0}}

        fills, progress = build_auto_live_incremental_fills(statuses, first_partial)

        self.assertEqual(len(fills), 1)
        self.assertEqual(int(fills.loc[0, "quantity"]), 100)
        self.assertEqual(float(fills.loc[0, "amount"]), 1100.0)
        self.assertEqual(float(fills.loc[0, "price"]), 11.0)
        self.assertEqual(float(fills.loc[0, "fee"]), 1.0)
        self.assertEqual(fills.loc[0, "reason"], "auto_live:r1:b1:cum200")
        self.assertEqual(progress.loc[0, "status"], "new_increment")

        fully_imported = {"auto_live:r1:b1": {"quantity": 200, "amount": 2100.0, "fee": 2.0}}
        replay_fills, replay_progress = build_auto_live_incremental_fills(statuses, fully_imported)
        self.assertTrue(replay_fills.empty)
        self.assertEqual(replay_progress.loc[0, "status"], "already_imported")

    def test_command_fill_without_exact_fee_provenance_is_not_importable(self):
        statuses = pd.DataFrame([{
            "source_run_id": "r1", "trade_date": "2026-07-04", "ticket_id": "t1",
            "symbol": "600000", "side": "buy", "broker_order_id": "b1",
            "broker_status": "filled", "filled_quantity": 100,
            "avg_fill_price": 10.0, "filled_amount": 1000.0, "fee": 0.0,
            "fee_status": "unavailable", "fee_source": "huatai_order_grid",
        }])
        fills, progress = build_auto_live_incremental_fills(
            statuses, {}, require_exact_fees=True
        )
        audit = build_auto_live_fill_sync_audit(
            {"auto_live": {"broker_adapter": "command"}}, statuses,
            pd.DataFrame([{"check": "all", "passed": True}]), fills, progress,
        ).set_index("check")

        self.assertTrue(fills.empty)
        self.assertEqual(progress.loc[0, "status"], "fee_not_exact")
        self.assertFalse(bool(audit.at["filled_fee_provenance_exact", "passed"]))
        self.assertFalse(bool(audit.at["cumulative_fill_progress_non_regressive", "passed"]))

    def test_exact_fee_provenance_allows_command_fill(self):
        statuses = pd.DataFrame([{
            "source_run_id": "r1", "trade_date": "2026-07-04", "ticket_id": "t1",
            "symbol": "600000", "side": "buy", "broker_order_id": "b1",
            "broker_status": "filled", "filled_quantity": 100,
            "avg_fill_price": 10.0, "filled_amount": 1000.0, "fee": 5.0,
            "fee_status": "exact", "fee_source": "broker_trade_ledger",
        }])
        fills, progress = build_auto_live_incremental_fills(
            statuses, {}, require_exact_fees=True
        )

        self.assertEqual(len(fills), 1)
        self.assertEqual(float(fills.loc[0, "fee"]), 5.0)
        self.assertEqual(progress.loc[0, "fee_status"], "exact")

    def test_cumulative_fill_regression_blocks_sync_audit(self):
        statuses = pd.DataFrame([
            {
                "source_run_id": "r1", "trade_date": "2026-07-04", "ticket_id": "t1",
                "symbol": "600000", "side": "buy", "broker_order_id": "b1",
                "broker_status": "partially_filled", "filled_quantity": 100,
                "avg_fill_price": 10.0, "filled_amount": 1000.0, "fee": 1.0,
            }
        ])
        imported = {"auto_live:r1:b1": {"quantity": 200, "amount": 2100.0, "fee": 2.0}}
        fills, progress = build_auto_live_incremental_fills(statuses, imported)
        status_audit = pd.DataFrame([{"check": "all", "passed": True}])

        audit = build_auto_live_fill_sync_audit({}, statuses, status_audit, fills, progress)

        row = audit[audit["check"] == "cumulative_fill_progress_non_regressive"].iloc[0]
        self.assertFalse(bool(row["passed"]))
        self.assertIn("cumulative_regression", str(row["value"]))

    def test_live_fill_progress_database_error_fails_closed(self):
        root = Path.cwd() / "test_outputs" / "auto_live_fill_progress_db_error"
        root.mkdir(parents=True, exist_ok=True)
        state_db = root / "broken.sqlite3"
        state_db.write_text("not a sqlite database", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Unable to load imported live-fill progress"):
            load_auto_live_imported_fill_progress({"paths": {"state_db": str(state_db)}})

        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [{"query_run_id": "q1", "source_run_id": "r1", "ticket_id": "t1", "broker_order_id": "b1", "broker_status": "accepted", "filled_quantity": 0}]
        ).to_csv(output / "auto_live_order_status.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": True}]).to_csv(
            output / "auto_live_order_status_audit.csv", index=False
        )
        paths = write_auto_live_fill_sync(
            {"paths": {"output_dir": str(output), "state_db": str(state_db)}}
        )
        audit = pd.read_csv(paths["auto_live_fill_sync_audit.csv"])
        progress_check = audit.loc[audit["check"] == "fill_progress_database_readable"].iloc[0]
        self.assertFalse(bool(progress_check["passed"]))
        self.assertIn("Unable to load", progress_check["value"])

    def test_command_fill_sync_rejects_tampered_status_file(self):
        root = Path.cwd() / "test_outputs" / "auto_live_fill_status_tamper"
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        status_path = output / "auto_live_order_status.csv"
        pd.DataFrame(
            [
                {
                    "query_run_id": "q1",
                    "source_run_id": "r1",
                    "trade_date": "2026-07-04",
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "broker_order_id": "b1",
                    "broker_status": "filled",
                    "filled_quantity": 100,
                    "avg_fill_price": 10.0,
                    "filled_amount": 1000.0,
                    "fee": 1.0,
                    "fee_status": "exact",
                    "fee_source": "broker_trade_ledger",
                }
            ]
        ).to_csv(status_path, index=False, encoding="utf-8-sig")
        original_hash = hashlib.sha256(status_path.read_bytes()).hexdigest()
        pd.DataFrame(
            [
                {"check": "all", "passed": True, "value": "ok"},
                {"check": "order_status_file_sha256", "passed": True, "value": original_hash},
            ]
        ).to_csv(output / "auto_live_order_status_audit.csv", index=False)
        statuses = pd.read_csv(status_path)
        statuses.loc[0, "filled_amount"] = 2000.0
        statuses.to_csv(status_path, index=False, encoding="utf-8-sig")

        paths = write_auto_live_fill_sync(
            {"paths": {"output_dir": str(output)}, "auto_live": {"broker_adapter": "command"}}
        )
        audit = pd.read_csv(paths["auto_live_fill_sync_audit.csv"])
        integrity = audit.loc[audit["check"] == "fill_source_status_file_integrity"].iloc[0]
        self.assertFalse(bool(integrity["passed"]))
        self.assertEqual(integrity["value"], "order_status_file_sha256_mismatch")

    def test_write_sync_audits_missing_trade_date(self):
        root = Path.cwd() / "test_outputs" / "auto_live_fill_missing_date"
        root.mkdir(parents=True, exist_ok=True)
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "query_run_id": "q1",
                    "source_run_id": "r1",
                    "trade_date": "",
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "broker_order_id": "b1",
                    "broker_status": "filled",
                    "filled_quantity": 100,
                    "avg_fill_price": 10.0,
                    "filled_amount": 1000.0,
                    "fee": 1.0,
                    "message": "filled",
                }
            ]
        ).to_csv(output / "auto_live_order_status.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": True, "value": "ok", "threshold": "", "severity": "ok", "next_action": "", "message": ""}]).to_csv(output / "auto_live_order_status_audit.csv", index=False)

        paths = write_auto_live_fill_sync({"paths": {"output_dir": str(output)}, "reconciliation": {"cash_tolerance": 1.0}})
        audit = pd.read_csv(paths["auto_live_fill_sync_audit.csv"])
        fills = pd.read_csv(paths["auto_live_fill_import.csv"], dtype={"symbol": str})

        self.assertEqual(len(fills), 1)
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("filled_trade_dates_present", failed)

    def test_write_sync_allows_no_fills_after_successful_query(self):
        root = Path.cwd() / "test_outputs" / "auto_live_fill_no_fills"
        root.mkdir(parents=True, exist_ok=True)
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "query_run_id": "q1",
                    "source_run_id": "r1",
                    "trade_date": "",
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "broker_order_id": "b1",
                    "broker_status": "accepted",
                    "filled_quantity": 0,
                    "avg_fill_price": 0,
                    "filled_amount": 0,
                    "fee": 0,
                    "message": "accepted",
                }
            ]
        ).to_csv(output / "auto_live_order_status.csv", index=False)
        pd.DataFrame([{"check": "all", "passed": True, "value": "ok", "threshold": "", "severity": "ok", "next_action": "", "message": ""}]).to_csv(output / "auto_live_order_status_audit.csv", index=False)

        paths = write_auto_live_fill_sync({"paths": {"output_dir": str(output)}})
        audit = pd.read_csv(paths["auto_live_fill_sync_audit.csv"])
        fills = pd.read_csv(paths["auto_live_fill_import.csv"])

        self.assertTrue(fills.empty)
        self.assertTrue(audit["passed"].astype(bool).all())

    def test_settle_without_confirm_does_not_import_account(self):
        root = Path.cwd() / "test_outputs" / "auto_live_settle_no_confirm"
        root.mkdir(parents=True, exist_ok=True)
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "run_id": "r1",
                    "date": "2026-07-04",
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "status": "submitted",
                    "broker_order_id": "b1",
                    "message": "submitted",
                }
            ]
        ).to_csv(output / "auto_live_order_results.csv", index=False)
        config = {"paths": {"output_dir": str(output)}, "auto_live": {"broker_adapter": "mock", "allow_mock_adapter": True}}

        paths = settle_auto_live_fills(config, confirm_import=False)
        audit = pd.read_csv(paths["auto_live_settlement_audit.csv"])

        self.assertTrue((output / "auto_live_order_status.csv").exists())
        self.assertTrue((output / "auto_live_fill_import.csv").exists())
        self.assertFalse((output / "fill_import_audit.csv").exists())
        self.assertIn("account_import_confirmed", set(audit["check"]))
        self.assertFalse(bool(audit.loc[audit["check"] == "account_import_confirmed", "passed"].iloc[0]))

    def test_settle_confirm_requires_account_sync_config(self):
        root = Path.cwd() / "test_outputs" / "auto_live_settle_confirm_blocked"
        root.mkdir(parents=True, exist_ok=True)
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "run_id": "r1",
                    "date": "2026-07-04",
                    "ticket_id": "t1",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "status": "submitted",
                    "broker_order_id": "b1",
                    "message": "submitted",
                }
            ]
        ).to_csv(output / "auto_live_order_results.csv", index=False)
        config = {"paths": {"output_dir": str(output)}, "auto_live": {"broker_adapter": "mock", "allow_mock_adapter": True, "allow_account_sync": False}}

        paths = settle_auto_live_fills(config, confirm_import=True)
        audit = pd.read_csv(paths["auto_live_settlement_audit.csv"])

        self.assertFalse((output / "fill_import_audit.csv").exists())
        row = audit[audit["check"] == "account_sync_config_allowed"].iloc[0]
        self.assertFalse(bool(row["passed"]))
        self.assertEqual(str(row["value"]).lower(), "false")



if __name__ == "__main__":
    unittest.main()
