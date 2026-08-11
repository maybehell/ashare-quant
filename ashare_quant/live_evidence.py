from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .audit import backtest_config_sha256, build_run_manifest, mapping_sha256
from .config import project_path
from .io_utils import atomic_to_csv, replace_with_retry
from .readiness import (
    build_live_readiness_from_outputs,
    current_manifest_frames,
    readiness_evidence_issues,
)


STRATEGY_EVIDENCE_FILES = (
    "equity_curve.csv",
    "performance_metrics.csv",
    "benchmark_comparison.csv",
    "excess_bootstrap.csv",
    "factor_robustness.csv",
    "factor_robustness_summary.csv",
    "data_quality.csv",
    "data_source_health.csv",
    "robustness_split.csv",
    "annual_performance.csv",
    "rolling_validation.csv",
    "regime_validation.csv",
    "strategy_reliability.csv",
    "strategy_candidate_stability.csv",
    "strategy_research.csv",
    "strategy_research_manifest.csv",
    "strategy_selection_bias.csv",
    "strategy_selection_bias_splits.csv",
    "strategy_candidate_family.csv",
    "strategy_candidate_similarity.csv",
    "strategy_family_selection.csv",
    "strategy_family_selection_splits.csv",
    "walk_forward.csv",
    "walk_forward_summary.csv",
    "cost_stress.csv",
    "risk_exposure.csv",
    "industry_exposure.csv",
    "industry_risk_audit.csv",
    "shadow_forward_audit.csv",
    "execution_model_reconciliation.csv",
    "execution_model_reconciliation_summary.csv",
    "strategy_backtest_compare.csv",
    "strategy_promotion.csv",
    "strategy_promotion_decision.csv",
    "primary_challenger_observation.csv",
    "candidate_failure_history.csv",
)

REQUIRED_STRATEGY_EVIDENCE_FILES = (
    "performance_metrics.csv",
    "benchmark_comparison.csv",
    "excess_bootstrap.csv",
    "factor_robustness.csv",
    "factor_robustness_summary.csv",
    "strategy_candidate_stability.csv",
    "walk_forward_summary.csv",
    "cost_stress.csv",
    "shadow_forward_audit.csv",
    "execution_model_reconciliation.csv",
    "execution_model_reconciliation_summary.csv",
    "strategy_promotion.csv",
    "strategy_promotion_decision.csv",
)

SYNC_COLUMNS = [
    "status",
    "file",
    "source_path",
    "destination_path",
    "sha256",
    "bytes",
    "source_config_sha256",
    "live_config_sha256",
    "backtest_config_sha256",
    "evidence_bundle_sha256",
    "generated_at",
]


DERIVED_EVIDENCE_IDENTITY_CHECKS = {
    "factor_robustness": (
        "factor_robustness_candidate_identity_current",
        "factor_robustness_config_current",
        "factor_robustness_data_bound",
        "factor_robustness_data_current",
        "factor_robustness_forward_isolation",
    ),
    "cost_stress": (
        "cost_stress_config_hash",
        "cost_stress_data_hash",
    ),
}


def strategy_derived_evidence_issues(config: dict[str, Any]) -> list[str]:
    guard = config.get("live_guard", {})
    if not isinstance(guard, dict):
        guard = {}
    required: list[str] = []
    if bool(guard.get("require_factor_robustness", False)):
        required.extend(DERIVED_EVIDENCE_IDENTITY_CHECKS["factor_robustness"])
    if bool(guard.get("require_cost_stress", False)) and bool(
        guard.get("require_current_cost_stress", True)
    ):
        required.extend(DERIVED_EVIDENCE_IDENTITY_CHECKS["cost_stress"])
    if not required:
        return []

    readiness = build_live_readiness_from_outputs(config)
    if readiness.empty or not {"check", "passed", "value"}.issubset(readiness.columns):
        return ["derived evidence identity checks are missing"]

    issues: list[str] = []
    for check in required:
        selected = readiness[readiness["check"].astype(str) == check]
        if selected.empty:
            issues.append(f"{check} missing")
            continue
        row = selected.iloc[-1]
        passed = str(row.get("passed", "")).strip().lower() in {"true", "1", "yes"}
        if not passed:
            issues.append(f"{check}={row.get('value', 'failed')}")
    return issues


def sync_live_strategy_evidence(
    strategy_config: dict[str, Any],
    live_config: dict[str, Any],
) -> dict[str, Path]:
    """Publish verified read-only strategy evidence into the live workspace."""
    source_dir = project_path(strategy_config["paths"]["output_dir"])
    destination_dir = project_path(live_config["paths"]["output_dir"])
    if source_dir.resolve() == destination_dir.resolve():
        raise RuntimeError("strategy and live output directories must differ")

    strategy_hash = backtest_config_sha256(strategy_config)
    live_strategy_hash = backtest_config_sha256(live_config)
    if strategy_hash != live_strategy_hash:
        raise RuntimeError(
            "live strategy evidence cannot be published because backtest-relevant configurations differ"
        )

    evidence_issues = readiness_evidence_issues(strategy_config)
    if evidence_issues:
        raise RuntimeError("source strategy evidence is not current: " + "; ".join(evidence_issues))
    derived_issues = strategy_derived_evidence_issues(strategy_config)
    if derived_issues:
        raise RuntimeError(
            "source derived strategy evidence is not current: " + "; ".join(derived_issues)
        )

    missing = [name for name in REQUIRED_STRATEGY_EVIDENCE_FILES if not (source_dir / name).is_file()]
    if missing:
        raise RuntimeError("source strategy evidence is incomplete: " + ", ".join(missing))

    source_files = {
        name: source_dir / name
        for name in STRATEGY_EVIDENCE_FILES
        if (source_dir / name).is_file()
    }
    source_hashes = {name: file_sha256(path) for name, path in source_files.items()}
    evidence_bundle_hash = mapping_sha256(source_hashes)

    destination_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    source_config_hash = mapping_sha256(strategy_config)
    live_config_hash = mapping_sha256(live_config)
    rows: list[dict[str, object]] = []
    paths: dict[str, Path] = {}

    for name, source in source_files.items():
        expected_sha256 = source_hashes[name]
        if file_sha256(source) != expected_sha256:
            raise RuntimeError(f"source strategy evidence changed before publishing {name}")
        destination = destination_dir / name
        atomic_copy(source, destination)
        source_sha256 = file_sha256(source)
        destination_sha256 = file_sha256(destination)
        if source_sha256 != expected_sha256:
            raise RuntimeError(f"source strategy evidence changed while publishing {name}")
        if destination_sha256 != expected_sha256:
            raise RuntimeError(f"strategy evidence hash mismatch after publishing {name}")
        rows.append(
            {
                "status": "copied_verified",
                "file": name,
                "source_path": str(source.resolve()),
                "destination_path": str(destination.resolve()),
                "sha256": destination_sha256,
                "bytes": destination.stat().st_size,
                "source_config_sha256": source_config_hash,
                "live_config_sha256": live_config_hash,
                "backtest_config_sha256": strategy_hash,
                "evidence_bundle_sha256": evidence_bundle_hash,
                "generated_at": generated_at,
            }
        )
        paths[f"live-evidence:{name}"] = destination

    changed_sources = [
        name for name, source in source_files.items()
        if file_sha256(source) != source_hashes[name]
    ]
    if changed_sources:
        raise RuntimeError(
            "source strategy evidence changed during publish: " + ", ".join(changed_sources)
        )

    frames = current_manifest_frames(live_config)
    run_manifest = build_run_manifest(
        live_config,
        stocks=frames.get("stocks", pd.DataFrame()),
        prices=frames.get("prices", pd.DataFrame()),
        fundamentals=frames.get("fundamentals", pd.DataFrame()),
        benchmark=frames.get("benchmark", pd.DataFrame()),
    )
    run_manifest_path = destination_dir / "run_manifest.csv"
    atomic_to_csv(run_manifest, run_manifest_path, index=False, encoding="utf-8-sig")
    rows.append(
        {
            "status": "generated_verified",
            "file": "run_manifest.csv",
            "source_path": str((source_dir / "run_manifest.csv").resolve()),
            "destination_path": str(run_manifest_path.resolve()),
            "sha256": file_sha256(run_manifest_path),
            "bytes": run_manifest_path.stat().st_size,
            "source_config_sha256": source_config_hash,
            "live_config_sha256": live_config_hash,
            "backtest_config_sha256": strategy_hash,
            "generated_at": generated_at,
        }
    )
    paths["live-evidence:run_manifest.csv"] = run_manifest_path

    remaining_issues = readiness_evidence_issues(live_config)
    remaining_issues.extend(strategy_derived_evidence_issues(live_config))
    if remaining_issues:
        raise RuntimeError("published live strategy evidence failed verification: " + "; ".join(remaining_issues))

    audit_path = destination_dir / "live_strategy_evidence_sync.csv"
    atomic_to_csv(pd.DataFrame(rows, columns=SYNC_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    summary_path = destination_dir / "live_strategy_evidence_sync_summary.csv"
    summary = pd.DataFrame(
        [
            {
                "status": "ok",
                "copied_files": sum(row["status"] == "copied_verified" for row in rows),
                "generated_files": sum(row["status"] == "generated_verified" for row in rows),
                "source_output_dir": str(source_dir.resolve()),
                "live_output_dir": str(destination_dir.resolve()),
                "source_config_sha256": source_config_hash,
                "live_config_sha256": live_config_hash,
                "backtest_config_sha256": strategy_hash,
                "evidence_bundle_sha256": evidence_bundle_hash,
                "orders_copied": False,
                "positions_copied": False,
                "account_state_copied": False,
                "generated_at": generated_at,
            }
        ]
    )
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    paths["live_strategy_evidence_sync.csv"] = audit_path
    paths["live_strategy_evidence_sync_summary.csv"] = summary_path
    return paths


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        replace_with_retry(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
