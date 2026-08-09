from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.autolive_confirmation import (
    consume_action_confirmation,
    issue_action_confirmation,
    validate_action_confirmation,
)
from ashare_quant.validation import validate_config


class AutoLiveConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = Path.cwd() / "test_outputs" / "action_confirmation"
        self.output.mkdir(parents=True, exist_ok=True)
        self.path = self.output / "auto_live_action_confirmation.csv"
        self.path.unlink(missing_ok=True)
        self.config = {
            "paths": {"output_dir": str(self.output)},
            "auto_live": {
                "action_confirmation": {
                    "required_before_submit": True,
                    "ttl_seconds": 600,
                }
            },
        }
        self.now = datetime(2026, 7, 13, 1, 30, tzinfo=timezone.utc)

    def test_token_is_hashed_bound_and_single_use(self):
        issued = issue_action_confirmation(
            self.config,
            tickets_sha256="tickets-a",
            auto_live_config_sha256="config-a",
            now=self.now,
        )
        persisted = pd.read_csv(issued.path, dtype=str, keep_default_na=False)
        self.assertNotIn(issued.token, issued.path.read_text(encoding="utf-8-sig"))
        self.assertEqual(len(persisted.loc[0, "token_sha256"]), 64)

        valid, reason = validate_action_confirmation(
            self.config,
            issued.token,
            tickets_sha256="tickets-a",
            auto_live_config_sha256="config-a",
            now=self.now + timedelta(seconds=30),
        )
        self.assertTrue(valid)
        self.assertEqual(reason, "valid")

        consumed, reason, _ = consume_action_confirmation(
            self.config,
            issued.token,
            tickets_sha256="tickets-a",
            auto_live_config_sha256="config-a",
            now=self.now + timedelta(seconds=31),
        )
        self.assertTrue(consumed)
        self.assertEqual(reason, "consumed")
        replay_valid, replay_reason = validate_action_confirmation(
            self.config,
            issued.token,
            tickets_sha256="tickets-a",
            auto_live_config_sha256="config-a",
            now=self.now + timedelta(seconds=32),
        )
        self.assertFalse(replay_valid)
        self.assertEqual(replay_reason, "action_confirmation_already_consumed")

    def test_changed_orders_config_and_expiry_are_rejected(self):
        issued = issue_action_confirmation(
            self.config,
            tickets_sha256="tickets-a",
            auto_live_config_sha256="config-a",
            now=self.now,
        )
        cases = [
            (issued.token, "tickets-b", "config-a", self.now, "action_confirmation_tickets_changed"),
            (issued.token, "tickets-a", "config-b", self.now, "action_confirmation_config_changed"),
            (issued.token, "tickets-a", "config-a", self.now + timedelta(seconds=601), "action_confirmation_expired"),
            ("wrong-token", "tickets-a", "config-a", self.now, "action_confirmation_token_mismatch"),
        ]
        for token, tickets_hash, config_hash, now, expected in cases:
            with self.subTest(expected=expected):
                valid, reason = validate_action_confirmation(
                    self.config,
                    token,
                    tickets_sha256=tickets_hash,
                    auto_live_config_sha256=config_hash,
                    now=now,
                )
                self.assertFalse(valid)
                self.assertEqual(reason, expected)

    def test_confirmation_ttl_config_is_bounded(self):
        config = {"auto_live": {"action_confirmation": {"required_before_submit": True, "ttl_seconds": 901}}}
        self.assertIn(
            "auto_live.action_confirmation.ttl_seconds must be between 30 and 900",
            validate_config(config),
        )


if __name__ == "__main__":
    unittest.main()
