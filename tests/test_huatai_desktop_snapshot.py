from decimal import Decimal
import importlib.util
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "huatai_desktop_snapshot.py"
SPEC = importlib.util.spec_from_file_location("huatai_desktop_snapshot", SCRIPT)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class HuataiDesktopSnapshotTests(unittest.TestCase):
    def test_verified_funds_navigation_uses_only_safe_page_and_returns_bound_root(self):
        root = object()
        class Driver:
            def __init__(self):
                self.root = root
                self.pages = []
            def navigate(self, page):
                self.pages.append(page)
        driver = Driver()
        selected = snapshot.navigate_to_verified_funds_root(lambda: driver)
        self.assertIs(selected, root)
        self.assertEqual(driver.pages, ["资金股票"])
    def test_verified_funds_navigation_rejects_driver_without_bound_root(self):
        class Driver:
            def navigate(self, _page):
                return None
        with self.assertRaisesRegex(RuntimeError, "did not return a trading root"):
            snapshot.navigate_to_verified_funds_root(Driver)
    def test_clipboard_text_accepts_gbk_cf_text(self):
        class Clipboard:
            CF_UNICODETEXT = 13
            CF_TEXT = 1
            CF_OEMTEXT = 7

            @staticmethod
            def IsClipboardFormatAvailable(format_id):
                return format_id == Clipboard.CF_TEXT

            @staticmethod
            def GetClipboardData(format_id):
                return "证券代码\t股票余额\n600000\t100\n".encode("gb18030")

            @staticmethod
            def EnumClipboardFormats(format_id):
                return 0

            @staticmethod
            def GetClipboardFormatName(format_id):
                return ""

        text = snapshot.clipboard_text(Clipboard)
        self.assertIn("600000", text)
        self.assertIn("证券代码", text)

    def test_clipboard_text_rejects_stale_or_non_text_formats(self):
        class Clipboard:
            CF_UNICODETEXT = 13
            CF_TEXT = 1
            CF_OEMTEXT = 7

            @staticmethod
            def IsClipboardFormatAvailable(format_id):
                return False

            @staticmethod
            def EnumClipboardFormats(format_id):
                return 49161 if format_id == 0 else 0

            @staticmethod
            def GetClipboardFormatName(format_id):
                return "HTML Format"

        with self.assertRaisesRegex(RuntimeError, "formats=49161:HTML Format"):
            snapshot.clipboard_text(Clipboard)

    def test_parse_amount(self):
        self.assertEqual(snapshot.parse_amount("10,563.17"), Decimal("10563.17"))

    def test_normalize_header_removes_parenthesized_units(self):
        self.assertEqual(snapshot.normalize_header("成本价(元)"), "成本价")
        self.assertEqual(snapshot.normalize_header(" 持仓数量（股） "), "持仓数量")

    def test_account_summary_layout_binds_labels_to_control_ids(self):
        entries = [
            ("冻结金额", ""), ("可用金额", ""), ("0.00", "1013"), ("2567.44", "1016"),
            ("可取金额", "3004"), ("股票市值", "1335"), ("总 资 产", ""),
            ("0.00", "1017"), ("0.00", "1014"), ("2567.44", "1015"),
            ("买入未成", "3012"), ("0.00", "3401"), ("可用金额", "help-label"),
        ]

        evidence = snapshot.validate_account_summary_layout(entries)

        self.assertEqual(evidence["available_cash"], {"label": "可用金额", "automation_id": "1016"})
        self.assertEqual(evidence["total_asset"], {"label": "总资产", "automation_id": "1015"})

    def test_account_summary_layout_rejects_sparse_labels_even_with_known_ids(self):
        entries = [
            ("0.00", "1017"), ("0.00", "1016"), ("-2.060", "1014"),
            ("0.00", "1013"), ("2567.44", "1015"), ("可用金额", "help-label"),
        ]

        with self.assertRaisesRegex(RuntimeError, "layout changed"):
            snapshot.validate_account_summary_layout(entries)
    def test_account_summary_layout_fails_closed_when_value_ids_swap(self):
        entries = [
            ("冻结金额", ""), ("可用金额", ""), ("0.00", "1016"), ("2567.44", "1013"),
            ("可取金额", "3004"), ("股票市值", "1335"), ("总资产", ""),
            ("0.00", "1017"), ("0.00", "1014"), ("2567.44", "1015"),
            ("买入未成", "3012"), ("0.00", "3401"),
        ]

        with self.assertRaisesRegex(RuntimeError, "layout changed"):
            snapshot.validate_account_summary_layout(entries)
    def test_parse_positions_grid_accepts_unit_annotated_headers(self):
        text = (
            "证券代码\t证券名称\t持仓数量(股)\t可卖股数（股）\t成本价格(元)\t当前价(元)\t最新市值(元)\n"
            "600000\t浦发银行\t100\t100\t10.10\t10.50\t1050.00\n"
        )
        positions = snapshot.parse_positions_grid_text(text)
        self.assertEqual(positions[0]["symbol"], "600000")
        self.assertEqual(positions[0]["sellable_quantity"], 100)

    def test_successful_snapshot_diagnostics_preserve_verified_desktop_state(self):
        diagnostics = snapshot.successful_snapshot_diagnostics(
            {"hexin.exe": [10], "xiadan.exe": [20, 21]},
            [object()],
            [object()],
        )

        self.assertEqual(diagnostics["stage"], "account_snapshot_ready")
        self.assertEqual(diagnostics["hexin_process_count"], 1)
        self.assertEqual(diagnostics["xiadan_process_count"], 2)
        self.assertEqual(diagnostics["visible_client_windows"], 1)
        self.assertEqual(diagnostics["trading_grid_count"], 1)
    def test_desktop_stage_is_actionable_and_fail_closed(self):
        self.assertEqual(
            snapshot.classify_desktop_stage(
                hexin_process_count=0,
                xiadan_process_count=0,
                visible_client_windows=0,
                trading_grid_count=0,
            ),
            "client_process_missing",
        )
        self.assertEqual(
            snapshot.classify_desktop_stage(
                hexin_process_count=1,
                xiadan_process_count=1,
                visible_client_windows=0,
                total_client_windows=0,
                trading_grid_count=0,
                xiadan_native_window_count=0,
            ),
            "client_background_process_only",
        )
        self.assertEqual(
            snapshot.classify_desktop_stage(
                hexin_process_count=1,
                xiadan_process_count=1,
                visible_client_windows=1,
                trading_grid_count=0,
            ),
            "trading_page_unavailable",
        )
        self.assertEqual(
            snapshot.classify_desktop_stage(
                hexin_process_count=1,
                xiadan_process_count=1,
                visible_client_windows=1,
                trading_grid_count=0,
                xiadan_native_window_count=4,
                xiadan_visible_window_count=0,
                xiadan_top_level_window_count=2,
            ),
            "trading_module_ui_not_loaded",
        )

    def test_background_process_failure_message_is_actionable(self):
        message = snapshot.desktop_failure_message(
            {"stage": "client_background_process_only"},
            RuntimeError("generic"),
        )
        self.assertIn("fully exit the stale client processes", message)

    def test_native_process_window_inventory_counts_only_target_process(self):
        class Win32Gui:
            @staticmethod
            def EnumWindows(callback, context):
                callback(10, context)
                callback(20, context)

            @staticmethod
            def EnumChildWindows(handle, callback, context):
                if handle == 10:
                    callback(11, context)

            @staticmethod
            def IsWindowVisible(handle):
                return handle == 11

            @staticmethod
            def GetParent(handle):
                return 10 if handle == 11 else 0

        class Win32Process:
            @staticmethod
            def GetWindowThreadProcessId(handle):
                return 1, 200 if handle in {10, 11} else 300

        inventory = snapshot.native_process_window_inventory(
            {200},
            win32gui_module=Win32Gui,
            win32process_module=Win32Process,
        )

        self.assertEqual(inventory["xiadan_native_window_count"], 2)
        self.assertEqual(inventory["xiadan_visible_window_count"], 1)
        self.assertEqual(inventory["xiadan_top_level_window_count"], 1)

    def test_diagnostics_preserve_process_counts_when_minimized_pattern_is_unsupported(self):
        class FakeWindow:
            def process_id(self):
                return 101

            def is_visible(self):
                return True

            def is_minimized(self):
                raise RuntimeError("NoPatternInterfaceError")

            def descendants(self):
                return []

        class FakeDesktop:
            def windows(self, visible_only=False):
                self.visible_only = visible_only
                return [FakeWindow()]

        fake_module = SimpleNamespace(Desktop=lambda backend: FakeDesktop())
        with (
            patch.object(
                snapshot,
                "process_ids_by_name",
                return_value={"hexin.exe": {101}, "xiadan.exe": {202}},
            ),
            patch.dict(sys.modules, {"pywinauto": fake_module}),
        ):
            diagnostics = snapshot.collect_desktop_diagnostics()

        self.assertEqual(diagnostics["hexin_process_count"], 1)
        self.assertEqual(diagnostics["xiadan_process_count"], 1)
        self.assertEqual(diagnostics["visible_client_windows"], 1)
        self.assertEqual(diagnostics["total_client_windows"], 1)
        self.assertEqual(diagnostics["stage"], "trading_module_ui_not_loaded")
        self.assertIn("NoPatternInterfaceError", diagnostics["diagnostic_error"])
    def test_parse_amount_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            snapshot.parse_amount("missing")
        self.assertEqual(
            snapshot.classify_desktop_stage(
                hexin_process_count=1,
                xiadan_process_count=1,
                visible_client_windows=0,
                total_client_windows=1,
                minimized_client_windows=1,
                trading_grid_count=0,
            ),
            "client_window_minimized",
        )
        self.assertEqual(
            snapshot.classify_desktop_stage(
                hexin_process_count=1,
                xiadan_process_count=1,
                visible_client_windows=0,
                total_client_windows=1,
                minimized_client_windows=0,
                trading_grid_count=0,
            ),
            "client_window_hidden",
        )

    def test_parse_positions_grid_text_supports_nonempty_live_account(self):
        text = (
            "证券代码\t证券名称\t股票余额\t可用余额\t成本价\t市价\t市值\n"
            "600000\t浦发银行\t100\t0\t10.10\t10.50\t1,050.00\n"
            "000001\t平安银行\t200\t200\t11.00\t12.00\t2,400.00\n"
            "\t合计\t\t\t\t\t3,450.00\n"
        )

        positions = snapshot.parse_positions_grid_text(text)

        self.assertEqual([row["symbol"] for row in positions], ["600000", "000001"])
        self.assertEqual(positions[0]["sellable_quantity"], 0)
        self.assertEqual(positions[1]["quantity"], 200)
        snapshot.reconcile_position_market_value(positions, Decimal("3450.00"))

    def test_parse_positions_grid_text_accepts_header_only_cash_account(self):
        text = "证券代码\t证券名称\t股票余额\t可用余额\t成本价\t市价\t市值\n"
        positions = snapshot.parse_positions_grid_text(text)
        self.assertEqual(positions, [])
        snapshot.reconcile_position_market_value(positions, Decimal("0"))

    def test_parse_positions_grid_text_fails_closed_on_invalid_sellable_quantity(self):
        text = (
            "证券代码\t股票余额\t可用余额\t成本价\t市价\t市值\n"
            "600000\t100\t200\t10.00\t10.00\t1000.00\n"
        )
        with self.assertRaisesRegex(RuntimeError, "sellable quantity exceeds"):
            snapshot.parse_positions_grid_text(text)

    def test_zero_market_value_skips_unreadable_empty_grid(self):
        def copier(_):
            raise AssertionError("empty account must not copy the owner-drawn grid")

        self.assertEqual(
            snapshot.positions_for_market_value(object(), Decimal("0"), copier=copier),
            [],
        )

    def test_nonzero_market_value_requires_readable_positions(self):
        with self.assertRaisesRegex(RuntimeError, "nonzero market value"):
            snapshot.positions_for_market_value(
                object(),
                Decimal("100"),
                copier=lambda _: "证券代码\t股票余额\t可用余额\t成本价\t市价\t市值\n",
            )

    def test_position_market_value_must_reconcile(self):
        positions = [{"market_value": 1000.0}]
        with self.assertRaisesRegex(RuntimeError, "does not reconcile"):
            snapshot.reconcile_position_market_value(positions, Decimal("1200"))


if __name__ == "__main__":
    unittest.main()
