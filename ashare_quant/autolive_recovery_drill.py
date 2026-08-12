from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd

from .autolive import (
    auto_live_config_sha256,
    auto_live_dedupe_key,
    build_auto_live_order_status_audit,
    build_auto_live_submission_recovery,
    duplicate_auto_live_submission,
    load_auto_live_query_source,
    order_status_row,
    prior_auto_live_submission_status,
    tickets_sha256,
    write_auto_live_submission_intent,
    write_command_adapter_request,
)
from .config import project_path
from .io_utils import atomic_to_csv


RECOVERY_DRILL_SCHEMA = 1
RECOVERY_DRILL_AUDIT_COLUMNS = [
    "check",
    "passed",
    "value",
    "threshold",
    "message",
]
RECOVERY_DRILL_SUMMARY_COLUMNS = [
    "status",
    "schema_version",
    "auto_live_config_sha256",
    "broker_adapter_invoked",
    "isolated_workspace_removed",
    "passed_checks",
    "total_checks",
    "generated_at",
    "message",
]


def write_auto_live_recovery_drill(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "auto_live_recovery_drill_audit.csv"
    summary_path = output_dir / "auto_live_recovery_drill_summary.csv"
    rows: list[dict[str, object]] = []
    isolated_workspace_removed = False

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "message": message,
            }
        )

    auto_hash = auto_live_config_sha256(config)
    try:
        with TemporaryDirectory(prefix="ashare-quant-recovery-drill-") as temp_value:
            temp_dir = Path(temp_value)
            drill_config = deepcopy(config)
            drill_config.setdefault("paths", {})["output_dir"] = str(temp_dir)
            run_id = "recovery-drill-submit"
            query_run_id = "recovery-drill-query"
            ticket = pd.DataFrame(
                [
                    {
                        "date": "2099-01-01",
                        "ticket_id": "recovery-drill-ticket",
                        "symbol": "600000",
                        "side": "buy",
                        "quantity": 100,
                        "limit_price": 1.0,
                        "status": "ready",
                    }
                ]
            )
            ticket_hash = tickets_sha256(ticket)
            dedupe_key = auto_live_dedupe_key(ticket_hash, auto_hash)
            write_auto_live_submission_intent(
                drill_config,
                run_id=run_id,
                state="adapter_invocation_started",
                adapter="command",
                order_count=len(ticket),
                tickets_hash=ticket_hash,
                auto_hash=auto_hash,
                dedupe_key=dedupe_key,
                message="Isolated recovery drill; no broker adapter invocation.",
            )
            request_path = temp_dir / f"auto_live_command_request_{run_id}.json"
            result_path = temp_dir / f"auto_live_command_result_{run_id}.json"
            write_command_adapter_request(
                drill_config, ticket, run_id, request_path, result_path
            )

            reconstructed = load_auto_live_query_source(temp_dir, config=drill_config)
            reconstructed_reason = str(
                reconstructed.attrs.get("query_source_integrity_reason", "")
            )
            reconstructed_ok = bool(
                reconstructed.attrs.get("query_source_integrity_passed", False)
                and len(reconstructed) == 1
                and str(reconstructed.iloc[0].get("status", "")) == "unknown"
                and not str(reconstructed.iloc[0].get("broker_order_id", ""))
            )
            add(
                "interrupted_submit_reconstructed_as_unknown",
                reconstructed_ok,
                reconstructed_reason,
                "one request-bound unknown result",
                "A missing adapter result must become unknown and must never be treated as failed.",
            )

            duplicate = duplicate_auto_live_submission(temp_dir, dedupe_key)
            add(
                "duplicate_submission_blocked_by_persisted_intent",
                duplicate is not None,
                "blocked" if duplicate is not None else "not_blocked",
                "blocked",
                "The persisted pre-submit intent must suppress a second submission of the same batch.",
            )

            source_row = next(reconstructed.itertuples(index=False))
            accepted = pd.DataFrame(
                [
                    order_status_row(
                        source_row,
                        query_run_id,
                        "accepted",
                        0,
                        0.0,
                        0.0,
                        0.0,
                        "Isolated query recovered broker acceptance.",
                        resolved_broker_order_id="recovery-drill-broker-order",
                    )
                ]
            )
            accepted_audit = build_auto_live_order_status_audit(
                reconstructed,
                accepted,
                query_run_id,
                True,
                "isolated_recovery_drill_query_ready",
                source_integrity_passed=bool(
                    reconstructed.attrs.get("query_source_integrity_passed", False)
                ),
                source_integrity_value=reconstructed_reason,
            )
            accepted_recovery = build_auto_live_submission_recovery(
                reconstructed, accepted
            )
            accepted_ok = bool(
                not accepted_audit.empty
                and accepted_audit["passed"].astype(bool).all()
                and len(accepted_recovery) == 1
                and bool(accepted_recovery.iloc[0]["resolved"])
                and accepted_recovery.iloc[0]["action"]
                == "continue_query_cancel_or_settlement"
            )
            add(
                "unknown_submission_resolved_by_bound_query",
                accepted_ok,
                f"audit={int(accepted_audit['passed'].sum())}/{len(accepted_audit)}",
                "all query checks passed and recovery resolved",
                "A request-bound query must recover a stable broker order ID without resubmission.",
            )
            atomic_to_csv(
                accepted,
                temp_dir / "auto_live_order_status.csv",
                index=False,
                encoding="utf-8-sig",
            )
            active_resolved, active_value = prior_auto_live_submission_status(temp_dir)
            add(
                "active_recovered_order_still_blocks_next_batch",
                not active_resolved,
                active_value,
                "prior submission unresolved until terminal broker status",
                "Acceptance is not terminal; the next batch must remain blocked.",
            )

            terminal = accepted.copy()
            terminal["broker_status"] = "canceled"
            atomic_to_csv(
                terminal,
                temp_dir / "auto_live_order_status.csv",
                index=False,
                encoding="utf-8-sig",
            )
            terminal_resolved, terminal_value = prior_auto_live_submission_status(
                temp_dir
            )
            add(
                "terminal_query_releases_prior_batch_guard",
                terminal_resolved,
                terminal_value,
                "all prior broker orders terminal",
                "Only a complete terminal broker query may release the prior-batch guard.",
            )
            add(
                "broker_adapter_never_invoked",
                True,
                False,
                False,
                "The drill builds persisted requests and synthetic query responses but never executes an adapter command.",
            )
            add(
                "isolated_workspace_contains_no_live_ledger",
                not any(temp_dir.glob("*.sqlite3")),
                len(list(temp_dir.glob("*.sqlite3"))),
                0,
                "The recovery drill must not initialize any live or paper ledger.",
            )
        isolated_workspace_removed = not temp_dir.exists()
    except Exception as exc:
        add(
            "recovery_drill_completed",
            False,
            f"{type(exc).__name__}: {exc}",
            "completed without exception",
            "Any drill setup, integrity, identity, or recovery failure blocks the evidence.",
        )

    add(
        "isolated_workspace_removed",
        isolated_workspace_removed,
        isolated_workspace_removed,
        True,
        "All synthetic intent, request, and order-state artifacts must be destroyed after the drill.",
    )
    audit = pd.DataFrame(rows, columns=RECOVERY_DRILL_AUDIT_COLUMNS)
    passed_checks = int(audit["passed"].astype(bool).sum()) if not audit.empty else 0
    all_passed = bool(not audit.empty and passed_checks == len(audit))
    summary = pd.DataFrame(
        [
            {
                "status": "passed" if all_passed else "failed",
                "schema_version": RECOVERY_DRILL_SCHEMA,
                "auto_live_config_sha256": auto_hash,
                "broker_adapter_invoked": False,
                "isolated_workspace_removed": isolated_workspace_removed,
                "passed_checks": passed_checks,
                "total_checks": len(audit),
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": (
                    "Isolated unknown-outcome recovery and duplicate-submission drill passed; no broker adapter was invoked."
                    if all_passed
                    else "Recovery drill failed closed; inspect auto_live_recovery_drill_audit.csv."
                ),
            }
        ],
        columns=RECOVERY_DRILL_SUMMARY_COLUMNS,
    )
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    return {
        "auto_live_recovery_drill_audit.csv": audit_path,
        "auto_live_recovery_drill_summary.csv": summary_path,
    }
