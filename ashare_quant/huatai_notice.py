from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
from typing import Any, Callable, Protocol

import pandas as pd

from .config import project_path
from .huatai_client import configured_huatai_client_executable, huatai_client_integrity
from .io_utils import atomic_to_csv


NOTICE_DIALOG_CLASS = "#32770"
NAVIGATION_NAME = "\u5bfc\u822a"
NO_NEW_MESSAGE_NAME = "\u6ca1\u6709\u65b0\u6d88\u606f\uff0c\u4e0b\u6b21\u4e0d\u518d\u663e\u793a"
TODAY_ONLY_NAME = "\u4eca\u65e5\u4e0d\u518d\u63d0\u793a"
NOTICE_TAB_CLASS = "CCustomTabCtrl"
HUATAI_NOTICE_AUDIT_COLUMNS = [
    "status",
    "enabled",
    "detected",
    "attempted",
    "close_count",
    "dismissal_confirmed",
    "message",
    "generated_at",
]


class NoticeNotPresent(RuntimeError):
    pass


class NoticeDriver(Protocol):
    def close_once(self) -> None: ...
    def wait_closed(self, timeout_seconds: float, poll_seconds: float) -> bool: ...


@dataclass(frozen=True)
class HuataiNoticeResult:
    status: str
    enabled: bool
    detected: bool
    attempted: bool
    close_count: int
    dismissal_confirmed: bool
    message: str
    audit_path: Path


def huatai_notice_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("desktop_notice", {})
    return value if isinstance(value, dict) else {}


def huatai_notice_enabled(config: dict[str, Any]) -> bool:
    return bool(huatai_notice_config(config).get("enabled", False))


def _write_notice_audit(
    config: dict[str, Any],
    *,
    status: str,
    enabled: bool,
    detected: bool,
    attempted: bool,
    close_count: int,
    dismissal_confirmed: bool,
    message: str,
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "huatai_notice_audit.csv"
    row = {
        "status": status,
        "enabled": enabled,
        "detected": detected,
        "attempted": attempted,
        "close_count": close_count,
        "dismissal_confirmed": dismissal_confirmed,
        "message": message,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    atomic_to_csv(
        pd.DataFrame([row], columns=HUATAI_NOTICE_AUDIT_COLUMNS),
        path,
        index=False,
        encoding="utf-8-sig",
    )
    return path


def dismiss_huatai_notice(
    config: dict[str, Any],
    *,
    driver_factory: Callable[[dict[str, Any]], NoticeDriver] | None = None,
) -> HuataiNoticeResult:
    enabled = huatai_notice_enabled(config)
    status = "disabled"
    detected = False
    attempted = False
    close_count = 0
    confirmed = False
    message = "Huatai post-login notice dismissal is disabled."
    if enabled:
        try:
            driver = (driver_factory or HuataiDesktopNoticeDriver)(config)
            detected = True
            driver.close_once()
            attempted = True
            close_count = 1
            notice = huatai_notice_config(config)
            timeout_seconds = max(float(notice.get("confirmation_timeout_seconds", 3)), 0.0)
            poll_seconds = max(float(notice.get("confirmation_poll_seconds", 0.1)), 0.05)
            confirmed = driver.wait_closed(timeout_seconds, poll_seconds)
            if not confirmed:
                raise RuntimeError("Huatai post-login notice remained visible after one WM_CLOSE")
            status = "dismissed"
            message = "The uniquely identified Huatai post-login notice was closed once."
        except NoticeNotPresent:
            status = "not_present"
            message = "No Huatai post-login notice is visible."
        except Exception as exc:
            status = "blocked"
            message = f"{type(exc).__name__}: {exc}"
    audit_path = _write_notice_audit(
        config,
        status=status,
        enabled=enabled,
        detected=detected,
        attempted=attempted,
        close_count=close_count,
        dismissal_confirmed=confirmed,
        message=message,
    )
    return HuataiNoticeResult(
        status,
        enabled,
        detected,
        attempted,
        close_count,
        confirmed,
        message,
        audit_path,
    )


class HuataiDesktopNoticeDriver:
    """Strict post-login notice driver; sends one WM_CLOSE to one exact dialog."""

    def __init__(self, config: dict[str, Any]) -> None:
        from pywinauto import Desktop

        executable = configured_huatai_client_executable(config)
        _, integrity_ok = huatai_client_integrity(config, executable)
        if executable is None or not integrity_ok:
            raise RuntimeError("configured Huatai executable is missing or failed SHA-256 validation")
        pids = self._pinned_process_ids(executable)
        if len(pids["hexin.exe"]) != 1 or len(pids["xiadan.exe"]) != 1:
            raise RuntimeError(
                "expected one pinned Huatai market process and one trading process; "
                f"found hexin={len(pids['hexin.exe'])}, xiadan={len(pids['xiadan.exe'])}"
            )
        windows = [
            window
            for window in Desktop(backend="win32").windows(visible_only=True)
            if int(window.process_id()) in pids["xiadan.exe"]
            and str(window.element_info.class_name or "") == NOTICE_DIALOG_CLASS
        ]
        candidates = [window for window in windows if self._matches_notice_contract(window)]
        if not candidates:
            if windows:
                raise RuntimeError(
                    f"Huatai #32770 dialog is visible but does not match the post-login notice contract ({len(windows)})"
                )
            raise NoticeNotPresent("Huatai post-login notice is not present")
        if len(candidates) != 1:
            raise RuntimeError(f"expected one Huatai post-login notice, found {len(candidates)}")
        self.handle = int(candidates[0].handle)

    @staticmethod
    def _pinned_process_ids(executable: Path) -> dict[str, set[int]]:
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
                process_path = Path(win32process.GetModuleFileNameEx(handle, 0)).resolve()
                if process_path == executable.resolve():
                    result["hexin.exe"].add(int(pid))
                elif process_path.name.lower() == "xiadan.exe" and process_path.parent == executable.parent:
                    result["xiadan.exe"].add(int(pid))
            except Exception:
                continue
            finally:
                if handle is not None:
                    handle.Close()
        return result

    @classmethod
    def _matches_notice_contract(cls, window: Any) -> bool:
        controls = []
        for control in window.descendants():
            try:
                if control.is_visible() and control.is_enabled():
                    controls.append(control)
            except Exception:
                continue
        names = [str(control.element_info.name or "") for control in controls]
        classes = [str(control.element_info.class_name or "") for control in controls]
        return (
            names.count(NAVIGATION_NAME) == 1
            and names.count(NO_NEW_MESSAGE_NAME) == 1
            and names.count(TODAY_ONLY_NAME) == 1
            and classes.count(NOTICE_TAB_CLASS) == 1
        )

    def close_once(self) -> None:
        import win32con
        import win32gui

        if not win32gui.IsWindow(self.handle) or not win32gui.IsWindowVisible(self.handle):
            raise RuntimeError("Huatai post-login notice disappeared before WM_CLOSE")
        win32gui.PostMessage(self.handle, win32con.WM_CLOSE, 0, 0)

    def wait_closed(self, timeout_seconds: float, poll_seconds: float) -> bool:
        import win32gui

        deadline = time.monotonic() + timeout_seconds
        while True:
            if not win32gui.IsWindow(self.handle) or not win32gui.IsWindowVisible(self.handle):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(poll_seconds, max(deadline - time.monotonic(), 0.0)))
