from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from ashare_quant.huatai_canary import (
    BrokerSubmission,
    aggregate_trade_rows,
    consume_desktop_canary_arm,
    desktop_canary_certification_status,
    CanaryOrder,
    match_order_row,
    parse_order_grid_text,
    parse_trade_grid_text,
    submit_desktop_canary,
    validate_canary_orders,
)


class FakeDriver:
    def __init__(self, result=None, error=None):
        self.result = result or BrokerSubmission("submitted", "broker-1", "accepted")
        self.error = error
        self.calls = []

    def submit(self, order, *, run_id):
        self.calls.append((order, run_id))
        if self.error:
            raise self.error
        return self.result


class HuataiCanaryTests(unittest.TestCase):
    def config(self, **overrides):
        policy = {"enabled": True, "allow_submit": True, "max_orders": 1, "max_order_amount": 1000}
        policy.update(overrides)
        return {"huatai": {"desktop_canary": policy}}

    def order(self, **overrides):
        value = {"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 9.99}
        value.update(overrides)
        return value

    def test_hard_limits_cannot_be_relaxed_by_config(self):
        with self.assertRaisesRegex(ValueError, "hard limit"):
            validate_canary_orders([self.order(limit_price=10.01)], self.config(max_order_amount=999999))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_canary_orders([self.order(), self.order(ticket_id="t2")], self.config(max_orders=99))

    def test_buy_lot_and_switches_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "100-share"):
            validate_canary_orders([self.order(quantity=99)], self.config())
        request = {"run_id": "run-1", "orders": [self.order()]}
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            submit_desktop_canary(request, self.config(allow_submit=False), FakeDriver())

    def test_submission_requires_broker_order_id_or_becomes_unknown(self):
        request = {"run_id": "run-1", "orders": [self.order()]}
        driver = FakeDriver(BrokerSubmission("submitted", "", "dialog closed"))
        result = submit_desktop_canary(request, self.config(), driver)
        self.assertEqual(result[0]["status"], "unknown")
        self.assertEqual(len(driver.calls), 1)

    def test_driver_exception_is_unknown_to_prevent_retry(self):
        request = {"run_id": "run-1", "orders": [self.order()]}
        result = submit_desktop_canary(request, self.config(), FakeDriver(error=TimeoutError("no acknowledgement")))
        self.assertEqual(result[0]["status"], "unknown")
        self.assertIn("TimeoutError", result[0]["message"])

    def test_parses_and_matches_clipboard_order_grid(self):
        text = (
            "合同编号\t证券代码\t证券名称\t买卖标志\t委托数量\t委托价格\t成交数量\t成交均价\t状态说明\n"
            "A123\t600000\t浦发银行\t买入\t100\t9.99\t0\t0\t已报\n"
        )
        rows = parse_order_grid_text(text)
        self.assertEqual(rows[0]["broker_order_id"], "A123")
        self.assertEqual(rows[0]["broker_status"], "accepted")
        order = CanaryOrder("t1", "600000", "buy", 100, Decimal("9.99"))
        self.assertEqual(match_order_row(rows, order)["broker_order_id"], "A123")

    def test_unknown_headers_and_ambiguous_rows_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "headers"):
            parse_order_grid_text("foo\tbar\n1\t2")
        text = (
            "合同编号\t证券代码\t买卖标志\t委托数量\t委托价格\t状态说明\n"
            "A1\t600000\t买入\t100\t9.99\t已报\n"
            "A2\t600000\t买入\t100\t9.99\t已报\n"
        )
        rows = parse_order_grid_text(text)
        order = CanaryOrder("t1", "600000", "buy", 100, Decimal("9.99"))
        with self.assertRaisesRegex(RuntimeError, "multiple"):
            match_order_row(rows, order)

        duplicate_headers = (
            "合同编号\t证券代码\t股票代码\t买卖标志\t委托数量\t委托价格\t状态说明\n"
            "A1\t600000\t600001\t买入\t100\t9.99\t已报\n"
        )
        with self.assertRaisesRegex(ValueError, "duplicate canonical headers"):
            parse_order_grid_text(duplicate_headers)

    def test_order_grid_rejects_inconsistent_status_and_fill_quantity(self):
        filled_without_quantity = (
            "合同编号\t证券代码\t买卖标志\t委托数量\t委托价格\t成交数量\t状态说明\n"
            "A1\t600000\t买入\t100\t9.99\t0\t已成\n"
        )
        with self.assertRaisesRegex(ValueError, "full filled quantity"):
            parse_order_grid_text(filled_without_quantity)

        partial_without_fill = (
            "合同编号\t证券代码\t买卖标志\t委托数量\t委托价格\t成交数量\t状态说明\n"
            "A1\t600000\t买入\t100\t9.99\t0\t部成\n"
        )
        with self.assertRaisesRegex(ValueError, "inconsistent filled quantity"):
            parse_order_grid_text(partial_without_fill)

        overfilled = (
            "合同编号\t证券代码\t买卖标志\t委托数量\t委托价格\t成交数量\t状态说明\n"
            "A1\t600000\t买入\t100\t9.99\t101\t已成\n"
        )
        with self.assertRaisesRegex(ValueError, "outside the order quantity"):
            parse_order_grid_text(overfilled)

    def test_trade_aggregation_requires_unique_nonempty_trade_ids(self):
        rows = [
            {"broker_order_id": "A123", "trade_id": "", "symbol": "600000", "side": "buy",
             "filled_quantity": 100, "filled_amount": 1000.0, "fee": 5.0, "fee_status": "exact"},
        ]
        with self.assertRaisesRegex(RuntimeError, "missing Huatai trade id"):
            aggregate_trade_rows(rows, "A123")


    def test_trade_grid_aggregates_multiple_fills_and_exact_total_fee(self):
        text = (
            "\u5408\u540c\u7f16\u53f7\t\u6210\u4ea4\u7f16\u53f7\t\u8bc1\u5238\u4ee3\u7801\t\u4e70\u5356\u6807\u5fd7\t\u6210\u4ea4\u6570\u91cf\t\u6210\u4ea4\u4ef7\u683c\t\u6210\u4ea4\u91d1\u989d\t\u8d39\u7528\u5408\u8ba1\t\u6210\u4ea4\u65e5\u671f\n"
            "A123\tT1\t600000\t\u4e70\u5165\t40\t10.00\t400.00\t2.00\t2026-07-15\n"
            "A123\tT2\t600000\t\u4e70\u5165\t60\t10.10\t606.00\t3.00\t2026-07-15\n"
        )
        aggregate = aggregate_trade_rows(parse_trade_grid_text(text), "A123")
        self.assertEqual(aggregate["filled_quantity"], 100)
        self.assertAlmostEqual(aggregate["filled_amount"], 1006.0)
        self.assertAlmostEqual(aggregate["avg_fill_price"], 10.06)
        self.assertEqual(aggregate["fee"], 5.0)
        self.assertEqual(aggregate["fee_status"], "exact")
        self.assertEqual(aggregate["trade_date"], "2026-07-15")

    def test_occurrence_amount_is_not_treated_as_gross_trade_amount(self):
        text = (
            "\u5408\u540c\u7f16\u53f7\t\u6210\u4ea4\u7f16\u53f7\t\u8bc1\u5238\u4ee3\u7801\t\u4e70\u5356\u6807\u5fd7\t\u6210\u4ea4\u6570\u91cf\t\u6210\u4ea4\u4ef7\u683c\t\u53d1\u751f\u91d1\u989d\n"
            "A123\tT1\t600000\t\u4e70\u5165\t100\t10.00\t1005.00\n"
        )
        row = parse_trade_grid_text(text)[0]
        self.assertEqual(row["filled_amount"], 1000.0)
        self.assertEqual(row["fee_status"], "unavailable")

    def test_trade_grid_component_fee_without_total_is_unavailable(self):
        text = (
            "\u5408\u540c\u7f16\u53f7\t\u6210\u4ea4\u7f16\u53f7\t\u8bc1\u5238\u4ee3\u7801\t\u4e70\u5356\u6807\u5fd7\t\u6210\u4ea4\u6570\u91cf\t\u6210\u4ea4\u4ef7\u683c\t\u4f63\u91d1\n"
            "A123\tT1\t600000\t\u4e70\u5165\t100\t10.00\t5.00\n"
        )
        aggregate = aggregate_trade_rows(parse_trade_grid_text(text), "A123")
        self.assertEqual(aggregate["fee"], 0.0)
        self.assertEqual(aggregate["fee_status"], "unavailable")

    def test_certification_is_bound_complete_and_unexpired(self):
        path = Path.cwd() / "test_outputs" / "huatai_canary_certification.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
        payload = {
            "schema_version": "ashare_quant.huatai_desktop_canary_certification.v1",
            "status": "certified",
            "auto_live_config_sha256": "config-a",
            "submit_form_preview": True,
            "empty_order_monitor": True,
            "account_snapshot_live": True,
            "operator_confirmation_required": True,
            "max_orders": 1,
            "max_order_amount": 1000,
            "submit_contract": True,
            "query_contract": True,
            "cancel_contract": True,
            "nonempty_order_parser": True,
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        config = self.config(certification_path=str(path))
        self.assertEqual(
            desktop_canary_certification_status(config, expected_config_sha256="config-a", now=now),
            (True, "certified"),
        )
        passed, reason = desktop_canary_certification_status(
            config, expected_config_sha256="config-b", now=now
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "desktop_canary_certification_config_mismatch")
        payload["cancel_contract"] = False
        path.write_text(json.dumps(payload), encoding="utf-8")
        passed, reason = desktop_canary_certification_status(
            config, expected_config_sha256="config-a", now=now
        )
        self.assertFalse(passed)
        self.assertIn("certification_incomplete", reason)


    def test_armed_stage_breaks_first_canary_deadlock_without_claiming_full_certification(self):
        path = Path.cwd() / "test_outputs" / "huatai_canary_armed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
        payload = {
            "schema_version": "ashare_quant.huatai_desktop_canary_certification.v1",
            "status": "armed",
            "auto_live_config_sha256": "config-a",
            "submit_form_preview": True,
            "empty_order_monitor": True,
            "account_snapshot_live": True,
            "operator_confirmation_required": True,
            "max_orders": 1,
            "max_order_amount": 1000,
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        config = self.config(
            certification_path=str(path),
            required_certification_stage="armed",
        )
        self.assertEqual(
            desktop_canary_certification_status(config, expected_config_sha256="config-a", now=now),
            (True, "armed"),
        )
        passed, reason = desktop_canary_certification_status(
            config,
            expected_config_sha256="config-a",
            now=now,
            required_stage="certified",
        )
        self.assertFalse(passed)
        self.assertEqual(reason, "desktop_canary_full_lifecycle_not_certified")


    def test_armed_certificate_is_atomically_consumed_once_and_stays_queryable(self):
        path = Path.cwd() / "test_outputs" / "huatai_canary_consume.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
        payload = {
            "schema_version": "ashare_quant.huatai_desktop_canary_certification.v1",
            "status": "armed",
            "auto_live_config_sha256": "config-a",
            "submit_form_preview": True,
            "empty_order_monitor": True,
            "account_snapshot_live": True,
            "operator_confirmation_required": True,
            "max_orders": 1,
            "max_order_amount": 1000,
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        config = self.config(
            certification_path=str(path),
            required_certification_stage="armed",
        )
        orders = [self.order()]
        consumed, reason = consume_desktop_canary_arm(
            config,
            expected_config_sha256="config-a",
            run_id="run-1",
            orders=orders,
            now=now,
        )
        self.assertTrue(consumed)
        self.assertEqual(reason, "consumed_armed")
        second, second_reason = consume_desktop_canary_arm(
            config,
            expected_config_sha256="config-a",
            run_id="run-2",
            orders=orders,
            now=now + timedelta(seconds=1),
        )
        self.assertFalse(second)
        self.assertEqual(second_reason, "desktop_canary_arm_already_consumed")
        queryable, stage = desktop_canary_certification_status(
            config,
            expected_config_sha256="config-a",
            now=now + timedelta(seconds=1),
            required_stage="armed",
            allow_consumed=True,
        )
        self.assertTrue(queryable)
        self.assertEqual(stage, "consumed_armed")
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["consumed_run_id"], "run-1")
        self.assertEqual(len(persisted["consumed_orders_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
