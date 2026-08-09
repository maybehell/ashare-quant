from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
from io import StringIO
import json
from pathlib import Path
import re
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ashare_quant.live_secrets import read_live_secrets  # noqa: E402


SCHEMA = "ashare_quant.huatai_desktop_snapshot.v1"
PROCESS_NAMES = {"hexin.exe", "xiadan.exe"}
POSITION_HEADERS = {
    "symbol": {"证券代码", "股票代码", "代码"},
    "name": {"证券名称", "股票名称", "名称"},
    "quantity": {"股票余额", "证券数量", "持仓数量", "股份余额", "当前拥股", "当前数量", "实际数量", "数量"},
    "sellable_quantity": {"可用余额", "可卖数量", "可用数量", "可用股份", "可用股数", "可卖股数", "可卖"},
    "cost": {"成本价", "成本价格", "摊薄成本价", "保本价"},
    "last_price": {"市价", "现价", "当前价", "最新", "最新价"},
    "market_value": {"市值", "最新市值", "参考市值", "证券市值", "股票市值"},
}
REQUIRED_POSITION_FIELDS = {"symbol", "quantity", "sellable_quantity", "cost", "last_price", "market_value"}
ACCOUNT_SUMMARY_LABELS = {
    "frozen_cash": {"冻结金额", "冻结资金"},
    "available_cash": {"可用金额", "可用资金"},
    "withdrawable_cash": {"可取金额", "可取资金"},
    "market_value": {"股票市值", "证券市值"},
    "total_asset": {"总资产"},
    "pending_buy": {"买入未成", "买入冻结"},
}
ACCOUNT_SUMMARY_IDS = {
    "frozen_cash": "1013",
    "available_cash": "1016",
    "withdrawable_cash": "1017",
    "market_value": "1014",
    "total_asset": "1015",
    "pending_buy": "3401",
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Huatai desktop account snapshot collector.")
    parser.add_argument("--secrets", default="data/secrets/live_credentials.dpapi")
    parser.add_argument("--output", default="outputs/huatai_desktop_snapshot.json")
    return parser.parse_args(argv)


def parse_amount(value: object) -> Decimal:
    text = str(value or "").replace(",", "").replace(" ", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        raise RuntimeError("numeric account control is unreadable")
    try:
        return Decimal(match.group())
    except InvalidOperation as exc:
        raise RuntimeError("numeric account control is invalid") from exc


def normalize_header(value: object) -> str:
    text = re.sub(r"[（(][^）)]*[）)]", "", str(value or ""))
    return re.sub(r"[\s%％]", "", text).strip()

def validate_account_summary_layout(entries: list[tuple[object, object]]) -> dict[str, dict[str, str]]:
    label_fields = {
        normalize_header(alias): field
        for field, aliases in ACCOUNT_SUMMARY_LABELS.items()
        for alias in aliases
    }
    id_fields = {automation_id: field for field, automation_id in ACCOUNT_SUMMARY_IDS.items()}
    tokens: list[str] = []
    observed_labels: dict[str, str] = {}
    for raw_name, raw_id in entries:
        name = normalize_header(raw_name)
        automation_id = str(raw_id or "").strip()
        if name in label_fields:
            field = label_fields[name]
            tokens.append(f"label:{field}")
            observed_labels[field] = name
        if automation_id in id_fields:
            tokens.append(f"value:{id_fields[automation_id]}")

    expected = [
        "label:frozen_cash", "label:available_cash",
        "value:frozen_cash", "value:available_cash",
        "label:withdrawable_cash", "label:market_value", "label:total_asset",
        "value:withdrawable_cash", "value:market_value", "value:total_asset",
        "label:pending_buy", "value:pending_buy",
    ]
    matches = [
        index
        for index in range(len(tokens) - len(expected) + 1)
        if tokens[index:index + len(expected)] == expected
    ]
    value_ids_unique = all(tokens.count(f"value:{field}") == 1 for field in ACCOUNT_SUMMARY_IDS)
    if len(matches) != 1 or not value_ids_unique:
        raise RuntimeError(f"Huatai account summary label/control layout changed: observed={tokens}")
    return {
        field: {"label": observed_labels[field], "automation_id": automation_id}
        for field, automation_id in ACCOUNT_SUMMARY_IDS.items()
    }


def parse_position_quantity(value: object, field: str) -> int:
    number = parse_amount(value)
    if number != number.to_integral_value() or number < 0:
        raise RuntimeError(f"Huatai position {field} must be a non-negative integer")
    return int(number)


def parse_positions_grid_text(text: str) -> list[dict[str, Any]]:
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(StringIO(str(text or "")), delimiter="\t")
        if any(cell.strip() for cell in row)
    ]
    header_index = -1
    columns: dict[str, int] = {}
    for row_index, row in enumerate(rows):
        normalized = [normalize_header(cell) for cell in row]
        candidate = {
            field: index
            for field, aliases in POSITION_HEADERS.items()
            for index, header in enumerate(normalized)
            if header in aliases
        }
        if REQUIRED_POSITION_FIELDS.issubset(candidate):
            header_index = row_index
            columns = candidate
            break
    if header_index < 0:
        raise RuntimeError("Huatai positions grid header is incomplete or unsupported")

    positions: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_index = max(columns.values())
    for row in rows[header_index + 1 :]:
        if len(row) <= max_index:
            if any(cell.strip() for cell in row):
                raise RuntimeError("Huatai positions grid row has fewer columns than its header")
            continue
        symbol_text = row[columns["symbol"]]
        symbol_match = re.search(r"(?<!\d)(\d{6})(?!\d)", symbol_text)
        if symbol_match is None:
            if any(marker in "".join(row) for marker in ("合计", "总计")):
                continue
            raise RuntimeError("Huatai positions grid contains a row without a six-digit symbol")
        symbol = symbol_match.group(1)
        if symbol in seen:
            raise RuntimeError(f"Huatai positions grid contains duplicate symbol {symbol}")
        quantity = parse_position_quantity(row[columns["quantity"]], "quantity")
        sellable = parse_position_quantity(row[columns["sellable_quantity"]], "sellable_quantity")
        if sellable > quantity:
            raise RuntimeError(f"Huatai position {symbol} sellable quantity exceeds total quantity")
        cost = parse_amount(row[columns["cost"]])
        last_price = parse_amount(row[columns["last_price"]])
        market_value = parse_amount(row[columns["market_value"]])
        if min(cost, last_price, market_value) < 0:
            raise RuntimeError(f"Huatai position {symbol} contains a negative price or market value")
        if quantity == 0 and market_value == 0:
            continue
        if quantity <= 0 or last_price <= 0 or market_value <= 0:
            raise RuntimeError(f"Huatai position {symbol} has inconsistent quantity, price or market value")
        name = row[columns["name"]].strip() if "name" in columns else ""
        positions.append(
            {
                "symbol": symbol,
                "name": name,
                "quantity": quantity,
                "sellable_quantity": sellable,
                "cost": float(cost),
                "last_price": float(last_price),
                "market_value": float(market_value),
                "message": "Huatai desktop positions grid read-only snapshot.",
            }
        )
        seen.add(symbol)
    return positions


def positions_for_market_value(
    grid: Any,
    account_market_value: Decimal,
    *,
    copier: Any = None,
) -> list[dict[str, Any]]:
    if account_market_value == 0:
        return []
    copy_text = copier or copy_grid_unicode_text
    positions = parse_positions_grid_text(copy_text(grid))
    if not positions:
        raise RuntimeError("Huatai account has nonzero market value but no positions were readable")
    reconcile_position_market_value(positions, account_market_value)
    return positions


def reconcile_position_market_value(positions: list[dict[str, Any]], account_market_value: Decimal) -> None:
    position_total = sum((Decimal(str(row["market_value"])) for row in positions), Decimal("0"))
    tolerance = max(Decimal("1.00"), abs(account_market_value) * Decimal("0.0001"))
    if abs(position_total - account_market_value) > tolerance:
        raise RuntimeError(
            "Huatai positions market value does not reconcile with account summary: "
            f"positions={position_total};account={account_market_value};tolerance={tolerance}"
        )


def decode_clipboard_bytes(value: Any, encodings: tuple[str, ...]) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, (bytes, bytearray)):
        return str(value)
    raw = bytes(value).rstrip(b"\x00")
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise RuntimeError("Huatai clipboard text encoding is unreadable")


def clipboard_text(win32clipboard: Any) -> str:
    candidates = (
        (win32clipboard.CF_UNICODETEXT, ()),
        (win32clipboard.CF_TEXT, ("mbcs", "gb18030", "utf-8")),
        (getattr(win32clipboard, "CF_OEMTEXT", -1), ("oem", "gb18030", "utf-8")),
    )
    for format_id, encodings in candidates:
        if format_id < 0 or not win32clipboard.IsClipboardFormatAvailable(format_id):
            continue
        value = win32clipboard.GetClipboardData(format_id)
        text = str(value) if not encodings else decode_clipboard_bytes(value, encodings)
        if text.strip():
            return text
    available: list[str] = []
    format_id = 0
    while True:
        format_id = win32clipboard.EnumClipboardFormats(format_id)
        if not format_id:
            break
        try:
            name = win32clipboard.GetClipboardFormatName(format_id)
        except Exception:
            name = ""
        available.append(f"{format_id}:{name or 'standard'}")
    raise RuntimeError(
        "Huatai positions grid did not place readable text on clipboard; "
        f"formats={','.join(available) or 'none'}"
    )


def copy_grid_unicode_text(grid: Any) -> str:
    import time
    import win32clipboard
    from pywinauto.keyboard import send_keys

    old_format = None
    old_text: Any = ""
    try:
        win32clipboard.OpenClipboard()
        for format_id in (win32clipboard.CF_UNICODETEXT, win32clipboard.CF_TEXT):
            if win32clipboard.IsClipboardFormatAvailable(format_id):
                old_format = format_id
                old_text = win32clipboard.GetClipboardData(format_id)
                break
        win32clipboard.EmptyClipboard()
        win32clipboard.CloseClipboard()
        grid.set_focus()
        send_keys("^a^c")
        last_error: RuntimeError | None = None
        for _ in range(10):
            time.sleep(0.1)
            try:
                win32clipboard.OpenClipboard()
                return clipboard_text(win32clipboard)
            except RuntimeError as exc:
                last_error = exc
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
        raise last_error or RuntimeError("Huatai positions clipboard remained empty")
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            if old_format is not None:
                win32clipboard.SetClipboardData(old_format, old_text)
            win32clipboard.CloseClipboard()
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass


def process_ids_by_name() -> dict[str, set[int]]:
    import win32api
    import win32con
    import win32process

    result = {name: set() for name in PROCESS_NAMES}
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            name = Path(win32process.GetModuleFileNameEx(handle, 0)).name.lower()
            if name in result:
                result[name].add(int(pid))
        except Exception:
            continue
        finally:
            if handle is not None:
                handle.Close()
    return result


def native_process_window_inventory(
    process_ids: set[int],
    *,
    win32gui_module: Any | None = None,
    win32process_module: Any | None = None,
) -> dict[str, int]:
    if not process_ids:
        return {
            "xiadan_native_window_count": 0,
            "xiadan_visible_window_count": 0,
            "xiadan_top_level_window_count": 0,
        }
    if win32gui_module is None:
        import win32gui as win32gui_module
    if win32process_module is None:
        import win32process as win32process_module

    handles: dict[int, tuple[bool, bool]] = {}

    def inspect(handle: int) -> None:
        if handle in handles:
            return
        try:
            _, process_id = win32process_module.GetWindowThreadProcessId(handle)
            if int(process_id) not in process_ids:
                return
            handles[handle] = (
                bool(win32gui_module.IsWindowVisible(handle)),
                not bool(win32gui_module.GetParent(handle)),
            )
        except Exception:
            return

    def inspect_top(handle: int, _context: Any) -> None:
        inspect(handle)
        try:
            win32gui_module.EnumChildWindows(
                handle,
                lambda child, _child_context: (inspect(child), True)[1],
                None,
            )
        except Exception:
            pass

    win32gui_module.EnumWindows(inspect_top, None)
    return {
        "xiadan_native_window_count": len(handles),
        "xiadan_visible_window_count": sum(visible for visible, _top_level in handles.values()),
        "xiadan_top_level_window_count": sum(top_level for _visible, top_level in handles.values()),
    }


def classify_desktop_stage(    *,
    hexin_process_count: int,
    xiadan_process_count: int,
    visible_client_windows: int,
    trading_grid_count: int,
    total_client_windows: int | None = None,
    minimized_client_windows: int = 0,
    xiadan_native_window_count: int | None = None,
    xiadan_visible_window_count: int | None = None,
    xiadan_top_level_window_count: int | None = None,
) -> str:
    if hexin_process_count == 0 or xiadan_process_count == 0:
        return "client_process_missing"
    if visible_client_windows == 0:
        if total_client_windows is None:
            return "client_window_not_visible"
        if minimized_client_windows > 0:
            return "client_window_minimized"
        if total_client_windows > 0:
            return "client_window_hidden"
        if xiadan_native_window_count == 0:
            return "client_background_process_only"
        return "client_window_absent"
    if trading_grid_count == 0:
        if xiadan_visible_window_count == 0:
            return "trading_module_ui_not_loaded"
        return "trading_page_unavailable"
    return "account_controls_unreadable"


def collect_desktop_diagnostics() -> dict[str, Any]:
    from pywinauto import Desktop

    try:
        pids = process_ids_by_name()
    except Exception as exc:
        return {
            "stage": "diagnostic_failed",
            "hexin_process_count": 0,
            "xiadan_process_count": 0,
            "visible_client_windows": 0,
            "total_client_windows": 0,
            "minimized_client_windows": 0,
            "trading_grid_count": 0,
            "diagnostic_error": f"{type(exc).__name__}: {exc}",
        }

    values = {
        "hexin_process_count": len(pids["hexin.exe"]),
        "xiadan_process_count": len(pids["xiadan.exe"]),
        "visible_client_windows": 0,
        "total_client_windows": 0,
        "minimized_client_windows": 0,
        "trading_grid_count": 0,
        "xiadan_native_window_count": 0,
        "xiadan_visible_window_count": 0,
        "xiadan_top_level_window_count": 0,
    }
    errors: list[str] = []
    try:
        windows = Desktop(backend="uia").windows(visible_only=False)
    except Exception as exc:
        values["stage"] = classify_desktop_stage(**values)
        values["diagnostic_error"] = f"{type(exc).__name__}: {exc}"
        return values

    all_roots = []
    for window in windows:
        try:
            if window.process_id() in pids["hexin.exe"]:
                all_roots.append(window)
        except Exception as exc:
            errors.append(f"process_id:{type(exc).__name__}: {exc}")
    roots = []
    minimized = 0
    for window in all_roots:
        try:
            if window.is_visible():
                roots.append(window)
        except Exception as exc:
            errors.append(f"is_visible:{type(exc).__name__}: {exc}")
        try:
            minimized += int(bool(window.is_minimized()))
        except Exception as exc:
            errors.append(f"is_minimized:{type(exc).__name__}: {exc}")

    try:
        values.update(native_process_window_inventory(pids["xiadan.exe"]))
    except Exception as exc:
        errors.append(f"native_window_inventory:{type(exc).__name__}: {exc}")

    grid_count = 0
    for root in roots:
        try:
            descendants = root.descendants()
        except Exception as exc:
            errors.append(f"descendants:{type(exc).__name__}: {exc}")
            continue
        for control in descendants:
            try:
                if (
                    control.process_id() in pids["xiadan.exe"]
                    and control.element_info.class_name == "CVirtualGridCtrl"
                ):
                    grid_count += 1
            except Exception as exc:
                errors.append(f"grid_control:{type(exc).__name__}: {exc}")

    values.update(
        {
            "visible_client_windows": len(roots),
            "total_client_windows": len(all_roots),
            "minimized_client_windows": minimized,
            "trading_grid_count": grid_count,
        }
    )
    values["stage"] = classify_desktop_stage(**values)
    if errors:
        values["diagnostic_error"] = "; ".join(dict.fromkeys(errors))
    return values


def successful_snapshot_diagnostics(
    pids: dict[str, list[int]],
    roots: list[Any],
    grids: list[Any],
) -> dict[str, Any]:
    return {
        "stage": "account_snapshot_ready",
        "hexin_process_count": len(pids.get("hexin.exe", [])),
        "xiadan_process_count": len(pids.get("xiadan.exe", [])),
        "visible_client_windows": len(roots),
        "total_client_windows": len(roots),
        "minimized_client_windows": 0,
        "trading_grid_count": len(grids),
    }

def desktop_failure_message(diagnostics: dict[str, Any], exc: Exception) -> str:
    stage = str(diagnostics.get("stage", "") or "")
    if stage == "client_background_process_only":
        return (
            "Huatai client has background processes but no desktop window; "
            "fully exit the stale client processes, reopen the pinned desktop client, "
            "log in, and keep the funds/positions page visible"
        )
    return f"{type(exc).__name__}: {exc}"

def navigate_to_verified_funds_root(driver_factory=None) -> Any:
    if driver_factory is None:
        from ashare_quant.huatai_desktop_rpa import HuataiDesktopPreviewDriver
        driver_factory = HuataiDesktopPreviewDriver
    driver = driver_factory()
    driver.navigate("资金股票")
    root = getattr(driver, "root", None)
    if root is None:
        raise RuntimeError("Huatai verified funds navigation did not return a trading root")
    return root


def collect_snapshot(secret_path: Path) -> dict[str, Any]:
    from pywinauto import Desktop

    secrets = read_live_secrets(secret_path)
    huatai = secrets.get("huatai", {}) if isinstance(secrets.get("huatai"), dict) else {}
    raw_account_id = str(huatai.get("account_id", "")).strip()
    if len(raw_account_id) < 4:
        raise RuntimeError("Huatai account identifier is missing from encrypted credentials")

    pids = process_ids_by_name()
    desktop = Desktop(backend="uia")
    roots = [
        window
        for window in desktop.windows(visible_only=True)
        if window.process_id() in pids["hexin.exe"]
    ]
    if not roots:
        all_roots = [
            window
            for window in desktop.windows(visible_only=False)
            if window.process_id() in pids["hexin.exe"]
        ]
        minimized = sum(1 for window in all_roots if window.is_minimized())
        if minimized:
            detail = "Huatai client window is minimized; restore it and keep the funds/positions page open"
        elif all_roots:
            detail = "Huatai client window is hidden; restore it from the taskbar or tray"
        else:
            detail = "Huatai client process has no desktop window; reopen the client and log in"
        raise RuntimeError(detail)

    verified_root = navigate_to_verified_funds_root()
    if verified_root.process_id() not in pids["hexin.exe"]:
        raise RuntimeError("Huatai verified funds root is not owned by the official client process")
    roots = [verified_root]
    controls = verified_root.descendants()
    if not any(
        control.process_id() in pids["xiadan.exe"]
        and control.element_info.class_name == "CVirtualGridCtrl"
        for control in controls
    ):
        raise RuntimeError("Huatai verified funds page does not expose a positions grid")
    mapping_evidence = validate_account_summary_layout([
        (control.element_info.name, control.element_info.automation_id)
        for control in controls
        if control.process_id() in pids["xiadan.exe"] and control.is_visible()
    ])

    def amount(automation_id: str, *, required: bool = True) -> Decimal:
        candidates = [
            c for c in controls
            if c.process_id() in pids["xiadan.exe"]
            and str(c.element_info.automation_id or "") == automation_id
            and c.is_visible()
        ]
        if not candidates and not required:
            return Decimal("0")
        if len(candidates) != 1:
            raise RuntimeError(f"expected one visible Huatai account control {automation_id}, found {len(candidates)}")
        return parse_amount(candidates[0].element_info.name)

    available_cash = amount("1016")
    frozen_cash = amount("1013")
    market_value = amount("1014")
    total_asset = amount("1015")
    pending_buy = amount("3401", required=False)
    withdrawable_cash = amount("1017")
    account_values = {
        "available_cash": available_cash,
        "frozen_cash": frozen_cash,
        "market_value": market_value,
        "total_asset": total_asset,
        "pending_buy": pending_buy,
        "withdrawable_cash": withdrawable_cash,
    }
    negative_values = {key: str(value) for key, value in account_values.items() if value < 0}
    if negative_values:
        raise RuntimeError(f"negative Huatai account summary value is not accepted: {negative_values}")
    grids = [
        control
        for control in controls
        if control.process_id() in pids["xiadan.exe"]
        and control.element_info.class_name == "CVirtualGridCtrl"
        and control.is_visible()
    ]
    if len(grids) != 1:
        raise RuntimeError(f"expected one visible Huatai positions grid, found {len(grids)}")
    positions = positions_for_market_value(grids[0], market_value)
    cash = available_cash + frozen_cash
    unclassified_asset_value = total_asset - market_value - cash
    if cash < 0 or unclassified_asset_value < Decimal("-0.01"):
        raise RuntimeError("Huatai account summary does not reconcile")
    if abs(unclassified_asset_value) <= Decimal("0.01"):
        unclassified_asset_value = Decimal("0")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    account_id = "huatai-" + hashlib.sha256(raw_account_id.encode("utf-8")).hexdigest()[:20]
    return {
        "schema_version": SCHEMA,
        "status": "ok",
        "broker": "huatai",
        "environment": "live",
        "source": "huatai_desktop_accessibility",
        "broker_time": now,
        "generated_at": now,
        "account": {
            "account_id": account_id,
            "cash": float(cash),
            "available_cash": float(available_cash),
            "market_value": float(market_value),
            "total_asset": float(total_asset),
            "frozen_cash": float(frozen_cash),
            "unclassified_asset_value": float(unclassified_asset_value),
            "withdrawable_cash": float(withdrawable_cash),
            "pending_buy": float(pending_buy),
        },
        "positions": positions,
        "diagnostics": {
            **successful_snapshot_diagnostics(pids, roots, grids),
            "account_field_mapping": mapping_evidence,
            "safe_page_navigation": "verified_funds_tree_item",
            "cash_derivation": "available_cash+frozen_cash",
            "unclassified_asset_derivation": "total_asset-market_value-cash",
        },
        "message": f"Read-only Huatai desktop snapshot; {len(positions)} positions reconciled to account market value.",
    }


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    output = PROJECT_ROOT / args.output
    try:
        payload = collect_snapshot(PROJECT_ROOT / args.secrets)
    except Exception as exc:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        diagnostics = collect_desktop_diagnostics()
        failure_message = desktop_failure_message(diagnostics, exc)
        payload = {
            "schema_version": SCHEMA,
            "status": "failed",
            "broker": "huatai",
            "environment": "unknown",
            "generated_at": now,
            "account": {},
            "positions": [],
            "diagnostics": diagnostics,
            "message": failure_message,
        }
        write_payload(output, payload)
        print(f"Huatai read-only snapshot failed: {failure_message}")
        return 2
    write_payload(output, payload)
    print(f"Huatai read-only snapshot ready: {payload['account']['account_id']} (positions={len(payload['positions'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
