import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import tickets_sha256
from ashare_quant.autolive_batch import build_auto_live_batch_plan, write_auto_live_batch_plan
from ashare_quant.order_integrity import attach_order_provenance_integrity, write_rpa_ticket_integrity_manifest
from ashare_quant.strategy_activation import ensure_execution_epoch


class AutoLiveBatchTests(unittest.TestCase):
    def fresh_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def config(self, output: Path, max_orders: int = 50) -> dict:
        return {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "risk_limits": {
                    "enabled": True,
                    "max_orders_per_batch": max_orders,
                    "max_single_order_amount": 10000,
                    "max_batch_buy_amount": 1000000,
                    "max_batch_gross_amount": 1000000,
                    "max_daily_buy_amount": 2000000,
                    "max_daily_gross_amount": 2000000,
                }
            },
        }

    def tickets(self, count: int, sells: int = 0) -> pd.DataFrame:
        rows = []
        for index in range(count):
            rows.append(
                {
                    "ticket_id": f"ticket-{index:03d}",
                    "date": "2026-07-11",
                    "symbol": f"{600000 + index:06d}",
                    "side": "sell" if index < sells else "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                }
            )
        return pd.DataFrame(rows)

    def test_splits_all_orders_without_dropping_and_selects_only_first_batch(self):
        output = self.fresh_output("auto_live_batch_split")
        source = self.tickets(72, sells=12)
        source.to_csv(output / "rpa_orders.csv", index=False)

        paths = write_auto_live_batch_plan(self.config(output))

        plan = pd.read_csv(paths["auto_live_batch_plan.csv"])
        orders = pd.read_csv(paths["auto_live_batch_orders.csv"], dtype={"symbol": str})
        current = pd.read_csv(paths["auto_live_current_batch.csv"], dtype={"symbol": str})
        summary = pd.read_csv(paths["auto_live_batch_summary.csv"])
        self.assertEqual(plan["order_count"].tolist(), [50, 22])
        self.assertEqual(len(orders), 72)
        self.assertEqual(set(orders["ticket_id"]), set(source["ticket_id"]))
        self.assertEqual(len(current), 50)
        self.assertEqual(current.iloc[:12]["side"].tolist(), ["sell"] * 12)
        self.assertEqual(summary.loc[0, "status"], "ready")
        self.assertEqual(int(summary.loc[0, "queued_order_count"]), 22)

    def test_tampered_signed_ticket_file_clears_executable_batch(self):
        output = self.fresh_output("auto_live_batch_signed_tamper")
        config = self.config(output, max_orders=2)
        config["rpa"] = {
            "require_order_provenance_hmac": True,
            "order_provenance_hmac_key_env": "TEST_BATCH_HMAC_KEY",
        }
        source = self.tickets(2)
        source.to_csv(output / "rpa_orders.csv", index=False)
        provenance = {
            "source": "paper",
            **ensure_execution_epoch(config),
            "orders_sha256": "orders-hash",
        }
        with patch.dict(os.environ, {"TEST_BATCH_HMAC_KEY": "b" * 40}, clear=False):
            signed = attach_order_provenance_integrity(config, provenance)
            pd.DataFrame([{"item": key, "value": value} for key, value in signed.items()]).to_csv(
                output / "orders_provenance.csv", index=False
            )
            write_rpa_ticket_integrity_manifest(config, source, signed)
            ready_paths = write_auto_live_batch_plan(config)
            ready = pd.read_csv(ready_paths["auto_live_batch_summary.csv"])
            self.assertEqual(ready.loc[0, "status"], "ready")

            source.loc[0, "quantity"] = 200
            source.to_csv(output / "rpa_orders.csv", index=False)
            blocked_paths = write_auto_live_batch_plan(config)
            blocked = pd.read_csv(blocked_paths["auto_live_batch_summary.csv"])
            current = pd.read_csv(blocked_paths["auto_live_current_batch.csv"])
            self.assertEqual(blocked.loc[0, "status"], "blocked_ticket_integrity")
            self.assertEqual(blocked.loc[0, "message"], "rpa_ticket_integrity_tickets_hash_mismatch")
            self.assertTrue(current.empty)

    def test_active_first_batch_blocks_next_batch_until_terminal(self):
        output = self.fresh_output("auto_live_batch_active")
        config = self.config(output, max_orders=2)
        source = self.tickets(3)
        _, _, first, _ = build_auto_live_batch_plan(config, source)
        first_hash = tickets_sha256(first)
        pd.DataFrame([{"run_id": "run-1", "tickets_sha256": first_hash, "status": "submitted"}]).to_csv(
            output / "auto_live_submission_ledger.csv", index=False
        )
        pd.DataFrame(
            [{"source_run_id": "run-1", "ticket_id": ticket_id, "broker_status": "accepted"} for ticket_id in first["ticket_id"]]
        ).to_csv(output / "auto_live_order_status.csv", index=False)

        plan, orders, current, summary = build_auto_live_batch_plan(config, source)

        self.assertTrue(current.empty)
        self.assertEqual(plan["status"].tolist(), ["active", "queued"])
        self.assertEqual(summary.loc[0, "status"], "waiting_active_batch")
        self.assertEqual(len(orders), 3)

    def test_terminal_first_batch_advances_to_second_batch(self):
        output = self.fresh_output("auto_live_batch_terminal")
        config = self.config(output, max_orders=2)
        source = self.tickets(3)
        _, _, first, _ = build_auto_live_batch_plan(config, source)
        first_hash = tickets_sha256(first)
        pd.DataFrame([{"run_id": "run-1", "tickets_sha256": first_hash, "status": "submitted"}]).to_csv(
            output / "auto_live_submission_ledger.csv", index=False
        )
        pd.DataFrame(
            [{"source_run_id": "run-1", "ticket_id": ticket_id, "broker_status": "filled"} for ticket_id in first["ticket_id"]]
        ).to_csv(output / "auto_live_order_status.csv", index=False)

        plan, _, current, summary = build_auto_live_batch_plan(config, source)

        self.assertEqual(plan["status"].tolist(), ["completed", "selected"])
        self.assertEqual(current["ticket_id"].tolist(), ["ticket-002"])
        self.assertEqual(int(summary.loc[0, "selected_batch_no"]), 2)

    def test_invalid_oversized_order_is_audited_and_blocks_whole_plan(self):
        output = self.fresh_output("auto_live_batch_invalid")
        config = self.config(output, max_orders=2)
        source = self.tickets(3)
        source.loc[2, ["quantity", "estimated_amount"]] = [2000, 20000.0]

        _, orders, current, summary = build_auto_live_batch_plan(config, source)

        self.assertTrue(current.empty)
        self.assertEqual(len(orders), 3)
        blocked = orders[orders["planning_status"] == "blocked"]
        self.assertEqual(blocked.iloc[0]["planning_reason"], "single_order_amount_exceeds_limit")
        self.assertEqual(summary.loc[0, "status"], "blocked_invalid_orders")


if __name__ == "__main__":
    unittest.main()
