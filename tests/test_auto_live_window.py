import sys
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256, run_auto_live, write_auto_live_adapter_selftest
from ashare_quant.autolive_window import (
    auto_live_window_audit_passed,
    auto_live_window_blockers,
    build_auto_live_window_audit,
    write_auto_live_window_audit,
)


class AutoLiveWindowTests(unittest.TestCase):
    def setUp(self):
        self.output = Path.cwd() / "test_outputs" / self._testMethodName
        self.output.mkdir(parents=True, exist_ok=True)
        for child in self.output.glob("*"):
            if child.is_file():
                child.unlink()

    def config(self, **auto_live):
        return {"paths": {"output_dir": str(self.output)}, "auto_live": auto_live}

    @staticmethod
    def tickets(ticket_date="2026-07-10", status="ready"):
        return pd.DataFrame(
            [
                {
                    "ticket_id": "t1",
                    "date": ticket_date,
                    "symbol": "600000",
                    "side": "buy",
                    "quantity": 100,
                    "limit_price": 10.0,
                    "status": status,
                }
            ]
        )

    def test_configured_trading_window_and_fresh_ready_ticket_pass(self):
        audit = build_auto_live_window_audit(
            self.config(),
            self.tickets(),
            now=datetime(2026, 7, 10, 9, 30),
        )

        self.assertTrue(auto_live_window_audit_passed(audit))
        self.assertEqual(auto_live_window_blockers(audit), "")

    def test_outside_trading_window_is_blocked(self):
        audit = build_auto_live_window_audit(
            self.config(),
            self.tickets(),
            now=datetime(2026, 7, 10, 15, 30),
        )

        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("within_trading_window", failed)
        self.assertIn("within_trading_window", auto_live_window_blockers(audit))

    def test_stale_ticket_is_blocked(self):
        audit = build_auto_live_window_audit(
            self.config(),
            self.tickets(ticket_date="2026-07-06"),
            now=datetime(2026, 7, 10, 9, 30),
        )

        failed = set(audit.loc[~audit["passed"].astype(bool), "check"])
        self.assertIn("ticket_dates_not_stale", failed)

    def test_explicit_drill_overrides_allow_weekend_and_stale_ticket(self):
        audit = build_auto_live_window_audit(
            self.config(allow_outside_trading_window=True, allow_stale_tickets=True),
            self.tickets(ticket_date="2026-07-06"),
            now=datetime(2026, 7, 11, 18, 0),
        )

        self.assertTrue(auto_live_window_audit_passed(audit))

    def test_window_audit_is_written_with_expected_checks(self):
        path = write_auto_live_window_audit(
            self.config(),
            tickets=self.tickets(),
            now=datetime(2026, 7, 10, 9, 30),
        )
        audit = pd.read_csv(path)

        self.assertTrue(path.exists())
        self.assertIn("within_trading_window", set(audit["check"]))
        self.assertIn("ticket_dates_not_stale", set(audit["check"]))

    def test_stale_ticket_blocks_before_command_submit_is_called(self):
        marker = self.output / "submit_called.txt"
        bridge = self.output / "window_guard_bridge.py"
        bridge.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            f"marker = Path({str(marker)!r})\n"
            "request_path, result_path = sys.argv[1], sys.argv[2]\n"
            "payload = json.load(open(request_path, encoding='utf-8'))\n"
            "if payload.get('mode') == 'self_test':\n"
            "    result = {'schema_version': payload['schema_version'], 'run_id': payload['run_id'], 'status': 'ok', 'capabilities': ['submit_orders', 'query_orders', 'cancel_orders', 'account_snapshot'], 'auto_live_config_sha256': payload['auto_live_config_sha256']}\n"
            "else:\n"
            "    marker.write_text('called', encoding='utf-8')\n"
            "    result = {'results': [{'ticket_id': order['ticket_id'], 'status': 'submitted', 'broker_order_id': 'unexpected-' + order['ticket_id'], 'message': 'unexpected'} for order in payload['orders']]}\n"
            "json.dump(result, open(result_path, 'w', encoding='utf-8'), ensure_ascii=False)\n",
            encoding="utf-8",
        )
        config = self.config(
            enabled=True,
            broker_adapter="command",
            allow_command_adapter=True,
            allow_broker_submit=True,
            allow_outside_trading_window=True,
            max_ticket_age_days=0,
            command_adapter={"command": [sys.executable, str(bridge)], "timeout_seconds": 10},
        )
        pd.DataFrame([{"check": "all", "passed": True, "severity": "ok"}]).to_csv(self.output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"mode": "manual_ticket_review", "passed": True, "status": "ready", "blockers": ""},
                {"mode": "strict_live_execution", "passed": True, "status": "ready", "blockers": ""},
            ]
        ).to_csv(self.output / "execution_modes.csv", index=False)
        self.tickets(ticket_date="2000-01-01").to_csv(self.output / "rpa_orders.csv", index=False)
        pd.DataFrame(
            [{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "severity": "ok", "next_action": "", "message": "ok"}]
        ).to_csv(self.output / "auto_live_broker_account_audit.csv", index=False)
        pd.DataFrame([{"snapshot_run_id": "window-test", "broker": "test-broker", "account_id": "acct-test", "environment": "live", "available_cash": 1000000.0, "total_asset": 1000000.0, "generated_at": datetime.now().isoformat(timespec="seconds"), "auto_live_config_sha256": auto_live_config_sha256(config)}]).to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)
        write_auto_live_adapter_selftest(config)

        with self.assertRaisesRegex(RuntimeError, "ticket_dates_not_stale"):
            run_auto_live(config)

        run = pd.read_csv(self.output / "auto_live_run.csv")
        self.assertEqual(run["status"].iloc[0], "blocked")
        self.assertIn("ticket_dates_not_stale", run["blocked_reason"].iloc[0])
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
