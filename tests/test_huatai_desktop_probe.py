from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "huatai_desktop_probe.py"
SPEC = importlib.util.spec_from_file_location("huatai_desktop_probe", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class HuataiDesktopProbeTests(unittest.TestCase):
    def test_redacts_account_numbers_amounts_and_email(self):
        value = probe.redact_text("account 123456789 amount 123,456.78 me@example.com")
        self.assertNotIn("123456789", value)
        self.assertNotIn("123,456.78", value)
        self.assertNotIn("me@example.com", value)
        self.assertIn("12***89", value)
        self.assertIn("[amount]", value)

    def test_sensitive_control_is_fully_redacted(self):
        self.assertEqual(probe.redact_text("secret", label="资金密码"), "[redacted-sensitive-control]")

    def test_normal_labels_are_preserved(self):
        self.assertEqual(probe.redact_text("资金股份"), "资金股份")


if __name__ == "__main__":
    unittest.main()
