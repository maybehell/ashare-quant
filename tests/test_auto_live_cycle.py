import json
import shutil
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import run_auto_live
from ashare_quant.autolive_cycle import run_auto_live_cycle
from ashare_quant.autolive_risk import activate_auto_live_stop
from ashare_quant.audit import frame_sha256


class AutoLiveCycleTests(unittest.TestCase):
    def fresh_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def ready_config(self, output: Path) -> dict:
        return {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
                "risk_limits": {"enabled": True, "max_orders_per_batch": 10},
            },
        }

    def write_ready_inputs(self, output: Path) -> None:
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(
            output / "execution_readiness.csv", index=False
        )
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame(
            [
                {
                    "ticket_id": "cycle-t1",
                    "date": "2026-07-10",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                }
            ]
        ).to_csv(output / "rpa_orders.csv", index=False)

    def test_audit_cycle_never_submits_and_persists_all_stages(self):
        output = self.fresh_output("auto_live_cycle_audit")
        config = {"paths": {"output_dir": str(output)}}

        paths = run_auto_live_cycle(config)

        summary = pd.read_csv(paths["auto_live_cycle_summary.csv"])
        stages = pd.read_csv(paths["auto_live_cycle.csv"])
        self.assertEqual(summary.loc[0, "mode"], "audit")
        self.assertEqual(summary.loc[0, "status"], "audit_complete_with_blockers")
        self.assertEqual(stages.loc[stages["stage"] == "broker_submit", "status"].iloc[0], "skipped")
        self.assertFalse((output / "auto_live_submission_ledger.csv").exists())
        self.assertEqual(len(stages), 12)
        self.assertIn("batch_plan", set(stages["stage"]))

    def test_submit_cycle_runs_mock_submit_query_and_fill_sync(self):
        output = self.fresh_output("auto_live_cycle_submit")
        config = self.ready_config(output)
        self.write_ready_inputs(output)

        paths = run_auto_live_cycle(config, submit=True)

        summary = pd.read_csv(paths["auto_live_cycle_summary.csv"])
        stages = pd.read_csv(paths["auto_live_cycle.csv"]).set_index("stage")
        self.assertEqual(summary.loc[0, "status"], "completed")
        self.assertEqual(int(summary.loc[0, "submitted_count"]), 1)
        self.assertEqual(int(summary.loc[0, "queried_count"]), 1)
        self.assertEqual(stages.loc["broker_submit", "status"], "passed")
        self.assertEqual(stages.loc["post_submit_query", "status"], "passed")
        self.assertEqual(stages.loc["fill_sync", "status"], "passed")
        self.assertEqual(stages.loc["account_sync", "status"], "skipped")
        self.assertTrue((output / "auto_live_submission_ledger.csv").exists())

    def test_explicit_simulation_account_sync_completes_without_fills(self):
        output = self.fresh_output("auto_live_cycle_sync")
        config = self.ready_config(output)
        config["auto_live"]["allow_account_sync"] = True
        config["auto_live"]["allow_simulated_account_snapshot"] = True
        self.write_ready_inputs(output)
        pd.DataFrame(
            [
                {
                    "snapshot_date": "2026-07-10",
                    "mark_date": "2026-07-10",
                    "cash": 1000000.0,
                    "recorded_equity": 1000000.0,
                    "market_value": 0.0,
                    "marked_equity": 1000000.0,
                    "position_count": 0,
                    "locked_quantity": 0,
                    "sellable_quantity": 0,
                }
            ]
        ).to_csv(output / "account_state_snapshot.csv", index=False)
        pd.DataFrame(columns=["symbol", "quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"]).to_csv(
            output / "account_state_positions.csv", index=False
        )

        paths = run_auto_live_cycle(config, submit=True, confirm_account_sync=True)

        summary = pd.read_csv(paths["auto_live_cycle_summary.csv"])
        stages = pd.read_csv(paths["auto_live_cycle.csv"]).set_index("stage")
        settlement = pd.read_csv(output / "auto_live_settlement_audit.csv")
        self.assertEqual(summary.loc[0, "status"], "completed_and_synced")
        self.assertTrue(bool(summary.loc[0, "account_sync_completed"]))
        self.assertEqual(stages.loc["account_sync", "status"], "passed")
        self.assertEqual(stages.loc["post_trade_reconciliation", "status"], "passed")
        self.assertTrue(settlement["passed"].astype(bool).all())
        self.assertTrue((output / "auto_live_post_trade_reconciliation.csv").exists())
        self.assertTrue((output / "auto_live_post_trade_manifest.csv").exists())
        manifest = pd.read_csv(output / "auto_live_post_trade_manifest.csv")
        self.assertTrue(bool(manifest.loc[0, "passed"]))
        self.assertTrue(str(manifest.loc[0, "broker_snapshot_run_id"]))
        for column in [
            "local_snapshot_sha256", "local_positions_sha256", "broker_snapshot_sha256",
            "broker_positions_sha256", "fill_progress_sha256", "reconciliation_audit_sha256",
        ]:
            self.assertEqual(len(str(manifest.loc[0, column])), 64)
        journal = pd.read_csv(output / "auto_live_event_journal.csv")
        result_event = journal.loc[journal["event_type"].eq("cycle_result")].iloc[-1]
        payload = json.loads(result_event["payload_json"])
        self.assertEqual(payload["post_trade_manifest_sha256"], frame_sha256(manifest))
        self.assertFalse((output / "fill_import_audit.csv").exists())

    def test_failed_risk_gate_stops_before_adapter_call(self):
        output = self.fresh_output("auto_live_cycle_risk_block")
        config = self.ready_config(output)
        self.write_ready_inputs(output)
        activate_auto_live_stop(config, reason="cycle test")

        paths = run_auto_live_cycle(config, submit=True)

        summary = pd.read_csv(paths["auto_live_cycle_summary.csv"])
        stages = pd.read_csv(paths["auto_live_cycle.csv"]).set_index("stage")
        self.assertEqual(summary.loc[0, "status"], "blocked")
        self.assertEqual(stages.loc["broker_submit", "status"], "blocked")
        self.assertFalse((output / "auto_live_submission_ledger.csv").exists())

    def test_cycle_queries_unresolved_prior_batch_before_blocking_new_submit(self):
        output = self.fresh_output("auto_live_cycle_prior_recovery")
        config = self.ready_config(output)
        self.write_ready_inputs(output)
        run_auto_live(config)
        tickets = pd.read_csv(output / "rpa_orders.csv", dtype={"symbol": str})
        tickets.loc[0, "ticket_id"] = "cycle-t2"
        tickets.loc[0, "symbol"] = "000001"
        tickets.to_csv(output / "rpa_orders.csv", index=False)

        paths = run_auto_live_cycle(config, submit=True)

        summary = pd.read_csv(paths["auto_live_cycle_summary.csv"])
        stages = pd.read_csv(paths["auto_live_cycle.csv"]).set_index("stage")
        status = pd.read_csv(output / "auto_live_order_status.csv")
        ledger = pd.read_csv(output / "auto_live_submission_ledger.csv")
        self.assertEqual(summary.loc[0, "status"], "blocked")
        self.assertEqual(stages.loc["prior_batch_recovery", "status"], "blocked")
        self.assertEqual(stages.loc["broker_submit", "status"], "blocked")
        self.assertEqual(status.loc[0, "broker_status"], "accepted")
        self.assertEqual(len(ledger), 1)

    def test_account_sync_confirmation_requires_submit_mode(self):
        output = self.fresh_output("auto_live_cycle_sync_guard")
        with self.assertRaisesRegex(RuntimeError, "requires --submit"):
            run_auto_live_cycle({"paths": {"output_dir": str(output)}}, confirm_account_sync=True)


if __name__ == "__main__":
    unittest.main()
