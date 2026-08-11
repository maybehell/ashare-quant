from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from ashare_quant.cli import run_huatai_read_only_refresh
from ashare_quant.huatai_login import (
    HuataiLoginResult,
    attempt_huatai_auto_login,
    enroll_huatai_credentials,
)
from ashare_quant.live_secrets import read_live_secrets, write_live_secrets


class FakeLoginDriver:
    def __init__(self, *, readback: str = "12345678") -> None:
        self.readback = readback
        self.account_writes: list[str] = []
        self.password_writes: list[str] = []
        self.clicks = 0
        self.clears = 0

    def write_account(self, value: str) -> None:
        self.account_writes.append(value)

    def read_account(self) -> str:
        return self.readback

    def write_password(self, value: str) -> None:
        self.password_writes.append(value)

    def click_login_once(self) -> None:
        self.clicks += 1

    def clear_password(self) -> None:
        self.clears += 1


class HuataiLoginTests(unittest.TestCase):
    def test_login_button_names_use_exact_unicode_contract(self):
        from ashare_quant.huatai_login import LOGIN_BUTTON_NAME, LOGIN_PANEL_BUTTON_NAME

        self.assertEqual(LOGIN_BUTTON_NAME, "\u767b\u5f55")
        self.assertEqual(LOGIN_PANEL_BUTTON_NAME, "\u7528\u6237\u767b\u5f55")

    def config(self, name: str, *, enabled: bool = True) -> dict:
        root = Path("test_outputs") / name
        root.mkdir(parents=True, exist_ok=True)
        return {
            "paths": {"output_dir": str(root)},
            "desktop_login": {"enabled": enabled},
            "security": {"live_secret_file": str(root / "credentials.dpapi")},
        }

    def test_disabled_login_never_reads_credentials_or_creates_driver(self):
        config = self.config(self._testMethodName, enabled=False)
        factory = Mock()

        result = attempt_huatai_auto_login(config, driver_factory=factory)

        self.assertEqual(result.status, "disabled")
        self.assertFalse(result.attempted)
        factory.assert_not_called()

    def test_enrollment_preserves_existing_secret_sections_and_encrypts_password(self):
        config = self.config(self._testMethodName)
        path = Path(config["security"]["live_secret_file"])
        write_live_secrets(path, {"schema_version": 1, "smtp": {"password": "mail-secret"}})

        enroll_huatai_credentials(config, "12345678", "trade-secret")

        payload = read_live_secrets(path)
        self.assertEqual(payload["smtp"]["password"], "mail-secret")
        self.assertEqual(payload["huatai"]["account_id"], "12345678")
        self.assertEqual(payload["huatai"]["trading_password"], "trade-secret")
        raw = path.read_bytes()
        self.assertNotIn(b"trade-secret", raw)
        self.assertNotIn(b"12345678", raw)

    def test_unique_login_contract_writes_credentials_and_clicks_exactly_once(self):
        config = self.config(self._testMethodName)
        enroll_huatai_credentials(config, "12345678", "trade-secret")
        driver = FakeLoginDriver()

        result = attempt_huatai_auto_login(config, driver_factory=lambda _: driver)

        self.assertEqual(result.status, "login_submitted")
        self.assertTrue(result.attempted)
        self.assertEqual(driver.account_writes, ["12345678"])
        self.assertEqual(driver.password_writes, ["trade-secret"])
        self.assertEqual(driver.clicks, 1)
        audit = pd.read_csv(result.audit_path)
        audit_text = audit.to_csv(index=False)
        self.assertNotIn("trade-secret", audit_text)
        self.assertNotIn("12345678", audit_text)

    def test_account_readback_mismatch_blocks_before_password_or_click(self):
        config = self.config(self._testMethodName)
        enroll_huatai_credentials(config, "12345678", "trade-secret")
        driver = FakeLoginDriver(readback="87654321")

        result = attempt_huatai_auto_login(config, driver_factory=lambda _: driver)

        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.attempted)
        self.assertEqual(driver.password_writes, [])
        self.assertEqual(driver.clicks, 0)
        self.assertEqual(driver.clears, 1)

    def test_missing_password_fails_closed_before_desktop_access(self):
        config = self.config(self._testMethodName)
        write_live_secrets(
            config["security"]["live_secret_file"],
            {"schema_version": 1, "huatai": {"account_id": "12345678"}},
        )
        factory = Mock()

        result = attempt_huatai_auto_login(config, driver_factory=factory)

        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.attempted)
        factory.assert_not_called()
        self.assertNotIn("12345678", result.message)

    def test_read_only_refresh_invokes_blocked_login_only_once(self):
        config = self.config(self._testMethodName)
        snapshot_path = Path(config["paths"]["output_dir"]) / "snapshot.json"
        snapshot_path.write_text(
            '{"status":"failed","message":"login required","diagnostics":{"stage":"broker_authentication_required"}}',
            encoding="utf-8",
        )
        audit_path = Path(config["paths"]["output_dir"]) / "huatai_login_audit.csv"
        login_result = HuataiLoginResult(
            "blocked", False, True, False, 0, "credential missing", audit_path
        )
        completed = subprocess.CompletedProcess(
            [], 1, stdout="Huatai trading authentication is required", stderr=""
        )

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.write_huatai_client_status", return_value=Path("client.csv")),
            patch("ashare_quant.cli.attempt_huatai_auto_login", return_value=login_result) as login,
            patch("ashare_quant.cli.invalidate_huatai_broker_account_evidence", return_value={}),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=Path("canary.csv")),
            patch("ashare_quant.cli.regenerate_report", return_value=Path("report.html")),
        ):
            with self.assertRaisesRegex(RuntimeError, "credential missing"):
                run_huatai_read_only_refresh(config)

        login.assert_called_once_with(config)
        status = pd.read_csv(Path(config["paths"]["output_dir"]) / "huatai_read_only_refresh.csv")
        self.assertEqual(status.loc[0, "login_status"], "blocked")
        self.assertFalse(bool(status.loc[0, "login_attempted"]))


if __name__ == "__main__":
    unittest.main()
