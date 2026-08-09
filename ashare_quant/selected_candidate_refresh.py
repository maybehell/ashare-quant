from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import mapping_sha256
from .backtest import run_backtest
from .broker_preview import file_state
from .candidate import load_candidate_manifest
from .capital_sensitivity import run_capital_sensitivity
from .config import load_config, project_path
from .io_utils import atomic_to_csv
from .readiness import build_live_readiness_from_outputs
from .strategy_activation import (
    _candidate_strict_live_readiness_status,
    _forward_selection_winner_status,
    active_execution_path,
)
from .stress import run_cost_stress


SELECTED_REFRESH_COLUMNS = [
    "status",
    "selected_candidate_id",
    "selected_candidate_config",
    "selection_ready",
    "winner_identity_valid",
    "cache_fingerprint",
    "refresh_attempted",
    "strict_ready_before",
    "strict_ready_after",
    "protected_artifacts_unchanged",
    "broker_submit_enabled",
    "message",
    "generated_at",
]


def refresh_selected_candidate_evidence(base_config: dict[str, Any]) -> dict[str, Path]:
    """Refresh only the frozen forward winner's non-trading deployment evidence."""
    output_dir = project_path(base_config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "selected_candidate_evidence_refresh.csv"
    summary = _read_csv(output_dir / "forward_candidate_selection_summary.csv")
    row = summary.iloc[-1] if len(summary) == 1 else pd.Series(dtype=object)
    selected_value = row.get("selected_candidate_id", "")
    selected_id = "" if pd.isna(selected_value) else str(selected_value).strip()
    selection_ready = bool(
        len(summary) == 1
        and _truth(row.get("selection_ready"))
        and str(row.get("status", "") or "").strip().lower() == "ready"
        and selected_id
    )
    registered = _registered_candidates(base_config)
    selected_path = registered.get(selected_id)
    winner_ok = False
    winner_value = "selection_not_ready"
    if selection_ready and selected_path is not None:
        winner_ok, winner_value = _forward_selection_winner_status(
            base_config,
            list(registered.values()),
            selected_id,
        )

    if not selection_ready or selected_path is None or not winner_ok:
        message = (
            "Waiting for one identity-bound frozen forward winner."
            if not selection_ready
            else f"Selected winner identity is invalid: {winner_value}"
        )
        _write_audit(
            audit_path,
            status="waiting_selection" if not selection_ready else "invalid_selection",
            selected_id=selected_id,
            selected_path=selected_path,
            selection_ready=selection_ready,
            winner_ok=winner_ok,
            cache_fingerprint="",
            attempted=False,
            strict_before=False,
            strict_after=False,
            protected_unchanged=True,
            message=message,
        )
        return {"selected_candidate_evidence_refresh.csv": audit_path}

    candidate_config = load_config(selected_path)
    fingerprint = candidate_cache_fingerprint(candidate_config)
    strict_before, strict_before_value = _candidate_strict_live_readiness_status(
        candidate_config,
        selected_id,
    )
    if strict_before:
        _write_audit(
            audit_path,
            status="already_ready",
            selected_id=selected_id,
            selected_path=selected_path,
            selection_ready=True,
            winner_ok=True,
            cache_fingerprint=fingerprint,
            attempted=False,
            strict_before=True,
            strict_after=True,
            protected_unchanged=True,
            message="Selected winner already has current strict live-readiness evidence.",
        )
        return {"selected_candidate_evidence_refresh.csv": audit_path}

    previous = _read_csv(audit_path)
    if _same_blocked_fingerprint(previous, selected_id, fingerprint):
        _write_audit(
            audit_path,
            status="blocked_evidence_unchanged",
            selected_id=selected_id,
            selected_path=selected_path,
            selection_ready=True,
            winner_ok=True,
            cache_fingerprint=fingerprint,
            attempted=False,
            strict_before=False,
            strict_after=False,
            protected_unchanged=True,
            message=f"No cache change since the last complete refresh; blockers remain: {strict_before_value}",
        )
        return {"selected_candidate_evidence_refresh.csv": audit_path}

    protected = _protected_paths(base_config, candidate_config)
    before = {str(path): file_state(path) for path in protected}
    preexisting = [path for path in protected if before[str(path)][0]]
    if preexisting:
        _write_audit(
            audit_path,
            status="protected_artifacts_present",
            selected_id=selected_id,
            selected_path=selected_path,
            selection_ready=True,
            winner_ok=True,
            cache_fingerprint=fingerprint,
            attempted=False,
            strict_before=False,
            strict_after=False,
            protected_unchanged=True,
            message="Evidence refresh refused because protected artifacts already exist: "
            + ",".join(str(path) for path in preexisting),
        )
        return {"selected_candidate_evidence_refresh.csv": audit_path}
    paths: dict[str, Path] = {}
    status = "refresh_failed"
    message = ""
    strict_after = False
    try:
        for name, path in run_backtest(candidate_config).items():
            paths[f"selected-backtest:{name}"] = path
        stress_path = project_path(candidate_config["paths"]["output_dir"]) / "cost_stress.csv"
        atomic_to_csv(
            run_cost_stress(candidate_config),
            stress_path,
            index=False,
            encoding="utf-8-sig",
        )
        paths["selected-cost-stress:cost_stress.csv"] = stress_path
        for name, path in run_capital_sensitivity(candidate_config).items():
            paths[f"selected-capital:{name}"] = path
        readiness_path = project_path(candidate_config["paths"]["output_dir"]) / "live_readiness.csv"
        atomic_to_csv(
            build_live_readiness_from_outputs(candidate_config),
            readiness_path,
            index=False,
            encoding="utf-8-sig",
        )
        paths["selected-readiness:live_readiness.csv"] = readiness_path
        strict_after, strict_after_value = _candidate_strict_live_readiness_status(
            candidate_config,
            selected_id,
        )
        status = "refreshed_ready" if strict_after else "refreshed_blocked"
        message = (
            "Selected winner evidence is current and strict-ready for human activation review."
            if strict_after
            else f"Selected winner evidence refreshed but remains blocked: {strict_after_value}"
        )
    except Exception as exc:  # fail closed while preserving the daily observation loop
        message = f"{type(exc).__name__}: {exc}"

    after = {str(path): file_state(path) for path in protected}
    protected_unchanged = before == after
    if not protected_unchanged:
        newly_created = [
            path
            for path in protected
            if not before[str(path)][0] and after[str(path)][0]
        ]
        for path in newly_created:
            path.unlink(missing_ok=True)
        restored = {str(path): file_state(path) for path in protected} == before
        status = "safety_violation_recovered" if restored else "safety_violation"
        strict_after = False
        message = (
            "Protected artifacts changed during evidence refresh; newly created artifacts were removed."
            if restored
            else "Protected artifacts changed during evidence refresh and could not be fully restored."
        )
    _write_audit(
        audit_path,
        status=status,
        selected_id=selected_id,
        selected_path=selected_path,
        selection_ready=True,
        winner_ok=True,
        cache_fingerprint=fingerprint,
        attempted=True,
        strict_before=False,
        strict_after=strict_after,
        protected_unchanged=protected_unchanged,
        message=message,
    )
    paths["selected_candidate_evidence_refresh.csv"] = audit_path
    return paths


def candidate_cache_fingerprint(config: dict[str, Any]) -> str:
    cache_dir = project_path(config["paths"]["cache_dir"])
    entries = []
    if cache_dir.exists():
        for path in sorted(item for item in cache_dir.rglob("*") if item.is_file()):
            stat = path.stat()
            entries.append(
                {
                    "path": str(path.relative_to(cache_dir)).replace(chr(92), "/"),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    return mapping_sha256({"cache": entries}) if entries else ""


def _registered_candidates(base_config: dict[str, Any]) -> dict[str, Path]:
    validation = base_config.get("validation", {})
    validation = validation if isinstance(validation, dict) else {}
    values = validation.get("forward_only_candidate_configs", [])
    values = values if isinstance(values, list) else []
    result: dict[str, Path] = {}
    for value in values:
        path = project_path(str(value)).resolve()
        try:
            config = load_config(path)
            manifest = load_candidate_manifest(config) or {}
        except (OSError, ValueError, TypeError, KeyError):
            continue
        candidate_id = str(manifest.get("candidate_id", "") or "").strip()
        if candidate_id and candidate_id not in result:
            result[candidate_id] = path
    return result


def _protected_paths(base_config: dict[str, Any], candidate_config: dict[str, Any]) -> list[Path]:
    base_output = project_path(base_config["paths"]["output_dir"])
    candidate_output = project_path(candidate_config["paths"]["output_dir"])
    return [
        active_execution_path(base_config),
        base_output / "orders.csv",
        base_output / "rpa_orders.csv",
        candidate_output / "orders.csv",
        candidate_output / "rpa_orders.csv",
    ]


def _same_blocked_fingerprint(previous: pd.DataFrame, candidate_id: str, fingerprint: str) -> bool:
    if len(previous) != 1 or not fingerprint:
        return False
    row = previous.iloc[-1]
    return bool(
        str(row.get("selected_candidate_id", "") or "").strip() == candidate_id
        and str(row.get("cache_fingerprint", "") or "").strip() == fingerprint
        and str(row.get("status", "") or "").strip().lower()
        in {"refreshed_blocked", "blocked_evidence_unchanged"}
        and _truth(row.get("protected_artifacts_unchanged"))
    )


def _write_audit(
    path: Path,
    *,
    status: str,
    selected_id: str,
    selected_path: Path | None,
    selection_ready: bool,
    winner_ok: bool,
    cache_fingerprint: str,
    attempted: bool,
    strict_before: bool,
    strict_after: bool,
    protected_unchanged: bool,
    message: str,
) -> None:
    atomic_to_csv(
        pd.DataFrame(
            [
                {
                    "status": status,
                    "selected_candidate_id": selected_id,
                    "selected_candidate_config": "" if selected_path is None else str(selected_path),
                    "selection_ready": selection_ready,
                    "winner_identity_valid": winner_ok,
                    "cache_fingerprint": cache_fingerprint,
                    "refresh_attempted": attempted,
                    "strict_ready_before": strict_before,
                    "strict_ready_after": strict_after,
                    "protected_artifacts_unchanged": protected_unchanged,
                    "broker_submit_enabled": False,
                    "message": message,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            ],
            columns=SELECTED_REFRESH_COLUMNS,
        ),
        path,
        index=False,
        encoding="utf-8-sig",
    )


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (FileNotFoundError, pd.errors.EmptyDataError, OSError):
        return pd.DataFrame()


def _truth(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}
