from decimal import Decimal
import unittest
from unittest.mock import patch

from ashare_quant.huatai_desktop_rpa import (
    BUY_PAGE,
    FORM_CONTROL_IDS,
    HuataiDesktopPreviewDriver,
    SAFE_PAGE,
    forbidden_control,
    run_form_preview,
    validate_preview_order,
)


class FakeDriver:
    def __init__(self):
        self.values = {"symbol": "", "price": "", "quantity": ""}
        self.calls = []

    def navigate(self, page):
        self.calls.append(("navigate", page))

    def assert_form_contract(self, side):
        self.calls.append(("assert_form_contract", side))

    def read_field(self, role):
        self.calls.append(("read", role))
        return self.values[role]

    def write_field(self, role, value):
        self.calls.append(("write", role, value))
        self.values[role] = value


class HuataiDesktopRpaTests(unittest.TestCase):
    def test_navigation_retries_transient_empty_accessibility_tree(self):
        class Info:
            control_type = "TreeItem"
            name = SAFE_PAGE

        class Control:
            element_info = Info()

            def __init__(self):
                self.clicked = False

            def click_input(self):
                self.clicked = True

        control = Control()

        class Root:
            def __init__(self):
                self.calls = 0
                self.focused = False

            def descendants(self):
                self.calls += 1
                return [] if self.calls < 3 else [control]

            def set_focus(self):
                self.focused = True

        driver = HuataiDesktopPreviewDriver.__new__(HuataiDesktopPreviewDriver)
        driver.root = Root()
        with patch("ashare_quant.huatai_desktop_rpa.time.sleep"):
            driver.navigate(SAFE_PAGE)

        self.assertEqual(driver.root.calls, 3)
        self.assertTrue(driver.root.focused)
        self.assertTrue(control.clicked)

    def test_field_discovery_retries_transient_missing_control(self):
        class Info:
            automation_id = FORM_CONTROL_IDS["symbol"]
            control_type = "Edit"

        class Control:
            element_info = Info()

        control = Control()
        driver = HuataiDesktopPreviewDriver.__new__(HuataiDesktopPreviewDriver)
        responses = [[], [], [control]]
        with (
            patch.object(driver, "_visible_controls", side_effect=responses) as visible,
            patch("ashare_quant.huatai_desktop_rpa.time.sleep") as sleep,
        ):
            result = driver._field("symbol")

        self.assertIs(result, control)
        self.assertEqual(visible.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_validation_enforces_buy_lot(self):
        order = validate_preview_order("600000", "buy", "1.00", "100")
        self.assertEqual(order.price, Decimal("1.00"))
        with self.assertRaisesRegex(ValueError, "100-share"):
            validate_preview_order("600000", "buy", "1.00", "99")

    def test_submit_and_cancel_controls_are_forbidden(self):
        for identifier in ["1006", "1099", "30001", "30002", "30003", "2053", "30022"]:
            self.assertTrue(forbidden_control(identifier))
        self.assertFalse(forbidden_control(FORM_CONTROL_IDS["symbol"]))

    def test_preview_writes_reads_restores_and_never_submits(self):
        driver = FakeDriver()
        order = validate_preview_order("600000", "buy", "1.00", "100")
        result = run_form_preview(driver, order, pause=lambda _seconds: None)
        self.assertEqual(driver.calls[0], ("navigate", BUY_PAGE))
        self.assertEqual(driver.calls[-1], ("navigate", SAFE_PAGE))
        self.assertEqual(driver.values, {"symbol": "", "price": "", "quantity": ""})
        self.assertFalse(result["submit_invoked"])
        self.assertFalse(result["cancel_invoked"])
        self.assertTrue(result["form_restore_verified"])
        self.assertFalse(any(call[0] in {"submit", "cancel", "click_button"} for call in driver.calls))

    def test_readback_mismatch_fails_and_still_restores(self):
        class BadDriver(FakeDriver):
            def read_field(self, role):
                value = super().read_field(role)
                return "999999" if role == "symbol" and value else value

        driver = BadDriver()
        order = validate_preview_order("600000", "buy", "1.00", "100")
        with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
            run_form_preview(driver, order, pause=lambda _seconds: None)
        self.assertEqual(driver.values, {"symbol": "", "price": "", "quantity": ""})
        self.assertEqual(driver.calls[-1], ("navigate", SAFE_PAGE))

    def test_restore_mismatch_fails_and_returns_to_safe_page(self):
        class FailedRestoreDriver(FakeDriver):
            def write_field(self, role, value):
                if role == "quantity" and value == "" and self.values[role]:
                    self.calls.append(("write_ignored", role, value))
                    return
                super().write_field(role, value)

        driver = FailedRestoreDriver()
        order = validate_preview_order("600000", "buy", "1.00", "100")
        with self.assertRaisesRegex(RuntimeError, "restore mismatch"):
            run_form_preview(driver, order, pause=lambda _seconds: None)
        self.assertEqual(driver.calls[-1], ("navigate", SAFE_PAGE))


if __name__ == "__main__":
    unittest.main()
