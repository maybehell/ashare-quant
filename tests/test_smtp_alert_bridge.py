from __future__ import annotations

from email.message import EmailMessage
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from ashare_quant.live_secrets import write_live_secrets


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smtp_alert_bridge.py"
SPEC = importlib.util.spec_from_file_location("smtp_alert_bridge", SCRIPT)
assert SPEC and SPEC.loader
smtp_bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smtp_bridge)


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list[object] = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, **kwargs):
        self.calls.append(("starttls", kwargs))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send_message", message))


class SmtpAlertBridgeTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances.clear()
        self.environment = {
            "ASHARE_QUANT_SMTP_HOST": "smtp.example.com",
            "ASHARE_QUANT_SMTP_USERNAME": "sender@example.com",
            "ASHARE_QUANT_SMTP_PASSWORD": "app-password",
            "ASHARE_QUANT_SMTP_FROM": "sender@example.com",
            "ASHARE_QUANT_SMTP_TO": "first@example.com; second@example.com",
        }

    def test_settings_default_to_verified_starttls(self):
        with patch.dict("os.environ", self.environment, clear=True):
            settings = smtp_bridge.smtp_settings()
        self.assertEqual(settings["security"], "starttls")
        self.assertEqual(settings["port"], 587)
        self.assertTrue(settings["verify_tls"])
        self.assertEqual(settings["recipients"], ["first@example.com", "second@example.com"])

    def test_send_uses_tls_login_and_message(self):
        with patch.dict("os.environ", self.environment, clear=True):
            settings = smtp_bridge.smtp_settings()
        message = EmailMessage()
        message.set_content("test")
        smtp_bridge.send_message(message, settings, smtp_factory=FakeSMTP)
        instance = FakeSMTP.instances[-1]
        self.assertEqual(instance.kwargs["host"], "smtp.example.com")
        self.assertEqual(instance.calls[0], "ehlo")
        self.assertEqual(instance.calls[2], "ehlo")
        self.assertEqual(instance.calls[3], ("login", "sender@example.com", "app-password"))
        self.assertEqual(instance.calls[4][0], "send_message")

    def test_message_has_safe_subject_and_truncated_context(self):
        with patch.dict("os.environ", self.environment, clear=True):
            settings = smtp_bridge.smtp_settings()
        request = {
            "mode": "deliver",
            "event_type": "failure\r\nBcc: attacker@example.com",
            "severity": "critical",
            "message": "cycle stopped",
            "context": {"detail": "x" * 7000},
            "run_id": "run-1",
        }
        message = smtp_bridge.build_message(request, settings)
        self.assertNotIn("\n", message["Subject"])
        self.assertIn("[truncated]", message.get_content())

    def test_dpapi_secret_file_supplies_qq_settings(self):
        path = Path("test_outputs") / self._testMethodName / "credentials.dpapi"
        write_live_secrets(path, {"smtp": {
            "host": "smtp.qq.com", "port": 465, "security": "ssl",
            "username": "test-user@example.invalid", "password": "authorization-code",
            "sender": "test-user@example.invalid", "recipients": ["test-user@example.invalid"],
            "verify_tls": True,
        }})
        with patch.dict("os.environ", {}, clear=True):
            settings = smtp_bridge.smtp_settings(path)
        self.assertEqual(settings["host"], "smtp.qq.com")
        self.assertEqual(settings["security"], "ssl")
        self.assertEqual(settings["password"], "authorization-code")

    def test_missing_password_fails_without_default(self):
        environment = dict(self.environment)
        del environment["ASHARE_QUANT_SMTP_PASSWORD"]
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ASHARE_QUANT_SMTP_PASSWORD"):
                smtp_bridge.smtp_settings()


if __name__ == "__main__":
    unittest.main()
