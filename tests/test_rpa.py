from pathlib import Path
import json
import unittest
from datetime import date

import pandas as pd

from ashare_quant.audit import mapping_sha256, orders_frame_sha256
from ashare_quant.data import DataStore
from ashare_quant.rpa import (
    build_pre_trade_checks,
    build_rpa_preflight,
    export_rpa_orders,
    enforce_daily_amount_limit,
    fill_rpa_ticket,
    pre_trade_checks_passed,
    preview_rpa_orders,
    ticket_id,
    validate_rpa_order,
    write_rpa_preflight,
)
from ashare_quant.strategy_activation import ensure_execution_epoch


class RpaTests(unittest.TestCase):
    def execution_epoch_rows(self, config: dict) -> list[dict[str, str]]:
        return [
            {"item": key, "value": value}
            for key, value in ensure_execution_epoch(config).items()
        ]

    def test_validate_blocks_non_lot_buy(self):
        config = {
            "trading": {"lot_size": 100},
            "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000},
        }
        status, message = validate_rpa_order(config, "buy", 99, 10, 990)
        self.assertEqual(status, "blocked")
        self.assertEqual(message, "buy_quantity_not_lot_size")

    def test_ticket_id_is_stable(self):
        self.assertEqual(ticket_id("2026-01-01", "000001", "buy", 100, 10), ticket_id("2026-01-01", "000001", "buy", 100, 10))

    def test_daily_limit_keeps_ready_tickets_until_cap_then_blocks_overflow(self):
        config = {"rpa": {"max_daily_order_amount": 1000}}
        tickets = pd.DataFrame(
            [
                {"date": "2026-01-01", "side": "buy", "status": "ready", "estimated_amount": 600, "message": "manual_confirm_required"},
                {"date": "2026-01-01", "side": "buy", "status": "ready", "estimated_amount": 600, "message": "manual_confirm_required"},
                {"date": "2026-01-01", "side": "sell", "status": "ready", "estimated_amount": 2000, "message": "manual_confirm_required"},
            ]
        )
        result = enforce_daily_amount_limit(config, tickets)
        self.assertEqual(result["status"].tolist(), ["ready", "blocked", "ready"])
        self.assertEqual(result["message"].tolist()[1], "exceeds_daily_order_amount_limit")

    def test_stale_order_is_blocked(self):
        config = {"trading": {"lot_size": 100}, "rpa": {"max_single_order_amount": 100000}}
        status, message = validate_rpa_order(
            config,
            "buy",
            100,
            10,
            1000,
            order_date="2024-01-01",
            latest_market_date="2024-01-02",
        )
        self.assertEqual(status, "blocked")
        self.assertEqual(message, "stale_order_date")

    def test_order_before_today_is_blocked(self):
        config = {"trading": {"lot_size": 100}, "rpa": {"max_single_order_amount": 100000}}
        status, message = validate_rpa_order(
            config,
            "buy",
            100,
            10,
            1000,
            order_date="2026-01-01",
            latest_market_date="2026-01-01",
            current_date=date(2026, 1, 2),
        )
        self.assertEqual(status, "blocked")
        self.assertEqual(message, "stale_order_date")

    def test_preview_summarizes_blocked_reasons(self):
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-01",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10,
                    "estimated_amount": 1000,
                    "status": "blocked",
                    "message": "stale_order_date",
                }
            ]
        )
        path = Path("test_outputs/rpa_preview.csv")
        path.parent.mkdir(exist_ok=True)
        tickets.to_csv(path, index=False)
        text = preview_rpa_orders({"paths": {"output_dir": "test_outputs"}}, path)
        self.assertIn("Blocked reasons: stale_order_date=1", text)
        self.assertIn("No ready tickets", text)

    def test_pre_trade_checks_pass_for_verified_ready_tickets(self):
        config = {
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
            },
        }
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        )
        provenance = {"source": "paper", "allow_weak_strategy": "False", "order_count": "1"}
        checks = build_pre_trade_checks(config, tickets, provenance)
        self.assertTrue(pre_trade_checks_passed(checks))

    def test_pre_trade_checks_support_ready_only_ticket_export(self):
        config = {
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
            },
        }
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "sell",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        )
        provenance = {"source": "paper", "allow_weak_strategy": "False", "order_count": "2"}
        checks = build_pre_trade_checks(config, tickets, provenance, source_ticket_count=2)
        failed = set(checks.loc[~checks["passed"], "check"])
        self.assertNotIn("orders_provenance_count", failed)
        self.assertNotIn("blocked_ticket_count", failed)
        self.assertTrue(pre_trade_checks_passed(checks))

    def test_pre_trade_checks_fail_for_blocked_tickets(self):
        config = {
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
            },
        }
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "blocked",
                    "message": "stale_order_date",
                }
            ]
        )
        provenance = {"source": "paper", "allow_weak_strategy": "False", "order_count": "1"}
        checks = build_pre_trade_checks(config, tickets, provenance)
        self.assertFalse(pre_trade_checks_passed(checks))
        failed = set(checks.loc[~checks["passed"], "check"])
        self.assertIn("ready_ticket_count", failed)
        self.assertIn("blocked_ticket_count", failed)

    def test_pre_trade_checks_require_account_reconciliation_when_enabled(self):
        output_dir = Path("test_outputs/rpa_reconciliation_required")
        output_dir.mkdir(parents=True, exist_ok=True)
        reconciliation_path = output_dir / "account_reconciliation.csv"
        if reconciliation_path.exists():
            reconciliation_path.unlink()
        config = {
            "paths": {"output_dir": str(output_dir)},
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
                "require_account_reconciliation": True,
            },
        }
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        )
        provenance = {"source": "paper", "allow_weak_strategy": "False", "order_count": "1"}
        checks = build_pre_trade_checks(config, tickets, provenance)
        failed = set(checks.loc[~checks["passed"], "check"])
        self.assertIn("account_reconciliation", failed)

        pd.DataFrame(
            [{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]
        ).to_csv(reconciliation_path, index=False)
        checks = build_pre_trade_checks(config, tickets, provenance)
        self.assertNotIn("account_reconciliation", set(checks.loc[~checks["passed"], "check"]))

    def test_pre_trade_checks_require_account_state_audit_when_enabled(self):
        output_dir = Path("test_outputs/rpa_account_state_required")
        output_dir.mkdir(parents=True, exist_ok=True)
        audit_path = output_dir / "account_state_audit.csv"
        if audit_path.exists():
            audit_path.unlink()
        config = {
            "paths": {"output_dir": str(output_dir)},
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
                "require_account_state_audit": True,
            },
        }
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        )
        provenance = {"source": "paper", "allow_weak_strategy": "False", "order_count": "1"}
        checks = build_pre_trade_checks(config, tickets, provenance)
        failed = set(checks.loc[~checks["passed"], "check"])
        self.assertIn("account_state_audit", failed)

        pd.DataFrame(
            [{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]
        ).to_csv(audit_path, index=False)
        checks = build_pre_trade_checks(config, tickets, provenance)
        self.assertNotIn("account_state_audit", set(checks.loc[~checks["passed"], "check"]))

    def test_pre_trade_checks_require_strategy_health_when_enabled(self):
        output_dir = Path("test_outputs/rpa_strategy_health_required")
        output_dir.mkdir(parents=True, exist_ok=True)
        health_path = output_dir / "strategy_health.csv"
        if health_path.exists():
            health_path.unlink()
        config = {
            "paths": {"output_dir": str(output_dir)},
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
                "require_strategy_health": True,
            },
        }
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        )
        provenance = {"source": "paper", "allow_weak_strategy": "False", "order_count": "1"}
        checks = build_pre_trade_checks(config, tickets, provenance)
        failed = set(checks.loc[~checks["passed"], "check"])
        self.assertIn("strategy_health_guard", failed)

        pd.DataFrame(
            [
                {
                    "check": "paper_orders_allowed",
                    "passed": True,
                    "value": True,
                    "threshold": True,
                    "severity": "ok",
                    "action": "",
                    "note": "",
                }
            ]
        ).to_csv(health_path, index=False)
        checks = build_pre_trade_checks(config, tickets, provenance)
        self.assertNotIn("strategy_health_guard", set(checks.loc[~checks["passed"], "check"]))

    def test_pre_trade_checks_require_order_liquidity_audit_when_enabled(self):
        output_dir = Path("test_outputs/rpa_order_liquidity_required")
        output_dir.mkdir(parents=True, exist_ok=True)
        audit_path = output_dir / "order_liquidity_audit.csv"
        if audit_path.exists():
            audit_path.unlink()
        config = {
            "paths": {"output_dir": str(output_dir)},
            "trading": {"lot_size": 100},
            "execution": {"require_order_liquidity_audit": True},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
            },
        }
        tickets = pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        )
        provenance = {"source": "paper", "allow_weak_strategy": "False", "order_count": "1"}
        checks = build_pre_trade_checks(config, tickets, provenance)
        self.assertIn("order_liquidity_audit", set(checks.loc[~checks["passed"], "check"]))

        pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "symbol": "600000",
                    "passed": True,
                    "amount_participation": 0.001,
                    "volume_participation": 0.001,
                }
            ]
        ).to_csv(audit_path, index=False)
        checks = build_pre_trade_checks(config, tickets, provenance)
        self.assertNotIn("order_liquidity_audit", set(checks.loc[~checks["passed"], "check"]))


    def test_rpa_preflight_passes_blocking_checks_for_ready_ticket(self):
        output_dir = Path("test_outputs/rpa_preflight_ready")
        output_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output_dir)},
            "rpa": {"require_manual_final_confirm": True, "never_click_submit": True},
            "execution": {"require_rpa_click_dependency": False, "require_order_liquidity_audit": False},
        }
        pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]).to_csv(output_dir / "orders.csv", index=False)
        pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        ).to_csv(output_dir / "rpa_orders.csv", index=False)
        pd.DataFrame(columns=["ticket_id", "date", "symbol", "side", "quantity", "limit_price", "estimated_amount", "status", "message"]).to_csv(output_dir / "rpa_blocked_orders.csv", index=False)
        pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]).to_csv(output_dir / "rpa_pre_trade_checks.csv", index=False)
        layout_path = output_dir / "layout.yaml"
        layout_json = json.dumps(
            {
                "broker": {"window_title": "HTS Trader", "coordinate_mode": "absolute_screen"},
                "fields": {
                    "buy_tab": {"x": 10, "y": 10},
                    "sell_tab": {"x": 20, "y": 20},
                    "symbol": {"x": 30, "y": 30},
                    "price": {"x": 40, "y": 40},
                    "quantity": {"x": 50, "y": 50},
                },
                "safety": {"require_manual_final_confirm": True, "never_click_submit": True},
            }
        )
        layout_path.write_text(layout_json, encoding="utf-8")

        path = write_rpa_preflight(config, layout_path=layout_path)
        preflight = pd.read_csv(path)
        blocking = preflight[preflight["severity"].astype(str).str.contains("safety_blocker|execution_blocker", regex=True, na=False)]

        self.assertTrue(bool(blocking["passed"].all()))
        self.assertTrue(bool(preflight.loc[preflight["check"] == "layout_fields_calibrated", "passed"].iloc[0]))
        self.assertTrue(bool(preflight.loc[preflight["check"] == "broker_window_title_configured", "passed"].iloc[0]))

    def test_rpa_preflight_flags_default_layout_placeholders(self):
        output_dir = Path("test_outputs/rpa_preflight_template")
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=["date", "symbol", "side", "quantity"]).to_csv(output_dir / "orders.csv", index=False)
        config = {
            "paths": {"output_dir": str(output_dir)},
            "rpa": {"require_manual_final_confirm": True, "never_click_submit": True},
            "execution": {"require_rpa_click_dependency": False, "require_order_liquidity_audit": False},
        }

        preflight = build_rpa_preflight(config, optional_dependencies={"pyautogui": False})
        by_check = {row.check: row for row in preflight.itertuples(index=False)}

        self.assertFalse(bool(by_check["layout_fields_calibrated"].passed))
        self.assertEqual(by_check["layout_fields_calibrated"].severity, "click_blocker")
        self.assertFalse(bool(by_check["broker_window_title_configured"].passed))

    def test_execute_fill_requires_pre_trade_checks(self):
        output_dir = Path("test_outputs/rpa_execute_precheck")
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "ticket_id": "a",
                    "date": "2026-01-02",
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "estimated_amount": 1000.0,
                    "status": "ready",
                    "message": "manual_confirm_required",
                }
            ]
        ).to_csv(output_dir / "rpa_orders.csv", index=False)
        config = {
            "paths": {"output_dir": str(output_dir)},
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
            },
        }
        with self.assertRaisesRegex(RuntimeError, "RPA 下单前检查未通过"):
            fill_rpa_ticket(config, output_dir / "rpa_orders.csv", None, 0, "missing-layout.yaml", execute=True)

    def test_export_requires_paper_provenance_by_default(self):
        output_dir = Path("test_outputs/rpa_provenance")
        output_dir.mkdir(parents=True, exist_ok=True)
        orders = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}])
        orders.to_csv(output_dir / "orders.csv", index=False)
        config = {"paths": {"output_dir": str(output_dir)}, "trading": {"lot_size": 100}, "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000}, "data": {"cache_dir": "data/cache", "state_db": "data/state.sqlite3"}}

        with self.assertRaisesRegex(RuntimeError, "orders_provenance"):
            export_rpa_orders(config)

    def test_export_accepts_matching_paper_provenance(self):
        output_dir = Path("test_outputs/rpa_provenance_ok")
        output_dir.mkdir(parents=True, exist_ok=True)
        orders = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}])
        orders.to_csv(output_dir / "orders.csv", index=False)
        config = {
            "paths": {"output_dir": str(output_dir), "cache_dir": "data/cache", "state_db": "data/state.sqlite3"},
            "strategy": {"name": "test_strategy"},
            "trading": {"lot_size": 100},
            "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000},
        }
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                *self.execution_epoch_rows(config),
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "strategy_name", "value": "test_strategy"},
                {"item": "config_sha256", "value": mapping_sha256(config)},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
                {"item": "orders_sha256", "value": orders_frame_sha256(orders)},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)

        path = export_rpa_orders(config)
        self.assertTrue(path.exists())
        self.assertTrue((output_dir / "rpa_pre_trade_checks.csv").exists())

    def test_export_blocks_all_tickets_when_strategy_health_is_paused(self):
        output_dir = Path("test_outputs/rpa_strategy_paused")
        output_dir.mkdir(parents=True, exist_ok=True)
        trade_date = date.today().isoformat()
        orders = pd.DataFrame([{"date": trade_date, "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}])
        orders.to_csv(output_dir / "orders.csv", index=False)
        config = {
            "paths": {"output_dir": str(output_dir), "cache_dir": str(output_dir / "cache"), "state_db": str(output_dir / "state.sqlite3")},
            "strategy": {"name": "test_strategy"},
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 100000,
                "max_daily_order_amount": 500000,
                "require_strategy_health": True,
                "export_ready_only": True,
            },
        }
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame([{"symbol": "600000", "name": "test", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False}]),
        )
        store.write_frame(
            "prices",
            pd.DataFrame([{
                "date": trade_date, "symbol": "600000", "open": 10.0, "high": 10.2, "low": 9.8,
                "close": 10.0, "volume": 1000000, "amount": 10000000,
            }]),
        )
        pd.DataFrame([
            {"item": "source", "value": "paper"},
            *self.execution_epoch_rows(config),
            {"item": "allow_weak_strategy", "value": "False"},
            {"item": "strategy_name", "value": "test_strategy"},
            {"item": "config_sha256", "value": mapping_sha256(config)},
            {"item": "execution_date", "value": trade_date},
            {"item": "order_count", "value": "1"},
            {"item": "orders_sha256", "value": orders_frame_sha256(orders)},
        ]).to_csv(output_dir / "orders_provenance.csv", index=False)
        pd.DataFrame([
            {"check": "paper_orders_allowed", "passed": False, "value": False},
        ]).to_csv(output_dir / "strategy_health.csv", index=False)

        path = export_rpa_orders(config)
        executable = pd.read_csv(path, dtype={"symbol": str})
        all_tickets = pd.read_csv(output_dir / "rpa_orders_all.csv", dtype={"symbol": str})
        blocked = pd.read_csv(output_dir / "rpa_blocked_orders.csv", dtype={"symbol": str})

        self.assertTrue(executable.empty)
        self.assertEqual(all_tickets["status"].tolist(), ["blocked"])
        self.assertEqual(all_tickets["message"].tolist(), ["strategy_health_paused"])
        self.assertEqual(len(blocked), 1)

    def test_export_writes_ready_only_file_and_blocked_audit(self):
        output_dir = Path("test_outputs/rpa_ready_only_export")
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = output_dir / "cache"
        trade_date = date.today().isoformat()
        orders = pd.DataFrame(
            [
                {"date": trade_date, "symbol": "600000", "side": "sell", "quantity": 100, "reason": "rebalance"},
                {"date": trade_date, "symbol": "600000", "side": "buy", "quantity": 3000, "reason": "rebalance"},
            ]
        )
        orders.to_csv(output_dir / "orders.csv", index=False)
        config = {
            "paths": {"output_dir": str(output_dir), "cache_dir": str(cache_dir), "state_db": str(output_dir / "state.sqlite3")},
            "strategy": {"name": "test_strategy"},
            "trading": {"lot_size": 100},
            "rpa": {
                "max_single_order_amount": 20000,
                "max_daily_order_amount": 500000,
                "require_manual_final_confirm": True,
                "never_click_submit": True,
                "export_ready_only": True,
            },
        }
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame(
                [{"symbol": "600000", "name": "test", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False}]
            ),
        )
        store.write_frame(
            "prices",
            pd.DataFrame(
                [
                    {
                        "date": trade_date,
                        "symbol": "600000",
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.8,
                        "close": 10.0,
                        "volume": 1000000,
                        "amount": 10000000,
                    }
                ]
            ),
        )
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                *self.execution_epoch_rows(config),
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "strategy_name", "value": "test_strategy"},
                {"item": "config_sha256", "value": mapping_sha256(config)},
                {"item": "execution_date", "value": trade_date},
                {"item": "order_count", "value": "2"},
                {"item": "orders_sha256", "value": orders_frame_sha256(orders)},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)

        path = export_rpa_orders(config)
        executable = pd.read_csv(path, dtype={"symbol": str})
        all_tickets = pd.read_csv(output_dir / "rpa_orders_all.csv", dtype={"symbol": str})
        blocked = pd.read_csv(output_dir / "rpa_blocked_orders.csv", dtype={"symbol": str})
        checks = pd.read_csv(output_dir / "rpa_pre_trade_checks.csv")
        failed = set(checks.loc[~checks["passed"].astype(bool), "check"])

        self.assertEqual(len(all_tickets), 2)
        self.assertEqual(len(executable), 1)
        self.assertEqual(executable["status"].iloc[0], "ready")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked["message"].iloc[0], "exceeds_single_order_amount_limit")
        self.assertNotIn("orders_provenance_count", failed)
        self.assertNotIn("blocked_ticket_count", failed)

    def test_export_rejects_modified_orders_after_provenance_written(self):
        output_dir = Path("test_outputs/rpa_provenance_modified")
        output_dir.mkdir(parents=True, exist_ok=True)
        original = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}])
        original.to_csv(output_dir / "orders.csv", index=False)
        config = {
            "paths": {"output_dir": str(output_dir), "cache_dir": "data/cache", "state_db": "data/state.sqlite3"},
            "strategy": {"name": "test_strategy"},
            "trading": {"lot_size": 100},
            "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000},
        }
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                *self.execution_epoch_rows(config),
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "strategy_name", "value": "test_strategy"},
                {"item": "config_sha256", "value": mapping_sha256(config)},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
                {"item": "orders_sha256", "value": orders_frame_sha256(original)},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)
        modified = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 200, "reason": "rebalance"}])
        modified.to_csv(output_dir / "orders.csv", index=False)

        with self.assertRaisesRegex(RuntimeError, "哈希不一致"):
            export_rpa_orders(config)

    def test_export_rejects_stale_config_or_strategy_provenance(self):
        output_dir = Path("test_outputs/rpa_provenance_stale_config")
        output_dir.mkdir(parents=True, exist_ok=True)
        orders = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}])
        orders.to_csv(output_dir / "orders.csv", index=False)
        old_config = {
            "paths": {"output_dir": str(output_dir), "cache_dir": "data/cache", "state_db": "data/state.sqlite3"},
            "strategy": {"name": "old_strategy"},
            "trading": {"lot_size": 100},
            "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000},
        }
        current_config = {
            "paths": {"output_dir": str(output_dir), "cache_dir": "data/cache", "state_db": "data/state.sqlite3"},
            "strategy": {"name": "new_strategy"},
            "trading": {"lot_size": 100},
            "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000},
        }
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "strategy_name", "value": "old_strategy"},
                {"item": "config_sha256", "value": mapping_sha256(old_config)},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
                {"item": "orders_sha256", "value": orders_frame_sha256(orders)},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)

        with self.assertRaisesRegex(RuntimeError, "当前配置"):
            export_rpa_orders(current_config)

        current_config_hash_only_changed = dict(old_config)
        current_config_hash_only_changed["rpa"] = {"max_single_order_amount": 90000, "max_daily_order_amount": 500000}
        with self.assertRaisesRegex(RuntimeError, "当前配置"):
            export_rpa_orders(current_config_hash_only_changed)

    def test_export_rejects_weak_strategy_provenance(self):
        output_dir = Path("test_outputs/rpa_provenance_weak")
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}]).to_csv(
            output_dir / "orders.csv", index=False
        )
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                {"item": "allow_weak_strategy", "value": "True"},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)
        config = {
            "paths": {"output_dir": str(output_dir), "cache_dir": "data/cache", "state_db": "data/state.sqlite3"},
            "trading": {"lot_size": 100},
            "rpa": {"max_single_order_amount": 100000, "max_daily_order_amount": 500000},
        }

        with self.assertRaisesRegex(RuntimeError, "弱门禁"):
            export_rpa_orders(config)


if __name__ == "__main__":
    unittest.main()
