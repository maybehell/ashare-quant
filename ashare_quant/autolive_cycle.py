from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .autolive import (
    AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS,
    all_passed,
    auto_live_allowed,
    prior_auto_live_submission_status,
    query_auto_live_orders,
    refresh_auto_live_account_snapshot_if_needed,
    run_auto_live,
    write_auto_live_status,
    write_auto_live_submit_lock_audit,
)
from .autolive_account import query_auto_live_account
from .autolive_cancel import cancel_verification_needed, verify_auto_live_cancellations
from .autolive_batch import write_auto_live_batch_plan
from .autolive_fills import settle_auto_live_fills, write_auto_live_fill_sync
from .autolive_risk import write_auto_live_risk_audit
from .autolive_window import auto_live_submit_window_enforced, write_auto_live_window_audit
from .audit import frame_sha256
from .config import project_path
from .io_utils import atomic_to_csv
from .autolive_journal import record_auto_live_event


AUTO_LIVE_CYCLE_COLUMNS = [
    "cycle_id",
    "mode",
    "stage_order",
    "stage",
    "status",
    "required",
    "artifact",
    "started_at",
    "completed_at",
    "message",
]
AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS = ["cycle_id", *AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS]
AUTO_LIVE_POST_TRADE_MANIFEST_COLUMNS = [
    "cycle_id", "broker_snapshot_run_id", "local_snapshot_sha256",
    "local_positions_sha256", "broker_snapshot_sha256", "broker_positions_sha256",
    "fill_progress_sha256", "reconciliation_audit_sha256", "passed", "generated_at",
]
AUTO_LIVE_CYCLE_SUMMARY_COLUMNS = [
    "cycle_id",
    "mode",
    "status",
    "blocking_stage",
    "submitted_count",
    "queried_count",
    "fill_count",
    "account_sync_requested",
    "account_sync_completed",
    "started_at",
    "completed_at",
    "message",
]


def run_auto_live_cycle(
    config: dict[str, Any],
    *,
    submit: bool = False,
    confirm_account_sync: bool = False,
) -> dict[str, Path]:
    """Run one fail-closed automatic-live cycle and persist every stage.

    Audit mode never calls a broker adapter. Submit mode may refresh a command
    adapter account snapshot, recover a prior batch, submit, query, and build
    fill candidates. Account state is mutated only with explicit confirmation.
    """
    if confirm_account_sync and not submit:
        raise RuntimeError("auto-live cycle blocked: --confirm-account-sync requires --submit")

    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    cycle_id = started.strftime("%Y%m%d%H%M%S%f")
    mode = "submit_and_sync" if confirm_account_sync else ("submit" if submit else "audit")
    rows: list[dict[str, object]] = []
    paths: dict[str, Path] = {}
    counters = {"submitted": 0, "queried": 0, "fills": 0}
    account_sync_completed = False

    def add_stage(
        stage: str,
        status: str,
        *,
        required: bool,
        artifact: Path | None = None,
        message: str,
        stage_started: datetime | None = None,
    ) -> None:
        now = datetime.now()
        rows.append(
            {
                "cycle_id": cycle_id,
                "mode": mode,
                "stage_order": len(rows) + 1,
                "stage": stage,
                "status": status,
                "required": bool(required),
                "artifact": str(artifact.resolve()) if artifact is not None else "",
                "started_at": (stage_started or now).isoformat(timespec="seconds"),
                "completed_at": now.isoformat(timespec="seconds"),
                "message": message,
            }
        )
        _write_cycle_artifacts(
            output_dir,
            rows,
            _build_cycle_summary(
                rows,
                cycle_id=cycle_id,
                mode=mode,
                submit=submit,
                confirm_account_sync=confirm_account_sync,
                counters=counters,
                account_sync_completed=account_sync_completed,
                started_at=started,
            ),
        )

    lock_started = datetime.now()
    lock_path = write_auto_live_submit_lock_audit(config)
    paths[lock_path.name] = lock_path
    lock_audit = _read_csv(lock_path)
    lock_clear = _named_check_passed(lock_audit, "submit_lock_clear")

    prior_resolved, prior_value = prior_auto_live_submission_status(output_dir)
    recovery_started = datetime.now()
    recovery_ok = prior_resolved
    recovery_artifact: Path | None = lock_path
    recovery_message = prior_value
    if submit and lock_clear and not prior_resolved:
        try:
            recovery_paths = query_auto_live_orders(config, raise_on_blocked=False)
            paths.update(recovery_paths)
            recovery_artifact = recovery_paths.get("auto_live_order_status_audit.csv")
            cancel_verification_ok = True
            if cancel_verification_needed(output_dir):
                verification_paths = verify_auto_live_cancellations(config, raise_on_blocked=False)
                paths.update(verification_paths)
                verification_audit = _read_csv(verification_paths.get("auto_live_cancel_verification_audit.csv"))
                cancel_verification_ok = all_passed(verification_audit)
                recovery_artifact = verification_paths.get("auto_live_cancel_verification_audit.csv")
            recovery_ok, recovery_message = prior_auto_live_submission_status(output_dir)
            recovery_ok = bool(recovery_ok and cancel_verification_ok)
            if not cancel_verification_ok:
                recovery_message = "前批撤单尚未由撤单后的券商查询确认进入 canceled 或 filled 终态。"
        except Exception as exc:  # Broker read failures must stop the cycle.
            recovery_ok = False
            recovery_message = f"前批委托自动查询失败：{exc}"

    lock_path = write_auto_live_submit_lock_audit(config)
    paths[lock_path.name] = lock_path
    lock_audit = _read_csv(lock_path)
    lock_ok = all_passed(lock_audit)
    add_stage(
        "submit_lock",
        "passed" if lock_ok else "blocked",
        required=submit,
        artifact=lock_path,
        message=_audit_message(lock_audit, "提交锁与前批状态通过。", "提交锁或前一批委托尚未闭环。"),
        stage_started=lock_started,
    )
    add_stage(
        "prior_batch_recovery",
        "passed" if recovery_ok else ("blocked" if submit else "observed"),
        required=submit,
        artifact=recovery_artifact,
        message=recovery_message,
        stage_started=recovery_started,
    )

    batch_started = datetime.now()
    batch_paths = write_auto_live_batch_plan(config)
    paths.update(batch_paths)
    batch_summary_path = batch_paths["auto_live_batch_summary.csv"]
    batch_summary = _read_csv(batch_summary_path)
    current_batch = _read_csv(batch_paths["auto_live_current_batch.csv"])
    batch_status = str(batch_summary.iloc[-1].get("status", "missing")) if not batch_summary.empty else "missing"
    batch_ok = batch_status == "ready" and not current_batch.empty
    add_stage(
        "batch_plan",
        "passed" if batch_ok else ("blocked" if submit else "observed"),
        required=submit,
        artifact=batch_summary_path,
        message=str(batch_summary.iloc[-1].get("message", batch_status)) if not batch_summary.empty else "缺少自动实盘批次规划结果。",
        stage_started=batch_started,
    )

    account_started = datetime.now()
    if submit and lock_ok and recovery_ok and batch_ok:
        try:
            account_paths = refresh_auto_live_account_snapshot_if_needed(config)
            paths.update(account_paths)
            refresh_path = account_paths.get("auto_live_account_refresh.csv")
            refresh = _read_csv(refresh_path) if refresh_path else pd.DataFrame()
            account_status = str(refresh.iloc[-1].get("status", "not_required")) if not refresh.empty else "not_required"
            account_ok = account_status not in {"failed", "blocked"}
            account_message = (
                str(refresh.iloc[-1].get("message", account_status)) if not refresh.empty else "当前适配器无需账户快照刷新。"
            )
        except Exception as exc:
            refresh_path = None
            account_ok = False
            account_message = f"券商账户只读刷新失败：{exc}"
        add_stage(
            "broker_account_refresh",
            "passed" if account_ok else "blocked",
            required=True,
            artifact=refresh_path,
            message=account_message,
            stage_started=account_started,
        )
    else:
        account_ok = True
        add_stage(
            "broker_account_refresh",
            "skipped",
            required=False,
            message="审计模式或前置门禁未通过，未调用券商账户接口。",
            stage_started=account_started,
        )

    gate_started = datetime.now()
    status_paths = write_auto_live_status(config, tickets=current_batch)
    paths.update(status_paths)
    summary_path = status_paths["auto_live_summary.csv"]
    summary = _read_csv(summary_path)
    gate_ok = auto_live_allowed(summary)
    add_stage(
        "auto_live_gate",
        "passed" if gate_ok else "blocked",
        required=submit,
        artifact=summary_path,
        message=_summary_message(summary),
        stage_started=gate_started,
    )

    risk_started = datetime.now()
    risk_path = write_auto_live_risk_audit(config, tickets=current_batch)
    paths[risk_path.name] = risk_path
    risk = _read_csv(risk_path)
    risk_ok = all_passed(risk)
    add_stage(
        "independent_risk",
        "passed" if risk_ok else "blocked",
        required=submit,
        artifact=risk_path,
        message=_audit_message(risk, "独立风险限额全部通过。", "独立风险限额存在阻断项。"),
        stage_started=risk_started,
    )

    window_started = datetime.now()
    window_path = write_auto_live_window_audit(config, tickets=current_batch)
    paths[window_path.name] = window_path
    window = _read_csv(window_path)
    window_required = bool(submit and auto_live_submit_window_enforced(config, dry_run=False))
    window_ok = all_passed(window)
    add_stage(
        "trading_window",
        "passed" if window_ok else ("blocked" if window_required else "observed"),
        required=window_required,
        artifact=window_path,
        message=_audit_message(window, "交易时间窗与工单日期通过。", "当前不在可提交时间窗或工单已过期。"),
        stage_started=window_started,
    )

    preflight_ok = bool(lock_ok and recovery_ok and batch_ok and account_ok and gate_ok and risk_ok and (window_ok or not window_required))
    submit_started = datetime.now()
    submitted = False
    if not submit:
        add_stage(
            "broker_submit",
            "skipped",
            required=False,
            message="本周期为审计模式，未调用券商提交接口。",
            stage_started=submit_started,
        )
    elif not preflight_ok:
        add_stage(
            "broker_submit",
            "blocked",
            required=True,
            message="前置阶段未全部通过，券商提交接口未被调用。",
            stage_started=submit_started,
        )
    else:
        try:
            submit_paths = run_auto_live(config, dry_run=False, raise_on_blocked=False, tickets=current_batch)
            paths.update(submit_paths)
            run_frame = _current_run_frame(submit_paths, output_dir)
            run_status = str(run_frame.iloc[-1].get("status", "missing")) if not run_frame.empty else "missing"
            counters["submitted"] = _numeric_last(run_frame, "submitted_count")
            submitted = run_status in {"submitted", "submission_uncertain", "partial_failed"}
            submit_ok = run_status == "submitted"
            submit_message = str(run_frame.iloc[-1].get("message", run_status)) if not run_frame.empty else "缺少自动实盘运行结果。"
        except Exception as exc:
            submit_ok = False
            submit_message = f"券商提交阶段异常：{exc}"
        add_stage(
            "broker_submit",
            "passed" if submit_ok else ("uncertain" if submitted else "blocked"),
            required=True,
            artifact=paths.get("auto_live_run.csv") or output_dir / "auto_live_run.csv",
            message=submit_message,
            stage_started=submit_started,
        )

    query_started = datetime.now()
    query_ok = False
    if submitted:
        try:
            query_paths = query_auto_live_orders(config, raise_on_blocked=False)
            paths.update(query_paths)
            status_frame = _read_csv(query_paths.get("auto_live_order_status.csv"))
            status_audit = _read_csv(query_paths.get("auto_live_order_status_audit.csv"))
            counters["queried"] = len(status_frame)
            query_ok = all_passed(status_audit)
            query_message = _audit_message(status_audit, "券商委托状态查询完成。", "券商委托状态查询未通过审计。")
            query_artifact = query_paths.get("auto_live_order_status_audit.csv")
            if cancel_verification_needed(output_dir):
                verification_paths = verify_auto_live_cancellations(config, raise_on_blocked=False)
                paths.update(verification_paths)
                verification_audit = _read_csv(verification_paths.get("auto_live_cancel_verification_audit.csv"))
                query_ok = bool(query_ok and all_passed(verification_audit))
                query_artifact = verification_paths.get("auto_live_cancel_verification_audit.csv")
                if not all_passed(verification_audit):
                    query_message = "券商查询完成，但撤单对象尚未确认进入 canceled 或 filled 终态。"
        except Exception as exc:
            query_message = f"券商委托查询异常：{exc}"
            query_artifact = None
        add_stage(
            "post_submit_query",
            "passed" if query_ok else "blocked",
            required=True,
            artifact=query_artifact,
            message=query_message,
            stage_started=query_started,
        )
    else:
        add_stage(
            "post_submit_query",
            "skipped",
            required=False,
            message="本周期没有进入券商提交阶段，无需查询新委托。",
            stage_started=query_started,
        )

    fill_started = datetime.now()
    fill_ok = False
    if submitted and query_ok:
        try:
            fill_paths = write_auto_live_fill_sync(config)
            paths.update(fill_paths)
            fills = _read_csv(fill_paths.get("auto_live_fill_import.csv"))
            fill_audit = _read_csv(fill_paths.get("auto_live_fill_sync_audit.csv"))
            counters["fills"] = len(fills)
            fill_ok = all_passed(fill_audit)
            fill_message = _audit_message(fill_audit, f"成交候选生成完成，共 {len(fills)} 笔。", "成交候选未通过同步审计。")
            fill_artifact = fill_paths.get("auto_live_fill_sync_audit.csv")
        except Exception as exc:
            fill_message = f"成交候选生成异常：{exc}"
            fill_artifact = None
        add_stage(
            "fill_sync",
            "passed" if fill_ok else "blocked",
            required=True,
            artifact=fill_artifact,
            message=fill_message,
            stage_started=fill_started,
        )
    else:
        add_stage(
            "fill_sync",
            "skipped",
            required=False,
            message="委托查询未完成，未生成成交导入候选。",
            stage_started=fill_started,
        )

    sync_started = datetime.now()
    if confirm_account_sync and submitted and query_ok and fill_ok:
        try:
            settlement_paths = settle_auto_live_fills(config, confirm_import=True)
            paths.update(settlement_paths)
            settlement_path = settlement_paths.get("auto_live_settlement_audit.csv")
            settlement = _read_csv(settlement_path)
            account_sync_completed = all_passed(settlement)
            sync_message = _audit_message(settlement, "成交已按审计结果同步到本地账户。", "本地账户同步未通过结算审计。")
        except Exception as exc:
            settlement_path = None
            account_sync_completed = False
            sync_message = f"本地账户同步异常：{exc}"
        add_stage(
            "account_sync",
            "passed" if account_sync_completed else "blocked",
            required=True,
            artifact=settlement_path,
            message=sync_message,
            stage_started=sync_started,
        )
    else:
        reason = "未显式确认账户同步，本地账户未修改。"
        if confirm_account_sync:
            reason = "提交、查询或成交同步未完成，本地账户未修改。"
        add_stage(
            "account_sync",
            "skipped" if not confirm_account_sync else "blocked",
            required=confirm_account_sync,
            message=reason,
            stage_started=sync_started,
        )

    reconciliation_started = datetime.now()
    if confirm_account_sync and account_sync_completed:
        try:
            account_paths = query_auto_live_account(config, raise_on_blocked=False)
            paths.update(account_paths)
            broker_audit = _read_csv(account_paths.get("auto_live_broker_account_audit.csv"))
            reconciliation_paths = _write_post_trade_reconciliation(output_dir, cycle_id, broker_audit)
            paths.update(reconciliation_paths)
            reconciliation_ok = all_passed(broker_audit)
            reconciliation_message = _audit_message(
                broker_audit,
                "券商资金持仓与更新后的本地账户对账通过。",
                "成交写回后的券商账户对账未通过。",
            )
        except Exception as exc:
            reconciliation_ok = False
            reconciliation_message = f"成交写回后账户对账异常：{exc}"
        add_stage(
            "post_trade_reconciliation",
            "passed" if reconciliation_ok else "blocked",
            required=True,
            artifact=paths.get("auto_live_post_trade_reconciliation.csv"),
            message=reconciliation_message,
            stage_started=reconciliation_started,
        )
    else:
        reconciliation_ok = False
        add_stage(
            "post_trade_reconciliation",
            "skipped" if not confirm_account_sync else "blocked",
            required=confirm_account_sync,
            message="未完成显式账户同步，无需执行后交易券商账户对账。" if not confirm_account_sync else "账户同步未完成，后交易对账被阻断。",
            stage_started=reconciliation_started,
        )

    final_summary = _build_cycle_summary(
        rows,
        cycle_id=cycle_id,
        mode=mode,
        submit=submit,
        confirm_account_sync=confirm_account_sync,
        counters=counters,
        account_sync_completed=account_sync_completed,
        started_at=started,
        completed=True,
    )
    cycle_paths = _write_cycle_artifacts(output_dir, rows, final_summary)
    paths.update(cycle_paths)
    summary_row = final_summary.iloc[-1] if not final_summary.empty else pd.Series(dtype=object)
    post_trade_manifest = _read_csv(paths.get("auto_live_post_trade_manifest.csv"))
    post_trade_manifest_sha256 = frame_sha256(post_trade_manifest) if not post_trade_manifest.empty else ""
    paths.update(
        record_auto_live_event(
            config,
            "cycle_result",
            cycle_id,
            {
                "mode": mode,
                "status": str(summary_row.get("status", "missing")),
                "blocking_stage": str(summary_row.get("blocking_stage", "")),
                "submitted_count": int(counters.get("submitted", 0)),
                "queried_count": int(counters.get("queried", 0)),
                "fill_count": int(counters.get("fills", 0)),
                "account_sync_completed": bool(account_sync_completed),
                "post_trade_manifest_sha256": post_trade_manifest_sha256,
            },
            strict=False,
        )
    )
    return paths


def _write_post_trade_reconciliation(output_dir: Path, cycle_id: str, audit: pd.DataFrame) -> dict[str, Path]:
    current_path = output_dir / "auto_live_post_trade_reconciliation.csv"
    history_path = output_dir / "auto_live_post_trade_reconciliation_history.csv"
    current = audit.copy()
    for column in AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS:
        if column not in current.columns:
            current[column] = ""
    current.insert(0, "cycle_id", cycle_id)
    current = current[AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS]
    atomic_to_csv(current, current_path, index=False, encoding="utf-8-sig")

    history = _read_csv(history_path)
    if not history.empty and "cycle_id" in history.columns:
        history = history[~history["cycle_id"].astype(str).eq(cycle_id)].copy()
    for column in AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS:
        if column not in history.columns:
            history[column] = ""
    combined = pd.concat([history[AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS], current], ignore_index=True)
    atomic_to_csv(combined, history_path, index=False, encoding="utf-8-sig")

    local_snapshot = _read_csv(output_dir / "account_state_snapshot.csv")
    local_positions = _read_csv(output_dir / "account_state_positions.csv")
    broker_snapshot = _read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    broker_positions = _read_csv(output_dir / "auto_live_broker_positions.csv")
    fill_progress = _read_csv(output_dir / "auto_live_fill_progress.csv")
    broker_snapshot_run_id = (
        str(broker_snapshot.iloc[-1].get("snapshot_run_id", "")) if not broker_snapshot.empty else ""
    )
    manifest = pd.DataFrame(
        [
            {
                "cycle_id": cycle_id,
                "broker_snapshot_run_id": broker_snapshot_run_id,
                "local_snapshot_sha256": frame_sha256(local_snapshot),
                "local_positions_sha256": frame_sha256(local_positions),
                "broker_snapshot_sha256": frame_sha256(broker_snapshot),
                "broker_positions_sha256": frame_sha256(broker_positions),
                "fill_progress_sha256": frame_sha256(fill_progress),
                "reconciliation_audit_sha256": frame_sha256(current),
                "passed": all_passed(audit) and bool(broker_snapshot_run_id),
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        ],
        columns=AUTO_LIVE_POST_TRADE_MANIFEST_COLUMNS,
    )
    manifest_path = output_dir / "auto_live_post_trade_manifest.csv"
    atomic_to_csv(manifest, manifest_path, index=False, encoding="utf-8-sig")
    return {
        current_path.name: current_path,
        history_path.name: history_path,
        manifest_path.name: manifest_path,
    }


def _write_cycle_artifacts(output_dir: Path, rows: list[dict[str, object]], summary: pd.DataFrame) -> dict[str, Path]:
    cycle_path = output_dir / "auto_live_cycle.csv"
    summary_path = output_dir / "auto_live_cycle_summary.csv"
    history_path = output_dir / "auto_live_cycle_history.csv"
    frame = pd.DataFrame(rows, columns=AUTO_LIVE_CYCLE_COLUMNS)
    atomic_to_csv(frame, cycle_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(summary[AUTO_LIVE_CYCLE_SUMMARY_COLUMNS], summary_path, index=False, encoding="utf-8-sig")

    existing = _read_csv(history_path)
    cycle_id = str(frame.iloc[0]["cycle_id"]) if not frame.empty else ""
    if not existing.empty and "cycle_id" in existing.columns:
        existing = existing[~existing["cycle_id"].astype(str).eq(cycle_id)].copy()
    for column in AUTO_LIVE_CYCLE_COLUMNS:
        if column not in existing.columns:
            existing[column] = ""
    history = pd.concat([existing[AUTO_LIVE_CYCLE_COLUMNS], frame], ignore_index=True)
    atomic_to_csv(history, history_path, index=False, encoding="utf-8-sig")
    empty_artifacts = {
        "auto_live_post_trade_reconciliation.csv": AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS,
        "auto_live_post_trade_reconciliation_history.csv": AUTO_LIVE_POST_TRADE_RECONCILIATION_COLUMNS,
        "auto_live_post_trade_manifest.csv": AUTO_LIVE_POST_TRADE_MANIFEST_COLUMNS,
    }
    for name, columns in empty_artifacts.items():
        post_trade_path = output_dir / name
        if not post_trade_path.exists():
            atomic_to_csv(
                pd.DataFrame(columns=columns),
                post_trade_path,
                index=False,
                encoding="utf-8-sig",
            )
    return {
        cycle_path.name: cycle_path,
        summary_path.name: summary_path,
        history_path.name: history_path,
        "auto_live_post_trade_reconciliation.csv": output_dir / "auto_live_post_trade_reconciliation.csv",
        "auto_live_post_trade_reconciliation_history.csv": output_dir / "auto_live_post_trade_reconciliation_history.csv",
        "auto_live_post_trade_manifest.csv": output_dir / "auto_live_post_trade_manifest.csv",
    }


def _build_cycle_summary(
    rows: list[dict[str, object]],
    *,
    cycle_id: str,
    mode: str,
    submit: bool,
    confirm_account_sync: bool,
    counters: dict[str, int],
    account_sync_completed: bool,
    started_at: datetime,
    completed: bool = False,
) -> pd.DataFrame:
    required_failures = [
        row
        for row in rows
        if bool(row.get("required")) and str(row.get("status")) not in {"passed"}
    ]
    blocking_stage = str(required_failures[0].get("stage", "")) if required_failures else ""
    if not completed:
        status = "running"
        message = "自动实盘周期正在运行。"
    elif required_failures:
        status = "blocked"
        message = f"周期在 {blocking_stage} 阶段被阻断；未通过阶段不会被绕过。"
    elif not submit:
        observed_blockers = [row for row in rows if str(row.get("status")) in {"blocked", "observed"}]
        status = "audit_complete_with_blockers" if observed_blockers else "audit_complete"
        message = "审计周期完成，未调用券商提交接口。"
    elif confirm_account_sync and account_sync_completed:
        status = "completed_and_synced"
        message = "提交、查询、成交审计和本地账户同步已完成。"
    else:
        status = "completed"
        message = "提交、查询和成交候选生成已完成；本地账户未自动修改。"
    completed_at = datetime.now().isoformat(timespec="seconds") if completed else ""
    return pd.DataFrame(
        [
            {
                "cycle_id": cycle_id,
                "mode": mode,
                "status": status,
                "blocking_stage": blocking_stage,
                "submitted_count": int(counters.get("submitted", 0)),
                "queried_count": int(counters.get("queried", 0)),
                "fill_count": int(counters.get("fills", 0)),
                "account_sync_requested": bool(confirm_account_sync),
                "account_sync_completed": bool(account_sync_completed),
                "started_at": started_at.isoformat(timespec="seconds"),
                "completed_at": completed_at,
                "message": message,
            }
        ],
        columns=AUTO_LIVE_CYCLE_SUMMARY_COLUMNS,
    )


def _read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (EmptyDataError, OSError):
        return pd.DataFrame()


def _audit_message(frame: pd.DataFrame, passed_message: str, failed_message: str) -> str:
    if frame.empty or "passed" not in frame.columns:
        return f"{failed_message} 审计文件为空或字段缺失。"
    failed = frame[~frame["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})]
    if failed.empty:
        return passed_message
    checks = ";".join(failed.get("check", pd.Series(dtype=str)).astype(str).tolist()) or "unknown"
    return f"{failed_message} 阻断项：{checks}"


def _named_check_passed(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False
    matched = frame[frame["check"].astype(str).eq(check)]
    if matched.empty:
        return False
    return str(matched.iloc[-1].get("passed", "")).strip().lower() in {"true", "1", "yes"}


def _summary_message(summary: pd.DataFrame) -> str:
    if summary.empty:
        return "自动实盘总门禁结果缺失。"
    row = summary.iloc[-1]
    if auto_live_allowed(summary):
        return "自动实盘总门禁全部通过。"
    blockers = str(row.get("blocker_details", "") or row.get("blockers", "") or "unknown")
    return f"自动实盘总门禁未通过：{blockers}"


def _numeric_last(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    value = pd.to_numeric(frame.iloc[-1].get(column, 0), errors="coerce")
    return 0 if pd.isna(value) else int(value)


def _current_run_frame(paths: dict[str, Path], output_dir: Path) -> pd.DataFrame:
    blocked_path = paths.get("auto_live_blocked_attempt.csv")
    if blocked_path is not None:
        blocked = _read_csv(blocked_path)
        if not blocked.empty:
            return blocked
    return _read_csv(paths.get("auto_live_run.csv") or output_dir / "auto_live_run.csv")
