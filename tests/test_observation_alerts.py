from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.observation_alerts import (
    build_observation_monitor_event,
    write_observation_alert_status,
)


class ObservationAlertTests(unittest.TestCase):
    def test_paused_strategy_builds_non_trading_warning(self):
        event = build_observation_monitor_event(
            pd.DataFrame([{"status": "completed"}]),
            pd.DataFrame([{"check": "strategy_health_status", "value": "pause_paper_orders"}]),
            pd.DataFrame(
                [
                    {
                        "decision": "observe_only",
                        "candidate_strategy_name": "robust_recovery",
                        "forward_days": 3,
                        "required_forward_days": 60,
                        "forward_excess_return": -0.0149,
                    }
                ]
            ),
            pd.DataFrame(),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "strategy_orders_paused")
        self.assertEqual(event["severity"], "warning")
        self.assertFalse(event["context"]["broker_submission_capability"])

    def test_broker_readonly_refresh_failure_has_priority(self):
        event = build_observation_monitor_event(
            pd.DataFrame([{"status": "completed"}]),
            pd.DataFrame([{"check": "strategy_health_status", "value": "pause_paper_orders"}]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame([{"status": "failed", "stage": "desktop_snapshot"}]),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "broker_readonly_refresh_failed")
        self.assertEqual(event["severity"], "critical")
        self.assertEqual(event["context"]["broker_refresh_stage"], "desktop_snapshot")
        self.assertFalse(event["context"]["broker_submission_capability"])

    def test_stale_lock_builds_critical_non_trading_alert(self):
        event = build_observation_monitor_event(
            pd.DataFrame([{"status": "stale_lock"}]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "daily_observation_failed")
        self.assertEqual(event["severity"], "critical")
        self.assertFalse(event["context"]["broker_submission_capability"])


    def test_formal_forward_selection_replaces_legacy_candidate_progress(self):
        event = build_observation_monitor_event(
            pd.DataFrame([{"status": "completed"}]),
            pd.DataFrame([{"check": "strategy_health_status", "value": "pause_paper_orders"}]),
            pd.DataFrame([{
                "decision": "do_not_promote_quarantined_dependency",
                "candidate_strategy_name": "legacy_quarantined",
            }]),
            pd.DataFrame(),
            pd.DataFrame([{
                "status": "collecting_forward_data",
                "candidate_count": 3,
                "structural_family_count": 2,
                "minimum_common_trading_days": 0,
                "required_common_trading_days": 60,
            }]),
        )

        self.assertEqual(event["event_type"], "strategy_orders_paused")
        self.assertEqual(event["context"]["forward_selection_status"], "collecting_forward_data")
        self.assertEqual(event["context"]["formal_candidate_count"], "3")
        self.assertEqual(event["context"]["structural_family_count"], "2")
        self.assertIn("共同前向=0/60", event["message"])

    def test_invalid_formal_selection_has_priority_over_paused_strategy(self):
        event = build_observation_monitor_event(
            pd.DataFrame([{"status": "completed"}]),
            pd.DataFrame([{"check": "strategy_health_status", "value": "pause_paper_orders"}]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame([{
                "status": "invalid_evidence",
                "candidate_count": 3,
                "structural_family_count": 2,
            }]),
        )

        self.assertEqual(event["event_type"], "forward_candidate_selection_invalid")
        self.assertEqual(event["severity"], "critical")
    def test_identical_delivered_warning_is_suppressed(self):
        root = Path.cwd() / "test_outputs" / "observation_alert_dedupe"
        strategy = root / "strategy"
        live = root / "live"
        strategy.mkdir(parents=True, exist_ok=True)
        live.mkdir(parents=True, exist_ok=True)
        self.clear_alert_files(live)
        self.write_strategy_frames(strategy)
        calls = []

        def fake_sender(config, **kwargs):
            calls.append(kwargs)
            return {}

        config = {"paths": {"output_dir": str(live)}}
        first = write_observation_alert_status(config, strategy, sender=fake_sender)
        first_status = pd.read_csv(first["observation_alert_status.csv"])
        second = write_observation_alert_status(config, strategy, sender=fake_sender)
        second_status = pd.read_csv(second["observation_alert_status.csv"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(first_status.loc[0, "status"], "delivered")
        self.assertEqual(second_status.loc[0, "status"], "suppressed_duplicate")
        self.assertTrue((live / "observation_alert_state.csv").exists())

    def test_pending_delivery_retries_without_advancing_state(self):
        root = Path.cwd() / "test_outputs" / "observation_alert_retry"
        strategy = root / "strategy"
        live = root / "live"
        strategy.mkdir(parents=True, exist_ok=True)
        live.mkdir(parents=True, exist_ok=True)
        self.clear_alert_files(live)
        self.write_strategy_frames(strategy)
        calls = []

        def pending_sender(config, **kwargs):
            calls.append(kwargs)
            outbox = live / "auto_live_alert_outbox.csv"
            pd.DataFrame(
                [{"alert_id": kwargs["alert_id"], "status": "pending"}]
            ).to_csv(outbox, index=False)
            return {"auto_live_alert_outbox.csv": outbox}

        config = {"paths": {"output_dir": str(live)}}
        write_observation_alert_status(config, strategy, sender=pending_sender)
        write_observation_alert_status(config, strategy, sender=pending_sender)

        status = pd.read_csv(live / "observation_alert_status.csv")
        self.assertEqual(len(calls), 2)
        self.assertEqual(status.loc[0, "status"], "pending_retry")
        self.assertFalse((live / "observation_alert_state.csv").exists())

    def test_clear_state_sends_recovery_once(self):
        root = Path.cwd() / "test_outputs" / "observation_alert_recovery"
        strategy = root / "strategy"
        live = root / "live"
        strategy.mkdir(parents=True, exist_ok=True)
        live.mkdir(parents=True, exist_ok=True)
        self.clear_alert_files(live)
        pd.DataFrame([{"status": "completed"}]).to_csv(strategy / "daily_observation_summary.csv", index=False)
        pd.DataFrame(
            [{"check": "strategy_health_status", "value": "paper_only_observe"}]
        ).to_csv(strategy / "strategy_health.csv", index=False)
        pd.DataFrame(
            [{"fingerprint": "old", "active": True, "event_type": "strategy_orders_paused", "severity": "warning"}]
        ).to_csv(live / "observation_alert_state.csv", index=False)
        calls = []

        def fake_sender(config, **kwargs):
            calls.append(kwargs)
            return {}

        write_observation_alert_status({"paths": {"output_dir": str(live)}}, strategy, sender=fake_sender)
        write_observation_alert_status({"paths": {"output_dir": str(live)}}, strategy, sender=fake_sender)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["event_type"], "observation_recovered")


    def test_raw_task_failure_takes_priority_over_stale_summary(self):
        root = Path.cwd() / "test_outputs" / "observation_alert_raw_failure"
        strategy = root / "strategy"
        live = root / "live"
        strategy.mkdir(parents=True, exist_ok=True)
        live.mkdir(parents=True, exist_ok=True)
        self.clear_alert_files(live)
        pd.DataFrame([{"status": "failed"}]).to_csv(strategy / "daily_observation_status.csv", index=False)
        pd.DataFrame([{"status": "completed"}]).to_csv(strategy / "daily_observation_summary.csv", index=False)
        pd.DataFrame(
            [{"check": "strategy_health_status", "value": "healthy"}]
        ).to_csv(strategy / "strategy_health.csv", index=False)
        pd.DataFrame(columns=["check", "passed", "severity"]).to_csv(strategy / "data_source_health.csv", index=False)
        calls = []

        def fake_sender(config, **kwargs):
            calls.append(kwargs)
            return {}

        write_observation_alert_status({"paths": {"output_dir": str(live)}}, strategy, sender=fake_sender)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["event_type"], "daily_observation_failed")
    @staticmethod
    def clear_alert_files(live: Path):
        for name in ("observation_alert_status.csv", "observation_alert_state.csv", "auto_live_alert_outbox.csv"):
            path = live / name
            if path.exists():
                path.unlink()

    @staticmethod
    def write_strategy_frames(strategy: Path):
        pd.DataFrame([{"status": "completed"}]).to_csv(strategy / "daily_observation_summary.csv", index=False)
        pd.DataFrame(
            [{"check": "strategy_health_status", "value": "pause_paper_orders"}]
        ).to_csv(strategy / "strategy_health.csv", index=False)
        pd.DataFrame(
            [
                {
                    "decision": "observe_only",
                    "candidate_strategy_name": "robust_recovery",
                    "forward_days": 3,
                    "required_forward_days": 60,
                    "forward_excess_return": -0.0149,
                }
            ]
        ).to_csv(strategy / "strategy_promotion_decision.csv", index=False)
        pd.DataFrame(columns=["check", "passed", "severity"]).to_csv(
            strategy / "data_source_health.csv", index=False
        )


if __name__ == "__main__":
    unittest.main()
