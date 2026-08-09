import json
import shutil
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import COMMAND_ADAPTER_SCHEMA, auto_live_config_sha256, build_auto_live_adapter_audit, build_auto_live_readiness
from ashare_quant.autolive_replay import build_auto_live_adapter_replay_audit, write_auto_live_adapter_replay_audit


class AutoLiveAdapterReplayTests(unittest.TestCase):
    def fresh_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def config(self, output: Path) -> dict:
        return {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "allow_broker_submit": True,
            },
        }

    def write_pair(self, output: Path, config: dict, mode: str, run_id: str, orders: list[dict], results: list[dict] | None = None, environment: str = "simulation") -> None:
        request_path = output / f"auto_live_{mode}_request_{run_id}.json"
        result_path = output / f"auto_live_{mode}_result_{run_id}.json"
        fingerprint = auto_live_config_sha256(config)
        request = {
            "schema_version": COMMAND_ADAPTER_SCHEMA,
            "mode": mode,
            "run_id": run_id,
            "result_path": str(result_path),
            "auto_live_config_sha256": fingerprint,
            "orders": orders,
        }
        if mode == "account_snapshot":
            result = {
                "schema_version": COMMAND_ADAPTER_SCHEMA,
                "run_id": run_id,
                "auto_live_config_sha256": fingerprint,
                "environment": environment,
                "account": {
                    "account_id": "masked-account",
                    "cash": 100000,
                    "available_cash": 90000,
                    "market_value": 10000,
                    "total_asset": 110000,
                    "frozen_cash": 0,
                    "positions_count": 1,
                },
                "positions": [
                    {
                        "symbol": "600000",
                        "quantity": 1000,
                        "sellable_quantity": 1000,
                        "cost": 9,
                        "last_price": 10,
                        "market_value": 10000,
                    }
                ],
            }
        else:
            result = {
                "schema_version": COMMAND_ADAPTER_SCHEMA,
                "run_id": run_id,
                "auto_live_config_sha256": fingerprint,
                "results": results or [],
            }
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    def write_complete_lifecycle(self, output: Path, config: dict, environment: str = "simulation", query_broker_id: str = "broker-1") -> None:
        submit_order = {"ticket_id": "ticket-1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0}
        linked_order = {**submit_order, "broker_order_id": "broker-1"}
        self.write_pair(output, config, "submit_orders", "submit-1", [submit_order], [{"ticket_id": "ticket-1", "status": "submitted", "broker_order_id": "broker-1"}])
        self.write_pair(output, config, "query_orders", "query-1", [linked_order], [{"ticket_id": "ticket-1", "broker_order_id": query_broker_id, "broker_status": "accepted", "filled_quantity": 0}])
        self.write_pair(output, config, "cancel_orders", "cancel-1", [linked_order], [{"ticket_id": "ticket-1", "broker_order_id": "broker-1", "cancel_status": "cancel_requested"}])
        self.write_pair(output, config, "account_snapshot", "account-1", [], environment=environment)

    def test_complete_simulation_lifecycle_is_certified_only_as_simulation(self):
        output = self.fresh_output("adapter_replay_simulation")
        config = self.config(output)
        self.write_complete_lifecycle(output, config)

        paths = write_auto_live_adapter_replay_audit(config)

        summary = pd.read_csv(paths["auto_live_adapter_replay_summary.csv"])
        audit = pd.read_csv(paths["auto_live_adapter_replay_audit.csv"])
        self.assertEqual(summary.loc[0, "status"], "certified_simulation")
        self.assertEqual(summary.loc[0, "profile"], "simulation")
        self.assertTrue(audit["passed"].astype(bool).all())

    def test_complete_live_lifecycle_is_certified_live(self):
        output = self.fresh_output("adapter_replay_live")
        config = self.config(output)
        self.write_complete_lifecycle(output, config, environment="live")

        _, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "certified_live")
        self.assertEqual(summary.loc[0, "profile"], "live")

    def test_live_account_only_is_certified_read_only_live(self):
        output = self.fresh_output("adapter_replay_read_only_live")
        config = self.config(output)
        self.write_pair(output, config, "account_snapshot", "account-1", [], environment="live")

        audit, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "certified_read_only_live")
        self.assertEqual(summary.loc[0, "profile"], "live")
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertEqual(
            failed,
            {
                "lifecycle_submit_orders_present",
                "lifecycle_query_orders_present",
                "lifecycle_cancel_orders_present",
                "lifecycle_submit_query_overlap",
                "lifecycle_query_broker_ids_stable",
                "lifecycle_submit_cancel_overlap",
                "lifecycle_cancel_broker_ids_stable",
            },
        )

    def test_account_cash_partition_mismatch_blocks_live_certification(self):
        output = self.fresh_output("adapter_replay_bad_cash_partition")
        config = self.config(output)
        self.write_complete_lifecycle(output, config, environment="live")
        result_path = output / "auto_live_account_snapshot_result_account-1.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["account"]["available_cash"] = 90000
        payload["account"]["frozen_cash"] = 20000
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        audit, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "failed")
        check = audit[audit["check"] == "account_cash_fields_consistent"].iloc[0]
        self.assertFalse(bool(check["passed"]))

    def test_account_position_unit_mismatch_blocks_live_certification(self):
        output = self.fresh_output("adapter_replay_bad_position_units")
        config = self.config(output)
        self.write_complete_lifecycle(output, config, environment="live")
        result_path = output / "auto_live_account_snapshot_result_account-1.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["positions"][0]["last_price"] = 1000
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        audit, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "failed")
        check = audit[audit["check"] == "account_position_row_values_consistent"].iloc[0]
        self.assertFalse(bool(check["passed"]))

    def test_duplicate_position_symbol_blocks_live_certification(self):
        output = self.fresh_output("adapter_replay_duplicate_position")
        config = self.config(output)
        self.write_complete_lifecycle(output, config, environment="live")
        result_path = output / "auto_live_account_snapshot_result_account-1.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["positions"].append(dict(payload["positions"][0]))
        payload["account"]["positions_count"] = 2
        payload["account"]["market_value"] = 20000
        payload["account"]["total_asset"] = 120000
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        audit, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "failed")
        check = audit[audit["check"] == "account_position_symbols_valid_unique"].iloc[0]
        self.assertFalse(bool(check["passed"]))

    def test_historical_config_requests_do_not_poison_current_certification(self):
        output = self.fresh_output("adapter_replay_historical_config")
        config = self.config(output)
        old_config = self.config(output)
        old_config["auto_live"]["legacy_marker"] = True
        self.write_pair(
            output,
            old_config,
            "account_snapshot",
            "historical-account",
            [],
            environment="live",
        )
        self.write_complete_lifecycle(output, config, environment="live")

        audit, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "certified_live")
        ignored = audit[audit["check"] == "historical_replay_requests_ignored"].iloc[0]
        self.assertEqual(int(ignored["value"]), 1)

    def test_missing_lifecycle_modes_is_incomplete(self):
        output = self.fresh_output("adapter_replay_incomplete")
        config = self.config(output)
        order = {"ticket_id": "ticket-1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0}
        self.write_pair(output, config, "submit_orders", "submit-1", [order], [{"ticket_id": "ticket-1", "status": "submitted", "broker_order_id": "broker-1"}])

        audit, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "incomplete")
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("lifecycle_query_orders_present", failed)
        self.assertIn("lifecycle_account_snapshot_present", failed)

    def test_changed_broker_order_id_fails_cross_lifecycle_binding(self):
        output = self.fresh_output("adapter_replay_changed_broker_id")
        config = self.config(output)
        self.write_complete_lifecycle(output, config, environment="live", query_broker_id="changed-id")

        audit, summary = build_auto_live_adapter_replay_audit(config, output)

        self.assertEqual(summary.loc[0, "status"], "failed")
        check = audit[audit["check"] == "lifecycle_query_broker_ids_stable"].iloc[0]
        self.assertFalse(bool(check["passed"]))


    def test_required_replay_gate_accepts_live_and_rejects_simulation(self):
        live_output = self.fresh_output("adapter_replay_gate_live")
        live_config = self.config(live_output)
        live_config["auto_live"]["require_adapter_replay_certification"] = True
        self.write_complete_lifecycle(live_output, live_config, environment="live")
        write_auto_live_adapter_replay_audit(live_config)
        live_audit = build_auto_live_adapter_audit(live_config).set_index("check")

        simulation_output = self.fresh_output("adapter_replay_gate_simulation")
        simulation_config = self.config(simulation_output)
        simulation_config["auto_live"]["require_adapter_replay_certification"] = True
        self.write_complete_lifecycle(simulation_output, simulation_config, environment="simulation")
        write_auto_live_adapter_replay_audit(simulation_config)
        simulation_audit = build_auto_live_adapter_audit(simulation_config).set_index("check")
        live_readiness = build_auto_live_readiness(live_config).set_index("check")
        simulation_readiness = build_auto_live_readiness(simulation_config).set_index("check")

        self.assertTrue(bool(live_audit.loc["command_adapter_replay_certified_live", "passed"]))
        self.assertFalse(bool(simulation_audit.loc["command_adapter_replay_certified_live", "passed"]))
        self.assertTrue(bool(live_readiness.loc["command_adapter_replay_certified_live", "passed"]))
        self.assertFalse(bool(simulation_readiness.loc["command_adapter_replay_certified_live", "passed"]))


if __name__ == "__main__":
    unittest.main()
