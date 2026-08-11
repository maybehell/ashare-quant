from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .audit import build_run_manifest, frame_sha256, mapping_sha256, orders_frame_sha256
from .autolive_fills import AUTO_LIVE_FILL_IMPORT_COLUMNS, AUTO_LIVE_FILL_PROGRESS_COLUMNS, AUTO_LIVE_FILL_SYNC_AUDIT_COLUMNS, AUTO_LIVE_SETTLEMENT_AUDIT_COLUMNS
from .autolive_cycle import AUTO_LIVE_CYCLE_COLUMNS, AUTO_LIVE_CYCLE_SUMMARY_COLUMNS, AUTO_LIVE_POST_TRADE_MANIFEST_COLUMNS, AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS
from .autolive_batch import AUTO_LIVE_BATCH_ORDER_COLUMNS, AUTO_LIVE_BATCH_PLAN_COLUMNS, AUTO_LIVE_BATCH_SUMMARY_COLUMNS, write_auto_live_batch_plan
from .autolive_cancel import AUTO_LIVE_CANCEL_VERIFICATION_COLUMNS
from .autolive_replay import AUTO_LIVE_ADAPTER_REPLAY_COLUMNS, AUTO_LIVE_ADAPTER_REPLAY_SUMMARY_COLUMNS
from .autolive_integrity import AUTO_LIVE_ADAPTER_FILE_AUDIT_COLUMNS
from .autolive_service import (
    AUTO_LIVE_SERVICE_HEARTBEAT_COLUMNS,
    AUTO_LIVE_SERVICE_HISTORY_COLUMNS,
    AUTO_LIVE_SERVICE_LOCK_AUDIT_COLUMNS,
    AUTO_LIVE_SERVICE_STOP_AUDIT_COLUMNS,
    AUTO_LIVE_SERVICE_SUMMARY_COLUMNS,
    write_auto_live_service_lock_audit,
)
from .autolive_window import AUTO_LIVE_WINDOW_AUDIT_COLUMNS, write_auto_live_window_audit
from .autolive_journal import AUTO_LIVE_JOURNAL_COLUMNS, AUTO_LIVE_JOURNAL_AUDIT_COLUMNS, AUTO_LIVE_JOURNAL_SUMMARY_COLUMNS, AUTO_LIVE_JOURNAL_ERROR_COLUMNS
from .autolive_binding import AUTO_LIVE_ACCOUNT_BINDING_AUDIT_COLUMNS
from .autolive_recovery import RECOVERY_AUDIT_COLUMNS, RECOVERY_INDEX_COLUMNS
from .autolive_alerts import ALERT_AUDIT_COLUMNS, ALERT_OUTBOX_COLUMNS, ALERT_SELFTEST_COLUMNS
from .autolive_confirmation import CONFIRMATION_COLUMNS
from .autolive_rollout import ROLLOUT_AUDIT_COLUMNS, ROLLOUT_HISTORY_COLUMNS
from .autolive_risk import AUTO_LIVE_RISK_AUDIT_COLUMNS, AUTO_LIVE_RISK_RESERVATION_COLUMNS, AUTO_LIVE_STOP_AUDIT_COLUMNS
from .autolive import AUTO_LIVE_ACK_AUDIT_COLUMNS, AUTO_LIVE_ADAPTER_AUDIT_COLUMNS, AUTO_LIVE_ADAPTER_MANIFEST_COLUMNS, AUTO_LIVE_ADAPTER_SELFTEST_COLUMNS, AUTO_LIVE_BLOCKER_DETAIL_COLUMNS, AUTO_LIVE_ORDER_RESULT_COLUMNS, AUTO_LIVE_ORDER_STATUS_AUDIT_COLUMNS, AUTO_LIVE_ORDER_STATUS_COLUMNS, AUTO_LIVE_SUBMISSION_RECOVERY_COLUMNS, AUTO_LIVE_CANCEL_AUDIT_COLUMNS, AUTO_LIVE_CANCEL_RESULT_COLUMNS, AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS, AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS, AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS, AUTO_LIVE_BROKER_POSITION_COLUMNS, AUTO_LIVE_READINESS_COLUMNS, AUTO_LIVE_RUN_COLUMNS, AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS, AUTO_LIVE_SUBMIT_LOCK_AUDIT_COLUMNS, AUTO_LIVE_SUBMISSION_INTENT_COLUMNS, AUTO_LIVE_SUMMARY_COLUMNS, write_auto_live_status
from .account_impact import ACCOUNT_EXCLUSION_IMPACT_COLUMNS, ACCOUNT_EXCLUSION_IMPACT_DETAIL_COLUMNS, write_account_exclusion_impact
from .account_plan import build_account_path_decision, build_account_readiness_plan
from .candidate import build_candidate_forward_progress, build_candidate_validation, candidate_frame, load_candidate_manifest
from .config import project_path
from .data import DataStore
from .execution_artifacts import invalidate_execution_artifacts
from .diagnostics import (
    annual_report,
    bias_warnings,
    data_quality_report,
    robustness_split_report,
    regime_validation_report,
    regime_validation_warnings,
    rolling_validation_report,
    rolling_warnings,
    strategy_candidate_stability_report,
    strategy_candidate_stability_warnings,
    strategy_reliability_report,
    strategy_reliability_warnings,
    walk_forward_warnings,
)
from .execution import build_execution_modes, build_execution_playbook, build_execution_readiness, build_execution_roadmap, execution_readiness_level
from .execution_reconciliation import DETAIL_COLUMNS as EXECUTION_RECONCILIATION_COLUMNS
from .execution_reconciliation import SUMMARY_COLUMNS as EXECUTION_RECONCILIATION_SUMMARY_COLUMNS
from .evidence import EVIDENCE_PLAN_COLUMNS, build_evidence_plan
from .execution_quality import EXECUTION_QUALITY_COLUMNS, EXECUTION_QUALITY_DETAIL_COLUMNS, build_execution_quality
from .factor_robustness import FACTOR_ROBUSTNESS_COLUMNS, FACTOR_ROBUSTNESS_SUMMARY_COLUMNS
from .factors import cap_and_normalize
from .industry_risk import INDUSTRY_RISK_COLUMNS, build_industry_risk_audit
from .index_membership import (
    MEMBERSHIP_QUALITY_COLUMNS,
    MEMBERSHIP_SNAPSHOT_COLUMNS,
    build_index_membership_quality,
    current_index_membership_quality,
    index_membership_quality_warnings,
    load_membership_from_config,
)
from .forward_factors import FORWARD_FACTOR_COLUMNS, build_forward_factor_monitor, forward_factor_warnings, write_forward_factor_monitor
from .io_utils import atomic_to_csv
from .observation_plan import OBSERVATION_SCHEDULE_COLUMNS, build_observation_schedule
from .observation_task import OBSERVATION_AUDIT_COLUMNS, OBSERVATION_SUMMARY_COLUMNS, write_observation_task_status
from .observation_progress import OBSERVATION_PROGRESS_AUDIT_COLUMNS, OBSERVATION_PROGRESS_COLUMNS
from .order_audit import ORDER_LIQUIDITY_COLUMNS
from .order_integrity import verify_order_provenance_integrity
from .rpa import RPA_COLUMNS, RPA_PREFLIGHT_COLUMNS, write_rpa_preflight
from .remediation import AUTO_LIVE_REMEDIATION_COLUMNS, AUTO_LIVE_VERIFICATION_COLUMNS, write_auto_live_remediation_plan, write_auto_live_verification_plan
from .readiness import (
    build_live_readiness,
    data_quality_with_current_cache_age,
    build_live_readiness_roadmap,
    build_practical_readiness,
    practical_readiness_level,
    readiness_level,
    readiness_evidence_issues,
    readiness_passed,
)
from .source_health import build_data_source_health
from .shadow import build_continuous_shadow_portfolios, build_shadow_forward_audit, exclude_pre_freeze_candidate_evidence, shadow_archive_dir_from_config
from .strategy_health import STRATEGY_HEALTH_COLUMNS, build_strategy_health, configured_promotion_decision
from .universe import eligible_universe
from .statistical_validation import EXCESS_BOOTSTRAP_COLUMNS, block_bootstrap_excess
from .survivorship import survivorship_warnings
from .validation import filter_complete_market_data
from .walkforward import empty_folds, empty_summary, walk_forward_report


REPORT_COLUMN_LABELS = {
    "check": "\u68c0\u67e5\u9879",
    "passed": "\u901a\u8fc7",
    "value": "\u5f53\u524d\u503c",
    "threshold": "\u8981\u6c42",
    "item": "审计项",
    "note": "解释",
    "year": "年份",
    "snapshot_date": "快照日期",
    "all_cached_priced_symbols": "缓存当日有行情股票数",
    "current_pool_priced_symbols": "当前池当日有行情股票数",
    "current_pool_listed_symbols": "当前池已上市股票数",
    "current_pool_not_yet_listed_symbols": "当前池未上市或上市日期未知数",
    "current_pool_priced_share_of_cache": "当前池占缓存当日行情比例",
    "candidate_a_id": "\u5019\u9009 A ID",
    "candidate_b_id": "\u5019\u9009 B ID",
    "candidate_a_name": "\u5019\u9009 A \u540d\u79f0",
    "candidate_b_name": "\u5019\u9009 B \u540d\u79f0",
    "common_start": "\u5171\u540c\u7a97\u53e3\u5f00\u59cb",
    "common_end": "\u5171\u540c\u7a97\u53e3\u7ed3\u675f",
    "common_trading_days": "\u5df2\u89c2\u5bdf\u5171\u540c\u4ea4\u6613\u65e5\u6570",
    "evaluated_trading_days": "\u5b9e\u9645\u68c0\u9a8c\u4ea4\u6613\u65e5\u6570",
    "required_common_trading_days": "\u8981\u6c42\u5171\u540c\u4ea4\u6613\u65e5\u6570",
    "evaluation_mode": "\u8bc4\u4f30\u65b9\u5f0f",
    "curve_ledger_valid": "\u8bc1\u636e\u8d26\u672c\u6709\u6548",
    "curve_ledger_sha256": "\u8bc1\u636e\u8d26\u672c\u6458\u8981",
    "minimum_curve_ledger_rows": "\u6700\u5c11\u8d26\u672c\u6536\u76ca\u884c\u6570",
    "policy_valid": "\u51bb\u7ed3\u653f\u7b56\u6709\u6548",
    "selection_ready": "\u5019\u9009\u9009\u62e9\u5b8c\u6210",
    "selected_candidate_id": "\u5165\u9009\u5019\u9009 ID",
    "selected_candidate_name": "\u5165\u9009\u5019\u9009\u540d\u79f0",
    "probability_candidate_a_superior": "\u5019\u9009 A \u4f18\u52bf\u6982\u7387",
    "lower_95_advantage": "\u4f18\u52bf 95% \u533a\u95f4\u4e0b\u754c",
    "upper_95_advantage": "\u4f18\u52bf 95% \u533a\u95f4\u4e0a\u754c",
    "favored_candidate_id": "\u4f18\u52bf\u5019\u9009 ID",
    "status": "\u72b6\u6001",
    "message": "\u8bf4\u660e",
    "alert_id": "\u544a\u8b66 ID",
    "event_type": "\u4e8b\u4ef6\u7c7b\u578b",
    "severity": "\u4e25\u91cd\u7ea7\u522b",
    "attempts": "\u5c1d\u8bd5\u6b21\u6570",
    "environment": "\u901a\u9053\u73af\u5883",
    "context_json": "\u4e8b\u4ef6\u4e0a\u4e0b\u6587",
    "created_at": "\u521b\u5efa\u65f6\u95f4",
    "last_attempt_at": "\u6700\u540e\u5c1d\u8bd5\u65f6\u95f4",
    "delivered_at": "\u9001\u8fbe\u65f6\u95f4",
    "error": "\u9519\u8bef",
    "broker_refresh_status": "\u5238\u5546\u53ea\u8bfb\u5237\u65b0\u72b6\u6001",
    "broker_refresh_stage": "\u5238\u5546\u53ea\u8bfb\u5237\u65b0\u9636\u6bb5",
}

REPORT_VALUE_LABELS = {
    "point_in_time_universe_available": "逐日历史可投资股票池可用",
    "survivorship_bias_corrected": "幸存者偏差已修正",
    "committed_universe_source": "冻结股票池来源",
    "current_pool_symbols": "当前冻结股票数",
    "all_cached_historical_symbols": "缓存历史股票数",
    "cached_symbols_excluded_from_current_pool": "未进入当前池的缓存股票数",
    "current_pool_share_of_cached_symbols": "当前池占缓存股票比例",
    "earliest_snapshot_current_pool_priced_share": "首年末当前池行情覆盖比例",
    "unreliable_current_pool_list_date_ratio": "当前池不可靠上市日期比例",
    "delisted_security_history_available": "退市证券历史可用",
    "survivorship_risk_level": "幸存者偏差风险等级",
    "study_range": "审计研究区间",
    "fetch_history_provenance": "逐股票历史抓取来源清单",
    "fallback_all_cached_symbols": "回退为全部缓存股票",
    "high": "高",
    "risk_cash_buffer_policy": "\u5206\u9636\u6bb5\u73b0\u91d1\u7f13\u51b2\u7b56\u7565",
    "query_source_integrity": "\u67e5\u8be2\u6062\u590d\u6e90\u5b8c\u6574\u6027",
    "cancel_source_preflight": "\u64a4\u5355\u6e90\u9884\u68c0",
    "order_status_file_sha256": "\u59d4\u6258\u72b6\u6001\u6587\u4ef6\u6458\u8981",
    "order_status_missing": "\u59d4\u6258\u72b6\u6001\u7f3a\u5931",
    "order_status_file_missing": "\u59d4\u6258\u72b6\u6001\u6587\u4ef6\u7f3a\u5931",
    "order_status_file_sha256_mismatch": "\u59d4\u6258\u72b6\u6001\u6587\u4ef6\u5df2\u53d8\u66f4\uff0c\u6458\u8981\u4e0d\u5339\u914d",
    "order_status_audit_missing_or_invalid": "\u59d4\u6258\u72b6\u6001\u5ba1\u8ba1\u7f3a\u5931\u6216\u65e0\u6548",
    "no_cancelable_orders": "\u5f53\u524d\u6ca1\u6709\u53ef\u64a4\u59d4\u6258",
    "cancel_source_ticket_id_missing": "\u64a4\u5355\u6e90\u7968\u636e\u53f7\u7f3a\u5931",
    "cancel_source_broker_order_id_missing": "\u64a4\u5355\u6e90\u5238\u5546\u59d4\u6258\u53f7\u7f3a\u5931",
    "cancel_source_ticket_id_duplicate": "\u64a4\u5355\u6e90\u7968\u636e\u53f7\u91cd\u590d",
    "cancel_source_broker_order_id_duplicate": "\u64a4\u5355\u6e90\u5238\u5546\u59d4\u6258\u53f7\u91cd\u590d",
    "cancel_source_query_run_id_ambiguous": "\u64a4\u5355\u6e90\u67e5\u8be2\u8fd0\u884c\u53f7\u4e0d\u552f\u4e00",
    "cancel_source_run_id_ambiguous": "\u64a4\u5355\u6e90\u63d0\u4ea4\u8fd0\u884c\u53f7\u4e0d\u552f\u4e00",
    "cancel_source_identity_verified": "\u64a4\u5355\u6e90\u8eab\u4efd\u5df2\u9a8c\u8bc1",
    "cancel_request_acknowledged": "\u64a4\u5355\u8bf7\u6c42\u5df2\u53d7\u7406",
    "cancel_lock_clear": "\u64a4\u5355\u9501\u4e3a\u7a7a",
    "cancel_lock_payload_valid": "\u64a4\u5355\u9501\u8eab\u4efd\u6709\u6548",
    "cancel_lock_recovery_query_audited": "清锁恢复查询已审计",
    "cancel_lock_recovery_query_fresh": "清锁恢复查询为新批次",
    "cancel_lock_request_state_safe": "撤单请求状态允许恢复",
    "cancel_lock_recovery_ready": "撤单锁恢复条件满足",
    "cancel_lock_active": "已有撤单任务持有原子锁",
    "fill_source_status_file_integrity": "\u6210\u4ea4\u6765\u6e90\u72b6\u6001\u6587\u4ef6\u5b8c\u6574\u6027",
    "fill_progress_database_readable": "\u5df2\u6709\u6210\u4ea4\u8fdb\u5ea6\u53ef\u8bfb",
    "order_status_file_sha256_valid": "委托状态文件摘要有效",
    "cancel_results_present": "撤单回报存在",
    "cancel_result_run_id_unique": "撤单运行号唯一",
    "cancel_request_identity_valid": "撤单请求身份有效",
    "cancel_request_integrity_valid": "\u64a4\u5355\u8bf7\u6c42\u5b8c\u6574\u6027\u6709\u6548",
    "cancel_results_bound_to_request": "\u64a4\u5355\u56de\u62a5\u5df2\u7ed1\u5b9a\u539f\u59cb\u8bf7\u6c42",
    "cancel_verification_status_audit_passed": "撤单后状态审计通过",
    "cancel_verification_query_fresh": "撤单后查询为新批次",
    "cancel_verification_status_pairs_unique": "撤单后订单身份唯一",
    "cancel_verification_rows_match": "撤单验证逐笔匹配",
    "cancel_verification_terminal": "\u64a4\u5355\u5bf9\u8c61\u8fdb\u5165\u7ec8\u6001",
    "submission_intent_missing": "\u63d0\u4ea4\u610f\u56fe\u7f3a\u5931",
    "submission_intent_run_id_missing": "\u63d0\u4ea4\u610f\u56fe\u8fd0\u884c\u53f7\u7f3a\u5931",
    "noncommand_results_bound_to_intent": "\u975e\u547d\u4ee4\u9002\u914d\u5668\u7ed3\u679c\u5df2\u7ed1\u5b9a\u63d0\u4ea4\u610f\u56fe",
    "submit_request_missing_or_identity_mismatch": "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u7f3a\u5931\u6216\u8eab\u4efd\u4e0d\u5339\u914d",
    "submit_request_required_integrity_missing": "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u7f3a\u5c11\u5fc5\u9700\u5b8c\u6574\u6027\u7b7e\u540d",
    "submit_request_ticket_hash_mismatch": "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u7968\u636e\u6458\u8981\u4e0d\u5339\u914d",
    "submit_request_order_count_mismatch": "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u8ba2\u5355\u6570\u4e0d\u5339\u914d",
    "submit_request_order_rows_invalid": "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u8ba2\u5355\u884c\u65e0\u6548",
    "submit_request_order_hash_mismatch": "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u8ba2\u5355\u6458\u8981\u4e0d\u5339\u914d",
    "current_result_order_count_mismatch": "\u5f53\u524d\u7ed3\u679c\u8ba2\u5355\u6570\u4e0d\u5339\u914d",
    "current_result_order_identity_mismatch": "\u5f53\u524d\u7ed3\u679c\u8ba2\u5355\u8eab\u4efd\u4e0d\u5339\u914d",
    "command_results_bound_to_request": "\u547d\u4ee4\u9002\u914d\u5668\u7ed3\u679c\u5df2\u7ed1\u5b9a\u539f\u59cb\u8bf7\u6c42",
    "submit_request_ticket_id_missing": "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u7968\u636e\u53f7\u7f3a\u5931",
    "unknown_results_reconstructed_from_bound_request": "\u5df2\u4ece\u7ed1\u5b9a\u7684\u539f\u59cb\u8bf7\u6c42\u91cd\u5efa\u672a\u77e5\u7ed3\u679c",
    "fixed_horizon": "\u56fa\u5b9a\u7a97\u53e3\uff08\u4ec5\u4f7f\u7528\u9996\u4e2a\u5b8c\u6574\u7a97\u53e3\uff09",
    "collecting_forward_data": "\u6b63\u5728\u6536\u96c6\u524d\u5411\u6570\u636e",
    "dominance_ready": "\u8fbe\u5230\u51bb\u7ed3\u4f18\u52bf\u9608\u503c",
    "no_clear_winner": "\u6ca1\u6709\u660e\u786e\u80dc\u8005",
    "invalid_evidence": "\u8bc1\u636e\u65e0\u6548",
    "submission_uncertain": "\u63d0\u4ea4\u72b6\u6001\u672a\u77e5",
    "partial_failed": "\u90e8\u5206\u63d0\u4ea4\u5931\u8d25",
    "cancel_request_failed": "\u64a4\u5355\u8bf7\u6c42\u5931\u8d25",
    "cancel_verification_pending": "\u64a4\u5355\u7ec8\u6001\u672a\u786e\u8ba4",
    "settlement_query_failed": "\u7ed3\u7b97\u59d4\u6258\u67e5\u8be2\u5931\u8d25",
    "fill_import_failed": "\u6210\u4ea4\u56de\u5199\u5931\u8d25",
    "fill_progress_database_unreadable": "\u6210\u4ea4\u8fdb\u5ea6\u5e93\u4e0d\u53ef\u8bfb",
    "delivered": "\u5df2\u9001\u8fbe",
    "pending": "\u5f85\u91cd\u8bd5",
    "local_only": "\u4ec5\u672c\u5730\u8bb0\u5f55",
    "critical": "\u4e25\u91cd",
    "broker_readonly_refresh_failed": "\u534e\u6cf0\u8d26\u6237\u53ea\u8bfb\u5feb\u7167\u5237\u65b0\u5931\u8d25",
    "service_cycle_blocked": "\u63d0\u4ea4\u5468\u671f\u88ab\u95e8\u7981\u963b\u65ad",
    "service_cycle_exception": "\u81ea\u52a8\u5b9e\u76d8\u670d\u52a1\u5468\u671f\u5f02\u5e38",
    "service_failure_fuse": "\u81ea\u52a8\u5b9e\u76d8\u670d\u52a1\u8fde\u7eed\u5931\u8d25\u7194\u65ad",
    "stopped_cycle_exception": "\u5355\u6b21\u5468\u671f\u5f02\u5e38\u540e\u505c\u6b62",
    "stopped_max_cycles_with_failure": "\u8fbe\u5230\u5468\u671f\u4e0a\u9650\u4e14\u5b58\u5728\u5931\u8d25",
    "stopped_cycle_blocked": "\u5355\u6b21\u63d0\u4ea4\u5468\u671f\u88ab\u963b\u65ad",
    "stopped_max_cycles_with_blockers": "\u8fbe\u5230\u5468\u671f\u4e0a\u9650\u4e14\u4ecd\u88ab\u963b\u65ad",
    "completed_once_with_blockers": "\u5ba1\u8ba1\u5b8c\u6210\u4f46\u5b58\u5728\u963b\u65ad\u9879",
    "completed_max_cycles_with_blockers": "\u591a\u5468\u671f\u5ba1\u8ba1\u5b8c\u6210\u4f46\u5b58\u5728\u963b\u65ad\u9879",
    "completed_once_with_recovery": "\u5355\u6b21\u8fd0\u884c\u5b8c\u6210\u4e14\u5df2\u6062\u590d",
    "completed_max_cycles_with_recovery": "\u591a\u5468\u671f\u8fd0\u884c\u5b8c\u6210\u4e14\u5df2\u6062\u590d",
    "ready": "\u5df2\u5c31\u7eea",
}

DEFAULT_COLUMNS = {
    "equity_curve.csv": ["date", "cash", "market_value", "equity", "fills", "turnover"],
    "orders.csv": ["date", "symbol", "side", "quantity", "reason"],
    "backtest_orders.csv": ["date", "symbol", "side", "quantity", "reason"],
    "positions.csv": ["symbol", "quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"],
    "trades.csv": ["date", "symbol", "side", "quantity", "price", "fee", "amount", "reason"],
    "performance_metrics.csv": ["metric", "value"],
    "industry_exposure.csv": ["industry", "market_value", "weight", "reference_weight", "active_weight", "active_cap", "within_active_cap"],
    "industry_risk_audit.csv": INDUSTRY_RISK_COLUMNS,
    "benchmark_comparison.csv": ["date", "strategy_nav", "benchmark_nav", "strategy_return", "benchmark_return", "excess_return"],
    "excess_bootstrap.csv": EXCESS_BOOTSTRAP_COLUMNS,
    "data_quality.csv": ["item", "value", "severity", "note"],
    "index_membership_quality.csv": MEMBERSHIP_QUALITY_COLUMNS,
    "index_membership_snapshots.csv": MEMBERSHIP_SNAPSHOT_COLUMNS,
    "data_source_health.csv": ["check", "passed", "value", "threshold", "severity", "next_action", "command"],
    "robustness_split.csv": ["sample", "start", "end", "strategy_return", "benchmark_return", "excess_return", "information_ratio"],
    "annual_performance.csv": ["year", "strategy_return", "benchmark_return", "excess_return", "max_strategy_drawdown"],
    "rolling_validation.csv": ["end_date", "window_days", "strategy_return", "benchmark_return", "excess_return", "max_strategy_drawdown", "information_ratio"],
    "regime_validation.csv": [
        "regime",
        "description",
        "observations",
        "start",
        "end",
        "strategy_return",
        "benchmark_return",
        "excess_return",
        "avg_daily_excess",
        "win_day_ratio",
        "information_ratio",
        "max_strategy_drawdown",
    ],
    "risk_exposure.csv": ["date", "enabled", "exposure", "benchmark_exposure", "breadth_exposure", "breadth_ratio", "breadth_state", "breadth_symbol_count", "breadth_stale_symbol_count", "breadth_latest_date", "benchmark_close", "moving_average", "annual_volatility", "bear_market", "high_volatility"],
    "walk_forward.csv": ["train_start", "train_end", "test_start", "test_end", "selected_candidate", "train_excess_return", "test_strategy_return", "test_benchmark_return", "test_excess_return", "test_max_drawdown"],
    "walk_forward_summary.csv": [
        "folds",
        "train_days",
        "test_days",
        "step_days",
        "selection_mode",
        "test_overlap_ratio",
        "positive_test_ratio",
        "avg_test_excess_return",
        "worst_test_excess_return",
        "avg_test_max_drawdown",
    ],
    "strategy_reliability.csv": ["item", "value", "threshold", "passed", "severity", "note"],
    "strategy_health.csv": STRATEGY_HEALTH_COLUMNS,
    "strategy_candidate_stability.csv": ["item", "value", "threshold", "passed", "severity", "note"],
    "cost_stress.csv": [
        "scenario",
        "slippage_bps",
        "commission_rate",
        "total_return",
        "max_drawdown",
        "excess_total_return",
        "information_ratio",
        "config_sha256",
        "stocks_sha256",
        "prices_sha256",
        "fundamentals_sha256",
        "benchmark_sha256",
    ],
    "run_manifest.csv": ["item", "value"],
    "live_candidate.csv": ["item", "value"],
    "live_candidate_validation.csv": ["check", "passed", "value", "threshold", "message"],
    "live_candidate_forward_progress.csv": [
        "candidate_id",
        "status",
        "freeze_price_end",
        "latest_comparison_date",
        "forward_start",
        "forward_end",
        "observed_forward_trading_days",
        "required_forward_trading_days",
        "remaining_forward_trading_days",
        "progress_ratio",
        "forward_strategy_return",
        "forward_benchmark_return",
        "forward_excess_return",
        "required_forward_excess_return",
        "forward_active_max_drawdown",
        "max_forward_active_drawdown",
        "forward_tracking_error",
        "max_forward_tracking_error",
        "forward_information_ratio",
        "min_forward_information_ratio",
        "config_hash_match",
        "optimized_on_current_cache_at_freeze",
        "overfit_status",
        "next_action",
    ],
    "live_readiness.csv": ["check", "passed", "value", "threshold", "message"],
    "practical_readiness.csv": ["check", "passed", "value", "threshold", "message"],
    "live_readiness_roadmap.csv": ["priority", "area", "check", "status", "current", "required", "remaining", "next_action", "command"],
    "strategy_research_gate.csv": ["item", "value", "threshold", "passed", "severity", "note"],
    "strategy_research.csv": [
        "rank",
        "name",
        "strategy_hash",
        "score",
        "strategy_return",
        "benchmark_return",
        "excess_return",
        "max_drawdown",
        "in_sample_excess_return",
        "out_of_sample_excess_return",
        "recent_excess_concentration",
        "annual_positive_ratio",
        "anti_overfit_passed",
        "overfit_penalty",
        "stability_score",
        "top_n",
        "weighting",
        "weights",
        "adaptive_weights",
        "industry_neutralization",
        "cross_sectional_neutralization",
        "industry_relative_cap",
        "ensemble_members",
    ],
    "factor_robustness.csv": FACTOR_ROBUSTNESS_COLUMNS,
    "factor_robustness_summary.csv": FACTOR_ROBUSTNESS_SUMMARY_COLUMNS,
    "forward_factor_monitor.csv": FORWARD_FACTOR_COLUMNS,
    "strategy_baselines.csv": [
        "rank",
        "baseline_type",
        "name",
        "strategy_hash",
        "description",
        "score",
        "strategy_return",
        "benchmark_return",
        "excess_return",
        "max_drawdown",
        "in_sample_excess_return",
        "out_of_sample_excess_return",
        "recent_excess_concentration",
        "annual_positive_ratio",
        "anti_overfit_passed",
        "overfit_penalty",
        "stability_score",
        "top_n",
        "weighting",
        "weights",
        "adaptive_weights",
        "industry_neutralization",
        "cross_sectional_neutralization",
        "industry_relative_cap",
        "ensemble_members",
    ],
    "strategy_backtest_compare.csv": ["output_dir", "strategy_name", "total_return", "benchmark_total_return", "excess_total_return", "max_drawdown", "information_ratio", "bootstrap_probability_excess_positive", "bootstrap_lower_95_excess_return", "walk_forward_avg_excess_return", "walk_forward_positive_ratio", "live_readiness_passed", "generated_at"],
    "strategy_promotion.csv": ["rank", "output_dir", "strategy_name", "promotion_status", "recommendation", "anti_overfit_status", "practical_passed", "strict_live_passed", "candidate_id", "forward_status", "forward_days", "required_forward_days", "remaining_forward_days", "forward_excess_return", "shadow_observed_signals", "shadow_required_signals", "shadow_avg_excess_return", "high_cost_excess_return", "total_return", "benchmark_total_return", "excess_total_return", "information_ratio", "bootstrap_probability_excess_positive", "bootstrap_lower_95_excess_return", "max_drawdown", "failed_live_checks", "generated_at"],
    "execution_model_reconciliation.csv": EXECUTION_RECONCILIATION_COLUMNS,
    "execution_model_reconciliation_summary.csv": EXECUTION_RECONCILIATION_SUMMARY_COLUMNS,
    "research_candidate_observation.csv": ["status", "candidate_config", "candidate_name", "candidate_output_dir", "strategy_hash", "backtest_mode", "backtest_reason", "allow_stale", "skip_stress", "elapsed_seconds", "message", "generated_at"],
    "forward_only_candidate_observation.csv": ["status", "candidate_config", "candidate_name", "candidate_id", "candidate_output_dir", "signal_rows", "freeze_price_end", "membership_evidence_passed", "membership_rows_at_freeze", "membership_sha256_at_freeze", "frozen_input_evidence_passed", "frozen_input_mismatches", "benchmark_mapping_evidence_passed", "shadow_evaluation_evidence_passed", "shadow_evaluation_sha256", "forward_risk_evaluation_evidence_passed", "forward_risk_evaluation_sha256", "market_data_current", "expected_market_date", "complete_market_end", "benchmark_end", "trade_calendar_covered", "observable_signal_count", "pending_signal_count", "latest_pending_signal_date", "next_pending_execution_date", "first_attribution_requires_close_after", "max_observed_trading_days", "required_forward_trading_days", "remaining_forward_trading_days", "forward_observation_status", "allow_stale", "elapsed_seconds", "message", "generated_at"],
    "forward_candidate_attribution.csv": ["candidate_config", "candidate_name", "signal_date", "execution_date", "latest_date", "observed_trading_days", "covered_weight", "anchor_covered_weight", "satellite_covered_weight", "portfolio_return", "anchor_return", "satellite_return", "benchmark_return", "excess_return", "anchor_excess_contribution", "satellite_excess_contribution", "signal_hash", "strategy_hash", "candidate_id", "status"],
    "forward_candidate_selection.csv": ["candidate_a_id", "candidate_b_id", "candidate_a_name", "candidate_b_name", "common_start", "common_end", "common_trading_days", "evaluated_trading_days", "required_common_trading_days", "candidate_a_return", "candidate_b_return", "candidate_a_advantage", "annualized_mean_return_difference", "return_correlation", "bootstrap_samples", "block_days", "probability_candidate_a_superior", "lower_95_advantage", "upper_95_advantage", "favored_candidate_id", "status", "message"],
    "forward_candidate_selection_summary.csv": ["status", "selection_ready", "selected_candidate_id", "selected_candidate_name", "candidate_count", "pair_count", "ready_pair_count", "minimum_common_trading_days", "required_common_trading_days", "evaluation_mode", "candidate_set_sha256", "policy_sha256", "policy_valid", "curve_ledger_valid", "curve_ledger_sha256", "minimum_curve_ledger_rows", "evidence_after", "generated_at", "message"],
    "candidate_failure_history.csv": ["candidate_id", "strategy_hash", "behavior_hash", "strategy_name", "status", "freeze_price_end", "observed_forward_trading_days", "forward_excess_return", "recorded_at"],
    "shadow_forward_audit.csv": ["signal_date", "execution_date", "latest_date", "observed_trading_days", "covered_weight", "max_target_weight", "configured_max_stock_weight", "weight_cap_passed", "portfolio_return", "benchmark_return", "excess_return", "active_max_drawdown", "tracking_error", "information_ratio", "signal_hash", "strategy_hash", "candidate_id", "status"],
    "order_source_diagnostics.csv": ["check", "passed", "value", "threshold", "message"],
    "account_reconciliation.csv": ["check", "passed", "value", "threshold", "message"],
    "account_reset_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "account_import_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "account_import_positions.csv": ["symbol", "quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"],
    "account_import_snapshot.csv": ["date", "cash", "market_value", "equity"],
    "account_import_check_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "account_import_check_positions.csv": ["symbol", "quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"],
    "account_import_check_snapshot.csv": ["date", "cash", "market_value", "equity", "would_write_state", "source_file", "source_sha256", "checked_at"],
    "account_import_from_state_template.csv": ["cash", "symbol", "quantity", "sellable_quantity", "cost"],
    "account_import_priced_only_template.csv": ["cash", "symbol", "quantity", "sellable_quantity", "cost"],
    "account_repair_review.csv": ["cash", "symbol", "quantity", "sellable_quantity", "cost", "include_in_import", "close", "market_value", "price_status", "issue", "suggested_action", "note"],
    "account_repair_excluded_positions.csv": ["cash", "symbol", "quantity", "sellable_quantity", "cost", "include_in_import", "close", "market_value", "price_status", "issue", "suggested_action", "note"],
    "account_price_overrides.csv": ["price_context", "symbol", "date", "close", "source", "note"],
    "account_price_overrides_template.csv": ["price_context", "symbol", "date", "close", "source", "note"],
    "account_price_override_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "account_price_override_effective.csv": ["symbol", "date", "close", "source", "note"],
    "account_state_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "account_state_snapshot.csv": ["snapshot_date", "mark_date", "cash", "recorded_equity", "market_value", "marked_equity", "position_count", "locked_quantity", "sellable_quantity"],
    "account_state_positions.csv": ["symbol", "quantity", "locked_quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"],
    "account_state_missing_prices.csv": [
        "price_context",
        "symbol",
        "quantity",
        "cost",
        "symbol_in_stock_cache",
        "symbol_in_price_cache",
        "first_price_date",
        "last_price_date",
        "likely_reason",
        "suggested_action",
        "message",
    ],
    "account_readiness_plan.csv": ["priority", "path", "status", "blocker", "command", "next_action", "note"],
    "account_path_decision.csv": ["path", "available", "status", "blockers", "command", "next_action", "note"],
    "account_exclusion_impact.csv": ACCOUNT_EXCLUSION_IMPACT_COLUMNS,
    "account_exclusion_impact_detail.csv": ACCOUNT_EXCLUSION_IMPACT_DETAIL_COLUMNS,
    "fill_import_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "fill_import_trades.csv": ["date", "symbol", "side", "quantity", "price", "fee", "amount", "reason"],
    "fill_order_match.csv": ["date", "symbol", "side", "fill_quantity", "order_quantity", "matched", "message"],
    "execution_quality.csv": EXECUTION_QUALITY_COLUMNS,
    "execution_quality_detail.csv": EXECUTION_QUALITY_DETAIL_COLUMNS,
    "fill_import_positions.csv": ["symbol", "quantity", "sellable_quantity", "cost", "close", "market_value", "pnl"],
    "fill_import_snapshot.csv": ["date", "cash", "market_value", "equity"],
    "rpa_orders.csv": RPA_COLUMNS,
    "rpa_orders_all.csv": RPA_COLUMNS,
    "rpa_blocked_orders.csv": RPA_COLUMNS,
    "rpa_pre_trade_checks.csv": ["check", "passed", "value", "threshold", "message"],
    "rpa_preflight.csv": RPA_PREFLIGHT_COLUMNS,
    "order_liquidity_audit.csv": ORDER_LIQUIDITY_COLUMNS,
    "order_liquidity_all_audit.csv": ORDER_LIQUIDITY_COLUMNS,
    "auto_live_readiness.csv": AUTO_LIVE_READINESS_COLUMNS,
    "auto_live_summary.csv": AUTO_LIVE_SUMMARY_COLUMNS,
    "auto_live_window_audit.csv": AUTO_LIVE_WINDOW_AUDIT_COLUMNS,
    "auto_live_submit_lock_audit.csv": AUTO_LIVE_SUBMIT_LOCK_AUDIT_COLUMNS,
    "auto_live_submission_intent.csv": AUTO_LIVE_SUBMISSION_INTENT_COLUMNS,
    "auto_live_action_confirmation.csv": CONFIRMATION_COLUMNS,
    "huatai_desktop_canary_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "auto_live_blocked_attempt.csv": AUTO_LIVE_RUN_COLUMNS,
    "auto_live_risk_audit.csv": AUTO_LIVE_RISK_AUDIT_COLUMNS,
    "auto_live_risk_reservations.csv": AUTO_LIVE_RISK_RESERVATION_COLUMNS,
    "auto_live_stop_audit.csv": AUTO_LIVE_STOP_AUDIT_COLUMNS,
    "auto_live_cycle.csv": AUTO_LIVE_CYCLE_COLUMNS,
    "auto_live_cycle_summary.csv": AUTO_LIVE_CYCLE_SUMMARY_COLUMNS,
    "auto_live_batch_summary.csv": AUTO_LIVE_BATCH_SUMMARY_COLUMNS,
    "auto_live_batch_plan.csv": AUTO_LIVE_BATCH_PLAN_COLUMNS,
    "auto_live_batch_orders.csv": AUTO_LIVE_BATCH_ORDER_COLUMNS,
    "auto_live_current_batch.csv": RPA_COLUMNS,
    "auto_live_cycle_history.csv": AUTO_LIVE_CYCLE_COLUMNS,
    "auto_live_post_trade_reconciliation.csv": AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS,
    "auto_live_post_trade_manifest.csv": AUTO_LIVE_POST_TRADE_MANIFEST_COLUMNS,
    "auto_live_post_trade_reconciliation_history.csv": AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS,
    "auto_live_run.csv": AUTO_LIVE_RUN_COLUMNS,
    "auto_live_order_results.csv": AUTO_LIVE_ORDER_RESULT_COLUMNS,
    "auto_live_ack_audit.csv": AUTO_LIVE_ACK_AUDIT_COLUMNS,
    "auto_live_order_status.csv": AUTO_LIVE_ORDER_STATUS_COLUMNS,
    "auto_live_order_status_audit.csv": AUTO_LIVE_ORDER_STATUS_AUDIT_COLUMNS,
    "auto_live_submission_recovery.csv": AUTO_LIVE_SUBMISSION_RECOVERY_COLUMNS,
    "auto_live_cancel_results.csv": AUTO_LIVE_CANCEL_RESULT_COLUMNS,
    "auto_live_cancel_audit.csv": AUTO_LIVE_CANCEL_AUDIT_COLUMNS,
    "auto_live_cancel_verification.csv": AUTO_LIVE_CANCEL_VERIFICATION_COLUMNS,
    "auto_live_cancel_verification_audit.csv": AUTO_LIVE_CANCEL_AUDIT_COLUMNS,
    "auto_live_cancel_lock_audit.csv": AUTO_LIVE_CANCEL_AUDIT_COLUMNS,
    "auto_live_account_refresh.csv": AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS,
    "auto_live_broker_account_snapshot.csv": AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS,
    "auto_live_broker_positions.csv": AUTO_LIVE_BROKER_POSITION_COLUMNS,
    "auto_live_broker_account_audit.csv": AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS,
    "capital_sensitivity.csv": [
        "initial_cash", "total_return", "excess_total_return", "max_drawdown", "annualized_turnover",
        "average_cash_ratio", "excess_cash_ratio_vs_reference", "fill_count", "rejected_order_count", "final_position_count",
        "return_drag_vs_reference", "passed", "run_output_dir",
    ],
    "capital_sensitivity_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "micro_capital_sensitivity.csv": [
        "initial_cash", "total_return", "excess_total_return", "max_drawdown", "annualized_turnover",
        "average_cash_ratio", "excess_cash_ratio_vs_reference", "fill_count", "rejected_order_count", "final_position_count",
        "return_drag_vs_reference", "passed", "run_output_dir",
    ],
    "micro_capital_sensitivity_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "rebalance_frequency_research.csv": [
        "frequency", "status", "research_only", "initial_cash", "total_return", "excess_total_return",
        "out_of_sample_excess_return", "max_drawdown", "annualized_turnover", "information_ratio", "sharpe",
        "trade_count", "rebalance_count", "bootstrap_probability_excess_positive",
        "bootstrap_lower_95_excess_return", "config_match", "output_dir",
    ],
    "rebalance_frequency_research_audit.csv": ["check", "passed", "value", "threshold", "severity", "message"],
    "broker_capital_feasibility_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "broker_capital_feasibility_detail.csv": [
        "symbol", "target_weight", "reference_price", "lot_size", "one_lot_amount",
        "required_equity_at_target_weight", "affordable_at_target_weight", "affordable_from_available_cash",
        "risk_lot_affordable_current", "allocated_lots_current", "allocated_amount_current",
        "actual_weight_current", "weight_gap_current",
    ],
    "broker_canary_capital_audit.csv": ["check", "passed", "value", "threshold", "message"],
    "broker_canary_capital_candidates.csv": ["asof", "rank", "symbol", "name", "board", "reference_price", "lot_size", "one_lot_amount", "estimated_buy_fee", "estimated_total_cost", "available_cash", "canary_notional_limit", "avg_amount", "limit_affordable", "cash_affordable", "affordable", "executable", "scope"],
    "capital_mode_assessment.csv": ["mode", "mode_label_cn", "snapshot_fresh", "capital_sensitivity_current", "current_total_asset", "available_cash", "strategy_target_positions", "minimum_diversified_positions", "nearest_tested_capital", "nearest_tested_positions", "nearest_tested_cash_ratio", "nearest_tested_return_drag", "minimum_full_strategy_equity", "capital_gap", "capital_ratio", "canary_ready", "full_strategy_ready", "strategy_deployment_allowed", "reason_cn"],
    "broker_capital_transition.csv": ["status", "allowed_mode", "snapshot_fresh", "current_total_asset", "available_cash", "minimum_full_strategy_equity", "full_strategy_capital_gap", "current_to_required_ratio", "full_strategy_ready", "canary_ready", "cheapest_canary_total_cost", "strategy_identity_preserved", "message"],
    "auto_live_fill_import.csv": AUTO_LIVE_FILL_IMPORT_COLUMNS,
    "auto_live_fill_progress.csv": AUTO_LIVE_FILL_PROGRESS_COLUMNS,
    "auto_live_fill_sync_audit.csv": AUTO_LIVE_FILL_SYNC_AUDIT_COLUMNS,
    "auto_live_settlement_audit.csv": AUTO_LIVE_SETTLEMENT_AUDIT_COLUMNS,
    "auto_live_submission_ledger.csv": AUTO_LIVE_SUBMISSION_LEDGER_COLUMNS,
    "auto_live_adapter_audit.csv": AUTO_LIVE_ADAPTER_AUDIT_COLUMNS,
    "auto_live_blocker_details.csv": AUTO_LIVE_BLOCKER_DETAIL_COLUMNS,
    "auto_live_adapter_manifest.csv": AUTO_LIVE_ADAPTER_MANIFEST_COLUMNS,
    "auto_live_adapter_selftest.csv": AUTO_LIVE_ADAPTER_SELFTEST_COLUMNS,
    "auto_live_adapter_replay_audit.csv": AUTO_LIVE_ADAPTER_REPLAY_COLUMNS,
    "auto_live_adapter_file_audit.csv": AUTO_LIVE_ADAPTER_FILE_AUDIT_COLUMNS,
    "auto_live_adapter_replay_summary.csv": AUTO_LIVE_ADAPTER_REPLAY_SUMMARY_COLUMNS,
    "auto_live_service_summary.csv": AUTO_LIVE_SERVICE_SUMMARY_COLUMNS,
    "auto_live_event_journal.csv": AUTO_LIVE_JOURNAL_COLUMNS,
    "auto_live_journal_audit.csv": AUTO_LIVE_JOURNAL_AUDIT_COLUMNS,
    "auto_live_journal_summary.csv": AUTO_LIVE_JOURNAL_SUMMARY_COLUMNS,
    "auto_live_journal_errors.csv": AUTO_LIVE_JOURNAL_ERROR_COLUMNS,
    "auto_live_account_binding_audit.csv": AUTO_LIVE_ACCOUNT_BINDING_AUDIT_COLUMNS,
    "auto_live_recovery_points.csv": RECOVERY_INDEX_COLUMNS,
    "auto_live_recovery_audit.csv": RECOVERY_AUDIT_COLUMNS,
    "auto_live_alert_audit.csv": ALERT_AUDIT_COLUMNS,
    "auto_live_alert_outbox.csv": ALERT_OUTBOX_COLUMNS,
    "auto_live_alert_selftest.csv": ALERT_SELFTEST_COLUMNS,
    "auto_live_rollout_audit.csv": ROLLOUT_AUDIT_COLUMNS,
    "auto_live_rollout_history.csv": ROLLOUT_HISTORY_COLUMNS,
    "auto_live_service_history.csv": AUTO_LIVE_SERVICE_HISTORY_COLUMNS,
    "auto_live_service_heartbeat.csv": AUTO_LIVE_SERVICE_HEARTBEAT_COLUMNS,
    "auto_live_service_lock_audit.csv": AUTO_LIVE_SERVICE_LOCK_AUDIT_COLUMNS,
    "auto_live_service_stop_audit.csv": AUTO_LIVE_SERVICE_STOP_AUDIT_COLUMNS,
    "evidence_plan.csv": EVIDENCE_PLAN_COLUMNS,
    "observation_schedule.csv": OBSERVATION_SCHEDULE_COLUMNS,
    "daily_observation_summary.csv": OBSERVATION_SUMMARY_COLUMNS,
    "daily_observation_audit.csv": OBSERVATION_AUDIT_COLUMNS,
    "daily_observation_progress.csv": OBSERVATION_PROGRESS_COLUMNS,
    "daily_observation_progress_audit.csv": OBSERVATION_PROGRESS_AUDIT_COLUMNS,
    "auto_live_remediation_plan.csv": AUTO_LIVE_REMEDIATION_COLUMNS,
    "auto_live_verification_plan.csv": AUTO_LIVE_VERIFICATION_COLUMNS,
    "execution_readiness.csv": ["check", "passed", "value", "threshold", "severity", "next_action", "message"],
    "execution_readiness_roadmap.csv": ["priority", "area", "status", "blocker", "command", "next_action", "note"],
    "execution_modes.csv": ["mode", "passed", "status", "blockers", "next_action", "note"],
    "execution_playbook.csv": ["route", "step", "status", "command", "destructive", "gate", "next_action", "note"],
}


def current_shadow_forward_audit(
    config: dict[str, Any],
    prices: pd.DataFrame | None = None,
    benchmark: pd.DataFrame | None = None,
) -> pd.DataFrame:
    archive_dir = shadow_archive_dir_from_config(config)
    if not archive_dir.exists():
        output_dir = project_path(config["paths"]["output_dir"])
        existing = read_csv(output_dir / "shadow_forward_audit.csv")
        if not existing.empty:
            return exclude_pre_freeze_candidate_evidence(
                existing, load_candidate_manifest(config)
            )
        return pd.DataFrame(columns=DEFAULT_COLUMNS["shadow_forward_audit.csv"])

    price_data = prices.copy() if prices is not None else pd.DataFrame()
    benchmark_data = benchmark.copy() if benchmark is not None else pd.DataFrame()
    if price_data.empty or benchmark_data.empty:
        try:
            store = DataStore.from_config(config)
            _, price_data, _ = store.load_market_data()
            benchmark_data = store.load_benchmark(required=False)
            price_data, benchmark_data = filter_complete_market_data(price_data, benchmark_data, config)
        except Exception:
            output_dir = project_path(config["paths"]["output_dir"])
            existing = read_csv(output_dir / "shadow_forward_audit.csv")
            if not existing.empty:
                return exclude_pre_freeze_candidate_evidence(
                    existing, load_candidate_manifest(config)
                )
            return pd.DataFrame(columns=DEFAULT_COLUMNS["shadow_forward_audit.csv"])

    if price_data.empty:
        return pd.DataFrame(columns=DEFAULT_COLUMNS["shadow_forward_audit.csv"])
    manifest = load_candidate_manifest(config)
    static_audit = exclude_pre_freeze_candidate_evidence(
        build_shadow_forward_audit(
            archive_dir,
            price_data,
            benchmark_data,
            max_stock_weight=float(
                config.get("strategy", {}).get("max_stock_weight", 1.0)
            ),
            config=config,
        ),
        manifest,
    )
    continuous, curve = build_continuous_shadow_portfolios(
        archive_dir, price_data, benchmark_data, config, manifest
    )
    output_dir = project_path(config["paths"]["output_dir"])
    atomic_to_csv(curve, output_dir / "shadow_continuous_nav.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(continuous, output_dir / "shadow_continuous_summary.csv", index=False, encoding="utf-8-sig")
    if continuous.empty:
        return static_audit
    return pd.concat([static_audit, continuous], ignore_index=True)


def post_warmup_validation_reports(
    comparison: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build validation reports from an equity comparison already past factor warmup."""
    robustness = robustness_split_report(
        comparison,
        float(config.get("validation", {}).get("holdout_split_ratio", 0.7)),
        warmup_days=0,
    )
    regimes = regime_validation_report(comparison, config, warmup_days=0)
    return robustness, regimes

def write_outputs(
    config: dict[str, Any],
    equity: pd.DataFrame,
    orders: pd.DataFrame,
    positions: pd.DataFrame,
    trades: pd.DataFrame,
    rejects: pd.DataFrame | None = None,
    stocks: pd.DataFrame | None = None,
    benchmark: pd.DataFrame | None = None,
    prices: pd.DataFrame | None = None,
    fundamentals: pd.DataFrame | None = None,
    risk_exposure: pd.DataFrame | None = None,
    raw_prices: pd.DataFrame | None = None,
    raw_benchmark: pd.DataFrame | None = None,
    order_output_name: str = "orders.csv",
) -> dict[str, Path]:
    if order_output_name not in {"orders.csv", "backtest_orders.csv"}:
        raise ValueError("order_output_name must be orders.csv or backtest_orders.csv")
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    invalidate_execution_artifacts(config, reason="backtest_outputs_started")

    comparison = benchmark_comparison(equity, benchmark if benchmark is not None else pd.DataFrame())
    excess_bootstrap = block_bootstrap_excess(comparison, config)
    metrics = calc_metrics(equity)
    metrics.update(benchmark_metrics(comparison))
    membership_quality, membership_snapshots = build_index_membership_quality(
        config,
        load_membership_from_config(config),
        stocks if stocks is not None else pd.DataFrame(),
        prices if prices is not None else pd.DataFrame(),
        benchmark if benchmark is not None else pd.DataFrame(),
    )
    data_quality = data_quality_report(
        stocks if stocks is not None else pd.DataFrame(),
        prices if prices is not None else pd.DataFrame(),
        fundamentals if fundamentals is not None else pd.DataFrame(),
        benchmark if benchmark is not None else pd.DataFrame(),
        config,
        raw_prices=raw_prices,
        raw_benchmark=raw_benchmark,
    )
    robustness, regime_validation = post_warmup_validation_reports(comparison, config)
    annual = annual_report(comparison)
    rolling = rolling_validation_report(comparison)
    if skip_expensive_diagnostics(config):
        walk_forward, walk_forward_summary = empty_folds(), empty_summary()
    else:
        walk_forward, walk_forward_summary = walk_forward_report(
            prices if prices is not None else pd.DataFrame(),
            stocks if stocks is not None else pd.DataFrame(),
            fundamentals if fundamentals is not None else pd.DataFrame(),
            benchmark if benchmark is not None else pd.DataFrame(),
            config,
        )
    strategy_reliability = strategy_reliability_report(
        comparison,
        robustness,
        rolling,
        walk_forward_summary,
        data_quality,
        config,
        regime_validation,
        excess_bootstrap,
    )
    run_manifest = build_run_manifest(config, stocks, prices, fundamentals, benchmark)
    strategy_research, strategy_candidate_stability = load_strategy_research_evidence(config, output_dir, run_manifest)
    strategy_baselines = read_csv(output_dir / "strategy_baselines.csv")
    live_candidate = candidate_frame(load_candidate_manifest(config))
    shadow_forward_audit = current_shadow_forward_audit(config, prices, benchmark)
    live_candidate_validation = build_candidate_validation(
        config, comparison, shadow_audit=shadow_forward_audit
    )
    live_candidate_forward_progress = build_candidate_forward_progress(
        config, comparison, shadow_audit=shadow_forward_audit
    )
    forward_factor_monitor = build_forward_factor_monitor(
        config,
        stocks if stocks is not None else pd.DataFrame(),
        prices if prices is not None else pd.DataFrame(),
        fundamentals if fundamentals is not None else pd.DataFrame(),
        benchmark if benchmark is not None else pd.DataFrame(),
    )
    cost_stress = read_csv(output_dir / "cost_stress.csv")
    data_source_health = build_data_source_health(config)
    industry_exposure_frame = (
        industry_exposure(positions, stocks, prices, config)
        if stocks is not None
        else pd.DataFrame(columns=DEFAULT_COLUMNS["industry_exposure.csv"])
    )
    industry_risk_audit = build_industry_risk_audit(industry_exposure_frame, config)
    live_readiness = build_live_readiness(
        config,
        metrics,
        data_quality,
        robustness,
        walk_forward_summary,
        live_candidate_validation,
        shadow_forward_audit,
        cost_stress,
        run_manifest,
        candidate_stability=strategy_candidate_stability,
        excess_bootstrap=excess_bootstrap,
        industry_risk_audit=industry_risk_audit,
        factor_robustness_summary=read_csv(output_dir / "factor_robustness_summary.csv"),
    )
    practical_readiness = build_practical_readiness(
        config,
        metrics,
        data_quality,
        robustness,
        walk_forward_summary,
        cost_stress,
        run_manifest,
        [],
        read_csv(output_dir / "capital_sensitivity_audit.csv"),
        industry_risk_audit=industry_risk_audit,
    )
    promotion_decision = configured_promotion_decision(config, output_dir)
    strategy_health = build_strategy_health(
        config,
        strategy_reliability,
        rolling,
        live_candidate_forward_progress,
        shadow_forward_audit,
        promotion_decision,
        practical_readiness,
        live_readiness,
        read_csv(output_dir / "factor_robustness_summary.csv"),
        forward_factor_monitor,
    )
    readiness_roadmap = build_live_readiness_roadmap(live_readiness, config)
    source_diagnostics = order_source_diagnostics(output_dir, orders, config)
    rpa_preflight_path = write_rpa_preflight(config)
    execution_readiness = build_execution_readiness(
        config,
        practical_readiness,
        live_readiness,
        source_diagnostics,
        read_csv(output_dir / "account_reconciliation.csv"),
        read_csv(output_dir / "account_state_audit.csv"),
        read_csv(output_dir / "rpa_pre_trade_checks.csv"),
        read_csv(output_dir / "rpa_orders.csv"),
        orders,
        strategy_decision=promotion_decision,
        strategy_health=strategy_health,
        rpa_preflight=read_csv(rpa_preflight_path),
    )
    execution_roadmap = build_execution_roadmap(execution_readiness)
    execution_modes = build_execution_modes(execution_readiness)
    account_readiness_plan = build_account_readiness_plan(config)
    account_path_decision = build_account_path_decision(config)
    execution_playbook = build_execution_playbook(execution_readiness, account_path_decision)
    account_exclusion_paths = write_account_exclusion_impact(config)
    execution_quality, execution_quality_detail = build_execution_quality(config)

    frames = {
        "equity_curve.csv": equity,
        order_output_name: orders,
        "positions.csv": positions,
        "trades.csv": trades,
        "industry_exposure.csv": industry_exposure_frame,
        "industry_risk_audit.csv": industry_risk_audit,
        "performance_metrics.csv": metrics_frame(metrics),
        "benchmark_comparison.csv": comparison,
        "excess_bootstrap.csv": excess_bootstrap,
        "data_quality.csv": data_quality,
        "index_membership_quality.csv": membership_quality,
        "index_membership_snapshots.csv": membership_snapshots,
        "data_source_health.csv": data_source_health,
        "robustness_split.csv": robustness,
        "annual_performance.csv": annual,
        "rolling_validation.csv": rolling,
        "regime_validation.csv": regime_validation,
        "strategy_reliability.csv": strategy_reliability,
        "strategy_health.csv": strategy_health,
        "strategy_candidate_stability.csv": strategy_candidate_stability,
        "walk_forward.csv": walk_forward,
        "walk_forward_summary.csv": walk_forward_summary,
        "run_manifest.csv": run_manifest,
        "live_candidate.csv": live_candidate,
        "live_candidate_validation.csv": live_candidate_validation,
        "live_candidate_forward_progress.csv": live_candidate_forward_progress,
        "live_readiness.csv": live_readiness,
        "forward_factor_monitor.csv": forward_factor_monitor,
        "practical_readiness.csv": practical_readiness,
        "live_readiness_roadmap.csv": readiness_roadmap,
        "shadow_forward_audit.csv": shadow_forward_audit,
        "order_source_diagnostics.csv": source_diagnostics,
        "execution_readiness.csv": execution_readiness,
        "execution_readiness_roadmap.csv": execution_roadmap,
        "execution_modes.csv": execution_modes,
        "execution_playbook.csv": execution_playbook,
        "execution_quality.csv": execution_quality,
        "execution_quality_detail.csv": execution_quality_detail,
        "account_readiness_plan.csv": account_readiness_plan,
        "account_path_decision.csv": account_path_decision,
        "account_exclusion_impact.csv": read_csv(account_exclusion_paths["account_exclusion_impact.csv"]),
        "account_exclusion_impact_detail.csv": read_csv(account_exclusion_paths["account_exclusion_impact_detail.csv"]),
    }
    if risk_exposure is not None:
        frames["risk_exposure.csv"] = risk_exposure
    if rejects is not None and not rejects.empty:
        frames["rejected_orders.csv"] = rejects

    paths: dict[str, Path] = {}
    for name, frame in frames.items():
        path = output_dir / name
        if frame.empty and name in DEFAULT_COLUMNS:
            frame = pd.DataFrame(columns=DEFAULT_COLUMNS[name])
        atomic_to_csv(frame, path, index=False, encoding="utf-8-sig")
        paths[name] = path

    for name, auto_path in write_auto_live_status(config).items():
        paths[name] = auto_path
    paths["auto_live_service_lock_audit.csv"] = write_auto_live_service_lock_audit(config)
    for service_name in [
        "auto_live_service_summary.csv",
        "auto_live_service_history.csv",
        "auto_live_service_heartbeat.csv",
        "auto_live_service_stop_audit.csv",
    ]:
        paths[service_name] = output_dir / service_name
    paths["auto_live_window_audit.csv"] = write_auto_live_window_audit(config)
    for cycle_name, cycle_columns in {
        "auto_live_cycle.csv": AUTO_LIVE_CYCLE_COLUMNS,
        "auto_live_cycle_summary.csv": AUTO_LIVE_CYCLE_SUMMARY_COLUMNS,
        "auto_live_cycle_history.csv": AUTO_LIVE_CYCLE_COLUMNS,
        "auto_live_post_trade_reconciliation.csv": AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS,
    "auto_live_post_trade_manifest.csv": AUTO_LIVE_POST_TRADE_MANIFEST_COLUMNS,
        "auto_live_post_trade_reconciliation_history.csv": AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS,
    }.items():
        cycle_path = output_dir / cycle_name
        if not cycle_path.exists():
            atomic_to_csv(pd.DataFrame(columns=cycle_columns), cycle_path, index=False, encoding="utf-8-sig")
        paths[cycle_name] = cycle_path
    account_refresh_path = output_dir / "auto_live_account_refresh.csv"
    if not account_refresh_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS), account_refresh_path, index=False, encoding="utf-8-sig")
    paths["auto_live_account_refresh.csv"] = account_refresh_path
    submission_recovery_path = output_dir / "auto_live_submission_recovery.csv"
    if not submission_recovery_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_SUBMISSION_RECOVERY_COLUMNS), submission_recovery_path, index=False, encoding="utf-8-sig")
    paths["auto_live_submission_recovery.csv"] = submission_recovery_path
    evidence_plan = build_evidence_plan(config)
    evidence_path = output_dir / "evidence_plan.csv"
    atomic_to_csv(evidence_plan, evidence_path, index=False, encoding="utf-8-sig")
    paths["evidence_plan.csv"] = evidence_path
    observation_schedule = build_observation_schedule(config)
    observation_path = output_dir / "observation_schedule.csv"
    atomic_to_csv(observation_schedule, observation_path, index=False, encoding="utf-8-sig")
    paths["observation_schedule.csv"] = observation_path
    paths.update(write_observation_task_status(config))
    remediation_path = write_auto_live_remediation_plan(config)
    paths["auto_live_remediation_plan.csv"] = remediation_path
    verification_path = write_auto_live_verification_plan(config)
    paths["auto_live_verification_plan.csv"] = verification_path

    paths["report.html"] = write_html_report(
        output_dir / "report.html",
        equity=equity,
        orders=orders,
        positions=positions,
        trades=trades,
        exposure=frames.get("industry_exposure.csv", pd.DataFrame()),
        industry_risk_audit=industry_risk_audit,
        comparison=comparison,
        excess_bootstrap=excess_bootstrap,
        metrics=metrics,
        data_quality=data_quality,
        index_membership_quality=membership_quality,
        index_membership_snapshots=membership_snapshots,
        data_source_health=data_source_health,
        robustness=robustness,
        annual=annual,
        rolling=rolling,
        regime_validation=regime_validation,
        strategy_reliability=strategy_reliability,
        strategy_health=strategy_health,
        strategy_candidate_stability=strategy_candidate_stability,
        strategy_candidate_family=read_strategy_research_artifact(config, output_dir, "strategy_candidate_family.csv"),
        strategy_candidate_similarity=read_strategy_research_artifact(config, output_dir, "strategy_candidate_similarity.csv"),
        strategy_family_selection=read_strategy_research_artifact(config, output_dir, "strategy_family_selection.csv"),
        strategy_research_gate=read_csv(output_dir / "strategy_research_gate.csv"),
        strategy_family_selection_splits=read_strategy_research_artifact(config, output_dir, "strategy_family_selection_splits.csv"),
        execution_model_reconciliation=read_csv(output_dir / "execution_model_reconciliation.csv"),
        execution_model_reconciliation_summary=read_csv(output_dir / "execution_model_reconciliation_summary.csv"),
        strategy_promotion=read_csv(output_dir / "strategy_promotion.csv"),
        research_candidate_observation=read_csv(output_dir / "research_candidate_observation.csv"),
        forward_only_candidate_observation=read_csv(output_dir / "forward_only_candidate_observation.csv"),
        forward_candidate_attribution=read_csv(output_dir / "forward_candidate_attribution.csv"),
        forward_candidate_selection=read_csv(output_dir / "forward_candidate_selection.csv"),
        forward_candidate_selection_summary=read_csv(output_dir / "forward_candidate_selection_summary.csv"),
        candidate_failure_history=read_csv(output_dir / "candidate_failure_history.csv"),
        factor_robustness=read_csv(output_dir / "factor_robustness.csv"),
        factor_robustness_summary=read_csv(output_dir / "factor_robustness_summary.csv"),
        risk_exposure=risk_exposure if risk_exposure is not None else pd.DataFrame(),
        walk_forward=walk_forward,
        walk_forward_summary=walk_forward_summary,
        cost_stress=cost_stress,
        capital_sensitivity=read_csv(output_dir / "capital_sensitivity.csv"),
        capital_sensitivity_audit=read_csv(output_dir / "capital_sensitivity_audit.csv"),
        micro_capital_sensitivity=read_csv(output_dir / "micro_capital_sensitivity.csv"),
        micro_capital_sensitivity_audit=read_csv(output_dir / "micro_capital_sensitivity_audit.csv"),
        rebalance_frequency_research=read_csv(output_dir / "rebalance_frequency_research.csv"),
        rebalance_frequency_research_audit=read_csv(output_dir / "rebalance_frequency_research_audit.csv"),
        run_manifest=run_manifest,
        live_candidate=live_candidate,
        live_candidate_validation=live_candidate_validation,
        live_candidate_forward_progress=live_candidate_forward_progress,
        candidate_activation_audit=read_csv(output_dir / "candidate_activation_audit.csv"),
        candidate_deactivation_audit=read_csv(output_dir / "candidate_deactivation_audit.csv"),
        active_execution_status=read_csv(output_dir / "active_execution_status.csv"),
        live_readiness=live_readiness,
        forward_factor_monitor=forward_factor_monitor,
        practical_readiness=practical_readiness,
        live_readiness_roadmap=readiness_roadmap,
        execution_readiness=execution_readiness,
        execution_readiness_roadmap=execution_roadmap,
        execution_modes=execution_modes,
        execution_playbook=execution_playbook,
        auto_live_readiness=read_csv(output_dir / "auto_live_readiness.csv"),
        auto_live_summary=read_csv(output_dir / "auto_live_summary.csv"),
        auto_live_journal_summary=read_csv(output_dir / "auto_live_journal_summary.csv"),
        auto_live_journal_audit=read_csv(output_dir / "auto_live_journal_audit.csv"),
        auto_live_event_journal=read_csv(output_dir / "auto_live_event_journal.csv"),
        auto_live_journal_errors=read_csv(output_dir / "auto_live_journal_errors.csv"),
        auto_live_account_binding_audit=read_csv(output_dir / "auto_live_account_binding_audit.csv"),
        auto_live_recovery_points=read_csv(output_dir / "auto_live_recovery_points.csv"),
        auto_live_recovery_audit=read_csv(output_dir / "auto_live_recovery_audit.csv"),
        auto_live_alert_audit=read_csv(output_dir / "auto_live_alert_audit.csv"),
        auto_live_alert_outbox=read_csv(output_dir / "auto_live_alert_outbox.csv"),
        auto_live_alert_selftest=read_csv(output_dir / "auto_live_alert_selftest.csv"),
        auto_live_rollout_audit=read_csv(output_dir / "auto_live_rollout_audit.csv"),
        auto_live_rollout_history=read_csv(output_dir / "auto_live_rollout_history.csv"),
        auto_live_service_summary=read_csv(output_dir / "auto_live_service_summary.csv"),
        auto_live_service_history=read_csv(output_dir / "auto_live_service_history.csv"),
        auto_live_service_heartbeat=read_csv(output_dir / "auto_live_service_heartbeat.csv"),
        auto_live_service_lock_audit=read_csv(output_dir / "auto_live_service_lock_audit.csv"),
        auto_live_service_stop_audit=read_csv(output_dir / "auto_live_service_stop_audit.csv"),
        auto_live_batch_summary=read_csv(output_dir / "auto_live_batch_summary.csv"),
        auto_live_batch_plan=read_csv(output_dir / "auto_live_batch_plan.csv"),
        auto_live_batch_orders=read_csv(output_dir / "auto_live_batch_orders.csv"),
        auto_live_current_batch=read_csv(output_dir / "auto_live_current_batch.csv"),
        auto_live_cycle_summary=read_csv(output_dir / "auto_live_cycle_summary.csv"),
        auto_live_cycle=read_csv(output_dir / "auto_live_cycle.csv"),
        auto_live_cycle_history=read_csv(output_dir / "auto_live_cycle_history.csv"),
        auto_live_post_trade_reconciliation=read_csv(output_dir / "auto_live_post_trade_reconciliation.csv"),
        auto_live_post_trade_manifest=read_csv(output_dir / "auto_live_post_trade_manifest.csv"),
        auto_live_post_trade_reconciliation_history=read_csv(output_dir / "auto_live_post_trade_reconciliation_history.csv"),
        auto_live_window_audit=read_csv(output_dir / "auto_live_window_audit.csv"),
        auto_live_submit_lock_audit=read_csv(output_dir / "auto_live_submit_lock_audit.csv"),
        auto_live_risk_audit=read_csv(output_dir / "auto_live_risk_audit.csv"),
        auto_live_risk_reservations=read_csv(output_dir / "auto_live_risk_reservations.csv"),
        auto_live_stop_audit=read_csv(output_dir / "auto_live_stop_audit.csv"),
        auto_live_submission_intent=read_csv(output_dir / "auto_live_submission_intent.csv"),
        auto_live_action_confirmation=read_csv(output_dir / "auto_live_action_confirmation.csv"),
        huatai_canary_audit=read_csv(output_dir / "huatai_desktop_canary_audit.csv"),
        auto_live_blocked_attempt=read_csv(output_dir / "auto_live_blocked_attempt.csv"),
        auto_live_account_refresh=read_csv(output_dir / "auto_live_account_refresh.csv"),
        auto_live_broker_account_snapshot=read_csv(output_dir / "auto_live_broker_account_snapshot.csv"),
        auto_live_broker_positions=read_csv(output_dir / "auto_live_broker_positions.csv"),
        auto_live_broker_account_audit=read_csv(output_dir / "auto_live_broker_account_audit.csv"),
        broker_account_import_preview_audit=read_csv(output_dir / "broker_account_import_preview_audit.csv"),
        broker_account_initialization_audit=read_csv(output_dir / "broker_account_initialization_audit.csv"),
        broker_account_initialization_provenance=read_csv(output_dir / "broker_account_initialization_provenance.csv"),
        broker_capital_feasibility_audit=read_csv(output_dir / "broker_capital_feasibility_audit.csv"),
        broker_capital_feasibility_detail=read_csv(output_dir / "broker_capital_feasibility_detail.csv"),
        broker_canary_capital_audit=read_csv(output_dir / "broker_canary_capital_audit.csv"),
        broker_canary_capital_candidates=read_csv(output_dir / "broker_canary_capital_candidates.csv"),
        broker_capital_transition=read_csv(output_dir / "broker_capital_transition.csv"),
        capital_mode_assessment=read_csv(output_dir / "capital_mode_assessment.csv"),
        broker_order_preview=read_csv(output_dir / "broker_order_preview.csv"),
        broker_order_preview_signal=read_csv(output_dir / "broker_order_preview_signal.csv"),
        broker_order_preview_audit=read_csv(output_dir / "broker_order_preview_audit.csv"),
        auto_live_run=read_csv(output_dir / "auto_live_run.csv"),
        auto_live_order_results=read_csv(output_dir / "auto_live_order_results.csv"),
        auto_live_ack_audit=read_csv(output_dir / "auto_live_ack_audit.csv"),
        auto_live_order_status=read_csv(output_dir / "auto_live_order_status.csv"),
        auto_live_order_status_audit=read_csv(output_dir / "auto_live_order_status_audit.csv"),
        auto_live_submission_recovery=read_csv(output_dir / "auto_live_submission_recovery.csv"),
        auto_live_cancel_results=read_csv(output_dir / "auto_live_cancel_results.csv"),
        auto_live_cancel_audit=read_csv(output_dir / "auto_live_cancel_audit.csv"),
        auto_live_cancel_verification=read_csv(output_dir / "auto_live_cancel_verification.csv"),
        auto_live_cancel_verification_audit=read_csv(output_dir / "auto_live_cancel_verification_audit.csv"),
        auto_live_cancel_lock_audit=read_csv(output_dir / "auto_live_cancel_lock_audit.csv"),
        auto_live_fill_import=read_csv(output_dir / "auto_live_fill_import.csv"),
        auto_live_fill_progress=read_csv(output_dir / "auto_live_fill_progress.csv"),
        auto_live_fill_sync_audit=read_csv(output_dir / "auto_live_fill_sync_audit.csv"),
        auto_live_settlement_audit=read_csv(output_dir / "auto_live_settlement_audit.csv"),
        auto_live_submission_ledger=read_csv(output_dir / "auto_live_submission_ledger.csv"),
        auto_live_adapter_audit=read_csv(output_dir / "auto_live_adapter_audit.csv"),
        auto_live_adapter_selftest=read_csv(output_dir / "auto_live_adapter_selftest.csv"),
        auto_live_adapter_replay_audit=read_csv(output_dir / "auto_live_adapter_replay_audit.csv"),
        auto_live_adapter_file_audit=read_csv(output_dir / "auto_live_adapter_file_audit.csv"),
        auto_live_adapter_replay_summary=read_csv(output_dir / "auto_live_adapter_replay_summary.csv"),
        auto_live_blocker_details=read_csv(output_dir / "auto_live_blocker_details.csv"),
        auto_live_adapter_manifest=read_csv(output_dir / "auto_live_adapter_manifest.csv"),
        evidence_plan=evidence_plan,
        observation_schedule=observation_schedule,
        daily_observation_summary=read_csv(output_dir / "daily_observation_summary.csv"),
        daily_observation_progress=read_csv(output_dir / "daily_observation_progress.csv"),
        daily_observation_progress_audit=read_csv(output_dir / "daily_observation_progress_audit.csv"),
        daily_observation_audit=read_csv(output_dir / "daily_observation_audit.csv"),
        auto_live_remediation_plan=read_csv(output_dir / "auto_live_remediation_plan.csv"),
        auto_live_verification_plan=read_csv(output_dir / "auto_live_verification_plan.csv"),
        account_readiness_plan=account_readiness_plan,
        account_path_decision=account_path_decision,
        account_exclusion_impact=read_csv(output_dir / "account_exclusion_impact.csv"),
        account_exclusion_impact_detail=read_csv(output_dir / "account_exclusion_impact_detail.csv"),
        shadow_forward_audit=shadow_forward_audit,
        order_source_diagnostics=frames["order_source_diagnostics.csv"],
        account_reconciliation=read_csv(output_dir / "account_reconciliation.csv"),
        account_reset_audit=read_csv(output_dir / "account_reset_audit.csv"),
        account_import_audit=read_csv(output_dir / "account_import_audit.csv"),
        account_import_positions=read_csv(output_dir / "account_import_positions.csv"),
        account_import_snapshot=read_csv(output_dir / "account_import_snapshot.csv"),
        account_import_check_audit=read_csv(output_dir / "account_import_check_audit.csv"),
        account_import_check_positions=read_csv(output_dir / "account_import_check_positions.csv"),
        account_import_check_snapshot=read_csv(output_dir / "account_import_check_snapshot.csv"),
        account_import_from_state_template=read_csv(output_dir / "account_import_from_state_template.csv"),
        account_import_priced_only_template=read_csv(output_dir / "account_import_priced_only_template.csv"),
        account_repair_review=read_csv(output_dir / "account_repair_review.csv"),
        account_repair_excluded_positions=read_csv(output_dir / "account_repair_excluded_positions.csv"),
        account_price_overrides=read_csv(output_dir / "account_price_overrides.csv"),
        account_price_overrides_template=read_csv(output_dir / "account_price_overrides_template.csv"),
        account_price_override_audit=read_csv(output_dir / "account_price_override_audit.csv"),
        account_price_override_effective=read_csv(output_dir / "account_price_override_effective.csv"),
        account_state_audit=read_csv(output_dir / "account_state_audit.csv"),
        account_state_snapshot=read_csv(output_dir / "account_state_snapshot.csv"),
        account_state_positions=read_csv(output_dir / "account_state_positions.csv"),
        account_state_missing_prices=read_csv(output_dir / "account_state_missing_prices.csv"),
        fill_import_audit=read_csv(output_dir / "fill_import_audit.csv"),
        fill_import_trades=read_csv(output_dir / "fill_import_trades.csv"),
        fill_order_match=read_csv(output_dir / "fill_order_match.csv"),
        execution_quality=execution_quality,
        execution_quality_detail=execution_quality_detail,
        fill_import_positions=read_csv(output_dir / "fill_import_positions.csv"),
        fill_import_snapshot=read_csv(output_dir / "fill_import_snapshot.csv"),
        rpa_orders=read_csv(output_dir / "rpa_orders.csv"),
        rpa_orders_all=read_csv(output_dir / "rpa_orders_all.csv"),
        rpa_blocked_orders=read_csv(output_dir / "rpa_blocked_orders.csv"),
        rpa_pre_trade_checks=read_csv(output_dir / "rpa_pre_trade_checks.csv"),
        rpa_preflight=read_csv(output_dir / "rpa_preflight.csv"),
        order_generation_status=read_csv(output_dir / "order_generation_status.csv"),
        order_artifact_invalidation=read_csv(output_dir / "order_artifact_invalidation.csv"),
        order_liquidity_audit=read_csv(output_dir / "order_liquidity_audit.csv"),
        order_liquidity_all_audit=read_csv(output_dir / "order_liquidity_all_audit.csv"),
        strategy_research=strategy_research,
        strategy_baselines=strategy_baselines,
        backtest_compare=read_csv(output_dir / "strategy_backtest_compare.csv"),
        config=config,
    )
    return paths


def skip_expensive_diagnostics(config: dict[str, Any]) -> bool:
    return bool(config.get("runtime", {}).get("skip_expensive_diagnostics", False))


def order_source_diagnostics(output_dir: Path, orders: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    provenance = item_value_map(read_csv(output_dir / "orders_provenance.csv"))
    reconciliation = read_csv(output_dir / "account_reconciliation.csv")
    source = provenance.get("source", "")
    allow_weak = parse_bool(provenance.get("allow_weak_strategy", ""))
    expected_count = parse_int(provenance.get("order_count", ""), default=None)
    expected_hash = provenance.get("orders_sha256", "")
    actual_hash = orders_frame_sha256(orders)
    execution_date = provenance.get("execution_date", "")
    paper_source = source == "paper"
    current_config_hash = mapping_sha256(config) if config is not None else ""
    provenance_config_hash = provenance.get("config_sha256", "")
    config_hash_match = True if config is None else bool(provenance_config_hash and provenance_config_hash == current_config_hash)
    current_strategy_name = str(config.get("strategy", {}).get("name", "")) if config is not None else ""
    provenance_strategy_name = provenance.get("strategy_name", "")
    strategy_name_match = True if config is None else bool(provenance_strategy_name and provenance_strategy_name == current_strategy_name)
    provenance_integrity_ok, provenance_integrity_reason = (
        verify_order_provenance_integrity(config, provenance) if config is not None else (True, "config_not_provided")
    )
    count_match = expected_count is not None and expected_count == len(orders)
    hash_match = bool(expected_hash) and expected_hash == actual_hash
    date_match = True
    if not orders.empty and "date" in orders and execution_date:
        order_dates = sorted({str(pd.to_datetime(value).date()) for value in orders["date"].dropna()})
        date_match = order_dates == [execution_date]
    elif not orders.empty:
        date_match = False
    reconciliation_ok = not reconciliation.empty and "passed" in reconciliation and bool(
        reconciliation["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"}).all()
    )
    executable = (
        paper_source
        and allow_weak is False
        and count_match
        and hash_match
        and date_match
        and reconciliation_ok
        and config_hash_match
        and strategy_name_match
        and provenance_integrity_ok
    )
    benchmark_exposure = parse_float(provenance.get("benchmark_exposure_multiplier", ""))
    health_exposure = parse_float(provenance.get("strategy_health_exposure_multiplier", ""))
    combined_exposure = parse_float(provenance.get("combined_exposure_multiplier", ""))
    exposure_present = all(value is not None for value in (benchmark_exposure, health_exposure, combined_exposure))
    exposure_consistent = (
        not exposure_present
        or abs(float(combined_exposure) - float(benchmark_exposure) * float(health_exposure)) <= 1e-9
    )
    context = "paper_executable" if executable else "backtest_or_unverified"
    rows = [
        {
            "check": "orders_file_context",
            "passed": executable,
            "value": context,
            "threshold": "paper_executable for RPA/export",
            "message": "orders.csv 只有在来源、门禁、数量、日期、哈希、账户、配置和策略全部一致时，才可视为可执行订单。",
        },
        {
            "check": "paper_provenance_present",
            "passed": paper_source,
            "value": source or "missing",
            "threshold": "paper",
            "message": "缺少 paper 来源记录时，orders.csv 应按回测/研究输出处理。",
        },
        {
            "check": "orders_count_matches_provenance",
            "passed": count_match or orders.empty,
            "value": f"{len(orders)}/{'' if expected_count is None else expected_count}",
            "threshold": "orders/order_count match",
            "message": "订单数量必须与 paper 来源记录一致。",
        },
        {
            "check": "orders_hash_matches_provenance",
            "passed": hash_match or orders.empty,
            "value": actual_hash[:16] if actual_hash else "",
            "threshold": expected_hash[:16] if expected_hash else "orders_sha256 present",
            "message": "订单内容哈希必须与 paper 来源记录一致，防止生成后被手工改动。",
        },
        {
            "check": "orders_date_matches_provenance",
            "passed": date_match,
            "value": execution_date or "missing",
            "threshold": "execution_date",
            "message": "订单执行日期必须与 paper 来源记录一致。",
        },
        {
            "check": "account_reconciliation_passed",
            "passed": reconciliation_ok,
            "value": "missing" if reconciliation.empty else f"{int(reconciliation['passed'].map(lambda value: str(value).strip().lower() in {'true', '1', 'yes'}).sum())}/{len(reconciliation)}",
            "threshold": "all checks passed",
            "message": "可执行订单必须先通过账户、持仓、订单来源和状态库对账。",
        },
        {
            "check": "orders_provenance_integrity",
            "passed": provenance_integrity_ok,
            "value": provenance_integrity_reason,
            "threshold": "valid hash; HMAC required by live config",
            "message": "真实提交配置必须验证订单来源 HMAC，防止订单和来源记录被同时篡改。",
        },
        {
            "check": "orders_config_hash_matches_current",
            "passed": config_hash_match,
            "value": provenance_config_hash[:16] if provenance_config_hash else "missing",
            "threshold": current_config_hash[:16] if current_config_hash else "current config",
            "message": "orders.csv 必须由当前配置生成，防止旧配置订单被后续配置误执行。",
        },
        {
            "check": "orders_strategy_matches_current",
            "passed": strategy_name_match,
            "value": provenance_strategy_name or "missing",
            "threshold": current_strategy_name or "current strategy",
            "message": "orders.csv 的策略名必须与当前配置 strategy.name 一致。",
        },
        {
            "check": "benchmark_exposure_multiplier",
            "passed": benchmark_exposure is None or 0.0 <= benchmark_exposure <= 1.0,
            "value": "missing" if benchmark_exposure is None else benchmark_exposure,
            "threshold": "0.0..1.0",
            "message": "大盘趋势与波动率风控对目标股票仓位施加的乘数。",
        },
        {
            "check": "strategy_health_exposure_multiplier",
            "passed": health_exposure is None or 0.0 <= health_exposure <= 1.0,
            "value": "missing" if health_exposure is None else health_exposure,
            "threshold": "0.0..1.0",
            "message": "冻结候选触发前向预警时使用的独立操作层仓位乘数，不改变回测和影子证据。",
        },
        {
            "check": "combined_exposure_multiplier",
            "passed": combined_exposure is None or 0.0 <= combined_exposure <= 1.0,
            "value": "missing" if combined_exposure is None else combined_exposure,
            "threshold": "benchmark x strategy_health",
            "message": f"最终目标股票仓位乘数；触发原因：{provenance.get('strategy_health_exposure_reason', 'missing')}。",
        },
        {
            "check": "operational_exposure_overlay_consistent",
            "passed": exposure_consistent,
            "value": "legacy_or_missing" if not exposure_present else exposure_consistent,
            "threshold": "combined = benchmark x strategy_health",
            "message": "仓位保护乘数必须能从订单来源记录完整复算；旧订单缺失时仅展示，不改变既有兼容性。",
        },
        {
            "check": "rpa_exportable",
            "passed": executable,
            "value": executable,
            "threshold": True,
            "message": "为 False 时，不应将当前 orders.csv 导出或填入券商客户端。",
        },
    ]
    return pd.DataFrame(rows, columns=DEFAULT_COLUMNS["order_source_diagnostics.csv"])

def item_value_map(frame: pd.DataFrame) -> dict[str, str]:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return {}
    return {str(row.item): "" if pd.isna(row.value) else str(row.value) for row in frame.itertuples(index=False)}


def parse_bool(value: object) -> bool | None:
    text = str(value).strip().lower()
    if text in {"false", "0", "no"}:
        return False
    if text in {"true", "1", "yes"}:
        return True
    return None


def parse_int(value: object, default: int | None = 0) -> int | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return int(parsed)


def parse_float(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return float(parsed)


def formal_candidate_report_link(path: Path, observation: pd.DataFrame) -> str:
    if observation.empty or "candidate_output_dir" not in observation.columns:
        return ""
    row = observation.iloc[-1]
    output_value = str(row.get("candidate_output_dir", "") or "").strip()
    if not output_value:
        return ""
    target = Path(output_value) / "report.html"
    try:
        if not target.exists() or target.resolve() == path.resolve():
            return ""
        href = Path(os.path.relpath(target.resolve(), path.parent.resolve())).as_posix()
    except (OSError, ValueError):
        return ""
    candidate_id = str(row.get("candidate_id", "") or "").strip() or "未登记"
    observed = parse_int(row.get("max_observed_trading_days"), default=0) or 0
    required = parse_int(row.get("required_forward_trading_days"), default=0) or 0
    status = str(row.get("status", "") or "").strip()
    status_labels = {
        "observed": "观察已更新",
        "waiting_for_market_data": "等待完整行情",
        "failed": "观察失败",
    }
    status_label = status_labels.get(status, status or "状态未知")
    label = f"正式冻结候选 {candidate_id} · 前向 {observed}/{required} · {status_label}"
    return (
        f'<a class="gate-link candidate-link" href="{escape(href, quote=True)}">'
        f"{escape(label)}</a>"
    )


def write_html_report(path: Path, **kwargs: Any) -> Path:
    frame_keys = [
        "daily_observation_summary", "daily_observation_audit",
        "daily_observation_progress", "daily_observation_progress_audit",
        "equity", "orders", "positions", "exposure", "industry_risk_audit", "comparison", "excess_bootstrap", "data_quality", "index_membership_quality", "index_membership_snapshots", "survivorship_bias_audit", "survivorship_bias_snapshots", "data_source_health",
        "robustness", "annual", "rolling", "regime_validation", "strategy_reliability", "strategy_health",
        "strategy_candidate_stability", "strategy_promotion", "research_candidate_observation", "forward_only_candidate_observation", "forward_candidate_attribution", "forward_candidate_selection", "forward_candidate_selection_summary", "candidate_failure_history", "risk_exposure", "walk_forward", "walk_forward_summary",
        "execution_model_reconciliation", "execution_model_reconciliation_summary",
        "strategy_candidate_family", "strategy_candidate_similarity",
        "strategy_research_gate",
        "strategy_family_selection", "strategy_family_selection_splits",
        "cost_stress", "run_manifest", "live_candidate", "live_candidate_validation", "live_candidate_forward_progress", "forward_factor_monitor",
        "candidate_activation_audit",
        "candidate_deactivation_audit",
        "active_execution_status",
        "order_generation_status",
        "order_artifact_invalidation",
        "live_readiness", "practical_readiness", "live_readiness_roadmap", "execution_readiness",
        "execution_readiness_roadmap", "execution_modes", "execution_playbook", "auto_live_readiness",
        "capital_sensitivity", "capital_sensitivity_audit", "micro_capital_sensitivity", "micro_capital_sensitivity_audit", "rebalance_frequency_research", "rebalance_frequency_research_audit",
        "auto_live_summary", "auto_live_journal_summary", "auto_live_journal_audit", "auto_live_event_journal", "auto_live_journal_errors", "auto_live_account_binding_audit", "auto_live_recovery_points", "auto_live_recovery_audit", "auto_live_alert_audit", "auto_live_alert_outbox", "auto_live_alert_selftest", "auto_live_rollout_audit", "auto_live_rollout_history", "auto_live_service_summary", "auto_live_service_history", "auto_live_service_heartbeat", "auto_live_service_lock_audit", "auto_live_service_stop_audit", "auto_live_batch_summary", "auto_live_batch_plan", "auto_live_batch_orders", "auto_live_current_batch", "auto_live_cycle_summary", "auto_live_cycle", "auto_live_cycle_history", "auto_live_post_trade_reconciliation", "auto_live_post_trade_reconciliation_history", "auto_live_post_trade_manifest", "auto_live_window_audit", "auto_live_submit_lock_audit", "auto_live_risk_audit", "auto_live_risk_reservations", "auto_live_stop_audit", "auto_live_action_confirmation", "huatai_canary_audit", "auto_live_submission_intent", "auto_live_blocked_attempt", "auto_live_account_refresh", "auto_live_broker_account_snapshot", "auto_live_broker_positions", "auto_live_broker_account_audit", "broker_account_import_preview_audit", "broker_account_initialization_audit", "broker_account_initialization_provenance", "broker_capital_feasibility_audit", "broker_capital_feasibility_detail", "broker_canary_capital_audit", "broker_canary_capital_candidates", "broker_capital_transition", "capital_mode_assessment", "broker_order_preview", "broker_order_preview_signal", "broker_order_preview_audit", "auto_live_run", "auto_live_order_results", "auto_live_ack_audit", "auto_live_order_status", "auto_live_order_status_audit", "auto_live_submission_recovery", "auto_live_cancel_results", "auto_live_cancel_audit", "auto_live_cancel_verification", "auto_live_cancel_verification_audit", "auto_live_cancel_lock_audit", "auto_live_fill_import", "auto_live_fill_progress", "auto_live_fill_sync_audit", "auto_live_settlement_audit", "auto_live_submission_ledger", "auto_live_adapter_audit", "auto_live_adapter_selftest", "auto_live_adapter_replay_summary", "auto_live_adapter_replay_audit", "auto_live_adapter_file_audit", "auto_live_blocker_details", "auto_live_adapter_manifest", "evidence_plan", "observation_schedule", "auto_live_remediation_plan", "auto_live_verification_plan", "account_readiness_plan", "account_path_decision", "account_exclusion_impact", "account_exclusion_impact_detail", "shadow_forward_audit",
        "order_source_diagnostics", "account_reconciliation", "account_reset_audit", "account_import_audit",
        "account_import_positions", "account_import_snapshot", "account_import_check_audit", "account_import_check_positions",
        "account_import_check_snapshot", "account_import_from_state_template", "account_import_priced_only_template",
        "account_repair_review", "account_repair_excluded_positions", "account_price_overrides",
        "account_price_overrides_template", "account_price_override_audit", "account_price_override_effective",
        "account_state_audit", "account_state_snapshot", "account_state_positions", "account_state_missing_prices",
        "fill_import_audit", "fill_import_trades", "fill_order_match", "execution_quality", "execution_quality_detail",
        "fill_import_positions", "fill_import_snapshot", "rpa_orders", "rpa_orders_all", "rpa_blocked_orders",
        "rpa_pre_trade_checks", "rpa_preflight", "order_liquidity_audit", "order_liquidity_all_audit",
        "strategy_research", "strategy_baselines", "factor_robustness", "factor_robustness_summary", "backtest_compare",
    ]
    frames = {key: kwargs.get(key, pd.DataFrame()) for key in frame_keys}
    metrics = kwargs.get("metrics") or calc_metrics(frames["equity"])
    config = kwargs.get("config", {})

    warning_items = (
        bias_warnings(frames["data_quality"], frames["robustness"])
        + index_membership_quality_warnings(frames["index_membership_quality"])
        + survivorship_warnings(frames["survivorship_bias_audit"])
        + rolling_warnings(frames["rolling"])
        + regime_validation_warnings(frames["regime_validation"])
        + walk_forward_warnings(frames["walk_forward_summary"])
        + strategy_reliability_warnings(frames["strategy_reliability"])
        + strategy_candidate_stability_warnings(frames["strategy_candidate_stability"])
        + forward_factor_warnings(
            frames["forward_factor_monitor"],
            float(config.get("strategy_health", {}).get("forward_factor_adverse_weight_warning", 0.50)),
        )
    )
    warning_html = "".join(f"<li>{escape(str(item))}</li>" for item in warning_items) if warning_items else "<li>未发现高优先级诊断警告。</li>"
    strict_level = readiness_level(frames["live_readiness"])
    practical_level = practical_readiness_level(frames["practical_readiness"])
    execution_level = execution_readiness_level(frames["execution_readiness"])
    strict_class = "pass" if readiness_passed(frames["live_readiness"]) else "fail"
    practical_class = "pass" if readiness_passed(frames["practical_readiness"]) else "fail"
    execution_class = "pass" if execution_level == "执行就绪" or str(execution_level).startswith("人工工单就绪") else "fail"
    auto_live_summary = frames["auto_live_summary"]
    auto_live_allowed = False
    if not auto_live_summary.empty and "auto_live_allowed" in auto_live_summary.columns:
        auto_live_allowed = str(auto_live_summary["auto_live_allowed"].iloc[0]).strip().lower() in {"true", "1", "yes"}
    auto_live_level = "允许" if auto_live_allowed else "未允许"
    auto_live_class = "pass" if auto_live_allowed else "fail"

    section_specs = [
        ("\u6bcf\u65e5\u81ea\u52a8\u89c2\u5bdf\u72b6\u6001", frames["daily_observation_summary"], "\u6bcf\u65e5\u81ea\u52a8\u89c2\u5bdf\u4efb\u52a1\u5c1a\u672a\u8fd0\u884c\u3002"),
        ("\u6bcf\u65e5\u81ea\u52a8\u89c2\u5bdf\u5ba1\u8ba1", frames["daily_observation_audit"], "\u6682\u65e0\u6bcf\u65e5\u81ea\u52a8\u89c2\u5bdf\u5ba1\u8ba1\u3002"),
        ("\u5019\u9009\u89c2\u5bdf\u8bc1\u636e\u8fdb\u5ea6", frames["daily_observation_progress"], "\u6682\u65e0\u5019\u9009\u89c2\u5bdf\u8bc1\u636e\u8fdb\u5ea6\u3002"),
        ("\u5019\u9009\u89c2\u5bdf\u8fdb\u5ea6\u5ba1\u8ba1", frames["daily_observation_progress_audit"], "\u6682\u65e0\u5019\u9009\u89c2\u5bdf\u8fdb\u5ea6\u5ba1\u8ba1\u3002"),
        ("自动实盘证据计划", frames["evidence_plan"], "暂无自动实盘证据计划。运行 aq evidence-plan 生成。"),
        ("自动实盘当前结论", frames["auto_live_summary"], "暂无自动实盘结论。运行 aq auto-live-check 生成。"),
        ("自动实盘审计链结论", frames["auto_live_journal_summary"], "暂无自动实盘审计链结论。"),
        ("自动实盘审计链校验", frames["auto_live_journal_audit"], "暂无自动实盘审计链校验。运行 aq auto-live-journal 生成。"),
        ("自动实盘事件日志", frames["auto_live_event_journal"], "暂无自动实盘事件。"),
        ("自动实盘审计写入错误", frames["auto_live_journal_errors"], "暂无审计写入错误。"),
        ("自动实盘账户绑定审计", frames["auto_live_account_binding_audit"], "暂无账户绑定审计。运行 aq auto-live-account-binding 生成。"),
        ("自动实盘恢复点", frames["auto_live_recovery_points"], "暂无提交前恢复点。"),
        ("自动实盘恢复审计", frames["auto_live_recovery_audit"], "暂无恢复审计。运行 aq auto-live-recovery-audit 生成。"),
        ("自动实盘告警通道审计", frames["auto_live_alert_audit"], "暂无告警通道审计。"),
        ("自动实盘告警自检", frames["auto_live_alert_selftest"], "暂无告警自检。运行 aq auto-live-alert-selftest 生成。"),
        ("自动实盘告警发件箱", frames["auto_live_alert_outbox"], "暂无告警事件。"),
        ("自动实盘分阶段授权审计", frames["auto_live_rollout_audit"], "暂无分阶段授权审计。"),
        ("自动实盘分阶段授权历史", frames["auto_live_rollout_history"], "暂无授权或撤销历史。"),
        ("自动实盘服务结论", frames["auto_live_service_summary"], "暂无自动实盘服务运行记录。运行 aq auto-live-service 生成。"),
        ("自动实盘服务心跳", frames["auto_live_service_heartbeat"], "暂无自动实盘服务心跳。"),
        ("自动实盘服务周期历史", frames["auto_live_service_history"], "暂无自动实盘服务周期历史。"),
        ("自动实盘服务锁审计", frames["auto_live_service_lock_audit"], "暂无自动实盘服务锁审计。"),
        ("自动实盘服务停止记录", frames["auto_live_service_stop_audit"], "暂无自动实盘服务停止记录。"),
        ("自动实盘批次结论", frames["auto_live_batch_summary"], "暂无自动实盘批次结论。运行 aq auto-live-batch 生成。"),
        ("自动实盘批次计划", frames["auto_live_batch_plan"], "暂无自动实盘批次计划。"),
        ("自动实盘批次订单", frames["auto_live_batch_orders"], "暂无自动实盘批次订单。"),
        ("自动实盘当前批次", frames["auto_live_current_batch"], "当前没有允许提交的批次。"),
        ("自动实盘周期结论", frames["auto_live_cycle_summary"], "暂无自动实盘周期结论。运行 aq auto-live-cycle 生成。"),
        ("自动实盘周期阶段", frames["auto_live_cycle"], "暂无自动实盘周期阶段记录。运行 aq auto-live-cycle 生成。"),
        ("自动实盘周期历史", frames["auto_live_cycle_history"], "暂无自动实盘周期历史。"),
        ("自动实盘后交易账户对账", frames["auto_live_post_trade_reconciliation"], "暂无后交易账户对账。仅在显式成交写回后生成。"),
        ("后交易对账证据绑定", frames["auto_live_post_trade_manifest"], "暂无后交易对账证据；只有显式同步账户并重新读取券商快照后才生成。"),
        ("自动实盘后交易账户对账历史", frames["auto_live_post_trade_reconciliation_history"], "暂无后交易账户对账历史。"),
        ("自动实盘门控明细", frames["auto_live_readiness"], "暂无自动实盘门控明细。运行 aq auto-live-check 生成。"),
        ("自动实盘提交时间窗审计", frames["auto_live_window_audit"], "暂无自动实盘提交时间窗审计。运行 aq auto-live-window 生成。"),
        ("自动实盘提交锁审计", frames["auto_live_submit_lock_audit"], "暂无提交锁审计。运行 aq auto-live-lock 生成。"),
        ("自动实盘独立风控", frames["auto_live_risk_audit"], "暂无自动实盘风险审计。运行 aq auto-live-risk 生成。"),
        ("自动实盘当日风险预留", frames["auto_live_risk_reservations"], "暂无真实提交风险预留。"),
        ("自动实盘紧急停机记录", frames["auto_live_stop_audit"], "暂无紧急停机操作记录。"),
        ("自动实盘动作确认", frames["auto_live_action_confirmation"], "暂无动作确认票据；只有全部门禁通过后才会签发。"),
        ("华泰 Canary 武装审计", frames["huatai_canary_audit"], "暂无华泰 Canary 审计。运行 aq huatai-canary-audit 生成。"),
        ("自动实盘提交意图", frames["auto_live_submission_intent"], "暂无真实提交意图。"),
        ("自动实盘被阻断尝试", frames["auto_live_blocked_attempt"], "暂无重复或并发提交阻断记录。"),
        ("自动实盘券商账户自动刷新", frames["auto_live_account_refresh"], "暂无券商账户自动刷新审计。运行 aq auto-live-run 后生成。"),
        ("自动实盘券商账户快照", frames["auto_live_broker_account_snapshot"], "暂无券商账户快照。运行 aq auto-live-account 生成。"),
        ("自动实盘券商持仓快照", frames["auto_live_broker_positions"], "暂无券商持仓快照。运行 aq auto-live-account 生成。"),
        ("自动实盘券商账户审计", frames["auto_live_broker_account_audit"], "暂无券商账户审计。运行 aq auto-live-account 生成。"),
        ("券商账户导入预检", frames["broker_account_import_preview_audit"], "暂无券商账户导入预检；运行 broker-refresh 生成。"),
        ("券商账户账本初始化审计", frames["broker_account_initialization_audit"], "暂无账本初始化审计；broker-account-init 默认只检查，不写数据库。"),
        ("券商账户账本初始化来源", frames["broker_account_initialization_provenance"], "暂无账本初始化来源记录。"),
        ("\u672c\u91d1\u654f\u611f\u6027\u56de\u6d4b", frames["capital_sensitivity"], "\u6682\u65e0\u591a\u672c\u91d1\u6574\u624b\u56de\u6d4b\uff1b\u8fd0\u884c capital-sensitivity \u751f\u6210\u3002"),
        ("\u672c\u91d1\u654f\u611f\u6027\u5ba1\u8ba1", frames["capital_sensitivity_audit"], "\u6682\u65e0\u672c\u91d1\u654f\u611f\u6027\u5ba1\u8ba1\u3002"),
        ("小资金整手压力研究", frames["micro_capital_sensitivity"], "暂无小资金整手压力研究。"),
        ("小资金研究审计", frames["micro_capital_sensitivity_audit"], "暂无小资金研究审计。"),
        ("调仓频率研究", frames["rebalance_frequency_research"], "暂无月度与季度调仓对照研究。"),
        ("调仓频率研究审计", frames["rebalance_frequency_research_audit"], "暂无调仓频率研究审计。"),
        ("实盘资金可执行性审计", frames["broker_capital_feasibility_audit"], "暂无资金可执行性审计；运行 broker-refresh 生成。"),
        ("实盘资金整手需求明细", frames["broker_capital_feasibility_detail"], "暂无整手需求明细。"),
        ("实盘资金过渡结论", frames["broker_capital_transition"], "暂无完整策略与 Canary 资金过渡结论。"),
        ("资金运行模式", frames["capital_mode_assessment"], "暂无资金运行模式结论；重新运行部署门禁生成。"),
        ("Canary 一手资金审计", frames["broker_canary_capital_audit"], "暂无 Canary 一手可负担性审计；重新刷新真实账户后生成。"),
        ("Canary 非执行候选", frames["broker_canary_capital_candidates"], "当前没有同时满足整手、可用资金和 1000 元限额的候选。"),
        ("券商订单只读预览", frames["broker_order_preview"], "暂无订单预览；预览文件不会进入提交链路。"),
        ("券商订单预览信号", frames["broker_order_preview_signal"], "暂无订单预览信号。"),
        ("券商订单预览审计", frames["broker_order_preview_audit"], "暂无订单预览审计；运行 broker-refresh 生成。"),
        ("自动实盘运行审计", frames["auto_live_run"], "暂无自动实盘运行审计。运行 aq auto-live-run 后生成。"),
        ("自动实盘订单提交结果", frames["auto_live_order_results"], "暂无自动实盘订单提交结果。"),
        ("自动实盘券商回报审计", frames["auto_live_ack_audit"], "暂无自动实盘券商回报审计。运行 aq auto-live-run 后生成。"),
        ("自动实盘委托状态", frames["auto_live_order_status"], "暂无自动实盘委托状态。运行 aq auto-live-query 后生成。"),
        ("自动实盘委托状态审计", frames["auto_live_order_status_audit"], "暂无自动实盘委托状态审计。运行 aq auto-live-query 后生成。"),
        ("自动实盘不确定提交恢复", frames["auto_live_submission_recovery"], "暂无不确定提交恢复记录。运行 aq auto-live-query 后生成。"),
        ("自动实盘撤单结果", frames["auto_live_cancel_results"], "暂无自动实盘撤单结果。运行 aq auto-live-cancel 后生成。"),
        ("自动实盘撤单审计", frames["auto_live_cancel_audit"], "暂无自动实盘撤单审计。运行 aq auto-live-cancel 后生成。"),
        ("自动实盘撤单终态验证", frames["auto_live_cancel_verification"], "暂无撤单终态验证；撤单后重新运行 auto-live-query 生成。"),
        ("自动实盘撤单终态审计", frames["auto_live_cancel_verification_audit"], "暂无撤单终态审计；cancel_requested 不代表券商已完成撤单。"),
        ("自动实盘撤单锁审计", frames["auto_live_cancel_lock_audit"], "当前没有撤单锁审计；正常状态应显示锁为空。"),
        ("自动实盘成交导入候选", frames["auto_live_fill_import"], "暂无自动实盘成交导入候选。运行 aq auto-live-fills 后生成。"),
        ("自动实盘累计成交差额", frames["auto_live_fill_progress"], "暂无累计成交差额记录。系统只会把相对本地账本新增的成交写入账户。"),
        ("自动实盘成交同步审计", frames["auto_live_fill_sync_audit"], "暂无自动实盘成交同步审计。运行 aq auto-live-fills 后生成。"),
        ("自动实盘结算审计", frames["auto_live_settlement_audit"], "暂无自动实盘结算审计。运行 aq auto-live-settle 后生成。"),
        ("自动实盘提交台账", frames["auto_live_submission_ledger"], "暂无自动实盘提交台账。系统只有真实提交适配器被调用后才写入。"),
        ("自动实盘适配器审计", frames["auto_live_adapter_audit"], "暂无自动实盘适配器审计。运行 aq auto-live-check 生成。"),
        ("自动实盘适配器自检", frames["auto_live_adapter_selftest"], "暂无适配器自检。运行 aq auto-live-adapter-selftest 生成。"),
        ("自动实盘适配器文件哈希", frames["auto_live_adapter_file_audit"], "暂无适配器文件哈希审计。运行 aq auto-live-adapter-files 生成。"),
        ("自动实盘适配器回放认证结论", frames["auto_live_adapter_replay_summary"], "暂无适配器回放认证。运行 aq auto-live-adapter-replay 生成。"),
        ("自动实盘适配器回放认证明细", frames["auto_live_adapter_replay_audit"], "暂无适配器回放认证明细。"),
        ("自动实盘阻断明细", frames["auto_live_blocker_details"], "暂无自动实盘阻断明细。运行 aq auto-live-check 生成。"),
        ("自动实盘适配器指纹", frames["auto_live_adapter_manifest"], "暂无自动实盘适配器指纹。运行 aq auto-live-check 生成。"),
        ("自动观察调度计划", frames["observation_schedule"], "暂无自动观察调度计划。运行 aq observation-plan 生成。"),
        ("自动实盘修复计划", frames["auto_live_remediation_plan"], "暂无自动实盘修复计划。运行 aq remediation-plan 生成。"),
        ("自动实盘修复后验证链", frames["auto_live_verification_plan"], "暂无修复后验证链。运行 aq verification-plan 生成。"),
        ("订单文件来源诊断", frames["order_source_diagnostics"], "暂无订单文件来源诊断。"),
        ("执行模式汇总", frames["execution_modes"], "暂无执行模式汇总。"),
        ("运行方案", frames["execution_playbook"], "暂无运行方案。运行 aq execution-ready 后生成。"),
        ("执行就绪检查", frames["execution_readiness"], "暂无执行就绪检查。"),
        ("执行准备路线图", frames["execution_readiness_roadmap"], "暂无执行准备路线图。"),
        ("账户路径预判", frames["account_path_decision"], "暂无账户路径预判。运行 aq account-paths 后生成。"),
        ("账户缺价排除影响", frames["account_exclusion_impact"], "暂无账户缺价排除影响审计。运行 aq account-exclusion-audit 生成。"),
        ("账户缺价排除明细", frames["account_exclusion_impact_detail"], "暂无账户缺价排除明细。"),
        ("账户准备计划", frames["account_readiness_plan"], "暂无账户准备计划。运行 aq account-plan 后生成。"),
        ("策略可信度与过拟合检查", frames["strategy_reliability"], "暂无策略可信度诊断。"),
        ("超额收益区块 Bootstrap", frames["excess_bootstrap"], "暂无超额收益统计检验。"),
        ("策略运行健康与降级监控", frames["strategy_health"], "暂无策略健康监控。运行 aq strategy-health 后生成。"),
        ("候选策略稳定性诊断", frames["strategy_candidate_stability"], "暂无候选策略稳定性诊断。先运行 aq research 生成候选研究输出。"),
        ("\u5019\u9009\u7b56\u7565\u65cf\u805a\u7c7b", frames["strategy_candidate_family"], "\u6682\u65e0\u5019\u9009\u7b56\u7565\u65cf\u805a\u7c7b\u3002\u5148\u8fd0\u884c aq research \u751f\u6210\u5019\u9009\u7814\u7a76\u8f93\u51fa\u3002"),
        ("\u5019\u9009\u7b56\u7565\u6298\u6536\u76ca\u76f8\u4f3c\u5ea6", frames["strategy_candidate_similarity"], "\u6682\u65e0\u5019\u9009\u7b56\u7565\u6298\u6536\u76ca\u76f8\u4f3c\u5ea6\u3002"),
        ("\u7b56\u7565\u65cf\u7ea7\u9009\u62e9\u504f\u5dee", frames["strategy_family_selection"], "\u6682\u65e0\u7b56\u7565\u65cf\u7ea7\u9009\u62e9\u504f\u5dee\u5ba1\u8ba1\u3002"),
        ("研究候选门禁", frames["strategy_research_gate"], "暂无研究候选门禁状态。运行 aq research 或 aq report 生成。"),
        ("\u7b56\u7565\u65cf\u4e92\u8865\u7559\u51fa\u660e\u7ec6", frames["strategy_family_selection_splits"], "\u6682\u65e0\u7b56\u7565\u65cf\u4e92\u8865\u7559\u51fa\u660e\u7ec6\u3002"),
        ("因子稳健性汇总", frames["factor_robustness_summary"], "暂无因子稳健性汇总。运行 aq factor-robustness 生成。"),
        ("因子扰动压力测试", frames["factor_robustness"], "暂无因子扰动压力测试。运行 aq factor-robustness 生成。"),
        ("研究与正式撮合一致性汇总", frames["execution_model_reconciliation_summary"], "暂无执行模型一致性汇总。运行 aq compare 生成。"),
        ("研究与正式撮合一致性明细", frames["execution_model_reconciliation"], "暂无执行模型一致性明细。运行 aq compare 生成。"),
        ("策略晋级看板", frames["strategy_promotion"], "运行 aq compare 生成策略晋级看板。"),
        ("研究候选持续观察", frames["research_candidate_observation"], "暂无隔离研究候选观察记录。"),
        ("前向候选不可改写观察", frames["forward_only_candidate_observation"], "暂无前向候选观察记录。运行一键系统后生成。"),
        ("冻结候选锚定/多因子前向归因", frames["forward_candidate_attribution"], "暂无可观察的锚定与多因子卫星收益归因；新信号成交后自动生成。"),
        ("前向候选配对决赛结论", frames["forward_candidate_selection_summary"], "暂无配对决赛结论；运行 forward-daily 后生成。"),
        ("前向候选配对收益明细", frames["forward_candidate_selection"], "决赛政策冻结后尚未累计足够的共同交易日。"),
        ("候选失败隔离历史", frames["candidate_failure_history"], "暂无候选策略前向失败隔离记录。"),
        ("实用门禁检查", frames["practical_readiness"], "暂无实用门禁检查。"),
        ("严格实盘就绪检查", frames["live_readiness"], "暂无严格实盘就绪检查。"),
        ("冻结候选参数", frames["live_candidate"], "暂无冻结候选参数。"),
        ("冻结后前向验证进度", frames["live_candidate_forward_progress"], "暂无冻结后前向验证进度。"),
        ("冻结后前向验证", frames["live_candidate_validation"], "暂无冻结后前向验证。"),
        ("候选执行激活审计", frames["candidate_activation_audit"], "暂无候选激活审计。运行 candidate-activation-audit 生成。"),
        ("当前执行策略身份", frames["active_execution_status"], "暂无执行策略身份审计。运行一键系统后生成。"),
        ("候选执行停用审计", frames["candidate_deactivation_audit"], "暂无候选停用审计。运行 deactivate-candidate 生成。"),
        ("订单生成状态", frames["order_generation_status"], "暂无订单生成状态。"),
        ("旧执行产物隔离审计", frames["order_artifact_invalidation"], "暂无执行产物隔离记录。"),
        ("Shadow 前向观察", frames["shadow_forward_audit"], "暂无 Shadow 前向观察。"),
        ("\u51bb\u7ed3\u540e\u56e0\u5b50\u524d\u5411\u76d1\u63a7", frames["forward_factor_monitor"], "\u6682\u65e0\u53ef\u8bc4\u4f30\u7684\u51bb\u7ed3\u540e\u56e0\u5b50\u6837\u672c\u3002"),
        ("账户/订单对账", frames["account_reconciliation"], "暂无账户/订单对账。运行 paper 或 aq reconcile 后生成。"),
        ("账户重置审计", frames["account_reset_audit"], "暂无账户重置审计。"),
        ("账户导入审计", frames["account_import_audit"], "暂无账户导入审计。运行 aq account-template 和 aq account-import 后生成。"),
        ("账户导入快照", frames["account_import_snapshot"], "暂无账户导入快照。"),
        ("账户导入持仓", frames["account_import_positions"], "暂无账户导入持仓。"),
        ("账户导入预检查", frames["account_import_check_audit"], "暂无账户导入预检查。运行 aq account-import-check 后生成。"),
        ("账户导入预检查快照", frames["account_import_check_snapshot"], "暂无账户导入预检查快照。"),
        ("账户导入预检查持仓", frames["account_import_check_positions"], "暂无账户导入预检查持仓。"),
        ("账户修复审阅模板", frames["account_repair_review"], "暂无账户修复审阅模板。运行 aq account-repair-template 后生成。"),
        ("账户状态导入模板", frames["account_import_from_state_template"], "暂无账户状态导入模板。运行 aq account-repair-template 后生成。"),
        ("可估值持仓导入候选模板", frames["account_import_priced_only_template"], "暂无可估值持仓导入候选模板。运行 aq account-repair-template 后生成。"),
        ("账户修复排除清单", frames["account_repair_excluded_positions"], "暂无账户修复排除清单。"),
        ("账户手工价格覆盖", frames["account_price_overrides"], "暂无账户手工价格覆盖。填写 outputs/account_price_overrides.csv 后生成。"),
        ("账户手工价格覆盖模板", frames["account_price_overrides_template"], "暂无账户手工价格覆盖模板。运行 aq account-price-template 后生成。"),
        ("账户手工价格覆盖审计", frames["account_price_override_audit"], "暂无账户手工价格覆盖审计。运行 aq account-price-check 后生成。"),
        ("账户手工价格实际生效项", frames["account_price_override_effective"], "暂无账户手工价格实际生效项。运行 aq account-price-check 后生成。"),
        ("当前账户状态审计", frames["account_state_audit"], "暂无当前账户状态审计。运行 aq account-state 后生成。"),
        ("当前账户缺价持仓", frames["account_state_missing_prices"], "暂无缺价持仓。"),
        ("当前账户状态快照", frames["account_state_snapshot"], "暂无当前账户状态快照。"),
        ("当前账户状态持仓", frames["account_state_positions"], "暂无当前账户状态持仓。"),
        ("成交导入审计", frames["fill_import_audit"], "暂无成交导入审计。运行 aq fill-template 和 aq fill-import 后生成。"),
        ("成交与订单匹配", frames["fill_order_match"], "暂无成交与订单匹配明细。"),
        ("成交质量复盘", frames["execution_quality"], "暂无成交质量复盘。运行 aq execution-quality 或 aq fill-import 后生成。"),
        ("成交质量复盘明细", frames["execution_quality_detail"], "暂无成交质量复盘明细。导入成交后生成。"),
        ("成交导入快照", frames["fill_import_snapshot"], "暂无成交导入快照。"),
        ("成交导入持仓", frames["fill_import_positions"], "暂无成交导入持仓。"),
        ("成交导入明细", frames["fill_import_trades"], "暂无成交导入明细。"),
        ("RPA 下单前检查", frames["rpa_pre_trade_checks"], "暂无 RPA 下单前检查。"),
        ("RPA 前置环境审计", frames["rpa_preflight"], "暂无 RPA 前置环境审计。运行 aq rpa-preflight 生成。"),
        ("订单流动性与容量审计", frames["order_liquidity_audit"], "暂无订单流动性审计。运行 aq order-audit 或 export-orders 后生成。"),
        ("数据源健康", frames["data_source_health"], "暂无数据源健康诊断。运行 aq source-health 后生成。"),
        ("数据质量诊断", frames["data_quality"], "暂无数据质量诊断。"),
        ("时点指数成分质量门禁", frames["index_membership_quality"], "当前策略未启用时点指数成分，或尚未生成质量审计。"),
        ("时点指数成分快照覆盖", frames["index_membership_snapshots"], "暂无时点指数成分快照覆盖明细。"),
        ("幸存者偏差审计", frames["survivorship_bias_audit"], "暂无幸存者偏差审计；长期结果不能据此视为无偏。"),
        ("幸存者偏差年度快照", frames["survivorship_bias_snapshots"], "暂无年度股票池覆盖快照。"),
        ("样本内 / 样本外拆分", frames["robustness"], "暂无拆分数据。"),
        ("市场状态分段验证", frames["regime_validation"], "暂无市场状态分段验证。"),
        ("走步参数冻结验证", frames["walk_forward_summary"], "暂无走步汇总。"),
        ("走步验证明细", frames["walk_forward"], "暂无走步明细。"),
        ("正式回测对比", frames["backtest_compare"], "先运行 aq compare 汇总正式回测输出目录。"),
        ("市面常见基线策略对比", frames["strategy_baselines"].head(12), "先运行 aq baselines 生成常见因子基线对比。"),
        ("策略候选排名", frames["strategy_research"].head(12), "先运行 aq research 生成候选策略比较。"),
        ("成本压力测试", frames["cost_stress"], "暂无成本压力测试。"),
        ("年度稳定性", frames["annual"], "暂无年度统计。"),
        ("最近滚动验证", frames["rolling"].tail(12), "暂无滚动验证。"),
        ("风险覆盖层日志", frames["risk_exposure"].tail(12), "暂无风险覆盖层日志。"),
        ("最近订单", frames["orders"].tail(30), "暂无订单。"),
        ("当前持仓", frames["positions"], "暂无持仓。"),
        ("行业暴露", frames["exposure"], "暂无行业暴露。"),
        ("成交后行业风险审计", frames["industry_risk_audit"], "暂无成交后行业风险审计。"),
        ("运行追踪", frames["run_manifest"], "暂无运行追踪。"),
        ("最近净值", frames["equity"].tail(30), "暂无净值数据。"),
        ("实盘就绪路线图", frames["live_readiness_roadmap"], "暂无实盘就绪路线图。"),
    ]
    sections = "\n  ".join(section_html(title, frame, empty) for title, frame, empty in section_specs)
    deployment_gate_link = (
        '<a class="gate-link" href="deployment_gate.html">查看实盘部署门禁</a>'
        if (path.parent / "deployment_gate.html").exists()
        else ""
    )
    formal_candidate_link = formal_candidate_report_link(
        path,
        frames["forward_only_candidate_observation"],
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A 股量化交易系统报告</title>
  <style>
    * {{ box-sizing:border-box; }}
    html,body {{ max-width:100%; overflow-x:hidden; }}
    body {{ margin:0; background:#f5f5f7; color:#1d1d1f; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif; line-height:1.5; }}
    main {{ width:100%; max-width:1280px; margin:0 auto; padding:32px 24px 48px; }}
    header {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:22px; }}
    header > div {{ min-width:0; }}
    .pills {{ display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }}
    .header-links {{ display:flex;flex-wrap:wrap;gap:8px;margin-top:12px; }}
    .gate-link {{ display:inline-flex;color:#fff;background:#0071e3;padding:8px 12px;border-radius:7px;text-decoration:none;font-size:13px;font-weight:600; }}
    .candidate-link {{ background:#1d1d1f; }}
    h1 {{ font-size:28px; margin:0 0 6px; }}
    h2 {{ font-size:18px; margin:0 0 12px; }}
    p {{ color:#6e6e73; margin:0; overflow-wrap:anywhere; }}
    .pill {{ display:inline-flex; border-radius:999px; padding:6px 10px; font-size:13px; font-weight:700; background:#e8e8ed; white-space:nowrap; }}
    .pass {{ color:#1f8f45; background:#e8f5ed; }}
    .fail {{ color:#c21d14; background:#fde8e7; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(160px,1fr)); gap:12px; margin:22px 0; }}
    .card {{ min-width:0; background:#fff; border:1px solid rgba(0,0,0,.08); border-radius:8px; padding:16px; box-shadow:0 10px 28px rgba(0,0,0,.04); }}
    .metric-label {{ color:#6e6e73; font-size:13px; }}
    .metric-value {{ font-size:24px; font-weight:700; margin-top:6px; overflow-wrap:anywhere; }}
    .section {{ margin-top:14px; }}
    .warning {{ border-color:#f1c27d; background:#fff8ec; }}
    .warning p {{ margin-bottom:10px; }}
    .warning ul {{ margin:0; padding-left:20px; color:#6b4a00; }}
    table {{ border-collapse:collapse; width:100%; margin:8px 0 4px; font-size:13px; overflow-wrap:anywhere; }}
    th,td {{ border-bottom:1px solid #e5e5ea; padding:8px 10px; text-align:right; }}
    th:first-child,td:first-child {{ text-align:left; }}
    th {{ color:#6e6e73; background:#fbfbfd; }}
    .table-wrap {{ overflow-x:auto; }}
    .two-col {{ display:grid; grid-template-columns:1.2fr .8fr; gap:12px; }}
    .muted {{ color:#6e6e73; }}
    @media (max-width:900px) {{ main {{ padding:20px 12px 32px; }} header,.two-col {{ display:block; }} .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media (max-width:600px) {{
      h1 {{ font-size:25px; }}
      .pills {{ justify-content:flex-start; margin-top:12px; }}
      .pill {{ white-space:normal; }}
      .grid {{ grid-template-columns:1fr; }}
      .metric-value {{ font-size:22px; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>A 股量化交易系统报告</h1>
      <p>本报告由本地回测和模拟盘系统生成，仅用于研究、模拟交易和人工复核，不构成投资建议。</p>
      <div class="header-links">{deployment_gate_link}{formal_candidate_link}</div>
    </div>
    <div class="pills">
      <span class="pill {practical_class}">实用状态：{escape(practical_level)}</span>
      <span class="pill {strict_class}">严格实盘：{escape(strict_level)}</span>
      <span class="pill {execution_class}">执行：{escape(execution_level)}</span>
      <span class="pill {auto_live_class}">自动实盘：{escape(auto_live_level)}</span>
    </div>
  </header>

  <section class="grid">
    {kpi_card("最新权益", format_number(metrics.get("latest_equity")))}
    {kpi_card("策略收益", format_percent(metrics.get("total_return")))}
    {kpi_card("基准收益", format_percent(metrics.get("benchmark_total_return")))}
    {kpi_card("超额收益", format_percent(metrics.get("excess_total_return")))}
    {kpi_card("\u5386\u53f2\u56de\u6d4b\u53ef\u4fe1\u5ea6", reliability_display(frames["strategy_reliability"]))}
    {kpi_card("\u5f53\u524d\u90e8\u7f72\u53ef\u4fe1\u5ea6", deployment_evidence_display(frames["strategy_health"]))}
    {kpi_card("执行就绪", execution_level)}
    {kpi_card("自动实盘", auto_live_level)}
    {kpi_card("最大回撤", format_percent(metrics.get("max_drawdown")))}
    {kpi_card("基准最大回撤", format_percent(metrics.get("benchmark_max_drawdown")))}
    {kpi_card("Sharpe", format_float(metrics.get("sharpe")))}
    {kpi_card("信息比率", format_float(metrics.get("information_ratio")))}
  </section>

  <section class="card warning section">
    <h2>证据缺口提示</h2>
    <p class="muted">这些提示不是收益预测评分；它们说明当前数据、风控元数据或验证流程是否足以支撑实盘级结论。</p>
    <ul>{warning_html}</ul>
  </section>

  <section class="two-col section">
    <div class="card">
      <h2>策略与大盘净值对比</h2>
      {comparison_svg(frames["comparison"])}
    </div>
    <div class="card">
      <h2>当前策略配置</h2>
      {strategy_config_html(config)}
    </div>
  </section>

  {sections}
</main>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")
    return path

def kpi_card(label: str, value: str) -> str:
    return f'<div class="card"><div class="metric-label">{escape(label)}</div><div class="metric-value">{escape(value)}</div></div>'


def reliability_display(reliability: pd.DataFrame) -> str:
    if reliability.empty or not {"item", "value"}.issubset(reliability.columns):
        return "-"
    values = item_value_map(reliability.rename(columns={"item": "item", "value": "value"}))
    grade = values.get("reliability_grade", "-")
    score = values.get("reliability_score", "")
    if score:
        return f"{grade} / {score}"
    return grade

def deployment_evidence_display(strategy_health: pd.DataFrame) -> str:
    if strategy_health.empty or not {"check", "value"}.issubset(strategy_health.columns):
        return "-"
    values = item_value_map(strategy_health.rename(columns={"check": "item"}))
    grade = values.get("deployment_evidence_grade", "-")
    score = values.get("deployment_evidence_score", "")
    if score:
        return f"{grade} / {score}"
    return grade





def section_html(title: str, frame: pd.DataFrame, empty_text: str) -> str:
    return f'<section class="card section"><h2>{escape(title)}</h2><div class="table-wrap">{html_table(frame, empty_text)}</div></section>'


def html_table(frame: pd.DataFrame, empty_text: str) -> str:
    if frame.empty:
        return f'<p class="muted">{escape(empty_text)}</p>'
    display = frame.copy()
    for column in display.columns:
        display[column] = display[column].map(_report_display_value)
    display.rename(columns=REPORT_COLUMN_LABELS, inplace=True)
    return display.to_html(index=False, escape=True, border=0)


def _report_display_value(value: object) -> object:
    if pd.api.types.is_bool(value):
        return "\u662f" if bool(value) else "\u5426"
    text = str(value)
    if text.startswith("submit_request_integrity_failed:"):
        return "\u539f\u59cb\u63d0\u4ea4\u8bf7\u6c42\u5b8c\u6574\u6027\u6821\u9a8c\u5931\u8d25\uff1a" + text.split(":", 1)[1]
    if text.startswith("order_status_audit_required_checks_missing:"):
        return "\u59d4\u6258\u72b6\u6001\u5ba1\u8ba1\u7f3a\u5c11\u5fc5\u9700\u68c0\u67e5\uff1a" + text.split(":", 1)[1]
    if text.startswith("order_status_audit_failed:"):
        return "\u59d4\u6258\u72b6\u6001\u5ba1\u8ba1\u672a\u901a\u8fc7\uff1a" + text.split(":", 1)[1]
    if text.startswith("cancel_source_query_already_used:"):
        return "\u8be5\u67e5\u8be2\u6279\u6b21\u5df2\u53d1\u8d77\u8fc7\u64a4\u5355\uff0c\u5fc5\u987b\u5148\u91cd\u65b0\u67e5\u8be2\uff1a" + text.split(":", 1)[1]
    return REPORT_VALUE_LABELS.get(text, value)


def strategy_config_html(config: dict[str, Any]) -> str:
    strategy = config.get("strategy", {})
    rows = [
        {"项目": "策略名称", "值": strategy.get("name", "")},
        {"项目": "持仓数量", "值": strategy.get("top_n", "")},
        {"项目": "单股上限", "值": format_percent(strategy.get("max_stock_weight"))},
        {"项目": "行业上限", "值": format_percent(strategy.get("max_industry_weight"))},
        {"项目": "加权方式", "值": strategy.get("weighting", "equal")},
        {"项目": "因子权重", "值": ", ".join(f"{key}:{value}" for key, value in strategy.get("weights", {}).items())},
    ]
    return html_table(pd.DataFrame(rows), "暂无策略配置。")


def calc_metrics(equity: pd.DataFrame) -> dict[str, float]:
    if equity.empty or "equity" not in equity:
        return empty_metrics()
    values = pd.to_numeric(equity["equity"], errors="coerce").dropna()
    if values.empty:
        return empty_metrics()
    running_max = values.cummax()
    drawdown = values / running_max - 1
    returns = values.pct_change().dropna()
    periods = max(len(values) - 1, 1)
    annualized_return = (values.iloc[-1] / values.iloc[0]) ** (252 / periods) - 1 if values.iloc[0] else 0.0
    annualized_volatility = float(returns.std(ddof=0) * (252**0.5)) if not returns.empty else 0.0
    sharpe = annualized_return / annualized_volatility if annualized_volatility else 0.0
    return {
        "latest_equity": float(values.iloc[-1]),
        "total_return": float(values.iloc[-1] / values.iloc[0] - 1) if values.iloc[0] else 0.0,
        "max_drawdown": float(drawdown.min()),
        "annualized_return": float(annualized_return),
        "annualized_volatility": float(annualized_volatility),
        "sharpe": float(sharpe),
        "win_day_ratio": float((returns > 0).mean()) if not returns.empty else 0.0,
        "average_daily_turnover": average_turnover(equity),
        "annualized_turnover": average_turnover(equity) * 252,
    }


def empty_metrics() -> dict[str, float]:
    return {
        "latest_equity": 0.0,
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "annualized_return": 0.0,
        "annualized_volatility": 0.0,
        "sharpe": 0.0,
        "win_day_ratio": 0.0,
        "average_daily_turnover": 0.0,
        "annualized_turnover": 0.0,
    }


def average_turnover(equity: pd.DataFrame) -> float:
    if "turnover" not in equity.columns:
        return 0.0
    values = pd.to_numeric(equity["turnover"], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else 0.0


def metrics_frame(metrics: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame([{"metric": key, "value": value} for key, value in metrics.items()])


def benchmark_comparison(equity: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    if equity.empty or benchmark.empty or "equity" not in equity or "close" not in benchmark:
        return pd.DataFrame(columns=DEFAULT_COLUMNS["benchmark_comparison.csv"])
    strategy = equity[["date", "equity"]].copy()
    strategy["date"] = pd.to_datetime(strategy["date"]).dt.date
    benchmark = benchmark[["date", "close"]].copy()
    benchmark["date"] = pd.to_datetime(benchmark["date"]).dt.date
    merged = strategy.merge(benchmark, how="inner", on="date").sort_values("date")
    if merged.empty:
        return pd.DataFrame(columns=DEFAULT_COLUMNS["benchmark_comparison.csv"])
    merged["strategy_nav"] = merged["equity"] / merged["equity"].iloc[0]
    merged["benchmark_nav"] = merged["close"] / merged["close"].iloc[0]
    merged["strategy_return"] = merged["strategy_nav"].pct_change().fillna(0.0)
    merged["benchmark_return"] = merged["benchmark_nav"].pct_change().fillna(0.0)
    merged["excess_return"] = merged["strategy_return"] - merged["benchmark_return"]
    return merged[DEFAULT_COLUMNS["benchmark_comparison.csv"]].reset_index(drop=True)


def benchmark_metrics(comparison: pd.DataFrame) -> dict[str, float]:
    if comparison.empty:
        return {"benchmark_total_return": 0.0, "benchmark_max_drawdown": 0.0, "excess_total_return": 0.0, "tracking_error": 0.0, "information_ratio": 0.0}
    benchmark_nav = pd.to_numeric(comparison["benchmark_nav"], errors="coerce").dropna()
    strategy_nav = pd.to_numeric(comparison["strategy_nav"], errors="coerce").dropna()
    excess = pd.to_numeric(comparison["excess_return"], errors="coerce").dropna()
    benchmark_drawdown = benchmark_nav / benchmark_nav.cummax() - 1
    tracking_error = float(excess.std(ddof=0) * (252**0.5)) if not excess.empty else 0.0
    annual_excess = float(excess.mean() * 252) if not excess.empty else 0.0
    information_ratio = annual_excess / tracking_error if tracking_error else 0.0
    return {
        "benchmark_total_return": float(benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1) if len(benchmark_nav) else 0.0,
        "benchmark_max_drawdown": float(benchmark_drawdown.min()) if len(benchmark_drawdown) else 0.0,
        "excess_total_return": float(strategy_nav.iloc[-1] - benchmark_nav.iloc[-1]) if len(strategy_nav) and len(benchmark_nav) else 0.0,
        "tracking_error": tracking_error,
        "information_ratio": float(information_ratio),
    }


def comparison_svg(comparison: pd.DataFrame, width: int = 900, height: int = 280) -> str:
    if comparison.empty:
        return '<p class="muted">暂无净值图数据。</p>'
    data = comparison.tail(252).copy()
    strategy = pd.to_numeric(data["strategy_nav"], errors="coerce")
    benchmark = pd.to_numeric(data["benchmark_nav"], errors="coerce")
    values = pd.concat([strategy, benchmark]).dropna()
    if values.empty:
        return '<p class="muted">暂无净值图数据。</p>'
    low = float(values.min())
    high = float(values.max())
    if high == low:
        high = low + 1
    margin = 36

    def path_for(series: pd.Series) -> str:
        points = []
        denominator = max(len(series) - 1, 1)
        for idx, value in enumerate(series):
            if pd.isna(value):
                continue
            x = margin + idx * (width - margin * 2) / denominator
            y = height - margin - (float(value) - low) * (height - margin * 2) / (high - low)
            points.append(f"{x:.1f},{y:.1f}")
        return "M " + " L ".join(points) if points else ""

    return f"""
<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" aria-label="策略与基准净值对比">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" />
  <line x1="{margin}" y1="{height - margin}" x2="{width - margin}" y2="{height - margin}" stroke="#d2d2d7" />
  <line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height - margin}" stroke="#d2d2d7" />
  <path d="{path_for(benchmark.reset_index(drop=True))}" fill="none" stroke="#6e6e73" stroke-width="2" />
  <path d="{path_for(strategy.reset_index(drop=True))}" fill="none" stroke="#007aff" stroke-width="2.5" />
  <text x="{margin}" y="22" font-size="13" fill="#007aff">策略净值</text>
  <text x="{margin + 90}" y="22" font-size="13" fill="#6e6e73">沪深300</text>
  <text x="{margin}" y="{height - 8}" font-size="12" fill="#6e6e73">{escape(str(data['date'].iloc[0]))}</text>
  <text x="{width - margin - 86}" y="{height - 8}" font-size="12" fill="#6e6e73">{escape(str(data['date'].iloc[-1]))}</text>
</svg>
"""


def industry_exposure(
    positions: pd.DataFrame,
    stocks: pd.DataFrame,
    prices: pd.DataFrame | None = None,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if positions.empty or stocks.empty or "market_value" not in positions:
        return pd.DataFrame(columns=DEFAULT_COLUMNS["industry_exposure.csv"])
    stock_metadata = stocks[["symbol", "industry"]].copy()
    stock_metadata["symbol"] = stock_metadata["symbol"].astype(str)
    stock_metadata["industry"] = stock_metadata["industry"].fillna("unknown").astype(str)
    position_data = positions.copy()
    position_data["symbol"] = position_data["symbol"].astype(str)
    merged = position_data.merge(stock_metadata, how="left", on="symbol")
    merged["industry"] = merged["industry"].fillna("unknown")
    grouped = merged.groupby("industry", dropna=False)["market_value"].sum().reset_index()
    total = grouped["market_value"].sum()
    grouped["weight"] = grouped["market_value"] / total if total else 0.0

    strategy = (config or {}).get("strategy", {})
    policy = strategy.get("industry_relative_cap", {}) if isinstance(strategy, dict) else {}
    if prices is None or prices.empty or not {"date", "symbol", "amount"}.issubset(prices.columns):
        grouped["reference_weight"] = pd.NA
        grouped["active_weight"] = pd.NA
        grouped["active_cap"] = pd.NA
        grouped["within_active_cap"] = pd.NA
        return grouped.reindex(columns=DEFAULT_COLUMNS["industry_exposure.csv"]).sort_values(
            "weight", ascending=False
        ).reset_index(drop=True)

    lookback = max(int(strategy.get("lookbacks", {}).get("liquidity", 60)), 1)
    dates = sorted(prices["date"].dropna().unique())
    recent = prices[prices["date"].isin(dates[-lookback:])].copy()
    recent["symbol"] = recent["symbol"].astype(str)
    reference_metadata = stock_metadata
    if config and isinstance(config.get("universe"), dict) and dates:
        asof = pd.to_datetime(dates[-1]).date()
        eligible = eligible_universe(stocks, prices, asof, config)
        eligible_symbols = set(eligible["symbol"].astype(str))
        reference_metadata = stock_metadata[stock_metadata["symbol"].isin(eligible_symbols)]
    reference = (
        recent.groupby("symbol", as_index=False)["amount"].mean()
        .rename(columns={"amount": "avg_amount"})
        .merge(reference_metadata, how="inner", on="symbol")
    )
    reference["industry"] = reference["industry"].fillna("unknown")
    reference_weighting = str(
        policy.get("reference_weighting", "liquidity_sqrt")
        if isinstance(policy, dict)
        else "liquidity_sqrt"
    ).lower()
    if reference_weighting == "equal":
        raw = pd.Series(1.0, index=reference["symbol"], dtype=float)
    else:
        amount = pd.to_numeric(reference["avg_amount"], errors="coerce").fillna(0.0).clip(lower=0.0)
        raw = pd.Series(amount.values ** 0.5, index=reference["symbol"], dtype=float)
    reference_stock_cap = float(
        policy.get("reference_stock_cap", strategy.get("max_stock_weight", 1.0))
        if isinstance(policy, dict)
        else strategy.get("max_stock_weight", 1.0)
    )
    reference_weights = cap_and_normalize(raw, reference_stock_cap)
    reference_industry = reference.set_index("symbol")["industry"].reindex(reference_weights.index)
    reference_grouped = reference_weights.groupby(reference_industry).sum().rename("reference_weight")
    grouped = grouped.merge(reference_grouped, how="outer", left_on="industry", right_index=True)
    grouped[["market_value", "weight", "reference_weight"]] = grouped[
        ["market_value", "weight", "reference_weight"]
    ].fillna(0.0)
    grouped["active_weight"] = grouped["weight"] - grouped["reference_weight"]
    if isinstance(policy, dict) and bool(policy.get("enabled", False)):
        max_industry = float(strategy.get("max_industry_weight", 1.0))
        max_active = float(policy.get("max_active_weight", 0.10))
        grouped["active_cap"] = (grouped["reference_weight"] + max_active).clip(upper=max_industry)
        grouped["within_active_cap"] = (
            grouped["weight"] <= grouped["active_cap"] + 1e-9
        ).astype("boolean")
        unknown = grouped["industry"].fillna("").astype(str).str.casefold().eq("unknown")
        aggregate_unknown = bool(policy.get("aggregate_unknown", True))
        if aggregate_unknown and "max_unknown_weight" in policy:
            grouped.loc[unknown, "active_cap"] = grouped.loc[unknown, "active_cap"].clip(
                upper=float(policy["max_unknown_weight"])
            )
            grouped.loc[unknown, "within_active_cap"] = (
                grouped.loc[unknown, "weight"]
                <= grouped.loc[unknown, "active_cap"].astype(float) + 1e-9
            )
        elif not aggregate_unknown:
            grouped.loc[unknown, "active_cap"] = pd.NA
            grouped.loc[unknown, "within_active_cap"] = pd.NA
    else:
        grouped["active_cap"] = pd.NA
        grouped["within_active_cap"] = pd.NA
    grouped = grouped.reindex(columns=DEFAULT_COLUMNS["industry_exposure.csv"])
    return grouped.sort_values("weight", ascending=False).reset_index(drop=True)


def regenerate_report(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    from .research import refresh_strategy_research_metadata

    refresh_strategy_research_metadata(config)
    equity = read_csv(output_dir / "equity_curve.csv")
    orders = read_csv(output_dir / "orders.csv")
    comparison = read_csv(output_dir / "benchmark_comparison.csv")
    excess_bootstrap = read_csv(output_dir / "excess_bootstrap.csv")
    membership_quality, membership_snapshots = current_index_membership_quality(config)
    atomic_to_csv(
        membership_quality,
        output_dir / "index_membership_quality.csv",
        index=False,
        encoding="utf-8-sig",
    )
    atomic_to_csv(
        membership_snapshots,
        output_dir / "index_membership_snapshots.csv",
        index=False,
        encoding="utf-8-sig",
    )
    data_quality = read_csv(output_dir / "data_quality.csv")
    run_manifest = read_csv(output_dir / "run_manifest.csv")
    data_quality = data_quality_with_current_cache_age(data_quality, run_manifest, config)
    robustness, regime_validation = post_warmup_validation_reports(comparison, config)
    walk_forward_summary = read_csv(output_dir / "walk_forward_summary.csv")
    rolling = rolling_validation_report(comparison)
    metrics = calc_metrics(equity)
    metrics.update(benchmark_metrics(comparison))
    strategy_reliability = strategy_reliability_report(
        comparison,
        robustness,
        rolling,
        walk_forward_summary,
        data_quality,
        config,
        regime_validation,
        excess_bootstrap,
    )
    strategy_research, strategy_candidate_stability = load_strategy_research_evidence(config, output_dir, run_manifest)
    strategy_baselines = read_csv(output_dir / "strategy_baselines.csv")
    live_candidate = candidate_frame(load_candidate_manifest(config))
    shadow_forward_audit = current_shadow_forward_audit(config)
    live_candidate_validation = build_candidate_validation(
        config, comparison, shadow_audit=shadow_forward_audit
    )
    live_candidate_forward_progress = build_candidate_forward_progress(
        config, comparison, shadow_audit=shadow_forward_audit
    )
    cost_stress = read_csv(output_dir / "cost_stress.csv")
    forward_factor_monitor_path = write_forward_factor_monitor(config)
    forward_factor_monitor = read_csv(forward_factor_monitor_path)
    data_source_health = build_data_source_health(config)
    exposure = read_csv(output_dir / "industry_exposure.csv")
    industry_risk_audit = build_industry_risk_audit(exposure, config)
    atomic_to_csv(industry_risk_audit, output_dir / "industry_risk_audit.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(live_candidate, output_dir / "live_candidate.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(live_candidate_validation, output_dir / "live_candidate_validation.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(live_candidate_forward_progress, output_dir / "live_candidate_forward_progress.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(shadow_forward_audit, output_dir / "shadow_forward_audit.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(data_quality, output_dir / "data_quality.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(data_source_health, output_dir / "data_source_health.csv", index=False, encoding="utf-8-sig")
    live_readiness = build_live_readiness(
        config,
        metrics,
        data_quality,
        robustness,
        walk_forward_summary,
        live_candidate_validation,
        shadow_forward_audit,
        cost_stress,
        run_manifest,
        candidate_stability=strategy_candidate_stability,
        excess_bootstrap=excess_bootstrap,
        industry_risk_audit=industry_risk_audit,
        factor_robustness_summary=read_csv(output_dir / "factor_robustness_summary.csv"),
    )
    practical_readiness = build_practical_readiness(
        config,
        metrics,
        data_quality,
        robustness,
        walk_forward_summary,
        cost_stress,
        run_manifest,
        readiness_evidence_issues(config),
        read_csv(output_dir / "capital_sensitivity_audit.csv"),
        industry_risk_audit=industry_risk_audit,
    )
    promotion_decision = configured_promotion_decision(config, output_dir)
    strategy_health = build_strategy_health(
        config,
        strategy_reliability,
        rolling,
        live_candidate_forward_progress,
        shadow_forward_audit,
        promotion_decision,
        practical_readiness,
        live_readiness,
        read_csv(output_dir / "factor_robustness_summary.csv"),
        forward_factor_monitor,
    )
    readiness_roadmap = build_live_readiness_roadmap(live_readiness, config)
    atomic_to_csv(live_readiness, output_dir / "live_readiness.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(robustness, output_dir / "robustness_split.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(rolling, output_dir / "rolling_validation.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(practical_readiness, output_dir / "practical_readiness.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(readiness_roadmap, output_dir / "live_readiness_roadmap.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(strategy_reliability, output_dir / "strategy_reliability.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(strategy_health, output_dir / "strategy_health.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(strategy_candidate_stability, output_dir / "strategy_candidate_stability.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(regime_validation, output_dir / "regime_validation.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(metrics_frame(metrics), output_dir / "performance_metrics.csv", index=False, encoding="utf-8-sig")
    source_diagnostics = order_source_diagnostics(output_dir, orders, config)
    atomic_to_csv(source_diagnostics, output_dir / "order_source_diagnostics.csv", index=False, encoding="utf-8-sig")
    rpa_preflight_path = write_rpa_preflight(config)
    execution_readiness = build_execution_readiness(
        config,
        practical_readiness,
        live_readiness,
        source_diagnostics,
        read_csv(output_dir / "account_reconciliation.csv"),
        read_csv(output_dir / "account_state_audit.csv"),
        read_csv(output_dir / "rpa_pre_trade_checks.csv"),
        read_csv(output_dir / "rpa_orders.csv"),
        orders,
        strategy_decision=promotion_decision,
        strategy_health=strategy_health,
        rpa_preflight=read_csv(rpa_preflight_path),
    )
    execution_roadmap = build_execution_roadmap(execution_readiness)
    execution_modes = build_execution_modes(execution_readiness)
    account_path_decision = build_account_path_decision(config)
    execution_playbook = build_execution_playbook(execution_readiness, account_path_decision)
    account_exclusion_paths = write_account_exclusion_impact(config)
    execution_quality, execution_quality_detail = build_execution_quality(config)
    atomic_to_csv(execution_readiness, output_dir / "execution_readiness.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(execution_roadmap, output_dir / "execution_readiness_roadmap.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(execution_modes, output_dir / "execution_modes.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(execution_playbook, output_dir / "execution_playbook.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(execution_quality, output_dir / "execution_quality.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(execution_quality_detail, output_dir / "execution_quality_detail.csv", index=False, encoding="utf-8-sig")
    account_readiness_plan = build_account_readiness_plan(config)
    atomic_to_csv(account_readiness_plan, output_dir / "account_readiness_plan.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(account_path_decision, output_dir / "account_path_decision.csv", index=False, encoding="utf-8-sig")
    write_auto_live_status(config)
    write_auto_live_window_audit(config)
    write_auto_live_service_lock_audit(config)
    account_refresh_path = output_dir / "auto_live_account_refresh.csv"
    if not account_refresh_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_ACCOUNT_REFRESH_COLUMNS), account_refresh_path, index=False, encoding="utf-8-sig")
    submission_recovery_path = output_dir / "auto_live_submission_recovery.csv"
    if not submission_recovery_path.exists():
        atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_SUBMISSION_RECOVERY_COLUMNS), submission_recovery_path, index=False, encoding="utf-8-sig")
    regen_evidence_plan = build_evidence_plan(config)
    atomic_to_csv(regen_evidence_plan, output_dir / "evidence_plan.csv", index=False, encoding="utf-8-sig")
    regen_observation_schedule = build_observation_schedule(config)
    atomic_to_csv(regen_observation_schedule, output_dir / "observation_schedule.csv", index=False, encoding="utf-8-sig")
    write_observation_task_status(config)
    regen_remediation_path = write_auto_live_remediation_plan(config)
    regen_verification_path = write_auto_live_verification_plan(config)
    return write_html_report(
        output_dir / "report.html",
        equity=equity,
        orders=orders,
        positions=read_csv(output_dir / "positions.csv"),
        trades=read_csv(output_dir / "trades.csv"),
        exposure=exposure,
        industry_risk_audit=industry_risk_audit,
        comparison=comparison,
        excess_bootstrap=read_csv(output_dir / "excess_bootstrap.csv"),
        metrics=metrics,
        data_quality=data_quality,
        index_membership_quality=membership_quality,
        index_membership_snapshots=membership_snapshots,
        survivorship_bias_audit=read_csv(output_dir / "survivorship_bias_audit.csv"),
        survivorship_bias_snapshots=read_csv(output_dir / "survivorship_bias_snapshots.csv"),
        data_source_health=data_source_health,
        robustness=robustness,
        annual=read_csv(output_dir / "annual_performance.csv"),
        rolling=rolling,
        regime_validation=regime_validation,
        strategy_reliability=strategy_reliability,
        strategy_health=strategy_health,
        strategy_candidate_stability=strategy_candidate_stability,
        execution_model_reconciliation=read_csv(output_dir / "execution_model_reconciliation.csv"),
        execution_model_reconciliation_summary=read_csv(output_dir / "execution_model_reconciliation_summary.csv"),
        strategy_promotion=read_csv(output_dir / "strategy_promotion.csv"),
        strategy_candidate_family=read_strategy_research_artifact(config, output_dir, "strategy_candidate_family.csv"),
        strategy_candidate_similarity=read_strategy_research_artifact(config, output_dir, "strategy_candidate_similarity.csv"),
        research_candidate_observation=read_csv(output_dir / "research_candidate_observation.csv"),
        forward_only_candidate_observation=read_csv(output_dir / "forward_only_candidate_observation.csv"),
        forward_candidate_attribution=read_csv(output_dir / "forward_candidate_attribution.csv"),
        forward_candidate_selection=read_csv(output_dir / "forward_candidate_selection.csv"),
        forward_candidate_selection_summary=read_csv(output_dir / "forward_candidate_selection_summary.csv"),
        strategy_family_selection=read_strategy_research_artifact(config, output_dir, "strategy_family_selection.csv"),
        strategy_research_gate=read_csv(output_dir / "strategy_research_gate.csv"),
        strategy_family_selection_splits=read_strategy_research_artifact(config, output_dir, "strategy_family_selection_splits.csv"),
        candidate_failure_history=read_csv(output_dir / "candidate_failure_history.csv"),
        factor_robustness=read_csv(output_dir / "factor_robustness.csv"),
        factor_robustness_summary=read_csv(output_dir / "factor_robustness_summary.csv"),
        risk_exposure=read_csv(output_dir / "risk_exposure.csv"),
        walk_forward=read_csv(output_dir / "walk_forward.csv"),
        walk_forward_summary=walk_forward_summary,
        cost_stress=cost_stress,
        capital_sensitivity=read_csv(output_dir / "capital_sensitivity.csv"),
        capital_sensitivity_audit=read_csv(output_dir / "capital_sensitivity_audit.csv"),
        micro_capital_sensitivity=read_csv(output_dir / "micro_capital_sensitivity.csv"),
        micro_capital_sensitivity_audit=read_csv(output_dir / "micro_capital_sensitivity_audit.csv"),
        rebalance_frequency_research=read_csv(output_dir / "rebalance_frequency_research.csv"),
        rebalance_frequency_research_audit=read_csv(output_dir / "rebalance_frequency_research_audit.csv"),
        run_manifest=run_manifest,
        live_candidate=live_candidate,
        live_candidate_validation=live_candidate_validation,
        live_candidate_forward_progress=live_candidate_forward_progress,
        candidate_activation_audit=read_csv(output_dir / "candidate_activation_audit.csv"),
        candidate_deactivation_audit=read_csv(output_dir / "candidate_deactivation_audit.csv"),
        active_execution_status=read_csv(output_dir / "active_execution_status.csv"),
        live_readiness=live_readiness,
        practical_readiness=practical_readiness,
        forward_factor_monitor=forward_factor_monitor,
        live_readiness_roadmap=readiness_roadmap,
        execution_readiness=execution_readiness,
        execution_readiness_roadmap=execution_roadmap,
        execution_modes=execution_modes,
        execution_playbook=execution_playbook,
        auto_live_readiness=read_csv(output_dir / "auto_live_readiness.csv"),
        auto_live_summary=read_csv(output_dir / "auto_live_summary.csv"),
        auto_live_journal_summary=read_csv(output_dir / "auto_live_journal_summary.csv"),
        auto_live_journal_audit=read_csv(output_dir / "auto_live_journal_audit.csv"),
        auto_live_event_journal=read_csv(output_dir / "auto_live_event_journal.csv"),
        auto_live_journal_errors=read_csv(output_dir / "auto_live_journal_errors.csv"),
        auto_live_account_binding_audit=read_csv(output_dir / "auto_live_account_binding_audit.csv"),
        auto_live_recovery_points=read_csv(output_dir / "auto_live_recovery_points.csv"),
        auto_live_recovery_audit=read_csv(output_dir / "auto_live_recovery_audit.csv"),
        auto_live_alert_audit=read_csv(output_dir / "auto_live_alert_audit.csv"),
        auto_live_alert_outbox=read_csv(output_dir / "auto_live_alert_outbox.csv"),
        auto_live_alert_selftest=read_csv(output_dir / "auto_live_alert_selftest.csv"),
        auto_live_rollout_audit=read_csv(output_dir / "auto_live_rollout_audit.csv"),
        auto_live_rollout_history=read_csv(output_dir / "auto_live_rollout_history.csv"),
        auto_live_service_summary=read_csv(output_dir / "auto_live_service_summary.csv"),
        auto_live_service_history=read_csv(output_dir / "auto_live_service_history.csv"),
        auto_live_service_heartbeat=read_csv(output_dir / "auto_live_service_heartbeat.csv"),
        auto_live_service_lock_audit=read_csv(output_dir / "auto_live_service_lock_audit.csv"),
        auto_live_service_stop_audit=read_csv(output_dir / "auto_live_service_stop_audit.csv"),
        auto_live_batch_summary=read_csv(output_dir / "auto_live_batch_summary.csv"),
        auto_live_batch_plan=read_csv(output_dir / "auto_live_batch_plan.csv"),
        auto_live_batch_orders=read_csv(output_dir / "auto_live_batch_orders.csv"),
        auto_live_current_batch=read_csv(output_dir / "auto_live_current_batch.csv"),
        auto_live_cycle_summary=read_csv(output_dir / "auto_live_cycle_summary.csv"),
        auto_live_cycle=read_csv(output_dir / "auto_live_cycle.csv"),
        auto_live_cycle_history=read_csv(output_dir / "auto_live_cycle_history.csv"),
        auto_live_post_trade_reconciliation=read_csv(output_dir / "auto_live_post_trade_reconciliation.csv"),
        auto_live_post_trade_manifest=read_csv(output_dir / "auto_live_post_trade_manifest.csv"),
        auto_live_post_trade_reconciliation_history=read_csv(output_dir / "auto_live_post_trade_reconciliation_history.csv"),
        auto_live_window_audit=read_csv(output_dir / "auto_live_window_audit.csv"),
        auto_live_submit_lock_audit=read_csv(output_dir / "auto_live_submit_lock_audit.csv"),
        auto_live_risk_audit=read_csv(output_dir / "auto_live_risk_audit.csv"),
        auto_live_risk_reservations=read_csv(output_dir / "auto_live_risk_reservations.csv"),
        auto_live_stop_audit=read_csv(output_dir / "auto_live_stop_audit.csv"),
        auto_live_submission_intent=read_csv(output_dir / "auto_live_submission_intent.csv"),
        auto_live_action_confirmation=read_csv(output_dir / "auto_live_action_confirmation.csv"),
        huatai_canary_audit=read_csv(output_dir / "huatai_desktop_canary_audit.csv"),
        auto_live_blocked_attempt=read_csv(output_dir / "auto_live_blocked_attempt.csv"),
        auto_live_account_refresh=read_csv(output_dir / "auto_live_account_refresh.csv"),
        auto_live_broker_account_snapshot=read_csv(output_dir / "auto_live_broker_account_snapshot.csv"),
        auto_live_broker_positions=read_csv(output_dir / "auto_live_broker_positions.csv"),
        auto_live_broker_account_audit=read_csv(output_dir / "auto_live_broker_account_audit.csv"),
        broker_account_import_preview_audit=read_csv(output_dir / "broker_account_import_preview_audit.csv"),
        broker_account_initialization_audit=read_csv(output_dir / "broker_account_initialization_audit.csv"),
        broker_account_initialization_provenance=read_csv(output_dir / "broker_account_initialization_provenance.csv"),
        broker_capital_feasibility_audit=read_csv(output_dir / "broker_capital_feasibility_audit.csv"),
        broker_capital_feasibility_detail=read_csv(output_dir / "broker_capital_feasibility_detail.csv"),
        broker_canary_capital_audit=read_csv(output_dir / "broker_canary_capital_audit.csv"),
        broker_canary_capital_candidates=read_csv(output_dir / "broker_canary_capital_candidates.csv"),
        broker_capital_transition=read_csv(output_dir / "broker_capital_transition.csv"),
        capital_mode_assessment=read_csv(output_dir / "capital_mode_assessment.csv"),
        broker_order_preview=read_csv(output_dir / "broker_order_preview.csv"),
        broker_order_preview_signal=read_csv(output_dir / "broker_order_preview_signal.csv"),
        broker_order_preview_audit=read_csv(output_dir / "broker_order_preview_audit.csv"),
        auto_live_run=read_csv(output_dir / "auto_live_run.csv"),
        auto_live_order_results=read_csv(output_dir / "auto_live_order_results.csv"),
        auto_live_ack_audit=read_csv(output_dir / "auto_live_ack_audit.csv"),
        auto_live_order_status=read_csv(output_dir / "auto_live_order_status.csv"),
        auto_live_order_status_audit=read_csv(output_dir / "auto_live_order_status_audit.csv"),
        auto_live_submission_recovery=read_csv(output_dir / "auto_live_submission_recovery.csv"),
        auto_live_cancel_results=read_csv(output_dir / "auto_live_cancel_results.csv"),
        auto_live_cancel_audit=read_csv(output_dir / "auto_live_cancel_audit.csv"),
        auto_live_cancel_verification=read_csv(output_dir / "auto_live_cancel_verification.csv"),
        auto_live_cancel_verification_audit=read_csv(output_dir / "auto_live_cancel_verification_audit.csv"),
        auto_live_cancel_lock_audit=read_csv(output_dir / "auto_live_cancel_lock_audit.csv"),
        auto_live_fill_import=read_csv(output_dir / "auto_live_fill_import.csv"),
        auto_live_fill_progress=read_csv(output_dir / "auto_live_fill_progress.csv"),
        auto_live_fill_sync_audit=read_csv(output_dir / "auto_live_fill_sync_audit.csv"),
        auto_live_settlement_audit=read_csv(output_dir / "auto_live_settlement_audit.csv"),
        auto_live_submission_ledger=read_csv(output_dir / "auto_live_submission_ledger.csv"),
        auto_live_adapter_audit=read_csv(output_dir / "auto_live_adapter_audit.csv"),
        auto_live_adapter_selftest=read_csv(output_dir / "auto_live_adapter_selftest.csv"),
        auto_live_adapter_replay_audit=read_csv(output_dir / "auto_live_adapter_replay_audit.csv"),
        auto_live_adapter_file_audit=read_csv(output_dir / "auto_live_adapter_file_audit.csv"),
        auto_live_adapter_replay_summary=read_csv(output_dir / "auto_live_adapter_replay_summary.csv"),
        auto_live_blocker_details=read_csv(output_dir / "auto_live_blocker_details.csv"),
        auto_live_adapter_manifest=read_csv(output_dir / "auto_live_adapter_manifest.csv"),
        evidence_plan=regen_evidence_plan,
        observation_schedule=regen_observation_schedule,
        daily_observation_summary=read_csv(output_dir / "daily_observation_summary.csv"),
        daily_observation_audit=read_csv(output_dir / "daily_observation_audit.csv"),
        daily_observation_progress=read_csv(output_dir / "daily_observation_progress.csv"),
        daily_observation_progress_audit=read_csv(output_dir / "daily_observation_progress_audit.csv"),
        auto_live_remediation_plan=read_csv(regen_remediation_path),
        auto_live_verification_plan=read_csv(regen_verification_path),
        account_readiness_plan=account_readiness_plan,
        account_path_decision=account_path_decision,
        account_exclusion_impact=read_csv(account_exclusion_paths["account_exclusion_impact.csv"]),
        account_exclusion_impact_detail=read_csv(account_exclusion_paths["account_exclusion_impact_detail.csv"]),
        shadow_forward_audit=shadow_forward_audit,
        order_source_diagnostics=source_diagnostics,
        account_reconciliation=read_csv(output_dir / "account_reconciliation.csv"),
        account_reset_audit=read_csv(output_dir / "account_reset_audit.csv"),
        account_import_audit=read_csv(output_dir / "account_import_audit.csv"),
        account_import_positions=read_csv(output_dir / "account_import_positions.csv"),
        account_import_snapshot=read_csv(output_dir / "account_import_snapshot.csv"),
        account_import_check_audit=read_csv(output_dir / "account_import_check_audit.csv"),
        account_import_check_positions=read_csv(output_dir / "account_import_check_positions.csv"),
        account_import_check_snapshot=read_csv(output_dir / "account_import_check_snapshot.csv"),
        account_import_from_state_template=read_csv(output_dir / "account_import_from_state_template.csv"),
        account_import_priced_only_template=read_csv(output_dir / "account_import_priced_only_template.csv"),
        account_repair_review=read_csv(output_dir / "account_repair_review.csv"),
        account_repair_excluded_positions=read_csv(output_dir / "account_repair_excluded_positions.csv"),
        account_price_overrides=read_csv(output_dir / "account_price_overrides.csv"),
        account_price_overrides_template=read_csv(output_dir / "account_price_overrides_template.csv"),
        account_price_override_audit=read_csv(output_dir / "account_price_override_audit.csv"),
        account_price_override_effective=read_csv(output_dir / "account_price_override_effective.csv"),
        account_state_audit=read_csv(output_dir / "account_state_audit.csv"),
        account_state_snapshot=read_csv(output_dir / "account_state_snapshot.csv"),
        account_state_positions=read_csv(output_dir / "account_state_positions.csv"),
        account_state_missing_prices=read_csv(output_dir / "account_state_missing_prices.csv"),
        fill_import_audit=read_csv(output_dir / "fill_import_audit.csv"),
        fill_import_trades=read_csv(output_dir / "fill_import_trades.csv"),
        fill_order_match=read_csv(output_dir / "fill_order_match.csv"),
        execution_quality=execution_quality,
        execution_quality_detail=execution_quality_detail,
        fill_import_positions=read_csv(output_dir / "fill_import_positions.csv"),
        fill_import_snapshot=read_csv(output_dir / "fill_import_snapshot.csv"),
        rpa_orders=read_csv(output_dir / "rpa_orders.csv"),
        rpa_orders_all=read_csv(output_dir / "rpa_orders_all.csv"),
        rpa_blocked_orders=read_csv(output_dir / "rpa_blocked_orders.csv"),
        rpa_pre_trade_checks=read_csv(output_dir / "rpa_pre_trade_checks.csv"),
        rpa_preflight=read_csv(output_dir / "rpa_preflight.csv"),
        order_generation_status=read_csv(output_dir / "order_generation_status.csv"),
        order_artifact_invalidation=read_csv(output_dir / "order_artifact_invalidation.csv"),
        order_liquidity_audit=read_csv(output_dir / "order_liquidity_audit.csv"),
        order_liquidity_all_audit=read_csv(output_dir / "order_liquidity_all_audit.csv"),
        strategy_research=strategy_research,
        strategy_baselines=strategy_baselines,
        backtest_compare=read_csv(output_dir / "strategy_backtest_compare.csv"),
        config=config,
    )


def read_strategy_research_artifact(
    config: dict[str, Any],
    output_dir: Path,
    filename: str,
) -> pd.DataFrame:
    configured = str(config.get("validation", {}).get("strategy_research_path", "")).strip()
    if configured:
        source = project_path(configured).parent / filename
        if source.exists():
            return read_csv(source)
    return read_csv(output_dir / filename)
def load_strategy_research_evidence(
    config: dict[str, Any],
    output_dir: Path,
    current_manifest: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    configured = str(config.get("validation", {}).get("strategy_research_path", "")).strip()
    research_path = project_path(configured) if configured else output_dir / "strategy_research.csv"
    research = read_csv(research_path)
    stability = strategy_candidate_stability_report(research, config)
    source_manifest = read_csv(research_path.parent / "strategy_research_manifest.csv")

    source_exists = research_path.exists() and not research.empty
    manifest_exists = not source_manifest.empty
    data_current = manifests_share_market_data(source_manifest, current_manifest)
    expected_hash = manifest_item(source_manifest, "strategy_research_sha256")
    actual_hash = frame_sha256(research) if not research.empty else ""
    artifact_current = bool(expected_hash and actual_hash == expected_hash)

    provenance = pd.DataFrame(
        [
            {
                "item": "research_source_exists",
                "value": str(research_path),
                "threshold": "non-empty strategy_research.csv",
                "passed": source_exists,
                "severity": "ok" if source_exists else "warning",
                "note": "参数邻域稳定性必须来自明确、非空的候选研究文件。",
            },
            {
                "item": "research_source_manifest_exists",
                "value": str(research_path.parent / "strategy_research_manifest.csv"),
                "threshold": "present",
                "passed": manifest_exists,
                "severity": "ok" if manifest_exists else "warning",
                "note": "候选研究必须带有独立数据与产物指纹清单。",
            },
            {
                "item": "research_source_data_current",
                "value": data_current,
                "threshold": "current market data hashes",
                "passed": data_current,
                "severity": "ok" if data_current else "warning",
                "note": "候选研究所用股票、行情、基本面和基准必须与当前回测缓存一致。",
            },
            {
                "item": "research_artifact_hash_match",
                "value": actual_hash[:16] if actual_hash else "missing",
                "threshold": expected_hash[:16] if expected_hash else "manifest hash missing",
                "passed": artifact_current,
                "severity": "ok" if artifact_current else "warning",
                "note": "strategy_research.csv 必须与研究清单中的产物指纹一致，防止结果被替换或手工修改。",
            },
        ]
    )
    return research, pd.concat([stability, provenance], ignore_index=True)


def manifests_share_market_data(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    if left.empty or right.empty:
        return False
    for name in ["stocks", "prices", "fundamentals", "benchmark"]:
        left_hash = manifest_item(left, f"{name}_sha256")
        right_hash = manifest_item(right, f"{name}_sha256")
        if not left_hash or left_hash != right_hash:
            return False
    return True


def manifest_item(manifest: pd.DataFrame, item: str) -> str:
    if manifest.empty or not {"item", "value"}.issubset(manifest.columns):
        return ""
    row = manifest[manifest["item"].astype(str) == item]
    if row.empty:
        return ""
    value = row["value"].iloc[0]
    return "" if pd.isna(value) else str(value)

def read_csv(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path)
        except EmptyDataError:
            return pd.DataFrame()
    return pd.DataFrame()


def format_percent(value: object) -> str:
    if value in {None, ""}:
        return "-"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return str(value)


def format_float(value: object) -> str:
    if value in {None, ""}:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def format_number(value: object) -> str:
    if value in {None, ""}:
        return "-"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)
