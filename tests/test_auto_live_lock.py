import json
import shutil
import sys
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import (
    auto_live_config_sha256,
    load_auto_live_query_source,
    query_auto_live_orders,
    run_auto_live,
    write_auto_live_submission_intent,
    write_auto_live_submit_lock_audit,
)


class AutoLiveSubmitLockAuditTests(unittest.TestCase):
    def fresh_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        return output

    def test_no_lock_is_clear(self):
        output = self.fresh_output("auto_live_lock_clear")
        path = write_auto_live_submit_lock_audit({"paths": {"output_dir": str(output)}})
        audit = pd.read_csv(path)

        self.assertTrue(audit["passed"].astype(bool).all())
        clear = audit[audit["check"] == "submit_lock_clear"].iloc[0]
        self.assertEqual(clear["value"], "clear")

    def test_recent_lock_blocks_without_being_declared_stale(self):
        output = self.fresh_output("auto_live_lock_recent")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {"submit_lock_stale_seconds": 900}}
        payload = {"run_id": "run-recent", "created_at": "2026-07-10T10:00:00"}
        (output / "auto_live_submit.lock").write_text(json.dumps(payload), encoding="utf-8")

        path = write_auto_live_submit_lock_audit(config, now=datetime(2026, 7, 10, 10, 5, 0))
        audit = pd.read_csv(path)
        clear = audit[audit["check"] == "submit_lock_clear"].iloc[0]

        self.assertFalse(bool(clear["passed"]))
        self.assertIn("active_or_recent", clear["value"])
        self.assertIn("不要启动第二次提交", clear["next_action"])
        self.assertTrue(bool(audit[audit["check"] == "submit_lock_metadata_valid"]["passed"].iloc[0]))

    def test_stale_lock_requires_intent_and_terminal_evidence(self):
        output = self.fresh_output("auto_live_lock_stale")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {"submit_lock_stale_seconds": 60}}
        payload = {"run_id": "run-stale", "created_at": "2026-07-10T09:00:00"}
        (output / "auto_live_submit.lock").write_text(json.dumps(payload), encoding="utf-8")

        path = write_auto_live_submit_lock_audit(config, now=datetime(2026, 7, 10, 10, 0, 0))
        audit = pd.read_csv(path)
        checks = audit.set_index("check")

        self.assertIn("stale", checks.loc["submit_lock_clear", "value"])
        self.assertFalse(bool(checks.loc["submit_lock_intent_traceable", "passed"]))
        self.assertFalse(bool(checks.loc["submit_lock_terminal_traceable", "passed"]))
        self.assertIn("auto-live-query", checks.loc["submit_lock_terminal_traceable", "next_action"])

    def test_completed_submit_persists_terminal_intent_and_clears_lock(self):
        output = self.fresh_output("auto_live_intent_terminal")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame(
            [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]
        ).to_csv(output / "rpa_orders.csv", index=False)

        paths = run_auto_live(config)
        intent = pd.read_csv(paths["auto_live_submission_intent.csv"])
        lock_audit = pd.read_csv(paths["auto_live_submit_lock_audit.csv"])

        self.assertEqual(intent["state"].iloc[0], "submitted")
        self.assertEqual(intent["auto_live_config_sha256"].iloc[0], auto_live_config_sha256(config))
        self.assertFalse((output / "auto_live_submit.lock").exists())
        checks = lock_audit.set_index("check")
        self.assertTrue(bool(checks.loc["submit_lock_clear", "passed"]))
        self.assertFalse(bool(checks.loc["prior_submission_resolved", "passed"]))

    def test_unresolved_prior_batch_blocks_different_tickets(self):
        output = self.fresh_output("auto_live_prior_batch")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame(
            [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]
        ).to_csv(output / "rpa_orders.csv", index=False)
        first_paths = run_auto_live(config)
        first_run = pd.read_csv(first_paths["auto_live_run.csv"])
        first_results = pd.read_csv(first_paths["auto_live_order_results.csv"])
        pd.DataFrame(
            [{"ticket_id": "t2", "symbol": "000001", "side": "buy", "quantity": 100, "limit_price": 12.0, "status": "ready", "date": "2026-07-10"}]
        ).to_csv(output / "rpa_orders.csv", index=False)

        with self.assertRaisesRegex(RuntimeError, "prior_auto_live_batch_unresolved"):
            run_auto_live(config)
        blocked = pd.read_csv(output / "auto_live_blocked_attempt.csv")
        preserved_run = pd.read_csv(output / "auto_live_run.csv")
        preserved_results = pd.read_csv(output / "auto_live_order_results.csv")
        self.assertEqual(blocked["blocked_reason"].iloc[0], "prior_auto_live_batch_unresolved")
        self.assertEqual(preserved_run["run_id"].iloc[0], first_run["run_id"].iloc[0])
        self.assertEqual(preserved_results["ticket_id"].tolist(), first_results["ticket_id"].tolist())

    def test_terminal_broker_status_resolves_prior_batch(self):
        output = self.fresh_output("auto_live_prior_terminal")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame(
            [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]
        ).to_csv(output / "rpa_orders.csv", index=False)
        paths = run_auto_live(config)
        run_id = str(pd.read_csv(paths["auto_live_run.csv"])["run_id"].iloc[0])
        pd.DataFrame(
            [{"source_run_id": run_id, "ticket_id": "t1", "broker_status": "filled"}]
        ).to_csv(output / "auto_live_order_status.csv", index=False)

        audit = pd.read_csv(write_auto_live_submit_lock_audit(config)).set_index("check")
        self.assertTrue(bool(audit.loc["prior_submission_resolved", "passed"]))

    def test_unfinished_intent_blocks_same_dedupe_even_without_lock(self):
        output = self.fresh_output("auto_live_intent_dedupe")
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "enabled": True,
                "broker_adapter": "mock",
                "allow_mock_adapter": True,
                "allow_broker_submit": True,
            },
        }
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(output / "execution_modes.csv", index=False)
        tickets = pd.DataFrame(
            [{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}]
        )
        tickets.to_csv(output / "rpa_orders.csv", index=False)
        from ashare_quant.autolive import auto_live_dedupe_key, tickets_sha256

        ticket_hash = tickets_sha256(pd.read_csv(output / "rpa_orders.csv"))
        config_hash = auto_live_config_sha256(config)
        dedupe_key = auto_live_dedupe_key(ticket_hash, config_hash)
        write_auto_live_submission_intent(
            config,
            run_id="crashed-run",
            state="submitting",
            adapter="mock",
            order_count=1,
            tickets_hash=ticket_hash,
            auto_hash=config_hash,
            dedupe_key=dedupe_key,
            message="simulated crash after intent",
        )

        with self.assertRaisesRegex(RuntimeError, "duplicate_auto_live_submission"):
            run_auto_live(config)
        blocked = pd.read_csv(output / "auto_live_blocked_attempt.csv")
        self.assertEqual(blocked["blocked_reason"].iloc[0], "duplicate_auto_live_submission")
        self.assertFalse((output / "auto_live_submission_ledger.csv").exists())

    def test_query_source_rebuilds_interrupted_batch_from_persisted_request(self):
        output = self.fresh_output("auto_live_query_source_recovery")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {"broker_adapter": "command"}}
        orders = [
            {"ticket_id": "t-recover", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}
        ]
        ticket_frame = pd.DataFrame(orders)
        from ashare_quant.autolive import auto_live_dedupe_key, tickets_sha256

        ticket_hash = tickets_sha256(ticket_frame)
        config_hash = auto_live_config_sha256(config)
        run_id = "interrupted-run"
        write_auto_live_submission_intent(
            config,
            run_id=run_id,
            state="submitting",
            adapter="command",
            order_count=1,
            tickets_hash=ticket_hash,
            auto_hash=config_hash,
            dedupe_key=auto_live_dedupe_key(ticket_hash, config_hash),
            message="adapter interrupted",
        )
        request = {
            "schema_version": "ashare_quant.command_adapter.v1",
            "mode": "submit_orders",
            "run_id": run_id,
            "tickets_sha256": ticket_hash,
            "orders": orders,
        }
        (output / f"auto_live_command_request_{run_id}.json").write_text(json.dumps(request), encoding="utf-8")
        pd.DataFrame(
            [{"run_id": "older-run", "date": "2026-07-09", "ticket_id": "old", "symbol": "000001", "side": "buy", "quantity": 100, "limit_price": 8.0, "status": "submitted", "broker_order_id": "old-id", "message": "old"}]
        ).to_csv(output / "auto_live_order_results.csv", index=False)

        source = load_auto_live_query_source(output)
        self.assertEqual(source["run_id"].tolist(), [run_id])
        self.assertEqual(source["ticket_id"].tolist(), ["t-recover"])
        self.assertEqual(source["status"].tolist(), ["unknown"])
        self.assertTrue(source["broker_order_id"].fillna("").eq("").all())

    def test_query_source_rejects_result_identity_mismatch_and_tampered_request(self):
        output = self.fresh_output("auto_live_query_source_identity")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {"broker_adapter": "command"}}
        orders = [
            {"ticket_id": "t-bound", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}
        ]
        from ashare_quant.autolive import auto_live_dedupe_key, tickets_sha256

        ticket_hash = tickets_sha256(pd.DataFrame(orders))
        config_hash = auto_live_config_sha256(config)
        run_id = "identity-run"
        write_auto_live_submission_intent(
            config,
            run_id=run_id,
            state="submission_uncertain",
            adapter="command",
            order_count=1,
            tickets_hash=ticket_hash,
            auto_hash=config_hash,
            dedupe_key=auto_live_dedupe_key(ticket_hash, config_hash),
            message="identity test",
        )
        request_path = output / f"auto_live_command_request_{run_id}.json"
        request = {
            "schema_version": "ashare_quant.command_adapter.v1",
            "mode": "submit_orders",
            "run_id": run_id,
            "tickets_sha256": ticket_hash,
            "orders": orders,
        }
        request_path.write_text(json.dumps(request), encoding="utf-8")
        pd.DataFrame(
            [{"run_id": run_id, "date": "2026-07-10", "ticket_id": "t-bound", "symbol": "600001", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "unknown", "broker_order_id": "", "message": "mismatched"}]
        ).to_csv(output / "auto_live_order_results.csv", index=False)

        mismatched = load_auto_live_query_source(output)

        self.assertTrue(mismatched.empty)

        request["orders"][0]["symbol"] = "600001"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        pd.DataFrame(
            [{"run_id": run_id, "date": "2026-07-10", "ticket_id": "t-bound", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "unknown", "broker_order_id": "", "message": "original"}]
        ).to_csv(output / "auto_live_order_results.csv", index=False)

        tampered = load_auto_live_query_source(output)

        self.assertTrue(tampered.empty)
        self.assertEqual(
            tampered.attrs["query_source_integrity_reason"],
            "submit_request_order_hash_mismatch",
        )

        paths = query_auto_live_orders(config, raise_on_blocked=False)
        audit = pd.read_csv(paths["auto_live_order_status_audit.csv"]).set_index("check")

        self.assertFalse(bool(audit.loc["query_source_integrity", "passed"]))
        self.assertEqual(
            audit.loc["query_source_integrity", "value"],
            "submit_request_order_hash_mismatch",
        )

    def test_query_recovers_interrupted_batch_by_ticket_id(self):
        output = self.fresh_output("auto_live_query_interrupted")
        bridge = output / "query_bridge.py"
        bridge.write_text(
            "import json, sys\n"
            "request_path, result_path = sys.argv[1], sys.argv[2]\n"
            "payload = json.load(open(request_path, encoding='utf-8'))\n"
            "results = []\n"
            "for order in payload.get('orders', []):\n"
            "    results.append({'ticket_id': order['ticket_id'], 'broker_order_id': 'recovered-' + order['ticket_id'], 'broker_status': 'filled', 'filled_quantity': order['quantity'], 'avg_fill_price': order['limit_price'], 'filled_amount': order['quantity'] * order['limit_price'], 'fee': 1.0, 'message': 'found by ticket_id'})\n"
            "json.dump({'results': results}, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n",
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output)},
            "auto_live": {
                "broker_adapter": "command",
                "allow_command_adapter": True,
                "command_adapter": {"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
            },
        }
        pd.DataFrame(
            [
                {"check": "selftest_config_hash_match", "passed": True},
                {"check": "selftest_required_capabilities_declared", "passed": True},
            ]
        ).to_csv(output / "auto_live_adapter_selftest.csv", index=False)
        orders = [
            {"ticket_id": "t-recover", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready", "date": "2026-07-10"}
        ]
        ticket_frame = pd.DataFrame(orders)
        from ashare_quant.autolive import auto_live_dedupe_key, tickets_sha256

        ticket_hash = tickets_sha256(ticket_frame)
        config_hash = auto_live_config_sha256(config)
        run_id = "interrupted-query-run"
        write_auto_live_submission_intent(
            config,
            run_id=run_id,
            state="submitting",
            adapter="command",
            order_count=1,
            tickets_hash=ticket_hash,
            auto_hash=config_hash,
            dedupe_key=auto_live_dedupe_key(ticket_hash, config_hash),
            message="adapter interrupted",
        )
        request = {
            "schema_version": "ashare_quant.command_adapter.v1",
            "mode": "submit_orders",
            "run_id": run_id,
            "tickets_sha256": ticket_hash,
            "orders": orders,
        }
        (output / f"auto_live_command_request_{run_id}.json").write_text(json.dumps(request), encoding="utf-8")

        paths = query_auto_live_orders(config)
        source = pd.read_csv(output / "auto_live_order_results.csv")
        statuses = pd.read_csv(paths["auto_live_order_status.csv"])
        recovery = pd.read_csv(paths["auto_live_submission_recovery.csv"])
        lock_audit = pd.read_csv(write_auto_live_submit_lock_audit(config)).set_index("check")

        self.assertEqual(source["status"].tolist(), ["unknown"])
        self.assertEqual(statuses["broker_order_id"].tolist(), ["recovered-t-recover"])
        self.assertEqual(statuses["broker_status"].tolist(), ["filled"])
        self.assertTrue(bool(recovery["resolved"].iloc[0]))
        self.assertTrue(bool(lock_audit.loc["prior_submission_resolved", "passed"]))


if __name__ == "__main__":
    unittest.main()
