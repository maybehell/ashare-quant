from pathlib import Path
import shutil
import sqlite3
import unittest

import pandas as pd

from ashare_quant.account import (
    check_account_import_file,
    check_account_price_overrides,
    export_account_state,
    import_account_state,
    reset_account_state,
    write_account_import_template,
    write_account_price_override_template,
    write_account_repair_template,
)
from ashare_quant.account_plan import build_account_path_decision, build_account_readiness_plan, write_account_readiness_plan
from ashare_quant.data import DataStore


class AccountImportTests(unittest.TestCase):
    def test_template_writes_required_columns(self):
        root = Path.cwd() / "test_outputs" / "account_template"
        config = {"paths": {"output_dir": str(root / "outputs")}}
        path = write_account_import_template(config)
        frame = pd.read_csv(path)
        self.assertEqual(frame.columns.tolist(), ["cash", "symbol", "quantity", "sellable_quantity", "cost"])

    def test_import_requires_explicit_confirmation(self):
        root = Path.cwd() / "test_outputs" / "account_import_confirm"
        root.mkdir(parents=True, exist_ok=True)
        source = root / "account.csv"
        pd.DataFrame([{"cash": 1000.0, "symbol": "", "quantity": "", "sellable_quantity": "", "cost": ""}]).to_csv(source, index=False)
        config = {"paths": {"output_dir": str(root / "outputs"), "state_db": str(root / "state.sqlite3")}}

        with self.assertRaises(RuntimeError):
            import_account_state(config, source)

    def test_reset_requires_explicit_confirmation(self):
        root = Path.cwd() / "test_outputs" / "account_reset_confirm"
        config = self.make_config(root)

        with self.assertRaises(RuntimeError):
            reset_account_state(config)

    def test_reset_clears_local_paper_state(self):
        root = Path.cwd() / "test_outputs" / "account_reset"
        config = self.make_config(root)
        config["backtest"] = {"initial_cash": 123456.0}
        self.write_market_cache(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "rpa_orders.csv",
            "account_repair_excluded_positions.csv",
            "account_import_check_audit.csv",
            "account_exclusion_impact.csv",
            "account_path_decision.csv",
        ]:
            (output_dir / name).write_text("stale", encoding="utf-8")
        store = DataStore.from_config(config)
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert into account_snapshots(date, cash, equity) values ('2026-06-15', 9000, 10000)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('600000', 100, 9.5, 40)")
            conn.execute("insert into orders(date, symbol, side, quantity, reason) values ('2026-06-17', '600000', 'buy', 100, 'stale')")
            conn.execute("insert into trades(date, symbol, side, quantity, price, fee, amount, reason) values ('2026-06-16', '600000', 'buy', 100, 10, 5, 1005, 'old')")

        paths = reset_account_state(config, confirm=True)

        audit = pd.read_csv(paths["account_state_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        snapshot = pd.read_csv(paths["account_state_snapshot.csv"])
        self.assertEqual(float(snapshot["cash"].iloc[0]), 123456.0)
        self.assertEqual(int(snapshot["position_count"].iloc[0]), 0)
        missing = pd.read_csv(paths["account_state_missing_prices.csv"])
        self.assertTrue(missing.empty)
        with sqlite3.connect(store.state_db) as conn:
            self.assertEqual(conn.execute("select count(*) from positions").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from orders").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from trades").fetchone()[0], 0)
        for name in [
            "rpa_orders.csv",
            "account_repair_excluded_positions.csv",
            "account_import_check_audit.csv",
            "account_exclusion_impact.csv",
            "account_path_decision.csv",
        ]:
            self.assertFalse((output_dir / name).exists())
        self.assertTrue((output_dir / "account_reset_audit.csv").exists())

    def test_valid_import_writes_state_and_clears_old_execution_artifacts(self):
        root = Path.cwd() / "test_outputs" / "account_import_valid"
        config = self.make_config(root)
        self.write_market_cache(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "orders_provenance.csv", "account_reconciliation.csv", "rpa_orders.csv",
            "rpa_pre_trade_checks.csv", "auto_live_post_trade_reconciliation.csv",
            "auto_live_post_trade_manifest.csv",
        ]:
            (output_dir / name).write_text("stale", encoding="utf-8")

        store = DataStore.from_config(config)
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert into orders(date, symbol, side, quantity, reason) values ('2026-06-17', '600000', 'buy', 100, 'stale')")

        source = root / "account.csv"
        pd.DataFrame(
            [
                {"cash": 9000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 40, "cost": 9.5},
                {"cash": "", "symbol": "000001", "quantity": 200, "sellable_quantity": 200, "cost": 12.0},
            ]
        ).to_csv(source, index=False)

        check_account_import_file(config, source, asof_date="2026-06-16")
        paths = import_account_state(config, source, asof_date="2026-06-16", confirm=True)

        audit = pd.read_csv(paths["account_import_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        self.assertIn("account_state_snapshot.csv", paths)
        self.assertIn("account_state_missing_prices.csv", paths)
        state_snapshot = pd.read_csv(paths["account_state_snapshot.csv"])
        self.assertEqual(float(state_snapshot["marked_equity"].iloc[0]), 12500.0)
        state_positions = pd.read_csv(paths["account_state_positions.csv"], dtype={"symbol": str})
        self.assertEqual(state_positions["locked_quantity"].sum(), 60)
        positions_snapshot = pd.read_csv(paths["account_import_positions.csv"], dtype={"symbol": str})
        self.assertEqual(positions_snapshot["market_value"].sum(), 3500.0)
        with sqlite3.connect(store.state_db) as conn:
            snapshot = conn.execute("select date, cash, equity from account_snapshots order by date desc limit 1").fetchone()
            positions = conn.execute("select symbol, quantity, cost, locked_quantity from positions order by symbol").fetchall()
            order_count = conn.execute("select count(*) from orders").fetchone()[0]
        self.assertEqual(snapshot, ("2026-06-16", 9000.0, 12500.0))
        self.assertEqual(positions, [("000001", 200, 12.0, 0), ("600000", 100, 9.5, 60)])
        self.assertEqual(order_count, 0)
        self.assertFalse((output_dir / "orders_provenance.csv").exists())
        self.assertFalse((output_dir / "account_reconciliation.csv").exists())
        self.assertFalse((output_dir / "rpa_orders.csv").exists())
        self.assertFalse((output_dir / "rpa_pre_trade_checks.csv").exists())
        self.assertFalse((output_dir / "auto_live_post_trade_reconciliation.csv").exists())
        self.assertFalse((output_dir / "auto_live_post_trade_manifest.csv").exists())

    def test_import_confirm_requires_matching_precheck_when_config_enabled(self):
        root = Path.cwd() / "test_outputs" / "account_import_precheck_required"
        config = self.make_config(root)
        self.write_market_cache(config)
        output_dir = root / "outputs"
        for name in ["account_import_check_audit.csv", "account_import_check_snapshot.csv", "account_import_check_positions.csv"]:
            path = output_dir / name
            if path.exists():
                path.unlink()
        source = root / "account.csv"
        pd.DataFrame([{"cash": 9000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 40, "cost": 9.5}]).to_csv(source, index=False)

        with self.assertRaises(RuntimeError):
            import_account_state(config, source, asof_date="2026-06-16", confirm=True)

        check_account_import_file(config, source, asof_date="2026-06-16")
        paths = import_account_state(config, source, asof_date="2026-06-16", confirm=True)
        audit = pd.read_csv(paths["account_import_audit.csv"])
        self.assertTrue(bool(audit.loc[audit["check"] == "import_precheck_matched", "passed"].iloc[0]))

    def test_import_confirm_blocks_if_checked_file_changed(self):
        root = Path.cwd() / "test_outputs" / "account_import_precheck_changed"
        config = self.make_config(root)
        self.write_market_cache(config)
        output_dir = root / "outputs"
        for name in ["account_import_check_audit.csv", "account_import_check_snapshot.csv", "account_import_check_positions.csv"]:
            path = output_dir / name
            if path.exists():
                path.unlink()
        source = root / "account.csv"
        pd.DataFrame([{"cash": 9000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 40, "cost": 9.5}]).to_csv(source, index=False)

        check_account_import_file(config, source, asof_date="2026-06-16")
        pd.DataFrame([{"cash": 9000.0, "symbol": "600000", "quantity": 200, "sellable_quantity": 200, "cost": 9.5}]).to_csv(source, index=False)

        with self.assertRaises(RuntimeError):
            import_account_state(config, source, asof_date="2026-06-16", confirm=True)
        audit = pd.read_csv(root / "outputs" / "account_import_audit.csv")
        self.assertFalse(bool(audit.loc[audit["check"] == "import_precheck_matched", "passed"].iloc[0]))

    def test_priced_only_import_requires_exclusion_ack(self):
        root = Path.cwd() / "test_outputs" / "account_import_priced_only_ack"
        if root.exists():
            shutil.rmtree(root)
        config = self.make_config(root)
        self.write_market_cache(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        source = output_dir / "account_import_priced_only_template.csv"
        pd.DataFrame([{"cash": 9000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 40, "cost": 9.5}]).to_csv(source, index=False)
        pd.DataFrame([{"symbol": "000002", "quantity": 200, "price_status": "missing_price"}]).to_csv(
            output_dir / "account_repair_excluded_positions.csv",
            index=False,
        )

        check_account_import_file(config, source, asof_date="2026-06-16")
        with self.assertRaisesRegex(RuntimeError, "--ack-exclusions"):
            import_account_state(config, source, asof_date="2026-06-16", confirm=True)
        failed_audit = pd.read_csv(output_dir / "account_import_audit.csv")
        self.assertFalse(bool(failed_audit.loc[failed_audit["check"] == "priced_only_exclusions_acknowledged", "passed"].iloc[0]))

        paths = import_account_state(config, source, asof_date="2026-06-16", confirm=True, ack_exclusions=True)
        audit = pd.read_csv(paths["account_import_audit.csv"])
        self.assertTrue(bool(audit.loc[audit["check"] == "priced_only_exclusions_acknowledged", "passed"].iloc[0]))

    def test_account_import_check_validates_without_writing_state(self):
        root = Path.cwd() / "test_outputs" / "account_import_check_valid"
        config = self.make_config(root)
        self.write_market_cache(config)
        state_db = Path(config["paths"]["state_db"])
        if state_db.exists():
            state_db.unlink()
        source = root / "account.csv"
        pd.DataFrame(
            [
                {"cash": 9000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 40, "cost": 9.5},
                {"cash": "", "symbol": "000001", "quantity": 200, "sellable_quantity": 200, "cost": 12.0},
            ]
        ).to_csv(source, index=False)

        paths = check_account_import_file(config, source, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_import_check_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        snapshot = pd.read_csv(paths["account_import_check_snapshot.csv"])
        self.assertEqual(float(snapshot["equity"].iloc[0]), 12500.0)
        positions = pd.read_csv(paths["account_import_check_positions.csv"], dtype={"symbol": str})
        self.assertEqual(int(positions["quantity"].sum()), 300)
        store = DataStore.from_config(config)
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            self.assertEqual(conn.execute("select count(*) from account_snapshots").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from positions").fetchone()[0], 0)

    def test_account_import_check_blocks_missing_prices_without_raising(self):
        root = Path.cwd() / "test_outputs" / "account_import_check_missing_price"
        config = self.make_config(root)
        self.write_market_cache(config)
        state_db = Path(config["paths"]["state_db"])
        if state_db.exists():
            state_db.unlink()
        source = root / "account.csv"
        pd.DataFrame([{"cash": 1000.0, "symbol": "000002", "quantity": 100, "sellable_quantity": 100, "cost": 9.5}]).to_csv(source, index=False)

        paths = check_account_import_file(config, source, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_import_check_audit.csv"])
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("symbols_priced", failed)
        self.assertIn("would_write_state", failed)

    def test_account_import_check_uses_manual_price_overrides_for_missing_prices(self):
        root = Path.cwd() / "test_outputs" / "account_import_check_manual_price"
        if root.exists():
            shutil.rmtree(root)
        config = self.make_config(root)
        self.write_market_cache(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [{"price_context": "import", "symbol": "000002", "date": "2026-06-16", "close": 8.8, "source": "broker_statement", "note": "manual"}]
        ).to_csv(output_dir / "account_price_overrides.csv", index=False)
        source = root / "account.csv"
        pd.DataFrame([{"cash": 1000.0, "symbol": "000002", "quantity": 100, "sellable_quantity": 100, "cost": 9.5}]).to_csv(source, index=False)

        paths = check_account_import_file(config, source, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_import_check_audit.csv"])
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("symbols_priced", failed)

        check_account_price_overrides(config, asof_date="2026-06-16")
        paths = check_account_import_file(config, source, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_import_check_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        snapshot = pd.read_csv(paths["account_import_check_snapshot.csv"])
        self.assertEqual(float(snapshot["equity"].iloc[0]), 1880.0)
        positions = pd.read_csv(paths["account_import_check_positions.csv"], dtype={"symbol": str})
        self.assertEqual(float(positions.loc[positions["symbol"] == "000002", "close"].iloc[0]), 8.8)

    def test_account_import_check_ignores_price_overrides_after_hash_change(self):
        root = Path.cwd() / "test_outputs" / "account_import_price_override_changed"
        if root.exists():
            shutil.rmtree(root)
        config = self.make_config(root)
        self.write_market_cache(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        override_path = output_dir / "account_price_overrides.csv"
        pd.DataFrame(
            [{"price_context": "import", "symbol": "000002", "date": "2026-06-16", "close": 8.8, "source": "broker_statement", "note": "manual"}]
        ).to_csv(override_path, index=False)
        check_account_price_overrides(config, asof_date="2026-06-16")
        pd.DataFrame(
            [{"price_context": "import", "symbol": "000002", "date": "2026-06-16", "close": 9.9, "source": "broker_statement", "note": "changed"}]
        ).to_csv(override_path, index=False)
        source = root / "account.csv"
        pd.DataFrame([{"cash": 1000.0, "symbol": "000002", "quantity": 100, "sellable_quantity": 100, "cost": 9.5}]).to_csv(source, index=False)

        paths = check_account_import_file(config, source, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_import_check_audit.csv"])
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("symbols_priced", failed)
        positions = pd.read_csv(paths["account_import_check_positions.csv"], dtype={"symbol": str})
        self.assertEqual(float(positions.loc[positions["symbol"] == "000002", "close"].iloc[0]), 0.0)

    def test_invalid_position_is_rejected_before_state_write(self):
        root = Path.cwd() / "test_outputs" / "account_import_invalid"
        config = self.make_config(root)
        config["account"] = {"require_import_check_before_confirm": False}
        self.write_market_cache(config)
        source = root / "bad_account.csv"
        pd.DataFrame([{"cash": 1000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 101, "cost": 9.5}]).to_csv(source, index=False)

        with self.assertRaises(ValueError):
            import_account_state(config, source, asof_date="2026-06-16", confirm=True)

        audit = pd.read_csv(root / "outputs" / "account_import_audit.csv")
        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("position_rows_valid", failed)
        store = DataStore.from_config(config)
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            count = conn.execute("select count(*) from account_snapshots").fetchone()[0]
        self.assertEqual(count, 0)

    def test_export_account_state_marks_current_positions(self):
        root = Path.cwd() / "test_outputs" / "account_state_export"
        config = self.make_config(root)
        self.write_market_cache(config)
        state_db = Path(config["paths"]["state_db"])
        if state_db.exists():
            state_db.unlink()
        store = DataStore.from_config(config)
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert into account_snapshots(date, cash, equity) values ('2026-06-16', 9000, 10000)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('600000', 100, 9.5, 40)")

        paths = export_account_state(config, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_state_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        snapshot = pd.read_csv(paths["account_state_snapshot.csv"])
        self.assertEqual(float(snapshot["market_value"].iloc[0]), 1000.0)
        self.assertEqual(int(snapshot["locked_quantity"].iloc[0]), 40)
        positions = pd.read_csv(paths["account_state_positions.csv"], dtype={"symbol": str})
        self.assertEqual(positions.loc[0, "sellable_quantity"], 60)
        missing = pd.read_csv(paths["account_state_missing_prices.csv"], dtype={"symbol": str})
        self.assertTrue(missing.empty)

    def test_export_account_state_explains_missing_price_coverage(self):
        root = Path.cwd() / "test_outputs" / "account_state_missing_detail"
        config = self.make_config(root)
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame(
                [
                    {"symbol": "600000", "name": "A", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False},
                    {"symbol": "000002", "name": "B", "board": "main", "industry": "real_estate", "list_date": "2020-01-01", "is_st": False},
                ]
            ),
        )
        store.write_frame(
            "prices",
            pd.DataFrame(
                [{"date": "2026-06-16", "symbol": "600000", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1000, "amount": 10000}]
            ),
        )
        state_db = Path(config["paths"]["state_db"])
        if state_db.exists():
            state_db.unlink()
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert into account_snapshots(date, cash, equity) values ('2026-06-16', 1000, 1000)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('000002', 100, 9.5, 0)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('600002', 100, 9.5, 0)")

        paths = export_account_state(config, asof_date="2026-06-16")

        missing = pd.read_csv(paths["account_state_missing_prices.csv"], dtype={"symbol": str})
        self.assertIn("symbol_in_stock_cache", missing.columns)
        self.assertIn("symbol_in_price_cache", missing.columns)
        self.assertIn("likely_reason", missing.columns)
        stock_only = missing[missing["symbol"] == "000002"].iloc[0]
        unknown = missing[missing["symbol"] == "600002"].iloc[0]
        self.assertTrue(bool(stock_only["symbol_in_stock_cache"]))
        self.assertFalse(bool(stock_only["symbol_in_price_cache"]))
        self.assertFalse(bool(unknown["symbol_in_stock_cache"]))
        self.assertIn("当前行情缓存覆盖范围", stock_only["likely_reason"])
        self.assertIn("不在当前股票列表", unknown["likely_reason"])

    def test_account_price_override_template_uses_missing_context_dates(self):
        root = Path.cwd() / "test_outputs" / "account_price_override_template"
        config = self.make_config(root)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [{"snapshot_date": "2026-06-15", "mark_date": "2026-06-16", "cash": 1000.0, "recorded_equity": 1000.0}]
        ).to_csv(output_dir / "account_state_snapshot.csv", index=False)
        pd.DataFrame(
            [
                {"price_context": "snapshot", "symbol": "000002", "quantity": 100, "cost": 9.5},
                {"price_context": "mark", "symbol": "000002", "quantity": 100, "cost": 9.5},
            ]
        ).to_csv(output_dir / "account_state_missing_prices.csv", index=False)

        path = write_account_price_override_template(config)
        template = pd.read_csv(path, dtype={"symbol": str})

        self.assertEqual(template.columns.tolist(), ["price_context", "symbol", "date", "close", "source", "note"])
        self.assertEqual(template["symbol"].tolist(), ["000002", "000002"])
        self.assertEqual(template["date"].tolist(), ["2026-06-15", "2026-06-16"])

    def test_account_price_override_check_audits_missing_file(self):
        root = Path.cwd() / "test_outputs" / "account_price_override_missing"
        config = self.make_config(root)

        paths = check_account_price_overrides(config, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_price_override_audit.csv"])
        self.assertFalse(bool(audit.loc[audit["check"] == "override_file_exists", "passed"].iloc[0]))
        effective = pd.read_csv(paths["account_price_override_effective.csv"])
        self.assertTrue(effective.empty)

    def test_account_price_override_check_reports_effective_coverage(self):
        root = Path.cwd() / "test_outputs" / "account_price_override_check"
        config = self.make_config(root)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"price_context": "mark", "symbol": "000002", "quantity": 100, "cost": 9.5}]).to_csv(
            output_dir / "account_state_missing_prices.csv",
            index=False,
        )
        pd.DataFrame(
            [
                {"price_context": "mark", "symbol": "000002", "date": "2026-06-15", "close": 8.7, "source": "broker", "note": "older"},
                {"price_context": "mark", "symbol": "000002", "date": "2026-06-16", "close": 8.8, "source": "broker", "note": "latest"},
            ]
        ).to_csv(output_dir / "account_price_overrides.csv", index=False)

        paths = check_account_price_overrides(config, asof_date="2026-06-16")

        audit = pd.read_csv(paths["account_price_override_audit.csv"])
        self.assertTrue(audit["passed"].astype(bool).all())
        source_hash = str(audit.loc[audit["check"] == "source_sha256", "value"].iloc[0])
        self.assertEqual(len(source_hash), 64)
        effective = pd.read_csv(paths["account_price_override_effective.csv"], dtype={"symbol": str})
        self.assertEqual(effective["symbol"].tolist(), ["000002"])
        self.assertEqual(float(effective["close"].iloc[0]), 8.8)

    def test_account_repair_template_prefills_state_and_flags_missing_prices(self):
        root = Path.cwd() / "test_outputs" / "account_repair_template"
        config = self.make_config(root)
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame(
                [
                    {"symbol": "600000", "name": "A", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False},
                    {"symbol": "000002", "name": "B", "board": "main", "industry": "real_estate", "list_date": "2020-01-01", "is_st": False},
                ]
            ),
        )
        store.write_frame(
            "prices",
            pd.DataFrame(
                [{"date": "2026-06-16", "symbol": "600000", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1000, "amount": 10000}]
            ),
        )
        state_db = Path(config["paths"]["state_db"])
        if state_db.exists():
            state_db.unlink()
        store.init_db()
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert into account_snapshots(date, cash, equity) values ('2026-06-16', 1234, 3234)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('600000', 100, 9.5, 20)")
            conn.execute("insert into positions(symbol, quantity, cost, locked_quantity) values ('000002', 200, 8.5, 0)")

        paths = write_account_repair_template(config, asof_date="2026-06-16")

        import_template = pd.read_csv(paths["account_import_from_state_template.csv"], dtype={"symbol": str})
        priced_only_template = pd.read_csv(paths["account_import_priced_only_template.csv"], dtype={"symbol": str})
        excluded = pd.read_csv(paths["account_repair_excluded_positions.csv"], dtype={"symbol": str})
        review = pd.read_csv(paths["account_repair_review.csv"], dtype={"symbol": str})
        self.assertEqual(import_template.columns.tolist(), ["cash", "symbol", "quantity", "sellable_quantity", "cost"])
        self.assertEqual(float(import_template["cash"].dropna().iloc[0]), 1234.0)
        self.assertEqual(set(priced_only_template["symbol"].dropna()), {"600000"})
        self.assertEqual(excluded["symbol"].tolist(), ["000002"])
        priced = review[review["symbol"] == "600000"].iloc[0]
        missing = review[review["symbol"] == "000002"].iloc[0]
        self.assertEqual(priced["price_status"], "priced")
        self.assertTrue(bool(priced["include_in_import"]))
        self.assertEqual(missing["price_status"], "missing_price")
        self.assertFalse(bool(missing["include_in_import"]))
        self.assertIn("account_state_missing_prices.csv", paths)

    def test_account_plan_offers_simulation_and_real_account_paths_when_audit_fails(self):
        root = Path.cwd() / "test_outputs" / "account_plan_failed"
        config = self.make_config(root)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {"check": "state_db_readable", "passed": True, "value": "ok", "threshold": "readable", "message": ""},
                {"check": "mark_positions_priced", "passed": False, "value": 1, "threshold": 2, "message": "missing"},
            ]
        ).to_csv(output_dir / "account_state_audit.csv", index=False)
        pd.DataFrame([{"price_context": "mark", "symbol": "600000", "quantity": 100, "cost": 10.0, "message": "missing"}]).to_csv(
            output_dir / "account_state_missing_prices.csv",
            index=False,
        )

        path = write_account_readiness_plan(config)
        plan = pd.read_csv(path)

        self.assertEqual(plan.columns.tolist(), ["priority", "path", "status", "blocker", "command", "next_action", "note"])
        self.assertIn("模拟账户路径", set(plan["path"]))
        self.assertIn("真实账户路径", set(plan["path"]))
        self.assertIn("缺价持仓处理", set(plan["path"]))
        self.assertIn("补价文件校验", set(plan["path"]))
        self.assertIn("account-reset --confirm", " ".join(plan["command"].astype(str)))
        self.assertIn("account-repair-template", " ".join(plan["command"].astype(str)))
        self.assertIn("account-price-check", " ".join(plan["command"].astype(str)))

    def test_account_plan_passed_state_points_to_practical_and_fill_import(self):
        root = Path.cwd() / "test_outputs" / "account_plan_passed"
        config = self.make_config(root)
        audit = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        snapshot = pd.DataFrame([{"position_count": 2, "cash": 1000.0, "marked_equity": 5000.0}])

        plan = build_account_readiness_plan(config, account_state_audit=audit, missing_prices=pd.DataFrame(), account_snapshot=snapshot)

        self.assertEqual(plan["status"].iloc[0], "passed")
        self.assertIn("practical", plan["command"].iloc[0])
        self.assertIn("fill-template", " ".join(plan["command"].astype(str)))

    def test_account_path_decision_separates_full_and_priced_only_imports(self):
        root = Path.cwd() / "test_outputs" / "account_path_decision"
        if root.exists():
            shutil.rmtree(root)
        config = self.make_config(root)
        self.write_market_cache(config)
        output_dir = root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {"check": "state_db_readable", "passed": True, "value": "ok", "threshold": "readable", "message": ""},
                {"check": "snapshot_positions_priced", "passed": False, "value": 1, "threshold": 2, "message": "missing"},
            ]
        ).to_csv(output_dir / "account_state_audit.csv", index=False)
        pd.DataFrame(
            [
                {"cash": 9000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 100, "cost": 9.5},
                {"cash": "", "symbol": "000002", "quantity": 200, "sellable_quantity": 200, "cost": 8.5},
            ]
        ).to_csv(output_dir / "account_import_from_state_template.csv", index=False)
        pd.DataFrame(
            [
                {"cash": 9000.0, "symbol": "600000", "quantity": 100, "sellable_quantity": 100, "cost": 9.5},
            ]
        ).to_csv(output_dir / "account_import_priced_only_template.csv", index=False)
        pd.DataFrame([{"symbol": "000002", "quantity": 200, "price_status": "missing_price"}]).to_csv(
            output_dir / "account_repair_excluded_positions.csv",
            index=False,
        )

        decisions = build_account_path_decision(config).set_index("path")

        self.assertFalse(bool(decisions.loc["current_account_state", "available"]))
        self.assertEqual(decisions.loc["simulation_reset", "status"], "available_with_confirmation")
        self.assertFalse(bool(decisions.loc["real_account_full_import", "available"]))
        self.assertEqual(decisions.loc["real_account_full_import", "status"], "blocked_missing_prices")
        self.assertTrue(bool(decisions.loc["real_account_priced_only_import", "available"]))
        self.assertEqual(decisions.loc["real_account_priced_only_import", "status"], "available_with_exclusions")

        check_account_import_file(config, output_dir / "account_import_priced_only_template.csv", asof_date="2026-06-16")
        decisions = build_account_path_decision(config).set_index("path")

        self.assertTrue(bool(decisions.loc["real_account_priced_only_import", "available"]))
        self.assertEqual(decisions.loc["real_account_priced_only_import", "status"], "precheck_passed_with_exclusions")
        self.assertIn("dry-run", decisions.loc["real_account_priced_only_import", "note"])
        self.assertIn("--ack-exclusions", decisions.loc["real_account_priced_only_import", "note"])
        self.assertIn("--ack-exclusions", decisions.loc["real_account_priced_only_import", "next_action"])

    def make_config(self, root: Path) -> dict:
        return {
            "paths": {
                "cache_dir": str(root / "cache"),
                "output_dir": str(root / "outputs"),
                "state_db": str(root / "state.sqlite3"),
            },
            "data": {"min_daily_price_coverage_ratio": 0.9, "require_benchmark_on_signal_date": False},
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
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
