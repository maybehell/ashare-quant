from decimal import Decimal
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from ashare_quant.huatai_canary import CanaryOrder
from ashare_quant.huatai_desktop_live import CANCEL_PAGE, ORDER_PAGE, TRADE_PAGE, HuataiDesktopCanaryDriver
from ashare_quant.huatai_desktop_rpa import BUY_PAGE, READ_ONLY_PAGES, SAFE_PAGE, SELL_PAGE


class FakeButton:
    def __init__(self):
        self.clicks = 0

    def click_input(self):
        self.clicks += 1


class FakeUi:
    def __init__(self):
        self.values = {"symbol": "", "price": "", "quantity": ""}
        self.pages = []

    def navigate(self, page):
        self.pages.append(page)

    def assert_form_contract(self, side):
        self.side = side

    def write_field(self, role, value):
        self.values[role] = value

    def read_field(self, role):
        return self.values[role]

    def visible_grids(self):
        return [object()]


class FakeLiveDriver(HuataiDesktopCanaryDriver):
    def __init__(self, row=None, stop_path=None):
        self.driver = FakeUi()
        self.emergency_stop_path = Path(stop_path) if stop_path else None
        self.submit_button = FakeButton()
        self.confirm_button = FakeButton()
        self.row = row

    def _button(self, automation_id):
        return self.submit_button

    def _find_order_confirmation(self, order, *, timeout_seconds):
        return self.confirm_button

    def _wait_for_order_row(self, order, *, timeout_seconds):
        return self.row

    def _read_order_rows(self, page):
        return []


class HuataiDesktopLiveTests(unittest.TestCase):
    def order(self):
        return CanaryOrder("t1", "600000", "buy", 100, Decimal("9.99"))

    def test_submit_requires_readback_then_returns_broker_id(self):
        row = {
            "broker_order_id": "A123",
            "broker_status": "accepted",
            "raw_status": "已报",
        }
        driver = FakeLiveDriver(row)
        result = driver.submit(self.order(), run_id="run-1")
        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.broker_order_id, "A123")
        self.assertEqual(driver.submit_button.clicks, 1)
        self.assertEqual(driver.confirm_button.clicks, 1)
        self.assertEqual(driver.driver.pages[-1], SAFE_PAGE)

    def test_runtime_selftest_uses_canonical_read_only_pages(self):
        self.assertIn(ORDER_PAGE, READ_ONLY_PAGES)
        self.assertIn(TRADE_PAGE, READ_ONLY_PAGES)
        self.assertIn(CANCEL_PAGE, READ_ONLY_PAGES)
        driver = FakeLiveDriver(None)

        passed, message = driver.runtime_selftest()

        self.assertTrue(passed, message)
        self.assertEqual(
            driver.driver.pages,
            [BUY_PAGE, SELL_PAGE, ORDER_PAGE, TRADE_PAGE, CANCEL_PAGE, SAFE_PAGE],
        )

    def test_query_merges_exact_trade_totals(self):
        driver = FakeLiveDriver(None)
        driver._read_order_rows = lambda page: [{
            "broker_order_id": "A123", "broker_status": "filled", "raw_status": "\u5df2\u6210",
            "symbol": "600000", "side": "buy", "filled_quantity": 100, "avg_fill_price": 10.05,
        }]
        driver._read_trade_rows = lambda: [
            {"broker_order_id": "A123", "trade_id": "T1", "symbol": "600000", "side": "buy", "filled_quantity": 40, "filled_amount": 400.0, "fee": 2.0, "fee_status": "exact", "trade_date": "2026-07-15"},
            {"broker_order_id": "A123", "trade_id": "T2", "symbol": "600000", "side": "buy", "filled_quantity": 60, "filled_amount": 606.0, "fee": 3.0, "fee_status": "exact", "trade_date": "2026-07-15"},
        ]
        result = driver.query({"broker_order_id": "A123"})
        self.assertEqual(result["filled_quantity"], 100)
        self.assertEqual(result["filled_amount"], 1006.0)
        self.assertEqual(result["fee"], 5.0)
        self.assertEqual(result["fee_status"], "exact")
    def test_query_rejects_broker_id_with_different_order_identity(self):
        driver = FakeLiveDriver(None)
        driver._read_order_rows = lambda page: [{
            "broker_order_id": "A123",
            "broker_status": "accepted",
            "raw_status": "已报",
            "symbol": "600001",
            "side": "buy",
            "quantity": 100,
            "limit_price": 9.99,
            "filled_quantity": 0,
        }]

        with self.assertRaisesRegex(RuntimeError, "identity mismatch: symbol"):
            driver.query({
                "broker_order_id": "A123",
                "symbol": "600000",
                "side": "buy",
                "quantity": 100,
                "limit_price": 9.99,
            })

    def test_cancel_rejects_broker_id_with_different_order_identity_before_click(self):
        driver = FakeLiveDriver(None)
        driver._read_rows_and_grid = lambda page, keep_page: ([{
            "broker_order_id": "A123",
            "symbol": "600001",
            "side": "buy",
            "quantity": 100,
            "limit_price": 9.99,
        }], object())

        with self.assertRaisesRegex(RuntimeError, "identity mismatch: symbol"):
            driver.cancel({
                "broker_order_id": "A123",
                "symbol": "600000",
                "side": "buy",
                "quantity": 100,
                "limit_price": 9.99,
            })


    def test_query_rejects_order_trade_quantity_mismatch(self):
        driver = FakeLiveDriver(None)
        driver._read_order_rows = lambda page: [{
            "broker_order_id": "A123", "broker_status": "partially_filled", "raw_status": "\u90e8\u6210",
            "symbol": "600000", "side": "buy", "filled_quantity": 100, "avg_fill_price": 10.0,
        }]
        driver._read_trade_rows = lambda: [
            {"broker_order_id": "A123", "trade_id": "T1", "symbol": "600000", "side": "buy", "filled_quantity": 90, "filled_amount": 900.0, "fee": 5.0, "fee_status": "exact", "trade_date": "2026-07-15"},
        ]
        with self.assertRaisesRegex(RuntimeError, "cumulative filled quantity"):
            driver.query({"broker_order_id": "A123"})

    def test_missing_order_row_after_click_is_unknown(self):
        driver = FakeLiveDriver(None)
        result = driver.submit(self.order(), run_id="run-1")
        self.assertEqual(result.status, "unknown")
        self.assertEqual(driver.submit_button.clicks, 1)

    def test_emergency_stop_race_blocks_before_submit_click(self):
        stop = Path.cwd() / "test_outputs" / "huatai_live.stop"
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("active", encoding="utf-8")
        driver = FakeLiveDriver(None, stop)
        with self.assertRaisesRegex(RuntimeError, "emergency stop"):
            driver.submit(self.order(), run_id="run-1")
        self.assertEqual(driver.submit_button.clicks, 0)
        stop.unlink(missing_ok=True)

    def test_preexisting_order_blocks_before_form_or_submit_click(self):
        driver = FakeLiveDriver(None)
        driver._read_order_rows = lambda page: [{"broker_order_id": "OLD-1"}]

        with self.assertRaisesRegex(RuntimeError, "empty current-order grid"):
            driver.submit(self.order(), run_id="run-1")

        self.assertEqual(driver.submit_button.clicks, 0)
        self.assertNotIn(BUY_PAGE, driver.driver.pages)

    def test_grid_row_bands_are_detected_for_cancel_selection(self):
        image = Image.new("RGB", (600, 220), "white")
        draw = ImageDraw.Draw(image)
        draw.line((0, 37, 599, 37), fill="black")
        draw.rectangle((20, 60, 250, 70), fill="black")
        draw.rectangle((20, 88, 250, 98), fill="black")
        bands = HuataiDesktopCanaryDriver._grid_row_bands(image)
        self.assertEqual(bands, [(60, 70), (88, 98)])


if __name__ == "__main__":
    unittest.main()
