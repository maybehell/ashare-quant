from pathlib import Path
import sqlite3
import unittest

import pandas as pd

from ashare_quant.reconciliation import build_account_reconciliation, reconciliation_passed


class ReconciliationTests(unittest.TestCase):
    def test_reconciliation_passes_balanced_snapshot(self):
        frame = build_account_reconciliation(
            {"trading": {"lot_size": 100}},
            pd.DataFrame([{"date": "2026-06-16", "cash": 9000.0, "market_value": 1000.0, "equity": 10000.0}]),
            pd.DataFrame([{"date": "2026-06-17", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}]),
            pd.DataFrame([{"symbol": "600000", "quantity": 100, "sellable_quantity": 100, "cost": 9.5, "close": 10.0, "market_value": 1000.0, "pnl": 50.0}]),
            pd.DataFrame(),
            {"source": "paper", "execution_date": "2026-06-17", "order_count": 1},
        )
        self.assertTrue(reconciliation_passed(frame))

    def test_reconciliation_fails_unbalanced_equity(self):
        frame = build_account_reconciliation(
            {"trading": {"lot_size": 100}},
            pd.DataFrame([{"date": "2026-06-16", "cash": 9000.0, "market_value": 1000.0, "equity": 10001.5}]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            {"execution_date": "2026-06-17", "order_count": 0},
        )
        failed = set(frame.loc[~frame["passed"], "check"])
        self.assertIn("equity_cash_plus_market_value", failed)

    def test_reconciliation_checks_state_database(self):
        root = Path.cwd() / "test_outputs" / "reconciliation_state"
        root.mkdir(parents=True, exist_ok=True)
        state_db = root / "state.sqlite3"
        if state_db.exists():
            state_db.unlink()
        with sqlite3.connect(state_db) as conn:
            conn.executescript(
                """
                create table account_snapshots(date text primary key, cash real not null, equity real not null);
                create table positions(symbol text primary key, quantity integer not null, cost real not null, locked_quantity integer not null);
                """
            )
            conn.execute("insert into account_snapshots(date, cash, equity) values ('2026-06-16', 9000, 10000)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('600000', 100, 9.5, 0)")
        frame = build_account_reconciliation(
            {"paths": {"state_db": str(state_db)}, "trading": {"lot_size": 100}},
            pd.DataFrame([{"date": "2026-06-16", "cash": 9000.0, "market_value": 1000.0, "equity": 10000.0}]),
            pd.DataFrame(),
            pd.DataFrame([{"symbol": "600000", "quantity": 100, "sellable_quantity": 100, "cost": 9.5, "close": 10.0, "market_value": 1000.0, "pnl": 50.0}]),
            pd.DataFrame(),
            {"execution_date": "2026-06-17", "order_count": 0},
        )
        self.assertTrue(reconciliation_passed(frame))


if __name__ == "__main__":
    unittest.main()
