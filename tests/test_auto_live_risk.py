import shutil
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import run_auto_live, submit_with_adapter
from ashare_quant.autolive_risk import (
    activate_auto_live_stop,
    build_auto_live_risk_audit,
    clear_auto_live_stop,
    read_auto_live_risk_reservations,
    upsert_auto_live_risk_reservation,
)


class AutoLiveRiskTests(unittest.TestCase):
    def fresh_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def config(self, output: Path, **risk_limits):
        return {
            "paths": {"output_dir": str(output)},
            "trading": {"reserve_cash_ratio": 0.01},
            "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000},
            "auto_live": {"broker_adapter": "mock", "risk_limits": {"enabled": True, **risk_limits}},
        }

    def tickets(self, ticket_id="t1", symbol="600000", amount=1000.0, side="buy"):
        price = 10.0
        quantity = int(amount / price)
        return pd.DataFrame(
            [
                {
                    "ticket_id": ticket_id,
                    "date": "2026-07-10",
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "limit_price": price,
                    "estimated_amount": amount,
                    "status": "ready",
                }
            ]
        )

    def test_valid_batch_passes_independent_risk_audit(self):
        output = self.fresh_output("auto_live_risk_valid")
        audit = build_auto_live_risk_audit(self.config(output), tickets=self.tickets())
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertIn("risk_daily_buy_amount_limit", set(audit["check"]))
        self.assertIn("risk_available_cash_buffer", set(audit["check"]))

    def test_runtime_limits_clamp_configured_buy_cap_to_system_ceiling(self):
        output = self.fresh_output("auto_live_risk_system_cap")
        config = self.config(
            output,
            max_single_order_amount=900000,
            max_batch_buy_amount=900000,
            max_batch_gross_amount=900000,
            max_daily_buy_amount=900000,
            max_daily_gross_amount=900000,
        )
        audit = build_auto_live_risk_audit(
            config, tickets=self.tickets(amount=200100)
        ).set_index("check")

        self.assertEqual(float(audit.loc["risk_single_order_amount_limit", "threshold"]), 200000.0)
        self.assertEqual(float(audit.loc["risk_batch_buy_amount_limit", "threshold"]), 200000.0)
        self.assertFalse(bool(audit.loc["risk_single_order_amount_limit", "passed"]))
        self.assertFalse(bool(audit.loc["risk_batch_buy_amount_limit", "passed"]))

    def test_emergency_stop_file_blocks_submit_risk(self):
        output = self.fresh_output("auto_live_risk_stop")
        config = self.config(output)
        paths = activate_auto_live_stop(config, reason="operator review")
        audit = build_auto_live_risk_audit(config, tickets=self.tickets()).set_index("check")

        self.assertTrue(paths["auto_live.stop"].exists())
        self.assertFalse(bool(audit.loc["emergency_stop_inactive", "passed"]))
        self.assertIn("file=True", audit.loc["emergency_stop_inactive", "value"])

    def test_resume_requires_confirmation_and_no_submit_lock(self):
        output = self.fresh_output("auto_live_risk_resume")
        config = self.config(output)
        activate_auto_live_stop(config)
        with self.assertRaisesRegex(RuntimeError, "confirmation_missing"):
            clear_auto_live_stop(config, confirm=False)
        self.assertTrue((output / "auto_live.stop").exists())
        (output / "auto_live_submit.lock").write_text("active", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "submit_lock_exists"):
            clear_auto_live_stop(config, confirm=True)
        (output / "auto_live_submit.lock").unlink()

        paths = clear_auto_live_stop(config, confirm=True)
        self.assertFalse((output / "auto_live.stop").exists())
        audit = pd.read_csv(paths["auto_live_stop_audit.csv"])
        self.assertTrue(bool(audit["passed"].iloc[0]))

    def test_daily_reservations_are_counted_conservatively(self):
        output = self.fresh_output("auto_live_risk_daily")
        config = self.config(output, max_daily_buy_amount=3500, max_daily_gross_amount=3500)
        prior = self.tickets(ticket_id="prior", symbol="000001", amount=3000)
        upsert_auto_live_risk_reservation(
            config,
            prior,
            run_id="run-prior",
            status="submission_uncertain",
            tickets_hash="prior-hash",
            auto_hash="config-hash",
            dedupe_key="prior-key",
            message="unknown broker state",
        )

        audit = build_auto_live_risk_audit(config, tickets=self.tickets(amount=1000)).set_index("check")
        self.assertFalse(bool(audit.loc["risk_daily_buy_amount_limit", "passed"]))
        self.assertIn("projected=4000.00", audit.loc["risk_daily_buy_amount_limit", "value"])
        self.assertFalse(bool(audit.loc["risk_daily_gross_amount_limit", "passed"]))

    def test_command_adapter_requires_cash_plus_buffer(self):
        output = self.fresh_output("auto_live_risk_cash")
        config = self.config(output, min_cash_buffer_amount=500, min_cash_buffer_ratio=0.0)
        config["auto_live"]["broker_adapter"] = "command"
        pd.DataFrame([{"available_cash": 1200, "total_asset": 10000}]).to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        audit = build_auto_live_risk_audit(config, tickets=self.tickets(amount=1000)).set_index("check")
        self.assertFalse(bool(audit.loc["risk_available_cash_buffer", "passed"]))

        pd.DataFrame([{"available_cash": 2000, "total_asset": 10000}]).to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        audit = build_auto_live_risk_audit(config, tickets=self.tickets(amount=1000)).set_index("check")
        self.assertTrue(bool(audit.loc["risk_available_cash_buffer", "passed"]))

    def test_canary_stage_uses_signed_small_account_cash_buffer(self):
        output = self.fresh_output("auto_live_risk_canary_cash")
        config = self.config(output)
        config["auto_live"]["broker_adapter"] = "command"
        config["auto_live"]["rollout"] = {
            "stage": "canary",
            "stages": {
                "canary": {
                    "min_cash_buffer_amount": 500,
                    "min_cash_buffer_ratio": 0.10,
                }
            },
        }
        pd.DataFrame([{"available_cash": 2567.44, "total_asset": 2567.44}]).to_csv(
            output / "auto_live_broker_account_snapshot.csv", index=False
        )

        audit = build_auto_live_risk_audit(
            config, tickets=self.tickets(amount=1000)
        ).set_index("check")

        self.assertTrue(bool(audit.loc["risk_available_cash_buffer", "passed"]))
        self.assertIn("reserve=500.00", audit.loc["risk_available_cash_buffer", "value"])
        self.assertIn("source=rollout:canary", audit.loc["risk_cash_buffer_policy", "value"])

    def test_full_stage_keeps_large_cash_buffer(self):
        output = self.fresh_output("auto_live_risk_full_cash")
        config = self.config(output)
        config["auto_live"]["broker_adapter"] = "command"
        config["auto_live"]["rollout"] = {"stage": "full"}
        pd.DataFrame([{"available_cash": 9000, "total_asset": 100000}]).to_csv(
            output / "auto_live_broker_account_snapshot.csv", index=False
        )

        audit = build_auto_live_risk_audit(
            config, tickets=self.tickets(amount=1000)
        ).set_index("check")

        self.assertFalse(bool(audit.loc["risk_available_cash_buffer", "passed"]))
        self.assertIn("reserve=10000.00", audit.loc["risk_available_cash_buffer", "value"])
        self.assertIn("source=rollout:full", audit.loc["risk_cash_buffer_policy", "value"])

    def test_reservation_upsert_keeps_one_row_per_run(self):
        output = self.fresh_output("auto_live_risk_upsert")
        config = self.config(output)
        tickets = self.tickets()
        for status in ["reserved", "submitted"]:
            upsert_auto_live_risk_reservation(
                config,
                tickets,
                run_id="run-one",
                status=status,
                tickets_hash="ticket-hash",
                auto_hash="config-hash",
                dedupe_key="dedupe",
                message=status,
            )
        reservations = read_auto_live_risk_reservations(output)
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations["status"].iloc[0], "submitted")
        self.assertEqual(float(reservations["buy_amount"].iloc[0]), 1000.0)

    def write_auto_live_gates(self, output: Path):
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)

    def test_run_blocks_before_adapter_when_independent_risk_fails(self):
        output = self.fresh_output("auto_live_risk_run_block")
        config = self.config(output, max_single_order_amount=500)
        config["auto_live"].update({"enabled": True, "allow_mock_adapter": True, "allow_broker_submit": True})
        self.write_auto_live_gates(output)
        self.tickets(amount=1000).to_csv(output / "rpa_orders.csv", index=False)

        with self.assertRaisesRegex(RuntimeError, "auto_live_risk_audit_passed"):
            run_auto_live(config)
        run = pd.read_csv(output / "auto_live_run.csv")
        reservations = read_auto_live_risk_reservations(output)
        self.assertEqual(run["status"].iloc[0], "blocked")
        self.assertTrue(reservations.empty)
        self.assertFalse((output / "auto_live_submission_ledger.csv").exists())

    def test_successful_run_reserves_daily_capacity_before_submit(self):
        output = self.fresh_output("auto_live_risk_run_reserve")
        config = self.config(output)
        config["auto_live"].update({"enabled": True, "allow_mock_adapter": True, "allow_broker_submit": True})
        self.write_auto_live_gates(output)
        self.tickets(amount=1000).to_csv(output / "rpa_orders.csv", index=False)

        paths = run_auto_live(config)
        reservations = pd.read_csv(paths["auto_live_risk_reservations.csv"])
        run = pd.read_csv(paths["auto_live_run.csv"])
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations["status"].iloc[0], "submitted")
        self.assertEqual(float(reservations["buy_amount"].iloc[0]), 1000.0)
        self.assertEqual(str(reservations["run_id"].iloc[0]), str(run["run_id"].iloc[0]))

    def test_adapter_boundary_rechecks_emergency_stop(self):
        output = self.fresh_output("auto_live_risk_adapter_stop")
        config = self.config(output)
        config["auto_live"].update({"enabled": True, "allow_mock_adapter": True, "allow_broker_submit": True})
        activate_auto_live_stop(config, reason="last-second stop")

        results = submit_with_adapter(config, self.tickets(), run_id="stop-boundary")
        self.assertTrue((results["status"] == "failed").all())
        self.assertTrue(results["broker_order_id"].fillna("").eq("").all())
        self.assertTrue(results["message"].str.contains("broker adapter was not called").all())

    def test_risk_recheck_catches_stop_after_submit_lock_acquisition(self):
        output = self.fresh_output("auto_live_risk_stop_race")
        config = self.config(output)
        config["auto_live"].update({"enabled": True, "allow_mock_adapter": True, "allow_broker_submit": True})
        self.write_auto_live_gates(output)
        self.tickets().to_csv(output / "rpa_orders.csv", index=False)
        from ashare_quant.autolive import acquire_auto_live_submit_lock

        def acquire_then_stop(output_dir, run_id):
            lock_path = acquire_auto_live_submit_lock(output_dir, run_id)
            activate_auto_live_stop(config, reason="stop after lock")
            return lock_path

        with patch("ashare_quant.autolive.acquire_auto_live_submit_lock", side_effect=acquire_then_stop):
            with self.assertRaisesRegex(RuntimeError, "auto_live_risk_recheck_failed"):
                run_auto_live(config)
        blocked = pd.read_csv(output / "auto_live_blocked_attempt.csv")
        reservations = read_auto_live_risk_reservations(output)
        self.assertEqual(blocked["blocked_reason"].iloc[0], "auto_live_risk_recheck_failed")
        self.assertTrue(reservations.empty)
        self.assertFalse((output / "auto_live_submit.lock").exists())


if __name__ == "__main__":
    unittest.main()
