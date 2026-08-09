import unittest
from pathlib import Path

import pandas as pd

from ashare_quant.dashboard import generate_control_panel, has_mojibake, recommended_commands


class DashboardTests(unittest.TestCase):
    def test_generate_control_panel_uses_clean_chinese_and_one_click_entry(self):
        output = Path.cwd() / "test_outputs" / "dashboard"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}

        pd.DataFrame(
            [
                {"metric": "total_return", "value": 0.12},
                {"metric": "benchmark_total_return", "value": 0.08},
                {"metric": "excess_total_return", "value": 0.04},
                {"metric": "max_drawdown", "value": -0.09},
            ]
        ).to_csv(output / "performance_metrics.csv", index=False)
        pd.DataFrame(
            [
                {"item": "price_symbol_count", "value": 800},
                {"item": "backtest_trading_days", "value": 843},
            ]
        ).to_csv(output / "data_quality.csv", index=False)
        pd.DataFrame([{"item": "backtest_trading_days", "value": 843}]).to_csv(output / "strategy_reliability.csv", index=False)
        pd.DataFrame(
            [
                {"area": "forward_validation", "status": "collecting", "current": 9, "required": 60, "remaining": 51, "next_action": "继续每日观察。"},
                {"area": "shadow_observation", "status": "collecting", "current": 1, "required": 20, "remaining": 19, "next_action": "继续记录 Shadow。"},
                {"area": "account_audit", "status": "blocked", "current": 1, "required": 0, "remaining": 1, "next_action": "修复账户缺价。"},
            ]
        ).to_csv(output / "evidence_plan.csv", index=False)
        pd.DataFrame(
            [
                {"check": "account_state_audit", "passed": False, "value": "5/7", "next_action": "修复账户"},
                {"check": "rpa_pre_trade_checks", "passed": False, "value": "blocked", "next_action": "刷新订单审计"},
            ]
        ).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame([{"check": "practical", "passed": True, "value": "ok"}]).to_csv(output / "practical_readiness.csv", index=False)
        pd.DataFrame([{"check": "strict", "passed": False, "value": "collecting"}]).to_csv(output / "live_readiness.csv", index=False)
        pd.DataFrame([{"check": "price_cache_age_days", "passed": True, "value": 1, "next_action": ""}]).to_csv(output / "data_source_health.csv", index=False)
        pd.DataFrame([{"price_context": "mark", "symbol": "000002", "quantity": 100, "cost": 9.5}]).to_csv(output / "account_state_missing_prices.csv", index=False)
        pd.DataFrame([{"check": "excluded_position_count", "passed": False, "value": 1, "threshold": 0, "severity": "account_blocker", "next_action": "review"}]).to_csv(output / "account_exclusion_impact.csv", index=False)
        pd.DataFrame([{"symbol": "000002", "quantity": 100, "cost": 9.5, "cost_proxy": 950.0, "cost_proxy_ratio": 0.001, "price_status": "missing_price", "suggested_action": "review"}]).to_csv(output / "account_exclusion_impact_detail.csv", index=False)
        pd.DataFrame(
            [
                {"rank": 1, "strategy_name": "liqrob100", "promotion_status": "collecting_evidence", "forward_days": 9, "required_forward_days": 60, "shadow_observed_signals": 1, "shadow_required_signals": 20}
            ]
        ).to_csv(output / "strategy_promotion.csv", index=False)
        pd.DataFrame(
            [
                {"strategy_name": "liqrob100", "total_return": 0.12, "benchmark_total_return": 0.08, "excess_total_return": 0.04, "max_drawdown": -0.09, "information_ratio": 0.3, "live_readiness_passed": False}
            ]
        ).to_csv(output / "strategy_backtest_compare.csv", index=False)
        pd.DataFrame(
            [
                {"status": "blocked_execution_and_evidence", "auto_live_allowed": False, "manual_ticket_ready": False, "strict_live_ready": False, "execution_ready": False, "blockers": "account_state_audit;strict_live_execution_mode"}
            ]
        ).to_csv(output / "auto_live_summary.csv", index=False)
        pd.DataFrame(
            [
                {
                    "status": "observation_only",
                    "strategy_operating_mode": "successor_observation",
                    "strategy_operating_message": "正式策略已暂停；继续观察 robust。",
                }
            ]
        ).to_csv(output / "one_click_summary.csv", index=False)
        pd.DataFrame(
            [
                {"check": "adapter_name_present", "passed": False, "value": "missing", "threshold": "configured adapter name", "severity": "auto_blocker", "next_action": "配置适配器"}
            ]
        ).to_csv(output / "auto_live_adapter_audit.csv", index=False)
        pd.DataFrame(
            [
                {"run_id": "r1", "status": "blocked", "adapter": "missing", "order_count": 2, "submitted_count": 0, "blocked_reason": "account_state_audit", "tickets_sha256": "a" * 64, "auto_live_config_sha256": "b" * 64, "generated_at": "2026-07-03T10:00:00"}
            ]
        ).to_csv(output / "auto_live_run.csv", index=False)
        pd.DataFrame([{"ticket_id": "t1", "symbol": "600000", "side": "buy", "quantity": 100, "limit_price": 10.0, "status": "ready"}]).to_csv(output / "rpa_orders.csv", index=False)
        pd.DataFrame([{"check": "rpa_safety_policy", "passed": True, "value": "ok", "threshold": "ok", "severity": "ok", "next_action": ""}]).to_csv(output / "rpa_preflight.csv", index=False)

        path = generate_control_panel(config)
        html = path.read_text(encoding="utf-8")

        self.assertTrue(path.exists())
        self.assertIn("A 股量化控制面板", html)
        self.assertIn("一键运行", html)
        self.assertIn("策略运行模式", html)
        self.assertIn("successor_observation", html)
        self.assertIn("正式策略已暂停；继续观察 robust。", html)
        self.assertIn(".\\.venv\\Scripts\\python.exe -m ashare_quant run --allow-stale", html)
        self.assertIn("策略收益", html)
        self.assertIn("证据进度", html)
        self.assertIn("前向验证", html)
        self.assertIn("Shadow 样本", html)
        self.assertIn("账户缺价排除影响", html)
        self.assertIn("RPA 前置环境审计", html)
        self.assertIn("自动实盘提交时间窗", html)
        self.assertIn("自动实盘提交锁审计", html)
        self.assertIn("自动实盘独立风控", html)
        self.assertIn("适配器文件哈希", html)
        self.assertIn("适配器回放认证", html)
        self.assertIn("适配器回放认证明细", html)
        self.assertIn("自动实盘审计链", html)
        self.assertIn("自动实盘审计链校验", html)
        self.assertIn("自动实盘事件日志", html)
        self.assertIn("自动实盘审计写入错误", html)
        self.assertIn("自动实盘账户绑定审计", html)
        self.assertIn("自动实盘恢复点", html)
        self.assertIn("自动实盘恢复审计", html)
        self.assertIn("自动实盘告警通道审计", html)
        self.assertIn("自动实盘告警自检", html)
        self.assertIn("自动实盘告警发件箱", html)
        self.assertIn("自动实盘分阶段授权审计", html)
        self.assertIn("自动实盘分阶段授权历史", html)
        self.assertIn("自动实盘服务", html)
        self.assertIn("自动实盘服务心跳", html)
        self.assertIn("自动实盘服务锁审计", html)
        self.assertIn("自动实盘服务周期历史", html)
        self.assertIn("自动实盘服务停止记录", html)
        self.assertIn("无人值守：", html)
        self.assertIn("自动实盘当日风险预留", html)
        self.assertIn("自动实盘紧急停机记录", html)
        self.assertIn("自动实盘批次结论", html)
        self.assertIn("自动实盘批次计划", html)
        self.assertIn("自动实盘当前批次", html)
        self.assertIn("自动实盘周期结论", html)
        self.assertIn("自动实盘周期阶段", html)
        self.assertIn("自动实盘后交易账户对账", html)
        self.assertIn("自动实盘提交意图", html)
        self.assertIn("自动实盘被阻断尝试", html)
        self.assertIn("自动实盘券商账户自动刷新", html)
        self.assertIn("自动实盘不确定提交恢复", html)
        self.assertIn("自动实盘适配器审计", html)
        self.assertIn('class="table-scroll"', html)
        self.assertIn("overflow-x:hidden", html)
        self.assertIn("min-width:0", html)
        self.assertIn("自动实盘运行审计", html)
        self.assertIn("account-price-template", html)
        self.assertIn("liqrob100", html)
        self.assertFalse(has_mojibake(html))

    def test_recommended_commands_include_price_override_steps_for_missing_account_prices(self):
        commands = recommended_commands(
            promotion=pd.DataFrame(),
            practical=pd.DataFrame(),
            strict=pd.DataFrame(),
            execution=pd.DataFrame([{"check": "account_state_audit", "passed": False}]),
            source_health=pd.DataFrame(),
            account_missing_prices=pd.DataFrame([{"symbol": "000002"}]),
            account_price_audit=pd.DataFrame(),
        )
        command_text = " ".join(item["command"] for item in commands)
        self.assertIn(".\\.venv\\Scripts\\python.exe -m ashare_quant run", commands[0]["command"])
        self.assertIn("account-price-template", command_text)
        self.assertIn("account-price-check", command_text)

    def test_recommended_commands_keeps_one_click_as_primary(self):
        commands = recommended_commands(
            promotion=pd.DataFrame(),
            practical=pd.DataFrame(),
            strict=pd.DataFrame(),
            execution=pd.DataFrame(),
            source_health=pd.DataFrame(),
        )
        self.assertEqual(commands[0]["command"], ".\\.venv\\Scripts\\python.exe -m ashare_quant run --allow-stale")
        self.assertTrue(any(item["command"] == ".\\.venv\\Scripts\\python.exe -m ashare_quant execution-drill" for item in commands))


if __name__ == "__main__":
    unittest.main()
