from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.diagnostics import annual_report, data_quality_report, regime_validation_report, robustness_split_report, rolling_validation_report, strategy_candidate_stability_report
from ashare_quant.diagnostics import strategy_reliability_report
from ashare_quant.candidate import live_strategy_hash
from ashare_quant.audit import build_run_manifest, frame_sha256, mapping_sha256, orders_frame_sha256
from ashare_quant.execution import build_execution_modes, build_execution_playbook, build_execution_readiness, build_execution_roadmap, execution_readiness_level
from ashare_quant.report import calc_metrics, current_shadow_forward_audit, formal_candidate_report_link, html_table, industry_exposure, load_strategy_research_evidence, order_source_diagnostics, post_warmup_validation_reports, read_csv, write_outputs


class ReportTests(unittest.TestCase):
    def test_html_table_localizes_forward_selection_fields(self):
        html = html_table(
            pd.DataFrame(
                [{
                    "check": "query_source_integrity",
                    "value": "submit_request_order_hash_mismatch",
                    "evaluation_mode": "fixed_horizon",
                    "curve_ledger_valid": True,
                    "evaluated_trading_days": 60,
                    "status": "collecting_forward_data",
                }]
            ),
            "empty",
        )

        self.assertIn("\u8bc4\u4f30\u65b9\u5f0f", html)
        self.assertIn("\u56fa\u5b9a\u7a97\u53e3", html)
        self.assertIn("\u8bc1\u636e\u8d26\u672c\u6709\u6548", html)
        self.assertIn("\u5b9e\u9645\u68c0\u9a8c\u4ea4\u6613\u65e5\u6570", html)
        self.assertIn("\u6b63\u5728\u6536\u96c6\u524d\u5411\u6570\u636e", html)
        self.assertIn(">\u662f<", html)
        self.assertIn("\u67e5\u8be2\u6062\u590d\u6e90\u5b8c\u6574\u6027", html)
        self.assertIn("\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u8ba2\u5355\u6458\u8981\u4e0d\u5339\u914d", html)

    def test_html_table_localizes_cancel_source_preflight_reason(self):
        html = html_table(
            pd.DataFrame(
                [
                    {
                        "check": "cancel_source_preflight",
                        "value": "cancel_source_query_already_used:q1",
                        "passed": False,
                    }
                ]
            ),
            "empty",
        )

        self.assertIn("\u64a4\u5355\u6e90\u9884\u68c0", html)
        self.assertIn("\u8be5\u67e5\u8be2\u6279\u6b21\u5df2\u53d1\u8d77\u8fc7\u64a4\u5355", html)
        self.assertNotIn("cancel_source_query_already_used", html)

    def test_data_quality_flags_shallow_latest_universe_history(self):
        stocks = pd.DataFrame([
            {"symbol": "600000", "industry": "bank", "list_date": "2000-01-01"},
            {"symbol": "000001", "industry": "bank", "list_date": "2000-01-01"},
        ])
        prices = pd.DataFrame([
            {"date": "2023-01-03", "symbol": "600000", "close": 10},
            {"date": "2026-07-31", "symbol": "600000", "close": 11},
            {"date": "2026-07-31", "symbol": "000001", "close": 12},
        ])
        quality = data_quality_report(
            stocks,
            prices,
            pd.DataFrame(),
            pd.DataFrame(),
            {
                "backtest": {"start": "2023-01-01"},
                "data": {"require_benchmark_on_signal_date": False},
                "universe": {"scope": "curated"},
                "live_guard": {"min_latest_universe_history_coverage_ratio": 0.9},
            },
        ).set_index("item")
        self.assertEqual(float(quality.loc["latest_universe_history_coverage_ratio", "value"]), 0.5)
        self.assertEqual(quality.loc["latest_universe_history_coverage_ratio", "severity"], "warning")

    def test_deployed_report_preserves_verified_shadow_summary_without_archive(self):
        output = Path.cwd() / "test_outputs" / "report_deployed_shadow"
        output.mkdir(parents=True, exist_ok=True)
        expected = pd.DataFrame([{"signal_date": "2026-07-10", "status": "pending"}])
        expected.to_csv(output / "shadow_forward_audit.csv", index=False)

        actual = current_shadow_forward_audit({"paths": {"output_dir": str(output)}})

        self.assertEqual(actual.loc[0, "signal_date"], "2026-07-10")
        self.assertEqual(actual.loc[0, "status"], "pending")

    def test_metrics_include_risk_fields(self):
        equity = pd.DataFrame({"equity": [100.0, 101.0, 99.0, 104.0], "turnover": [0.0, 0.1, 0.0, 0.2]})
        metrics = calc_metrics(equity)
        self.assertIn("annualized_return", metrics)
        self.assertIn("sharpe", metrics)
        self.assertIn("annualized_turnover", metrics)
        self.assertLess(metrics["max_drawdown"], 0)

    def test_post_warmup_reports_do_not_discard_equity_twice(self):
        dates = pd.date_range("2026-01-01", periods=20, freq="B")
        comparison = pd.DataFrame(
            {
                "date": dates.date,
                "strategy_nav": [1.0 + index * 0.01 for index in range(20)],
                "benchmark_nav": [1.0 + index * 0.008 for index in range(20)],
            }
        )
        comparison["strategy_return"] = comparison["strategy_nav"].pct_change().fillna(0.0)
        comparison["benchmark_return"] = comparison["benchmark_nav"].pct_change().fillna(0.0)
        comparison["excess_return"] = (
            comparison["strategy_return"] - comparison["benchmark_return"]
        )
        config = {
            "validation": {"holdout_split_ratio": 0.5},
            "strategy": {"lookbacks": {"momentum": 10}},
            "risk_overlay": {
                "benchmark_ma_days": 5,
                "benchmark_vol_days": 5,
                "high_volatility_threshold": 0.5,
            },
        }

        robustness, regimes = post_warmup_validation_reports(comparison, config)

        full = robustness.set_index("sample").loc["full_period"]
        self.assertEqual(str(full["start"]), str(dates.date[0]))
        self.assertEqual(str(full["end"]), str(dates.date[-1]))
        full_regime = regimes.set_index("regime").loc["full_after_warmup"]
        self.assertEqual(int(full_regime["observations"]), len(comparison))
        self.assertEqual(str(full_regime["start"]), str(dates.date[0]))

    def test_industry_exposure_reports_reference_and_active_cap(self):
        stocks = pd.DataFrame(
            [
                {"symbol": "t1", "industry": "tech"},
                {"symbol": "t2", "industry": "tech"},
                {"symbol": "b1", "industry": "bank"},
                {"symbol": "b2", "industry": "bank"},
            ]
        )
        positions = pd.DataFrame(
            [
                {"symbol": "t1", "market_value": 70.0},
                {"symbol": "b1", "market_value": 30.0},
            ]
        )
        prices = pd.DataFrame(
            [
                {"date": "2026-07-15", "symbol": symbol, "amount": 100.0}
                for symbol in ("t1", "t2", "b1", "b2")
            ]
        )
        config = {
            "strategy": {
                "max_stock_weight": 0.4,
                "max_industry_weight": 0.8,
                "lookbacks": {"liquidity": 20},
                "industry_relative_cap": {
                    "enabled": True,
                    "reference_weighting": "equal",
                    "max_active_weight": 0.10,
                },
            }
        }

        exposure = industry_exposure(positions, stocks, prices, config).set_index("industry")

        self.assertAlmostEqual(float(exposure.loc["tech", "reference_weight"]), 0.5)
        self.assertAlmostEqual(float(exposure.loc["tech", "active_weight"]), 0.2)
        self.assertFalse(bool(exposure.loc["tech", "within_active_cap"]))
    def test_industry_exposure_audits_aggregate_unknown_cap(self):
        stocks = pd.DataFrame(
            [
                {"symbol": "u1", "industry": "unknown"},
                {"symbol": "u2", "industry": "unknown"},
                {"symbol": "t1", "industry": "tech"},
                {"symbol": "t2", "industry": "tech"},
            ]
        )
        positions = pd.DataFrame(
            [
                {"symbol": "u1", "market_value": 70.0},
                {"symbol": "t1", "market_value": 30.0},
            ]
        )
        prices = pd.DataFrame(
            [
                {"date": "2026-07-15", "symbol": symbol, "amount": 100.0}
                for symbol in ("u1", "u2", "t1", "t2")
            ]
        )
        config = {
            "strategy": {
                "max_stock_weight": 0.4,
                "max_industry_weight": 0.8,
                "lookbacks": {"liquidity": 20},
                "industry_relative_cap": {
                    "enabled": True,
                    "reference_weighting": "equal",
                    "max_active_weight": 0.10,
                    "aggregate_unknown": True,
                    "max_unknown_weight": 0.20,
                },
            }
        }

        exposure = industry_exposure(positions, stocks, prices, config).set_index("industry")

        self.assertAlmostEqual(float(exposure.loc["unknown", "active_cap"]), 0.20)
        self.assertFalse(bool(exposure.loc["unknown", "within_active_cap"]))

    def test_empty_frames_write_headers(self):
        tmp = Path.cwd() / "test_outputs"
        config = {"paths": {"output_dir": tmp}}
        paths = write_outputs(
            config,
            equity=pd.DataFrame(),
            orders=pd.DataFrame(),
            positions=pd.DataFrame(),
            trades=pd.DataFrame(),
        )
        orders = Path(paths["orders.csv"]).read_text(encoding="utf-8-sig")
        self.assertTrue(orders.startswith("date,symbol,side,quantity,reason"))
        self.assertTrue(read_csv(Path(paths["orders.csv"])).empty)
        self.assertTrue(Path(paths["industry_risk_audit.csv"]).exists())
        self.assertTrue(Path(paths["run_manifest.csv"]).exists())
        self.assertTrue(Path(paths["regime_validation.csv"]).exists())
        self.assertTrue(Path(paths["live_readiness_roadmap.csv"]).exists())
        self.assertTrue(Path(paths["execution_readiness.csv"]).exists())
        self.assertTrue(Path(paths["execution_modes.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_summary.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_account_refresh.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_submission_recovery.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_submit_lock_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_submission_intent.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_blocked_attempt.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_risk_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_risk_reservations.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_stop_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_adapter_replay_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_adapter_file_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_adapter_replay_summary.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_service_summary.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_event_journal.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_journal_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_journal_summary.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_journal_errors.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_account_binding_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_recovery_points.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_recovery_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_alert_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_alert_outbox.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_alert_selftest.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_rollout_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_rollout_history.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_service_history.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_service_heartbeat.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_service_lock_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_service_stop_audit.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_cycle.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_cycle_summary.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_batch_summary.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_batch_plan.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_batch_orders.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_current_batch.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_active_batch.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_cycle_history.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_post_trade_reconciliation.csv"]).exists())
        self.assertTrue(Path(paths["auto_live_post_trade_reconciliation_history.csv"]).exists())
        self.assertTrue(Path(paths["evidence_plan.csv"]).exists())
        self.assertTrue(Path(paths["daily_observation_summary.csv"]).exists())
        self.assertTrue(Path(paths["daily_observation_audit.csv"]).exists())
        self.assertTrue(Path(paths["forward_factor_monitor.csv"]).exists())
        html = Path(paths["report.html"]).read_text(encoding="utf-8")
        self.assertIn("A 股量化交易系统报告", html)
        self.assertIn("自动实盘证据计划", html)
        self.assertIn("成交后行业风险审计", html)
        self.assertIn("\u6bcf\u65e5\u81ea\u52a8\u89c2\u5bdf\u72b6\u6001", html)
        self.assertIn("\u6bcf\u65e5\u81ea\u52a8\u89c2\u5bdf\u5ba1\u8ba1", html)
        self.assertIn("\u51bb\u7ed3\u540e\u56e0\u5b50\u524d\u5411\u76d1\u63a7", html)
        self.assertIn("自动实盘券商账户自动刷新", html)
        self.assertIn("自动实盘不确定提交恢复", html)
        self.assertIn("自动实盘提交锁审计", html)
        self.assertIn("华泰 Canary 武装审计", html)
        self.assertIn("券商账户账本初始化审计", html)
        self.assertIn("券商订单只读预览", html)
        self.assertIn("券商订单预览审计", html)
        self.assertIn("自动实盘独立风控", html)
        self.assertIn("自动实盘适配器文件哈希", html)
        self.assertIn("自动实盘适配器回放认证结论", html)
        self.assertIn("自动实盘适配器回放认证明细", html)
        self.assertIn("自动实盘服务结论", html)
        self.assertIn("自动实盘服务心跳", html)
        self.assertIn("自动实盘服务周期历史", html)
        self.assertIn("自动实盘服务锁审计", html)
        self.assertIn("自动实盘服务停止记录", html)
        self.assertIn("自动实盘当日风险预留", html)
        self.assertIn("自动实盘紧急停机记录", html)
        self.assertIn("自动实盘批次结论", html)
        self.assertIn("自动实盘批次计划", html)
        self.assertIn("自动实盘当前批次", html)
        self.assertIn("自动实盘周期结论", html)
        self.assertIn("自动实盘周期阶段", html)
        self.assertIn("自动实盘周期历史", html)
        self.assertIn("自动实盘后交易账户对账", html)
        self.assertIn("自动实盘后交易账户对账历史", html)
        self.assertNotIn("����", html)

    def test_backtest_outputs_clear_stale_paper_execution_artifacts(self):
        tmp = Path.cwd() / "test_outputs" / "clear_paper_artifacts"
        tmp.mkdir(parents=True, exist_ok=True)
        for name in ["orders_provenance.csv", "account_reconciliation.csv", "rpa_orders.csv", "rpa_pre_trade_checks.csv"]:
            (tmp / name).write_text("stale", encoding="utf-8")
        write_outputs(
            {"paths": {"output_dir": tmp}},
            equity=pd.DataFrame(),
            orders=pd.DataFrame(),
            positions=pd.DataFrame(),
            trades=pd.DataFrame(),
        )
        self.assertFalse((tmp / "orders_provenance.csv").exists())
        self.assertFalse((tmp / "account_reconciliation.csv").exists())
        self.assertFalse((tmp / "rpa_orders.csv").exists())
        self.assertFalse((tmp / "rpa_pre_trade_checks.csv").exists())

    def test_write_outputs_refreshes_shadow_forward_audit_from_current_prices(self):
        tmp = Path.cwd() / "test_outputs" / "report_shadow_refresh"
        archive = tmp / "shadow_archive"
        archive.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {"signal_date": "2026-01-01", "execution_date": "2026-01-02", "rank": 1, "symbol": "600000", "score": 1.0, "target_weight": 0.6, "industry": "bank", "board": "main"},
                {"signal_date": "2026-01-01", "execution_date": "2026-01-02", "rank": 2, "symbol": "000001", "score": 0.8, "target_weight": 0.4, "industry": "bank", "board": "main"},
            ]
        ).to_csv(archive / "20260101_signal.csv", index=False)
        pd.DataFrame(
            [
                {"item": "signal_date", "value": "2026-01-01"},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "signal_hash", "value": "abc"},
                {"item": "strategy_hash", "value": "strategy"},
                {"item": "candidate_id", "value": "candidate"},
            ]
        ).to_csv(archive / "20260101_provenance.csv", index=False)
        pd.DataFrame(
            [{"signal_date": "2026-01-01", "execution_date": "2026-01-02", "latest_date": "", "observed_trading_days": 0, "status": "not_observable"}]
        ).to_csv(tmp / "shadow_forward_audit.csv", index=False)

        prices = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "600000", "close": 10.0},
                {"date": "2026-01-03", "symbol": "600000", "close": 11.0},
                {"date": "2026-01-05", "symbol": "600000", "close": 12.0},
                {"date": "2026-01-02", "symbol": "000001", "close": 20.0},
                {"date": "2026-01-03", "symbol": "000001", "close": 21.0},
                {"date": "2026-01-05", "symbol": "000001", "close": 22.0},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "000300", "name": "CSI300", "close": 100.0},
                {"date": "2026-01-03", "symbol": "000300", "name": "CSI300", "close": 101.0},
                {"date": "2026-01-05", "symbol": "000300", "name": "CSI300", "close": 103.0},
            ]
        )

        write_outputs(
            {"paths": {"output_dir": tmp}, "data": {"max_symbols": 2}},
            equity=pd.DataFrame(),
            orders=pd.DataFrame(),
            positions=pd.DataFrame(),
            trades=pd.DataFrame(),
            prices=prices,
            benchmark=benchmark,
        )

        audit = read_csv(tmp / "shadow_forward_audit.csv")
        self.assertEqual(audit.loc[0, "status"], "ok")
        self.assertEqual(audit.loc[0, "latest_date"], "2026-01-05")
        self.assertEqual(int(audit.loc[0, "observed_trading_days"]), 2)

    def test_strategy_research_evidence_checks_data_and_artifact_hashes(self):
        root = Path.cwd() / "test_outputs" / "strategy_research_evidence"
        source_dir = root / "source"
        output_dir = root / "current"
        source_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        research_path = source_dir / "strategy_research.csv"
        research = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "strategy_hash": live_strategy_hash({"strategy": {"name": "candidate"}}),
                    "name": "candidate",
                    "score": 0.20,
                    "anti_overfit_passed": True,
                    "out_of_sample_excess_return": 0.08,
                },
                {
                    "rank": 2,
                    "name": "neighbor",
                    "score": 0.18,
                    "anti_overfit_passed": True,
                    "out_of_sample_excess_return": 0.05,
                },
            ]
        )
        research.to_csv(research_path, index=False, encoding="utf-8-sig")
        persisted = pd.read_csv(research_path)
        market_hashes = [
            {"item": "stocks_sha256", "value": "stocks"},
            {"item": "prices_sha256", "value": "prices"},
            {"item": "fundamentals_sha256", "value": "fundamentals"},
            {"item": "benchmark_sha256", "value": "benchmark"},
        ]
        source_manifest = pd.DataFrame(
            market_hashes
            + [
                {"item": "strategy_research_rows", "value": str(len(persisted))},
                {"item": "strategy_research_sha256", "value": frame_sha256(persisted)},
            ]
        )
        source_manifest.to_csv(source_dir / "strategy_research_manifest.csv", index=False)
        current_manifest = pd.DataFrame(market_hashes)
        config = {
            "paths": {"output_dir": str(output_dir)},
            "validation": {
                "strategy_research_path": str(research_path),
                "min_strategy_research_candidates": 2,
                "min_anti_overfit_candidates": 2,
                "min_candidate_oos_positive_ratio": 0.5,
            },
            "strategy": {"name": "candidate"},
        }

        _, stability = load_strategy_research_evidence(config, output_dir, current_manifest)
        passed = dict(zip(stability["item"], stability["passed"]))
        self.assertTrue(bool(passed["research_source_data_current"]))
        self.assertTrue(bool(passed["research_artifact_hash_match"]))
        self.assertTrue(bool(stability["passed"].all()))

        persisted.loc[0, "score"] = 0.99
        persisted.to_csv(research_path, index=False, encoding="utf-8-sig")
        _, changed = load_strategy_research_evidence(config, output_dir, current_manifest)
        changed_passed = dict(zip(changed["item"], changed["passed"]))
        self.assertFalse(bool(changed_passed["research_artifact_hash_match"]))
    def test_order_source_diagnostics_mark_backtest_orders_unexportable(self):
        output_dir = Path.cwd() / "test_outputs" / "order_source_backtest"
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ["orders_provenance.csv", "account_reconciliation.csv"]:
            path = output_dir / name
            if path.exists():
                path.unlink()
        diagnostics = order_source_diagnostics(
            output_dir,
            pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            {"paths": {"output_dir": str(output_dir)}, "strategy": {"name": "test_strategy"}},
        )
        values = dict(zip(diagnostics["check"], diagnostics["value"]))
        passed = dict(zip(diagnostics["check"], diagnostics["passed"]))
        self.assertEqual(values["orders_file_context"], "backtest_or_unverified")
        self.assertFalse(bool(passed["orders_config_hash_matches_current"]))
        self.assertFalse(bool(passed["orders_strategy_matches_current"]))
        self.assertFalse(bool(passed["rpa_exportable"]))

    def test_order_source_diagnostics_mark_verified_paper_orders_exportable(self):
        output_dir = Path.cwd() / "test_outputs" / "order_source_paper"
        output_dir.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output_dir)}, "strategy": {"name": "test_strategy"}}
        orders = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}])
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "strategy_name", "value": "test_strategy"},
                {"item": "config_sha256", "value": mapping_sha256(config)},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
                {"item": "orders_sha256", "value": orders_frame_sha256(orders)},
                {"item": "benchmark_exposure_multiplier", "value": "0.8"},
                {"item": "strategy_health_exposure_multiplier", "value": "0.5"},
                {"item": "combined_exposure_multiplier", "value": "0.4"},
                {"item": "strategy_health_exposure_reason", "value": "early_forward_warning"},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)
        pd.DataFrame(
            [{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]
        ).to_csv(output_dir / "account_reconciliation.csv", index=False)
        diagnostics = order_source_diagnostics(
            output_dir,
            orders,
            config,
        )
        values = dict(zip(diagnostics["check"], diagnostics["value"]))
        passed = dict(zip(diagnostics["check"], diagnostics["passed"]))
        self.assertEqual(values["orders_file_context"], "paper_executable")
        self.assertTrue(bool(passed["rpa_exportable"]))
        self.assertTrue(bool(passed["orders_hash_matches_provenance"]))
        self.assertTrue(bool(passed["orders_config_hash_matches_current"]))
        self.assertTrue(bool(passed["orders_strategy_matches_current"]))
        self.assertEqual(float(values["benchmark_exposure_multiplier"]), 0.8)
        self.assertEqual(float(values["strategy_health_exposure_multiplier"]), 0.5)
        self.assertEqual(float(values["combined_exposure_multiplier"]), 0.4)
        self.assertTrue(bool(passed["operational_exposure_overlay_consistent"]))

    def test_order_source_diagnostics_rejects_stale_config_or_strategy(self):
        output_dir = Path.cwd() / "test_outputs" / "order_source_stale_config"
        output_dir.mkdir(parents=True, exist_ok=True)
        original_config = {"paths": {"output_dir": str(output_dir)}, "strategy": {"name": "old_strategy"}}
        current_config = {"paths": {"output_dir": str(output_dir)}, "strategy": {"name": "new_strategy"}}
        orders = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}])
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "strategy_name", "value": "old_strategy"},
                {"item": "config_sha256", "value": mapping_sha256(original_config)},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
                {"item": "orders_sha256", "value": orders_frame_sha256(orders)},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)
        pd.DataFrame(
            [{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]
        ).to_csv(output_dir / "account_reconciliation.csv", index=False)

        diagnostics = order_source_diagnostics(output_dir, orders, current_config)
        passed = dict(zip(diagnostics["check"], diagnostics["passed"]))
        values = dict(zip(diagnostics["check"], diagnostics["value"]))

        self.assertFalse(bool(passed["orders_config_hash_matches_current"]))
        self.assertFalse(bool(passed["orders_strategy_matches_current"]))
        self.assertFalse(bool(passed["rpa_exportable"]))
        self.assertEqual(values["orders_file_context"], "backtest_or_unverified")

    def test_order_source_diagnostics_reject_modified_paper_orders(self):
        output_dir = Path.cwd() / "test_outputs" / "order_source_paper_modified"
        output_dir.mkdir(parents=True, exist_ok=True)
        original = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}])
        modified = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 200}])
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
                {"item": "orders_sha256", "value": orders_frame_sha256(original)},
            ]
        ).to_csv(output_dir / "orders_provenance.csv", index=False)
        pd.DataFrame(
            [{"check": "all", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]
        ).to_csv(output_dir / "account_reconciliation.csv", index=False)

        diagnostics = order_source_diagnostics(output_dir, modified)
        passed = dict(zip(diagnostics["check"], diagnostics["passed"]))
        values = dict(zip(diagnostics["check"], diagnostics["value"]))

        self.assertFalse(bool(passed["orders_hash_matches_provenance"]))
        self.assertFalse(bool(passed["rpa_exportable"]))
        self.assertEqual(values["orders_file_context"], "backtest_or_unverified")

    def test_execution_readiness_summarizes_operational_blockers(self):
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        order_source = pd.DataFrame(
            [
                {"check": "orders_file_context", "passed": True, "value": "paper_executable", "threshold": "paper_executable", "message": ""},
                {"check": "rpa_exportable", "passed": True, "value": True, "threshold": True, "message": ""},
            ]
        )
        rpa_orders = pd.DataFrame([{"status": "ready", "estimated_amount": 1000.0}])
        orders = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}])
        readiness = build_execution_readiness(
            {"rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=passed_check,
            live_readiness=passed_check,
            order_source=order_source,
            account_reconciliation=passed_check,
            account_state_audit=passed_check,
            rpa_pre_trade_checks=passed_check,
            rpa_orders=rpa_orders,
            orders=orders,
            optional_dependencies={"pyautogui": True},
        )
        passed = dict(zip(readiness["check"], readiness["passed"]))
        self.assertTrue(bool(passed["rpa_ticket_status"]))
        self.assertEqual(execution_readiness_level(readiness), "执行就绪")

    def test_execution_readiness_flags_backtest_orders(self):
        order_source = pd.DataFrame(
            [
                {"check": "orders_file_context", "passed": False, "value": "backtest_or_unverified", "threshold": "paper_executable", "message": ""},
                {"check": "rpa_exportable", "passed": False, "value": False, "threshold": True, "message": ""},
            ]
        )
        readiness = build_execution_readiness(
            {"rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=pd.DataFrame(),
            live_readiness=pd.DataFrame(),
            order_source=order_source,
            account_reconciliation=pd.DataFrame(),
            account_state_audit=pd.DataFrame(),
            rpa_pre_trade_checks=pd.DataFrame(),
            rpa_orders=pd.DataFrame(),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": False},
        )
        passed = dict(zip(readiness["check"], readiness["passed"]))
        self.assertFalse(bool(passed["order_source_verified"]))
        self.assertTrue(bool(passed["rpa_dependency_pyautogui"]))
        self.assertIn("人工工单阻断", execution_readiness_level(readiness))
        roadmap = build_execution_roadmap(readiness)
        self.assertIn("真实账户路径", roadmap["area"].tolist())

    def test_execution_readiness_blocks_pyautogui_only_when_click_execution_required(self):
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        order_source = pd.DataFrame(
            [
                {"check": "orders_file_context", "passed": True, "value": "paper_executable", "threshold": "paper_executable", "message": ""},
                {"check": "rpa_exportable", "passed": True, "value": True, "threshold": True, "message": ""},
            ]
        )
        readiness = build_execution_readiness(
            {
                "rpa": {"require_manual_final_confirm": True, "never_click_submit": True},
                "execution": {"require_rpa_click_dependency": True},
            },
            practical_readiness=passed_check,
            live_readiness=passed_check,
            order_source=order_source,
            account_reconciliation=passed_check,
            account_state_audit=passed_check,
            rpa_pre_trade_checks=passed_check,
            rpa_orders=pd.DataFrame([{"status": "ready"}]),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": False},
        )
        passed = dict(zip(readiness["check"], readiness["passed"]))
        values = dict(zip(readiness["check"], readiness["value"]))
        self.assertFalse(bool(passed["rpa_dependency_pyautogui"]))
        self.assertEqual(values["rpa_dependency_pyautogui"], "missing_required")
        roadmap = build_execution_roadmap(readiness)
        self.assertIn("RPA 依赖", roadmap["area"].tolist())

    def test_execution_readiness_separates_manual_ticket_and_strict_live_evidence(self):
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        failed_live = pd.DataFrame([{"check": "forward", "passed": False, "value": "0", "threshold": "60", "message": ""}])
        order_source = pd.DataFrame(
            [
                {"check": "orders_file_context", "passed": True, "value": "paper_executable", "threshold": "paper_executable", "message": ""},
                {"check": "rpa_exportable", "passed": True, "value": True, "threshold": True, "message": ""},
            ]
        )
        readiness = build_execution_readiness(
            {"rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=passed_check,
            live_readiness=failed_live,
            order_source=order_source,
            account_reconciliation=passed_check,
            account_state_audit=passed_check,
            rpa_pre_trade_checks=passed_check,
            rpa_orders=pd.DataFrame([{"status": "ready"}]),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": False},
        )
        self.assertEqual(execution_readiness_level(readiness), "人工工单就绪；严格待验证 1 项")
        modes = build_execution_modes(readiness)
        status = dict(zip(modes["mode"], modes["status"]))
        passed = dict(zip(modes["mode"], modes["passed"]))
        self.assertEqual(status["manual_ticket_review"], "ready")
        self.assertEqual(status["strict_live_execution"], "blocked")
        self.assertTrue(bool(passed["manual_ticket_review"]))
        self.assertFalse(bool(passed["strict_live_execution"]))

    def test_execution_playbook_separates_simulation_real_and_strict_routes(self):
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        failed_account = pd.DataFrame([{"check": "account_state_audit", "passed": False, "value": "5/7", "threshold": "all", "message": "missing prices"}])
        failed_live = pd.DataFrame([{"check": "strict", "passed": False, "value": "collecting", "threshold": "all", "message": ""}])
        readiness = build_execution_readiness(
            {"rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=passed_check,
            live_readiness=failed_live,
            order_source=pd.DataFrame(),
            account_reconciliation=pd.DataFrame(),
            account_state_audit=failed_account,
            rpa_pre_trade_checks=pd.DataFrame(),
            rpa_orders=pd.DataFrame(),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": False},
        )
        account_paths = pd.DataFrame(
            [
                {"path": "current_account_state", "status": "blocked"},
                {"path": "real_account_full_import", "status": "blocked_missing_prices"},
                {"path": "real_account_priced_only_import", "status": "precheck_passed_with_exclusions"},
            ]
        )

        playbook = build_execution_playbook(readiness, account_paths)
        routes = set(playbook["route"])
        commands = " ".join(playbook["command"].astype(str))
        simulation = playbook[(playbook["route"] == "模拟盘闭环") & (playbook["step"] == 1)].iloc[0]
        strict = playbook[(playbook["route"] == "严格实盘观察") & (playbook["step"] == 2)].iloc[0]

        self.assertIn("模拟盘闭环", routes)
        self.assertIn("真实账户闭环", routes)
        self.assertIn("严格实盘观察", routes)
        self.assertEqual(simulation["status"], "available_with_confirmation")
        self.assertIn("account-price-check", commands)
        self.assertEqual(strict["status"], "blocked")

    def test_execution_modes_require_pyautogui_for_click_fill(self):
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        order_source = pd.DataFrame(
            [
                {"check": "orders_file_context", "passed": True, "value": "paper_executable", "threshold": "paper_executable", "message": ""},
                {"check": "rpa_exportable", "passed": True, "value": True, "threshold": True, "message": ""},
            ]
        )
        readiness = build_execution_readiness(
            {"rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=passed_check,
            live_readiness=passed_check,
            order_source=order_source,
            account_reconciliation=passed_check,
            account_state_audit=passed_check,
            rpa_pre_trade_checks=passed_check,
            rpa_orders=pd.DataFrame([{"status": "ready"}]),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": False},
        )
        modes = build_execution_modes(readiness)
        status = dict(zip(modes["mode"], modes["status"]))
        blockers = dict(zip(modes["mode"], modes["blockers"]))
        self.assertEqual(status["manual_ticket_review"], "ready")
        self.assertEqual(status["rpa_click_fill"], "blocked")
        self.assertEqual(blockers["rpa_click_fill"], "rpa_dependency_pyautogui")

    def test_execution_readiness_requires_promoted_strategy_to_match_current_config(self):
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        order_source = pd.DataFrame(
            [
                {"check": "orders_file_context", "passed": True, "value": "paper_executable", "threshold": "paper_executable", "message": ""},
                {"check": "rpa_exportable", "passed": True, "value": True, "threshold": True, "message": ""},
            ]
        )
        decision = pd.DataFrame(
            [
                {
                    "decision": "allow_manual_live_review",
                    "can_promote": True,
                    "candidate_strategy_name": "candidate_strategy",
                    "required_action": "python -m ashare_quant execution-ready",
                }
            ]
        )
        readiness = build_execution_readiness(
            {"strategy": {"name": "current_strategy"}, "rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=passed_check,
            live_readiness=passed_check,
            order_source=order_source,
            account_reconciliation=passed_check,
            account_state_audit=passed_check,
            rpa_pre_trade_checks=passed_check,
            rpa_orders=pd.DataFrame([{"status": "ready"}]),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": True},
            strategy_decision=decision,
        )
        passed = dict(zip(readiness["check"], readiness["passed"]))
        values = dict(zip(readiness["check"], readiness["value"]))
        self.assertFalse(bool(passed["strategy_promotion_decision"]))
        self.assertIn("strategy_match=False", values["strategy_promotion_decision"])

        matched = build_execution_readiness(
            {"strategy": {"name": "candidate_strategy"}, "rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=passed_check,
            live_readiness=passed_check,
            order_source=order_source,
            account_reconciliation=passed_check,
            account_state_audit=passed_check,
            rpa_pre_trade_checks=passed_check,
            rpa_orders=pd.DataFrame([{"status": "ready"}]),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": True},
            strategy_decision=decision,
        )
        matched_passed = dict(zip(matched["check"], matched["passed"]))
        matched_values = dict(zip(matched["check"], matched["value"]))
        self.assertTrue(bool(matched_passed["strategy_promotion_decision"]))
        self.assertIn("strategy_match=True", matched_values["strategy_promotion_decision"])

    def test_execution_readiness_blocks_strict_live_when_promotion_decision_observe_only(self):
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        order_source = pd.DataFrame(
            [
                {"check": "orders_file_context", "passed": True, "value": "paper_executable", "threshold": "paper_executable", "message": ""},
                {"check": "rpa_exportable", "passed": True, "value": True, "threshold": True, "message": ""},
            ]
        )
        decision = pd.DataFrame(
            [
                {
                    "decision": "observe_only",
                    "can_promote": False,
                    "candidate_strategy_name": "candidate",
                    "required_action": "python -m ashare_quant observe-candidates --quiet",
                }
            ]
        )
        readiness = build_execution_readiness(
            {"rpa": {"require_manual_final_confirm": True, "never_click_submit": True}},
            practical_readiness=passed_check,
            live_readiness=passed_check,
            order_source=order_source,
            account_reconciliation=passed_check,
            account_state_audit=passed_check,
            rpa_pre_trade_checks=passed_check,
            rpa_orders=pd.DataFrame([{"status": "ready"}]),
            orders=pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]),
            optional_dependencies={"pyautogui": True},
            strategy_decision=decision,
        )

        passed = dict(zip(readiness["check"], readiness["passed"]))
        values = dict(zip(readiness["check"], readiness["value"]))
        self.assertFalse(bool(passed["strategy_promotion_decision"]))
        self.assertIn("observe_only", values["strategy_promotion_decision"])
        modes = build_execution_modes(readiness)
        mode_status = dict(zip(modes["mode"], modes["status"]))
        blockers = dict(zip(modes["mode"], modes["blockers"]))
        self.assertEqual(mode_status["manual_ticket_review"], "ready")
        self.assertEqual(mode_status["strict_live_execution"], "blocked")
        self.assertIn("strategy_promotion_decision", blockers["strict_live_execution"])

    def test_manifest_hash_is_stable(self):
        frame = pd.DataFrame({"date": ["2024-01-01"], "symbol": ["600000"], "close": [10.0]})
        self.assertEqual(frame_sha256(frame), frame_sha256(frame.copy()))
        manifest = build_run_manifest({"strategy": {"name": "test"}, "backtest": {"start": "2024-01-01"}}, prices=frame)
        self.assertIn("prices_sha256", manifest["item"].tolist())

    def test_strategy_candidate_stability_flags_isolated_candidate(self):
        config = {"strategy": {"name": "current"}, "validation": {"min_strategy_research_candidates": 2, "min_anti_overfit_candidates": 2}}
        research = pd.DataFrame(
            [
                {"rank": 1, "name": "current", "strategy_hash": live_strategy_hash(config), "anti_overfit_passed": True, "out_of_sample_excess_return": 0.05, "score": 0.10},
                {"rank": 2, "name": "alt", "anti_overfit_passed": False, "out_of_sample_excess_return": -0.01, "score": 0.08},
            ]
        )
        report = strategy_candidate_stability_report(research, config)
        passed = dict(zip(report["item"], report["passed"]))
        self.assertFalse(bool(passed["anti_overfit_passed_count"]))
        self.assertTrue(bool(passed["current_strategy_rank"]))
        self.assertTrue(bool(passed["current_strategy_research_hash"]))

        research.loc[research["name"] == "current", "strategy_hash"] = "different"
        mismatch = strategy_candidate_stability_report(research, config)
        mismatch_passed = dict(zip(mismatch["item"], mismatch["passed"]))
        self.assertFalse(bool(mismatch_passed["current_strategy_research_hash"]))

    def test_candidate_stability_includes_family_selection_bias(self):
        config = {
            "strategy": {"name": "stable"},
            "validation": {
                "min_strategy_research_candidates": 4,
                "min_anti_overfit_candidates": 2,
                "min_selection_bias_candidates": 4,
                "min_selection_bias_folds": 4,
                "min_selection_bias_splits": 6,
                "min_candidate_family_clusters": 1,
                "min_effective_candidate_count": 1.0,
                "max_largest_candidate_cluster_share": 1.0,
                "min_family_selection_clusters": 2,
                "min_family_selection_folds": 2,
                "min_family_selection_splits": 1,
                "max_family_selection_bias_probability": 1.0,
                "min_family_selected_test_median_excess_return": -1.0,
                "min_family_selected_test_positive_ratio": 0.0,
                "min_family_selection_consensus_ratio": 0.0,
            },
        }
        rows = [
            ("stable", "[0.04, 0.05, 0.03, 0.04]"),
            ("second", "[0.02, 0.01, 0.02, 0.01]"),
            ("mixed", "[0.01, -0.01, 0.01, -0.01]"),
            ("weak", "[-0.02, -0.01, -0.03, -0.02]"),
        ]
        research = pd.DataFrame([
            {
                "rank": index + 1,
                "name": name,
                "strategy_hash": live_strategy_hash(config) if name == "stable" else f"hash-{index}",
                "anti_overfit_passed": True,
                "out_of_sample_excess_return": 0.01,
                "score": 0.10 - index * 0.01,
                "time_fold_excess_returns": folds,
            }
            for index, (name, folds) in enumerate(rows)
        ])
        report = strategy_candidate_stability_report(research, config).set_index("item")
        self.assertTrue(bool(report.loc["strategy_selection_bias_ready", "passed"]))
        self.assertEqual(float(report.loc["fold_selection_bias_probability", "value"]), 0.0)
    def test_annual_report(self):
        comparison = pd.DataFrame(
            {
                "date": ["2024-01-01", "2024-12-31", "2025-01-01", "2025-12-31"],
                "strategy_nav": [1.0, 1.1, 1.1, 1.21],
                "benchmark_nav": [1.0, 1.05, 1.05, 1.08],
                "strategy_return": [0, 0.1, 0, 0.1],
                "benchmark_return": [0, 0.05, 0, 0.03],
                "excess_return": [0, 0.05, 0, 0.07],
            }
        )
        annual = annual_report(comparison)
        self.assertEqual(annual["year"].tolist(), [2024, 2025])

    def test_rolling_validation_report(self):
        comparison = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=30, freq="B").date,
                "strategy_nav": [1 + i * 0.01 for i in range(30)],
                "benchmark_nav": [1 + i * 0.005 for i in range(30)],
                "strategy_return": [0.0] * 30,
                "benchmark_return": [0.0] * 30,
                "excess_return": [0.0] * 30,
            }
        )
        rolling = rolling_validation_report(comparison, window=10, step=5)
        self.assertFalse(rolling.empty)
        self.assertIn("information_ratio", rolling.columns)

    def test_regime_validation_report_segments_market_days(self):
        comparison = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=8, freq="B").date,
                "strategy_nav": [1.0, 1.02, 1.01, 1.04, 1.03, 1.05, 1.06, 1.08],
                "benchmark_nav": [1.0, 1.01, 1.00, 1.02, 1.01, 1.00, 1.02, 1.03],
                "strategy_return": [0.0, 0.02, -0.0098039, 0.029703, -0.009615, 0.019417, 0.009524, 0.018868],
                "benchmark_return": [0.0, 0.01, -0.009901, 0.02, -0.009804, -0.009901, 0.02, 0.009804],
            }
        )

        regimes = regime_validation_report(comparison, {"risk_overlay": {"benchmark_ma_days": 3, "benchmark_vol_days": 3, "high_volatility_threshold": 0.5}})
        values = regimes.set_index("regime")

        self.assertIn("benchmark_up_days", values.index)
        self.assertIn("benchmark_down_days", values.index)
        self.assertGreater(int(values.loc["benchmark_up_days", "observations"]), 0)
        self.assertGreater(int(values.loc["benchmark_down_days", "observations"]), 0)
        self.assertIn("information_ratio", regimes.columns)

    def test_robustness_split_can_skip_factor_warmup(self):
        comparison = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=10, freq="B").date,
                "strategy_nav": [1.0, 0.9, 0.8, 0.7, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
                "benchmark_nav": [1.0] * 10,
                "strategy_return": [0.0] * 10,
                "benchmark_return": [0.0] * 10,
                "excess_return": [0.0] * 10,
            }
        )
        split = robustness_split_report(comparison, split_ratio=0.5, warmup_days=4)
        values = dict(zip(split["sample"], split["start"]))
        self.assertEqual(str(values["in_sample_first_70pct"]), "2024-01-05")

    def test_data_quality_reports_raw_and_complete_market_dates(self):
        raw_prices = pd.DataFrame(
            [
                {"date": "2026-06-16", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-06-16", "symbol": "000002", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-06-17", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
            ]
        )
        filtered_prices = raw_prices[raw_prices["date"] == "2026-06-16"].copy()
        benchmark = pd.DataFrame([{"date": "2026-06-16", "name": "沪深300", "close": 100}])
        stocks = pd.DataFrame(
            [
                {"symbol": "000001", "industry": "bank", "list_date": "2020-01-01"},
                {"symbol": "000002", "industry": "bank", "list_date": "2020-01-01"},
            ]
        )
        report = data_quality_report(
            stocks,
            filtered_prices,
            pd.DataFrame(),
            benchmark,
            {"data": {"min_daily_price_coverage_ratio": 0.9, "require_benchmark_on_signal_date": True}},
            raw_prices=raw_prices,
            raw_benchmark=benchmark,
        )
        values = dict(zip(report["item"], report["value"]))
        severities = dict(zip(report["item"], report["severity"]))
        self.assertEqual(str(values["raw_price_end"]), "2026-06-17")
        self.assertEqual(str(values["complete_price_end"]), "2026-06-16")
        self.assertEqual(str(values["excluded_partial_price_date"]), "2026-06-17")
        self.assertEqual(severities["raw_latest_price_coverage_ratio"], "warning")
        self.assertEqual(values["stock_pool_coverage_interpretation"], "curated_subset")

    def test_full_market_data_quality_warns_on_low_price_coverage(self):
        prices = pd.DataFrame(
            [
                {"date": "2026-06-16", "symbol": "000001", "open": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-06-16", "symbol": "000002", "open": 1, "close": 1, "volume": 1, "amount": 1},
            ]
        )
        stocks = pd.DataFrame(
            [{"symbol": f"{idx:06d}", "industry": "bank", "list_date": "2020-01-01"} for idx in range(1, 11)]
        )
        benchmark = pd.DataFrame([{"date": "2026-06-16", "name": "沪深300", "close": 100}])
        report = data_quality_report(
            stocks,
            prices,
            pd.DataFrame(),
            benchmark,
            {
                "universe": {"scope": "full_market"},
                "live_guard": {"min_full_market_priced_stock_coverage": 0.7},
                "data": {"min_daily_price_coverage_ratio": 0.9},
            },
        )
        values = dict(zip(report["item"], report["value"]))
        severities = dict(zip(report["item"], report["severity"]))
        self.assertEqual(values["stock_pool_coverage_interpretation"], "full_market")
        self.assertEqual(severities["priced_stock_coverage"], "warning")
        self.assertEqual(severities["stock_pool_coverage_interpretation"], "warning")


    def test_strategy_reliability_caps_grade_for_weak_statistical_evidence(self):
        quality = pd.DataFrame([
            {"item": "universe_scope", "value": "curated"},
            {"item": "optimized_on_current_cache", "value": True},
        ])
        bootstrap = pd.DataFrame([{
            "status": "ok",
            "probability_excess_positive": 0.602,
            "lower_95_excess_return": -0.42,
        }])

        reliability = strategy_reliability_report(
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            quality,
            {"validation": {"min_backtest_trading_days": 756}},
            excess_bootstrap=bootstrap,
        )

        values = dict(zip(reliability["item"], reliability["value"]))
        passed = dict(zip(reliability["item"], reliability["passed"]))
        self.assertEqual(values["reliability_grade"], "低可信")
        self.assertLessEqual(float(values["reliability_score"]), 54.0)
        self.assertEqual(float(values["reliability_evidence_cap"]), 54.0)
        self.assertFalse(bool(passed["bootstrap_probability_excess_positive"]))
        self.assertFalse(bool(passed["bootstrap_lower_95_excess_return"]))
    def test_strategy_reliability_scores_metadata_quality_gaps(self):
        quality = pd.DataFrame([
            {"item": "universe_scope", "value": "curated"},
            {"item": "unknown_industry_ratio", "value": 0.90},
            {"item": "imputed_list_date_ratio", "value": 0.80},
            {"item": "optimized_on_current_cache", "value": False},
        ])
        reliability = strategy_reliability_report(
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            quality,
            {
                "validation": {"min_backtest_trading_days": 1},
                "live_guard": {
                    "max_unknown_industry_ratio": 0.20,
                    "max_imputed_list_date_ratio": 0.20,
                },
            },
        )
        passed = dict(zip(reliability["item"], reliability["passed"]))
        self.assertFalse(bool(passed["unknown_industry_ratio"]))
        self.assertFalse(bool(passed["imputed_list_date_ratio"]))


    def test_formal_candidate_report_link_keeps_default_and_candidate_reports_distinct(self):
        root = Path.cwd() / "test_outputs" / "formal_candidate_report_link"
        default_dir = root / "outputs"
        candidate_dir = root / "candidate"
        default_dir.mkdir(parents=True, exist_ok=True)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        report_path = default_dir / "report.html"
        candidate_report = candidate_dir / "report.html"
        candidate_report.write_text("candidate", encoding="utf-8")
        observation = pd.DataFrame(
            [
                {
                    "candidate_id": "candidate-1",
                    "candidate_output_dir": str(candidate_dir),
                    "status": "waiting_for_market_data",
                    "max_observed_trading_days": 0,
                    "required_forward_trading_days": 60,
                }
            ]
        )

        link = formal_candidate_report_link(report_path, observation)

        self.assertIn('../candidate/report.html', link)
        self.assertIn('正式冻结候选 candidate-1', link)
        self.assertIn('前向 0/60', link)
        self.assertIn('等待完整行情', link)

if __name__ == "__main__":
    unittest.main()
