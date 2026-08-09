from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .autolive import write_auto_live_status
from .autolive_service import write_auto_live_service_lock_audit
from .autolive_window import write_auto_live_window_audit
from .config import project_path


CONTROL_PANEL = "control_panel.html"
MOJIBAKE_MARKERS = (
    "锟斤拷",
    "�",
    "閿",
    "閼",
    "閸",
    "鐎",
    "缁",
    "绛栫",
    "鑲",
    "瀹炵",
)

COLUMN_LABELS = {
    "area": "领域",
    "status": "状态",
    "current": "当前",
    "required": "要求",
    "remaining": "剩余",
    "blocker": "阻断项",
    "command": "命令",
    "next_action": "下一步",
    "check": "检查项",
    "passed": "通过",
    "value": "当前值",
    "threshold": "阈值",
    "severity": "级别",
    "message": "说明",
    "symbol": "证券代码",
    "side": "方向",
    "quantity": "数量",
    "limit_price": "限价",
    "estimated_amount": "预计金额",
    "amount_participation": "成交额占比",
    "volume_participation": "成交量占比",
    "strategy_name": "策略",
    "total_return": "策略收益",
    "benchmark_total_return": "基准收益",
    "excess_total_return": "超额收益",
    "max_drawdown": "最大回撤",
    "information_ratio": "信息比率",
    "walk_forward_avg_excess_return": "走步平均超额",
    "walk_forward_positive_ratio": "走步胜率",
    "live_readiness_passed": "严格门禁",
    "item": "项目",
    "adapter": "适配器",
    "order_count": "工单数",
    "submitted_count": "提交数",
    "blocked_reason": "阻断原因",
    "generated_at": "生成时间",
    "required_before": "前置条件",
    "stop_on_failure": "失败停止",
    "expected_output": "预期产物",
    "success_condition": "通过条件",
    "note": "说明",
    "ticket_id": "工单 ID",
    "broker_order_id": "券商订单号",
    "submission_status": "提交状态",
    "broker_status": "券商状态",
    "resolved": "已恢复",
    "action": "处理动作",
    "remaining_blockers": "剩余阻断",
    "state": "意图状态",
    "request_file": "请求文件",
    "result_file": "结果文件",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "buy_amount": "买入金额",
    "sell_amount": "卖出金额",
    "gross_amount": "交易总额",
    "trade_date": "交易日期",
    "action": "操作",
}

AREA_LABELS = {
    "forward_validation": "前向验证",
    "shadow_observation": "Shadow 样本",
    "account_audit": "账户审计",
    "rpa_pre_trade": "RPA 前置检查",
    "strict_live_gate": "严格实盘门禁",
    "auto_live_config": "自动实盘配置",
    "order_capacity": "订单容量",
    "execution_gate": "执行门禁",
    "auto_live_gate": "自动实盘门禁",
    "auto_live_dry_run": "自动实盘 Dry-run",
    "auto_live_submit_window": "提交时间窗",
    "auto_live_account_refresh": "券商账户刷新",
    "reporting": "报告刷新",
}


def generate_control_panel(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    write_auto_live_status(config)
    write_auto_live_window_audit(config)
    write_auto_live_service_lock_audit(config)
    frames = {
        "metrics": read_csv(output_dir / "performance_metrics.csv"),
        "quality": read_csv(output_dir / "data_quality.csv"),
        "promotion": read_csv(output_dir / "strategy_promotion.csv"),
        "practical": read_csv(output_dir / "practical_readiness.csv"),
        "strict": read_csv(output_dir / "live_readiness.csv"),
        "execution": read_csv(output_dir / "execution_readiness.csv"),
        "execution_modes": read_csv(output_dir / "execution_modes.csv"),
        "source_health": read_csv(output_dir / "data_source_health.csv"),
        "account_paths": read_csv(output_dir / "account_path_decision.csv"),
        "account_missing_prices": read_csv(output_dir / "account_state_missing_prices.csv"),
        "account_price_audit": read_csv(output_dir / "account_price_override_audit.csv"),
        "account_exclusion_impact": read_csv(output_dir / "account_exclusion_impact.csv"),
        "account_exclusion_impact_detail": read_csv(output_dir / "account_exclusion_impact_detail.csv"),
        "reliability": read_csv(output_dir / "strategy_reliability.csv"),
        "forward_progress": read_csv(output_dir / "live_candidate_forward_progress.csv"),
        "shadow_audit": read_csv(output_dir / "shadow_forward_audit.csv"),
        "decision": read_csv(output_dir / "strategy_promotion_decision.csv"),
        "strategy_health": read_csv(output_dir / "strategy_health.csv"),
        "rpa_orders": read_csv(output_dir / "rpa_orders.csv"),
        "rpa_blocked_orders": read_csv(output_dir / "rpa_blocked_orders.csv"),
        "rpa_preflight": read_csv(output_dir / "rpa_preflight.csv"),
        "order_liquidity": read_csv(output_dir / "order_liquidity_audit.csv"),
        "execution_quality": read_csv(output_dir / "execution_quality.csv"),
        "factor_robustness_summary": read_csv(output_dir / "factor_robustness_summary.csv"),
        "backtest_compare": read_csv(output_dir / "strategy_backtest_compare.csv"),
        "auto_live_summary": read_csv(output_dir / "auto_live_summary.csv"),
        "auto_live_journal_summary": read_csv(output_dir / "auto_live_journal_summary.csv"),
        "auto_live_journal_audit": read_csv(output_dir / "auto_live_journal_audit.csv"),
        "auto_live_event_journal": read_csv(output_dir / "auto_live_event_journal.csv"),
        "auto_live_journal_errors": read_csv(output_dir / "auto_live_journal_errors.csv"),
        "auto_live_account_binding_audit": read_csv(output_dir / "auto_live_account_binding_audit.csv"),
        "auto_live_recovery_points": read_csv(output_dir / "auto_live_recovery_points.csv"),
        "auto_live_recovery_audit": read_csv(output_dir / "auto_live_recovery_audit.csv"),
        "auto_live_alert_audit": read_csv(output_dir / "auto_live_alert_audit.csv"),
        "auto_live_alert_outbox": read_csv(output_dir / "auto_live_alert_outbox.csv"),
        "auto_live_alert_selftest": read_csv(output_dir / "auto_live_alert_selftest.csv"),
        "auto_live_rollout_audit": read_csv(output_dir / "auto_live_rollout_audit.csv"),
        "auto_live_rollout_history": read_csv(output_dir / "auto_live_rollout_history.csv"),
        "auto_live_service_summary": read_csv(output_dir / "auto_live_service_summary.csv"),
        "auto_live_service_history": read_csv(output_dir / "auto_live_service_history.csv"),
        "auto_live_service_heartbeat": read_csv(output_dir / "auto_live_service_heartbeat.csv"),
        "auto_live_service_lock_audit": read_csv(output_dir / "auto_live_service_lock_audit.csv"),
        "auto_live_service_stop_audit": read_csv(output_dir / "auto_live_service_stop_audit.csv"),
        "auto_live_batch_summary": read_csv(output_dir / "auto_live_batch_summary.csv"),
        "auto_live_batch_plan": read_csv(output_dir / "auto_live_batch_plan.csv"),
        "auto_live_current_batch": read_csv(output_dir / "auto_live_current_batch.csv"),
        "auto_live_cycle_summary": read_csv(output_dir / "auto_live_cycle_summary.csv"),
        "auto_live_cycle": read_csv(output_dir / "auto_live_cycle.csv"),
        "auto_live_cycle_history": read_csv(output_dir / "auto_live_cycle_history.csv"),
        "auto_live_post_trade_reconciliation": read_csv(output_dir / "auto_live_post_trade_reconciliation.csv"),
        "auto_live_post_trade_manifest": read_csv(output_dir / "auto_live_post_trade_manifest.csv"),
        "auto_live_post_trade_reconciliation_history": read_csv(output_dir / "auto_live_post_trade_reconciliation_history.csv"),
        "auto_live_readiness": read_csv(output_dir / "auto_live_readiness.csv"),
        "auto_live_window_audit": read_csv(output_dir / "auto_live_window_audit.csv"),
        "auto_live_submit_lock_audit": read_csv(output_dir / "auto_live_submit_lock_audit.csv"),
        "auto_live_risk_audit": read_csv(output_dir / "auto_live_risk_audit.csv"),
        "auto_live_risk_reservations": read_csv(output_dir / "auto_live_risk_reservations.csv"),
        "auto_live_stop_audit": read_csv(output_dir / "auto_live_stop_audit.csv"),
        "auto_live_submission_intent": read_csv(output_dir / "auto_live_submission_intent.csv"),
        "auto_live_blocked_attempt": read_csv(output_dir / "auto_live_blocked_attempt.csv"),
        "auto_live_account_refresh": read_csv(output_dir / "auto_live_account_refresh.csv"),
        "auto_live_broker_account_snapshot": read_csv(output_dir / "auto_live_broker_account_snapshot.csv"),
        "auto_live_broker_positions": read_csv(output_dir / "auto_live_broker_positions.csv"),
        "auto_live_broker_account_audit": read_csv(output_dir / "auto_live_broker_account_audit.csv"),
        "auto_live_run": read_csv(output_dir / "auto_live_run.csv"),
        "auto_live_order_results": read_csv(output_dir / "auto_live_order_results.csv"),
        "auto_live_ack_audit": read_csv(output_dir / "auto_live_ack_audit.csv"),
        "auto_live_order_status": read_csv(output_dir / "auto_live_order_status.csv"),
        "auto_live_order_status_audit": read_csv(output_dir / "auto_live_order_status_audit.csv"),
        "auto_live_submission_recovery": read_csv(output_dir / "auto_live_submission_recovery.csv"),
        "auto_live_cancel_results": read_csv(output_dir / "auto_live_cancel_results.csv"),
        "auto_live_cancel_audit": read_csv(output_dir / "auto_live_cancel_audit.csv"),
        "auto_live_fill_import": read_csv(output_dir / "auto_live_fill_import.csv"),
        "auto_live_fill_progress": read_csv(output_dir / "auto_live_fill_progress.csv"),
        "auto_live_fill_sync_audit": read_csv(output_dir / "auto_live_fill_sync_audit.csv"),
        "auto_live_settlement_audit": read_csv(output_dir / "auto_live_settlement_audit.csv"),
        "auto_live_submission_ledger": read_csv(output_dir / "auto_live_submission_ledger.csv"),
        "auto_live_adapter_audit": read_csv(output_dir / "auto_live_adapter_audit.csv"),
        "auto_live_adapter_selftest": read_csv(output_dir / "auto_live_adapter_selftest.csv"),
        "auto_live_adapter_replay_summary": read_csv(output_dir / "auto_live_adapter_replay_summary.csv"),
        "auto_live_adapter_replay_audit": read_csv(output_dir / "auto_live_adapter_replay_audit.csv"),
        "auto_live_adapter_file_audit": read_csv(output_dir / "auto_live_adapter_file_audit.csv"),
        "auto_live_blocker_details": read_csv(output_dir / "auto_live_blocker_details.csv"),
        "auto_live_adapter_manifest": read_csv(output_dir / "auto_live_adapter_manifest.csv"),
        "evidence_plan": read_csv(output_dir / "evidence_plan.csv"),
        "observation_schedule": read_csv(output_dir / "observation_schedule.csv"),
        "daily_observation_summary": read_csv(output_dir / "daily_observation_summary.csv"),
        "daily_observation_progress": read_csv(output_dir / "daily_observation_progress.csv"),
        "daily_observation_progress_audit": read_csv(output_dir / "daily_observation_progress_audit.csv"),
        "auto_live_remediation_plan": read_csv(output_dir / "auto_live_remediation_plan.csv"),
        "auto_live_verification_plan": read_csv(output_dir / "auto_live_verification_plan.csv"),
        "one_click_summary": read_csv(output_dir / "one_click_summary.csv"),
        "local_operator_status": read_csv(output_dir / "local_operator_status.csv"),
    }
    path = output_dir / CONTROL_PANEL
    path.write_text(render_control_panel(config, frames), encoding="utf-8")
    return path


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def render_control_panel(config: dict[str, Any], frames: dict[str, pd.DataFrame]) -> str:
    metrics = metric_map(frames["metrics"])
    quality = item_map(frames["quality"])
    reliability = item_map(frames["reliability"])
    commands = recommended_commands(
        frames["promotion"],
        frames["practical"],
        frames["strict"],
        frames["execution"],
        frames["source_health"],
        account_missing_prices=frames["account_missing_prices"],
        account_price_audit=frames["account_price_audit"],
        strategy_health=frames["strategy_health"],
    )
    primary = commands[0]
    secondary = commands[1:4]
    auto_summary = frames["auto_live_summary"]
    one_click = frames["one_click_summary"]
    auto_allowed = auto_live_allowed(auto_summary)
    auto_label = "允许自动实盘" if auto_allowed else "未允许自动实盘"
    auto_class = "pass" if auto_allowed else "fail"
    one_click_status = frame_first(one_click, "status") or "未运行"
    one_click_mode, one_click_message = dashboard_strategy_mode(frames)
    cycle_status = frame_first(frames["auto_live_cycle_summary"], "status") or "未运行"
    service_status = frame_first(frames["auto_live_service_summary"], "status") or "未运行"
    service_class = "pass" if service_status in {"completed_once", "completed_max_cycles"} else "wait" if service_status in {"running", "running_with_blockers", "running_with_failures"} else "fail"
    cycle_class = "pass" if cycle_status in {"completed", "completed_and_synced", "audit_complete"} else "wait" if cycle_status in {"running", "audit_complete_with_blockers"} else "fail"
    observation_status = frame_first(frames["daily_observation_summary"], "status") or "not_run"
    observation_required = bool_value(frame_first(frames["daily_observation_summary"], "required"))
    observation_class = "pass" if observation_status in {"completed", "running"} else "fail" if observation_required else "wait"
    progress_audit = frames["daily_observation_progress_audit"]
    progress_failures = 0 if progress_audit.empty or "passed" not in progress_audit.columns else int((~progress_audit["passed"].map(bool_value)).sum())
    progress_status = "passed" if progress_failures == 0 and not progress_audit.empty else "not_run" if progress_audit.empty else f"failed:{progress_failures}"
    progress_class = "pass" if progress_status == "passed" else "wait" if progress_status == "not_run" else "fail"

    cards = "".join(
        metric_card(label, value)
        for label, value in [
            ("策略收益", format_percent(metrics.get("total_return"))),
            ("基准收益", format_percent(metrics.get("benchmark_total_return"))),
            ("超额收益", format_percent(metrics.get("excess_total_return"))),
            ("最大回撤", format_percent(metrics.get("max_drawdown"))),
            ("回测交易日", str(reliability.get("backtest_trading_days") or "-")),
            ("行情股票数", str(quality.get("price_symbol_count") or "-")),
        ]
    )
    secondary_html = "".join(command_card(item) for item in secondary) or '<p class="muted">暂无额外操作。</p>'

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A 股量化控制面板</title>
  <style>
    :root {{
      color-scheme: light;
      --bg:#f5f5f7; --panel:#ffffff; --text:#1d1d1f; --muted:#6e6e73;
      --line:#d2d2d7; --blue:#007aff; --green:#248a3d; --orange:#b25f00; --red:#c21d14;
    }}
    * {{ box-sizing:border-box; }}
    html, body {{ max-width:100%; overflow-x:hidden; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI",sans-serif; }}
    main {{ width:100%; max-width:1120px; margin:0 auto; padding:28px 18px 42px; }}
    header {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:30px; line-height:1.18; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:18px; line-height:1.3; letter-spacing:0; }}
    h3 {{ margin:0 0 8px; font-size:15px; letter-spacing:0; }}
    p {{ margin:6px 0 0; color:var(--muted); line-height:1.55; }}
    a {{ color:var(--blue); text-decoration:none; }}
    .status-row {{ display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }}
    .pill {{ display:inline-flex; align-items:center; min-height:28px; border-radius:999px; padding:4px 10px; font-size:13px; font-weight:650; border:1px solid transparent; }}
    .pass {{ color:var(--green); background:#eaf7ed; border-color:#c7ebcf; }}
    .wait {{ color:var(--orange); background:#fff7e8; border-color:#f0d29a; }}
    .fail {{ color:var(--red); background:#fde8e7; border-color:#f2c2bf; }}
    .muted {{ color:var(--muted); }}
    .section {{ margin-top:12px; }}
    .panel {{ min-width:0; background:var(--panel); border:1px solid rgba(0,0,0,.08); border-radius:8px; padding:16px; box-shadow:0 10px 26px rgba(0,0,0,.04); }}
    .hero {{ display:grid; grid-template-columns:minmax(0,1.25fr) minmax(280px,.75fr); gap:12px; align-items:stretch; }}
    .primary {{ border-color:rgba(0,122,255,.28); box-shadow:0 14px 34px rgba(0,122,255,.08); }}
    .command {{ display:block; margin-top:12px; white-space:pre-wrap; word-break:break-word; background:#f0f0f2; color:#111; padding:12px; border-radius:8px; font-family:"SFMono-Regular",Consolas,monospace; font-size:13px; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
    .two {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .label {{ color:var(--muted); font-size:13px; }}
    .value {{ font-size:24px; font-weight:720; margin-top:6px; overflow-wrap:anywhere; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ text-align:left; border-bottom:1px solid #e5e5ea; padding:8px; vertical-align:top; }}
    th {{ color:var(--muted); font-weight:650; background:#fbfbfd; }}
    td {{ overflow-wrap:anywhere; }}
    .table-scroll {{ width:100%; max-width:100%; overflow-x:auto; -webkit-overflow-scrolling:touch; }}
    .table-scroll table {{ min-width:640px; }}
    .summary-table table {{ min-width:0; }}
    .progress-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
    .progress-item {{ border:1px solid #e5e5ea; border-radius:8px; padding:12px; background:#fbfbfd; }}
    .progress-head {{ display:flex; justify-content:space-between; gap:8px; align-items:center; margin-bottom:8px; }}
    .progress-title {{ font-weight:700; font-size:14px; }}
    .progress-value {{ font-weight:700; color:var(--muted); font-size:13px; }}
    .progress-track {{ height:8px; background:#e5e5ea; border-radius:999px; overflow:hidden; }}
    .progress-fill {{ height:100%; background:var(--blue); border-radius:999px; }}
    .footer {{ margin-top:14px; }}
    @media (max-width:860px) {{ header,.hero,.two {{ display:block; }} .grid,.progress-grid {{ grid-template-columns:1fr; }} .panel {{ margin-bottom:12px; }} h1 {{ font-size:25px; }} }}
    @media (max-width:600px) {{ .status-row {{ justify-content:flex-start; margin-top:12px; }} .command {{ font-size:12px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>A 股量化控制面板</h1>
      <p>本地研究、模拟盘、执行审计与自动实盘门禁汇总。</p>
    </div>
    <div class="status-row">
      {status_pill('实用', collection_status(frames['practical']))}
      {status_pill('严格实盘', collection_status(frames['strict']))}
      {status_pill('执行', collection_status(frames['execution']))}
      {status_pill('独立风控', collection_status(frames['auto_live_risk_audit']))}
      <span class="pill {cycle_class}">运行周期：{escape(cycle_status)}</span>
      <span class="pill {service_class}">无人值守：{escape(service_status)}</span>
      <span class="pill {auto_class}">{escape(auto_label)}</span>
      <span class="pill {observation_class}">每日观察：{escape(observation_status)}</span>
      <span class="pill {progress_class}">证据推进：{escape(progress_status)}</span>
    </div>
  </header>

  <section class="hero">
    <div class="panel primary">
      <h2>一键运行</h2>
      <p>{escape(primary['note'])}</p>
      <code class="command">{escape(primary['command'])}</code>
    </div>
    <div class="panel">
      <h2>当前结论</h2>
      {auto_live_summary_html(auto_summary)}
      <p>最近一键运行状态：{escape(one_click_status)}</p>
      <p>策略运行模式：{escape(one_click_mode)}</p>
      <p>{escape(one_click_message)}</p>
    </div>
  </section>
      <p>每日观察任务：{escape(observation_status)}</p>
      <p>候选证据进度审计：{escape(progress_status)}</p>

  <section class="grid section">{cards}</section>

  <section class="panel section">
    <h2>证据进度</h2>
    {evidence_progress(frames['evidence_plan'], frames['promotion'], frames['forward_progress'], frames['shadow_audit'])}
  </section>

  <section class="two section">
    <div class="panel">
      <h2>主要阻断</h2>
      {blocking_table(frames)}
    </div>
    <div class="panel">
      <h2>辅助操作</h2>
      {secondary_html}
    </div>
  </section>

  <section class="panel section">
    <h2>自动实盘审计链</h2>
    {small_table(frames['auto_live_journal_summary'], ['status','event_count','last_sequence','hmac_required','hmac_key_present','passed_checks','total_checks','generated_at','message'], '暂无自动实盘审计链结论。')}
  </section>

  <section class="panel section">
    <h2>自动实盘审计链校验</h2>
    {small_table(frames['auto_live_journal_audit'], ['check','passed','value','threshold','severity','next_action','message'], '暂无自动实盘审计链校验。')}
  </section>

  <section class="panel section">
    <h2>自动实盘事件日志</h2>
    {small_table(frames['auto_live_event_journal'], ['sequence','event_type','event_id','event_time','prev_event_hash','event_hash','hmac_sha256'], '暂无自动实盘事件。')}
  </section>

  <section class="panel section">
    <h2>自动实盘审计写入错误</h2>
    {small_table(frames['auto_live_journal_errors'], ['event_key','event_type','event_id','error','resolved','generated_at'], '暂无审计写入错误。')}
  </section>

  <section class="panel section">
    <h2>自动实盘账户绑定审计</h2>
    {small_table(frames['auto_live_account_binding_audit'], ['check','passed','value','threshold','severity','next_action','message'], '暂无账户绑定审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘恢复点</h2>
    {small_table(frames['auto_live_recovery_points'], ['recovery_id','run_id','status','state_db_sha256','tickets_sha256','file_count','path','created_at'], '暂无提交前恢复点。')}
  </section>

  <section class="panel section">
    <h2>自动实盘恢复审计</h2>
    {small_table(frames['auto_live_recovery_audit'], ['check','passed','value','threshold','severity','next_action','message'], '暂无恢复审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘告警通道审计</h2>
    {small_table(frames['auto_live_alert_audit'], ['check','passed','value','threshold','severity','next_action','message'], '暂无告警通道审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘告警自检</h2>
    {small_table(frames['auto_live_alert_selftest'], ['run_id','status','environment','capabilities','config_sha256','generated_at','message'], '暂无告警自检。')}
  </section>

  <section class="panel section">
    <h2>自动实盘告警发件箱</h2>
    {small_table(frames['auto_live_alert_outbox'], ['alert_id','event_type','severity','status','attempts','environment','message','created_at','last_attempt_at','delivered_at','error'], '暂无告警事件。')}
  </section>

  <section class="panel section">
    <h2>自动实盘分阶段授权审计</h2>
    {small_table(frames['auto_live_rollout_audit'], ['check','passed','value','threshold','severity','next_action','message'], '暂无分阶段授权审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘分阶段授权历史</h2>
    {small_table(frames['auto_live_rollout_history'], ['action','approval_id','stage','account_id','auto_live_config_sha256','expires_at','reason','generated_at'], '暂无授权或撤销历史。')}
  </section>

  <section class="panel section">
    <h2>自动实盘服务</h2>
    {small_table(frames['auto_live_service_summary'], ['service_run_id','mode','status','cycle_count','completed_cycle_count','blocked_cycle_count','failed_cycle_count','consecutive_failures','stop_requested','started_at','completed_at','message'], '暂无自动实盘服务运行记录。')}
  </section>

  <section class="panel section">
    <h2>自动实盘服务心跳</h2>
    {small_table(frames['auto_live_service_heartbeat'], ['service_run_id','pid','host','mode','status','service_cycle_no','last_cycle_id','last_cycle_status','heartbeat_at','next_run_at','message'], '暂无自动实盘服务心跳。')}
  </section>

  <section class="panel section">
    <h2>自动实盘服务锁审计</h2>
    {small_table(frames['auto_live_service_lock_audit'], ['check','passed','value','threshold','severity','next_action','message'], '暂无自动实盘服务锁审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘服务周期历史</h2>
    {small_table(frames['auto_live_service_history'], ['service_run_id','service_cycle_no','mode','cycle_id','cycle_status','blocking_stage','submitted_count','queried_count','fill_count','started_at','completed_at','message'], '暂无自动实盘服务周期历史。')}
  </section>

  <section class="panel section">
    <h2>自动实盘服务停止记录</h2>
    {small_table(frames['auto_live_service_stop_audit'], ['action','passed','value','message','generated_at'], '暂无自动实盘服务停止记录。')}
  </section>

  <section class="panel section">
    <h2>自动实盘批次结论</h2>
    {small_table(frames['auto_live_batch_summary'], ['plan_id','status','source_order_count','planned_order_count','blocked_order_count','batch_count','completed_batch_count','active_batch_count','selected_batch_no','selected_order_count','queued_order_count','message'], '暂无自动实盘批次结论。运行 python -m ashare_quant auto-live-batch 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘批次计划</h2>
    {small_table(frames['auto_live_batch_plan'], ['batch_no','batch_id','status','selected','order_count','sell_count','buy_count','sell_amount','buy_amount','gross_amount','source_run_id','message'], '暂无自动实盘批次计划。')}
  </section>

  <section class="panel section">
    <h2>自动实盘当前批次</h2>
    {small_table(frames['auto_live_current_batch'], ['ticket_id','date','symbol','side','quantity','limit_price','estimated_amount','status'], '当前没有允许提交的批次。')}
  </section>

  <section class="panel section">
    <h2>自动实盘周期结论</h2>
    {small_table(frames['auto_live_cycle_summary'], ['cycle_id','mode','status','blocking_stage','submitted_count','queried_count','fill_count','account_sync_requested','account_sync_completed','started_at','completed_at','message'], '暂无自动实盘周期结论。运行 python -m ashare_quant auto-live-cycle 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘周期阶段</h2>
    {small_table(frames['auto_live_cycle'], ['stage_order','stage','status','required','artifact','started_at','completed_at','message'], '暂无自动实盘周期阶段记录。')}
  </section>

  <section class="panel section">
    <h2>自动实盘后交易账户对账</h2>
    {small_table(frames['auto_live_post_trade_reconciliation'], ['cycle_id','check','passed','value','threshold','severity','next_action','message'], '暂无后交易账户对账。仅在显式成交写回后生成。')}
  </section>

  <section class="panel section">
    <h2>适配器文件哈希</h2>
    {small_table(frames['auto_live_adapter_file_audit'], ['check','passed','value','threshold','severity','path','expected_sha256','actual_sha256','next_action','message'], '暂无适配器文件哈希审计。')}
  </section>

  <section class="panel section">
    <h2>适配器回放认证</h2>
    {small_table(frames['auto_live_adapter_replay_summary'], ['status','profile','request_count','result_count','submit_count','query_count','cancel_count','account_snapshot_count','ticket_count','passed_checks','total_checks','message'], '暂无适配器回放认证。运行 python -m ashare_quant auto-live-adapter-replay 生成。')}
  </section>

  <section class="panel section">
    <h2>适配器回放认证明细</h2>
    {small_table(frames['auto_live_adapter_replay_audit'], ['mode','run_id','check','passed','value','threshold','severity','request_file','result_file','next_action'], '暂无适配器回放认证明细。')}
  </section>

  <section class="panel section">
    <h2>自动实盘修复计划</h2>
    {small_table(frames['auto_live_remediation_plan'], ['priority','area','blocker','status','command','requires_confirmation','next_action'], '暂无自动实盘修复计划。')}
  </section>

  <section class="panel section">
    <h2>修复后验证链</h2>
    {small_table(frames['auto_live_verification_plan'], ['step','area','command','required_before','stop_on_failure','expected_output','success_condition'], '暂无修复后验证链。运行 python -m ashare_quant verification-plan 生成。')}
  </section>


  <section class="panel section">
    <h2>RPA 前置环境审计</h2>
    {small_table(frames['rpa_preflight'], ['check','passed','value','threshold','severity','next_action'], '暂无 RPA 前置环境审计。运行 python -m ashare_quant rpa-preflight 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘提交时间窗</h2>
    {small_table(frames['auto_live_window_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无提交时间窗审计。运行 python -m ashare_quant auto-live-window 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘提交锁审计</h2>
    {small_table(frames['auto_live_submit_lock_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无提交锁审计。运行 python -m ashare_quant auto-live-lock 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘独立风控</h2>
    {small_table(frames['auto_live_risk_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无自动实盘风险审计。运行 python -m ashare_quant auto-live-risk 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘当日风险预留</h2>
    {small_table(frames['auto_live_risk_reservations'], ['run_id','trade_date','status','order_count','buy_amount','sell_amount','gross_amount','tickets_sha256','dedupe_key','created_at','updated_at','message'], '暂无真实提交风险预留。')}
  </section>

  <section class="panel section">
    <h2>自动实盘紧急停机记录</h2>
    {small_table(frames['auto_live_stop_audit'], ['action','passed','value','message','generated_at'], '暂无紧急停机操作记录。')}
  </section>

  <section class="panel section">
    <h2>自动实盘提交意图</h2>
    {small_table(frames['auto_live_submission_intent'], ['run_id','state','adapter','order_count','tickets_sha256','auto_live_config_sha256','dedupe_key','request_file','result_file','created_at','updated_at','message'], '暂无真实提交意图。只有准备调用券商适配器时才会生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘被阻断尝试</h2>
    {small_table(frames['auto_live_blocked_attempt'], ['run_id','status','adapter','order_count','blocked_reason','tickets_sha256','dedupe_key','generated_at','message'], '暂无被阻断的重复或并发提交尝试。')}
  </section>

  <section class="panel section">
    <h2>自动实盘适配器审计</h2>
    {small_table(frames['auto_live_adapter_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无适配器审计。运行 python -m ashare_quant auto-live-check 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘适配器自检</h2>
    {small_table(frames['auto_live_adapter_selftest'], ['check','passed','value','threshold','severity','next_action'], '暂无适配器自检。运行 python -m ashare_quant auto-live-adapter-selftest 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘阻断明细</h2>
    {small_table(frames['auto_live_blocker_details'], ['source','check','severity','value','next_action','message'], '暂无自动实盘阻断明细。')}
  </section>

  <section class="panel section">
    <h2>自动实盘券商账户自动刷新</h2>
    {small_table(frames['auto_live_account_refresh'], ['generated_at','adapter','attempted','trigger','status','remaining_blockers','message'], '暂无账户自动刷新审计。运行 python -m ashare_quant auto-live-run 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘券商账户快照</h2>
    {small_table(frames['auto_live_broker_account_snapshot'], ['snapshot_run_id','broker','account_id','environment','cash','available_cash','market_value','total_asset','frozen_cash','positions_count','broker_time','generated_at','auto_live_config_sha256'], '暂无券商账户快照。')}
  </section>

  <section class="panel section">
    <h2>自动实盘券商账户审计</h2>
    {small_table(frames['auto_live_broker_account_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无券商账户审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘券商持仓快照</h2>
    {small_table(frames['auto_live_broker_positions'], ['snapshot_run_id','symbol','quantity','sellable_quantity','cost','market_value','last_price'], '暂无券商持仓快照。')}
  </section>

  <section class="panel section">
    <h2>自动实盘运行审计</h2>
    {small_table(frames['auto_live_run'], ['run_id','status','adapter','order_count','submitted_count','blocked_reason','tickets_sha256','auto_live_config_sha256','dedupe_key','generated_at'], '暂无自动实盘运行审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘券商回报审计</h2>
    {small_table(frames['auto_live_ack_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无自动实盘券商回报审计。')}
  </section>

  <section class="panel section">
    <h2>后交易对账证据绑定</h2>
    {small_table(frames['auto_live_post_trade_manifest'], ['cycle_id','broker_snapshot_run_id','local_snapshot_sha256','local_positions_sha256','broker_snapshot_sha256','broker_positions_sha256','fill_progress_sha256','reconciliation_audit_sha256','passed','generated_at'], '暂无后交易对账证据。')}
  </section>

  <section class="panel section">
    <h2>自动实盘委托状态</h2>
    {small_table(frames['auto_live_order_status'], ['query_run_id','source_run_id','ticket_id','symbol','side','quantity','broker_order_id','broker_status','filled_quantity','avg_fill_price','fee','fee_status','fee_source'], '暂无自动实盘委托状态。')}
  </section>

  <section class="panel section">
    <h2>自动实盘委托状态审计</h2>
    {small_table(frames['auto_live_order_status_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无自动实盘委托状态审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘不确定提交恢复</h2>
    {small_table(frames['auto_live_submission_recovery'], ['source_run_id','ticket_id','submission_status','broker_order_id','broker_status','resolved','action','message'], '暂无不确定提交恢复记录。运行 python -m ashare_quant auto-live-query 生成。')}
  </section>

  <section class="panel section">
    <h2>自动实盘撤单结果</h2>
    {small_table(frames['auto_live_cancel_results'], ['cancel_run_id','source_run_id','ticket_id','symbol','side','quantity','broker_order_id','cancel_status','message'], '暂无自动实盘撤单结果。')}
  </section>

  <section class="panel section">
    <h2>自动实盘撤单审计</h2>
    {small_table(frames['auto_live_cancel_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无自动实盘撤单审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘成交导入候选</h2>
    {small_table(frames['auto_live_fill_import'], ['date','symbol','side','quantity','price','fee','amount','reason'], '暂无自动实盘成交导入候选。')}
  </section>

  <section class="panel section">
    <h2>自动实盘累计成交差额</h2>
    {small_table(frames['auto_live_fill_progress'], ['source_run_id','broker_order_id','ticket_id','cumulative_quantity','imported_quantity','delta_quantity','cumulative_amount','imported_amount','delta_amount','cumulative_fee','imported_fee','delta_fee','fee_status','fee_source','status'], '暂无累计成交差额记录。')}
  </section>

  <section class="panel section">
    <h2>自动实盘成交同步审计</h2>
    {small_table(frames['auto_live_fill_sync_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无自动实盘成交同步审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘结算审计</h2>
    {small_table(frames['auto_live_settlement_audit'], ['check','passed','value','threshold','severity','next_action'], '暂无自动实盘结算审计。')}
  </section>

  <section class="panel section">
    <h2>自动实盘提交台账</h2>
    {small_table(frames['auto_live_submission_ledger'], ['run_id','status','adapter','order_count','submitted_count','tickets_sha256','auto_live_config_sha256','dedupe_key','generated_at'], '暂无自动实盘提交台账。')}
  </section>

  <section class="panel section">
    <h2>候选策略与回测对比</h2>
    {small_table(frames['backtest_compare'], ['strategy_name','total_return','benchmark_total_return','excess_total_return','max_drawdown','information_ratio','walk_forward_avg_excess_return','walk_forward_positive_ratio','live_readiness_passed'], '暂无策略对比。')}
  </section>

  <section class="two section">
    <div class="panel">
      <h2>账户缺价</h2>
      {small_table(frames['account_missing_prices'], ['price_context','symbol','quantity','cost','message'], '暂无账户缺价项。')}
    </div>
    <div class="panel">
      <h2>RPA 工单</h2>
      {small_table(frames['rpa_orders'], ['ticket_id','symbol','side','quantity','limit_price','status','message'], '暂无 RPA 工单。')}
    </div>
  </section>

  <section class="two section">
    <div class="panel">
      <h2>账户缺价排除影响</h2>
      {small_table(frames['account_exclusion_impact'], ['check','passed','value','threshold','severity','next_action'], '暂无账户缺价排除影响审计。运行 python -m ashare_quant account-exclusion-audit 生成。')}
    </div>
    <div class="panel">
      <h2>账户缺价排除明细</h2>
      {small_table(frames['account_exclusion_impact_detail'], ['symbol','quantity','cost','cost_proxy','cost_proxy_ratio','price_status','suggested_action'], '暂无账户缺价排除明细。')}
    </div>
  </section>

  <section class="panel section">
    <h2>观察计划</h2>
    {small_table(frames['observation_schedule'], ['date','workflow','command','status','next_action'], '暂无观察计划。')}
  </section>

  <p class="muted footer">生成命令：.\\.venv\\Scripts\\python.exe -m ashare_quant dashboard。报告文件：outputs/report.html。</p>
</main>
</body>
</html>"""


def metric_card(label: str, value: str) -> str:
    return f'<div class="panel"><div class="label">{escape(label)}</div><div class="value">{escape(value)}</div></div>'


def command_card(item: dict[str, str]) -> str:
    return f'<div><h3>{escape(item["title"])}</h3><code class="command">{escape(item["command"])}</code><p>{escape(item["note"])}</p></div>'


def status_pill(label: str, status: str) -> str:
    cls = "pass" if status == "通过" else "wait" if status.startswith("未通过") else "fail"
    return f'<span class="pill {cls}">{escape(label)}：{escape(status)}</span>'


def auto_live_summary_html(summary: pd.DataFrame) -> str:
    if summary.empty:
        return '<p class="muted">暂无自动实盘结论。</p>'
    row = summary.iloc[0]
    lines = [
        ("状态", auto_live_status_label(str(row.get("status", "")))),
        ("允许自动实盘", "是" if bool_value(row.get("auto_live_allowed")) else "否"),
        ("人工工单", "就绪" if bool_value(row.get("manual_ticket_ready")) else "未就绪"),
        ("严格实盘", "就绪" if bool_value(row.get("strict_live_ready")) else "未就绪"),
        ("执行链路", "就绪" if bool_value(row.get("execution_ready")) else "未就绪"),
        ("阻断项", str(row.get("blockers", "") or "-")),
    ]
    body = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in lines)
    return f'<div class="table-scroll summary-table"><table><tbody>{body}</tbody></table></div>'


def auto_live_status_label(status: str) -> str:
    return {
        "blocked_safety": "安全门禁阻断",
        "blocked_execution_and_evidence": "执行与证据同时阻断",
        "blocked_execution": "执行链路阻断",
        "collecting_evidence": "正在累计证据",
        "ready_for_manual_review": "可人工复核，不能自动提交",
        "auto_live_ready": "自动实盘就绪",
    }.get(status, status or "-")


def evidence_progress(evidence: pd.DataFrame, promotion: pd.DataFrame, forward_progress: pd.DataFrame, shadow_audit: pd.DataFrame) -> str:
    if not evidence.empty and {"area", "current", "required", "remaining", "next_action"}.issubset(evidence.columns):
        rows = evidence.head(6).to_dict("records")
        items = [progress_item(AREA_LABELS.get(str(row.get("area", "")), str(row.get("area", ""))), row.get("current"), row.get("required"), row.get("next_action", "")) for row in rows]
        return f'<div class="progress-grid">{"".join(items)}</div>'
    best = top_row(promotion)
    forward_row = top_row(forward_progress)
    shadow_count = count_shadow(shadow_audit)
    items = [
        progress_item("前向交易日", first_number(best.get("forward_days"), forward_row.get("observed_forward_trading_days")), first_number(best.get("required_forward_days"), forward_row.get("required_forward_trading_days")), "继续观察冻结参数后的新增交易日。"),
        progress_item("Shadow 信号", first_number(best.get("shadow_observed_signals"), shadow_count), first_number(best.get("shadow_required_signals"), 20), "继续记录非执行信号。"),
        progress_item("策略晋级", 1 if str(best.get("promotion_status", "")) == "allow_manual_live_review" else 0, 1, str(best.get("recommendation", "等待晋级决策。"))),
    ]
    return f'<div class="progress-grid">{"".join(items)}</div>'


def progress_item(title: str, current: Any, required: Any, note: Any) -> str:
    ratio = progress_ratio(current, required)
    width = 0 if ratio is None else max(0, min(100, ratio * 100))
    return (
        '<div class="progress-item">'
        '<div class="progress-head">'
        f'<div class="progress-title">{escape(title)}</div>'
        f'<div class="progress-value">{escape(format_plain_number(current))} / {escape(format_plain_number(required))}</div>'
        '</div>'
        '<div class="progress-track">'
        f'<div class="progress-fill" style="width:{width:.1f}%"></div>'
        '</div>'
        f'<p>{escape(str(note or ""))}</p>'
        '</div>'
    )


def blocking_table(frames: dict[str, pd.DataFrame]) -> str:
    rows: list[dict[str, Any]] = []
    for name, frame in [
        ("证据计划", frames["evidence_plan"]),
        ("执行门禁", frames["execution"]),
        ("自动实盘", frames["auto_live_readiness"]),
        ("适配器", frames["auto_live_adapter_audit"]),
        ("适配器自检", frames.get("auto_live_adapter_selftest", pd.DataFrame())),
        ("自动实盘明细", frames.get("auto_live_blocker_details", pd.DataFrame())),
    ]:
        if frame.empty or "passed" not in frame.columns:
            continue
        failed = frame[~frame["passed"].map(bool_value)]
        for row in failed.head(4).to_dict("records"):
            rows.append({
                "area": name,
                "check": row.get("check") or AREA_LABELS.get(str(row.get("area", "")), row.get("area", "")),
                "value": row.get("value", row.get("current", "")),
                "next_action": row.get("next_action", ""),
            })
    if not rows:
        return '<p class="muted">暂无阻断项。</p>'
    return small_table(pd.DataFrame(rows), ["area", "check", "value", "next_action"], "暂无阻断项。")


def small_table(frame: pd.DataFrame, columns: list[str], empty: str) -> str:
    if frame.empty:
        return f'<p class="muted">{escape(empty)}</p>'
    cols = [col for col in columns if col in frame.columns]
    if not cols:
        return f'<p class="muted">{escape(empty)}</p>'
    rows = frame[cols].head(12).fillna("").to_dict("records")
    header = "".join(f"<th>{escape(column_label(col))}</th>" for col in cols)
    body = "".join("<tr>" + "".join(f"<td>{escape(format_cell(row[col], col))}</td>" for col in cols) + "</tr>" for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def column_label(column: str) -> str:
    return COLUMN_LABELS.get(column, column)


def recommended_commands(
    promotion: pd.DataFrame,
    practical: pd.DataFrame,
    strict: pd.DataFrame,
    execution: pd.DataFrame,
    source_health: pd.DataFrame,
    account_missing_prices: pd.DataFrame | None = None,
    account_price_audit: pd.DataFrame | None = None,
    strategy_health: pd.DataFrame | None = None,
) -> list[dict[str, str]]:
    refresh_needed = has_failed(source_health, "price_cache_age_days") or has_failed(source_health, "raw_latest_coverage")
    command = ".\\.venv\\Scripts\\python.exe -m ashare_quant run --allow-stale"
    note = "使用已有缓存完成模拟盘、执行门禁、自动实盘审计、报告和控制面板刷新；不会提交券商订单。"
    if refresh_needed:
        command = ".\\.venv\\Scripts\\python.exe -m ashare_quant run --refresh --cached-universe --max-symbols 800 --no-metadata"
        note = "先刷新缓存股票池，再完成全套本地审计；不会提交券商订单。"
    commands = [{"title": "一键运行系统", "command": command, "note": note}]

    missing_prices = account_missing_prices if account_missing_prices is not None else pd.DataFrame()
    price_audit = account_price_audit if account_price_audit is not None else pd.DataFrame()
    if has_failed(execution, "account_state_audit") or not missing_prices.empty:
        commands.append({"title": "修复账户状态", "command": ".\\.venv\\Scripts\\python.exe -m ashare_quant account-repair-template", "note": "生成账户修复模板，先处理缺价或持仓不一致问题。"})
        if not missing_prices.empty:
            commands.append({"title": "生成补价模板", "command": ".\\.venv\\Scripts\\python.exe -m ashare_quant account-price-template", "note": "为缺价持仓生成手工价格覆盖模板。"})
            if not all_passed(price_audit):
                commands.append({"title": "校验补价文件", "command": ".\\.venv\\Scripts\\python.exe -m ashare_quant account-price-check", "note": "填好补价文件后先 dry-run 校验。"})

    if not promotion.empty:
        status = str(promotion.iloc[0].get("promotion_status", ""))
        if status in {"collecting_evidence", "forward_underperforming"}:
            commands.append({"title": "继续观察候选", "command": ".\\.venv\\Scripts\\python.exe -m ashare_quant observe-candidates --quiet", "note": "累计冻结参数后的前向验证和 Shadow 样本。"})

    if has_failed(execution, "rpa_pre_trade_checks"):
        commands.append({"title": "刷新订单审计", "command": ".\\.venv\\Scripts\\python.exe -m ashare_quant order-audit", "note": "检查工单成交量、成交额和容量限制。"})

    commands.append({"title": "刷新执行门禁", "command": ".\\.venv\\Scripts\\python.exe -m ashare_quant execution-ready", "note": "重新生成执行模式、路线图和可执行门禁。"})
    commands.append({"title": "隔离演练", "command": ".\\.venv\\Scripts\\python.exe -m ashare_quant execution-drill", "note": "在独立状态库中演练模拟盘和 RPA 工单链路，不触碰主账户。"})
    return commands[:6]


def all_passed(frame: pd.DataFrame) -> bool:
    return not frame.empty and "passed" in frame.columns and bool(frame["passed"].map(bool_value).all())


def has_failed(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False
    rows = frame[frame["check"].astype(str) == check]
    return not rows.empty and not bool_value(rows["passed"].iloc[0])


def collection_status(frame: pd.DataFrame) -> str:
    if frame.empty or "passed" not in frame.columns:
        return "未生成"
    passed = frame["passed"].map(bool_value)
    failed = int((~passed).sum())
    return "通过" if failed == 0 else f"未通过 {failed}"


def auto_live_allowed(summary: pd.DataFrame) -> bool:
    return not summary.empty and "auto_live_allowed" in summary.columns and bool_value(summary["auto_live_allowed"].iloc[0])


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def metric_map(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or not {"metric", "value"}.issubset(frame.columns):
        return {}
    return {str(row.metric): row.value for row in frame.itertuples(index=False)}


def item_map(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return {}
    return {str(row.item): row.value for row in frame.itertuples(index=False)}


def top_row(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    if "rank" in frame.columns:
        frame = frame.sort_values("rank")
    return frame.iloc[0].to_dict()


def count_shadow(frame: pd.DataFrame) -> int | None:
    if frame.empty or "status" not in frame.columns:
        return None
    return int((frame["status"].astype(str) == "ok").sum())


def progress_ratio(current: Any, required: Any) -> float | None:
    cur = first_number(current)
    req = first_number(required)
    if cur is None or req in {None, 0}:
        return None
    return float(max(0.0, min(1.0, cur / req)))


def first_number(*values: Any) -> float | None:
    for value in values:
        parsed = pd.to_numeric(value, errors="coerce")
        if not pd.isna(parsed):
            return float(parsed)
    return None


def dashboard_strategy_mode(frames: dict[str, pd.DataFrame]) -> tuple[str, str]:
    one_click = frames.get("one_click_summary", pd.DataFrame())
    mode = frame_first(one_click, "strategy_operating_mode") or "unresolved"
    message = frame_first(one_click, "strategy_operating_message") or "No current strategy operating guidance."

    decision = frames.get("decision", pd.DataFrame())
    decision_value = frame_first(decision, "decision").strip().lower()
    health = frames.get("strategy_health", pd.DataFrame())
    paper_allowed = False
    if not health.empty and {"check", "value"}.issubset(health.columns):
        matches = health[health["check"].astype(str) == "paper_orders_allowed"]
        if not matches.empty:
            paper_allowed = bool_value(matches.iloc[-1]["value"])

    if decision_value.startswith("do_not_promote") and not paper_allowed:
        return (
            "strategy_halted",
            "\u6b63\u5f0f\u7b56\u7565\u5df2\u6682\u505c\uff0c\u4e3b\u6311\u6218\u8005\u5df2\u9694\u79bb\uff0c\u5f53\u524d\u6ca1\u6709\u53ef\u7528\u66ff\u4ee3\u5019\u9009\uff1b\u4ec5\u5141\u8bb8\u5237\u65b0\u7814\u7a76\u8bc1\u636e\uff0c\u4e0d\u5f97\u751f\u6210\u5de5\u5355\u3002",
        )

    local_status = frames.get("local_operator_status", pd.DataFrame())
    if not local_status.empty and {"item", "value"}.issubset(local_status.columns):
        rows = local_status[local_status["item"].astype(str) == "strategy_operating_mode"]
        if not rows.empty:
            row = rows.iloc[-1]
            local_mode = str(row.get("value", "") or "").strip()
            local_message = str(row.get("message", "") or "").strip()
            if local_mode:
                return local_mode, local_message or message

    return mode, message

def frame_first(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    value = frame[column].iloc[0]
    return "" if pd.isna(value) else str(value)


def format_cell(value: Any, column: str = "") -> str:
    if pd.isna(value):
        return ""
    if column == "passed" or column.endswith("_passed") or column.startswith("allow_"):
        return "是" if bool_value(value) else "否"
    text = str(value)
    if len(text) == 64 and all(ch in "0123456789abcdef" for ch in text.lower()):
        return text[:16]
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return text
    number = float(parsed)
    if column != "information_ratio" and any(token in column for token in ("return", "ratio", "drawdown", "excess", "participation")):
        return format_percent(number)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}"


def format_percent(value: Any) -> str:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return "-"
    return f"{float(parsed) * 100:.2f}%"


def format_plain_number(value: Any) -> str:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return "-"
    number = float(parsed)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def has_mojibake(text: str) -> bool:
    return any(marker in text for marker in MOJIBAKE_MARKERS)
