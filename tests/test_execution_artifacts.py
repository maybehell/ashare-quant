import hashlib
import shutil
import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.execution_artifacts import (
    EXECUTION_ARTIFACT_FILES,
    invalidate_execution_artifacts,
)


class ExecutionArtifactInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.output = (
            Path.cwd()
            / "test_outputs"
            / "execution_artifacts"
            / self._testMethodName
        )
        shutil.rmtree(self.output, ignore_errors=True)
        self.output.mkdir(parents=True, exist_ok=True)
        self.config = {
            "paths": {"output_dir": str(self.output)},
            "strategy": {"name": "test"},
            "validation": {
                "active_execution_path": str(self.output / "active.json"),
                "execution_epoch_path": str(self.output / "epoch.json"),
            },
        }

    def test_all_current_execution_artifacts_are_quarantined_with_hashes(self):
        expected: dict[str, str] = {}
        for index, name in enumerate(EXECUTION_ARTIFACT_FILES):
            payload = f"{name}:{index}\n".encode("utf-8")
            (self.output / name).write_bytes(payload)
            expected[name] = hashlib.sha256(payload).hexdigest()
        intent = self.output / "auto_live_submission_intent.csv"
        intent.write_text("state\npending\n", encoding="utf-8")
        account = self.output / "account_state_snapshot.csv"
        account.write_text("cash\n1000\n", encoding="utf-8")

        paths = invalidate_execution_artifacts(
            self.config,
            reason="strategy_health_paused",
        )

        for name in EXECUTION_ARTIFACT_FILES:
            self.assertFalse((self.output / name).exists(), name)
            archived = paths[f"quarantined:{name}"]
            self.assertTrue(archived.is_file())
            self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(), expected[name])
        self.assertTrue(intent.is_file(), "Pending submission intent is recovery evidence and must remain visible.")
        self.assertTrue(account.is_file(), "Account evidence must never be moved with order artifacts.")
        audit = pd.read_csv(paths["order_artifact_invalidation.csv"])
        self.assertEqual(set(audit["status"]), {"quarantined"})
        self.assertEqual(set(audit["reason"]), {"strategy_health_paused"})
        self.assertEqual(len(audit), len(EXECUTION_ARTIFACT_FILES))
        manifest = pd.read_csv(paths["order_quarantine_manifest.csv"])
        self.assertEqual(set(manifest["sha256"]), set(expected.values()))

    def test_no_artifact_cycle_writes_auditable_safe_state(self):
        paths = invalidate_execution_artifacts(
            self.config,
            reason="new_cycle",
        )

        audit = pd.read_csv(paths["order_artifact_invalidation.csv"])
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.loc[0, "status"], "no_current_artifacts")
        self.assertEqual(audit.loc[0, "reason"], "new_cycle")
        self.assertNotIn("order_quarantine_manifest.csv", paths)


if __name__ == "__main__":
    unittest.main()
