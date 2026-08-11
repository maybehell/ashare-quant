from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any, Callable, Protocol

import pandas as pd

from .config import project_path
from .huatai_client import configured_huatai_client_executable, huatai_client_integrity
from .io_utils import atomic_to_csv
from .live_secrets import read_live_secrets, write_live_secrets


ACCOUNT_COMBO_ID = "1011"
ACCOUNT_EDIT_ID = "1001"
PASSWORD_EDIT_ID = "1012"
LOGIN_BUTTON_ID = "1006"
LOGIN_BUTTON_NAME = "\u767b\u5f55"
LOGIN_PANEL_BUTTON_ID = "1008"
LOGIN_PANEL_BUTTON_NAME = "\u7528\u6237\u767b\u5f55"
HUATAI_LOGIN_AUDIT_COLUMNS = [
    "status",
    "attempted",
    "enabled",
    "credential_source",
    "account_field_verified",
    "login_click_count",
    "message",
    "generated_at",
]


@dataclass(frozen=True)
class HuataiLoginResult:
    status: str
    attempted: bool
    enabled: bool
    account_field_verified: bool
    login_click_count: int
    message: str
    audit_path: Path


class LoginDriver(Protocol):
    def write_account(self, value: str) -> None: ...
    def read_account(self) -> str: ...
    def write_password(self, value: str) -> None: ...
    def click_login_once(self) -> None: ...
    def clear_password(self) -> None: ...


def huatai_login_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("desktop_login", {})
    return value if isinstance(value, dict) else {}


def huatai_login_enabled(config: dict[str, Any]) -> bool:
    return bool(huatai_login_config(config).get("enabled", False))


def live_secret_path(config: dict[str, Any]) -> Path:
    security = config.get("security", {}) if isinstance(config.get("security"), dict) else {}
    value = str(security.get("live_secret_file", "") or "").strip()
    if not value:
        raise RuntimeError("security.live_secret_file is required for Huatai automatic login")
    return project_path(value)


def validate_huatai_credentials(account_id: object, password: object) -> tuple[str, str]:
    account = str(account_id or "").strip()
    secret = str(password or "")
    if not re.fullmatch(r"\d{6,20}", account):
        raise RuntimeError("Huatai account ID must contain 6 to 20 digits")
    if len(secret) < 6 or len(secret) > 64 or "\x00" in secret:
        raise RuntimeError("Huatai trading password must contain 6 to 64 characters")
    return account, secret


def enroll_huatai_credentials(config: dict[str, Any], account_id: object, password: object) -> Path:
    account, secret = validate_huatai_credentials(account_id, password)
    path = live_secret_path(config)
    payload = read_live_secrets(path) if path.exists() else {"schema_version": 1}
    huatai = payload.get("huatai", {}) if isinstance(payload.get("huatai"), dict) else {}
    payload["huatai"] = {**huatai, "account_id": account, "trading_password": secret}
    return write_live_secrets(path, payload)


def read_huatai_credentials(config: dict[str, Any]) -> tuple[str, str]:
    payload = read_live_secrets(live_secret_path(config))
    huatai = payload.get("huatai", {}) if isinstance(payload.get("huatai"), dict) else {}
    return validate_huatai_credentials(huatai.get("account_id"), huatai.get("trading_password"))


def _write_audit(
    config: dict[str, Any],
    *,
    status: str,
    attempted: bool,
    enabled: bool,
    account_field_verified: bool,
    login_click_count: int,
    message: str,
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "huatai_login_audit.csv"
    row = {
        "status": status,
        "attempted": attempted,
        "enabled": enabled,
        "credential_source": "windows_dpapi_current_user",
        "account_field_verified": account_field_verified,
        "login_click_count": login_click_count,
        "message": message,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    atomic_to_csv(pd.DataFrame([row], columns=HUATAI_LOGIN_AUDIT_COLUMNS), path, index=False, encoding="utf-8-sig")
    return path


def attempt_huatai_auto_login(
    config: dict[str, Any],
    *,
    driver_factory: Callable[[dict[str, Any]], LoginDriver] | None = None,
) -> HuataiLoginResult:
    enabled = huatai_login_enabled(config)
    status = "disabled"
    attempted = False
    account_verified = False
    click_count = 0
    message = "Huatai automatic login is disabled."
    driver: LoginDriver | None = None
    if enabled:
        try:
            account, password = read_huatai_credentials(config)
            driver = (driver_factory or HuataiDesktopLoginDriver)(config)
            driver.write_account(account)
            account_verified = driver.read_account().strip() == account
            if not account_verified:
                raise RuntimeError("Huatai account field readback mismatch")
            driver.write_password(password)
            attempted = True
            driver.click_login_once()
            click_count = 1
            status = "login_submitted"
            message = "One Huatai login click was submitted; broker snapshot confirmation is required."
        except Exception as exc:
            if driver is not None:
                try:
                    driver.clear_password()
                except Exception:
                    pass
            status = "blocked"
            message = f"{type(exc).__name__}: {exc}"
    audit_path = _write_audit(
        config,
        status=status,
        attempted=attempted,
        enabled=enabled,
        account_field_verified=account_verified,
        login_click_count=click_count,
        message=message,
    )
    return HuataiLoginResult(status, attempted, enabled, account_verified, click_count, message, audit_path)


class HuataiDesktopLoginDriver:
    """Strict login-only UIA driver; it has no order or ledger capability."""

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
        roots = [
            window for window in Desktop(backend="uia").windows(visible_only=True)
            if int(window.process_id()) in pids["hexin.exe"]
        ]
        candidates = []
        for root in roots:
            controls = self._visible_enabled_descendants(root)
            if self._has_exact_login_signature(controls):
                candidates.append((root, controls))
        if len(candidates) != 1:
            raise RuntimeError(f"expected one Huatai login root, found {len(candidates)}")
        self.root, controls = candidates[0]
        self.account = self._one(
            controls,
            control_type="Edit",
            automation_id=ACCOUNT_EDIT_ID,
            parent_automation_id=ACCOUNT_COMBO_ID,
        )
        self.password = self._one(controls, control_type="Edit", automation_id=PASSWORD_EDIT_ID)
        self.login_button = self._one(
            controls,
            control_type="Button",
            automation_id=LOGIN_BUTTON_ID,
            name=LOGIN_BUTTON_NAME,
        )
        unexpected_edits = [
            control for control in controls
            if self._control_type(control) == "Edit"
            and control is not self.account
            and control is not self.password
            and self._inside_root(control, self.root)
        ]
        if unexpected_edits:
            raise RuntimeError("unexpected visible login input detected; captcha or changed UI requires manual login")

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
    def _visible_enabled_descendants(cls, root: Any) -> list[Any]:
        result = []
        for control in root.descendants():
            try:
                if control.is_visible() and control.is_enabled():
                    result.append(control)
            except Exception:
                continue
        return result

    @staticmethod
    def _control_type(control: Any) -> str:
        return str(control.element_info.control_type or "")

    @staticmethod
    def _automation_id(control: Any) -> str:
        return str(control.element_info.automation_id or "")

    @classmethod
    def _matches(
        cls,
        control: Any,
        *,
        control_type: str,
        automation_id: str,
        name: str | None = None,
        parent_automation_id: str | None = None,
    ) -> bool:
        if cls._control_type(control) != control_type or cls._automation_id(control) != automation_id:
            return False
        if name is not None and str(control.element_info.name or "") != name:
            return False
        if parent_automation_id is not None:
            try:
                if cls._automation_id(control.parent()) != parent_automation_id:
                    return False
            except Exception:
                return False
        return True

    @classmethod
    def _one(cls, controls: list[Any], **criteria: Any) -> Any:
        matches = [control for control in controls if cls._matches(control, **criteria)]
        if len(matches) != 1:
            raise RuntimeError(f"expected one Huatai login control {criteria}, found {len(matches)}")
        return matches[0]

    @classmethod
    def _has_exact_login_signature(cls, controls: list[Any]) -> bool:
        required = [
            dict(control_type="Edit", automation_id=ACCOUNT_EDIT_ID, parent_automation_id=ACCOUNT_COMBO_ID),
            dict(control_type="Edit", automation_id=PASSWORD_EDIT_ID),
            dict(control_type="Button", automation_id=LOGIN_BUTTON_ID, name=LOGIN_BUTTON_NAME),
            dict(control_type="Button", automation_id=LOGIN_PANEL_BUTTON_ID, name=LOGIN_PANEL_BUTTON_NAME),
        ]
        return all(sum(cls._matches(control, **criteria) for control in controls) == 1 for criteria in required)

    @staticmethod
    def _inside_root(control: Any, root: Any) -> bool:
        try:
            child = control.rectangle()
            parent = root.rectangle()
            return child.left >= parent.left and child.top >= parent.top and child.right <= parent.right and child.bottom <= parent.bottom
        except Exception:
            return True

    def write_account(self, value: str) -> None:
        self.root.set_focus()
        self.account.set_edit_text(value)

    def read_account(self) -> str:
        return str(self.account.get_value() or "")

    def write_password(self, value: str) -> None:
        self.password.set_edit_text(value)

    def click_login_once(self) -> None:
        self.login_button.click_input()

    def clear_password(self) -> None:
        self.password.set_edit_text("")
