from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
import subprocess
import shutil
import sys
import time
from datetime import datetime
from copy import deepcopy
from pathlib import Path
from typing import Iterable

import pandas as pd
from pandas.errors import EmptyDataError

from .audit import backtest_config_sha256, mapping_sha256
from .autolive import AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS, AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS, AUTO_LIVE_BROKER_POSITION_COLUMNS, auto_live_config_sha256, prepare_auto_live_action_confirmation, query_auto_live_orders, run_auto_live, tickets_sha256, write_auto_live_adapter_selftest, write_auto_live_status, write_auto_live_submit_lock_audit
from .autolive_window import write_auto_live_window_audit
from .autolive_risk import activate_auto_live_stop, clear_auto_live_stop, write_auto_live_risk_audit
from .autolive_fills import settle_auto_live_fills, write_auto_live_fill_sync
from .autolive_cycle import run_auto_live_cycle
from .autolive_batch import write_auto_live_batch_plan
from .broker_readonly_history import write_broker_readonly_history
from .autolive_replay import write_auto_live_adapter_replay_audit
from .autolive_integrity import write_command_adapter_file_audit
from .autolive_journal import write_auto_live_journal_audit
from .autolive_binding import bind_auto_live_account, unbind_auto_live_account, write_auto_live_account_binding_audit
from .autolive_recovery import create_auto_live_recovery_point, write_auto_live_recovery_audit
from .autolive_alerts import retry_auto_live_alerts, send_auto_live_alert, write_auto_live_alert_audit, write_auto_live_alert_selftest
from .autolive_rollout import approve_auto_live_rollout, revoke_auto_live_rollout, write_auto_live_rollout_audit
from .autolive_service import (
    activate_auto_live_service_stop,
    clear_auto_live_service_stop,
    run_auto_live_service,
    write_auto_live_service_lock_audit,
)
from .autolive_account import query_auto_live_account
from .autolive_cancel import cancel_auto_live_orders, cancel_verification_needed, clear_auto_live_cancel_lock, verify_auto_live_cancellations, write_auto_live_cancel_lock_audit
from .account import (
    check_account_import_file,
    check_account_price_overrides,
    export_account_state,
    import_account_state,
    reset_account_state,
    write_account_import_template,
    write_account_price_override_template,
    write_account_repair_template,
)
from .account_impact import write_account_exclusion_impact
from .account_plan import build_account_path_decision, write_account_path_decision, write_account_readiness_plan
from .backtest import run_backtest
from .capital_sensitivity import capital_sensitivity_needs_refresh, run_capital_sensitivity
from .canary_capital import write_broker_canary_capital_feasibility
from .broker_initialization import initialize_broker_account_from_snapshot
from .broker_preview import (
    check_passed,
    write_broker_account_import_preview,
    write_broker_capital_feasibility,
    write_broker_order_preview,
)
from .candidate import build_candidate_forward_progress, build_candidate_validation, candidate_frame, candidate_freeze_boundary_evidence, candidate_benchmark_mapping_evidence, candidate_frozen_input_evidence, candidate_index_membership_evidence, candidate_shadow_observation, forward_risk_evaluation_evidence, forward_risk_evaluation_identity, candidate_validation_path, freeze_live_candidate, live_strategy_hash, load_candidate_manifest, matching_candidate_id, preregister_forward_candidate, record_candidate_failure, seal_candidate_frozen_inputs
from .challengers import primary_challenger_config_paths
from .compare import compare_backtest_outputs, write_strategy_promotion_decision
from .config import load_config
from .config import project_path
from .dashboard import generate_control_panel
from .data import DataStore, akshare_network_context, fetch_akshare, fetch_benchmark_akshare, fetch_stock_spot, finalized_market_data_end, make_sample_data, merge_benchmark_cache, normalize_benchmark, normalize_trade_calendar, recover_partial_prices, refresh_cached_metadata, required_fetch_symbols
from .deployment_gate import forward_candidate_cache_isolation, write_deployment_gate
from .diagnostics import data_quality_report
from .execution import build_execution_modes, build_execution_playbook, build_execution_readiness, build_execution_roadmap, execution_readiness_level, failed_readiness_rows, rows_with_severity
from .execution_artifacts import invalidate_execution_artifacts
from .execution_quality import write_execution_quality
from .evidence import write_evidence_plan
from .factor_robustness import run_factor_robustness
from .forward_selection import write_forward_candidate_selection
from .fills import import_fills, write_fill_import_template
from .huatai_certification import arm_huatai_desktop_canary, write_huatai_canary_arming_audit, write_huatai_execution_sandbox_summary
from .huatai_client import request_huatai_client_window_wake, write_huatai_client_status
from .history_validation import run_extended_history_validation
from .io_utils import atomic_to_csv
from .live_evidence import sync_live_strategy_evidence
from .local_operator import _formal_candidate_observation_status, strategy_operating_mode, write_local_operator_status
from .index_membership import refresh_benchmark_membership
from .observation_plan import write_observation_schedule
from .observation_task import write_observation_task_status
from .observation_progress import capture_observation_snapshot, observation_progress_blockers, write_observation_progress
from .order_audit import write_order_liquidity_audit
from .observation_alerts import write_observation_alert_status
from .paper import run_paper, run_shadow_signals
from .readiness import (
    build_live_readiness_from_outputs,
    build_live_readiness_roadmap,
    build_practical_readiness_from_outputs,
    cost_stress_values,
    readiness_evidence_issues,
    shadow_observation_values,
)
from .reconciliation import reconcile_outputs
from .research import run_strategy_baselines, run_strategy_research
from .rebalance_frequency import write_rebalance_frequency_research
from .selection_bias import write_strategy_selection_bias
from .selected_candidate_refresh import refresh_selected_candidate_evidence
from .report import order_source_diagnostics, regenerate_report
from .remediation import write_auto_live_remediation_plan, write_auto_live_verification_plan
from .rpa import export_rpa_orders, fill_rpa_ticket, preview_rpa_orders, write_rpa_preflight
from .shadow import SHADOW_ATTRIBUTION_COLUMNS, run_shadow_audit, run_shadow_component_attribution
from .shadow_policy import shadow_evaluation_evidence, shadow_evaluation_identity
from .source_health import build_data_source_health, health_failed_rows, write_data_source_health
from .strategy_health import strategy_health_allows_paper, write_strategy_health
from .stress import run_cost_stress
from .trading_day_status import write_trading_day_status
from .validation import dependency_status, optional_dependency_status, validate_config, validate_market_data
from .validation import complete_market_dates, filter_complete_market_data
from .strategy_activation import (
    activate_forward_candidate,
    deactivate_execution_candidate,
    require_active_execution_consistency,
    write_active_execution_status,
    write_candidate_activation_audit,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="aq", description="A-share daily multi-factor quant system")
    parser.add_argument("--config", help="Path to JSON-compatible YAML config")
    sub = parser.add_subparsers(dest="command", required=True)

    calendar_status = sub.add_parser("calendar-status", help="Audit whether a date is an exchange trading session")
    calendar_status.add_argument("--date", help="Target date in YYYY-MM-DD; defaults to local today")
    calendar_status.add_argument("--refresh-missing", action="store_true", help="Refresh only the trade calendar when the target date is not cached")
    calendar_status.add_argument("--strict-exit-code", action="store_true", help="Exit 3 for closed and 4 for unknown; intended for the local scheduler")
    fetch = sub.add_parser("fetch", help="Fetch and cache market data")
    fetch.add_argument("--sample", action="store_true", help="Generate deterministic sample data")
    fetch.add_argument("--benchmark-only", action="store_true", help="Only refresh benchmark index data")
    fetch.add_argument("--max-symbols", type=int, help="Override data.max_symbols for this fetch")
    fetch.add_argument("--no-metadata", action="store_true", help="Skip per-symbol industry/listing-date enrichment")
    fetch.add_argument("--cached-universe", action="store_true", help="Skip stock spot source and refresh using cached universe symbols")
    fetch.add_argument("--full-history", action="store_true", help="Fetch from backtest.start instead of incremental overlap")
    fetch.add_argument("--history-budget", type=int, help="Maximum unfinished symbols to fetch in this resumable history-backfill run")

    sub.add_parser("fetch-account-prices", help="Refresh only current account-position prices from cached universe, then rerun account-state")

    metadata = sub.add_parser("metadata", help="Refresh industry and listing-date metadata for cached stocks")
    metadata.add_argument("--max-symbols", type=int, help="Maximum cached symbols to enrich, ranked by recent liquidity")
    metadata.add_argument("--workers", type=int, help="Concurrent metadata workers; lower this when the data source throttles")

    membership = sub.add_parser("index-membership", help="Refresh point-in-time CSI 300 membership from BaoStock")
    membership.add_argument("--start", help="First membership snapshot date; defaults to backtest.start")
    membership.add_argument("--end", help="Last membership snapshot date; defaults to latest cached benchmark date")
    membership.add_argument("--refresh-all", action="store_true", help="Refetch all requested monthly snapshots")

    sub.add_parser("backtest", help="Run daily multi-factor backtest")
    sub.add_parser("history-validate", help="Validate a completed research-only historical backfill with fixed strategy parameters")
    sub.add_parser("capital-sensitivity", help="Rerun isolated lot-aware backtests across configured capital levels")

    paper = sub.add_parser("paper", help="Generate paper-trading orders from latest cached data")
    paper.add_argument("--date", help="Signal date, defaults to latest cached trading date")
    paper.add_argument("--allow-stale", action="store_true", help="Allow generating historical paper orders from stale cached data")
    paper.add_argument("--allow-weak-strategy", action="store_true", help="Bypass live readiness guard for research drills")
    paper.add_argument("--practical", action="store_true", help="Use practical readiness guard instead of strict live guard")

    shadow = sub.add_parser("shadow", help="Record non-executable shadow signals without updating account state")
    shadow.add_argument("--date", help="Signal date, defaults to latest cached trading date")
    shadow.add_argument("--allow-stale", action="store_true", help="Allow historical shadow signal recording from stale cached data")
    sub.add_parser("shadow-audit", help="Evaluate archived shadow signals against subsequent cached market data")

    observe = sub.add_parser("observe", help="Run safe daily observation workflow: optional refresh, backtest, shadow, audit, report")
    observe.add_argument("--refresh", action="store_true", help="Refresh market data before observation")
    observe.add_argument("--max-symbols", type=int, help="Override data.max_symbols when --refresh is used")
    observe.add_argument("--cached-universe", action="store_true", help="Use cached universe when --refresh is used")
    observe.add_argument("--no-metadata", action="store_true", help="Skip metadata enrichment when --refresh is used")
    observe.add_argument("--allow-stale", action="store_true", help="Allow shadow observation on stale cached data")
    observe.add_argument("--skip-stress", action="store_true", help="Skip automatic cost-stress refresh even when live guard requires it")
    observe.add_argument("--quiet", action="store_true", help="Print only key observation outputs")

    observe_candidates = sub.add_parser("observe-candidates", help="Run observation workflow for default and configured candidate strategies")
    observe_candidates.add_argument("--refresh", action="store_true", help="Refresh market data once before observing all strategies")
    observe_candidates.add_argument("--max-symbols", type=int, help="Override data.max_symbols when --refresh is used")
    observe_candidates.add_argument("--cached-universe", action="store_true", help="Use cached universe when --refresh is used")
    observe_candidates.add_argument("--no-metadata", action="store_true", help="Skip metadata enrichment when --refresh is used")
    observe_candidates.add_argument("--allow-stale", action="store_true", help="Allow shadow observation on stale cached data")
    observe_candidates.add_argument("--skip-stress", action="store_true", help="Skip automatic cost-stress refresh for all observed strategies")
    observe_candidates.add_argument("--quiet", action="store_true", help="Print only key observation outputs")
    observe_candidates.add_argument("--candidate-config", action="append", default=[], help="Additional strategy config to observe; defaults to adaptive_trend, robust_blend, adaptive_robust, and adaptive_redundant when omitted")

    forward_daily = sub.add_parser("forward-daily", help="Refresh only preregistered forward candidates and update immutable shadow evidence")
    forward_daily.add_argument("--refresh", action="store_true", help="Refresh each forward candidate's dedicated market universe")
    forward_daily.add_argument("--allow-stale", action="store_true", help="Allow observation from stale cached data")
    forward_daily.add_argument("--quiet", action="store_true", help="Print only key forward observation outputs")
    forward_daily.add_argument("--live-config", default="configs/huatai_desktop_canary.yaml", help="Read-only broker evidence configuration")

    observe_challengers = sub.add_parser("observe-challengers", help="Refresh only the pre-registered challenger set without paper orders")
    observe_challengers.add_argument("--allow-stale", action="store_true", help="Allow shadow observation on stale cached data")
    observe_challengers.add_argument("--skip-stress", action="store_true", help="Skip automatic cost-stress refresh")
    observe_challengers.add_argument("--quiet", action="store_true", help="Print only key observation outputs")

    daily = sub.add_parser("daily", help="Run daily paper workflow: optional refresh, paper, export orders, preview RPA tickets")
    daily.add_argument("--refresh", action="store_true", help="Refresh market data before generating orders")
    daily.add_argument("--max-symbols", type=int, help="Override data.max_symbols when --refresh is used")
    daily.add_argument("--no-metadata", action="store_true", help="Skip metadata enrichment when --refresh is used")
    daily.add_argument("--cached-universe", action="store_true", help="Use cached universe when --refresh is used")
    daily.add_argument("--allow-stale", action="store_true", help="Allow workflow to run on stale cached data")
    daily.add_argument("--allow-weak-strategy", action="store_true", help="Bypass live readiness guard for research drills")
    daily.add_argument("--practical", action="store_true", help="Use practical readiness guard instead of strict live guard")
    daily.add_argument("--skip-stress", action="store_true", help="Skip automatic cost-stress refresh even when live guard requires it")

    drill = sub.add_parser("execution-drill", help="Run an isolated end-to-end paper/RPA execution drill without touching the main account state")
    drill.add_argument("--output-dir", help="Drill output directory; defaults to <output_dir>/execution_drill")
    drill.add_argument("--reset-cash", type=float, help="Cash amount for the isolated drill account; defaults to backtest.initial_cash")
    drill.add_argument("--allow-stale", action="store_true", help="Allow the drill to use stale cached market data for historical rehearsal")
    drill.add_argument("--skip-backtest", action="store_true", help="Reuse existing drill backtest outputs")
    drill.add_argument("--skip-stress", action="store_true", help="Reuse existing drill cost stress outputs")

    practical = sub.add_parser("practical", help="Run one practical daily workflow: refresh evidence if needed, paper, export orders, report")
    practical.add_argument("--refresh", action="store_true", help="Refresh market data before generating orders")
    practical.add_argument("--max-symbols", type=int, help="Override data.max_symbols when --refresh is used")
    practical.add_argument("--no-metadata", action="store_true", help="Skip metadata enrichment when --refresh is used")
    practical.add_argument("--cached-universe", action="store_true", help="Use cached universe when --refresh is used")
    practical.add_argument("--allow-stale", action="store_true", help="Allow workflow to run on stale cached data")
    practical.add_argument("--skip-stress", action="store_true", help="Skip automatic practical cost-stress refresh")
    practical.add_argument("--reset-account", action="store_true", help="Reset local paper account to cash-only state before generating orders")
    practical.add_argument("--reset-cash", type=float, help="Cash amount for --reset-account; defaults to backtest.initial_cash")
    practical.add_argument("--confirm-account-reset", action="store_true", help="Confirm clearing local paper positions, orders, trades, snapshots, and executable artifacts")

    workflow = sub.add_parser("workflow", help="Run research, backtest, stress, compare, and report in sequence")
    workflow.add_argument("--skip-stress", action="store_true", help="Skip cost stress tests for a faster workflow")

    freeze = sub.add_parser("freeze-candidate", help="Freeze current live strategy parameters for forward validation")
    freeze.add_argument("--force", action="store_true", help="Replace an existing frozen candidate")
    preregister = sub.add_parser("preregister-forward-candidate", help="Freeze a research-only candidate for forward shadow observation without historical promotion evidence")
    preregister.add_argument("--force", action="store_true", help="Replace an existing forward-only preregistration")
    sub.add_parser("candidate-audit", help="Refresh frozen-candidate forward validation from current backtest outputs")
    sub.add_parser("seal-candidate-inputs", help="Bind a preregistered candidate to immutable frozen market inputs")
    activation_audit = sub.add_parser("candidate-activation-audit", help="Audit whether a validated frozen candidate can become the default execution strategy")
    activation_audit.add_argument("--candidate-config", default="configs/hs300_point_in_time_core.yaml", help="Registered forward-only candidate configuration")
    activate_candidate = sub.add_parser("activate-candidate", help="Atomically select a validated candidate for default execution without enabling broker submission")
    activate_candidate.add_argument("--candidate-config", default="configs/hs300_point_in_time_core.yaml", help="Registered forward-only candidate configuration")
    activate_candidate.add_argument("--candidate-id", required=True, help="Exact frozen candidate ID shown by the activation audit")
    activate_candidate.add_argument("--confirm", action="store_true", help="Write the activation record after every current validation check passes")
    deactivate_candidate = sub.add_parser("deactivate-candidate", help="Audit and archive the active execution candidate before restoring the default strategy")
    deactivate_candidate.add_argument("--candidate-id", required=True, help="Exact active candidate ID loaded by the default execution configuration")
    deactivate_candidate.add_argument("--confirm", action="store_true", help="Archive and remove the active execution record after every audit check passes")
    sub.add_parser("execution-strategy-status", help="Write the default execution strategy identity audit without generating orders")

    one_click = sub.add_parser("run", help="Run the safe one-click daily workflow without broker submission")
    one_click.add_argument("--refresh", action="store_true", help="Refresh market data before running the workflow")
    one_click.add_argument("--max-symbols", type=int, help="Override data.max_symbols when --refresh is used")
    one_click.add_argument("--no-metadata", action="store_true", help="Skip metadata enrichment when --refresh is used")
    one_click.add_argument("--cached-universe", action="store_true", help="Use cached universe when --refresh is used")
    one_click.add_argument("--allow-stale", action="store_true", help="Allow workflow to run on stale cached data")
    one_click.add_argument("--skip-stress", action="store_true", help="Skip automatic practical cost-stress refresh")
    one_click.add_argument("--auto-live-dry-run", action="store_true", help="Write auto-live dry-run audit if gates pass; never submits broker orders")
    one_click.add_argument("--skip-shadow", action="store_true", help="Skip lightweight shadow signal recording during one-click run")

    local_run = sub.add_parser("local-run", help="Run strategy plus Huatai read-only audit locally without broker submission")
    local_run.add_argument("--live-config", default="configs/huatai_desktop_canary.yaml", help="Huatai read-only/Canary audit configuration")
    local_run.add_argument("--refresh", action="store_true", help="Refresh market data before running the strategy workflow")
    local_run.add_argument("--max-symbols", type=int, help="Override data.max_symbols when --refresh is used")
    local_run.add_argument("--no-metadata", action="store_true", help="Skip metadata enrichment when --refresh is used")
    local_run.add_argument("--cached-universe", action="store_true", help="Use cached universe when --refresh is used")
    local_run.add_argument("--allow-stale", action="store_true", help="Allow observation on stale data; execution gates remain enforced")
    local_run.add_argument("--skip-stress", action="store_true", help="Skip automatic practical cost-stress refresh")
    local_run.add_argument("--skip-shadow", action="store_true", help="Skip lightweight shadow signal recording")
    local_run.add_argument("--skip-broker", action="store_true", help="Skip Huatai desktop refresh and reuse existing read-only audits")
    local_run.add_argument("--broker-optional", action="store_true", help="Finish strategy workflow when Huatai is offline; other broker audit errors still fail")

    local_status = sub.add_parser("local-status", help="Regenerate local operator status from existing evidence without network or broker actions")
    local_status.add_argument("--live-config", default="configs/huatai_desktop_canary.yaml", help="Huatai read-only/Canary audit configuration")
    deployment_gate = sub.add_parser("deployment-gate", help="Build a read-only strategy, broker, and safety gate for Canary review")
    deployment_gate.add_argument("--live-config", default="configs/huatai_desktop_canary.yaml", help="Read-only broker evidence configuration")
    observation_alert = sub.add_parser("observation-alert", help="Send a deduplicated non-trading alert from daily observation evidence")
    observation_alert.add_argument("--strategy-output", default="outputs", help="Strategy output directory containing observation and health evidence")

    sub.add_parser("broker-canary-capital", help="Audit execution-only one-lot Canary affordability; never creates orders")

    broker_refresh = sub.add_parser("broker-refresh", help="Retry only the Huatai read-only account refresh; never submits orders")
    broker_refresh.add_argument("--live-config", default="configs/huatai_desktop_canary.yaml", help="Huatai read-only/Canary audit configuration")
    broker_init = sub.add_parser("broker-account-init", help="Audit or explicitly initialize an absent live SQLite ledger from the current broker snapshot")
    broker_init.add_argument("--snapshot-run-id", required=True, help="Exact current snapshot_run_id shown in the broker account report")
    broker_init.add_argument("--account-id", required=True, help="Exact stable masked account_id shown in the broker account report")
    broker_init.add_argument("--confirm", action="store_true", help="Atomically commit the verified one-time initialization; without this flag only audits")

    live_sync = sub.add_parser("live-evidence-sync", help="Publish verified read-only strategy evidence into the live audit workspace")
    live_sync.add_argument("--live-config", default="configs/huatai_desktop_canary.yaml", help="Live audit configuration receiving verified strategy evidence")

    sub.add_parser("report", help="Regenerate HTML report from existing CSV outputs")
    sub.add_parser("dashboard", help="Generate clean local HTML control panel")
    sub.add_parser("roadmap", help="Write live-readiness roadmap from current outputs")
    sub.add_parser("ready", help="Write practical readiness checks from current outputs")
    sub.add_parser("execution-ready", help="Write execution readiness checks from current outputs")
    sub.add_parser("auto-live-check", help="Write guarded automatic-live readiness checks from current outputs")
    sub.add_parser("auto-live-adapter-selftest", help="Run non-order self-test for the configured command auto-live adapter")
    sub.add_parser("auto-live-adapter-files", help="Audit pinned SHA-256 hashes for command adapter scripts and configuration")
    sub.add_parser("auto-live-journal", help="Verify the append-only auto-live event hash chain")
    adapter_replay = sub.add_parser("auto-live-adapter-replay", help="Offline-audit saved command adapter request/result lifecycle JSON without broker calls")
    adapter_replay.add_argument("--dir", dest="replay_dir", help="Directory containing saved request/result JSON; defaults to output_dir")
    sub.add_parser("auto-live-account", help="Read and audit broker account snapshot through the configured auto-live adapter")
    sub.add_parser("auto-live-account-binding", help="Audit the signed local-state to broker-account binding")
    sub.add_parser("auto-live-recovery-audit", help="Verify the latest pre-submit recovery point")
    sub.add_parser("auto-live-backup", help="Create and verify an auto-live recovery point without broker submission")
    sub.add_parser("auto-live-alert-audit", help="Audit external alert-channel readiness and pending outbox")
    sub.add_parser("auto-live-alert-selftest", help="Run signed non-trading alert channel self-test")
    sub.add_parser("auto-live-alert-test", help="Send an explicit non-trading test alert")
    sub.add_parser("auto-live-alert-retry", help="Retry pending auto-live alerts")
    sub.add_parser("auto-live-rollout-audit", help="Audit signed staged rollout authorization and current ticket limits")
    rollout_approve = sub.add_parser("auto-live-rollout-approve", help="Approve the configured canary/limited/full stage")
    rollout_approve.add_argument("--stage", choices=["canary", "limited", "full"], required=True)
    rollout_approve.add_argument("--confirm", action="store_true")
    rollout_approve.add_argument("--reason", default="operator_approved")
    rollout_revoke = sub.add_parser("auto-live-rollout-revoke", help="Revoke current staged rollout authorization")
    rollout_revoke.add_argument("--confirm", action="store_true")
    rollout_revoke.add_argument("--reason", default="operator_revoked")
    bind_account = sub.add_parser("auto-live-bind-account", help="Bind this local state database to the reconciled live broker account")
    bind_account.add_argument("--confirm", action="store_true", help="Explicitly confirm live broker account binding")
    unbind_account = sub.add_parser("auto-live-unbind-account", help="Remove the live broker account binding")
    unbind_account.add_argument("--confirm", action="store_true", help="Explicitly confirm account unbinding")
    sub.add_parser("auto-live-window", help="Audit current trading window and RPA ticket freshness before auto-live submit")
    sub.add_parser("auto-live-lock", help="Audit auto-live submit lock age and crash-recovery evidence without deleting it")
    sub.add_parser("auto-live-risk", help="Audit independent auto-live order, cash, daily-capacity, and emergency-stop limits")
    sub.add_parser("auto-live-batch", help="Build an audited sequential batch plan without submitting broker orders")
    auto_live_stop = sub.add_parser("auto-live-stop", help="Activate the local emergency stop before any further broker submission")
    auto_live_stop.add_argument("--reason", default="manual_emergency_stop", help="Operator-visible emergency-stop reason")
    auto_live_resume = sub.add_parser("auto-live-resume", help="Clear the local emergency stop after explicit operator review")
    auto_live_resume.add_argument("--confirm", action="store_true", help="Confirm emergency-stop clearance; normal gates still apply")
    sub.add_parser("huatai-canary-audit", help="Audit Huatai desktop evidence for a short-lived first Canary arm")
    sub.add_parser("huatai-canary-sandbox", help="Rebuild Huatai Canary sandbox evidence from current read-only artifacts")
    huatai_canary_arm = sub.add_parser("huatai-canary-arm", help="Issue an HMAC-signed short-lived Huatai Canary arm")
    huatai_canary_arm.add_argument("--confirm", action="store_true", help="Explicitly confirm Canary arming after reviewing the audit")
    sub.add_parser("auto-live-confirm", help="Issue a short-lived single-use confirmation token for the current live order batch")
    auto_live_run = sub.add_parser("auto-live-run", help="Run broker auto-live submit only after all auto-live gates pass")
    auto_live_run.add_argument("--dry-run", action="store_true", help="Recheck gates and write dry-run order results without broker submission")
    auto_live_run.add_argument("--confirmation-token", help="Short-lived token returned by auto-live-confirm; required when configured")
    auto_live_cycle = sub.add_parser("auto-live-cycle", help="Run one audited auto-live cycle; audit-only unless --submit is explicit")
    auto_live_cycle.add_argument("--submit", action="store_true", help="Allow this cycle to call the configured broker adapter after every gate passes")
    auto_live_cycle.add_argument("--confirm-account-sync", action="store_true", help="Import audited fills into the local account; requires --submit and auto_live.allow_account_sync=true")
    auto_live_service = sub.add_parser("auto-live-service", help="Run the single-instance unattended auto-live service; one audit cycle by default")
    auto_live_service.add_argument("--loop", action="store_true", help="Keep running audited cycles until stopped or max-cycles is reached")
    auto_live_service.add_argument("--submit", action="store_true", help="Allow submit cycles only when both service-level submit switches and all normal gates pass")
    auto_live_service.add_argument("--confirm-account-sync", action="store_true", help="Import audited fills into local account; requires --submit and normal account-sync gate")
    auto_live_service.add_argument("--interval-seconds", type=float, help="Override wait interval between loop cycles")
    auto_live_service.add_argument("--max-cycles", type=int, help="Stop after this many cycles; 0 means unlimited in loop mode")
    auto_live_service_stop = sub.add_parser("auto-live-service-stop", help="Create the unattended-service stop file")
    auto_live_service_stop.add_argument("--reason", default="operator_stop", help="Operator-visible service-stop reason")
    auto_live_service_resume = sub.add_parser("auto-live-service-resume", help="Clear the unattended-service stop file after review")
    auto_live_service_resume.add_argument("--confirm", action="store_true", help="Confirm service-stop clearance; trading gates remain enforced")
    sub.add_parser("auto-live-service-lock", help="Audit unattended-service lock and heartbeat without deleting either")
    sub.add_parser("auto-live-query", help="Query broker order status for the latest auto-live submitted orders")
    sub.add_parser("auto-live-cancel", help="Cancel open or partially filled auto-live broker orders after status query")
    sub.add_parser("auto-live-cancel-verify", help="Verify a prior cancel request reached canceled or filled state after a fresh query")
    sub.add_parser("auto-live-cancel-lock", help="Audit the atomic cancel lock and crash-recovery evidence")
    cancel_lock_clear = sub.add_parser("auto-live-cancel-lock-clear", help="Clear a crash-left cancel lock only after fresh broker recovery evidence")
    cancel_lock_clear.add_argument("--confirm", action="store_true", help="Explicitly confirm clearing a recovery-ready cancel lock")
    sub.add_parser("auto-live-fills", help="Build fill-import candidate CSV from auto-live broker status results")
    auto_live_settle = sub.add_parser("auto-live-settle", help="Query broker status, build fill candidates, and optionally import fills into account state")
    auto_live_settle.add_argument("--confirm-import", action="store_true", help="Import audited auto-live fills into local account state; also requires auto_live.allow_account_sync=true")
    sub.add_parser("evidence-plan", help="Write automatic-live evidence gap plan from current outputs")
    sub.add_parser("observation-plan", help="Write daily forward/Shadow observation schedule from current evidence")
    sub.add_parser("remediation-plan", help="Write prioritized auto-live remediation plan from current blockers")
    sub.add_parser("verification-plan", help="Write ordered post-remediation auto-live verification chain")
    sub.add_parser("order-audit", help="Write per-order liquidity and capacity audit from current RPA tickets")
    sub.add_parser("execution-quality", help="Write post-trade fill execution quality review from imported fills")
    sub.add_parser("strategy-health", help="Write strategy health and degradation checks from current outputs")
    sub.add_parser("research", help="Evaluate configured strategy candidates and write strategy_research.csv")
    sub.add_parser("rebalance-research", help="Audit monthly versus quarterly lot-aware strategy evidence without promotion")
    sub.add_parser("selection-bias", help="Audit candidate-family fold selection bias from existing research evidence")
    sub.add_parser("baselines", help="Compare the formal strategy with common factor baselines")
    sub.add_parser("factor-robustness", help="Stress-test the formal strategy against factor drop and weight perturbations")
    sub.add_parser("promotion-decision", help="Write guarded strategy promotion decision from current evidence")
    compare = sub.add_parser("compare", help="Compare formal backtest output directories")
    compare.add_argument("output_dirs", nargs="*", help="Output directories to compare; defaults to outputs and outputs_*")
    sub.add_parser("stress", help="Run configured cost stress scenarios")
    sub.add_parser("reconcile", help="Audit paper account, positions, orders, and state database consistency")
    sub.add_parser("account-plan", help="Write a non-destructive account readiness plan")
    sub.add_parser("account-paths", help="Write non-destructive account path decisions")
    sub.add_parser("account-exclusion-audit", help="Write non-destructive impact audit for excluded unpriced account positions")
    account_state = sub.add_parser("account-state", help="Export current SQLite account state with latest mark-to-market valuation")
    account_state.add_argument("--date", help="Mark date; defaults to latest complete cached market date")
    account_reset = sub.add_parser("account-reset", help="Reset local paper account to cash-only state")
    account_reset.add_argument("--date", help="Reset date; defaults to latest complete cached market date")
    account_reset.add_argument("--cash", type=float, help="Reset cash; defaults to backtest.initial_cash")
    account_reset.add_argument("--confirm", action="store_true", help="Confirm clearing local paper positions, orders, trades, snapshots, and executable artifacts")
    sub.add_parser("account-template", help="Write a CSV template for manual account-state import")
    account_repair = sub.add_parser("account-repair-template", help="Write prefilled account review and import templates from current local state")
    account_repair.add_argument("--date", help="Mark date; defaults to latest complete cached market date")
    account_price = sub.add_parser("account-price-template", help="Write a manual price override template for missing account-position prices")
    account_price.add_argument("--date", help="Mark date; defaults to latest complete cached market date")
    account_price_check = sub.add_parser("account-price-check", help="Dry-run manual account price overrides without changing account state")
    account_price_check.add_argument("--date", help="Effective date; defaults to current account mark date")
    account_import = sub.add_parser("account-import", help="Import manually verified cash and positions into local account state")
    account_import.add_argument("--file", required=True, help="CSV file that has just passed account-import-check")
    account_import.add_argument("--date", help="Account date; defaults to latest complete cached market date")
    account_import.add_argument("--confirm", action="store_true", help="Confirm overwriting local account state and clearing stale executable artifacts")
    account_import.add_argument("--ack-exclusions", action="store_true", help="Acknowledge that account_import_priced_only_template.csv excludes unpriced positions")
    account_import_check = sub.add_parser("account-import-check", help="Dry-run account import validation without overwriting local state")
    account_import_check.add_argument("--file", required=True, help="CSV file to validate before account-import --confirm")
    account_import_check.add_argument("--date", help="Account date; defaults to latest complete cached market date")
    sub.add_parser("fill-template", help="Write a CSV template for broker/manual fill import")
    fill_import = sub.add_parser("fill-import", help="Import manually verified broker fills into local account state")
    fill_import.add_argument("--file", required=True, help="CSV file created from outputs/fill_import_template.csv")
    fill_import.add_argument("--confirm", action="store_true", help="Confirm updating cash, positions, trades, and clearing stale executable artifacts")

    export_orders = sub.add_parser("export-orders", help="Export latest orders to RPA-ready ticket file")
    export_orders.add_argument("--orders", help="Source orders CSV, defaults to outputs/orders.csv")
    export_orders.add_argument("--all", action="store_true", help="Export all orders instead of latest order date only")
    export_orders.add_argument("--allow-unverified-source", action="store_true", help="Allow exporting orders without paper provenance; research/debug only")

    rpa_preflight = sub.add_parser("rpa-preflight", help="Write non-destructive RPA environment and ticket preflight audit")
    rpa_preflight.add_argument("--tickets", help="Ticket CSV, defaults to outputs/rpa_orders.csv")
    rpa_preflight.add_argument("--layout", default="configs/rpa_template.yaml", help="RPA coordinate layout file")

    rpa_preview = sub.add_parser("rpa-preview", help="Preview RPA order tickets and safety status")
    rpa_preview.add_argument("--tickets", help="Ticket CSV, defaults to outputs/rpa_orders.csv")

    rpa_fill = sub.add_parser("rpa-fill", help="Fill one broker ticket by screen coordinates; never clicks final submit")
    rpa_fill.add_argument("--tickets", help="Ticket CSV, defaults to outputs/rpa_orders.csv")
    rpa_fill.add_argument("--ticket-id", help="Ticket id to fill")
    rpa_fill.add_argument("--index", type=int, default=0, help="Ticket row index when --ticket-id is omitted")
    rpa_fill.add_argument("--layout", default="configs/rpa_template.yaml", help="RPA coordinate layout file")
    rpa_fill.add_argument("--execute", action="store_true", help="Actually fill fields using pyautogui; default is dry-run")

    sub.add_parser("source-health", help="Write local data-source health and refresh recommendations")
    sub.add_parser("recover-partial", help="Merge data/cache/prices_partial into the formal cache with audit summary")
    sub.add_parser("doctor", help="Check config, optional dependencies, and cached data")
    sub.add_parser("probe", help="Probe AkShare data sources without writing cache")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    try:
        run_command(args, config)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def run_command(args: argparse.Namespace, config: dict) -> None:
    if args.command == "calendar-status":
        target = pd.to_datetime(args.date, errors="raise").date() if args.date else None
        path, status = write_trading_day_status(
            config,
            target=target,
            refresh_missing=bool(args.refresh_missing),
        )
        print(path.resolve())
        if args.strict_exit_code and status != "open":
            raise SystemExit(3 if status == "closed" else 4)
    elif args.command == "fetch":
        store = DataStore.from_config(config)
        store.init_db()
        if args.max_symbols:
            config.setdefault("data", {})["max_symbols"] = args.max_symbols
        if args.no_metadata:
            config.setdefault("data", {})["enrich_metadata"] = False
        if args.full_history:
            config.setdefault("data", {})["incremental_fetch"] = False
        history_budget = getattr(args, "history_budget", None)
        if history_budget is not None:
            config.setdefault("data", {})["history_request_budget"] = max(0, history_budget)
        if args.cached_universe:
            config.setdefault("data", {})["force_cached_universe"] = True
        if args.benchmark_only:
            import akshare as ak  # type: ignore

            with akshare_network_context(config):
                benchmark = normalize_benchmark(fetch_benchmark_akshare(ak, config))
            benchmark = merge_benchmark_cache(store, benchmark, config)
            paths = [store.write_frame("benchmark", benchmark)]
        else:
            paths = make_sample_data(store) if args.sample else fetch_akshare(store, config)
        store.log_run("fetch", "ok", ", ".join(str(path) for path in paths))
        print_paths(paths)
    elif args.command == "fetch-account-prices":
        print_paths(fetch_account_prices(config).values())
    elif args.command == "recover-partial":
        store = DataStore.from_config(config)
        paths = recover_partial_prices(store, config)
        store.log_run("recover-partial", "ok", ", ".join(str(path) for path in paths))
        print_paths(paths)
    elif args.command == "backtest":
        print_paths(run_backtest(config).values())
    elif args.command == "history-validate":
        print_paths(run_extended_history_validation(config).values())
    elif args.command == "capital-sensitivity":
        print_paths(run_capital_sensitivity(config).values())
    elif args.command == "metadata":
        store = DataStore.from_config(config)
        if args.workers:
            config.setdefault("data", {})["metadata_workers"] = args.workers
        paths = refresh_cached_metadata(store, config, max_symbols=args.max_symbols)
        store.log_run("metadata", "ok", ", ".join(str(path) for path in paths))
        print_paths(paths)
    elif args.command == "index-membership":
        start = pd.to_datetime(args.start).date() if args.start else None
        end = pd.to_datetime(args.end).date() if args.end else None
        print_paths(
            refresh_benchmark_membership(
                config,
                start=start,
                end=end,
                refresh_all=args.refresh_all,
            ).values()
        )
    elif args.command == "paper":
        require_active_execution_consistency(config)
        print_paths(
            invalidate_execution_artifacts(
                config,
                reason="direct_paper_cycle_started",
            ).values()
        )
        print_paths(
            run_paper(
                config,
                args.date,
                allow_stale=args.allow_stale,
                allow_weak_strategy=args.allow_weak_strategy,
                practical_mode=args.practical,
            ).values()
        )
    elif args.command == "shadow":
        print_paths(run_shadow_signals(config, args.date, allow_stale=args.allow_stale).values())
    elif args.command == "shadow-audit":
        print(run_shadow_audit(config).resolve())
    elif args.command == "observe":
        paths = run_observe(config, args)
        if args.quiet:
            print_observe_summary(paths)
        else:
            print_paths(paths.values())
    elif args.command == "observe-candidates":
        paths = run_observe_candidates(config, args)
        if args.quiet:
            print_candidate_observe_summary(paths)
        else:
            print_paths(paths.values())
    elif args.command == "observe-challengers":
        paths = run_primary_challenger_observation(config, allow_stale=args.allow_stale, skip_stress=args.skip_stress)
        paths["report.html"] = regenerate_report(config)
        if args.quiet:
            print_candidate_observe_summary(paths)
        else:
            print_paths(paths.values())
    elif args.command == "forward-daily":
        paths = run_forward_daily(config, args)
        if args.quiet:
            print_candidate_observe_summary(paths)
        else:
            print_paths(paths.values())
    elif args.command == "daily":
        print_paths(run_daily(config, args).values())
    elif args.command == "execution-drill":
        print_paths(run_execution_drill(config, args).values())
    elif args.command == "practical":
        print_paths(run_practical(config, args).values())
    elif args.command == "workflow":
        print_paths(run_workflow(config, skip_stress=args.skip_stress).values())
    elif args.command == "freeze-candidate":
        store = DataStore.from_config(config)
        print_paths(freeze_live_candidate(config, store, force=args.force))
    elif args.command == "preregister-forward-candidate":
        store = DataStore.from_config(config)
        print_paths(preregister_forward_candidate(config, store, force=args.force))
    elif args.command == "candidate-audit":
        print(run_candidate_audit(config).resolve())
    elif args.command == "seal-candidate-inputs":
        store = DataStore.from_config(config)
        print_paths(seal_candidate_frozen_inputs(config, store))
    elif args.command == "candidate-activation-audit":
        print(
            write_candidate_activation_audit(
                config,
                args.candidate_config,
            ).resolve()
        )
    elif args.command == "activate-candidate":
        print_paths(activate_forward_candidate(
            config,
            args.candidate_config,
            expected_candidate_id=args.candidate_id,
            confirm=args.confirm,
        ).values())
    elif args.command == "deactivate-candidate":
        print_paths(deactivate_execution_candidate(
            config,
            expected_candidate_id=args.candidate_id,
            confirm=args.confirm,
        ).values())
    elif args.command == "execution-strategy-status":
        print(write_active_execution_status(config).resolve())
    elif args.command == "run":
        print_paths(run_one_click(config, args).values())
    elif args.command == "local-run":
        print_paths(run_local_system(config, args).values())
    elif args.command == "local-status":
        live_config = load_config(str(args.live_config))
        paths = write_observation_task_status(config)
        paths.update(write_local_operator_status(config, live_config))
        print_paths(paths.values())
    elif args.command == "deployment-gate":
        live_config = load_config(str(args.live_config))
        print_paths(write_deployment_gate(config, live_config).values())
    elif args.command == "broker-canary-capital":
        print_paths(write_broker_canary_capital_feasibility(config).values())
    elif args.command == "broker-refresh":
        print_paths(run_broker_refresh(config, args).values())
    elif args.command == "observation-alert":
        print_paths(write_observation_alert_status(config, args.strategy_output).values())
    elif args.command == "broker-account-init":
        print_paths(initialize_broker_account_from_snapshot(
            config,
            expected_snapshot_run_id=args.snapshot_run_id,
            expected_account_id=args.account_id,
            confirm=args.confirm,
        ).values())
    elif args.command == "live-evidence-sync":
        live_config = load_config(str(args.live_config))
        print_paths(refresh_live_deployment_evidence(config, live_config).values())
    elif args.command == "report":
        print(regenerate_report(config))
    elif args.command == "dashboard":
        print(generate_control_panel(config).resolve())
    elif args.command == "roadmap":
        print(write_readiness_roadmap(config).resolve())
    elif args.command == "ready":
        print(write_practical_readiness(config).resolve())
    elif args.command == "execution-ready":
        path = write_execution_readiness(config)
        print(path.resolve())
        print_execution_readiness_summary(path)
    elif args.command == "auto-live-check":
        paths = write_auto_live_status(config)
        paths["auto_live_window_audit.csv"] = write_auto_live_window_audit(config)
        paths["auto_live_remediation_plan.csv"] = write_auto_live_remediation_plan(config)
        paths["auto_live_verification_plan.csv"] = write_auto_live_verification_plan(config)
        print_paths(paths.values())
    elif args.command == "auto-live-adapter-selftest":
        print(write_auto_live_adapter_selftest(config).resolve())
    elif args.command == "auto-live-adapter-files":
        print(write_command_adapter_file_audit(config).resolve())
    elif args.command == "auto-live-journal":
        print_paths(write_auto_live_journal_audit(config).values())
    elif args.command == "auto-live-adapter-replay":
        print_paths(write_auto_live_adapter_replay_audit(config, replay_dir=args.replay_dir).values())
    elif args.command == "auto-live-account":
        print_paths(query_auto_live_account(config).values())
    elif args.command == "auto-live-account-binding":
        print_paths(write_auto_live_account_binding_audit(config).values())
    elif args.command == "auto-live-recovery-audit":
        print_paths(write_auto_live_recovery_audit(config).values())
    elif args.command == "auto-live-alert-audit":
        print_paths(write_auto_live_alert_audit(config).values())
    elif args.command == "auto-live-alert-selftest":
        print_paths(write_auto_live_alert_selftest(config).values())
    elif args.command == "auto-live-alert-test":
        print_paths(send_auto_live_alert(config, event_type="operator_test", severity="info", message="Operator requested auto-live alert test.", context={"source": "cli"}).values())
    elif args.command == "auto-live-alert-retry":
        print_paths(retry_auto_live_alerts(config).values())
    elif args.command == "auto-live-rollout-audit":
        rollout_batches = write_auto_live_batch_plan(config)
        rollout_tickets = read_output_csv(rollout_batches["auto_live_current_batch.csv"])
        print_paths(write_auto_live_rollout_audit(config, tickets=rollout_tickets).values())
    elif args.command == "auto-live-rollout-approve":
        print_paths(approve_auto_live_rollout(config, stage=args.stage, confirm=args.confirm, reason=args.reason).values())
    elif args.command == "auto-live-rollout-revoke":
        print_paths(revoke_auto_live_rollout(config, confirm=args.confirm, reason=args.reason).values())
    elif args.command == "auto-live-backup":
        batch_paths = write_auto_live_batch_plan(config)
        recovery_tickets = read_output_csv(batch_paths["auto_live_current_batch.csv"])
        if recovery_tickets.empty:
            recovery_tickets = read_output_csv(batch_paths["auto_live_active_batch.csv"])
        recovery_run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        print_paths(create_auto_live_recovery_point(
            config,
            run_id=recovery_run_id,
            tickets=recovery_tickets,
            tickets_sha256=tickets_sha256(recovery_tickets),
            auto_live_config_sha256=auto_live_config_sha256(config),
        ).values())
    elif args.command == "auto-live-bind-account":
        print_paths(bind_auto_live_account(config, confirm=args.confirm).values())
    elif args.command == "auto-live-unbind-account":
        print_paths(unbind_auto_live_account(config, confirm=args.confirm).values())
    elif args.command == "auto-live-window":
        print(write_auto_live_window_audit(config).resolve())
    elif args.command == "auto-live-lock":
        print(write_auto_live_submit_lock_audit(config).resolve())
    elif args.command == "auto-live-risk":
        paths = write_auto_live_status(config)
        print(paths["auto_live_risk_audit.csv"].resolve())
    elif args.command == "auto-live-batch":
        print_paths(write_auto_live_batch_plan(config).values())
    elif args.command == "auto-live-stop":
        print_paths(activate_auto_live_stop(config, reason=args.reason).values())
    elif args.command == "auto-live-resume":
        print_paths(clear_auto_live_stop(config, confirm=args.confirm).values())
    elif args.command == "huatai-canary-audit":
        print(write_huatai_canary_arming_audit(config).resolve())
    elif args.command == "huatai-canary-sandbox":
        print(write_huatai_execution_sandbox_summary(config).resolve())
    elif args.command == "huatai-canary-arm":
        print_paths(arm_huatai_desktop_canary(config, confirm=args.confirm).values())
    elif args.command == "auto-live-confirm":
        confirmation = prepare_auto_live_action_confirmation(config)
        print(confirmation.path.resolve())
        print(f"ACTION_CONFIRMATION_TOKEN={confirmation.token}")
        print(f"EXPIRES_AT={confirmation.expires_at}")
    elif args.command == "auto-live-run":
        confirmation_token = getattr(args, "confirmation_token", None)
        if confirmation_token:
            print_paths(run_auto_live(config, dry_run=args.dry_run, confirmation_token=confirmation_token).values())
        else:
            print_paths(run_auto_live(config, dry_run=args.dry_run).values())
    elif args.command == "auto-live-cycle":
        paths = run_auto_live_cycle(config, submit=args.submit, confirm_account_sync=args.confirm_account_sync)
        paths["report.html"] = regenerate_report(config)
        paths["control_panel.html"] = generate_control_panel(config)
        print_paths(paths.values())
    elif args.command == "auto-live-service":
        paths = run_auto_live_service(
            config,
            submit=args.submit,
            confirm_account_sync=args.confirm_account_sync,
            loop=args.loop,
            interval_seconds=args.interval_seconds,
            max_cycles=args.max_cycles,
        )
        paths["report.html"] = regenerate_report(config)
        paths["control_panel.html"] = generate_control_panel(config)
        print_paths(paths.values())
    elif args.command == "auto-live-service-stop":
        print_paths(activate_auto_live_service_stop(config, reason=args.reason).values())
    elif args.command == "auto-live-service-resume":
        print_paths(clear_auto_live_service_stop(config, confirm=args.confirm).values())
    elif args.command == "auto-live-service-lock":
        print(write_auto_live_service_lock_audit(config).resolve())
    elif args.command == "auto-live-query":
        paths = query_auto_live_orders(config)
        output_dir = project_path(config["paths"]["output_dir"])
        if cancel_verification_needed(output_dir):
            paths.update(verify_auto_live_cancellations(config, raise_on_blocked=False))
        print_paths(paths.values())
    elif args.command == "auto-live-cancel":
        print_paths(cancel_auto_live_orders(config).values())
    elif args.command == "auto-live-cancel-verify":
        print_paths(verify_auto_live_cancellations(config).values())
    elif args.command == "auto-live-cancel-lock":
        print(write_auto_live_cancel_lock_audit(config).resolve())
    elif args.command == "auto-live-cancel-lock-clear":
        print(clear_auto_live_cancel_lock(config, confirm=args.confirm).resolve())
    elif args.command == "auto-live-fills":
        print_paths(write_auto_live_fill_sync(config).values())
    elif args.command == "auto-live-settle":
        print_paths(settle_auto_live_fills(config, confirm_import=args.confirm_import).values())
    elif args.command == "evidence-plan":
        print(write_evidence_plan(config).resolve())
    elif args.command == "observation-plan":
        print(write_observation_schedule(config).resolve())
    elif args.command == "remediation-plan":
        print(write_auto_live_remediation_plan(config).resolve())
    elif args.command == "verification-plan":
        print(write_auto_live_verification_plan(config).resolve())
    elif args.command == "order-audit":
        print(write_order_liquidity_audit(config).resolve())
    elif args.command == "execution-quality":
        print_paths(write_execution_quality(config).values())
    elif args.command == "strategy-health":
        print(write_strategy_health(config).resolve())
    elif args.command == "research":
        path = run_strategy_research(config)
        print(path.resolve())
    elif args.command == "rebalance-research":
        print_paths(write_rebalance_frequency_research(config).values())
    elif args.command == "selection-bias":
        print_paths(write_strategy_selection_bias(config).values())
    elif args.command == "baselines":
        path = run_strategy_baselines(config)
        print(path.resolve())
    elif args.command == "factor-robustness":
        print_paths(run_factor_robustness(config).values())
    elif args.command == "promotion-decision":
        configured_paths = primary_challenger_config_paths(config)
        output_dirs = None
        if configured_paths:
            output_dirs = [str(project_path(config["paths"]["output_dir"]))]
            for configured in configured_paths:
                candidate_path = project_path(configured)
                if not candidate_path.exists():
                    continue
                candidate_config = load_config(candidate_path)
                candidate_output = str(project_path(candidate_config["paths"]["output_dir"]))
                if candidate_output not in output_dirs:
                    output_dirs.append(candidate_output)
        compare_backtest_outputs(config, output_dirs)
        print(write_strategy_promotion_decision(config).resolve())
    elif args.command == "compare":
        print(compare_backtest_outputs(config, args.output_dirs or None).resolve())
    elif args.command == "stress":
        print(write_cost_stress(config).resolve())
    elif args.command == "reconcile":
        print(reconcile_outputs(config).resolve())
    elif args.command == "account-plan":
        path = write_account_readiness_plan(config)
        print(path.resolve())
        print_account_plan_summary(path)
        decision_path = write_account_path_decision(config)
        print(decision_path.resolve())
        print_account_path_summary(decision_path)
    elif args.command == "account-paths":
        path = write_account_path_decision(config)
        print(path.resolve())
        print_account_path_summary(path)
    elif args.command == "account-exclusion-audit":
        print_paths(write_account_exclusion_impact(config).values())
    elif args.command == "account-state":
        print_paths(export_account_state(config, asof_date=args.date).values())
    elif args.command == "account-reset":
        print_paths(reset_account_state(config, asof_date=args.date, cash=args.cash, confirm=args.confirm).values())
    elif args.command == "account-template":
        print(write_account_import_template(config).resolve())
    elif args.command == "account-repair-template":
        print_paths(write_account_repair_template(config, asof_date=args.date).values())
    elif args.command == "account-price-template":
        print(write_account_price_override_template(config, asof_date=args.date).resolve())
    elif args.command == "account-price-check":
        print_paths(check_account_price_overrides(config, asof_date=args.date).values())
    elif args.command == "account-import":
        print_paths(import_account_state(config, args.file, asof_date=args.date, confirm=args.confirm, ack_exclusions=args.ack_exclusions).values())
    elif args.command == "account-import-check":
        paths = check_account_import_file(config, args.file, asof_date=args.date)
        print_paths(paths.values())
        print_account_import_check_summary(paths["account_import_check_audit.csv"])
    elif args.command == "fill-template":
        print(write_fill_import_template(config).resolve())
    elif args.command == "fill-import":
        print_paths(import_fills(config, args.file, confirm=args.confirm).values())
    elif args.command == "export-orders":
        print(export_rpa_orders(config, args.orders, latest_only=not args.all, require_paper_source=not args.allow_unverified_source).resolve())
        print(write_rpa_preflight(config).resolve())
    elif args.command == "rpa-preflight":
        print(write_rpa_preflight(config, ticket_path=args.tickets, layout_path=args.layout).resolve())
    elif args.command == "rpa-preview":
        print(preview_rpa_orders(config, args.tickets))
    elif args.command == "rpa-fill":
        print(fill_rpa_ticket(config, args.tickets, args.ticket_id, args.index, args.layout, execute=args.execute).resolve())
    elif args.command == "source-health":
        path = write_data_source_health(config)
        print(path.resolve())
        print_data_source_health_summary(build_data_source_health(config))
    elif args.command == "doctor":
        run_doctor(config)
    elif args.command == "probe":
        run_probe(config)


def fetch_account_prices(config: dict) -> dict[str, Path]:
    store = DataStore.from_config(config)
    store.init_db()
    symbols = required_fetch_symbols(store, config)
    if not symbols:
        raise RuntimeError("当前 SQLite 账户没有可刷新的 A 股持仓；如需刷新普通股票池，请运行 fetch。")
    targeted = deepcopy(config)
    data = targeted.setdefault("data", {})
    data["force_cached_universe"] = True
    data["max_symbols"] = 0
    data["enrich_metadata"] = False
    data["include_account_positions_on_fetch"] = True
    paths: dict[str, Path] = {f"fetch:{Path(path).name}": Path(path) for path in fetch_akshare(store, targeted)}
    paths.update({f"account-state:{name}": path for name, path in export_account_state(config).items()})
    return paths


def print_paths(paths) -> None:
    for path in paths:
        print(Path(path).resolve())


def print_observe_summary(paths: dict[str, Path]) -> None:
    keys = [
        "backtest:live_readiness.csv",
        "cost_stress.csv",
        "shadow:shadow_provenance.csv",
        "shadow_forward_audit.csv",
        "report.html",
    ]
    for key in keys:
        path = paths.get(key)
        if path is not None:
            print(f"{key}: {Path(path).resolve()}")


def print_candidate_observe_summary(paths: dict[str, Path]) -> None:
    for key in sorted(paths):
        if key.endswith(("live_readiness.csv", "shadow_forward_audit.csv", "report.html", "strategy_promotion.csv", "deployment_gate.csv", "deployment_gate.html")):
            print(f"{key}: {Path(paths[key]).resolve()}")


def run_observe_candidates(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    configs = candidate_observation_configs(config, args.candidate_config)
    progress_configs = [candidate_config for _, candidate_config in configs]
    for registered_path in primary_challenger_config_paths(config):
        progress_configs.append(load_config(project_path(registered_path)))
    progress_before = capture_observation_snapshot(progress_configs) if args.refresh else pd.DataFrame()
    if args.refresh:
        store = DataStore.from_config(config)
        store.init_db()
        if args.max_symbols:
            config.setdefault("data", {})["max_symbols"] = args.max_symbols
        if args.no_metadata:
            config.setdefault("data", {})["enrich_metadata"] = False
        if args.cached_universe:
            config.setdefault("data", {})["force_cached_universe"] = True
        for path in fetch_akshare(store, config):
            paths[f"fetch:{path.name}"] = path
        store.log_run("observe_candidates_fetch", "ok", f"refreshed {len(paths)} files")

    total = len(configs)
    print(f"observe-candidates: queued {total} strategy configs", flush=True)
    output_dirs: list[str] = []
    for idx, (label, candidate_config) in enumerate(configs, start=1):
        started = time.monotonic()
        print(f"observe-candidates: [{idx}/{total}] {label} started", flush=True)
        observe_args = argparse.Namespace(
            refresh=False,
            max_symbols=None,
            cached_universe=False,
            no_metadata=False,
            allow_stale=args.allow_stale,
            skip_stress=args.skip_stress,
        )
        try:
            observed_paths = run_observe(candidate_config, observe_args)
        except Exception as exc:
            elapsed = time.monotonic() - started
            print(f"observe-candidates: [{idx}/{total}] {label} failed after {elapsed:.1f}s: {exc}", flush=True)
            raise
        for name, path in observed_paths.items():
            paths[f"{label}:{name}"] = path
        output_dirs.append(str(project_path(candidate_config["paths"]["output_dir"])))
        elapsed = time.monotonic() - started
        print(f"observe-candidates: [{idx}/{total}] {label} done in {elapsed:.1f}s", flush=True)

    if primary_challenger_config_paths(config):
        print("observe-candidates: refreshing primary challenger summary", flush=True)
        challenger_paths = run_primary_challenger_observation(
            config,
            allow_stale=args.allow_stale,
            skip_stress=args.skip_stress,
        )
        for name, path in challenger_paths.items():
            paths[f"primary-challenger:{name}"] = path
        summary_path = challenger_paths.get("primary_challenger_observation.csv")
        summary = read_output_csv(summary_path) if summary_path is not None else pd.DataFrame()
        if not summary.empty and {"status", "candidate_output_dir"}.issubset(summary.columns):
            observed = summary[summary["status"].astype(str).str.lower() == "observed"]
            for value in observed["candidate_output_dir"]:
                candidate_output = str(value or "").strip()
                if candidate_output and candidate_output not in output_dirs:
                    output_dirs.append(candidate_output)

    print("observe-candidates: comparing strategy outputs", flush=True)
    compare_path = compare_backtest_outputs(config, output_dirs)
    paths["strategy_backtest_compare.csv"] = compare_path
    promotion_path = compare_path.parent / "strategy_promotion.csv"
    if promotion_path.exists():
        paths["strategy_promotion.csv"] = promotion_path
    progress_failures: list[str] = []
    if args.refresh:
        progress_after = capture_observation_snapshot(progress_configs)
        progress_paths, progress_audit = write_observation_progress(config, progress_before, progress_after)
        paths.update(progress_paths)
        progress_failures = observation_progress_blockers(progress_audit)
    print("observe-candidates: regenerating main report", flush=True)
    paths["report.html"] = regenerate_report(config)
    if progress_failures:
        raise RuntimeError("Observation evidence progress audit failed: " + ";".join(progress_failures))
    print("observe-candidates: completed", flush=True)
    return paths


def candidate_observation_configs(config: dict, candidate_config_paths: list[str]) -> list[tuple[str, dict]]:
    configs: list[tuple[str, dict]] = [("default", config)]
    registered_outputs: set[str] = set()
    for registered_path in primary_challenger_config_paths(config):
        try:
            registered_config = load_config(project_path(registered_path))
            registered_outputs.add(
                str(project_path(registered_config["paths"]["output_dir"]).resolve())
            )
        except (KeyError, OSError, ValueError):
            continue
    if candidate_config_paths:
        paths = candidate_config_paths
    elif registered_outputs:
        # Registered challengers are observed by run_primary_challenger_observation.
        # Avoid running legacy defaults a second time in the generic loop.
        paths = []
    else:
        paths = [
            "configs/adaptive_trend.yaml",
            "configs/robust_blend.yaml",
            "configs/adaptive_robust.yaml",
            "configs/adaptive_redundant.yaml",
        ]
    seen_outputs = {str(project_path(config["paths"]["output_dir"]).resolve())}
    for idx, path in enumerate(paths, start=1):
        candidate_path = project_path(path)
        if not candidate_path.exists():
            continue
        candidate_config = load_config(candidate_path)
        output_dir = str(project_path(candidate_config["paths"]["output_dir"]).resolve())
        if output_dir in seen_outputs or output_dir in registered_outputs:
            continue
        seen_outputs.add(output_dir)
        label = candidate_config.get("strategy", {}).get("name") or f"candidate{idx}"
        configs.append((str(label), candidate_config))
    return configs


PRIMARY_CHALLENGER_BACKTEST_ARTIFACTS = (
    "performance_metrics.csv",
    "benchmark_comparison.csv",
    "run_manifest.csv",
    "live_readiness.csv",
    "practical_readiness.csv",
    "strategy_reliability.csv",
)


def primary_challenger_backtest_issues(candidate_config: dict) -> list[str]:
    output_dir = project_path(candidate_config["paths"]["output_dir"])
    missing = [name for name in PRIMARY_CHALLENGER_BACKTEST_ARTIFACTS if not (output_dir / name).is_file()]
    if missing:
        return ["missing_artifacts=" + ",".join(missing)]
    return readiness_evidence_issues(candidate_config)


def run_primary_challenger_observation(
    config: dict,
    *,
    allow_stale: bool = False,
    skip_stress: bool = False,
) -> dict[str, Path]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured_paths = primary_challenger_config_paths(config)
    if not configured_paths:
        return {}
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "primary_challenger_observation.csv"
    paths: dict[str, Path] = {}
    rows: list[dict[str, object]] = []
    successful_outputs: list[str] = []
    strict_errors: list[str] = []
    seen_outputs = {output_dir.resolve()}
    total = len(configured_paths)
    for idx, configured in enumerate(configured_paths, start=1):
        started = time.monotonic()
        candidate_name = Path(configured).stem
        candidate_output = ""
        backtest_mode = "not_started"
        backtest_reason = ""
        status = "failed"
        message = ""
        print(f"primary-challengers: [{idx}/{total}] {configured} started", flush=True)
        try:
            candidate_path = project_path(configured)
            if not candidate_path.exists():
                raise RuntimeError(f"primary challenger config not found: {candidate_path}")
            candidate_config = load_config(candidate_path)
            candidate_name = str(candidate_config.get("strategy", {}).get("name", "") or candidate_path.stem)
            candidate_output_path = project_path(candidate_config["paths"]["output_dir"])
            candidate_output_resolved = candidate_output_path.resolve()
            candidate_output = str(candidate_output_resolved)
            if candidate_output_resolved in seen_outputs:
                raise RuntimeError("primary challenger output_dir must be unique and differ from champion output_dir")
            seen_outputs.add(candidate_output_resolved)
            reuse_enabled = bool(validation.get("primary_challenger_reuse_current_backtest", True))
            backtest_issues = primary_challenger_backtest_issues(candidate_config) if reuse_enabled else ["reuse_disabled"]
            prefix = f"challenger-{idx}"
            if backtest_issues:
                backtest_mode = "refreshed"
                backtest_reason = ";".join(backtest_issues)
                for name, result_path in run_backtest(candidate_config).items():
                    paths[f"{prefix}-backtest:{name}"] = result_path
            else:
                backtest_mode = "reused"
                backtest_reason = "manifest_current"
                paths[f"{prefix}-backtest:run_manifest.csv"] = candidate_output_path / "run_manifest.csv"
                paths[f"{prefix}-backtest:performance_metrics.csv"] = candidate_output_path / "performance_metrics.csv"
            if not skip_stress:
                stress_path = ensure_cost_stress_current(candidate_config)
                if stress_path is not None:
                    paths[f"{prefix}:cost_stress.csv"] = stress_path
            for name, result_path in run_shadow_signals(candidate_config, allow_stale=allow_stale).items():
                paths[f"{prefix}-shadow:{name}"] = result_path
            paths[f"{prefix}:shadow_forward_audit.csv"] = run_shadow_audit(candidate_config)
            paths[f"{prefix}:live_candidate_validation.csv"] = run_candidate_audit(candidate_config)
            successful_outputs.append(candidate_output)
            status = "observed"
            message = f"Registered challenger evidence refreshed; historical backtest {backtest_mode}."
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            strict_errors.append(f"{configured}: {message}")
        elapsed = round(time.monotonic() - started, 3)
        rows.append(
            {
                "status": status,
                "candidate_config": configured,
                "candidate_name": candidate_name,
                "candidate_output_dir": candidate_output,
                "backtest_mode": backtest_mode,
                "backtest_reason": backtest_reason,
                "allow_stale": bool(allow_stale),
                "skip_stress": bool(skip_stress),
                "elapsed_seconds": elapsed,
                "message": message,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        print(f"primary-challengers: [{idx}/{total}] {candidate_name} {status} in {elapsed:.1f}s", flush=True)

    atomic_to_csv(pd.DataFrame(rows), summary_path, index=False, encoding="utf-8-sig")
    paths["primary_challenger_observation.csv"] = summary_path
    if successful_outputs:
        compare_path = compare_backtest_outputs(
            config,
            [str(output_dir), *successful_outputs],
        )
        paths["strategy_backtest_compare.csv"] = compare_path
        paths["strategy_promotion_decision.csv"] = write_strategy_promotion_decision(config)
    if strict_errors and bool(validation.get("primary_challenger_strict", False)):
        raise RuntimeError("primary challenger observation failed: " + " | ".join(strict_errors))
    return paths


def run_research_candidate_observations(
    config: dict,
    *,
    allow_stale: bool = False,
    skip_stress: bool = False,
) -> dict[str, Path]:
    """Refresh isolated research candidates without making them promotable."""
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured = validation.get(
        "research_observation_configs",
        ["configs/breadth_guarded_champion.yaml"],
    )
    if not isinstance(configured, list) or not configured:
        return {}
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "research_candidate_observation.csv"
    paths: dict[str, Path] = {}
    rows: list[dict[str, object]] = []
    seen_outputs = {output_dir.resolve()}
    for idx, configured_path in enumerate(configured, start=1):
        started = time.monotonic()
        candidate_name = Path(str(configured_path)).stem
        candidate_output = ""
        backtest_mode = "not_started"
        backtest_reason = ""
        status = "failed"
        message = ""
        strategy_hash = ""
        try:
            candidate_path = project_path(str(configured_path))
            if not candidate_path.exists():
                raise RuntimeError(f"research candidate config not found: {candidate_path}")
            candidate_config = load_config(candidate_path)
            candidate_name = str(candidate_config.get("strategy", {}).get("name", "") or candidate_path.stem)
            if not bool(candidate_config.get("validation", {}).get("research_only", False)):
                raise RuntimeError("research observation config must set validation.research_only=true")
            candidate_output_path = project_path(candidate_config["paths"]["output_dir"])
            candidate_output = str(candidate_output_path.resolve())
            if candidate_output_path.resolve() in seen_outputs:
                raise RuntimeError("research candidate output_dir must be unique and differ from champion output_dir")
            seen_outputs.add(candidate_output_path.resolve())
            strategy_hash = live_strategy_hash(candidate_config)
            backtest_issues = primary_challenger_backtest_issues(candidate_config)
            if backtest_issues:
                backtest_mode = "refreshed"
                backtest_reason = ";".join(backtest_issues)
                for name, result_path in run_backtest(candidate_config).items():
                    paths[f"research-{idx}-backtest:{name}"] = result_path
            else:
                backtest_mode = "reused"
                backtest_reason = "manifest_current"
                paths[f"research-{idx}-backtest:run_manifest.csv"] = candidate_output_path / "run_manifest.csv"
            if not skip_stress:
                stress_path = ensure_cost_stress_current(candidate_config, guard_name="practical_guard")
                if stress_path is not None:
                    paths[f"research-{idx}:cost_stress.csv"] = stress_path
            for name, result_path in run_shadow_signals(candidate_config, allow_stale=allow_stale).items():
                paths[f"research-{idx}-shadow:{name}"] = result_path
            paths[f"research-{idx}:shadow_forward_audit.csv"] = run_shadow_audit(candidate_config)
            paths[f"research-{idx}:report.html"] = regenerate_report(candidate_config)
            status = "observed"
            message = f"Research evidence refreshed; historical backtest {backtest_mode}."
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "status": status,
                "candidate_config": str(configured_path),
                "candidate_name": candidate_name,
                "candidate_output_dir": candidate_output,
                "strategy_hash": strategy_hash,
                "backtest_mode": backtest_mode,
                "backtest_reason": backtest_reason,
                "allow_stale": bool(allow_stale),
                "skip_stress": bool(skip_stress),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "message": message,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    atomic_to_csv(pd.DataFrame(rows), summary_path, index=False, encoding="utf-8-sig")
    paths["research_candidate_observation.csv"] = summary_path
    return paths


def run_daily(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    paths["active_execution_status.csv"] = require_active_execution_consistency(config)
    for name, path in invalidate_execution_artifacts(
        config, reason="daily_cycle_started"
    ).items():
        paths[f"invalidation:{name}"] = path

    if args.refresh:
        store = DataStore.from_config(config)
        store.init_db()
        if args.max_symbols:
            config.setdefault("data", {})["max_symbols"] = args.max_symbols
        if args.no_metadata:
            config.setdefault("data", {})["enrich_metadata"] = False
        if args.cached_universe:
            config.setdefault("data", {})["force_cached_universe"] = True
        for path in fetch_akshare(store, config):
            paths[f"fetch:{path.name}"] = path
        store.log_run("daily_fetch", "ok", f"refreshed {len(paths)} files")
        for name, path in run_backtest(config).items():
            paths[f"readiness_backtest:{name}"] = path
    practical_mode = bool(getattr(args, "practical", False))
    if not args.skip_stress:
        stress_path = ensure_cost_stress_current(config, guard_name="practical_guard" if practical_mode else "live_guard")
        if stress_path is not None:
            paths["cost_stress.csv"] = stress_path
    if practical_mode:
        paths["practical_readiness.csv"] = write_practical_readiness(config)
    paths["strategy_health.csv"] = write_strategy_health(config)
    for name, path in run_paper(
        config,
        allow_stale=args.allow_stale,
        allow_weak_strategy=args.allow_weak_strategy,
        practical_mode=practical_mode,
    ).items():
        paths[f"paper:{name}"] = path
    rpa_path = export_rpa_orders(config)
    paths["rpa_orders.csv"] = rpa_path
    print(preview_rpa_orders(config, rpa_path))
    return paths


def build_candidate_market_freshness(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    trade_calendar: pd.DataFrame,
    config: dict,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current = now or datetime.now()
    target = finalized_market_data_end(config, current)
    calendar_required = bool(config.get("data", {}).get("trade_calendar_preflight", True))
    try:
        calendar = normalize_trade_calendar(trade_calendar)
    except ValueError:
        calendar = pd.DataFrame()
    calendar_covered = bool(
        not calendar.empty
        and "date" in calendar
        and max(calendar["date"]) >= target
    )
    expected = target
    if calendar_covered:
        sessions = calendar[
            (calendar["date"] <= target)
            & calendar["is_trading_day"].astype(bool)
        ]
        if not sessions.empty:
            expected = max(sessions["date"])

    complete_dates = complete_market_dates(prices, benchmark, config, now=current)
    complete_end = complete_dates[-1] if complete_dates else None
    benchmark_dates = (
        pd.to_datetime(benchmark.get("date"), errors="coerce").dropna()
        if not benchmark.empty and "date" in benchmark
        else pd.Series(dtype="datetime64[ns]")
    )
    benchmark_end = benchmark_dates.max().date() if not benchmark_dates.empty else None
    market_data_current = bool(
        (calendar_covered or not calendar_required)
        and complete_end is not None
        and complete_end >= expected
        and benchmark_end is not None
        and benchmark_end >= expected
    )
    return {
        "market_data_current": market_data_current,
        "expected_market_date": expected.isoformat(),
        "complete_market_end": "" if complete_end is None else complete_end.isoformat(),
        "benchmark_end": "" if benchmark_end is None else benchmark_end.isoformat(),
        "trade_calendar_covered": calendar_covered or not calendar_required,
    }


def candidate_market_freshness(config: dict, *, now: datetime | None = None) -> dict[str, object]:
    store = DataStore.from_config(config)
    _, prices, _ = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    try:
        calendar = store.read_frame("trade_calendar", required=False)
    except FileNotFoundError:
        calendar = pd.DataFrame()
    return build_candidate_market_freshness(
        prices, benchmark, calendar, config, now=now
    )

def run_forward_only_candidate_observations(
    config: dict,
    *,
    allow_stale: bool = False,
) -> dict[str, Path]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured = validation.get(
        "forward_only_candidate_configs",
        ["configs/hs300_point_in_time_core.yaml"],
    )
    if not isinstance(configured, list) or not configured:
        return {}
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "forward_only_candidate_observation.csv"
    paths: dict[str, Path] = {}
    rows: list[dict[str, object]] = []
    attribution_frames: list[pd.DataFrame] = []
    for idx, configured_path in enumerate(configured, start=1):
        started = time.monotonic()
        candidate_name = Path(str(configured_path)).stem
        candidate_id = ""
        candidate_output = ""
        status = "failed"
        message = ""
        signal_rows = 0
        frozen_input_evidence_passed = False
        frozen_input_mismatches = ""
        freeze_price_end = ""
        membership_evidence_passed = False
        benchmark_mapping_evidence_passed = False
        shadow_evaluation_evidence_passed = False
        shadow_evaluation_sha256 = ""
        forward_risk_evaluation_evidence_passed = False
        forward_risk_evaluation_sha256 = ""
        market_data_current = False
        expected_market_date = ""
        complete_market_end = ""
        benchmark_end = ""
        trade_calendar_covered = False
        membership_rows_at_freeze = 0
        membership_sha256_at_freeze = ""
        observable_signal_count = 0
        pending_signal_count = 0
        latest_pending_signal_date = ""
        next_pending_execution_date = ""
        first_attribution_requires_close_after = ""
        max_observed_trading_days = 0
        required_forward_trading_days = 0
        remaining_forward_trading_days = 0
        forward_observation_status = "not_started"
        try:
            candidate_config = load_config(project_path(str(configured_path)))
            candidate_name = str(candidate_config.get("strategy", {}).get("name", "") or candidate_name)
            candidate_output_path = project_path(candidate_config["paths"]["output_dir"])
            candidate_output = str(candidate_output_path.resolve())
            candidate_validation = candidate_config.get("validation", {})
            if not bool(candidate_validation.get("research_only", False)):
                raise RuntimeError("forward-only candidate must remain research_only")
            if str(candidate_validation.get("experiment_status", "")) != "preregistered_forward_only":
                raise RuntimeError("forward-only candidate experiment_status is not preregistered_forward_only")
            manifest = load_candidate_manifest(candidate_config)
            if not manifest or manifest.get("freeze_mode") != "forward_only_preregistration":
                raise RuntimeError("forward-only candidate has no valid preregistration manifest")
            candidate_id = matching_candidate_id(candidate_config, manifest)
            freeze_price_end = str(manifest.get("freeze_price_end", "") or "")
            required_forward_trading_days = int(
                manifest.get("required_forward_trading_days", 0) or 0
            )
            if not candidate_id:
                raise RuntimeError("forward-only candidate hash no longer matches its preregistration")
            boundary_ok, _, _ = candidate_freeze_boundary_evidence(manifest)
            if not boundary_ok:
                raise RuntimeError("forward-only candidate freeze boundary is unsafe; re-register without migrating old observations")
            membership_result = candidate_index_membership_evidence(candidate_config, manifest)
            membership_evidence_passed = bool(membership_result[0])
            mapping_result = candidate_benchmark_mapping_evidence(candidate_config, manifest)
            benchmark_mapping_evidence_passed = bool(mapping_result[0])
            if not benchmark_mapping_evidence_passed:
                raise RuntimeError("frozen benchmark index mapping evidence no longer matches")
            shadow_result = shadow_evaluation_evidence(candidate_config, manifest)
            shadow_evaluation_evidence_passed = bool(shadow_result[0])
            shadow_evaluation_sha256 = mapping_sha256(shadow_evaluation_identity(candidate_config))
            if not shadow_evaluation_evidence_passed:
                raise RuntimeError("frozen shadow evaluation policy no longer matches")
            forward_risk_result = forward_risk_evaluation_evidence(candidate_config, manifest)
            forward_risk_evaluation_evidence_passed = bool(forward_risk_result[0])
            forward_risk_evaluation_sha256 = mapping_sha256(
                forward_risk_evaluation_identity(candidate_config)
            )
            if not forward_risk_evaluation_evidence_passed:
                raise RuntimeError("frozen forward risk evaluation policy no longer matches")
            if bool(candidate_validation.get("require_frozen_input_evidence", False)):
                frozen_evidence = candidate_frozen_input_evidence(candidate_config, manifest)
                frozen_input_evidence_passed = bool(
                    not frozen_evidence.empty and frozen_evidence["passed"].all()
                )
                frozen_input_mismatches = ",".join(
                    frozen_evidence.loc[~frozen_evidence["passed"], "input"].astype(str)
                )
                if not frozen_input_evidence_passed:
                    raise RuntimeError("frozen candidate market inputs no longer match")
            else:
                frozen_input_evidence_passed = True

            membership_rows_at_freeze = int(membership_result[3])
            membership_sha256_at_freeze = str(membership_result[4])
            if not membership_evidence_passed:
                raise RuntimeError("frozen point-in-time index membership evidence no longer matches")

            freshness = candidate_market_freshness(candidate_config)
            market_data_current = bool(freshness["market_data_current"])
            expected_market_date = str(freshness["expected_market_date"])
            complete_market_end = str(freshness["complete_market_end"])
            benchmark_end = str(freshness["benchmark_end"])
            trade_calendar_covered = bool(freshness["trade_calendar_covered"])
            stale_market_message = ""
            if not market_data_current:
                stale_market_message = (
                    "candidate market data is not current: "
                    f"expected={expected_market_date};complete={complete_market_end or 'missing'};"
                    f"benchmark={benchmark_end or 'missing'};calendar={trade_calendar_covered}"
                )

            if market_data_current:
                for name, result_path in run_shadow_signals(
                    candidate_config,
                    allow_stale=allow_stale,
                ).items():
                    paths[f"forward-{idx}-shadow:{name}"] = result_path
            audit_path = run_shadow_audit(candidate_config)
            paths[f"forward-{idx}:shadow_forward_audit.csv"] = audit_path
            attribution_path = run_shadow_component_attribution(candidate_config)
            paths[f"forward-{idx}:shadow_component_attribution.csv"] = attribution_path
            candidate_audit_path = run_candidate_audit(candidate_config)
            paths[f"forward-{idx}:live_candidate_validation.csv"] = candidate_audit_path
            attribution = read_output_csv(attribution_path)
            if not attribution.empty:
                attribution.insert(0, "candidate_name", candidate_name)
                attribution.insert(0, "candidate_config", str(configured_path))
                attribution_frames.append(attribution)
            audit = read_output_csv(audit_path)
            if not audit.empty and "candidate_id" in audit.columns:
                audit = audit[audit["candidate_id"].fillna("").astype(str) == candidate_id]
            if not audit.empty:
                freeze_date = pd.to_datetime(freeze_price_end, errors="coerce")
                execution_dates = pd.to_datetime(
                    audit.get("execution_date"), errors="coerce"
                )
                audit_status = audit.get(
                    "status", pd.Series("", index=audit.index)
                ).fillna("").astype(str).str.lower()
                pending = audit_status.eq("not_observable") & execution_dates.gt(freeze_date)
                pending_signal_count = int(pending.sum())
                if pending_signal_count:
                    signal_dates = pd.to_datetime(
                        audit.get("signal_date"), errors="coerce"
                    )
                    latest_pending_signal_date = str(signal_dates[pending].max().date())
                    next_pending_execution_date = str(execution_dates[pending].min().date())
                    first_attribution_requires_close_after = next_pending_execution_date
            observation = candidate_shadow_observation(
                audit,
                candidate_id,
                freeze_price_end,
            )
            if observation:
                max_observed_trading_days = int(observation["days"])
                execution_dates = pd.to_datetime(
                    audit.get("execution_date"), errors="coerce"
                )
                freeze_date = pd.to_datetime(freeze_price_end, errors="coerce")
                valid_status = audit.get(
                    "status", pd.Series("", index=audit.index)
                ).fillna("").astype(str).str.lower().eq("ok")
                observed_days = pd.to_numeric(
                    audit.get("observed_trading_days", pd.Series(dtype=float)),
                    errors="coerce",
                ).fillna(0)
                observable_signal_count = int(
                    (valid_status & execution_dates.gt(freeze_date) & observed_days.gt(0)).sum()
                )
            remaining_forward_trading_days = max(required_forward_trading_days - max_observed_trading_days, 0)
            forward_observation_status = (
                "shadow_window_complete"
                if required_forward_trading_days and remaining_forward_trading_days == 0
                else "collecting_forward_data"
                if max_observed_trading_days > 0
                else "waiting_for_post_freeze_market_data"
            )
            signal_path = candidate_output_path / "shadow_signal_snapshot.csv"
            signal_rows = len(read_output_csv(signal_path))
            status = "observed" if market_data_current else "waiting_for_market_data"
            message = (
                (stale_market_message + "; ") if stale_market_message else ""
            ) + (
                "Forward-only immutable shadow evidence refreshed; "
                f"pending_signals={pending_signal_count}; "
                f"next_execution={next_pending_execution_date or 'none'}; "
                f"first_attribution_close_after={first_attribution_requires_close_after or 'none'}; "
                "no historical backtest or executable order was created."
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "status": status,
                "candidate_config": str(configured_path),
                "candidate_name": candidate_name,
                "candidate_id": candidate_id,
                "candidate_output_dir": candidate_output,
                "signal_rows": signal_rows,
                "frozen_input_evidence_passed": frozen_input_evidence_passed,
                "frozen_input_mismatches": frozen_input_mismatches,
                "freeze_price_end": freeze_price_end,
                "membership_evidence_passed": membership_evidence_passed,
                "benchmark_mapping_evidence_passed": benchmark_mapping_evidence_passed,
                "shadow_evaluation_evidence_passed": shadow_evaluation_evidence_passed,
                "shadow_evaluation_sha256": shadow_evaluation_sha256,
                "forward_risk_evaluation_evidence_passed": forward_risk_evaluation_evidence_passed,
                "forward_risk_evaluation_sha256": forward_risk_evaluation_sha256,
                "market_data_current": market_data_current,
                "expected_market_date": expected_market_date,
                "complete_market_end": complete_market_end,
                "benchmark_end": benchmark_end,
                "trade_calendar_covered": trade_calendar_covered,
                "membership_rows_at_freeze": membership_rows_at_freeze,
                "membership_sha256_at_freeze": membership_sha256_at_freeze,
                "observable_signal_count": observable_signal_count,
                "pending_signal_count": pending_signal_count,
                "latest_pending_signal_date": latest_pending_signal_date,
                "next_pending_execution_date": next_pending_execution_date,
                "first_attribution_requires_close_after": first_attribution_requires_close_after,
                "max_observed_trading_days": max_observed_trading_days,
                "required_forward_trading_days": required_forward_trading_days,
                "remaining_forward_trading_days": remaining_forward_trading_days,
                "forward_observation_status": forward_observation_status,
                "allow_stale": bool(allow_stale),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "message": message,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    atomic_to_csv(pd.DataFrame(rows), summary_path, index=False, encoding="utf-8-sig")
    paths["forward_only_candidate_observation.csv"] = summary_path
    attribution_summary_path = output_dir / "forward_candidate_attribution.csv"
    attribution_columns = ["candidate_config", "candidate_name", *SHADOW_ATTRIBUTION_COLUMNS]
    attribution_summary = (
        pd.concat(attribution_frames, ignore_index=True).reindex(columns=attribution_columns)
        if attribution_frames
        else pd.DataFrame(columns=attribution_columns)
    )
    atomic_to_csv(attribution_summary, attribution_summary_path, index=False, encoding="utf-8-sig")
    paths["forward_candidate_attribution.csv"] = attribution_summary_path
    for name, selection_path in write_forward_candidate_selection(
        config,
        pd.DataFrame(rows),
    ).items():
        paths[name] = selection_path
    return paths



def run_forward_daily(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured = validation.get(
        "forward_only_candidate_configs",
        ["configs/hs300_point_in_time_core.yaml"],
    )
    if not isinstance(configured, list) or not configured:
        raise RuntimeError("No forward-only candidate configuration is registered.")

    paths: dict[str, Path] = {}
    if getattr(args, "refresh", False):
        cache_isolation_ok, cache_isolation_value = forward_candidate_cache_isolation(config, [str(value) for value in configured])
        if not cache_isolation_ok:
            raise RuntimeError(f"Forward candidate cache isolation failed: {cache_isolation_value}")
        for idx, configured_path in enumerate(configured, start=1):
            candidate_config = load_config(project_path(str(configured_path)))
            candidate_validation = candidate_config.get("validation", {})
            if not bool(candidate_validation.get("research_only", False)):
                raise RuntimeError(f"Forward candidate {configured_path} is not research_only.")
            if str(candidate_validation.get("experiment_status", "")) != "preregistered_forward_only":
                raise RuntimeError(f"Forward candidate {configured_path} is not preregistered_forward_only.")
            candidate_config.setdefault("data", {})["force_cached_universe"] = True
            candidate_config["data"]["enrich_metadata"] = False
            store = DataStore.from_config(candidate_config)
            store.init_db()
            print(f"forward-daily: [{idx}/{len(configured)}] refreshing {configured_path}", flush=True)
            for result_path in fetch_akshare(store, candidate_config):
                paths[f"forward-fetch-{idx}:{result_path.name}"] = result_path
            store.log_run(
                "forward_daily_fetch",
                "ok",
                f"candidate={configured_path}; files={len(paths)}",
            )

    print("forward-daily: validating and observing frozen candidates", flush=True)
    for name, result_path in run_forward_only_candidate_observations(
        config,
        allow_stale=getattr(args, "allow_stale", False),
    ).items():
        paths[f"forward-observation:{name}"] = result_path

    summary = read_output_csv(project_path(config["paths"]["output_dir"]) / "forward_only_candidate_observation.csv")
    if summary.empty:
        raise RuntimeError("Forward candidate observation produced no summary rows.")
    failed = summary[summary.get("status", pd.Series("", index=summary.index)).astype(str).str.lower() != "observed"]
    if not failed.empty:
        messages = failed.get("message", pd.Series("forward observation failed", index=failed.index)).astype(str)
        raise RuntimeError("Forward candidate observation failed: " + "; ".join(messages))

    print("forward-daily: selected winner evidence refresh audit", flush=True)
    for name, result_path in refresh_selected_candidate_evidence(config).items():
        paths[f"selected-evidence:{name}"] = result_path
    live_config = load_config(str(getattr(args, "live_config", "configs/huatai_desktop_canary.yaml")))
    for name, result_path in write_deployment_gate(config, live_config).items():
        paths[name] = result_path

    paths["report.html"] = regenerate_report(config)
    paths["control_panel.html"] = generate_control_panel(config)
    print("forward-daily: completed without paper or broker orders", flush=True)
    return paths

def run_practical(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    paths["active_execution_status.csv"] = require_active_execution_consistency(config)
    for name, path in invalidate_execution_artifacts(
        config, reason="practical_cycle_started"
    ).items():
        paths[f"invalidation:{name}"] = path

    if args.refresh:
        store = DataStore.from_config(config)
        store.init_db()
        if args.max_symbols:
            config.setdefault("data", {})["max_symbols"] = args.max_symbols
        if args.no_metadata:
            config.setdefault("data", {})["enrich_metadata"] = False
        if args.cached_universe:
            config.setdefault("data", {})["force_cached_universe"] = True
        for path in fetch_akshare(store, config):
            paths[f"fetch:{path.name}"] = path
        store.log_run("practical_fetch", "ok", f"refreshed {len(paths)} files")

    if args.refresh or readiness_evidence_issues(config):
        for name, path in run_backtest(config).items():
            paths[f"backtest:{name}"] = path

    if not args.skip_stress:
        stress_path = ensure_cost_stress_current(config, guard_name="practical_guard")
        if stress_path is not None:
            paths["cost_stress.csv"] = stress_path

    for name, path in ensure_capital_sensitivity_current(
        config, guard_name="practical_guard"
    ).items():
        paths[name] = path

    paths["practical_readiness.csv"] = write_practical_readiness(config)
    paths["strategy_health.csv"] = write_strategy_health(config)
    if getattr(args, "continue_without_orders_when_blocked", False):
        health = read_output_csv(paths["strategy_health.csv"])
        paper_allowed, health_value_text = strategy_health_allows_paper(health)
        if not paper_allowed:
            print(
                "practical: strategy health blocks paper/RPA orders; continuing observation-only workflow",
                flush=True,
            )
            status_path = project_path(config["paths"]["output_dir"]) / "order_generation_status.csv"
            atomic_to_csv(
                pd.DataFrame([{
                    "status": "skipped_strategy_health",
                    "paper_orders_allowed": False,
                    "orders_generated": False,
                    "reason": health_value(health, "strategy_health_status") or health_value_text,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                }]),
                status_path, index=False, encoding="utf-8-sig",
            )
            paths["order_generation_status.csv"] = status_path
            if not getattr(args, "skip_report", False):
                paths["report.html"] = regenerate_report(config)
            return paths
    if getattr(args, "reset_account", False):
        for name, path in reset_account_state(config, cash=args.reset_cash, confirm=args.confirm_account_reset).items():
            paths[f"account-reset:{name}"] = path
    for name, path in export_account_state(config).items():
        paths[f"account-state:{name}"] = path
    for name, path in run_paper(config, allow_stale=args.allow_stale, practical_mode=True).items():
        paths[f"paper:{name}"] = path
    rpa_path = export_rpa_orders(config)
    paths["rpa_orders.csv"] = rpa_path
    if not getattr(args, "skip_report", False):
        paths["report.html"] = regenerate_report(config)
    if not getattr(args, "quiet", False):
        print(preview_rpa_orders(config, rpa_path))
    return paths


def run_execution_drill(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    drill_config = execution_drill_config(config, output_dir=getattr(args, "output_dir", None))
    paths: dict[str, Path] = {}
    drill_output = project_path(drill_config["paths"]["output_dir"])
    drill_output.mkdir(parents=True, exist_ok=True)
    reset_cash = getattr(args, "reset_cash", None)
    if reset_cash is None:
        reset_cash = default_execution_drill_cash(drill_config)
    drill_config.setdefault("execution_drill", {})["reset_cash"] = reset_cash

    if not getattr(args, "skip_backtest", False):
        for name, path in run_backtest(drill_config).items():
            paths[f"backtest:{name}"] = path
    if not getattr(args, "skip_stress", False):
        paths["cost_stress.csv"] = write_cost_stress(drill_config)
    paths["strategy_backtest_compare.csv"] = compare_backtest_outputs(drill_config, [str(drill_output)])
    paths["practical_readiness.csv"] = write_practical_readiness(drill_config)
    for name, path in reset_account_state(drill_config, cash=reset_cash, confirm=True).items():
        paths[f"account-reset:{name}"] = path
    for name, path in export_account_state(drill_config).items():
        paths[f"account-state:{name}"] = path
    for name, path in run_paper(drill_config, allow_stale=getattr(args, "allow_stale", False), practical_mode=True).items():
        paths[f"paper:{name}"] = path
    paths["strategy_health.csv"] = write_strategy_health(drill_config)
    paths["rpa_orders.csv"] = export_rpa_orders(drill_config)
    paths["execution_readiness.csv"] = write_execution_readiness(drill_config)
    paths["report.html"] = regenerate_report(drill_config)
    paths["control_panel.html"] = generate_control_panel(drill_config)
    paths["execution_drill_summary.csv"] = write_execution_drill_summary(config, drill_config)
    return paths


def default_execution_drill_cash(config: dict) -> float:
    initial_cash = float(config.get("backtest", {}).get("initial_cash", 1000000))
    max_daily = float(config.get("rpa", {}).get("max_daily_order_amount", initial_cash))
    if max_daily <= 0:
        return initial_cash
    return max(0.0, min(initial_cash, max_daily * 0.8))


def execution_drill_config(config: dict, output_dir: str | None = None) -> dict:
    drill_config = deepcopy(config)
    base_output = project_path(config["paths"]["output_dir"])
    drill_output = project_path(output_dir) if output_dir else base_output / "execution_drill"
    drill_config.setdefault("paths", {})["output_dir"] = str(drill_output)
    drill_config["paths"]["state_db"] = str(drill_output / "state.sqlite3")
    drill_config.setdefault("rpa", {})["require_strategy_health"] = True
    return drill_config


def write_execution_drill_summary(source_config: dict, drill_config: dict) -> Path:
    output_dir = project_path(drill_config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    readiness = read_output_csv(output_dir / "execution_readiness.csv")
    tickets = read_output_csv(output_dir / "rpa_orders.csv")
    checks = read_output_csv(output_dir / "rpa_pre_trade_checks.csv")
    health = read_output_csv(output_dir / "strategy_health.csv")
    failed = failed_readiness_rows(readiness)
    execution_failed = rows_with_severity(failed, "execution")
    safety_failed = rows_with_severity(failed, "safety")
    live_failed = rows_with_severity(failed, "live")
    ready_tickets = count_ticket_status(tickets, "ready")
    blocked_tickets = len(tickets) - ready_tickets if not tickets.empty else 0
    pre_trade_passed, pre_trade_value = readiness_counts(checks)
    health_status = health_value(health, "strategy_health_status")
    paper_allowed = health_value(health, "paper_orders_allowed")
    manual_ready = safety_failed.empty and execution_failed.empty and ready_tickets > 0 and blocked_tickets == 0 and pre_trade_passed
    rows = [
        drill_summary_row("isolated_output_dir", True, str(output_dir), "present", "Drill outputs are separated from the main output directory."),
        drill_summary_row("isolated_state_db", True, str(project_path(drill_config["paths"]["state_db"])), "not main state_db", "Drill account reset only touches the isolated SQLite database."),
        drill_summary_row("drill_reset_cash", True, drill_config.get("execution_drill", {}).get("reset_cash", ""), "<= 80% of daily RPA buy cap by default", "Default drill cash is scaled so the isolated rehearsal can pass daily ticket limits."),
        drill_summary_row("paper_orders_allowed", str(paper_allowed).lower() == "true", paper_allowed or "missing", "True", "Strategy health must allow paper/RPA order generation."),
        drill_summary_row("rpa_pre_trade_checks", pre_trade_passed, pre_trade_value, "all checks passed", "RPA tickets must pass local safety and provenance checks."),
        drill_summary_row("ready_ticket_count", ready_tickets > 0 and blocked_tickets == 0, f"ready={ready_tickets}; blocked={blocked_tickets}", "ready>0 and blocked=0", "The drill should produce ready manual-review tickets."),
        drill_summary_row("manual_ticket_review_ready", manual_ready, drill_blocker_names(pd.concat([safety_failed, execution_failed])) or "ready", "no safety/execution blockers", "Manual ticket review route can be rehearsed without touching the main account."),
        drill_summary_row("strict_live_still_guarded", not live_failed.empty, drill_blocker_names(live_failed) or "none", "live blockers remain until forward evidence clears", "The drill proves operations only; it must not bypass anti-overfit live evidence gates."),
        drill_summary_row("source_output_dir", True, str(project_path(source_config["paths"]["output_dir"])), "main outputs preserved", "The main output directory is not used for the drill account state."),
        drill_summary_row("strategy_health_status", bool(health_status), health_status or "missing", "present", "Current health status copied from the isolated drill run."),
    ]
    path = output_dir / "execution_drill_summary.csv"
    atomic_to_csv(pd.DataFrame(rows), path, index=False, encoding="utf-8-sig")
    return path



def drill_blocker_names(frame: pd.DataFrame) -> str:
    if frame.empty or "check" not in frame:
        return ""
    return ";".join(frame["check"].dropna().astype(str).tolist())

def drill_summary_row(check: str, passed: bool, value: object, threshold: object, message: str) -> dict[str, object]:
    return {"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message}


def count_ticket_status(frame: pd.DataFrame, status: str) -> int:
    if frame.empty or "status" not in frame:
        return 0
    return int((frame["status"].astype(str).str.lower() == status).sum())


def readiness_counts(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or "passed" not in frame:
        return False, "missing"
    passed = frame["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    return bool(len(frame) > 0 and passed.all()), f"{int(passed.sum())}/{len(frame)}"


def health_value(frame: pd.DataFrame, check: str) -> str:
    if frame.empty or not {"check", "value"}.issubset(frame.columns):
        return ""
    rows = frame[frame["check"].astype(str) == check]
    if rows.empty:
        return ""
    value = rows["value"].iloc[0]
    return "" if pd.isna(value) else str(value)


def run_observe(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    label = config.get("strategy", {}).get("name", "default")
    if args.refresh:
        print(f"observe {label}: refresh market data started", flush=True)
        store = DataStore.from_config(config)
        store.init_db()
        if args.max_symbols:
            config.setdefault("data", {})["max_symbols"] = args.max_symbols
        if args.no_metadata:
            config.setdefault("data", {})["enrich_metadata"] = False
        if args.cached_universe:
            config.setdefault("data", {})["force_cached_universe"] = True
        for path in fetch_akshare(store, config):
            paths[f"fetch:{path.name}"] = path
        store.log_run("observe_fetch", "ok", f"refreshed {len(paths)} files")
        print(f"observe {label}: refresh market data done", flush=True)
    reuse_enabled = not args.refresh and bool(
        config.get("validation", {}).get("observation_reuse_current_backtest", True)
    )
    backtest_issues = primary_challenger_backtest_issues(config) if reuse_enabled else ["reuse_disabled_or_refreshed"]
    if backtest_issues:
        print(f"observe {label}: backtest started ({';'.join(backtest_issues)})", flush=True)
        for name, path in run_backtest(config).items():
            paths[f"backtest:{name}"] = path
        print(f"observe {label}: backtest done", flush=True)
    else:
        output_dir = project_path(config["paths"]["output_dir"])
        for name in PRIMARY_CHALLENGER_BACKTEST_ARTIFACTS:
            paths[f"backtest:{name}"] = output_dir / name
        print(f"observe {label}: backtest reused (manifest current)", flush=True)
    if not args.skip_stress:
        print(f"observe {label}: cost stress check started", flush=True)
        stress_path = ensure_cost_stress_current(config)
        if stress_path is not None:
            paths["cost_stress.csv"] = stress_path
        print(f"observe {label}: cost stress check done", flush=True)
    else:
        print(f"observe {label}: cost stress skipped", flush=True)
    print(f"observe {label}: shadow signal started", flush=True)
    for name, path in run_shadow_signals(config, allow_stale=args.allow_stale).items():
        paths[f"shadow:{name}"] = path
    print(f"observe {label}: shadow signal done", flush=True)
    print(f"observe {label}: shadow audit started", flush=True)
    paths["shadow_forward_audit.csv"] = run_shadow_audit(config)
    print(f"observe {label}: shadow audit done", flush=True)
    print(f"observe {label}: report started", flush=True)
    paths["report.html"] = regenerate_report(config)
    print(f"observe {label}: report done", flush=True)
    return paths


def run_one_click(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    practical_args = argparse.Namespace(
        refresh=getattr(args, "refresh", False),
        max_symbols=getattr(args, "max_symbols", None),
        cached_universe=getattr(args, "cached_universe", False),
        no_metadata=getattr(args, "no_metadata", False),
        allow_stale=getattr(args, "allow_stale", False),
        skip_stress=getattr(args, "skip_stress", False),
        reset_account=False,
        reset_cash=None,
        confirm_account_reset=False,
        skip_report=True,
        quiet=True,
        continue_without_orders_when_blocked=True,
    )
    print("run: practical workflow started", flush=True)
    for name, path in run_practical(config, practical_args).items():
        paths[f"practical:{name}"] = path

    if not getattr(args, "skip_shadow", False):
        print("run: shadow observation", flush=True)
        for name, path in run_shadow_signals(config, allow_stale=getattr(args, "allow_stale", False)).items():
            paths[f"shadow:{name}"] = path
        paths["shadow_forward_audit.csv"] = run_shadow_audit(config)
        print("run: daily mode skips legacy challenger research", flush=True)
        print("run: forward-only candidate observation", flush=True)
        for name, path in run_forward_only_candidate_observations(
            config,
            allow_stale=getattr(args, "allow_stale", False),
        ).items():
            paths[f"forward-only:{name}"] = path
        print("run: selected winner evidence refresh audit", flush=True)
        for name, path in refresh_selected_candidate_evidence(config).items():
            paths[f"selected-evidence:{name}"] = path
        print("run: daily mode skips exploratory candidate research", flush=True)
    print("run: data source health", flush=True)
    paths["data_source_health.csv"] = write_data_source_health(config)
    print("run: account exclusion impact", flush=True)
    for name, path in write_account_exclusion_impact(config).items():
        paths[f"account-exclusion:{name}"] = path
    print("run: RPA preflight", flush=True)
    paths["rpa_preflight.csv"] = write_rpa_preflight(config)
    print("run: execution readiness", flush=True)
    paths["execution_readiness.csv"] = write_execution_readiness(config)
    print("run: auto-live cycle audit", flush=True)
    for name, path in run_auto_live_cycle(config).items():
        paths[f"auto-live-cycle:{name}"] = path
    print("run: evidence and observation plan", flush=True)
    paths["evidence_plan.csv"] = write_evidence_plan(config)
    paths["observation_schedule.csv"] = write_observation_schedule(config)
    paths["auto_live_remediation_plan.csv"] = write_auto_live_remediation_plan(config)
    paths["auto_live_verification_plan.csv"] = write_auto_live_verification_plan(config)
    if getattr(args, "auto_live_dry_run", False):
        print("run: auto-live dry-run audit", flush=True)
        for name, path in run_auto_live(config, dry_run=True, raise_on_blocked=False).items():
            paths[f"auto-live-dry-run:{name}"] = path
    print("run: report", flush=True)
    paths["report.html"] = regenerate_report(config)
    print("run: dashboard", flush=True)
    paths["control_panel.html"] = generate_control_panel(config)
    paths["one_click_summary.csv"] = write_one_click_summary(config)
    print("run: completed", flush=True)
    return paths


HUATAI_READ_ONLY_REFRESH_COLUMNS = [
    "status",
    "stage",
    "message",
    "snapshot_path",
    "snapshot_status",
    "desktop_stage",
    "hexin_process_count",
    "xiadan_process_count",
    "visible_client_windows",
    "total_client_windows",
    "minimized_client_windows",
    "trading_grid_count",
    "authentication_control_count",
    "xiadan_native_window_count",
    "xiadan_visible_window_count",
    "xiadan_top_level_window_count",
    "window_wake_attempted",
    "window_wake_message",
    "snapshot_attempts",
    "generated_at",
]


HUATAI_BROKER_SNAPSHOT_INVALIDATION_COLUMNS = [
    "generated_at",
    "status",
    "reason",
    "previous_snapshot_run_id",
    "archive_dir",
]


def invalidate_huatai_broker_account_evidence(
    live_config: dict,
    reason: str,
) -> dict[str, Path]:
    """Archive stale broker evidence and publish a current fail-closed marker."""
    output_dir = project_path(live_config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "auto_live_broker_account_snapshot.csv"
    positions_path = output_dir / "auto_live_broker_positions.csv"
    audit_path = output_dir / "auto_live_broker_account_audit.csv"
    invalidation_path = output_dir / "auto_live_broker_snapshot_invalidation.csv"
    previous = read_output_csv(snapshot_path)
    previous_run_id = (
        str(previous.iloc[-1].get("snapshot_run_id", "") or "")
        if not previous.empty
        else ""
    )
    invalidation_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    archive_dir = output_dir / "broker_snapshot_history" / invalidation_id
    archived_any = False
    for source in (snapshot_path, positions_path, audit_path):
        if source.exists():
            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, archive_dir / source.name)
            archived_any = True

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    message = f"Current Huatai broker snapshot invalidated after read-only refresh failure: {reason}"
    invalid_snapshot = pd.DataFrame(
        [
            {
                "snapshot_run_id": f"invalid-{invalidation_id}",
                "broker": "huatai",
                "account_id": "",
                "environment": "unknown",
                "cash": -1.0,
                "available_cash": -1.0,
                "market_value": -1.0,
                "total_asset": -1.0,
                "frozen_cash": -1.0,
                "unclassified_asset_value": -1.0,
                "positions_count": 0,
                "broker_time": "",
                "generated_at": generated_at,
                "auto_live_config_sha256": auto_live_config_sha256(live_config),
                "message": message,
            }
        ],
        columns=AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS,
    )
    invalid_audit = pd.DataFrame(
        [
            {
                "check": "broker_refresh_current_snapshot_valid",
                "passed": False,
                "value": "invalidated",
                "threshold": "latest read-only refresh succeeded",
                "severity": "account_blocker",
                "next_action": "打开完整资金摘要页并重新运行 broker-refresh。",
                "message": message,
            }
        ],
        columns=AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS,
    )
    atomic_to_csv(invalid_snapshot, snapshot_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(pd.DataFrame(columns=AUTO_LIVE_BROKER_POSITION_COLUMNS), positions_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(invalid_audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(
        pd.DataFrame(
            [
                {
                    "generated_at": generated_at,
                    "status": "invalidated",
                    "reason": reason,
                    "previous_snapshot_run_id": previous_run_id,
                    "archive_dir": str(archive_dir.resolve()) if archived_any else "",
                }
            ],
            columns=HUATAI_BROKER_SNAPSHOT_INVALIDATION_COLUMNS,
        ),
        invalidation_path,
        index=False,
        encoding="utf-8-sig",
    )
    return {
        "auto_live_broker_account_snapshot.csv": snapshot_path,
        "auto_live_broker_positions.csv": positions_path,
        "auto_live_broker_account_audit.csv": audit_path,
        "auto_live_broker_snapshot_invalidation.csv": invalidation_path,
    }


def refresh_huatai_canary_read_only_evidence(
    live_config: dict,
    *,
    runner=None,
) -> dict[str, Path]:
    auto = live_config.get("auto_live", {}) if isinstance(live_config.get("auto_live"), dict) else {}
    certification = (
        auto.get("desktop_canary_certification", {})
        if isinstance(auto.get("desktop_canary_certification"), dict)
        else {}
    )
    if not bool(certification.get("refresh_read_only_evidence_during_broker_refresh", False)):
        return {}

    probe = certification.get("preview_probe", {})
    if not isinstance(probe, dict):
        raise ValueError("desktop_canary_certification.preview_probe must be a mapping")
    symbol = str(probe.get("symbol", "") or "").strip()
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError("Canary preview probe symbol must be a six-digit A-share code")
    try:
        price = Decimal(str(probe.get("price", "")))
        quantity_decimal = Decimal(str(probe.get("quantity", "")))
        max_notional = Decimal(str(probe.get("max_notional", "1000")))
    except InvalidOperation as exc:
        raise ValueError("Canary preview probe price, quantity and max_notional must be numeric") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError("Canary preview probe price must be finite and positive")
    if not quantity_decimal.is_finite() or quantity_decimal != quantity_decimal.to_integral_value():
        raise ValueError("Canary preview probe quantity must be a positive integer")
    quantity = int(quantity_decimal)
    if quantity <= 0 or quantity % 100 != 0:
        raise ValueError("Canary preview probe quantity must be a positive 100-share lot")
    if not max_notional.is_finite() or max_notional <= 0 or max_notional > Decimal("1000"):
        raise ValueError("Canary preview probe max_notional must be in (0, 1000]")
    notional = price * quantity_decimal
    if notional > max_notional:
        raise ValueError(
            f"Canary preview probe notional {notional} exceeds configured maximum {max_notional}"
        )

    evidence = certification.get("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("desktop_canary_certification.evidence must be a mapping")
    required = ("buy_preview", "sell_preview", "orders_monitor", "trades_monitor", "cancels_monitor")
    missing = [name for name in required if not str(evidence.get(name, "") or "").strip()]
    if missing:
        raise ValueError(f"Canary read-only evidence paths missing: {', '.join(missing)}")

    commands: list[tuple[str, list[str], Path, str]] = []
    preview_script = str(project_path("scripts/huatai_desktop_preview.py"))
    monitor_script = str(project_path("scripts/huatai_desktop_monitor.py"))
    for side, evidence_key in (("buy", "buy_preview"), ("sell", "sell_preview")):
        output_path = project_path(str(evidence[evidence_key]))
        commands.append(
            (
                evidence_key,
                [
                    sys.executable,
                    preview_script,
                    "--symbol", symbol,
                    "--side", side,
                    "--price", str(price),
                    "--quantity", str(quantity),
                    "--output", str(output_path),
                ],
                output_path,
                "preview_verified",
            )
        )
    for page, evidence_key in (
        ("orders", "orders_monitor"),
        ("trades", "trades_monitor"),
        ("cancels", "cancels_monitor"),
    ):
        output_path = project_path(str(evidence[evidence_key]))
        commands.append(
            (
                evidence_key,
                [sys.executable, monitor_script, "--page", page, "--output", str(output_path)],
                output_path,
                "empty_verified",
            )
        )

    run_process = runner or subprocess.run
    paths: dict[str, Path] = {}
    for evidence_key, command, output_path, expected_status in commands:
        completed = run_process(
            command,
            cwd=project_path("."),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        if output:
            print(output, flush=True)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Huatai read-only Canary {evidence_key} failed: "
                f"{output or f'exit code {completed.returncode}'}"
            )
        payload = read_json_mapping(output_path)
        if (
            str(payload.get("status", "") or "") != expected_status
            or bool(payload.get("submit_invoked", True))
            or bool(payload.get("cancel_invoked", True))
        ):
            raise RuntimeError(
                f"Huatai read-only Canary {evidence_key} returned unsafe or invalid evidence"
            )
        if expected_status == "preview_verified" and not bool(payload.get("form_readback_verified", False)):
            raise RuntimeError(f"Huatai read-only Canary {evidence_key} did not verify form readback")
        paths[f"huatai_{evidence_key}.json"] = output_path
    return paths


def run_huatai_read_only_refresh(
    live_config: dict,
    *,
    retry_window_wake: bool = True,
) -> dict[str, Path]:
    live_output = project_path(live_config["paths"]["output_dir"])
    live_output.mkdir(parents=True, exist_ok=True)
    refresh_path = live_output / "huatai_read_only_refresh.csv"
    snapshot_path = huatai_snapshot_path(live_config)
    paths: dict[str, Path] = {}
    paths["broker:huatai_client_status.csv"] = write_huatai_client_status(live_config, start_if_needed=True)
    stage = "desktop_snapshot"
    payload: dict = {}
    window_wake_attempted = False
    window_wake_message = ""
    snapshot_attempts = 0
    canary_messages: list[str] = []
    wakeable_desktop_stages = {"client_window_absent", "client_window_minimized"}
    retryable_after_wake_stages = wakeable_desktop_stages | {
        "client_window_hidden",
        "client_window_not_visible",
        "trading_module_ui_not_loaded",
        "trading_page_unavailable",
    }

    def collect_desktop_snapshot():
        completed = subprocess.run(
            [
                sys.executable,
                str(project_path("scripts/huatai_desktop_snapshot.py")),
                "--output",
                str(snapshot_path),
            ],
            cwd=project_path("."),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        if output:
            print(output, flush=True)
        payload = read_json_mapping(snapshot_path)
        diagnostics = payload.get("diagnostics", {}) if isinstance(payload.get("diagnostics"), dict) else {}
        desktop_stage = str(diagnostics.get("stage", "") or "").strip().lower()
        return completed, output, payload, desktop_stage

    try:
        completed, output, payload, desktop_stage = collect_desktop_snapshot()
        snapshot_attempts = 1
        client = live_config.get("desktop_client", {})
        startup_race = (
            desktop_stage == "client_process_missing"
            and bool(client.get("auto_start", False))
        )
        should_wait_for_ui = (
            completed.returncode != 0
            and retry_window_wake
            and (desktop_stage in retryable_after_wake_stages or startup_race)
        )
        if should_wait_for_ui:
            if desktop_stage in wakeable_desktop_stages | {"trading_module_ui_not_loaded"}:
                window_wake_attempted, window_wake_message = request_huatai_client_window_wake(live_config)
            wake_wait = max(float(client.get("window_wake_wait_seconds", 3)), 0.0)
            ready_timeout = max(float(client.get("window_ready_timeout_seconds", 30)), wake_wait)
            poll_seconds = max(float(client.get("window_ready_poll_seconds", 2)), 0.25)
            max_attempts = max(int(client.get("window_ready_max_attempts", 10)), 2)
            deadline = time.monotonic() + ready_timeout
            time.sleep(wake_wait)
            while snapshot_attempts < max_attempts:
                completed, output, payload, desktop_stage = collect_desktop_snapshot()
                snapshot_attempts += 1
                if completed.returncode == 0 or desktop_stage not in retryable_after_wake_stages:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(poll_seconds, remaining))
            paths["broker:huatai_client_status.csv"] = write_huatai_client_status(
                live_config, start_if_needed=False
            )
            if window_wake_message and not window_wake_attempted:
                output = f"{output}; wake not attempted: {window_wake_message}".strip("; ")
        if completed.returncode != 0:
            raise RuntimeError(output or f"snapshot collector exited {completed.returncode}")
        if str(payload.get("status", "")).lower() != "ok":
            raise RuntimeError(str(payload.get("message", "")) or "snapshot payload is not successful")

        stage = "canary_read_only_evidence"
        try:
            for name, result_path in refresh_huatai_canary_read_only_evidence(live_config).items():
                paths[f"broker:{name}"] = result_path
        except RuntimeError as exc:
            canary_messages.append(str(exc))

        stage = "account_audit"
        for name, result_path in query_auto_live_account(live_config, raise_on_blocked=False).items():
            paths[f"broker:{name}"] = result_path
        stage = "account_history"
        for name, result_path in write_broker_readonly_history(live_config).items():
            paths[f"broker:{name}"] = result_path
        stage = "account_preview"
        account_preview_paths = write_broker_account_import_preview(live_config)
        for name, result_path in account_preview_paths.items():
            paths[f"broker:{name}"] = result_path
        account_preview_audit_path = account_preview_paths.get("broker_account_import_preview_audit.csv")
        account_preview_audit = (
            read_output_csv(account_preview_audit_path)
            if account_preview_audit_path is not None
            else pd.DataFrame()
        )
        if not check_passed(account_preview_audit, "broker_account_preview_ready"):
            failed = failing_checks(account_preview_audit) or "broker_account_preview_ready"
            raise RuntimeError(f"Huatai broker account preview blocked: {failed}")
        state_db_value = str(live_config.get("paths", {}).get("state_db", "") or "").strip()
        if state_db_value and not project_path(state_db_value).exists():
            stage = "account_initialization_preview"
            account_snapshot = read_output_csv(live_output / "auto_live_broker_account_snapshot.csv")
            snapshot_row = account_snapshot.iloc[0] if len(account_snapshot) == 1 else pd.Series(dtype=object)
            initialization_paths = initialize_broker_account_from_snapshot(
                live_config,
                expected_snapshot_run_id=str(snapshot_row.get("snapshot_run_id", "") or ""),
                expected_account_id=str(snapshot_row.get("account_id", "") or ""),
                confirm=False,
            )
            for name, result_path in initialization_paths.items():
                paths[f"broker:{name}"] = result_path
        stage = "capital_feasibility"
        for name, result_path in write_broker_capital_feasibility(live_config).items():
            paths[f"broker:{name}"] = result_path
        stage = "canary_capital"
        for name, result_path in write_broker_canary_capital_feasibility(live_config).items():
            paths[f"broker:{name}"] = result_path
        stage = "order_preview"
        for name, result_path in write_broker_order_preview(live_config).items():
            paths[f"broker:{name}"] = result_path
        stage = "canary_audit"
        try:
            paths["broker:huatai_execution_sandbox_summary.json"] = write_huatai_execution_sandbox_summary(live_config)
        except RuntimeError as exc:
            # Canary readiness is stricter than a read-only account refresh. Keep its
            # fail-closed evidence without misreporting a valid broker snapshot as failed.
            canary_messages.append(str(exc))
        paths["broker:huatai_desktop_canary_audit.csv"] = write_huatai_canary_arming_audit(live_config)
        paths["broker:report.html"] = regenerate_report(live_config)
        message = "Huatai read-only account evidence refreshed."
        if canary_messages:
            message += " Canary remains blocked: " + " | ".join(canary_messages)
        atomic_to_csv(
            pd.DataFrame([huatai_read_only_refresh_row("ok", "completed", message, snapshot_path, payload, window_wake_attempted=window_wake_attempted, window_wake_message=window_wake_message, snapshot_attempts=snapshot_attempts)], columns=HUATAI_READ_ONLY_REFRESH_COLUMNS),
            refresh_path,
            index=False,
            encoding="utf-8-sig",
        )
        paths["broker:huatai_read_only_refresh.csv"] = refresh_path
        return paths
    except Exception as exc:
        if not payload:
            payload = read_json_mapping(snapshot_path)
        if stage == "desktop_snapshot":
            for name, result_path in invalidate_huatai_broker_account_evidence(
                live_config, f"{type(exc).__name__}: {exc}"
            ).items():
                paths[f"broker:{name}"] = result_path
        atomic_to_csv(
            pd.DataFrame([huatai_read_only_refresh_row("failed", stage, f"{type(exc).__name__}: {exc}", snapshot_path, payload, window_wake_attempted=window_wake_attempted, window_wake_message=window_wake_message, snapshot_attempts=snapshot_attempts)], columns=HUATAI_READ_ONLY_REFRESH_COLUMNS),
            refresh_path,
            index=False,
            encoding="utf-8-sig",
        )
        try:
            write_huatai_canary_arming_audit(live_config)
            regenerate_report(live_config)
        except Exception as audit_exc:
            print(f"broker-refresh: audit refresh failed: {type(audit_exc).__name__}: {audit_exc}", flush=True)
        raise


def huatai_read_only_refresh_row(
    status: str,
    stage: str,
    message: str,
    snapshot_path: Path,
    payload: dict,
    *,
    window_wake_attempted: bool = False,
    window_wake_message: str = "",
    snapshot_attempts: int = 1,
) -> dict[str, object]:
    diagnostics = payload.get("diagnostics", {}) if isinstance(payload.get("diagnostics"), dict) else {}
    return {
        "status": status,
        "stage": stage,
        "message": message,
        "snapshot_path": str(snapshot_path.resolve()),
        "snapshot_status": str(payload.get("status", "") or "missing"),
        "desktop_stage": str(diagnostics.get("stage", "") or "unknown"),
        "hexin_process_count": int(diagnostics.get("hexin_process_count", 0) or 0),
        "xiadan_process_count": int(diagnostics.get("xiadan_process_count", 0) or 0),
        "visible_client_windows": int(diagnostics.get("visible_client_windows", 0) or 0),
        "total_client_windows": int(diagnostics.get("total_client_windows", 0) or 0),
        "minimized_client_windows": int(diagnostics.get("minimized_client_windows", 0) or 0),
        "trading_grid_count": int(diagnostics.get("trading_grid_count", 0) or 0),
        "authentication_control_count": int(diagnostics.get("authentication_control_count", 0) or 0),
        "xiadan_native_window_count": int(diagnostics.get("xiadan_native_window_count", 0) or 0),
        "xiadan_visible_window_count": int(diagnostics.get("xiadan_visible_window_count", 0) or 0),
        "xiadan_top_level_window_count": int(diagnostics.get("xiadan_top_level_window_count", 0) or 0),
        "window_wake_attempted": bool(window_wake_attempted),
        "window_wake_message": str(window_wake_message or ""),
        "snapshot_attempts": max(int(snapshot_attempts), 0),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def read_json_mapping(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def run_broker_refresh(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    live_config = load_config(str(getattr(args, "live_config", "configs/huatai_desktop_canary.yaml")))
    try:
        paths = run_huatai_read_only_refresh(live_config)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        status_paths = write_local_operator_status(config, live_config, phase_errors={"broker": message})
        status_path = status_paths.get("local_operator_status.html")
        raise RuntimeError(f"broker-refresh blocked: {message}; status={status_path}") from exc
    paths.update(write_local_operator_status(config, live_config))
    print("broker-refresh: completed without broker submission", flush=True)
    return paths


def refresh_live_deployment_evidence(
    strategy_config: dict,
    live_config: dict,
) -> dict[str, Path]:
    """Refresh the live audit workspace without generating or submitting orders."""
    paths = sync_live_strategy_evidence(strategy_config, live_config)
    paths["live:candidate_validation.csv"] = run_candidate_audit(live_config)
    paths["live:report.html"] = regenerate_report(live_config)
    paths["live:control_panel.html"] = generate_control_panel(live_config)
    return paths


def run_local_system(config: dict, args: argparse.Namespace) -> dict[str, Path]:
    """Run the deterministic local operator workflow without any submit capability."""
    paths: dict[str, Path] = {}
    errors: dict[str, str] = {}
    strategy_completed = False
    strategy_args = argparse.Namespace(
        refresh=bool(getattr(args, "refresh", False)),
        max_symbols=getattr(args, "max_symbols", None),
        cached_universe=bool(getattr(args, "cached_universe", False)),
        no_metadata=bool(getattr(args, "no_metadata", False)),
        allow_stale=bool(getattr(args, "allow_stale", False)),
        skip_stress=bool(getattr(args, "skip_stress", False)),
        auto_live_dry_run=False,
        skip_shadow=bool(getattr(args, "skip_shadow", False)),
    )
    print("local-run: strategy preflight", flush=True)
    health_path = write_data_source_health(config)
    paths["strategy:data_source_health.csv"] = health_path
    stale_message = local_run_stale_cache_message(health_path)
    if stale_message and not strategy_args.refresh:
        for name, path in invalidate_execution_artifacts(
            config, reason="local_run_stale_market_data"
        ).items():
            paths[f"invalidation:{name}"] = path
        errors["strategy"] = stale_message
        print(f"local-run: strategy blocked before calculation: {stale_message}", flush=True)
    else:
        print("local-run: strategy workflow started", flush=True)
        try:
            for name, path in run_one_click(config, strategy_args).items():
                paths[f"strategy:{name}"] = path
            strategy_completed = True
        except Exception as exc:
            errors["strategy"] = f"{type(exc).__name__}: {exc}"
            print(f"local-run: strategy blocked: {errors['strategy']}", flush=True)

    live_config_path = str(getattr(args, "live_config", "configs/huatai_desktop_canary.yaml"))
    live_config = load_config(live_config_path)
    if strategy_completed:
        print("local-run: verified strategy evidence publish started", flush=True)
        try:
            paths.update(refresh_live_deployment_evidence(config, live_config))
        except Exception as exc:
            errors["live_evidence"] = f"{type(exc).__name__}: {exc}"
            print(f"local-run: live evidence publish blocked: {errors['live_evidence']}", flush=True)
    if not bool(getattr(args, "skip_broker", False)):
        print("local-run: Huatai read-only refresh started", flush=True)
        broker_optional = bool(getattr(args, "broker_optional", False))
        try:
            paths["broker:huatai_client_status.csv"] = write_huatai_client_status(live_config, start_if_needed=True)
            paths.update(
                run_huatai_read_only_refresh(
                    live_config, retry_window_wake=not broker_optional
                )
            )
        except Exception as exc:
            broker_message = f"{type(exc).__name__}: {exc}"
            if broker_optional and huatai_client_unavailable_error(broker_message):
                print("local-run: Huatai client offline; strategy outputs saved and broker refresh remains blocked", flush=True)
            else:
                errors["broker"] = broker_message
                print(f"local-run: broker read-only audit blocked: {errors['broker']}", flush=True)
    else:
        print("local-run: broker refresh skipped; existing read-only evidence retained", flush=True)

    print("local-run: deployment gate refresh", flush=True)
    try:
        for name, path in write_deployment_gate(config, live_config).items():
            paths[f"deployment:{name}"] = path
    except Exception as exc:
        print(f"local-run: deployment gate refresh blocked: {type(exc).__name__}: {exc}", flush=True)

    status_paths = write_local_operator_status(config, live_config, phase_errors=errors)
    for name, path in status_paths.items():
        paths[name] = path
    if errors:
        details = "; ".join(f"{phase}={message}" for phase, message in errors.items())
        status_path = status_paths.get("local_operator_status.html")
        raise RuntimeError(f"local-run blocked: {details}; status={status_path}")
    print("local-run: completed without broker submission", flush=True)
    return paths


def huatai_client_unavailable_error(message: object) -> bool:
    text = str(message or "").strip().lower()
    return any(token in text for token in (
        "client_process_missing",
        "client missing",
        "client window not found",
        "visible huatai client window not found",
        "process has no desktop window",
    ))


def local_run_stale_cache_message(path: Path) -> str:
    health = read_output_csv(path)
    if health.empty or not {"check", "passed"}.issubset(health.columns):
        return ""
    rows = health[health["check"].astype(str) == "price_cache_age_days"]
    if rows.empty:
        return ""
    passed = str(rows.iloc[0]["passed"]).strip().lower() in {"true", "1", "yes"}
    if passed:
        return ""
    value = rows.iloc[0].get("value", "unknown")
    threshold = rows.iloc[0].get("threshold", "unknown")
    return f"market data cache is stale (age={value}, limit={threshold}); rerun local-run with --refresh"


def huatai_snapshot_path(config: dict) -> Path:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live"), dict) else {}
    cert = auto.get("desktop_canary_certification", {}) if isinstance(auto.get("desktop_canary_certification"), dict) else {}
    evidence = cert.get("evidence", {}) if isinstance(cert.get("evidence"), dict) else {}
    value = str(evidence.get("account_snapshot", "outputs_live/huatai_account_snapshot.json"))
    return project_path(value)


def write_one_click_summary(config: dict) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    auto_summary = read_output_csv(output_dir / "auto_live_summary.csv")
    evidence = read_output_csv(output_dir / "evidence_plan.csv")
    remediation = read_output_csv(output_dir / "auto_live_remediation_plan.csv")
    execution = read_output_csv(output_dir / "execution_readiness.csv")
    challenger = read_output_csv(output_dir / "primary_challenger_observation.csv")
    health = read_output_csv(output_dir / "strategy_health.csv")
    promotion_decision = read_output_csv(output_dir / "strategy_promotion_decision.csv")
    formal_candidate = read_output_csv(output_dir / "forward_only_candidate_observation.csv")
    observation_task = config.get("observation_task", {}) if isinstance(config.get("observation_task"), dict) else {}
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    formal_ok, formal_value, _ = _formal_candidate_observation_status(
        formal_candidate,
        max_age_seconds=int(float(observation_task.get("max_age_hours", 36)) * 3600),
        expected_configs=validation.get("forward_only_candidate_configs", []),
    )
    expected_challenger_config = str(validation.get("primary_challenger_config", "") or "").strip()
    challenger_configured = bool(expected_challenger_config)
    challenger_status = frame_first(challenger, "status") if challenger_configured else "not_configured"
    observed_challenger_config = frame_first(challenger, "candidate_config")
    challenger_identity_ok = (
        not challenger_configured
        or _same_project_path(expected_challenger_config, observed_challenger_config)
    )
    challenger_ok = not challenger_configured or (challenger_status == "observed" and challenger_identity_ok)
    operating_mode, operating_message = strategy_operating_mode(
        health,
        promotion_decision,
        challenger_ok,
        formal_candidate_observation=formal_candidate,
        formal_candidate_observation_ok=formal_ok,
    )
    strategy_health_status = health_value(health, "strategy_health_status") or "missing"
    paper_orders_allowed = health_value(health, "paper_orders_allowed") or "missing"
    auto_allowed = bool_frame_value(auto_summary, "auto_live_allowed")
    blockers = frame_first(auto_summary, "blocker_details") or frame_first(auto_summary, "blockers") or failing_checks(execution)
    next_action = remediation_next_action(remediation) or prioritized_next_action(evidence) or "查看 outputs/control_panel.html"
    challenger_message = frame_first(challenger, "message")
    if challenger_configured and not challenger_ok:
        if challenger_status == "observed" and not challenger_identity_ok:
            challenger_message = "主候选配置已变更；旧观察结果不可复用，请重新运行一键流程。"
        elif not challenger_message:
            challenger_message = "主候选观察结果缺失；重新运行一键流程。"
        if not blockers:
            next_action = challenger_message
    status = "auto_live_ready" if auto_allowed else ("manual_review_only" if not blockers else "blocked")
    if not health.empty and operating_mode == "successor_observation":
        status = "observation_only"
        next_action = operating_message
    elif not health.empty and operating_mode == "strategy_halted":
        status = "strategy_blocked"
        next_action = operating_message
    elif challenger_configured and not challenger_ok and status != "blocked":
        status = "observation_degraded"
    row = {
        "status": status,
        "auto_live_allowed": auto_allowed,
        "blockers": blockers,
        "next_action": next_action,
        "strategy_operating_mode": operating_mode,
        "strategy_operating_message": operating_message,
        "strategy_health_status": strategy_health_status,
        "paper_orders_allowed": paper_orders_allowed,
        "challenger_observation_ok": challenger_ok,
        "challenger_status": challenger_status or "missing",
        "challenger_name": frame_first(challenger, "candidate_name"),
        "challenger_config_match": challenger_identity_ok,
        "challenger_backtest_mode": frame_first(challenger, "backtest_mode"),
        "challenger_backtest_reason": frame_first(challenger, "backtest_reason"),
        "challenger_message": challenger_message,
        "challenger_generated_at": frame_first(challenger, "generated_at"),
        "formal_candidate_observation_ok": formal_ok,
        "formal_candidate_observation": formal_value,
        "report": str((output_dir / "report.html").resolve()),
        "control_panel": str((output_dir / "control_panel.html").resolve()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    path = output_dir / "one_click_summary.csv"
    atomic_to_csv(pd.DataFrame([row]), path, index=False, encoding="utf-8-sig")
    return path


def bool_frame_value(frame: pd.DataFrame, column: str) -> bool:
    if frame.empty or column not in frame:
        return False
    return str(frame[column].iloc[0]).strip().lower() in {"true", "1", "yes"}


def _same_project_path(left: object, right: object) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if not left_value or not right_value:
        return False
    try:
        return project_path(left_value).resolve() == project_path(right_value).resolve()
    except OSError:
        return left_value.replace("\\", "/").casefold() == right_value.replace("\\", "/").casefold()


def frame_first(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame:
        return ""
    value = frame[column].iloc[0]
    return "" if pd.isna(value) else str(value)


def failing_checks(frame: pd.DataFrame) -> str:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return ""
    passed = frame["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    return ";".join(frame.loc[~passed, "check"].dropna().astype(str).tolist())


def first_next_action(frame: pd.DataFrame) -> str:
    if frame.empty or "next_action" not in frame:
        return ""
    candidates = frame["next_action"].dropna().astype(str)
    candidates = candidates[candidates.str.strip() != ""]
    return "" if candidates.empty else candidates.iloc[0]


def prioritized_next_action(frame: pd.DataFrame) -> str:
    if frame.empty or "next_action" not in frame.columns:
        return ""
    if "area" not in frame.columns:
        return first_next_action(frame)
    priority = ["account_audit", "rpa_pre_trade", "auto_live_config", "strict_live_gate", "forward_validation", "shadow_observation"]
    for area in priority:
        rows = frame[frame["area"].astype(str) == area]
        if rows.empty:
            continue
        status = str(rows.iloc[0].get("status", ""))
        action = str(rows.iloc[0].get("next_action", "") or "").strip()
        if action and status != "passed":
            return action
    return first_next_action(frame)


def remediation_next_action(frame: pd.DataFrame) -> str:
    if frame.empty or "next_action" not in frame.columns:
        return ""
    data = frame.copy()
    if "priority" in data.columns:
        data["priority"] = pd.to_numeric(data["priority"], errors="coerce").fillna(9999)
        data = data.sort_values("priority")
    for row in data.itertuples(index=False):
        action = str(getattr(row, "next_action", "") or "").strip()
        if action:
            return action
    return ""


def run_workflow(config: dict, skip_stress: bool = False) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    total_stages = 9 if skip_stress else 10
    stage = 0

    def progress(label: str) -> None:
        nonlocal stage
        stage += 1
        print(f"workflow: [{stage}/{total_stages}] {label}", flush=True)

    progress("research candidates")
    research_path = run_strategy_research(config)
    paths["strategy_research.csv"] = research_path
    progress("factor baselines")
    baseline_path = run_strategy_baselines(config)
    paths["strategy_baselines.csv"] = baseline_path
    progress("main backtest")
    for name, path in run_backtest(config).items():
        paths[f"backtest:{name}"] = path
    progress("factor robustness")
    paths.update(run_factor_robustness(config))
    if not skip_stress:
        progress("cost stress")
        paths["cost_stress.csv"] = write_cost_stress(config)
    progress("capital sensitivity")
    paths.update(ensure_capital_sensitivity_current(config))
    progress("practical readiness")
    paths["practical_readiness.csv"] = write_practical_readiness(config)
    progress("strategy health")
    paths["strategy_health.csv"] = write_strategy_health(config)
    progress("strategy comparison")
    compare_path = compare_backtest_outputs(config)
    paths["strategy_backtest_compare.csv"] = compare_path
    progress("HTML report")
    paths["report.html"] = regenerate_report(config)
    return paths


def ensure_capital_sensitivity_current(
    config: dict,
    guard_name: str = "practical_guard",
) -> dict[str, Path]:
    guard = config.get(guard_name, {})
    if not guard.get("require_capital_sensitivity", False):
        return {}
    if not capital_sensitivity_needs_refresh(config):
        print("capital-sensitivity: current evidence reused", flush=True)
        return {}
    print("capital-sensitivity: stale evidence; refreshing isolated capital levels", flush=True)
    return run_capital_sensitivity(config)


def ensure_cost_stress_current(config: dict, guard_name: str = "live_guard") -> Path | None:
    guard = config.get(guard_name, {})
    if guard_name == "practical_guard" and not guard:
        guard = config.get("live_guard", {})
    if not guard.get("require_cost_stress", False):
        return None
    pruned_path = prune_stale_cost_stress_rows(config)
    if not cost_stress_needs_refresh(config, guard_name=guard_name):
        return pruned_path
    scenario = str(guard.get("cost_stress_scenario", "high_cost"))
    return write_cost_stress(config, scenario_names=[scenario], merge_existing=True)


def prune_stale_cost_stress_rows(config: dict) -> Path | None:
    output_dir = project_path(config["paths"]["output_dir"])
    output = output_dir / "cost_stress.csv"
    manifest_path = output_dir / "run_manifest.csv"
    if not output.exists() or not manifest_path.exists():
        return None
    existing = read_output_csv(output)
    manifest = read_output_csv(manifest_path)
    if existing.empty or manifest.empty:
        return None
    manifest_values = {
        str(row.get("item", "")): "" if pd.isna(row.get("value")) else str(row.get("value"))
        for row in manifest.to_dict("records")
    }
    expected = {
        "config_sha256": backtest_config_sha256(config),
        "stocks_sha256": manifest_values.get("stocks_sha256", ""),
        "prices_sha256": manifest_values.get("prices_sha256", ""),
        "fundamentals_sha256": manifest_values.get("fundamentals_sha256", ""),
        "benchmark_sha256": manifest_values.get("benchmark_sha256", ""),
    }
    current = existing.copy()
    for column, value in expected.items():
        if not value or column not in current.columns:
            return None
        current = current[current[column].fillna("").astype(str) == value].copy()
    if len(current) == len(existing):
        return None
    atomic_to_csv(current, output, index=False, encoding="utf-8-sig")
    return output


def write_cost_stress(config: dict, scenario_names: Iterable[str] | None = None, merge_existing: bool = False) -> Path:
    result = run_cost_stress(config, scenario_names=scenario_names)
    output = project_path(config["paths"]["output_dir"]) / "cost_stress.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    if merge_existing and output.exists() and scenario_names is not None:
        existing = read_output_csv(output)
        selected = {str(name) for name in scenario_names}
        if not existing.empty and "scenario" in existing.columns:
            signature_columns = [
                "config_sha256",
                "stocks_sha256",
                "prices_sha256",
                "fundamentals_sha256",
                "benchmark_sha256",
            ]
            for column in signature_columns:
                if column not in result.columns or column not in existing.columns or result.empty:
                    continue
                expected = "" if pd.isna(result[column].iloc[0]) else str(result[column].iloc[0])
                if expected:
                    existing = existing[existing[column].fillna("").astype(str) == expected].copy()
            existing = existing[~existing["scenario"].astype(str).isin(selected)].copy()
            result = pd.concat([existing, result], ignore_index=True, sort=False)
    atomic_to_csv(result, output, index=False, encoding="utf-8-sig")
    return output


def cost_stress_needs_refresh(config: dict, guard_name: str = "live_guard") -> bool:
    output_dir = project_path(config["paths"]["output_dir"])
    stress_path = output_dir / "cost_stress.csv"
    manifest_path = output_dir / "run_manifest.csv"
    if not stress_path.exists() or not manifest_path.exists():
        return True
    try:
        stress = pd.read_csv(stress_path)
        manifest = pd.read_csv(manifest_path)
    except EmptyDataError:
        return True
    if stress.empty or manifest.empty:
        return True
    guard = config.get(guard_name, {})
    if guard_name == "practical_guard" and not guard:
        guard = config.get("live_guard", {})
    values = cost_stress_values(
        stress,
        str(guard.get("cost_stress_scenario", "high_cost")),
        config_sha256=backtest_config_sha256(config),
        require_current=bool(guard.get("require_current_cost_stress", True)),
        run_manifest=manifest,
    )
    if bool(guard.get("require_current_cost_stress", True)):
        return values["config_hash_match"] is not True or values["data_hash_match"] is not True
    return values["excess_total_return"] is None or values["max_drawdown"] is None


def run_candidate_audit(config: dict) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = output_dir / "benchmark_comparison.csv"
    try:
        comparison = pd.read_csv(comparison_path) if comparison_path.exists() else pd.DataFrame()
    except EmptyDataError:
        comparison = pd.DataFrame()
    manifest = load_candidate_manifest(config)
    candidate = candidate_frame(manifest)
    atomic_to_csv(candidate, output_dir / "live_candidate.csv", index=False, encoding="utf-8-sig")
    shadow_audit = (
        read_output_csv(output_dir / "shadow_forward_audit.csv")
        if manifest and str(manifest.get("freeze_mode", "")) == "forward_only_preregistration"
        else None
    )
    validation = build_candidate_validation(config, comparison, shadow_audit=shadow_audit)
    path = candidate_validation_path(config)
    atomic_to_csv(validation, path, index=False, encoding="utf-8-sig")
    progress = build_candidate_forward_progress(config, comparison, shadow_audit=shadow_audit)
    atomic_to_csv(progress, output_dir / "live_candidate_forward_progress.csv", index=False, encoding="utf-8-sig")
    record_candidate_failure(config, progress)
    live_readiness = build_live_readiness_from_outputs(config)
    atomic_to_csv(live_readiness, output_dir / "live_readiness.csv", index=False, encoding="utf-8-sig")
    practical_readiness = build_practical_readiness_from_outputs(config)
    atomic_to_csv(practical_readiness, output_dir / "practical_readiness.csv", index=False, encoding="utf-8-sig")
    roadmap = build_live_readiness_roadmap(live_readiness, config)
    atomic_to_csv(roadmap, output_dir / "live_readiness_roadmap.csv", index=False, encoding="utf-8-sig")
    write_strategy_health(config)
    return path


def write_readiness_roadmap(config: dict) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    roadmap = build_live_readiness_roadmap(build_live_readiness_from_outputs(config), config)
    path = output_dir / "live_readiness_roadmap.csv"
    atomic_to_csv(roadmap, path, index=False, encoding="utf-8-sig")
    return path


def write_practical_readiness(config: dict) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    readiness = build_practical_readiness_from_outputs(config)
    path = output_dir / "practical_readiness.csv"
    atomic_to_csv(readiness, path, index=False, encoding="utf-8-sig")
    return path


def write_execution_readiness(config: dict) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    orders = read_output_csv(output_dir / "orders.csv")
    source = order_source_diagnostics(output_dir, orders, config)
    atomic_to_csv(source, output_dir / "order_source_diagnostics.csv", index=False, encoding="utf-8-sig")
    practical_readiness = build_practical_readiness_from_outputs(config)
    atomic_to_csv(practical_readiness, output_dir / "practical_readiness.csv", index=False, encoding="utf-8-sig")
    decision_path = write_strategy_promotion_decision(config)
    health_path = write_strategy_health(config)
    rpa_preflight_path = write_rpa_preflight(config)
    readiness = build_execution_readiness(
        config,
        practical_readiness,
        build_live_readiness_from_outputs(config),
        source,
        read_output_csv(output_dir / "account_reconciliation.csv"),
        read_output_csv(output_dir / "account_state_audit.csv"),
        read_output_csv(output_dir / "rpa_pre_trade_checks.csv"),
        read_output_csv(output_dir / "rpa_orders.csv"),
        orders,
        strategy_decision=read_output_csv(decision_path),
        strategy_health=read_output_csv(health_path),
        rpa_preflight=read_output_csv(rpa_preflight_path),
    )
    path = output_dir / "execution_readiness.csv"
    account_paths = build_account_path_decision(config)
    atomic_to_csv(readiness, path, index=False, encoding="utf-8-sig")
    atomic_to_csv(build_execution_roadmap(readiness), output_dir / "execution_readiness_roadmap.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(build_execution_modes(readiness), output_dir / "execution_modes.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(account_paths, output_dir / "account_path_decision.csv", index=False, encoding="utf-8-sig")
    atomic_to_csv(build_execution_playbook(readiness, account_paths), output_dir / "execution_playbook.csv", index=False, encoding="utf-8-sig")
    return path


def print_execution_readiness_summary(path: Path) -> None:
    readiness = read_output_csv(path)
    print(f"Execution readiness: {execution_readiness_level(readiness)}")
    if readiness.empty or "passed" not in readiness:
        return
    failed = failed_readiness_rows(readiness)
    if failed.empty:
        return
    ordered = pd.concat(
        [
            rows_with_severity(failed, "safety"),
            rows_with_severity(failed, "execution"),
            rows_with_severity(failed, "live"),
            failed[
                ~failed.index.isin(
                    pd.concat(
                        [
                            rows_with_severity(failed, "safety"),
                            rows_with_severity(failed, "execution"),
                            rows_with_severity(failed, "live"),
                        ]
                    ).index
                )
            ],
        ]
    )
    for row in ordered.head(5).itertuples(index=False):
        next_action = getattr(row, "next_action", "")
        print(f"  - {row.check}: {row.value} / {row.threshold}; next={next_action}")
    roadmap_path = path.with_name("execution_readiness_roadmap.csv")
    roadmap = read_output_csv(roadmap_path)
    if not roadmap.empty:
        print("Next steps:")
        for row in roadmap.head(5).itertuples(index=False):
            print(f"  {row.priority}. [{row.area}] {row.command} - {row.next_action}")
    modes = read_output_csv(path.with_name("execution_modes.csv"))
    if not modes.empty:
        print("Modes:")
        for row in modes.itertuples(index=False):
            print(f"  - {row.mode}: {row.status}; blockers={row.blockers}")


def print_account_plan_summary(path: Path) -> None:
    plan = read_output_csv(path)
    if plan.empty:
        return
    for row in plan.head(5).itertuples(index=False):
        print(f"  {row.priority}. [{row.path}] {row.command} - {row.next_action}")


def print_account_path_summary(path: Path) -> None:
    decisions = read_output_csv(path)
    if decisions.empty:
        return
    for row in decisions.itertuples(index=False):
        print(f"  [{row.path}] {row.status}: {row.command}")


def print_account_import_check_summary(path: Path) -> None:
    audit = read_output_csv(path)
    if audit.empty or "passed" not in audit:
        print("Account import check: invalid")
        return
    passed = audit["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    print(f"Account import check: {'passed' if bool(passed.all()) else 'blocked'} ({int(passed.sum())}/{len(audit)} checks)")
    failed = audit.loc[~passed]
    for row in failed.head(5).itertuples(index=False):
        print(f"  - {row.check}: {row.value} / {row.threshold}; {row.message}")


def read_output_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()

def print_data_source_health_summary(health: pd.DataFrame) -> None:
    failed = health_failed_rows(health)
    print(f"Data source health: {'ok' if failed.empty else 'attention'} ({len(health) - len(failed)}/{len(health)} checks)")
    for row in failed.head(5).itertuples(index=False):
        print(f"  - {row.check}: {row.value} / {row.threshold}; next={row.next_action}; cmd={row.command}")

def report_render_issues(config: dict) -> list[str]:
    report_path = project_path(config["paths"]["output_dir"]) / "report.html"
    if not report_path.exists():
        return ["report.html missing"]
    text = report_path.read_text(encoding="utf-8", errors="replace")
    required_terms = ["A 股量化交易系统报告", "策略收益", "严格实盘", "实用状态"]
    suspicious_terms = ["����", "鑲", "鍖", "瀹", "绛", "�"]
    issues = [f"missing Chinese term: {term}" for term in required_terms if term not in text]
    for term in suspicious_terms:
        count = text.count(term)
        if count:
            issues.append(f"possible mojibake marker {term!r}: {count}")
    return issues


def run_doctor(config: dict) -> None:
    errors = validate_config(config)
    print("Dependencies:")
    for name, ok in dependency_status().items():
        print(f"  {name}: {'ok' if ok else 'missing'}")
    print("Optional dependencies:")
    for name, ok in optional_dependency_status().items():
        print(f"  {name}: {'ok' if ok else 'missing'}")
    if errors:
        print("Config errors:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("Config: ok")

    store = DataStore.from_config(config)
    try:
        stocks, prices, fundamentals = store.load_market_data()
    except FileNotFoundError as exc:
        print(f"Cached data: missing ({exc})")
        return
    data_errors = validate_market_data(stocks, prices, fundamentals)
    if data_errors:
        print("Cached data errors:")
        for error in data_errors:
            print(f"  - {error}")
    else:
        print(f"Cached data: ok ({len(stocks)} stocks, {len(prices)} price rows)")
    benchmark = store.load_benchmark(required=False)
    if benchmark.empty:
        print("Benchmark: missing")
    else:
        print(f"Benchmark: ok ({len(benchmark)} rows, {benchmark['name'].iloc[-1]})")
    print_complete_market_status(prices, benchmark, config)
    diagnostic_prices, diagnostic_benchmark = filter_complete_market_data(prices, benchmark, config)
    if diagnostic_benchmark is None:
        diagnostic_benchmark = benchmark
    quality = data_quality_report(stocks, diagnostic_prices, fundamentals, diagnostic_benchmark, config, raw_prices=prices, raw_benchmark=benchmark)
    warnings = quality[quality["severity"] == "warning"]
    if not warnings.empty:
        print("Data quality warnings:")
        for row in warnings.itertuples(index=False):
            print(f"  - {row.item}: {row.note} (value={row.value})")
    health = build_data_source_health(config)
    health_path = project_path(config["paths"]["output_dir"]) / "data_source_health.csv"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health.to_csv(health_path, index=False, encoding="utf-8-sig")
    print_data_source_health_summary(health)
    render_issues = report_render_issues(config)
    if render_issues:
        print("Report render health: attention")
        for issue in render_issues[:5]:
            print(f"  - {issue}")
    else:
        print("Report render health: ok")
    evidence_issues = readiness_evidence_issues(config)
    if evidence_issues:
        print("Live readiness evidence: stale")
        for issue in evidence_issues:
            print(f"  - {issue}")
    else:
        print("Live readiness evidence: current")
    practical_path = project_path(config["paths"]["output_dir"]) / "practical_readiness.csv"
    if practical_path.exists():
        try:
            practical = pd.read_csv(practical_path)
        except EmptyDataError:
            practical = pd.DataFrame()
        if not practical.empty and "passed" in practical:
            failed = practical[~practical["passed"].astype(bool)]
            print(f"Practical readiness: {'passed' if failed.empty else 'failed'} ({len(practical) - len(failed)}/{len(practical)} checks)")
            if not failed.empty:
                for row in failed.head(5).itertuples(index=False):
                    print(f"  - {row.check}: {row.value} / {row.threshold}")
    execution_path = project_path(config["paths"]["output_dir"]) / "execution_readiness.csv"
    if execution_path.exists():
        try:
            execution = pd.read_csv(execution_path)
        except EmptyDataError:
            execution = pd.DataFrame()
        if not execution.empty and "passed" in execution:
            passed = execution["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
            failed = execution[~passed]
            print(
                f"Execution readiness: {'passed' if failed.empty else 'blocked'} "
                f"(passed={int(passed.sum())}/{len(execution)}, failed={len(failed)})"
            )
            if not failed.empty:
                for row in failed.head(5).itertuples(index=False):
                    next_action = getattr(row, "next_action", "")
                    print(f"  - {row.check}: {row.value} / {row.threshold}; next={next_action}")
    candidate = load_candidate_manifest(config)
    if candidate:
        output_dir = project_path(config["paths"]["output_dir"])
        comparison_path = output_dir / "benchmark_comparison.csv"
        try:
            comparison = pd.read_csv(comparison_path) if comparison_path.exists() else pd.DataFrame()
        except EmptyDataError:
            comparison = pd.DataFrame()
        validation = build_candidate_validation(config, comparison)
        status = validation[validation["check"] == "forward_validation_status"]
        status_value = status["value"].iloc[0] if not status.empty else "unknown"
        days = validation[validation["check"] == "candidate_forward_trading_days"]
        days_text = ""
        if not days.empty:
            days_text = f" ({days['value'].iloc[0]}/{days['threshold'].iloc[0]} forward days)"
        print(f"Live candidate: {candidate.get('candidate_id')} status={status_value}{days_text}")
    else:
        print("Live candidate: missing (run freeze-candidate after choosing final parameters)")
    shadow_audit_path = project_path(config["paths"]["output_dir"]) / "shadow_forward_audit.csv"
    if shadow_audit_path.exists():
        try:
            shadow_audit = pd.read_csv(shadow_audit_path)
        except EmptyDataError:
            shadow_audit = pd.DataFrame()
        if not shadow_audit.empty and "status" in shadow_audit:
            status_counts = shadow_audit["status"].astype(str).value_counts().to_dict()
            status_text = ", ".join(f"{key}={value}" for key, value in status_counts.items())
            print(f"Shadow forward audit: {len(shadow_audit)} signals ({status_text})")
            guard = config.get("live_guard", {})
            stats = shadow_observation_values(
                shadow_audit,
                candidate_id=str(candidate.get("candidate_id", "")) if candidate else "",
                strategy_hash=live_strategy_hash(config),
                require_current=bool(guard.get("require_current_candidate_shadow", True)),
                min_observed_days=int(guard.get("min_shadow_observed_days_per_signal", 1)),
            )
            print(
                "Shadow current candidate progress: "
                f"observed={stats['observed_signal_count']}/{guard.get('min_shadow_observed_signals', 20)} "
                f"(min_days={guard.get('min_shadow_observed_days_per_signal', 1)}), "
                f"avg_excess={format_optional_percent(stats['avg_excess_return'])}, "
                f"positive_ratio={format_optional_percent(stats['positive_excess_ratio'])}, "
                f"min_coverage={format_optional_percent(stats['min_covered_weight'])}"
            )
    rpa = config.get("rpa", {})
    if rpa:
        final_confirm = bool(rpa.get("require_manual_final_confirm", True))
        no_submit = bool(rpa.get("never_click_submit", True))
        print(f"RPA safety: {'ok' if final_confirm and no_submit else 'unsafe'}")
        print(f"  max_single_order_amount={rpa.get('max_single_order_amount')}")
        print(f"  max_daily_order_amount={rpa.get('max_daily_order_amount')}")
    print_account_state_status(config)


def run_probe(config: dict) -> None:
    import akshare as ak  # type: ignore

    store = DataStore.from_config(config)
    with akshare_network_context(config):
        try:
            spot = fetch_stock_spot(ak)
            print(f"Stock spot source: ok ({len(spot)} rows)")
        except Exception as exc:
            cached = cached_universe_status(store, config)
            if cached:
                print(f"Stock spot source: unavailable; cached universe usable ({cached} symbols).")
                print(f"  detail: {exc}")
            else:
                raise RuntimeError(f"股票列表数据源不可用，且本地股票池缓存不可用。详情：{exc}") from exc
        benchmark = normalize_benchmark(fetch_benchmark_akshare(ak, config))
        if benchmark.empty:
            print("Benchmark source: missing")
        else:
            print(f"Benchmark source: ok ({len(benchmark)} rows, latest={benchmark['date'].iloc[-1]})")


def print_account_state_status(config: dict) -> None:
    output_dir = project_path(config["paths"]["output_dir"])
    audit_path = output_dir / "account_state_audit.csv"
    missing_path = output_dir / "account_state_missing_prices.csv"
    if not audit_path.exists():
        print("Account state audit: missing (run account-state, account-reset --confirm, or account-import)")
        return
    try:
        audit = pd.read_csv(audit_path)
    except EmptyDataError:
        audit = pd.DataFrame()
    if audit.empty or "passed" not in audit:
        print("Account state audit: invalid")
        return
    passed = audit["passed"].astype(bool)
    print(f"Account state audit: {'passed' if bool(passed.all()) else 'failed'} ({int(passed.sum())}/{len(audit)} checks)")
    if not bool(passed.all()):
        failed = audit.loc[~passed, ["check", "value", "threshold"]]
        for row in failed.head(5).itertuples(index=False):
            print(f"  - {row.check}: {row.value} / {row.threshold}")
        missing_count = 0
        if missing_path.exists():
            try:
                missing_count = len(pd.read_csv(missing_path))
            except EmptyDataError:
                missing_count = 0
        if missing_count:
            print(f"  - missing priced positions: {missing_count} rows in {missing_path}")
        print("  next: use account-reset --confirm for local paper trading, or account-repair-template/account-import-check/account-import for real holdings.")


def cached_universe_status(store: DataStore, config: dict) -> int:
    from .data import cached_universe_symbols

    cached = cached_universe_symbols(store, config)
    return len(cached[1]) if cached else 0


def print_complete_market_status(prices: pd.DataFrame, benchmark: pd.DataFrame, config: dict) -> None:
    if prices.empty or "date" not in prices or "symbol" not in prices:
        return
    data = prices.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce").dt.date
    daily_counts = data.dropna(subset=["date"]).groupby("date")["symbol"].nunique()
    if daily_counts.empty:
        return
    raw_latest = daily_counts.index.max()
    max_symbols = int(daily_counts.max())
    raw_latest_count = int(daily_counts.loc[raw_latest])
    coverage = raw_latest_count / max_symbols if max_symbols else 0.0
    complete_dates = complete_market_dates(prices, benchmark, config)
    complete_latest = complete_dates[-1] if complete_dates else "missing"
    print(f"Complete market date: {complete_latest} (raw_latest={raw_latest}, raw_latest_coverage={coverage:.2%})")


def format_optional_percent(value: object) -> str:
    if value in {None, ""}:
        return "n/a"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return str(value)
