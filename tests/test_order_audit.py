
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.order_audit import build_order_liquidity_audit, order_liquidity_audit_passed


class OrderAuditTests(unittest.TestCase):
    def prices(self):
        return pd.DataFrame(
            [
                {"date": f"2026-01-{day:02d}", "symbol": "600000", "volume": 1000000, "amount": 10000000}
                for day in range(1, 11)
            ]
        )

    def ticket(self, quantity=1000, amount=10000.0, status="ready"):
        return pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-11",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": quantity,
                    "limit_price": 10.0,
                    "estimated_amount": amount,
                    "status": status,
                    "message": "manual_confirm_required",
                }
            ]
        )

    def test_order_liquidity_audit_passes_small_order(self):
        config = {"execution": {"order_liquidity_lookback_days": 5, "max_order_avg_amount_participation": 0.05}, "trading": {"max_volume_participation": 0.10}}
        audit = build_order_liquidity_audit(config, self.ticket(), self.prices())
        self.assertTrue(bool(audit["passed"].iloc[0]))
        self.assertEqual(audit["message"].iloc[0], "liquidity_ok")
        self.assertEqual(order_liquidity_audit_passed(audit)[0], True)

    def test_order_liquidity_audit_blocks_large_amount_participation(self):
        config = {"execution": {"order_liquidity_lookback_days": 5, "max_order_avg_amount_participation": 0.01}, "trading": {"max_volume_participation": 0.10}}
        audit = build_order_liquidity_audit(config, self.ticket(quantity=2000, amount=300000.0), self.prices())
        self.assertFalse(bool(audit["passed"].iloc[0]))
        self.assertEqual(audit["message"].iloc[0], "exceeds_average_amount_participation")
        self.assertEqual(order_liquidity_audit_passed(audit)[0], False)

    def test_order_liquidity_audit_blocks_missing_history(self):
        config = {"execution": {"order_liquidity_lookback_days": 5, "max_order_avg_amount_participation": 0.05}, "trading": {"max_volume_participation": 0.10}}
        tickets = self.ticket()
        tickets.loc[0, "symbol"] = "000001"
        audit = build_order_liquidity_audit(config, tickets, self.prices())
        self.assertFalse(bool(audit["passed"].iloc[0]))
        self.assertEqual(audit["message"].iloc[0], "missing_average_volume")


if __name__ == "__main__":
    unittest.main()
