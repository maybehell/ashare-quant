import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.evidence import build_evidence_plan, write_evidence_plan


class EvidencePlanTests(unittest.TestCase):
    def test_evidence_plan_summarizes_forward_shadow_account_and_auto_gaps(self):
        output = Path.cwd() / "test_outputs" / "evidence_plan"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "strategy": {"name": "test", "top_n": 1, "max_stock_weight": 1, "max_industry_weight": 1, "lookbacks": {}, "weights": {}},
            "live_guard": {"min_forward_trading_days": 60, "min_shadow_observed_signals": 20, "min_shadow_observed_days_per_signal": 5},
        }
        pd.DataFrame([
            {"candidate_id": "abc", "status": "collecting_forward_data", "observed_forward_trading_days": 9, "required_forward_trading_days": 60, "remaining_forward_trading_days": 51}
        ]).to_csv(output / "live_candidate_forward_progress.csv", index=False)
        pd.DataFrame([
            {"status": "ok", "observed_trading_days": 6, "excess_return": 0.01, "covered_weight": 1.0, "candidate_id": "abc", "strategy_hash": "different"}
        ]).to_csv(output / "shadow_forward_audit.csv", index=False)
        pd.DataFrame([
            {"price_context": "mark", "symbol": "1"},
            {"price_context": "snapshot", "symbol": "000001"},
            {"price_context": "mark", "symbol": "600002"},
        ]).to_csv(output / "account_state_missing_prices.csv", index=False)
        pd.DataFrame([{"check": "snapshot_positions_priced", "passed": False}]).to_csv(output / "account_state_audit.csv", index=False)
        pd.DataFrame([{"check": "account_state_audit", "passed": False}]).to_csv(output / "rpa_pre_trade_checks.csv", index=False)
        pd.DataFrame([{"mode": "strict_live_execution", "status": "blocked", "blockers": "strict_live_readiness"}]).to_csv(output / "execution_modes.csv", index=False)
        pd.DataFrame([{"status": "blocked_execution_and_evidence", "auto_live_allowed": False, "blockers": "strict_live_execution_mode"}]).to_csv(output / "auto_live_summary.csv", index=False)

        plan = build_evidence_plan(config)
        by_area = {row.area: row for row in plan.itertuples(index=False)}

        self.assertEqual(by_area["forward_validation"].remaining, 51)
        self.assertEqual(by_area["shadow_observation"].current, 0)
        self.assertEqual(by_area["account_audit"].current, 2)
        self.assertEqual(by_area["rpa_pre_trade"].blocker, "account_state_audit")
        self.assertEqual(by_area["auto_live_config"].status, "blocked")

    def test_formal_forward_candidate_set_overrides_legacy_progress(self):
        output = Path.cwd() / "test_outputs" / "evidence_formal_forward_set"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{
            "candidate_id": "legacy",
            "status": "collecting_forward_data",
            "observed_forward_trading_days": 23,
            "required_forward_trading_days": 60,
        }]).to_csv(output / "live_candidate_forward_progress.csv", index=False)
        pd.DataFrame([
            {
                "candidate_id": "formal-a",
                "status": "observed",
                "max_observed_trading_days": 2,
                "observable_signal_count": 2,
                "required_forward_trading_days": 60,
                "forward_observation_status": "collecting_forward_data",
            },
            {
                "candidate_id": "formal-b",
                "status": "observed",
                "max_observed_trading_days": 1,
                "observable_signal_count": 1,
                "required_forward_trading_days": 60,
                "forward_observation_status": "collecting_forward_data",
            },
        ]).to_csv(output / "forward_only_candidate_observation.csv", index=False)

        plan = build_evidence_plan({"paths": {"output_dir": str(output)}})
        forward = {row.area: row for row in plan.itertuples(index=False)}["forward_validation"]

        self.assertEqual(forward.status, "collecting")
        self.assertEqual(forward.current, 1)
        self.assertEqual(forward.required, 60)
        self.assertEqual(forward.remaining, 59)
        self.assertEqual(forward.blocker, "formal_candidate_set_forward_trading_days")
        self.assertIn("forward-daily", forward.command)
        self.assertIn("2 个正式冻结候选", forward.next_action)
        shadow = {row.area: row for row in plan.itertuples(index=False)}["shadow_observation"]
        self.assertEqual(shadow.status, "collecting")
        self.assertEqual(shadow.current, 1)
        self.assertEqual(shadow.required, 20)
        self.assertEqual(shadow.remaining, 19)
        self.assertEqual(shadow.blocker, "formal_candidate_set_shadow_observed_signal_count")
        self.assertIn("forward-daily", shadow.command)
        self.assertIn("2 个正式冻结候选", shadow.next_action)

    def test_evidence_plan_blocks_missing_account_and_rpa_audits(self):
        output = Path.cwd() / "test_outputs" / "evidence_missing_audits"
        output.mkdir(parents=True, exist_ok=True)
        plan = build_evidence_plan({"paths": {"output_dir": str(output)}})
        by_area = {row.area: row for row in plan.itertuples(index=False)}
        self.assertEqual(by_area["account_audit"].blocker, "account_state_audit_missing")
        self.assertEqual(by_area["rpa_pre_trade"].blocker, "rpa_pre_trade_checks_missing")

    def test_evidence_plan_prioritizes_fetch_when_missing_prices_are_outside_price_cache(self):
        output = Path.cwd() / "test_outputs" / "evidence_fetch_missing_prices"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        pd.DataFrame([{"check": "snapshot_positions_priced", "passed": False}]).to_csv(output / "account_state_audit.csv", index=False)
        pd.DataFrame([
            {"price_context": "mark", "symbol": "000001", "symbol_in_price_cache": False},
            {"price_context": "snapshot", "symbol": "1", "symbol_in_price_cache": False},
        ]).to_csv(output / "account_state_missing_prices.csv", index=False)

        plan = build_evidence_plan(config)
        account = {row.area: row for row in plan.itertuples(index=False)}["account_audit"]

        self.assertIn("fetch-account-prices", account.command)
        self.assertIn("自动追加 SQLite 持仓代码", account.next_action)

    def test_evidence_plan_switches_to_repair_when_fetch_already_failed_missing_symbol(self):
        root = Path.cwd() / "test_outputs" / "evidence_fetch_failed_missing"
        output = root / "outputs"
        cache = root / "cache"
        output.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output), "cache_dir": str(cache)}}
        pd.DataFrame([{"check": "snapshot_positions_priced", "passed": False}]).to_csv(output / "account_state_audit.csv", index=False)
        pd.DataFrame([{"price_context": "mark", "symbol": "600002", "symbol_in_price_cache": False}]).to_csv(output / "account_state_missing_prices.csv", index=False)
        pd.DataFrame([{"symbol": "600002", "stage": "history", "error": "empty"}]).to_csv(cache / "fetch_failures.csv", index=False)

        plan = build_evidence_plan(config)
        account = {row.area: row for row in plan.itertuples(index=False)}["account_audit"]

        self.assertIn("account-repair-template", account.command)
        self.assertIn("无可用行情", account.next_action)

    def test_evidence_plan_uses_priced_only_check_after_unresolved_fetch_and_precheck(self):
        root = Path.cwd() / "test_outputs" / "evidence_unresolved_priced_only"
        output = root / "outputs"
        cache = root / "cache"
        output.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output), "cache_dir": str(cache)}}
        pd.DataFrame([{"check": "snapshot_positions_priced", "passed": False}]).to_csv(output / "account_state_audit.csv", index=False)
        pd.DataFrame([{"price_context": "mark", "symbol": "600002", "symbol_in_price_cache": False}]).to_csv(output / "account_state_missing_prices.csv", index=False)
        pd.DataFrame([{"symbol": "600002", "stage": "history", "error": "empty"}]).to_csv(cache / "fetch_failures.csv", index=False)
        pd.DataFrame([{"path": "real_account_priced_only_import", "status": "precheck_passed_with_exclusions"}]).to_csv(output / "account_path_decision.csv", index=False)

        plan = build_evidence_plan(config)
        account = {row.area: row for row in plan.itertuples(index=False)}["account_audit"]

        self.assertIn("account-import-check --file outputs/account_import_priced_only_template.csv", account.command)
        self.assertIn("--ack-exclusions", account.next_action)
        self.assertIn("dry-run 已通过", account.next_action)

    def test_write_evidence_plan_writes_csv(self):
        output = Path.cwd() / "test_outputs" / "evidence_plan_write"
        output.mkdir(parents=True, exist_ok=True)
        path = write_evidence_plan({"paths": {"output_dir": str(output)}})
        self.assertTrue(path.exists())
        frame = pd.read_csv(path)
        self.assertIn("area", frame.columns)


if __name__ == "__main__":
    unittest.main()
