from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


DEFAULT_OUTPUT = Path("outputs/huatai_desktop_probe.json")
PROCESS_NAMES = {"hexin.exe", "xiadan.exe", "weituo.exe"}
SENSITIVE_LABELS = re.compile(r"password|密码|验证码|资金密码", re.IGNORECASE)
LONG_DIGITS = re.compile(r"(?<!\d)(\d{2})\d{4,}(\d{2})(?!\d)")
MONEY_VALUE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*\.\d{2,}(?!\w)")
EMAIL = re.compile(r"([\w.+-]{1,2})[\w.+-]*(@[\w.-]+)")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Huatai desktop accessibility probe.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-controls", type=int, default=500)
    return parser.parse_args(argv)


def redact_text(value: object, *, label: str = "") -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if SENSITIVE_LABELS.search(label) or SENSITIVE_LABELS.search(text):
        return "[redacted-sensitive-control]"
    text = EMAIL.sub(r"\1***\2", text)
    text = LONG_DIGITS.sub(r"\1***\2", text)
    text = MONEY_VALUE.sub("[amount]", text)
    return text[:240]


def _processes() -> list[dict[str, Any]]:
    import win32api
    import win32con
    import win32process

    rows: list[dict[str, Any]] = []
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            exe = str(win32process.GetModuleFileNameEx(handle, 0) or "")
            name = Path(exe).name.lower()
            if name in PROCESS_NAMES:
                rows.append({"pid": int(pid), "name": name, "exe": exe})
        except Exception:
            continue
        finally:
            if handle is not None:
                handle.Close()
    return rows


def _control_row(control: Any, backend: str) -> dict[str, Any]:
    info = control.element_info
    control_type = str(getattr(info, "control_type", "") or getattr(info, "class_name", ""))
    automation_id = str(getattr(info, "automation_id", "") or "")
    raw_name = str(getattr(info, "name", "") or "")
    return {
        "backend": backend,
        "control_type": control_type,
        "class_name": str(getattr(info, "class_name", "") or "")[:120],
        "automation_id": automation_id[:120],
        "name": redact_text(raw_name, label=f"{control_type} {automation_id}"),
        "enabled": bool(control.is_enabled()),
        "visible": bool(control.is_visible()),
    }


def inspect_desktop(max_controls: int) -> dict[str, Any]:
    from pywinauto import Desktop

    processes = _processes()
    process_ids = {row["pid"] for row in processes}
    windows: list[dict[str, Any]] = []
    errors: list[str] = []
    for backend in ("uia", "win32"):
        try:
            candidates = Desktop(backend=backend).windows(visible_only=False)
        except Exception as exc:
            errors.append(f"{backend}: desktop enumeration failed: {type(exc).__name__}: {exc}")
            continue
        for window in candidates:
            try:
                pid = int(window.process_id())
                if pid not in process_ids:
                    continue
                controls: list[dict[str, Any]] = []
                descendants = window.descendants()
                for control in descendants[:max(max_controls, 0)]:
                    try:
                        controls.append(_control_row(control, backend))
                    except Exception as exc:
                        errors.append(f"{backend}: control read failed: {type(exc).__name__}")
                windows.append({
                    "backend": backend,
                    "pid": pid,
                    "title": redact_text(window.window_text()),
                    "class_name": str(window.element_info.class_name or "")[:120],
                    "visible": bool(window.is_visible()),
                    "enabled": bool(window.is_enabled()),
                    "control_count": len(controls),
                    "controls": controls,
                })
            except Exception as exc:
                errors.append(f"{backend}: window read failed: {type(exc).__name__}: {exc}")
    return {
        "schema_version": "ashare_quant.huatai_desktop_probe.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "read_only",
        "input_performed": False,
        "screenshot_captured": False,
        "processes": processes,
        "windows": windows,
        "errors": errors[:50],
        "status": "ready" if windows else ("client_running_without_accessible_window" if processes else "client_not_running"),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = inspect_desktop(args.max_controls)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(f"Huatai desktop probe: {payload['status']} ({len(payload['windows'])} windows)")
    return 0 if payload["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
