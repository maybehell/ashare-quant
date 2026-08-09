from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import time
from typing import Any, Callable, Protocol


BUY_PAGE = "买入[F1]"
SELL_PAGE = "卖出[F2]"
SAFE_PAGE = "资金股票"
READ_ONLY_PAGES = {"当日委托", "当日成交", "撤单[F3]"}
FORM_CONTROL_IDS = {"symbol": "1032", "price": "1033", "quantity": "1034"}
FIELD_DISCOVERY_ATTEMPTS = 10
FIELD_DISCOVERY_INTERVAL_SECONDS = 0.2
FORBIDDEN_CONTROL_IDS = {
    "1006",   # Buy/sell submit
    "1099",   # Cancel selected
    "2053",   # Cancel last
    "30001",  # Cancel all
    "30002",  # Cancel buys
    "30003",  # Cancel sells
    "30022",  # Cancel same symbol
}
FORBIDDEN_LABEL_FRAGMENTS = ("买入[", "卖出[", "撤单", "全撤", "确认", "确定")


@dataclass(frozen=True)
class PreviewOrder:
    symbol: str
    side: str
    price: Decimal
    quantity: int


class PreviewDriver(Protocol):
    def navigate(self, page: str) -> None: ...
    def assert_form_contract(self, side: str) -> None: ...
    def read_field(self, role: str) -> str: ...
    def write_field(self, role: str, value: str) -> None: ...


def validate_preview_order(symbol: object, side: object, price: object, quantity: object) -> PreviewOrder:
    normalized_symbol = str(symbol or "").strip().zfill(6)
    normalized_side = str(side or "").strip().lower()
    if len(normalized_symbol) != 6 or not normalized_symbol.isdigit():
        raise ValueError("symbol must contain exactly six digits")
    if normalized_side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    try:
        normalized_price = Decimal(str(price))
    except InvalidOperation as exc:
        raise ValueError("price must be numeric") from exc
    try:
        normalized_quantity = int(str(quantity))
    except ValueError as exc:
        raise ValueError("quantity must be an integer") from exc
    if normalized_price <= 0 or normalized_price > Decimal("10000"):
        raise ValueError("preview price must be in (0, 10000]")
    if normalized_quantity <= 0:
        raise ValueError("quantity must be positive")
    if normalized_side == "buy" and normalized_quantity % 100:
        raise ValueError("buy preview quantity must be a 100-share lot")
    return PreviewOrder(normalized_symbol, normalized_side, normalized_price, normalized_quantity)


def forbidden_control(automation_id: object, name: object = "") -> bool:
    identifier = str(automation_id or "")
    label = str(name or "")
    return identifier in FORBIDDEN_CONTROL_IDS or any(fragment in label for fragment in FORBIDDEN_LABEL_FRAGMENTS)


def run_form_preview(
    driver: PreviewDriver,
    order: PreviewOrder,
    *,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    page = BUY_PAGE if order.side == "buy" else SELL_PAGE
    original: dict[str, str] = {}
    readback: dict[str, str] = {}
    write_values = {
        "symbol": order.symbol,
        "price": format(order.price, "f"),
        "quantity": str(order.quantity),
    }
    driver.navigate(page)
    driver.assert_form_contract(order.side)
    try:
        original = {role: driver.read_field(role) for role in FORM_CONTROL_IDS}
        driver.write_field("symbol", write_values["symbol"])
        pause(0.6)
        driver.write_field("price", write_values["price"])
        driver.write_field("quantity", write_values["quantity"])
        pause(0.3)
        readback = {role: driver.read_field(role).strip() for role in FORM_CONTROL_IDS}
        symbol_ok = "".join(filter(str.isdigit, readback["symbol"]))[-6:] == order.symbol
        price_ok = _decimal_equal(readback["price"], order.price)
        quantity_ok = _integer_equal(readback["quantity"], order.quantity)
        if not (symbol_ok and price_ok and quantity_ok):
            raise RuntimeError(
                f"Huatai preview readback mismatch: symbol={symbol_ok};price={price_ok};quantity={quantity_ok}"
            )
    finally:
        try:
            # Changing the symbol can repopulate price fields, so restore it first.
            for role in ("symbol", "price", "quantity"):
                if role in original:
                    driver.write_field(role, original[role])
            pause(0.3)
            restored = {role: driver.read_field(role).strip() for role in original}
            restore_ok = {
                role: _field_value_equal(role, restored[role], original[role])
                for role in original
            }
            if not all(restore_ok.values()):
                details = ";".join(f"{role}={passed}" for role, passed in restore_ok.items())
                raise RuntimeError(f"Huatai preview restore mismatch: {details}")
        finally:
            driver.navigate(SAFE_PAGE)
    return {
        "status": "preview_verified",
        "environment": "live",
        "broker": "huatai",
        "side": order.side,
        "symbol": order.symbol,
        "price": float(order.price),
        "quantity": order.quantity,
        "form_readback_verified": True,
        "form_restore_verified": True,
        "submit_invoked": False,
        "cancel_invoked": False,
        "forbidden_control_ids_sha256": hashlib.sha256(
            ",".join(sorted(FORBIDDEN_CONTROL_IDS)).encode("ascii")
        ).hexdigest(),
    }


def _field_value_equal(role: str, value: str, expected: str) -> bool:
    actual = str(value or "").strip()
    original = str(expected or "").strip()
    if not original:
        return not actual
    if role == "symbol":
        return "".join(filter(str.isdigit, actual))[-6:] == "".join(filter(str.isdigit, original))[-6:]
    if role == "price":
        try:
            return Decimal(actual.replace(",", "")) == Decimal(original.replace(",", ""))
        except InvalidOperation:
            return False
    if role == "quantity":
        try:
            return int(Decimal(actual.replace(",", ""))) == int(Decimal(original.replace(",", "")))
        except (InvalidOperation, ValueError):
            return False
    return actual == original


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


class HuataiDesktopPreviewDriver:
    def __init__(self) -> None:
        from pywinauto import Desktop

        self._pids = self._process_ids()
        roots = [
            window for window in Desktop(backend="uia").windows(visible_only=True)
            if window.process_id() in self._pids["hexin.exe"]
        ]
        self.xiadan_pids = self._pids["xiadan.exe"]
        if not self.xiadan_pids:
            raise RuntimeError("Huatai trading process is not running")
        candidates = []
        for window in roots:
            descendants = window.descendants()
            has_trading_child = any(
                control.process_id() in self.xiadan_pids
                and control.element_info.class_name == "CVirtualGridCtrl"
                for control in descendants
            )
            has_buy_navigation = any(
                control.element_info.control_type == "TreeItem"
                and str(control.element_info.name or "") == BUY_PAGE
                for control in descendants
            )
            if has_trading_child and has_buy_navigation:
                candidates.append(window)
        if len(candidates) != 1:
            raise RuntimeError(
                f"expected one Huatai trading root window, found {len(candidates)} "
                f"from {len(roots)} visible roots"
            )
        self.root = candidates[0]

    @staticmethod
    def _process_ids() -> dict[str, set[int]]:
        import win32api
        import win32con
        import win32process

        result = {"hexin.exe": set(), "xiadan.exe": set()}
        for pid in win32process.EnumProcesses():
            handle = None
            try:
                handle = win32api.OpenProcess(
                    win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                    False,
                    pid,
                )
                exe = Path(win32process.GetModuleFileNameEx(handle, 0))
                name = exe.name.lower()
                if name in result and str(exe.parent).lower().startswith(r"c:\htzqzyb3"):
                    result[name].add(int(pid))
            except Exception:
                continue
            finally:
                if handle is not None:
                    handle.Close()
        return result

    def navigate(self, page: str) -> None:
        allowed = {BUY_PAGE, SELL_PAGE, SAFE_PAGE, *READ_ONLY_PAGES}
        if page not in allowed:
            raise RuntimeError(f"navigation page is not allowed in preview mode: {page}")
        items = []
        for attempt in range(3):
            items = [
                control for control in self.root.descendants()
                if control.element_info.control_type == "TreeItem"
                and str(control.element_info.name or "") == page
            ]
            if len(items) == 1:
                break
            if len(items) > 1:
                break
            time.sleep(0.25 * (attempt + 1))
        if len(items) != 1:
            raise RuntimeError(f"expected one Huatai navigation item {page}, found {len(items)} after 3 attempts")
        self.root.set_focus()
        items[0].click_input()
        time.sleep(0.5)

    def assert_form_contract(self, side: str) -> None:
        controls = self._visible_controls()
        for role, identifier in FORM_CONTROL_IDS.items():
            matches = [
                control for control in controls
                if str(control.element_info.automation_id or "") == identifier
                and control.element_info.control_type == "Edit"
            ]
            if len(matches) != 1:
                raise RuntimeError(f"Huatai {role} control contract mismatch: {len(matches)}")
        submit = [
            control for control in controls
            if str(control.element_info.automation_id or "") == "1006"
            and control.element_info.control_type == "Button"
        ]
        expected = "买入[B]" if side == "buy" else "卖出[S]"
        if len(submit) != 1 or str(submit[0].element_info.name or "") != expected:
            raise RuntimeError("Huatai submit control contract mismatch")
        if not forbidden_control(submit[0].element_info.automation_id, submit[0].element_info.name):
            raise RuntimeError("Huatai submit control is not protected by the denylist")

    def read_field(self, role: str) -> str:
        control = self._field(role)
        try:
            return str(control.get_value())
        except Exception:
            return str(control.window_text())

    def write_field(self, role: str, value: str) -> None:
        control = self._field(role)
        if forbidden_control(control.element_info.automation_id, control.element_info.name):
            raise RuntimeError(f"refusing to write forbidden Huatai control {role}")
        control.set_edit_text(str(value))

    def visible_grids(self) -> list[Any]:
        return [
            control for control in self._visible_controls()
            if control.element_info.class_name == "CVirtualGridCtrl"
        ]

    def _field(self, role: str) -> Any:
        identifier = FORM_CONTROL_IDS.get(role)
        if not identifier:
            raise RuntimeError(f"unknown Huatai preview field role: {role}")
        matches = []
        for attempt in range(FIELD_DISCOVERY_ATTEMPTS):
            matches = [
                control for control in self._visible_controls()
                if str(control.element_info.automation_id or "") == identifier
                and control.element_info.control_type == "Edit"
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                break
            if attempt + 1 < FIELD_DISCOVERY_ATTEMPTS:
                time.sleep(FIELD_DISCOVERY_INTERVAL_SECONDS)
        if len(matches) != 1:
            raise RuntimeError(f"expected one visible Huatai field {role}, found {len(matches)}")
        return matches[0]

    def _visible_controls(self) -> list[Any]:
        return [
            control for control in self.root.descendants()
            if control.process_id() in self.xiadan_pids and control.is_visible()
        ]
