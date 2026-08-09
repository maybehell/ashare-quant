from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.data import DataStore
from ashare_quant.fills import execution_import_lock, import_fills, write_fill_import_template


class FillImportTests(unittest.TestCase):
    def test_template_writes_required_columns(self):
        root = Path.cwd() / "test_outputs" / "fill_template"
        path = write_fill_import_template({"paths": {"output_dir": str(root / "outputs")}})
        frame = pd.read_csv(path)
        self.assertEqual(frame.columns.tolist(), ["date", "symbol", "side", "quantity", "price", "fee", "amount", "reason"])

    def test_execution_import_lock_is_exclusive_and_reusable(self):
        root = Path.cwd() / "test_outputs" / "fill_import_lock"
        lock_path = root / "state.sqlite3.fill-import.lock"
        with execution_import_lock(lock_path):
            with self.assertRaisesRegex(RuntimeError, "another account write is active"):
                with execution_import_lock(lock_path):
                    pass
        with execution_import_lock(lock_path):
            self.assertTrue(lock_path.exists())

    def test_import_requires_confirmation(self):
        root = Path.cwd() / "test_outputs" / "fill_confirm"
        root.mkdir(parents=True, exist_ok=True)
        source = root / "fills.csv"
        pd.DataFrame([{"date": "2026-06-17", "symbol": "600000", "side": "buy", "quantity": 100, "price": 10, "fee": 5, "amount": 1000, "reason": "manual"}]).to_csv(
            source,
            index=False,
        )
        with self.assertRaises(RuntimeError):
            import_fills({"paths": {"output_dir": str(root / "outputs"), "state_db": str(root / "state.sqlite3")}}, source)

    def test_execution_artifacts_are_invalidated_before_state_commit(self):
        root = Path.cwd() / "test_outputs" / "fill_fail_safe_ordering"
        config = self.make_config(root)
        self.write_market_cache(config)
        self.write_account_state(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        stale_order = output_dir / "rpa_orders.csv"
        stale_order.write_text("stale", encoding="utf-8")
        source = root / "fills.csv"
        pd.DataFrame(
            [{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 12.0, "fee": 5.0, "amount": 1200.0, "reason": "manual"}]
        ).to_csv(source, index=False)

        with patch("ashare_quant.fills.write_fill_state", side_effect=RuntimeError("simulated state commit failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated state commit failure"):
                import_fills(config, source, confirm=True)

        self.assertFalse(stale_order.exists())
        with sqlite3.connect(config["paths"]["state_db"]) as conn:
            trades = conn.execute("select count(*) from trades").fetchone()[0]
            cash = conn.execute("select cash from account_snapshots order by date desc limit 1").fetchone()[0]
        self.assertEqual(trades, 0)
        self.assertEqual(float(cash), 10000.0)
        audit = pd.read_csv(output_dir / "fill_import_audit.csv")
        self.assertFalse((audit["check"] == "state_written").any())

    def test_valid_import_updates_state_and_records_batch_hash(self):
        root = Path.cwd() / "test_outputs" / "fill_valid"
        config = self.make_config(root)
        self.write_market_cache(config)
        self.write_account_state(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ["orders_provenance.csv", "rpa_orders.csv", "rpa_pre_trade_checks.csv"]:
            (output_dir / name).write_text("stale", encoding="utf-8")

        source = root / "fills.csv"
        pd.DataFrame(
            [
                {"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 12.0, "fee": 5.0, "amount": 1200.0, "reason": "manual"},
                {"date": "2026-06-17", "symbol": "600000", "side": "sell", "quantity": 50, "price": 10.0, "fee": 1.0, "amount": 500.0, "reason": "manual"},
            ]
        ).to_csv(source, index=False)

        paths = import_fills(config, source, confirm=True)

        audit = pd.read_csv(paths["fill_import_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertIn("account_state_snapshot.csv", paths)
        state_snapshot = pd.read_csv(paths["account_state_snapshot.csv"])
        self.assertEqual(float(state_snapshot["marked_equity"].iloc[0]), 11044.0)
        state_positions = pd.read_csv(paths["account_state_positions.csv"], dtype={"symbol": str})
        self.assertEqual(int(state_positions.loc[state_positions["symbol"] == "000001", "locked_quantity"].iloc[0]), 100)
        positions_snapshot = pd.read_csv(paths["fill_import_positions.csv"], dtype={"symbol": str})
        self.assertEqual(round(float(positions_snapshot["market_value"].sum()), 2), 1750.0)
        with sqlite3.connect(config["paths"]["state_db"]) as conn:
            snapshot = conn.execute("select date, cash, equity from account_snapshots order by date desc limit 1").fetchone()
            positions = conn.execute("select symbol, quantity, round(cost, 4), locked_quantity from positions order by symbol").fetchall()
            trades = conn.execute("select symbol, side, quantity, price, fee, amount from trades order by symbol").fetchall()
            imports = conn.execute("select count(*) from execution_imports").fetchone()[0]
            orders = conn.execute("select count(*) from orders").fetchone()[0]
        self.assertEqual(snapshot, ("2026-06-17", 9294.0, 11044.0))
        self.assertEqual(positions, [("000001", 100, 12.05, 100), ("600000", 50, 9.0, 0)])
        self.assertEqual(trades, [("000001", "buy", 100, 12.0, 5.0, 1200.0), ("600000", "sell", 50, 10.0, 1.0, 500.0)])
        self.assertEqual(imports, 1)
        self.assertEqual(orders, 0)
        self.assertFalse((output_dir / "orders_provenance.csv").exists())
        self.assertFalse((output_dir / "rpa_orders.csv").exists())
        self.assertFalse((output_dir / "rpa_pre_trade_checks.csv").exists())

    def test_duplicate_source_hash_is_rejected_without_double_applying(self):
        root = Path.cwd() / "test_outputs" / "fill_duplicate"
        config = self.make_config(root)
        self.write_market_cache(config)
        self.write_account_state(config)
        source = root / "fills.csv"
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 12.0, "fee": 5.0, "amount": 1200.0, "reason": "manual"}]).to_csv(
            source,
            index=False,
        )

        import_fills(config, source, confirm=True)
        with self.assertRaises(RuntimeError):
            import_fills(config, source, confirm=True)

        with sqlite3.connect(config["paths"]["state_db"]) as conn:
            cash = conn.execute("select cash from account_snapshots order by date desc limit 1").fetchone()[0]
            trade_count = conn.execute("select count(*) from trades").fetchone()[0]
        self.assertEqual(cash, 8795.0)
        self.assertEqual(trade_count, 1)

    def test_duplicate_auto_live_cumulative_event_is_rejected_atomically(self):
        root = Path.cwd() / "test_outputs" / "fill_duplicate_auto_live_event"
        config = self.make_config(root)
        self.write_market_cache(config)
        self.write_account_state(config)
        first = root / "fills_first.csv"
        second = root / "fills_second.csv"
        event = "auto_live:r1:b1:cum100"
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 12.0, "fee": 5.0, "amount": 1200.0, "reason": event}]).to_csv(first, index=False)
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 12.1, "fee": 5.0, "amount": 1210.0, "reason": event}]).to_csv(second, index=False)

        import_fills(config, first, confirm=True)
        with self.assertRaisesRegex(RuntimeError, "fill events already imported"):
            import_fills(config, second, confirm=True)

        with sqlite3.connect(config["paths"]["state_db"]) as conn:
            cash = conn.execute("select cash from account_snapshots order by date desc limit 1").fetchone()[0]
            trade_count = conn.execute("select count(*) from trades").fetchone()[0]
            event_count = conn.execute("select count(*) from execution_fill_keys").fetchone()[0]
        self.assertEqual(cash, 8795.0)
        self.assertEqual(trade_count, 1)
        self.assertEqual(event_count, 1)

    def test_sell_over_sellable_is_rejected_before_state_write(self):
        root = Path.cwd() / "test_outputs" / "fill_sellable"
        config = self.make_config(root)
        self.write_market_cache(config)
        self.write_account_state(config, locked_quantity=80)
        source = root / "fills.csv"
        pd.DataFrame([{"date": "2026-06-16", "symbol": "600000", "side": "sell", "quantity": 50, "price": 10.0, "fee": 1.0, "amount": 500.0, "reason": "manual"}]).to_csv(
            source,
            index=False,
        )

        with self.assertRaises(ValueError):
            import_fills(config, source, confirm=True)

        audit = pd.read_csv(root / "outputs" / "fill_import_audit.csv")
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("sellable_quantity_sufficient", failed)
        with sqlite3.connect(config["paths"]["state_db"]) as conn:
            row = conn.execute("select cash from account_snapshots order by date desc limit 1").fetchone()
            trade_count = conn.execute("select count(*) from trades").fetchone()[0]
        self.assertEqual(row[0], 10000.0)
        self.assertEqual(trade_count, 0)

    def test_required_order_match_rejects_unverified_fills(self):
        root = Path.cwd() / "test_outputs" / "fill_order_match_reject"
        config = self.make_config(root)
        config["execution"] = {"require_fill_order_match": True}
        self.write_market_cache(config)
        self.write_account_state(config)
        source = root / "fills.csv"
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 12.0, "fee": 5.0, "amount": 1200.0, "reason": "manual"}]).to_csv(
            source,
            index=False,
        )

        with self.assertRaises(ValueError):
            import_fills(config, source, confirm=True)

        audit = pd.read_csv(root / "outputs" / "fill_import_audit.csv")
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("fill_order_source_is_paper", failed)
        self.assertIn("fill_orders_matched", failed)
        match = pd.read_csv(root / "outputs" / "fill_order_match.csv", dtype={"symbol": str})
        self.assertEqual(match["message"].iloc[0], "missing_orders_csv")
        with sqlite3.connect(config["paths"]["state_db"]) as conn:
            cash = conn.execute("select cash from account_snapshots order by date desc limit 1").fetchone()[0]
            trade_count = conn.execute("select count(*) from trades").fetchone()[0]
        self.assertEqual(cash, 10000.0)
        self.assertEqual(trade_count, 0)

    def test_required_order_match_allows_partial_verified_fills(self):
        root = Path.cwd() / "test_outputs" / "fill_order_match_pass"
        config = self.make_config(root)
        config["execution"] = {"require_fill_order_match": True}
        self.write_market_cache(config)
        self.write_account_state(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 200, "reason": "rebalance"}]).to_csv(
            output_dir / "orders.csv",
            index=False,
        )
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "execution_date", "value": "2026-06-17"},
                {"item": "order_count", "value": "1"},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)
        source = root / "fills.csv"
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 12.0, "fee": 5.0, "amount": 1200.0, "reason": "manual"}]).to_csv(
            source,
            index=False,
        )

        paths = import_fills(config, source, confirm=True)

        audit = pd.read_csv(paths["fill_import_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        match = pd.read_csv(paths["fill_order_match.csv"], dtype={"symbol": str})
        self.assertEqual(match["message"].iloc[0], "matched")
        self.assertEqual(int(match["order_quantity"].iloc[0]), 200)

    def make_config(self, root: Path) -> dict:
        return {
            "paths": {
                "cache_dir": str(root / "cache"),
                "output_dir": str(root / "outputs"),
                "state_db": str(root / "state.sqlite3"),
            },
            "data": {"min_daily_price_coverage_ratio": 0.9, "require_benchmark_on_signal_date": False},
            "reconciliation": {"cash_tolerance": 1.0},
        }

    def write_market_cache(self, config: dict) -> None:
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame(
                [
                    {"symbol": "600000", "name": "A", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False},
                    {"symbol": "000001", "name": "B", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False},
                ]
            ),
        )
        store.write_frame(
            "prices",
            pd.DataFrame(
                [
                    {"date": "2026-06-16", "symbol": "600000", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1000, "amount": 10000},
                    {"date": "2026-06-16", "symbol": "000001", "open": 12.5, "high": 12.8, "low": 12.3, "close": 12.5, "volume": 1000, "amount": 12500},
                    {"date": "2026-06-17", "symbol": "600000", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1000, "amount": 10000},
                    {"date": "2026-06-17", "symbol": "000001", "open": 12.5, "high": 12.8, "low": 12.3, "close": 12.5, "volume": 1000, "amount": 12500},
                ]
            ),
        )

    def write_account_state(self, config: dict, locked_quantity: int = 0) -> None:
        state_db = Path(config["paths"]["state_db"])
        if state_db.exists():
            state_db.unlink()
        store = DataStore.from_config(config)
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert into account_snapshots(date, cash, equity) values ('2026-06-16', 10000, 11000)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('600000', 100, 9.0, ?)", (locked_quantity,))
            conn.execute("insert into orders(date, symbol, side, quantity, reason) values ('2026-06-17', '000001', 'buy', 100, 'stale')")


if __name__ == "__main__":
    unittest.main()
