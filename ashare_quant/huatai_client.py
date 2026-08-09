from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv


HUATAI_CLIENT_STATUS_COLUMNS = [
    "status",
    "installed",
    "executable",
    "executable_sha256",
    "sha256_match",
    "hexin_process_count",
    "xiadan_process_count",
    "start_attempted",
    "started",
    "message",
    "generated_at",
]


def huatai_process_counts() -> dict[str, int]:
    import win32api
    import win32con
    import win32process

    counts = {"hexin.exe": 0, "xiadan.exe": 0}
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            name = Path(win32process.GetModuleFileNameEx(handle, 0)).name.lower()
            if name in counts:
                counts[name] += 1
        except Exception:
            continue
        finally:
            if handle is not None:
                handle.Close()
    return counts


def configured_huatai_client_executable(config: dict[str, Any]) -> Path | None:
    client = config.get("desktop_client", {}) if isinstance(config.get("desktop_client"), dict) else {}
    value = str(client.get("executable", "") or "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = project_path(value)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or resolved.name.lower() != "hexin.exe":
        return None
    return resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def huatai_client_integrity(config: dict[str, Any], executable: Path | None) -> tuple[str, bool]:
    if executable is None:
        return "", False
    client = config.get("desktop_client", {}) if isinstance(config.get("desktop_client"), dict) else {}
    expected = str(client.get("executable_sha256", "") or "").strip().lower()
    actual = file_sha256(executable).lower()
    return actual, not expected or actual == expected


def huatai_client_executable(config: dict[str, Any]) -> Path | None:
    executable = configured_huatai_client_executable(config)
    _, integrity_ok = huatai_client_integrity(config, executable)
    return executable if integrity_ok else None


def restore_huatai_trading_window(executable: Path) -> tuple[bool, str]:
    """Restore the unique minimized Huatai root that owns xiadan controls."""
    import win32api
    import win32con
    import win32gui
    import win32process
    from pywinauto import Desktop

    executable = executable.resolve()
    executable_parent = executable.parent
    hexin_pids: set[int] = set()
    xiadan_pids: set[int] = set()
    for pid in win32process.EnumProcesses():
        handle = None
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False,
                pid,
            )
            process_path = Path(win32process.GetModuleFileNameEx(handle, 0)).resolve()
            if process_path == executable:
                hexin_pids.add(int(pid))
            elif process_path.name.lower() == "xiadan.exe" and process_path.parent == executable_parent:
                xiadan_pids.add(int(pid))
        except Exception:
            continue
        finally:
            if handle is not None:
                handle.Close()
    if not hexin_pids or not xiadan_pids:
        return False, "pinned Huatai trading processes are not both running"

    candidates = []
    for window in Desktop(backend="uia").windows(visible_only=False):
        try:
            if window.process_id() not in hexin_pids or not win32gui.IsIconic(window.handle):
                continue
            if any(control.process_id() in xiadan_pids for control in window.descendants()):
                candidates.append(window)
        except Exception:
            continue
    if len(candidates) != 1:
        return False, f"expected one minimized Huatai trading root, found {len(candidates)}"

    try:
        win32gui.ShowWindow(candidates[0].handle, win32con.SW_RESTORE)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "restored the pinned minimized Huatai trading window"

def request_huatai_client_window_wake(
    config: dict[str, Any],
    *,
    launcher: Callable[..., Any] = subprocess.Popen,
    restorer: Callable[[Path], tuple[bool, str]] = restore_huatai_trading_window,
) -> tuple[bool, str]:
    client = config.get("desktop_client", {}) if isinstance(config.get("desktop_client"), dict) else {}
    if not bool(client.get("wake_windowless_on_refresh", False)):
        return False, "windowless wake is disabled"
    configured_executable = configured_huatai_client_executable(config)
    _, integrity_ok = huatai_client_integrity(config, configured_executable)
    if configured_executable is None:
        return False, "configured Huatai executable is missing or invalid"
    if not integrity_ok:
        return False, "configured Huatai executable SHA-256 does not match"
    try:
        restored, restore_message = restorer(configured_executable)
    except Exception as exc:
        restored, restore_message = False, f"{type(exc).__name__}: {exc}"
    if restored:
        return True, restore_message
    try:
        launcher(
            [str(configured_executable)],
            cwd=str(configured_executable.parent),
            shell=False,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, (
        "window wake requested from the pinned Huatai executable; "
        f"existing trading-window restore unavailable: {restore_message}"
    )


def build_huatai_client_status(
    config: dict[str, Any],
    *,
    start_if_needed: bool = False,
    process_counts: Callable[[], dict[str, int]] = huatai_process_counts,
    launcher: Callable[..., Any] = subprocess.Popen,
    pause: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    client = config.get("desktop_client", {}) if isinstance(config.get("desktop_client"), dict) else {}
    configured_executable = configured_huatai_client_executable(config)
    actual_sha256, sha256_match = huatai_client_integrity(config, configured_executable)
    executable = configured_executable if sha256_match else None
    counts = process_counts()
    hexin_count = int(counts.get("hexin.exe", 0) or 0)
    xiadan_count = int(counts.get("xiadan.exe", 0) or 0)
    attempted = False
    started = False
    message = ""

    if configured_executable is not None and not sha256_match:
        status = "client_integrity_mismatch"
        message = "Configured Huatai executable SHA-256 does not match; automatic start is blocked."
    elif hexin_count and xiadan_count:
        status = "trading_process_ready"
        message = "Huatai client and trading processes are running; account login and page checks still apply."
    elif hexin_count:
        status = "started_login_required"
        message = "Huatai client is running; log in and open the funds/positions page."
    elif executable is None:
        status = "client_executable_missing"
        message = "Configured Huatai hexin.exe is missing or invalid."
    elif start_if_needed and bool(client.get("auto_start", False)):
        attempted = True
        try:
            launcher([str(executable)], cwd=str(executable.parent), shell=False)
            wait_seconds = max(float(client.get("startup_wait_seconds", 5)), 0.0)
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                pause(min(0.25, max(deadline - time.monotonic(), 0.0)))
                counts = process_counts()
                hexin_count = int(counts.get("hexin.exe", 0) or 0)
                xiadan_count = int(counts.get("xiadan.exe", 0) or 0)
                if hexin_count:
                    break
            started = hexin_count > 0
            status = "started_login_required" if started else "client_start_unverified"
            message = (
                "Huatai client was started; complete login and open the funds/positions page."
                if started
                else "Huatai executable was launched but its process was not observed before timeout."
            )
        except Exception as exc:
            status = "client_start_failed"
            message = f"{type(exc).__name__}: {exc}"
    else:
        status = "installed_not_running"
        message = "Huatai client is installed but not running."

    row = {
        "status": status,
        "installed": executable is not None,
        "executable": str(configured_executable or ""),
        "executable_sha256": actual_sha256,
        "sha256_match": sha256_match,
        "hexin_process_count": hexin_count,
        "xiadan_process_count": xiadan_count,
        "start_attempted": attempted,
        "started": started,
        "message": message,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return pd.DataFrame([row], columns=HUATAI_CLIENT_STATUS_COLUMNS)


def write_huatai_client_status(config: dict[str, Any], *, start_if_needed: bool = False) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "huatai_client_status.csv"
    atomic_to_csv(build_huatai_client_status(config, start_if_needed=start_if_needed), path, index=False, encoding="utf-8-sig")
    return path
