from pathlib import Path
import unittest
from unittest.mock import Mock

import pandas as pd

from ashare_quant.huatai_notice import (
    NAVIGATION_NAME,
    NO_NEW_MESSAGE_NAME,
    TODAY_ONLY_NAME,
    HuataiDesktopNoticeDriver,
    NoticeNotPresent,
    dismiss_huatai_notice,
)


class FakeDriver:
    def __init__(self, *, closed: bool = True) -> None:
        self.closed = closed
        self.close_count = 0
        self.wait_calls: list[tuple[float, float]] = []

    def close_once(self) -> None:
        self.close_count += 1

    def wait_closed(self, timeout_seconds: float, poll_seconds: float) -> bool:
        self.wait_calls.append((timeout_seconds, poll_seconds))
        return self.closed


class ElementInfo:
    def __init__(self, name: str, class_name: str) -> None:
        self.name = name
        self.class_name = class_name


class FakeControl:
    def __init__(self, name: str = "", class_name: str = "") -> None:
        self.element_info = ElementInfo(name, class_name)

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True


class FakeWindow:
    def __init__(self, controls: list[FakeControl]) -> None:
        self._controls = controls

    def descendants(self) -> list[FakeControl]:
        return self._controls


class HuataiNoticeTests(unittest.TestCase):
    def config(self, name: str, *, enabled: bool = True) -> dict:
        root = Path("test_outputs") / name
        root.mkdir(parents=True, exist_ok=True)
        return {
            "paths": {"output_dir": str(root)},
            "desktop_notice": {
                "enabled": enabled,
                "confirmation_timeout_seconds": 2,
                "confirmation_poll_seconds": 0.2,
            },
        }

    def test_notice_names_use_exact_unicode_contract(self):
        self.assertEqual(NAVIGATION_NAME, "\u5bfc\u822a")
        self.assertEqual(NO_NEW_MESSAGE_NAME, "\u6ca1\u6709\u65b0\u6d88\u606f\uff0c\u4e0b\u6b21\u4e0d\u518d\u663e\u793a")
        self.assertEqual(TODAY_ONLY_NAME, "\u4eca\u65e5\u4e0d\u518d\u63d0\u793a")

    def test_disabled_notice_never_discovers_or_closes_window(self):
        factory = Mock()

        result = dismiss_huatai_notice(self.config(self._testMethodName, enabled=False), driver_factory=factory)

        self.assertEqual(result.status, "disabled")
        self.assertFalse(result.attempted)
        factory.assert_not_called()

    def test_notice_absence_is_normal_and_does_not_attempt_close(self):
        def absent(_: dict):
            raise NoticeNotPresent("not present")

        result = dismiss_huatai_notice(self.config(self._testMethodName), driver_factory=absent)

        self.assertEqual(result.status, "not_present")
        self.assertFalse(result.detected)
        self.assertEqual(result.close_count, 0)

    def test_unique_notice_is_closed_exactly_once_and_confirmed(self):
        driver = FakeDriver()

        result = dismiss_huatai_notice(
            self.config(self._testMethodName), driver_factory=lambda _: driver
        )

        self.assertEqual(result.status, "dismissed")
        self.assertTrue(result.dismissal_confirmed)
        self.assertEqual(result.close_count, 1)
        self.assertEqual(driver.close_count, 1)
        self.assertEqual(driver.wait_calls, [(2.0, 0.2)])

    def test_unconfirmed_close_blocks_without_retry(self):
        driver = FakeDriver(closed=False)

        result = dismiss_huatai_notice(
            self.config(self._testMethodName), driver_factory=lambda _: driver
        )

        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.attempted)
        self.assertEqual(result.close_count, 1)
        self.assertEqual(driver.close_count, 1)

    def test_audit_excludes_notice_body_and_account_fields(self):
        result = dismiss_huatai_notice(
            self.config(self._testMethodName), driver_factory=lambda _: FakeDriver()
        )

        audit = pd.read_csv(result.audit_path)
        text = audit.to_csv(index=False)
        self.assertNotIn("account_id", text)
        self.assertNotIn("trading_password", text)
        self.assertNotIn("公告[", text)

    def test_contract_requires_all_four_unique_markers(self):
        controls = [
            FakeControl(NAVIGATION_NAME, "Static"),
            FakeControl(NO_NEW_MESSAGE_NAME, "Button"),
            FakeControl(TODAY_ONLY_NAME, "Button"),
            FakeControl("Custom1", "CCustomTabCtrl"),
        ]
        self.assertTrue(HuataiDesktopNoticeDriver._matches_notice_contract(FakeWindow(controls)))
        self.assertFalse(HuataiDesktopNoticeDriver._matches_notice_contract(FakeWindow(controls[:-1])))
        self.assertFalse(
            HuataiDesktopNoticeDriver._matches_notice_contract(
                FakeWindow(controls + [FakeControl(NAVIGATION_NAME, "Static")])
            )
        )


if __name__ == "__main__":
    unittest.main()
