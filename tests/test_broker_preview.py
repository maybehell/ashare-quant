from datetime import date, datetime
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.audit import backtest_config_sha256, mapping_sha256
from ashare_quant.autolive import auto_live_config_sha256
from ashare_quant.broker_initialization import initialize_broker_account_from_snapshot
from ashare_quant.broker_preview import (
    write_broker_account_import_preview,
    write_broker_capital_feasibility,
    write_broker_order_preview,
)
from ashare_quant.data import DataStore


class BrokerPreviewTests(unittest.TestCase):
    def fixture(self, name: str, *, isolated: bool = True) -> tuple[dict, Path, Path]:
        root = Path.cwd() / "test_outputs" / name
        output = root / "outputs_live"
        output.mkdir(parents=True, exist_ok=True)
        state_db = root / "huatai_live_state.sqlite3" if isolated else Path.cwd() / "data" / "state.sqlite3"
        config = {
            "paths": {"output_dir": str(output), "state_db": str(state_db)},
            "auto_live": {
                "enabled": True,
                "allow_simulated_account_snapshot": False,
                "account_snapshot_max_age_seconds": 300,
            },
        }
        fingerprint = auto_live_config_sha256(config)
        pd.DataFrame(
            [
                {
                    "snapshot_run_id": "snapshot-1",
                    "broker": "huatai",
                    "account_id": "masked-account",
                    "environment": "live",
                    "cash": 120000.0,
                    "available_cash": 100000.0,
                    "market_value": 10000.0,
                    "total_asset": 130000.0,
                    "frozen_cash": 20000.0,
                    "positions_count": 1,
                    "broker_time": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "auto_live_config_sha256": fingerprint,
                    "message": "",
                }
            ]
        ).to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        pd.DataFrame(
            [
                {
                    "snapshot_run_id": "snapshot-1",
                    "symbol": "600000",
                    "quantity": 100,
                    "sellable_quantity": 100,
                    "cost": 9.5,
                    "market_value": 10000.0,
                    "last_price": 10.0,
                    "message": "",
                }
            ]
        ).to_csv(output / "auto_live_broker_positions.csv", index=False)
        pd.DataFrame(
            [
                {"check": "broker_account_snapshot_present", "passed": True},
                {"check": "broker_position_quantities_valid", "passed": True},
                {"check": "local_account_snapshot_present", "passed": False},
                {"check": "local_vs_broker_cash_match", "passed": False},
            ]
        ).to_csv(output / "auto_live_broker_account_audit.csv", index=False)
        return config, output, state_db

    def passing_precheck(self, config: dict, source: Path) -> dict[str, Path]:
        output = Path(config["paths"]["output_dir"])
        audit = output / "account_import_check_audit.csv"
        positions = output / "account_import_check_positions.csv"
        snapshot = output / "account_import_check_snapshot.csv"
        pd.DataFrame([{"check": "would_write_state", "passed": True}]).to_csv(audit, index=False)
        pd.DataFrame([{"symbol": "600000", "close": 10.0}]).to_csv(positions, index=False)
        pd.DataFrame([{"cash": 120000.0, "market_value": 10000.0, "equity": 130000.0, "would_write_state": True}]).to_csv(snapshot, index=False)
        return {
            "account_import_check_audit.csv": audit,
            "account_import_check_positions.csv": positions,
            "account_import_check_snapshot.csv": snapshot,
        }

    def test_broker_order_preview_caps_sell_at_broker_sellable_and_is_non_executable(self):
        config, output, state_db = self.fixture("broker_order_preview_sell")
        config.update({
            "data": {"max_cache_age_days": 10},
            "trading": {"reserve_cash_ratio": 0.01, "lot_size": 100},
            "strategy": {"name": "formal"},
            "backtest": {"start": "2023-01-01"},
        })
        snapshot = pd.read_csv(output / "auto_live_broker_account_snapshot.csv")
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        positions = pd.read_csv(output / "auto_live_broker_positions.csv", dtype={"symbol": str})
        positions.loc[0, "sellable_quantity"] = 40
        positions.to_csv(output / "auto_live_broker_positions.csv", index=False)
        self.write_order_preview_gates(config, output)
        store = self.fake_store()

        with (
            patch("ashare_quant.broker_preview.DataStore.from_config", return_value=store),
            patch("ashare_quant.broker_preview.validate_market_data", return_value=[]),
            patch("ashare_quant.broker_preview.complete_market_dates", return_value=[date(2026, 7, 10)]),
            patch("ashare_quant.broker_preview.latest_close_map_asof", return_value={"600000": 10.0}),
            patch("ashare_quant.broker_preview.eligible_universe", return_value=pd.DataFrame([{"symbol": "600000"}])),
            patch("ashare_quant.broker_preview.build_configured_target_portfolio", return_value=(pd.Series(dtype=float), pd.DataFrame())),
            patch("ashare_quant.broker_preview.benchmark_exposure_multiplier", return_value=1.0),
        ):
            paths = write_broker_order_preview(config, current_date=date(2026, 7, 15))

        preview = pd.read_csv(paths["broker_order_preview.csv"], dtype={"symbol": str})
        audit = pd.read_csv(paths["broker_order_preview_audit.csv"])
        self.assertEqual(preview.loc[0, "side"], "sell")
        self.assertEqual(int(preview.loc[0, "quantity"]), 40)
        self.assertFalse(bool(preview.loc[0, "executable"]))
        self.assertTrue(bool(audit.loc[audit["check"] == "sell_quantities_within_broker_sellable", "passed"].iloc[0]))
        self.assertTrue(bool(audit.loc[audit["check"] == "broker_order_preview_ready", "passed"].iloc[0]))
        self.assertFalse((output / "orders.csv").exists())
        self.assertFalse((output / "rpa_orders.csv").exists())
        self.assertFalse(state_db.exists())

    def test_broker_order_preview_rounds_buys_to_board_lots(self):
        config, output, _ = self.fixture("broker_order_preview_buy")
        config.update({
            "data": {"max_cache_age_days": 10},
            "trading": {"reserve_cash_ratio": 0.01, "lot_size": 100},
            "strategy": {"name": "formal"},
            "backtest": {"start": "2023-01-01"},
        })
        snapshot = pd.read_csv(output / "auto_live_broker_account_snapshot.csv")
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        pd.DataFrame(columns=pd.read_csv(output / "auto_live_broker_positions.csv").columns).to_csv(
            output / "auto_live_broker_positions.csv", index=False
        )
        self.write_order_preview_gates(config, output)
        store = self.fake_store()
        weights = pd.Series({"600000": 0.10}, dtype=float)
        scores = pd.DataFrame([{"symbol": "600000", "score": 1.0, "industry": "bank", "board": "main"}])

        with (
            patch("ashare_quant.broker_preview.DataStore.from_config", return_value=store),
            patch("ashare_quant.broker_preview.validate_market_data", return_value=[]),
            patch("ashare_quant.broker_preview.complete_market_dates", return_value=[date(2026, 7, 10)]),
            patch("ashare_quant.broker_preview.latest_close_map_asof", return_value={"600000": 10.0}),
            patch("ashare_quant.broker_preview.eligible_universe", return_value=pd.DataFrame([{"symbol": "600000"}])),
            patch("ashare_quant.broker_preview.build_configured_target_portfolio", return_value=(weights, scores)),
            patch("ashare_quant.broker_preview.benchmark_exposure_multiplier", return_value=1.0),
        ):
            paths = write_broker_order_preview(config, current_date=date(2026, 7, 15))

        preview = pd.read_csv(paths["broker_order_preview.csv"], dtype={"symbol": str})
        self.assertEqual(preview.loc[0, "side"], "buy")
        self.assertEqual(int(preview.loc[0, "quantity"]) % 100, 0)
        provenance = pd.read_csv(paths["broker_order_preview_provenance.csv"]).set_index("item")
        self.assertEqual(str(provenance.loc["executable", "value"]).lower(), "false")

    def test_broker_order_preview_applies_audited_forward_warning_exposure(self):
        config, output, _ = self.fixture("broker_order_preview_warning")
        config.update({
            "data": {"max_cache_age_days": 10},
            "trading": {"reserve_cash_ratio": 0.01, "lot_size": 100},
            "strategy": {"name": "formal"},
            "strategy_health": {"early_forward_warning_exposure": 0.50},
            "backtest": {"start": "2023-01-01"},
        })
        snapshot = pd.read_csv(output / "auto_live_broker_account_snapshot.csv")
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        pd.DataFrame(columns=pd.read_csv(output / "auto_live_broker_positions.csv").columns).to_csv(
            output / "auto_live_broker_positions.csv", index=False
        )
        self.write_order_preview_gates(config, output)
        pd.DataFrame([
            {"check": "paper_orders_allowed", "passed": True, "value": True},
            {"check": "forward_early_warning_clear", "passed": False, "value": -0.041},
        ]).to_csv(output / "strategy_health.csv", index=False)
        store = self.fake_store()
        weights = pd.Series({"600000": 0.10}, dtype=float)
        scores = pd.DataFrame([{"symbol": "600000", "score": 1.0, "industry": "bank", "board": "main"}])

        with (
            patch("ashare_quant.broker_preview.DataStore.from_config", return_value=store),
            patch("ashare_quant.broker_preview.validate_market_data", return_value=[]),
            patch("ashare_quant.broker_preview.complete_market_dates", return_value=[date(2026, 7, 10)]),
            patch("ashare_quant.broker_preview.latest_close_map_asof", return_value={"600000": 10.0}),
            patch("ashare_quant.broker_preview.eligible_universe", return_value=pd.DataFrame([{"symbol": "600000"}])),
            patch("ashare_quant.broker_preview.build_configured_target_portfolio", return_value=(weights, scores)),
            patch("ashare_quant.broker_preview.benchmark_exposure_multiplier", return_value=1.0),
        ):
            paths = write_broker_order_preview(config, current_date=date(2026, 7, 15))

        preview = pd.read_csv(paths["broker_order_preview.csv"], dtype={"symbol": str})
        provenance = pd.read_csv(paths["broker_order_preview_provenance.csv"]).set_index("item")
        self.assertAlmostEqual(float(preview.loc[0, "target_weight"]), 0.05)
        self.assertEqual(float(provenance.loc["strategy_health_exposure_multiplier", "value"]), 0.50)
        self.assertEqual(provenance.loc["strategy_health_exposure_reason", "value"], "early_forward_warning")

    def write_order_preview_gates(self, config: dict, output: Path) -> None:
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True, "value": "ready"}]).to_csv(
            output / "broker_account_import_preview_audit.csv", index=False
        )
        pd.DataFrame([{"check": "all", "passed": True}]).to_csv(output / "practical_readiness.csv", index=False)
        pd.DataFrame([{"check": "paper_orders_allowed", "value": True}]).to_csv(output / "strategy_health.csv", index=False)
        pd.DataFrame([{
            "status": "ok",
            "live_config_sha256": mapping_sha256(config),
            "backtest_config_sha256": backtest_config_sha256(config),
            "orders_copied": False,
            "positions_copied": False,
            "account_state_copied": False,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }]).to_csv(output / "live_strategy_evidence_sync_summary.csv", index=False)

    def fake_store(self):
        class Store:
            def load_market_data(self):
                stocks = pd.DataFrame([{"symbol": "600000"}])
                prices = pd.DataFrame([{"date": date(2026, 7, 10), "symbol": "600000", "close": 10.0}])
                return stocks, prices, pd.DataFrame()

            def load_benchmark(self, required=False):
                return pd.DataFrame([{"date": date(2026, 7, 10), "close": 4000.0}])

        return Store()

    def test_capital_feasibility_accepts_representable_board_lot_portfolio(self):
        config, output, state_db = self.fixture("broker_capital_feasible")
        config.update({
            "data": {"max_cache_age_days": 10},
            "trading": {"lot_size": 100},
            "strategy": {"max_stock_weight": 0.10},
            "auto_live": {
                **config["auto_live"],
                "capital_feasibility": {"min_target_weight_coverage": 0.80},
            },
        })
        snapshot_path = output / "auto_live_broker_account_snapshot.csv"
        snapshot = pd.read_csv(snapshot_path)
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(snapshot_path, index=False)

        with (
            patch("ashare_quant.broker_preview.DataStore.from_config", return_value=self.fake_store()),
            patch("ashare_quant.broker_preview.validate_market_data", return_value=[]),
            patch("ashare_quant.broker_preview.complete_market_dates", return_value=[date(2026, 7, 10)]),
            patch("ashare_quant.broker_preview.latest_close_map_asof", return_value={"600000": 10.0}),
            patch("ashare_quant.broker_preview.eligible_universe", return_value=pd.DataFrame([{"symbol": "600000"}])),
            patch("ashare_quant.broker_preview.build_configured_target_portfolio", return_value=(pd.Series({"600000": 0.10}), pd.DataFrame())),
        ):
            paths = write_broker_capital_feasibility(config)

        audit = pd.read_csv(paths["broker_capital_feasibility_audit.csv"]).set_index("check")
        detail = pd.read_csv(paths["broker_capital_feasibility_detail.csv"], dtype={"symbol": str})
        self.assertTrue(bool(audit.loc["broker_capital_feasibility_ready", "passed"]))
        self.assertEqual(float(audit.loc["capital_target_weight_coverage", "value"]), 1.0)
        self.assertEqual(float(audit.loc["capital_integer_target_weight_coverage", "value"]), 1.0)
        self.assertEqual(float(audit.loc["capital_integer_allocation_l1_error", "value"]), 0.0)
        self.assertEqual(int(detail.loc[0, "allocated_lots_current"]), 13)
        self.assertAlmostEqual(float(audit.loc["capital_minimum_equity_for_integer_tracking", "value"]), 10000.0)
        self.assertAlmostEqual(float(detail.loc[0, "required_equity_at_target_weight"]), 10000.0)
        self.assertFalse((output / "orders.csv").exists())
        self.assertFalse((output / "rpa_orders.csv").exists())
        self.assertFalse(state_db.exists())

    def test_capital_feasibility_blocks_account_too_small_for_one_target_lot(self):
        config, output, state_db = self.fixture("broker_capital_infeasible")
        config.update({
            "data": {"max_cache_age_days": 10},
            "trading": {"lot_size": 100},
            "strategy": {"max_stock_weight": 0.05},
            "auto_live": {
                **config["auto_live"],
                "capital_feasibility": {"min_target_weight_coverage": 0.80},
            },
        })
        snapshot_path = output / "auto_live_broker_account_snapshot.csv"
        snapshot = pd.read_csv(snapshot_path)
        snapshot.loc[0, ["cash", "available_cash", "total_asset", "market_value", "positions_count"]] = [2000.0, 2000.0, 2000.0, 0.0, 0]
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(snapshot_path, index=False)

        with (
            patch("ashare_quant.broker_preview.DataStore.from_config", return_value=self.fake_store()),
            patch("ashare_quant.broker_preview.validate_market_data", return_value=[]),
            patch("ashare_quant.broker_preview.complete_market_dates", return_value=[date(2026, 7, 10)]),
            patch("ashare_quant.broker_preview.latest_close_map_asof", return_value={"600000": 100.0}),
            patch("ashare_quant.broker_preview.eligible_universe", return_value=pd.DataFrame([{"symbol": "600000"}])),
            patch("ashare_quant.broker_preview.build_configured_target_portfolio", return_value=(pd.Series({"600000": 0.05}), pd.DataFrame())),
        ):
            paths = write_broker_capital_feasibility(config)

        audit = pd.read_csv(paths["broker_capital_feasibility_audit.csv"]).set_index("check")
        detail = pd.read_csv(paths["broker_capital_feasibility_detail.csv"], dtype={"symbol": str})
        self.assertFalse(bool(audit.loc["broker_capital_feasibility_ready", "passed"]))
        self.assertEqual(float(audit.loc["capital_target_weight_coverage", "value"]), 0.0)
        self.assertFalse(bool(audit.loc["capital_any_target_lot_affordable", "passed"]))
        self.assertAlmostEqual(float(detail.loc[0, "required_equity_at_target_weight"]), 200000.0)
        self.assertAlmostEqual(float(audit.loc["capital_minimum_equity_for_coverage", "value"]), 200000.0)
        self.assertFalse((output / "orders.csv").exists())
        self.assertFalse(bool(audit.loc["capital_single_lot_within_stock_cap", "passed"]))
        self.assertEqual(float(audit.loc["capital_integer_target_weight_coverage", "value"]), 0.0)
        self.assertEqual(int(detail.loc[0, "allocated_lots_current"]), 0)
        self.assertAlmostEqual(float(audit.loc["capital_minimum_equity_for_integer_tracking", "value"]), 200000.0)
        self.assertFalse((output / "rpa_orders.csv").exists())
        self.assertFalse(state_db.exists())

    def initialization_fixture(self, name: str) -> tuple[dict, Path, Path]:
        config, output, state_db = self.fixture(name)
        config["paths"]["cache_dir"] = str(output.parent / "cache")
        config["data"] = {
            "min_daily_price_coverage_ratio": 0.9,
            "require_benchmark_on_signal_date": False,
        }
        config["reconciliation"] = {"cash_tolerance": 1.0}
        config["auto_live"].update({"emergency_stop": True, "allow_broker_submit": False})
        if state_db.exists():
            state_db.unlink()
        snapshot_path = output / "auto_live_broker_account_snapshot.csv"
        snapshot = pd.read_csv(snapshot_path)
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(snapshot_path, index=False)
        positions_path = output / "auto_live_broker_positions.csv"
        positions = pd.read_csv(positions_path, dtype={"symbol": str})
        positions.loc[0, "last_price"] = 100.0
        positions.loc[0, "market_value"] = 10000.0
        positions.to_csv(positions_path, index=False)
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame([{
                "symbol": "600000", "name": "A", "board": "main", "industry": "bank",
                "list_date": "2020-01-01", "is_st": False,
            }]),
        )
        store.write_frame(
            "prices",
            pd.DataFrame([{
                "date": date.today().isoformat(), "symbol": "600000", "open": 100.0,
                "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000,
                "amount": 100000.0,
            }]),
        )
        return config, output, state_db

    def test_broker_account_initialization_defaults_to_audited_no_write(self):
        config, output, state_db = self.initialization_fixture("broker_account_init_dry")

        paths = initialize_broker_account_from_snapshot(
            config,
            expected_snapshot_run_id="snapshot-1",
            expected_account_id="masked-account",
            confirm=False,
        )

        audit = pd.read_csv(paths["broker_account_initialization_audit.csv"]).set_index("check")
        self.assertTrue(bool(audit.loc["broker_account_initialization_ready", "passed"]))
        self.assertFalse(state_db.exists())
        self.assertFalse((output / "auto_live_account_binding.json").exists())

    def test_broker_account_initialization_rejects_wrong_account_confirmation(self):
        config, _, state_db = self.initialization_fixture("broker_account_init_wrong_account")

        with self.assertRaisesRegex(RuntimeError, "initialization blocked"):
            initialize_broker_account_from_snapshot(
                config,
                expected_snapshot_run_id="snapshot-1",
                expected_account_id="different-account",
                confirm=True,
            )

        self.assertFalse(state_db.exists())

    def test_broker_account_initialization_atomically_commits_reconciled_total_cash(self):
        config, output, state_db = self.initialization_fixture("broker_account_init_commit")
        (output / "rpa_orders.csv").write_text("stale", encoding="utf-8")

        paths = initialize_broker_account_from_snapshot(
            config,
            expected_snapshot_run_id="snapshot-1",
            expected_account_id="masked-account",
            confirm=True,
        )

        self.assertTrue(state_db.exists())
        with sqlite3.connect(state_db) as conn:
            snapshot = conn.execute("select cash, equity from account_snapshots").fetchone()
            position = conn.execute("select symbol, quantity, locked_quantity from positions").fetchone()
        self.assertEqual(snapshot, (120000.0, 130000.0))
        self.assertEqual(position, ("600000", 100, 0))
        audit = pd.read_csv(paths["broker_account_initialization_audit.csv"]).set_index("check")
        self.assertTrue(bool(audit.loc["broker_account_initialization_committed", "passed"]))
        broker_audit = pd.read_csv(paths["auto_live_broker_account_audit.csv"])
        self.assertTrue(bool(broker_audit["passed"].astype(bool).all()))
        self.assertFalse((output / "rpa_orders.csv").exists())
        self.assertTrue((output / "account_state_snapshot.csv").exists())
        self.assertTrue((output / "account_state_positions.csv").exists())
        self.assertTrue((output / "account_state_audit.csv").exists())
        self.assertFalse((output / "auto_live_account_binding.json").exists())

    def test_broker_order_preview_rechecks_broker_snapshot_freshness(self):
        config, output, _ = self.fixture("broker_order_preview_stale_snapshot")
        config.update({
            "data": {"max_cache_age_days": 10},
            "trading": {"reserve_cash_ratio": 0.01, "lot_size": 100},
            "strategy": {"name": "formal"},
            "backtest": {"start": "2023-01-01"},
        })
        snapshot_path = output / "auto_live_broker_account_snapshot.csv"
        snapshot = pd.read_csv(snapshot_path)
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.loc[0, "generated_at"] = "2026-01-01T00:00:00+00:00"
        snapshot.loc[0, "broker_time"] = "2026-01-01T00:00:00+00:00"
        snapshot.to_csv(snapshot_path, index=False)
        self.write_order_preview_gates(config, output)

        with patch("ashare_quant.broker_preview.DataStore.from_config") as store_mock:
            paths = write_broker_order_preview(config, current_date=date(2026, 7, 15))

        audit = pd.read_csv(paths["broker_order_preview_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["broker_snapshot_fresh", "passed"]))
        self.assertFalse(bool(audit.loc["broker_order_preview_ready", "passed"]))
        store_mock.assert_not_called()

    def test_broker_order_preview_rejects_stale_strategy_evidence(self):
        config, output, _ = self.fixture("broker_order_preview_stale_evidence")
        config.update({
            "data": {"max_cache_age_days": 10},
            "trading": {"reserve_cash_ratio": 0.01, "lot_size": 100},
            "strategy": {"name": "formal"},
            "backtest": {"start": "2023-01-01"},
        })
        config["auto_live"]["strategy_evidence_sync_max_age_seconds"] = 60
        snapshot = pd.read_csv(output / "auto_live_broker_account_snapshot.csv")
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        self.write_order_preview_gates(config, output)
        deployment_path = output / "live_strategy_evidence_sync_summary.csv"
        deployment = pd.read_csv(deployment_path)
        deployment.loc[0, "generated_at"] = "2026-01-01T00:00:00+00:00"
        deployment.to_csv(deployment_path, index=False)

        with patch("ashare_quant.broker_preview.DataStore.from_config") as store_mock:
            paths = write_broker_order_preview(config, current_date=date(2026, 7, 15))

        audit = pd.read_csv(paths["broker_order_preview_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["strategy_evidence_current", "passed"]))
        self.assertFalse(bool(audit.loc["broker_order_preview_ready", "passed"]))
        store_mock.assert_not_called()

    def test_broker_order_preview_stops_before_market_data_when_strategy_is_paused(self):
        config, output, _ = self.fixture("broker_order_preview_paused_strategy")
        config.update({"strategy": {"name": "formal"}, "backtest": {"start": "2023-01-01"}})
        snapshot_path = output / "auto_live_broker_account_snapshot.csv"
        snapshot = pd.read_csv(snapshot_path)
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(snapshot_path, index=False)
        self.write_order_preview_gates(config, output)
        pd.DataFrame([{"check": "paper_orders_allowed", "value": False}]).to_csv(
            output / "strategy_health.csv", index=False
        )

        with patch("ashare_quant.broker_preview.DataStore.from_config") as store_mock:
            paths = write_broker_order_preview(config, current_date=date(2026, 7, 15))

        audit = pd.read_csv(paths["broker_order_preview_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["strategy_health_allows_preview", "passed"]))
        self.assertFalse(bool(audit.loc["broker_order_preview_ready", "passed"]))
        store_mock.assert_not_called()

    def test_broker_order_preview_rejects_mixed_snapshot_batches(self):
        config, output, _ = self.fixture("broker_order_preview_mixed_batches")
        config.update({"strategy": {"name": "formal"}, "backtest": {"start": "2023-01-01"}})
        snapshot_path = output / "auto_live_broker_account_snapshot.csv"
        snapshot = pd.read_csv(snapshot_path)
        snapshot.loc[0, "auto_live_config_sha256"] = auto_live_config_sha256(config)
        snapshot.to_csv(snapshot_path, index=False)
        positions_path = output / "auto_live_broker_positions.csv"
        positions = pd.read_csv(positions_path)
        positions.loc[0, "snapshot_run_id"] = "different-snapshot"
        positions.to_csv(positions_path, index=False)
        self.write_order_preview_gates(config, output)

        with patch("ashare_quant.broker_preview.DataStore.from_config") as store_mock:
            paths = write_broker_order_preview(config, current_date=date(2026, 7, 15))

        audit = pd.read_csv(paths["broker_order_preview_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["broker_positions_snapshot_match", "passed"]))
        self.assertFalse(bool(audit.loc["broker_order_preview_ready", "passed"]))
        store_mock.assert_not_called()

    def test_preview_uses_total_cash_and_never_writes_live_state(self):
        config, output, state_db = self.fixture("broker_preview_ok")

        with patch("ashare_quant.broker_preview.check_account_import_file", side_effect=self.passing_precheck):
            paths = write_broker_account_import_preview(config)

        preview = pd.read_csv(paths["broker_account_import_preview.csv"], dtype={"symbol": str})
        audit = pd.read_csv(paths["broker_account_import_preview_audit.csv"])
        self.assertEqual(float(preview["cash"].dropna().iloc[0]), 120000.0)
        self.assertTrue(bool(audit.loc[audit["check"] == "preview_uses_total_broker_cash", "passed"].iloc[0]))
        self.assertTrue(bool(audit.loc[audit["check"] == "preview_equity_matches_broker", "passed"].iloc[0]))
        self.assertEqual(preview["symbol"].dropna().iloc[0], "600000")
        self.assertFalse(state_db.exists())
        self.assertTrue(bool(audit.loc[audit["check"] == "live_state_db_unchanged", "passed"].iloc[0]))
        self.assertTrue(bool(audit.loc[audit["check"] == "broker_account_preview_ready", "passed"].iloc[0]))

    def test_preview_blocks_shared_paper_state_database(self):
        config, output, _ = self.fixture("broker_preview_shared", isolated=False)

        with patch("ashare_quant.broker_preview.check_account_import_file", side_effect=self.passing_precheck):
            paths = write_broker_account_import_preview(config)

        audit = pd.read_csv(paths["broker_account_import_preview_audit.csv"])
        self.assertFalse(bool(audit.loc[audit["check"] == "live_state_db_isolated", "passed"].iloc[0]))
        self.assertFalse(bool(audit.loc[audit["check"] == "broker_account_preview_ready", "passed"].iloc[0]))

    def test_preview_blocks_fractional_share_quantities(self):
        config, output, _ = self.fixture("broker_preview_fractional")
        positions = pd.read_csv(output / "auto_live_broker_positions.csv", dtype={"symbol": str})
        positions["quantity"] = positions["quantity"].astype(float)
        positions.loc[0, "quantity"] = 100.5
        positions.to_csv(output / "auto_live_broker_positions.csv", index=False)

        with patch("ashare_quant.broker_preview.check_account_import_file", side_effect=self.passing_precheck):
            paths = write_broker_account_import_preview(config)

        audit = pd.read_csv(paths["broker_account_import_preview_audit.csv"])
        self.assertFalse(bool(audit.loc[audit["check"] == "preview_content_valid", "passed"].iloc[0]))
        self.assertFalse(bool(audit.loc[audit["check"] == "broker_account_preview_ready", "passed"].iloc[0]))


if __name__ == "__main__":
    unittest.main()
