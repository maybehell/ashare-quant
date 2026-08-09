from datetime import datetime, timezone
from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.observation_progress import (
    build_observation_progress,
    capture_observation_snapshot,
    observation_progress_blockers,
    write_observation_progress,
)


NOW = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)


def snapshot(*, candidate_id="candidate-a", date="2026-07-15", forward=3, shadow=1, output="out"):
    return pd.DataFrame(
        [
            {
                "output_dir": output,
                "strategy_name": "strategy-a",
                "candidate_id": candidate_id,
                "latest_market_date": date,
                "forward_days": forward,
                "shadow_signals": shadow,
            }
        ]
    )


class ObservationProgressTests(unittest.TestCase):
    def test_market_and_forward_progress_pass(self):
        progress, audit = build_observation_progress(
            snapshot(),
            snapshot(date="2026-07-16", forward=4),
            now=NOW,
        )

        self.assertEqual(progress.loc[0, "status"], "progressed")
        self.assertEqual(int(progress.loc[0, "forward_days_delta"]), 1)
        self.assertEqual(observation_progress_blockers(audit), [])

    def test_same_market_day_is_valid_idempotent_run(self):
        progress, audit = build_observation_progress(snapshot(), snapshot(), now=NOW)

        self.assertEqual(progress.loc[0, "status"], "no_new_market_day")
        self.assertTrue(audit["passed"].all())

    def test_forward_stall_after_new_market_day_blocks_task(self):
        progress, audit = build_observation_progress(
            snapshot(),
            snapshot(date="2026-07-16", forward=3),
            now=NOW,
        )

        self.assertEqual(progress.loc[0, "status"], "forward_evidence_stalled")
        self.assertEqual(observation_progress_blockers(audit), ["observation_forward_evidence_progress"])

    def test_candidate_identity_change_cannot_inherit_old_evidence(self):
        progress, audit = build_observation_progress(
            snapshot(),
            snapshot(candidate_id="candidate-b", date="2026-07-16", forward=1),
            now=NOW,
        )

        self.assertEqual(progress.loc[0, "status"], "candidate_identity_changed")
        self.assertIn("observation_candidate_identity_stable", observation_progress_blockers(audit))

    def test_shadow_history_regression_blocks_task(self):
        progress, audit = build_observation_progress(
            snapshot(shadow=2),
            snapshot(shadow=1),
            now=NOW,
        )

        self.assertEqual(progress.loc[0, "status"], "shadow_evidence_regressed")
        self.assertIn("observation_shadow_evidence_not_regressed", observation_progress_blockers(audit))

    def test_capture_filters_shadow_rows_to_current_candidate(self):
        output = Path.cwd() / "test_outputs" / "observation_progress_capture"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "candidate_id": "candidate-a",
                    "latest_comparison_date": "2026-07-15",
                    "observed_forward_trading_days": 3,
                }
            ]
        ).to_csv(output / "live_candidate_forward_progress.csv", index=False)
        pd.DataFrame(
            [
                {"candidate_id": "candidate-a", "signal_hash": "a", "status": "ok", "observed_trading_days": 2},
                {"candidate_id": "candidate-a", "signal_hash": "b", "status": "not_observable", "observed_trading_days": 0},
                {"candidate_id": "candidate-b", "signal_hash": "c", "status": "ok", "observed_trading_days": 2},
            ]
        ).to_csv(output / "shadow_forward_audit.csv", index=False)

        captured = capture_observation_snapshot(
            [{"paths": {"output_dir": str(output)}, "strategy": {"name": "strategy-a"}}]
        )

        self.assertEqual(captured.loc[0, "candidate_id"], "candidate-a")
        self.assertEqual(int(captured.loc[0, "shadow_signals"]), 1)

    def test_writer_publishes_progress_and_audit(self):
        output = Path.cwd() / "test_outputs" / "observation_progress_writer"
        paths, audit = write_observation_progress(
            {"paths": {"output_dir": str(output)}},
            snapshot(output=str(output)),
            snapshot(output=str(output)),
        )

        self.assertTrue(paths["daily_observation_progress.csv"].exists())
        self.assertTrue(paths["daily_observation_progress_audit.csv"].exists())
        self.assertTrue(audit["passed"].all())


if __name__ == "__main__":
    unittest.main()
