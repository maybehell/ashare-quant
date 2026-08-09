import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.audit import mapping_sha256, orders_frame_sha256
from ashare_quant.order_integrity import (
    attach_order_provenance_integrity,
    verify_order_provenance_integrity,
    verify_rpa_ticket_integrity_manifest,
    verify_rpa_ticket_execution_subset,
    write_rpa_ticket_integrity_manifest,
)
from ashare_quant.rpa import validate_paper_order_source
from ashare_quant.strategy_activation import ensure_execution_epoch


class OrderIntegrityTests(unittest.TestCase):
    def config(self, required: bool = True) -> dict:
        return {
            "rpa": {
                "require_order_provenance_hmac": required,
                "order_provenance_hmac_key_env": "TEST_ORDER_HMAC_KEY",
            }
        }

    def test_required_hmac_round_trip_and_tamper_detection(self):
        with patch.dict(os.environ, {"TEST_ORDER_HMAC_KEY": "k" * 40}, clear=False):
            signed = attach_order_provenance_integrity(
                self.config(),
                {"source": "paper", "orders_sha256": "abc", "order_count": 2},
            )
            self.assertEqual(verify_order_provenance_integrity(self.config(), signed), (True, "order_integrity_hmac_valid"))
            signed["order_count"] = 3
            self.assertEqual(
                verify_order_provenance_integrity(self.config(), signed)[1],
                "order_integrity_payload_hash_mismatch",
            )

    def test_required_hmac_fails_without_strong_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HMAC key missing"):
                attach_order_provenance_integrity(self.config(), {"source": "paper"})
            self.assertEqual(
                verify_order_provenance_integrity(self.config(), {"source": "paper"}),
                (False, "order_integrity_missing"),
            )

    def test_optional_mode_still_hashes_provenance(self):
        with patch.dict(os.environ, {}, clear=True):
            signed = attach_order_provenance_integrity(self.config(required=False), {"source": "paper"})
            self.assertEqual(
                verify_order_provenance_integrity(self.config(required=False), signed),
                (True, "order_integrity_hash_valid"),
            )

    def test_signed_rpa_ticket_manifest_detects_ticket_tampering(self):
        root = Path.cwd() / "test_outputs" / "signed_rpa_ticket_manifest"
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        config = self.config(required=True)
        config.update({"paths": {"output_dir": str(output)}, "strategy": {"name": "signed_strategy"}})
        provenance = {
            "source": "paper",
            **ensure_execution_epoch(config),
            "orders_sha256": "orders-hash",
            "config_sha256": mapping_sha256(config),
        }
        tickets = pd.DataFrame([{
            "ticket_id": "ticket-1", "date": "2026-07-16", "symbol": "600000",
            "side": "buy", "quantity": 100, "limit_price": 10.0,
            "estimated_amount": 1000.0, "status": "ready", "message": "",
        }])
        with patch.dict(os.environ, {"TEST_ORDER_HMAC_KEY": "k" * 40}, clear=False):
            signed = attach_order_provenance_integrity(config, provenance)
            pd.DataFrame([{"item": key, "value": value} for key, value in signed.items()]).to_csv(
                output / "orders_provenance.csv", index=False
            )
            write_rpa_ticket_integrity_manifest(config, tickets, signed)
            self.assertEqual(
                verify_rpa_ticket_integrity_manifest(config, tickets),
                (True, "rpa_ticket_integrity_hmac_valid"),
            )
            tampered = tickets.copy()
            tampered.loc[0, "quantity"] = 200
            self.assertEqual(
                verify_rpa_ticket_integrity_manifest(config, tampered)[1],
                "rpa_ticket_integrity_tickets_hash_mismatch",
            )

    def test_signed_manifest_rejects_injected_execution_subset(self):
        root = Path.cwd() / "test_outputs" / "signed_rpa_execution_subset"
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        config = self.config(required=True)
        config.update({"paths": {"output_dir": str(output)}})
        tickets = pd.DataFrame([
            {
                "ticket_id": "ticket-1", "date": "2026-07-16", "symbol": "600000",
                "side": "buy", "quantity": 100, "limit_price": 10.0,
                "estimated_amount": 1000.0, "status": "ready", "message": "",
            },
            {
                "ticket_id": "ticket-2", "date": "2026-07-16", "symbol": "000001",
                "side": "sell", "quantity": 200, "limit_price": 12.0,
                "estimated_amount": 2400.0, "status": "ready", "message": "",
            },
        ])
        provenance = {
            "source": "paper",
            **ensure_execution_epoch(config),
            "orders_sha256": "orders-hash",
        }
        with patch.dict(os.environ, {"TEST_ORDER_HMAC_KEY": "k" * 40}, clear=False):
            signed = attach_order_provenance_integrity(config, provenance)
            pd.DataFrame([{"item": key, "value": value} for key, value in signed.items()]).to_csv(
                output / "orders_provenance.csv", index=False
            )
            tickets.to_csv(output / "rpa_orders.csv", index=False)
            write_rpa_ticket_integrity_manifest(config, tickets, signed)

            self.assertEqual(
                verify_rpa_ticket_execution_subset(config, tickets.iloc[[0]], source_tickets=tickets),
                (True, "rpa_ticket_execution_subset_hmac_valid"),
            )
            injected = tickets.iloc[[0]].copy()
            injected.loc[injected.index[0], "quantity"] = 300
            self.assertEqual(
                verify_rpa_ticket_execution_subset(config, injected, source_tickets=tickets),
                (False, "rpa_ticket_execution_subset_mismatch"),
            )

    def test_rpa_validation_requires_signed_untampered_provenance(self):
        root = Path.cwd() / "test_outputs" / "signed_order_provenance"
        output = root / "outputs"
        output.mkdir(parents=True, exist_ok=True)
        config = self.config(required=True)
        config.update({"paths": {"output_dir": str(output)}, "strategy": {"name": "signed_strategy"}})
        orders = pd.DataFrame([
            {"date": "2026-07-16", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}
        ])
        orders_path = output / "orders.csv"
        orders.to_csv(orders_path, index=False)
        provenance = {
            "source": "paper",
            **ensure_execution_epoch(config),
            "allow_weak_strategy": False,
            "config_sha256": mapping_sha256(config),
            "strategy_name": "signed_strategy",
            "execution_date": "2026-07-16",
            "order_count": 1,
            "orders_sha256": orders_frame_sha256(orders),
        }
        with patch.dict(os.environ, {"TEST_ORDER_HMAC_KEY": "k" * 40}, clear=False):
            signed = attach_order_provenance_integrity(config, provenance)
            pd.DataFrame([{"item": key, "value": value} for key, value in signed.items()]).to_csv(
                output / "orders_provenance.csv", index=False
            )
            validated = validate_paper_order_source(config, orders_path)
            self.assertEqual(validated["order_integrity_schema"], "ashare_quant.order_provenance.v1")

            signed["order_count"] = 2
            pd.DataFrame([{"item": key, "value": value} for key, value in signed.items()]).to_csv(
                output / "orders_provenance.csv", index=False
            )
            with self.assertRaisesRegex(RuntimeError, "order_integrity_payload_hash_mismatch"):
                validate_paper_order_source(config, orders_path)


if __name__ == "__main__":
    unittest.main()
