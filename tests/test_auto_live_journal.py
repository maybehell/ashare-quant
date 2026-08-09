import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive_journal import (
    append_auto_live_event,
    auto_live_journal_status,
    record_auto_live_event,
    write_auto_live_journal_audit,
)


class AutoLiveJournalTests(unittest.TestCase):
    def fresh_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def config(self, output: Path, hmac_required: bool = False) -> dict:
        return {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "journal": {
                    "enabled": True,
                    "require_hmac": hmac_required,
                    "hmac_key_env": "TEST_JOURNAL_HMAC_KEY",
                }
            },
        }

    def test_empty_journal_is_ready_for_first_event(self):
        output = self.fresh_output("journal_empty")
        config = self.config(output)

        paths = write_auto_live_journal_audit(config)

        summary = pd.read_csv(paths["auto_live_journal_summary.csv"])
        audit = pd.read_csv(paths["auto_live_journal_audit.csv"])
        self.assertEqual(summary.loc[0, "status"], "empty_ready")
        self.assertTrue(audit["passed"].astype(bool).all())

    def test_multiple_events_form_verified_hash_chain(self):
        output = self.fresh_output("journal_chain")
        config = self.config(output)

        append_auto_live_event(config, "cycle_result", "cycle-1", {"status": "audit_complete"})
        append_auto_live_event(config, "submission_intent", "run-1", {"order_count": 2})
        paths = append_auto_live_event(config, "submission_result", "run-1", {"status": "submitted"})

        journal = pd.read_csv(paths["auto_live_event_journal.csv"], dtype=str, keep_default_na=False)
        summary = pd.read_csv(paths["auto_live_journal_summary.csv"])
        self.assertEqual(journal["sequence"].astype(int).tolist(), [1, 2, 3])
        self.assertEqual(journal.loc[1, "prev_event_hash"], journal.loc[0, "event_hash"])
        self.assertEqual(journal.loc[2, "prev_event_hash"], journal.loc[1, "event_hash"])
        self.assertEqual(summary.loc[0, "status"], "verified")

    def test_payload_tampering_fails_audit_and_blocks_new_append(self):
        output = self.fresh_output("journal_tamper")
        config = self.config(output)
        paths = append_auto_live_event(config, "cycle_result", "cycle-1", {"status": "completed"})
        journal_path = paths["auto_live_event_journal.csv"]
        journal = pd.read_csv(journal_path, dtype=str, keep_default_na=False)
        journal.loc[0, "payload_json"] = '{"status":"tampered"}'
        journal.to_csv(journal_path, index=False, encoding="utf-8-sig")

        audit_paths = write_auto_live_journal_audit(config)
        audit = pd.read_csv(audit_paths["auto_live_journal_audit.csv"]).set_index("check")

        self.assertFalse(bool(audit.loc["journal_payload_hashes_valid", "passed"]))
        with self.assertRaisesRegex(RuntimeError, "integrity failed before append"):
            append_auto_live_event(config, "cycle_result", "cycle-2", {"status": "completed"})

    def test_required_hmac_signs_and_verifies_every_event(self):
        output = self.fresh_output("journal_hmac")
        config = self.config(output, hmac_required=True)
        with patch.dict(os.environ, {"TEST_JOURNAL_HMAC_KEY": "j" * 40}, clear=False):
            paths = append_auto_live_event(config, "account_snapshot", "account-1", {"environment": "live"})
            passed, _, required = auto_live_journal_status(config)

        journal = pd.read_csv(paths["auto_live_event_journal.csv"], dtype=str, keep_default_na=False)
        self.assertTrue(required)
        self.assertTrue(passed)
        self.assertTrue(journal.loc[0, "hmac_sha256"])

    def test_required_hmac_missing_key_blocks_first_append(self):
        output = self.fresh_output("journal_hmac_missing")
        config = self.config(output, hmac_required=True)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "journal integrity failed before append"):
                append_auto_live_event(config, "cycle_result", "cycle-1", {"status": "blocked"})

        self.assertFalse((output / "auto_live_journal.lock").exists())

    def test_duplicate_event_key_is_rejected(self):
        output = self.fresh_output("journal_duplicate")
        config = self.config(output)
        append_auto_live_event(config, "cycle_result", "same-id", {"status": "one"})

        with self.assertRaisesRegex(RuntimeError, "duplicate.*event_key"):
            append_auto_live_event(config, "cycle_result", "same-id", {"status": "two"})

    def test_best_effort_append_failure_is_persisted_and_blocks_readiness(self):
        output = self.fresh_output("journal_append_error")
        config = self.config(output)
        (output / "auto_live_journal.lock").write_text("active", encoding="utf-8")

        paths = record_auto_live_event(config, "cycle_result", "cycle-1", {}, strict=False)
        errors = pd.read_csv(paths["auto_live_journal_errors.csv"])
        audit = pd.read_csv(paths["auto_live_journal_audit.csv"]).set_index("check")

        self.assertEqual(len(errors), 1)
        self.assertFalse(bool(errors.loc[0, "resolved"]))
        self.assertFalse(bool(audit.loc["journal_append_errors_clear", "passed"]))

    def test_existing_journal_lock_blocks_writer_and_audit(self):
        output = self.fresh_output("journal_lock")
        config = self.config(output)
        (output / "auto_live_journal.lock").write_text("active", encoding="utf-8")

        paths = write_auto_live_journal_audit(config)
        audit = pd.read_csv(paths["auto_live_journal_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["journal_lock_clear", "passed"]))
        with self.assertRaises(FileExistsError):
            append_auto_live_event(config, "cycle_result", "cycle-1", {})


if __name__ == "__main__":
    unittest.main()
