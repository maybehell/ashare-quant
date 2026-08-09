import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import auto_live_account_snapshot_freshness_status, build_auto_live_readiness, refresh_auto_live_account_snapshot_if_needed, run_auto_live, write_auto_live_adapter_selftest
from ashare_quant.autolive_account import (
    build_auto_live_broker_account_audit,
    broker_clock_status,
    live_ledger_initialization_status,
    normalize_command_adapter_account_snapshot,
    query_auto_live_account,
)


class AutoLiveAccountTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        self.output.mkdir(parents=True, exist_ok=True)
        for child in self.output.glob("*"):
            if child.is_file():
                child.unlink()

    def write_bridge(self) -> Path:
        bridge = self.output / "broker_bridge_account.py"
        bridge.write_text(
            """import json, sys\n"""
            """request_path, result_path = sys.argv[1], sys.argv[2]\n"""
            """payload = json.load(open(request_path, encoding='utf-8'))\n"""
            """if payload.get('mode') == 'self_test':\n"""
            """    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}\n"""
            """elif payload.get('mode') == 'account_snapshot':\n"""
            """    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'broker': 'test-broker', 'environment': 'live', 'auto_live_config_sha256': payload['auto_live_config_sha256'], 'account': {'account_id': 'acct-1', 'cash': 1000000.0, 'available_cash': 1000000.0, 'market_value': 0.0, 'total_asset': 1000000.0, 'frozen_cash': 0.0}, 'positions': [], 'message': 'account-ok'}\n"""
            """else:\n"""
            """    result = {'results': []}\n"""
            """json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n""",
            encoding="utf-8",
        )
        return bridge

    def write_local_account(self):
        pd.DataFrame(
            [
                {
                    "snapshot_date": "2026-07-04",
                    "mark_date": "2026-07-04",
                    "cash": 1000000.0,
                    "recorded_equity": 1000000.0,
                    "market_value": 0.0,
                    "marked_equity": 1000000.0,
                    "position_count": 0,
                    "locked_quantity": 0,
                    "sellable_quantity": 0,
                }
            ]
        ).to_csv(self.output / "account_state_snapshot.csv", index=False)
        pd.DataFrame(columns=["symbol", "quantity", "locked_quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"]).to_csv(self.output / "account_state_positions.csv", index=False)

    def config(self, bridge: Path) -> dict:
        return {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
            },
        }

    def write_execution_gates(self):
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(self.output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(self.output / "execution_modes.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "estimated_amount": 1000.0, "status": "ready", "date": "2026-07-10"}]).to_csv(self.output / "rpa_orders.csv", index=False)

    def test_configured_live_ledger_requires_account_snapshot_row(self):
        import sqlite3

        state_db = self.output / "live.sqlite3"
        config = {"paths": {"state_db": str(state_db)}}

        self.assertFalse(live_ledger_initialization_status(config)[0])
        with sqlite3.connect(state_db) as conn:
            conn.execute("create table account_snapshots (id integer)")
        empty_status = live_ledger_initialization_status(config)
        self.assertFalse(empty_status[0])
        self.assertIn("account_snapshots=0", empty_status[1])

        with sqlite3.connect(state_db) as conn:
            conn.execute("insert into account_snapshots values (1)")
        initialized_status = live_ledger_initialization_status(config)
        self.assertTrue(initialized_status[0])
        self.assertIn("account_snapshots=1", initialized_status[1])

    def test_missing_live_ledger_blocks_all_local_reconciliation_without_numeric_gap(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        config["paths"]["state_db"] = str(self.output / "missing-live.sqlite3")
        self.write_local_account()
        snapshot_run_id = "snapshot-missing-ledger"
        snapshot = pd.DataFrame([{
            "snapshot_run_id": snapshot_run_id,
            "broker": "mock",
            "account_id": "acct-1",
            "environment": "live",
            "cash": 1000000.0,
            "available_cash": 1000000.0,
            "market_value": 0.0,
            "total_asset": 1000000.0,
            "frozen_cash": 0.0,
            "positions_count": 0,
            "broker_time": "",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "auto_live_config_sha256": "",
            "message": "",
        }])

        audit = build_auto_live_broker_account_audit(
            config, snapshot, pd.DataFrame(), snapshot_run_id, True, "mock_ready"
        ).set_index("check")

        for check in (
            "local_live_ledger_initialized",
            "local_account_snapshot_present",
            "local_vs_broker_cash_match",
            "local_vs_broker_total_asset_match",
            "local_vs_broker_position_symbols_match",
            "local_vs_broker_position_quantities_match",
            "local_vs_broker_sellable_quantities_match",
        ):
            self.assertFalse(bool(audit.loc[check, "passed"]), check)
        self.assertEqual(audit.loc["local_vs_broker_cash_match", "value"], "live_ledger_not_initialized")
        self.assertEqual(audit.loc["local_vs_broker_total_asset_match", "value"], "live_ledger_not_initialized")
        self.assertNotIn("gap=", str(audit.loc["local_vs_broker_cash_match", "value"]))
    def test_account_snapshot_command_adapter_reconciles_with_local_state(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_local_account()
        self.write_execution_gates()

        write_auto_live_adapter_selftest(config)
        paths = query_auto_live_account(config)
        snapshot = pd.read_csv(paths["auto_live_broker_account_snapshot.csv"])
        audit = pd.read_csv(paths["auto_live_broker_account_audit.csv"])
        readiness = build_auto_live_readiness(config)

        self.assertEqual(snapshot["broker"].tolist(), ["test-broker"])
        self.assertEqual(snapshot["environment"].tolist(), ["live"])
        self.assertEqual(float(snapshot["total_asset"].iloc[0]), 1000000.0)
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertTrue(bool(readiness.loc[readiness["check"] == "broker_account_snapshot_audit_passed", "passed"].iloc[0]))
        self.assertTrue(list(self.output.glob("auto_live_account_request_*.json")))


    def test_account_snapshot_normalization_preserves_invalid_money_as_failure(self):
        result_path = self.output / "invalid-money.json"
        result_path.write_text(
            json.dumps({
                "broker": "test-broker",
                "environment": "live",
                "account": {
                    "account_id": "acct-1",
                    "cash": "unreadable",
                    "available_cash": 0,
                    "market_value": 0,
                    "total_asset": 0,
                    "frozen_cash": 0,
                },
                "positions": [],
            }),
            encoding="utf-8",
        )

        snapshot, positions = normalize_command_adapter_account_snapshot(
            "invalid-money", result_path
        )
        self.assertTrue(pd.isna(snapshot["cash"].iloc[0]))
        audit = build_auto_live_broker_account_audit(
            {"paths": {"output_dir": str(self.output)}},
            snapshot,
            positions,
            "invalid-money",
            True,
            "command_ready",
        ).set_index("check")
        self.assertFalse(bool(audit.loc["broker_account_money_non_negative", "passed"]))

    def test_account_snapshot_normalization_rejects_non_list_positions(self):
        result_path = self.output / "invalid-positions.json"
        result_path.write_text(
            json.dumps({"broker": "test-broker", "account": {}, "positions": {}}),
            encoding="utf-8",
        )

        snapshot, positions = normalize_command_adapter_account_snapshot(
            "invalid-positions", result_path
        )
        self.assertTrue(positions.empty)
        self.assertEqual(snapshot["environment"].tolist(), ["unknown"])
        self.assertIn("positions must be a list", snapshot["message"].iloc[0])
    def test_account_audit_rejects_sellable_quantity_mismatch(self):
        config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {"allow_simulated_account_snapshot": True},
        }
        self.write_local_account()
        pd.DataFrame([
            {
                "symbol": "600000", "quantity": 100, "locked_quantity": 50,
                "sellable_quantity": 50, "cost": 10.0, "close": 10.0,
                "market_value": 1000.0, "pnl": 0.0,
            }
        ]).to_csv(self.output / "account_state_positions.csv", index=False)
        snapshot_run_id = "snapshot-sellable"
        snapshot = pd.DataFrame([
            {
                "snapshot_run_id": snapshot_run_id, "broker": "mock", "account_id": "mock",
                "environment": "simulation", "cash": 1000000.0, "available_cash": 1000000.0,
                "market_value": 1000.0, "total_asset": 1001000.0, "frozen_cash": 0.0,
                "positions_count": 1, "broker_time": "", "generated_at": datetime.now().isoformat(timespec="seconds"),
                "auto_live_config_sha256": "", "message": "",
            }
        ])
        positions = pd.DataFrame([
            {
                "snapshot_run_id": snapshot_run_id, "symbol": "600000", "quantity": 100,
                "sellable_quantity": 100, "cost": 10.0, "market_value": 1000.0,
                "last_price": 10.0, "message": "",
            }
        ])

        audit = build_auto_live_broker_account_audit(
            config, snapshot, positions, snapshot_run_id, True, "mock_ready"
        ).set_index("check")

        self.assertTrue(bool(audit.loc["local_vs_broker_position_quantities_match", "passed"]))
        self.assertFalse(bool(audit.loc["local_vs_broker_sellable_quantities_match", "passed"]))

    def test_account_audit_rejects_cash_partition_overflow(self):
        config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {"allow_simulated_account_snapshot": True},
        }
        self.write_local_account()
        snapshot_run_id = "snapshot-cash-overflow"
        snapshot = pd.DataFrame([{
            "snapshot_run_id": snapshot_run_id,
            "broker": "mock",
            "account_id": "mock",
            "environment": "simulation",
            "cash": 1000000.0,
            "available_cash": 900000.0,
            "market_value": 0.0,
            "total_asset": 1000000.0,
            "frozen_cash": 200000.0,
            "positions_count": 0,
            "broker_time": "",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "auto_live_config_sha256": "",
            "message": "",
        }])

        audit = build_auto_live_broker_account_audit(
            config,
            snapshot,
            pd.DataFrame(),
            snapshot_run_id,
            True,
            "mock_ready",
        ).set_index("check")

        self.assertFalse(bool(audit.loc["broker_cash_fields_consistent", "passed"]))

    def test_account_audit_rejects_duplicate_rows_and_summary_count_mismatch(self):
        config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {"allow_simulated_account_snapshot": True},
        }
        self.write_local_account()
        snapshot_run_id = "snapshot-duplicate-position"
        snapshot = pd.DataFrame([{
            "snapshot_run_id": snapshot_run_id,
            "broker": "mock",
            "account_id": "mock",
            "environment": "simulation",
            "cash": 999000.0,
            "available_cash": 999000.0,
            "market_value": 1000.0,
            "total_asset": 1000000.0,
            "frozen_cash": 0.0,
            "positions_count": 1,
            "broker_time": "",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "auto_live_config_sha256": "",
            "message": "",
        }])
        positions = pd.DataFrame([
            {"snapshot_run_id": snapshot_run_id, "symbol": "600000", "quantity": 50, "sellable_quantity": 50, "cost": 9.0, "market_value": 500.0, "last_price": 10.0, "message": ""},
            {"snapshot_run_id": snapshot_run_id, "symbol": "600000", "quantity": 50, "sellable_quantity": 50, "cost": 9.0, "market_value": 500.0, "last_price": 10.0, "message": ""},
        ])

        audit = build_auto_live_broker_account_audit(
            config, snapshot, positions, snapshot_run_id, True, "mock_ready"
        ).set_index("check")

        self.assertFalse(bool(audit.loc["broker_position_symbols_unique", "passed"]))
        self.assertFalse(bool(audit.loc["broker_positions_count_consistent", "passed"]))
        self.assertTrue(bool(audit.loc["broker_position_market_value_reconciles", "passed"]))

    def test_account_audit_rejects_price_or_unit_column_misread(self):
        config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {"allow_simulated_account_snapshot": True},
        }
        self.write_local_account()
        snapshot_run_id = "snapshot-unit-misread"
        snapshot = pd.DataFrame([{
            "snapshot_run_id": snapshot_run_id, "broker": "mock", "account_id": "mock",
            "environment": "simulation", "cash": 999000.0, "available_cash": 999000.0,
            "market_value": 1000.0, "total_asset": 1000000.0, "frozen_cash": 0.0,
            "positions_count": 1, "broker_time": "",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "auto_live_config_sha256": "", "message": "",
        }])
        positions = pd.DataFrame([{
            "snapshot_run_id": snapshot_run_id, "symbol": "600000", "quantity": 100,
            "sellable_quantity": 100, "cost": 10.0, "market_value": 1000.0,
            "last_price": 1000.0, "message": "",
        }])

        audit = build_auto_live_broker_account_audit(
            config, snapshot, positions, snapshot_run_id, True, "mock_ready"
        ).set_index("check")

        self.assertFalse(bool(audit.loc["broker_position_row_values_consistent", "passed"]))
        self.assertTrue(bool(audit.loc["broker_position_market_value_reconciles", "passed"]))

    def test_readiness_rejects_stale_or_config_mismatched_snapshot(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_local_account()
        self.write_execution_gates()
        write_auto_live_adapter_selftest(config)
        paths = query_auto_live_account(config)

        snapshot = pd.read_csv(paths["auto_live_broker_account_snapshot.csv"])
        snapshot.loc[0, "generated_at"] = "2000-01-01T00:00:00"
        snapshot.to_csv(paths["auto_live_broker_account_snapshot.csv"], index=False)
        stale_readiness = build_auto_live_readiness(config)
        stale_failed = set(stale_readiness.loc[~stale_readiness["passed"].astype(bool), "check"])
        self.assertIn("broker_account_snapshot_fresh", stale_failed)
        for request_path in self.output.glob("auto_live_account_request_*.json"):
            request_path.unlink()
        run_paths = run_auto_live(config, raise_on_blocked=False)
        refresh = pd.read_csv(run_paths["auto_live_account_refresh.csv"])
        run = pd.read_csv(run_paths["auto_live_run.csv"])
        self.assertTrue(bool(refresh["attempted"].iloc[0]))
        self.assertEqual(refresh["status"].iloc[0], "refreshed")
        self.assertTrue(list(self.output.glob("auto_live_account_request_*.json")))
        self.assertNotIn("broker_account_snapshot_fresh", str(run["blocked_reason"].iloc[0]))

        snapshot = pd.read_csv(paths["auto_live_broker_account_snapshot.csv"])
        snapshot.loc[0, "generated_at"] = datetime.now().isoformat(timespec="seconds")
        snapshot.to_csv(paths["auto_live_broker_account_snapshot.csv"], index=False)
        config["auto_live"]["account_snapshot_max_age_seconds"] = 120
        changed_readiness = build_auto_live_readiness(config)
        changed_failed = set(changed_readiness.loc[~changed_readiness["passed"].astype(bool), "check"])
        self.assertIn("broker_account_snapshot_config_match", changed_failed)

    def test_account_refresh_reuses_valid_snapshot_without_broker_call(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        self.write_local_account()
        self.write_execution_gates()
        write_auto_live_adapter_selftest(config)
        query_auto_live_account(config)
        before = set(self.output.glob("auto_live_account_request_*.json"))

        paths = refresh_auto_live_account_snapshot_if_needed(config)
        refresh = pd.read_csv(paths["auto_live_account_refresh.csv"])

        self.assertFalse(bool(refresh["attempted"].iloc[0]))
        self.assertEqual(refresh["status"].iloc[0], "reused")
        self.assertEqual(set(self.output.glob("auto_live_account_request_*.json")), before)

    def test_failed_simulation_refresh_blocks_before_submit_request(self):
        bridge = self.write_bridge()
        source = bridge.read_text(encoding="utf-8").replace("'environment': 'live'", "'environment': 'simulation'")
        bridge.write_text(source, encoding="utf-8")
        config = self.config(bridge)
        self.write_local_account()
        self.write_execution_gates()
        write_auto_live_adapter_selftest(config)

        paths = run_auto_live(config, raise_on_blocked=False)
        refresh = pd.read_csv(paths["auto_live_account_refresh.csv"])
        run = pd.read_csv(paths["auto_live_run.csv"])

        self.assertTrue(bool(refresh["attempted"].iloc[0]))
        self.assertEqual(refresh["status"].iloc[0], "failed")
        self.assertIn("broker_account_snapshot_environment_live", refresh["remaining_blockers"].iloc[0])
        self.assertIn("broker_account_snapshot", run["blocked_reason"].iloc[0])
        self.assertFalse(list(self.output.glob("auto_live_command_request_*.json")))

    def test_disabled_auto_live_defers_refresh_without_broker_call(self):
        bridge = self.write_bridge()
        config = self.config(bridge)
        config["auto_live"]["enabled"] = False

        paths = refresh_auto_live_account_snapshot_if_needed(config)
        refresh = pd.read_csv(paths["auto_live_account_refresh.csv"])

        self.assertFalse(bool(refresh["attempted"].iloc[0]))
        self.assertEqual(refresh["status"].iloc[0], "deferred")
        self.assertEqual(refresh["trigger"].iloc[0], "auto_live_disabled")
        self.assertFalse(list(self.output.glob("auto_live_account_request_*.json")))

    def test_freshness_check_normalizes_timezone_aware_broker_timestamp(self):
        config = {"auto_live": {"account_snapshot_max_age_seconds": 300}}
        snapshot = pd.DataFrame([{"generated_at": "2026-07-10T01:30:00+00:00"}])
        now = datetime(2026, 7, 10, 9, 31, tzinfo=timezone(timedelta(hours=8)))

        passed, value, threshold = auto_live_account_snapshot_freshness_status(config, snapshot, now=now)

        self.assertTrue(passed)
        self.assertIn("age_seconds=60.0", value)
        self.assertIn("300", threshold)

    def test_required_broker_clock_rejects_missing_naive_and_excessive_skew(self):
        config = {"auto_live": {"require_broker_clock_sync": True, "max_broker_clock_skew_seconds": 5}}
        now = datetime(2026, 7, 10, 9, 30, tzinfo=timezone(timedelta(hours=8)))

        missing = pd.DataFrame([{"broker_time": ""}])
        self.assertFalse(broker_clock_status(config, missing, now=now)[0])

        naive = pd.DataFrame([{"broker_time": "2026-07-10T09:30:00"}])
        self.assertFalse(broker_clock_status(config, naive, now=now)[0])

        synchronized = pd.DataFrame([{"broker_time": "2026-07-10T01:29:57+00:00"}])
        passed, value, _ = broker_clock_status(config, synchronized, now=now)
        self.assertTrue(passed)
        self.assertIn("skew_seconds=3.000", value)

        drifted = pd.DataFrame([{"broker_time": "2026-07-10T01:29:50+00:00"}])
        self.assertFalse(broker_clock_status(config, drifted, now=now)[0])

    def test_optional_broker_clock_allows_missing_but_rejects_malformed_value(self):
        config = {"auto_live": {"require_broker_clock_sync": False}}
        self.assertTrue(broker_clock_status(config, pd.DataFrame(), now=datetime.now(timezone.utc))[0])
        malformed = pd.DataFrame([{"broker_time": "not-a-time"}])
        self.assertFalse(broker_clock_status(config, malformed, now=datetime.now(timezone.utc))[0])

    def test_simulation_snapshot_is_blocked_even_when_balances_match(self):
        bridge = self.write_bridge()
        source = bridge.read_text(encoding="utf-8").replace("'environment': 'live'", "'environment': 'simulation'")
        bridge.write_text(source, encoding="utf-8")
        config = self.config(bridge)
        self.write_local_account()
        self.write_execution_gates()

        write_auto_live_adapter_selftest(config)
        paths = query_auto_live_account(config, raise_on_blocked=False)
        snapshot = pd.read_csv(paths["auto_live_broker_account_snapshot.csv"])
        audit = pd.read_csv(paths["auto_live_broker_account_audit.csv"])

        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertEqual(snapshot["environment"].tolist(), ["simulation"])
        self.assertIn("broker_account_environment_live", failed)
        self.assertTrue(bool(audit.loc[audit["check"] == "local_vs_broker_cash_match", "passed"].iloc[0]))

        config["auto_live"]["allow_simulated_account_snapshot"] = True
        write_auto_live_adapter_selftest(config)
        allowed_paths = query_auto_live_account(config)
        allowed_audit = pd.read_csv(allowed_paths["auto_live_broker_account_audit.csv"])
        self.assertTrue(allowed_audit["passed"].astype(bool).all())


if __name__ == "__main__":
    unittest.main()
