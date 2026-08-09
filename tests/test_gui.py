import unittest
from pathlib import Path

from ashare_quant.gui import candidate_forward_progress_lines, deployment_gate_label, execution_dashboard_lines, local_operator_next_action, local_operator_status_label, one_click_commands, primary_challenger_output_dir, recommended_action, strategy_profile_summary, strategy_promotion_lines


class GuiHelperTests(unittest.TestCase):

    def test_one_click_commands_are_daily_safe_sequence(self):
        self.assertEqual(
            one_click_commands(),
            [["local-run", "--broker-optional"]],
        )

    def test_primary_challenger_output_comes_from_configuration(self):
        self.assertEqual(primary_challenger_output_dir().name, "outputs_robust_blend")

    def test_deployment_gate_label_separates_read_only_and_execution(self):
        rows = [
            {"check": "broker_read_only_ready", "passed": "True"},
            {"check": "broker_execution_ready", "passed": "False"},
        ]

        self.assertEqual(
            deployment_gate_label(rows, "broker_read_only_ready", "已连接", "待登录"),
            "已连接",
        )
        self.assertEqual(
            deployment_gate_label(rows, "broker_execution_ready", "已就绪", "未就绪"),
            "未就绪",
        )
        self.assertEqual(deployment_gate_label(rows, "missing"), "未检查")

    def test_local_operator_status_prioritizes_blocking_action(self):
        rows = [
            {"item": "overall_status", "value": "blocked", "passed": "False"},
            {"item": "next_action", "value": "登录华泰并打开资金股票页面。", "passed": "True"},
        ]

        self.assertEqual(local_operator_status_label(rows), "待处理")
        self.assertEqual(local_operator_next_action(rows), "登录华泰并打开资金股票页面。")

    def test_local_operator_action_does_not_override_healthy_strategy_advice(self):
        rows = [
            {"item": "overall_status", "value": "paper_only", "passed": "True"},
            {"item": "next_action", "value": "旧建议", "passed": "True"},
        ]

        self.assertEqual(local_operator_status_label(rows), "仅模拟")
        self.assertEqual(local_operator_next_action(rows), "")

    def test_recommended_action_prioritizes_partial_recovery(self):
        advice = recommended_action(
            source_health=[{"check": "partial_recovery_available", "passed": "False"}, {"check": "price_cache_age_days", "passed": "False"}],
            practical=[{"check": "price_cache_age_days", "passed": "False"}],
            strict=[],
            execution=[],
            order_source=[],
        )

        self.assertEqual(advice["commands"], [["recover-partial"], ["source-health"], ["report"]])
        self.assertEqual(advice["label"], "恢复 partial 行情")

    def test_recommended_action_prioritizes_stale_cache(self):
        advice = recommended_action(
            source_health=[{"check": "price_cache_age_days", "passed": "False"}],
            practical=[],
            strict=[],
            execution=[],
            order_source=[],
        )

        self.assertEqual(advice["commands"][0], ["fetch", "--cached-universe", "--max-symbols", "100", "--no-metadata"])
        self.assertIn("行情缓存已过期", str(advice["message"]))

    def test_recommended_action_rebuilds_current_evidence(self):
        advice = recommended_action(
            source_health=[],
            practical=[{"check": "evidence_current", "passed": "False"}],
            strict=[],
            execution=[],
            order_source=[],
        )

        self.assertEqual(advice["commands"], [["backtest"], ["stress"], ["report"]])
        self.assertEqual(advice["label"], "重建证据链")

    def test_recommended_action_collects_shadow_when_only_strict_live_is_blocked(self):
        advice = recommended_action(
            source_health=[],
            practical=[{"check": "all", "passed": "True"}],
            strict=[{"check": "candidate_forward_trading_days", "passed": "False"}],
            execution=[],
            order_source=[{"check": "rpa_exportable", "value": "True", "passed": "True"}],
        )

        self.assertEqual(advice["commands"], [["observe", "--quiet"]])
        self.assertIn("严格实盘", str(advice["message"]))

    def test_strategy_promotion_lines_show_gate_status(self):
        lines = strategy_promotion_lines(
            [
                {
                    "strategy_name": "adaptive",
                    "promotion_status": "collecting_evidence",
                    "recommendation": "continue_forward_and_shadow_observation",
                    "forward_days": "9",
                    "required_forward_days": "60",
                    "forward_excess_return": "0.0078",
                    "shadow_observed_signals": "1",
                    "shadow_required_signals": "20",
                    "shadow_avg_excess_return": "0.025",
                    "high_cost_excess_return": "0.19",
                    "anti_overfit_status": "not_cleared",
                }
            ],
            prefix="  ",
        )
        text = "\n".join(lines)
        self.assertIn("adaptive -> collecting_evidence", text)
        self.assertIn("9/60", text)
        self.assertIn("0.78%", text)
        self.assertIn("1/20", text)
        self.assertIn("not_cleared", text)

    def test_recommended_action_uses_candidate_observation_when_promotion_is_collecting(self):
        advice = recommended_action(
            source_health=[],
            practical=[{"check": "all", "passed": "True"}],
            strict=[{"check": "candidate_forward_trading_days", "passed": "False"}],
            execution=[],
            order_source=[{"check": "rpa_exportable", "value": "True", "passed": "True"}],
            promotion=[{"promotion_status": "collecting_evidence"}],
        )

        self.assertEqual(advice["commands"], [["observe-candidates", "--quiet", "--skip-stress"]])
        self.assertEqual(advice["label"], "观察所有候选")

    def test_candidate_forward_progress_lines_show_days_and_overfit_status(self):
        lines = candidate_forward_progress_lines(
            [
                {
                    "status": "collecting_forward_data",
                    "observed_forward_trading_days": "12",
                    "required_forward_trading_days": "60",
                    "remaining_forward_trading_days": "48",
                    "forward_excess_return": "0.0123",
                    "overfit_status": "not_cleared",
                    "next_action": "继续观察。",
                }
            ],
            prefix="  ",
        )
        text = "\n".join(lines)
        self.assertIn("12/60", text)
        self.assertIn("还差 48 日", text)
        self.assertIn("1.23%", text)
        self.assertIn("not_cleared", text)

    def test_strategy_profile_summary_separates_research_and_live_status(self):
        output_dir = Path.cwd() / "test_outputs" / "gui_profile_summary"
        output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "performance_metrics.csv": (
                "metric,value\n"
                "total_return,0.4671\n"
                "benchmark_total_return,0.2563\n"
                "excess_total_return,0.2108\n"
                "information_ratio,0.4025\n"
                "max_drawdown,-0.1741\n"
                "annualized_turnover,3.396\n"
            ),
            "run_manifest.csv": (
                "item,value\n"
                "strategy_name,adaptive_candidate\n"
                "generated_at,2026-06-20T09:30:00\n"
                "prices_first_date,2023-01-03\n"
                "prices_last_date,2026-06-16\n"
            ),
            "strategy_reliability.csv": (
                "item,value,threshold,passed,severity,note\n"
                "reliability_grade,高可信,85,True,ok,-\n"
                "reliability_score,92.9,70,True,ok,-\n"
                "backtest_trading_days,834,756,True,ok,-\n"
                "backtest_start,2023-01-03,info,True,info,-\n"
                "backtest_end,2026-06-16,info,True,info,-\n"
            ),
            "strategy_candidate_stability.csv": (
                "item,value,threshold,passed,severity,note\n"
                "candidate_count,13,5,True,ok,-\n"
                "research_source_data_current,True,current,True,ok,-\n"
            ),
            "practical_readiness.csv": "check,passed,value,threshold,message\nall,True,ok,ok,-\n",
            "live_readiness.csv": (
                "check,passed,value,threshold,message\n"
                "candidate_forward_trading_days,False,0,60,-\n"
                "shadow_observed_signal_count,False,0,20 signals with >= 5 days,-\n"
            ),
            "live_candidate_forward_progress.csv": (
                "candidate_id,status,observed_forward_trading_days,required_forward_trading_days,"
                "forward_excess_return,overfit_status\n"
                "candidate-1,collecting_forward_data,0,60,,not_cleared\n"
            ),
            "shadow_forward_audit.csv": (
                "observed_trading_days,status\n"
                "0,not_observable\n"
            ),
        }
        for name, content in files.items():
            (output_dir / name).write_text(content, encoding="utf-8")

        text = "\n".join(strategy_profile_summary(output_dir, "自适应候选策略"))

        self.assertIn("adaptive_candidate", text)
        self.assertIn("46.71% / 25.63%", text)
        self.assertIn("累计超额  21.08%", text)
        self.assertIn("信息比率 / 最大回撤  0.40 / -17.41%", text)
        self.assertIn("研究可信度  高可信/92.9", text)
        self.assertIn("参数邻域稳定性  通过", text)
        self.assertIn("实用门禁  通过", text)
        self.assertIn("严格实盘  未通过 2", text)
        self.assertIn("0/60 交易日", text)
        self.assertIn("not_cleared", text)
        self.assertIn("not_observable", text)
        self.assertIn("严格实盘仍被前向验证阻断", text)
    def test_execution_dashboard_highlights_blockers_and_next_commands(self):
        lines = execution_dashboard_lines(
            execution=[
                {
                    "check": "order_source_verified",
                    "passed": "False",
                    "value": "backtest_or_unverified",
                    "next_action": "运行 `python -m ashare_quant paper --practical`。",
                    "severity": "execution_blocker",
                }
            ],
            execution_modes=[
                {
                    "mode": "manual_ticket_review",
                    "status": "blocked",
                    "blockers": "order_source_verified",
                },
                {
                    "mode": "strict_live_execution",
                    "status": "blocked",
                    "blockers": "order_source_verified;strict_live_readiness",
                },
            ],
            roadmap=[
                {
                    "area": "订单生成",
                    "command": "python -m ashare_quant practical",
                    "next_action": "用 practical 流程生成经过门禁的 paper 订单。",
                }
            ],
            account_plan=[
                {
                    "path": "模拟账户路径",
                    "command": "python -m ashare_quant account-reset --confirm",
                    "next_action": "只做模拟盘时重置账户。",
                }
            ],
            account_path_decision=[
                {
                    "path": "real_account_priced_only_import",
                    "status": "available_with_exclusions",
                    "blockers": "",
                }
            ],
            order_source=[
                {"check": "orders_file_context", "value": "backtest_or_unverified", "passed": "False"},
                {"check": "rpa_exportable", "value": "False", "passed": "False"},
            ],
            account_state=[{"check": "ok", "passed": "True"}],
            account_reconciliation=[],
            account_import_check=[{"check": "source_sha256", "passed": "True"}],
            tickets=[],
        )
        text = "\n".join(lines)
        self.assertIn("订单来源: backtest_or_unverified", text)
        self.assertIn("RPA 可导出: False", text)
        self.assertIn("order_source_verified", text)
        self.assertIn("python -m ashare_quant practical", text)
        self.assertIn("python -m ashare_quant account-reset --confirm", text)
        self.assertIn("人工工单阻断项: 1", text)
        self.assertIn("严格实盘待验证项: 0", text)
        self.assertIn("人工工单核对: blocked", text)
        self.assertIn("严格实盘执行: blocked", text)
        self.assertIn("真实账户可估值导入: available_with_exclusions", text)


if __name__ == "__main__":
    unittest.main()
