from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import time
from typing import Any

from .huatai_canary import (
    BrokerSubmission,
    CanaryOrder,
    aggregate_trade_rows,
    match_order_row,
    parse_order_grid_text,
    parse_trade_grid_text,
)
from .huatai_desktop_rpa import (
    BUY_PAGE,
    FORM_CONTROL_IDS,
    READ_ONLY_PAGES,
    SAFE_PAGE,
    SELL_PAGE,
    HuataiDesktopPreviewDriver,
)


ORDER_PAGE = "当日委托"
TRADE_PAGE = "当日成交"
CANCEL_PAGE = "撤单[F3]"
SUBMIT_CONTROL_ID = "1006"
CANCEL_SELECTED_CONTROL_ID = "1099"
AFFIRMATIVE_BUTTON_IDS = {"1", "6"}
AFFIRMATIVE_BUTTON_NAMES = {
    "\u786e\u5b9a",
    "\u786e\u8ba4",
    "\u662f",
    "\u662f(&Y)",
    "OK",
    "Yes",
}


class HuataiDesktopCanaryDriver:
    def __init__(self, *, emergency_stop_path: str | Path | None = None) -> None:
        self.driver = HuataiDesktopPreviewDriver()
        self.emergency_stop_path = Path(emergency_stop_path) if emergency_stop_path else None

    def runtime_selftest(self) -> tuple[bool, str]:
        if not {ORDER_PAGE, TRADE_PAGE, CANCEL_PAGE}.issubset(READ_ONLY_PAGES):
            return False, "Huatai read-only navigation contract is incomplete."
        try:
            self.driver.navigate(BUY_PAGE)
            self.driver.assert_form_contract("buy")
            self.driver.navigate(SELL_PAGE)
            self.driver.assert_form_contract("sell")
            for page in (ORDER_PAGE, TRADE_PAGE, CANCEL_PAGE):
                self.driver.navigate(page)
                if len(self.driver.visible_grids()) != 1:
                    return False, f"Huatai {page} grid contract mismatch."
        except Exception as exc:
            return False, f"Huatai desktop runtime self-test failed: {type(exc).__name__}: {exc}"
        finally:
            self.driver.navigate(SAFE_PAGE)
        return True, "Huatai desktop form and read-only grid contracts are available."

    def submit(self, order: CanaryOrder, *, run_id: str) -> BrokerSubmission:
        del run_id
        self._assert_not_stopped()
        baseline_rows = self._read_order_rows(ORDER_PAGE)
        if baseline_rows:
            raise RuntimeError(
                "Huatai desktop Canary requires an empty current-order grid immediately before submit"
            )
        self._assert_not_stopped()
        page = BUY_PAGE if order.side == "buy" else SELL_PAGE
        self.driver.navigate(page)
        self.driver.assert_form_contract(order.side)
        submit_clicked = False
        try:
            self._write_and_verify_order(order)
            self._assert_not_stopped()
            self._button(SUBMIT_CONTROL_ID).click_input()
            submit_clicked = True
            dialog = self._find_order_confirmation(order, timeout_seconds=2.5)
            if dialog is not None:
                self._assert_not_stopped()
                dialog.click_input()
            row = self._wait_for_order_row(order, timeout_seconds=6.0)
            if row is None:
                return BrokerSubmission(
                    "unknown",
                    "",
                    "Submit control was invoked but no unique broker order row was readable; do not retry.",
                )
            broker_order_id = str(row.get("broker_order_id", "") or "")
            broker_status = str(row.get("broker_status", "unknown"))
            if broker_status == "rejected":
                return BrokerSubmission("rejected", broker_order_id, str(row.get("raw_status", "rejected")))
            if not broker_order_id:
                return BrokerSubmission("unknown", "", "Matched Huatai row has no broker order id; do not retry.")
            return BrokerSubmission("submitted", broker_order_id, f"Huatai order row status={broker_status}.")
        except Exception:
            if submit_clicked:
                raise
            raise
        finally:
            self.driver.navigate(SAFE_PAGE)

    def query(self, order: dict[str, Any]) -> dict[str, Any]:
        rows = self._read_order_rows(ORDER_PAGE)
        broker_order_id = str(order.get("broker_order_id", "") or "")
        matches = [row for row in rows if broker_order_id and str(row.get("broker_order_id", "")) == broker_order_id]
        if not matches and all(key in order for key in ("symbol", "side", "quantity", "limit_price")):
            candidate = CanaryOrder(
                ticket_id=str(order.get("ticket_id", "")),
                symbol=str(order.get("symbol", "")).zfill(6),
                side=str(order.get("side", "")).lower(),
                quantity=int(float(order.get("quantity", 0))),
                limit_price=Decimal(str(order.get("limit_price", 0))),
            )
            matched = match_order_row(rows, candidate)
            matches = [matched] if matched is not None else []
        if len(matches) > 1:
            raise RuntimeError("multiple Huatai rows match broker order id")
        if not matches:
            return {"broker_status": "unknown", "message": "No unique Huatai order row matched."}
        result = dict(matches[0])
        _assert_order_row_identity(matches[0], order)
        filled_quantity = int(result.get("filled_quantity", 0) or 0)
        if filled_quantity <= 0:
            result.update({"filled_amount": 0.0, "fee": 0.0, "fee_status": "not_applicable", "fee_source": "none"})
            return result
        stable_order_id = str(result.get("broker_order_id", broker_order_id) or broker_order_id).strip()
        aggregate = aggregate_trade_rows(self._read_trade_rows(), stable_order_id)
        if aggregate is None:
            result.update(
                {
                    "filled_amount": filled_quantity * float(result.get("avg_fill_price", 0) or 0),
                    "fee": 0.0,
                    "fee_status": "unavailable",
                    "fee_source": "huatai_trade_grid_no_matching_rows",
                }
            )
            return result
        if int(aggregate["filled_quantity"]) != filled_quantity:
            raise RuntimeError("Huatai order and trade grids disagree on cumulative filled quantity")
        if str(aggregate["symbol"]) != str(result.get("symbol", "")):
            raise RuntimeError("Huatai order and trade grids disagree on symbol")
        order_side = str(result.get("side", ""))
        if str(aggregate.get("side", "")) and order_side and str(aggregate["side"]) != order_side:
            raise RuntimeError("Huatai order and trade grids disagree on side")
        broker_status = result.get("broker_status", "unknown")
        raw_status = result.get("raw_status", "")
        result.update(aggregate)
        result.update({"broker_status": broker_status, "raw_status": raw_status})
        return result

    def cancel(self, order: dict[str, Any]) -> dict[str, Any]:
        broker_order_id = str(order.get("broker_order_id", "") or "").strip()
        if not broker_order_id:
            raise RuntimeError("broker_order_id is required for Huatai cancel")
        self.driver.navigate(CANCEL_PAGE)
        try:
            rows, grid = self._read_rows_and_grid(CANCEL_PAGE, keep_page=True)
            indexes = [index for index, row in enumerate(rows) if str(row.get("broker_order_id", "")) == broker_order_id]
            if len(indexes) != 1:
                raise RuntimeError("Huatai cancel target is not a unique grid row")
            _assert_order_row_identity(rows[indexes[0]], order)
            bands = self._grid_row_bands(grid.capture_as_image())
            if len(bands) != len(rows):
                raise RuntimeError("Huatai cancel grid visual rows do not match parsed rows")
            top, bottom = bands[indexes[0]]
            grid.click_input(coords=(max(20, int(grid.rectangle().width() * 0.1)), (top + bottom) // 2))
            selected_text = self._copy_grid_text(grid, select_all=False)
            if broker_order_id not in selected_text:
                raise RuntimeError("Huatai selected cancel row failed broker id readback")
            self._assert_not_stopped()
            self._button(CANCEL_SELECTED_CONTROL_ID).click_input()
            dialog = self._find_text_confirmation(broker_order_id, timeout_seconds=2.5)
            if dialog is None:
                return {"cancel_status": "unknown", "message": "Cancel click had no verifiable confirmation dialog."}
            self._assert_not_stopped()
            dialog.click_input()
            time.sleep(0.8)
            queried = self.query(order)
            status = str(queried.get("broker_status", "unknown"))
            return {
                "cancel_status": "canceled" if status == "canceled" else "cancel_requested",
                "message": f"Huatai post-cancel order status={status}.",
            }
        finally:
            self.driver.navigate(SAFE_PAGE)

    def _write_and_verify_order(self, order: CanaryOrder) -> None:
        values = {
            "symbol": order.symbol,
            "price": format(order.limit_price, "f"),
            "quantity": str(order.quantity),
        }
        for role in ("symbol", "price", "quantity"):
            self.driver.write_field(role, values[role])
            if role == "symbol":
                time.sleep(0.5)
        readback = {role: self.driver.read_field(role).strip() for role in FORM_CONTROL_IDS}
        symbol_ok = "".join(re.findall(r"\d", readback["symbol"]))[-6:] == order.symbol
        price_ok = _decimal_equal(readback["price"], order.limit_price)
        quantity_ok = _integer_equal(readback["quantity"], order.quantity)
        if not (symbol_ok and price_ok and quantity_ok):
            raise RuntimeError(
                f"Huatai live form readback mismatch: symbol={symbol_ok};price={price_ok};quantity={quantity_ok}"
            )

    def _button(self, automation_id: str) -> Any:
        matches = [
            control
            for control in self.driver._visible_controls()
            if str(control.element_info.automation_id or "") == automation_id
            and control.element_info.control_type == "Button"
            and control.is_enabled()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected one enabled Huatai button {automation_id}, found {len(matches)}")
        return matches[0]

    def _wait_for_order_row(self, order: CanaryOrder, *, timeout_seconds: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                row = match_order_row(self._read_order_rows(ORDER_PAGE), order)
                if row is not None:
                    return row
            except Exception as exc:
                last_error = exc
            time.sleep(0.5)
        if last_error:
            raise RuntimeError(f"Huatai order query failed after submit: {last_error}") from last_error
        return None

    def _read_order_rows(self, page: str) -> list[dict[str, Any]]:
        rows, _ = self._read_rows_and_grid(page, keep_page=False)
        return rows

    def _read_trade_rows(self) -> list[dict[str, Any]]:
        self.driver.navigate(TRADE_PAGE)
        try:
            grids = self.driver.visible_grids()
            if len(grids) != 1:
                raise RuntimeError(f"expected one Huatai grid on {TRADE_PAGE}, found {len(grids)}")
            return parse_trade_grid_text(self._copy_grid_text(grids[0], select_all=True))
        finally:
            self.driver.navigate(SAFE_PAGE)

    def _read_rows_and_grid(self, page: str, *, keep_page: bool) -> tuple[list[dict[str, Any]], Any]:
        self.driver.navigate(page)
        try:
            grids = self.driver.visible_grids()
            if len(grids) != 1:
                raise RuntimeError(f"expected one Huatai grid on {page}, found {len(grids)}")
            text = self._copy_grid_text(grids[0], select_all=True)
            rows = parse_order_grid_text(text)
            return rows, grids[0]
        finally:
            if not keep_page:
                self.driver.navigate(SAFE_PAGE)

    def _copy_grid_text(self, grid: Any, *, select_all: bool) -> str:
        import win32clipboard
        from pywinauto.keyboard import send_keys

        old_text = ""
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                old_text = str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT))
            win32clipboard.CloseClipboard()
            grid.set_focus()
            send_keys("^a^c" if select_all else "^c")
            time.sleep(0.25)
            win32clipboard.OpenClipboard()
            if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                raise RuntimeError("Huatai grid did not place Unicode text on clipboard")
            return str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT))
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, old_text)
                win32clipboard.CloseClipboard()
            except Exception:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass

    def _find_order_confirmation(self, order: CanaryOrder, *, timeout_seconds: float) -> Any | None:
        return self._find_text_confirmation(order.symbol, str(order.quantity), timeout_seconds=timeout_seconds)

    def _find_text_confirmation(self, *required_text: str, timeout_seconds: float) -> Any | None:
        from pywinauto import Desktop

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for window in Desktop(backend="uia").windows(visible_only=True):
                if window.process_id() not in self.driver.xiadan_pids:
                    continue
                controls = [window, *window.descendants()]
                combined = " ".join(str(control.element_info.name or "") for control in controls)
                if not all(value in combined for value in required_text):
                    continue
                buttons = [
                    control
                    for control in controls
                    if control.element_info.control_type == "Button"
                    and control.is_enabled()
                    and (
                        str(control.element_info.automation_id or "") in AFFIRMATIVE_BUTTON_IDS
                        or str(control.element_info.name or "").strip() in AFFIRMATIVE_BUTTON_NAMES
                    )
                ]
                if len(buttons) == 1:
                    return buttons[0]
            time.sleep(0.1)
        return None

    def _assert_not_stopped(self) -> None:
        if self.emergency_stop_path and self.emergency_stop_path.exists():
            raise RuntimeError("emergency stop became active inside Huatai desktop driver")

    @staticmethod
    def _grid_row_bands(image: Any) -> list[tuple[int, int]]:
        import numpy as np

        if image is None:
            raise RuntimeError("Huatai grid capture returned no image")
        grayscale = np.asarray(image.convert("L"))
        if grayscale.ndim != 2:
            raise RuntimeError("Huatai grid image is invalid")
        dark = (grayscale < 180).sum(axis=1)
        width = grayscale.shape[1]
        separators = [index for index, value in enumerate(dark[:120]) if value >= int(width * 0.8)]
        if not separators:
            raise RuntimeError("Huatai grid separator is missing")
        start = max(separators) + 2
        active = dark[start : max(start, len(dark) - 20)] > max(20, int(width * 0.01))
        bands: list[tuple[int, int]] = []
        band_start: int | None = None
        for offset, value in enumerate(active):
            row = start + offset
            if value and band_start is None:
                band_start = row
            elif not value and band_start is not None:
                if row - band_start >= 2:
                    bands.append((band_start, row - 1))
                band_start = None
        if band_start is not None:
            bands.append((band_start, start + len(active) - 1))
        return bands



def _assert_order_row_identity(row: dict[str, Any], order: dict[str, Any]) -> None:
    mismatches: list[str] = []
    expected_broker_id = str(order.get("broker_order_id", "") or "").strip()
    actual_broker_id = str(row.get("broker_order_id", "") or "").strip()
    if expected_broker_id and actual_broker_id != expected_broker_id:
        mismatches.append("broker_order_id")

    expected_symbol = "".join(re.findall(r"\d", str(order.get("symbol", "") or "")))[-6:]
    if expected_symbol and str(row.get("symbol", "") or "") != expected_symbol:
        mismatches.append("symbol")

    expected_side = str(order.get("side", "") or "").strip().lower()
    if expected_side and str(row.get("side", "") or "").strip().lower() != expected_side:
        mismatches.append("side")

    if "quantity" in order and str(order.get("quantity", "") or "").strip():
        try:
            expected_quantity = int(Decimal(str(order["quantity"])))
        except (InvalidOperation, ValueError) as exc:
            raise RuntimeError("Huatai source order quantity is invalid") from exc
        if int(row.get("quantity", 0) or 0) != expected_quantity:
            mismatches.append("quantity")

    if "limit_price" in order and str(order.get("limit_price", "") or "").strip():
        try:
            expected_price = Decimal(str(order["limit_price"]))
            actual_price = Decimal(str(row.get("limit_price", 0)))
        except InvalidOperation as exc:
            raise RuntimeError("Huatai source or broker order price is invalid") from exc
        if abs(actual_price - expected_price) > Decimal("0.001"):
            mismatches.append("limit_price")

    if mismatches:
        raise RuntimeError(
            "Huatai broker order identity mismatch: " + ",".join(mismatches)
        )


def _decimal_equal(value: str, expected: Decimal) -> bool:
    try:
        return Decimal(value.replace(",", "").strip()) == expected
    except InvalidOperation:
        return False


def _integer_equal(value: str, expected: int) -> bool:
    try:
        return int(Decimal(value.replace(",", "").strip())) == expected
    except (InvalidOperation, ValueError):
        return False
