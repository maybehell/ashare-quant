from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv, replace_with_retry
from .strategy_activation import current_execution_epoch


EXECUTION_ARTIFACT_FILES = (
    "orders.csv",
    "orders_provenance.csv",
    "paper_equity_snapshot.csv",
    "paper_signal_snapshot.csv",
    "signal_log.csv",
    "account_reconciliation.csv",
    "rpa_orders.csv",
    "rpa_orders_all.csv",
    "rpa_blocked_orders.csv",
    "rpa_orders_integrity.json",
    "rpa_pre_trade_checks.csv",
    "rpa_action_log.csv",
    "order_liquidity_audit.csv",
    "order_liquidity_all_audit.csv",
    "auto_live_batch_plan.csv",
    "auto_live_batch_orders.csv",
    "auto_live_current_batch.csv",
    "auto_live_active_batch.csv",
    "auto_live_batch_summary.csv",
    "auto_live_action_confirmation.csv",
)

INVALIDATION_COLUMNS = [
    "run_id",
    "status",
    "reason",
    "source_path",
    "archive_path",
    "sha256",
    "bytes",
    "execution_epoch_id",
    "invalidated_at",
]


def invalidate_execution_artifacts(
    config: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    invalidated_at = datetime.now().astimezone()
    run_id = f"{invalidated_at.strftime('%Y%m%dT%H%M%S%z')}-{uuid4().hex[:12]}"
    archive_dir = output_dir / "order_quarantine" / run_id
    reason_text = str(reason or "new_execution_cycle").strip()
    try:
        epoch_id = current_execution_epoch(config).get("execution_epoch_id", "")
    except RuntimeError:
        epoch_id = ""

    rows: list[dict[str, object]] = []
    paths: dict[str, Path] = {}
    for name in EXECUTION_ARTIFACT_FILES:
        source = output_dir / name
        if not source.is_file():
            continue
        destination = archive_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = _file_sha256(source)
        size = source.stat().st_size
        replace_with_retry(source, destination)
        rows.append(
            {
                "run_id": run_id,
                "status": "quarantined",
                "reason": reason_text,
                "source_path": str(source.resolve()),
                "archive_path": str(destination.resolve()),
                "sha256": digest,
                "bytes": size,
                "execution_epoch_id": epoch_id,
                "invalidated_at": invalidated_at.isoformat(timespec="seconds"),
            }
        )
        paths[f"quarantined:{name}"] = destination

    if not rows:
        rows.append(
            {
                "run_id": run_id,
                "status": "no_current_artifacts",
                "reason": reason_text,
                "source_path": "",
                "archive_path": "",
                "sha256": "",
                "bytes": 0,
                "execution_epoch_id": epoch_id,
                "invalidated_at": invalidated_at.isoformat(timespec="seconds"),
            }
        )

    frame = pd.DataFrame(rows, columns=INVALIDATION_COLUMNS)
    audit_path = output_dir / "order_artifact_invalidation.csv"
    atomic_to_csv(frame, audit_path, index=False, encoding="utf-8-sig")
    paths["order_artifact_invalidation.csv"] = audit_path
    if archive_dir.exists():
        manifest_path = archive_dir / "manifest.csv"
        atomic_to_csv(frame, manifest_path, index=False, encoding="utf-8-sig")
        paths["order_quarantine_manifest.csv"] = manifest_path
    return paths


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
