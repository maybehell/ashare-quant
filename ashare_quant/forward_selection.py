from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .audit import mapping_sha256
from .config import load_config, project_path
from .io_utils import atomic_to_csv, replace_with_retry


FORWARD_SELECTION_SCHEMA_VERSION = 2
FORWARD_CURVE_LEDGER_SCHEMA_VERSION = 1
ZERO_SHA256 = "0" * 64
PAIR_COLUMNS = [
    "candidate_a_id",
    "candidate_b_id",
    "candidate_a_name",
    "candidate_b_name",
    "common_start",
    "common_end",
    "common_trading_days",
    "evaluated_trading_days",
    "required_common_trading_days",
    "candidate_a_return",
    "candidate_b_return",
    "candidate_a_advantage",
    "annualized_mean_return_difference",
    "return_correlation",
    "bootstrap_samples",
    "block_days",
    "probability_candidate_a_superior",
    "lower_95_advantage",
    "upper_95_advantage",
    "favored_candidate_id",
    "status",
    "message",
]
SUMMARY_COLUMNS = [
    "status",
    "selection_ready",
    "selected_candidate_id",
    "selected_candidate_name",
    "candidate_count",
    "structural_family_count",
    "largest_structural_family_share",
    "structural_diversity_ready",
    "pair_count",
    "ready_pair_count",
    "minimum_observed_forward_trading_days",
    "minimum_common_trading_days",
    "required_common_trading_days",
    "evaluation_mode",
    "candidate_set_sha256",
    "policy_sha256",
    "policy_valid",
    "curve_ledger_valid",
    "curve_ledger_sha256",
    "minimum_curve_ledger_rows",
    "evidence_after",
    "generated_at",
    "message",
]
STRUCTURAL_FAMILY_COLUMNS = [
    "candidate_id",
    "candidate_name",
    "candidate_config",
    "structural_family_sha256",
    "structural_family_size",
]


def forward_selection_settings(
    config: dict[str, Any],
    observation: pd.DataFrame,
) -> dict[str, object]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured = validation.get("forward_selection", {})
    configured = configured if isinstance(configured, dict) else {}
    required_values = pd.to_numeric(
        observation.get("required_forward_trading_days", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    default_days = int(required_values.max()) if not required_values.empty else 60
    return {
        "schema_version": FORWARD_SELECTION_SCHEMA_VERSION,
        "min_common_trading_days": max(
            int(configured.get("min_common_trading_days", default_days)), 20
        ),
        "block_days": max(int(configured.get("block_days", 5)), 1),
        "bootstrap_samples": max(int(configured.get("bootstrap_samples", 5000)), 500),
        "bootstrap_seed": int(configured.get("bootstrap_seed", 20260731)),
        "min_superiority_probability": min(
            max(float(configured.get("min_superiority_probability", 0.95)), 0.50),
            0.999,
        ),
        "min_cumulative_advantage": max(
            float(configured.get("min_cumulative_advantage", 0.0)), 0.0
        ),
        "evaluation_mode": "fixed_horizon",
    }


def candidate_set_records(observation: pd.DataFrame) -> list[dict[str, str]]:
    required = {
        "candidate_id",
        "candidate_name",
        "candidate_config",
        "candidate_output_dir",
    }
    if observation.empty or not required.issubset(observation.columns):
        return []
    rows: list[dict[str, str]] = []
    for row in observation.to_dict(orient="records"):
        candidate_id = str(row.get("candidate_id", "") or "").strip()
        config_path = str(row.get("candidate_config", "") or "").strip()
        output_dir = str(row.get("candidate_output_dir", "") or "").strip()
        if not candidate_id or not config_path or not output_dir:
            continue
        try:
            candidate_config = load_config(project_path(config_path))
        except (OSError, ValueError, TypeError, KeyError):
            continue
        strategy_hash = ""
        manifest_path = project_path(
            candidate_config.get("validation", {}).get("candidate_path", "")
        )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_id = str(manifest.get("candidate_id", "") or "").strip()
            strategy_hash = str(manifest.get("strategy_hash", "") or "").strip()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            manifest_id = ""
        if manifest_id != candidate_id or not strategy_hash:
            continue
        rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_name": str(row.get("candidate_name", "") or "").strip(),
                "candidate_config": config_path.replace(chr(92), "/"),
                "candidate_output_dir": str(Path(output_dir).resolve()),
                "strategy_hash": strategy_hash,
            }
        )
    return sorted(rows, key=lambda item: item["candidate_id"])


def candidate_set_sha256(records: list[dict[str, str]]) -> str:
    identity = [
        {
            "candidate_id": row["candidate_id"],
            "candidate_config": row["candidate_config"],
            "strategy_hash": row["strategy_hash"],
        }
        for row in records
    ]
    return mapping_sha256({"candidates": identity}) if identity else ""


def candidate_structural_families(records: list[dict[str, str]]) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for record in records:
        try:
            candidate_config = load_config(project_path(record["candidate_config"]))
        except (OSError, ValueError, TypeError, KeyError):
            continue
        strategy = candidate_config.get("strategy", {})
        strategy = strategy if isinstance(strategy, dict) else {}
        identity = {key: value for key, value in strategy.items() if key != "name"}
        rows.append({
            "candidate_id": record["candidate_id"],
            "candidate_name": record["candidate_name"],
            "candidate_config": record["candidate_config"],
            "structural_family_sha256": mapping_sha256(identity),
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=STRUCTURAL_FAMILY_COLUMNS)
    frame["structural_family_size"] = (
        frame.groupby("structural_family_sha256")["candidate_id"].transform("count").astype(int)
    )
    return frame[STRUCTURAL_FAMILY_COLUMNS].sort_values("candidate_id").reset_index(drop=True)


def selection_policy_path(config: dict[str, Any]) -> Path:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    value = str(
        validation.get(
            "forward_selection_policy_path",
            "data/forward_candidate_selection_policy.json",
        )
        or ""
    ).strip()
    return project_path(value)


def curve_ledger_path(config: dict[str, Any]) -> Path:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    value = str(validation.get("forward_curve_ledger_path", "") or "").strip()
    if value:
        return project_path(value)
    policy_path = selection_policy_path(config)
    return policy_path.with_name(f"{policy_path.stem}_curve_ledger.json")


def audit_forward_curve_ledger(
    config: dict[str, Any],
    records: list[dict[str, str]],
    summary_row: pd.Series | dict[str, object],
) -> tuple[bool, str]:
    path = curve_ledger_path(config)
    policy_path = selection_policy_path(config)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, f"ledger_or_policy_unreadable={type(exc).__name__}"

    set_hash = candidate_set_sha256(records)
    policy_hash = str(summary_row.get("policy_sha256", "") or "").strip()
    evidence_after = str(summary_row.get("evidence_after", "") or "").strip()
    required_rows = _safe_int(summary_row.get("required_common_trading_days"))
    expected_settings = forward_selection_settings(
        config,
        pd.DataFrame({"required_forward_trading_days": [required_rows]}),
    )
    expected_policy_hash = mapping_sha256(expected_settings)
    policy_ok = bool(
        int(policy.get("schema_version", 0) or 0) == FORWARD_SELECTION_SCHEMA_VERSION
        and str(policy.get("candidate_set_sha256", "") or "") == set_hash
        and str(policy.get("policy_sha256", "") or "") == expected_policy_hash
        and policy.get("settings") == expected_settings
        and str(policy.get("evidence_after", "") or "") == evidence_after
        and policy_hash == expected_policy_hash
        and str(summary_row.get("evaluation_mode", "") or "") == "fixed_horizon"
    )
    actual_hash = mapping_sha256(payload)
    expected_hash = str(summary_row.get("curve_ledger_sha256", "") or "").strip()
    candidates = payload.get("candidates", {})
    candidate_ids = {record["candidate_id"] for record in records}
    identity_ok = bool(
        policy_ok
        and int(payload.get("schema_version", 0) or 0) == FORWARD_CURVE_LEDGER_SCHEMA_VERSION
        and set_hash
        and str(payload.get("candidate_set_sha256", "") or "") == set_hash
        and str(payload.get("policy_sha256", "") or "") == policy_hash
        and str(payload.get("evidence_after", "") or "") == evidence_after
        and isinstance(candidates, dict)
        and set(candidates) == candidate_ids
        and all(
            isinstance(candidates.get(record["candidate_id"]), dict)
            and str(candidates[record["candidate_id"]].get("strategy_hash", "") or "")
            == record["strategy_hash"]
            for record in records
        )
    )
    if not identity_ok:
        return False, f"policy={policy_ok};ledger_identity_match=False"

    curves: dict[str, pd.DataFrame] = {}
    for record in records:
        curve, error = _load_curve(
            Path(record["candidate_output_dir"]) / "shadow_continuous_nav.csv",
            record,
        )
        if error:
            return False, f"curve={record['candidate_id']}:{error}"
        curves[record["candidate_id"]] = curve
    proposed, error = _prepare_curve_ledger(
        path,
        records=records,
        curves=curves,
        candidate_set_hash=set_hash,
        policy_hash=policy_hash,
        evidence_after=evidence_after,
    )
    if error:
        return False, error
    current_ok = proposed.get("candidates") == candidates
    minimum_rows = _minimum_ledger_rows(payload)
    summary_rows = _safe_int(summary_row.get("minimum_curve_ledger_rows"))
    selection_ready = str(summary_row.get("selection_ready", "")).strip().lower() in {
        "1", "true", "yes", "y"
    }
    passed = bool(
        current_ok
        and expected_hash
        and actual_hash == expected_hash
        and str(summary_row.get("curve_ledger_valid", "")).strip().lower()
        in {"1", "true", "yes", "y"}
        and minimum_rows == summary_rows
        and (not selection_ready or (required_rows > 0 and minimum_rows >= required_rows))
    )
    return passed, (
        f"policy={policy_ok};identity={identity_ok};current={current_ok};hash_match={actual_hash == expected_hash};"
        f"rows={minimum_rows}/{required_rows};summary_rows={summary_rows}"
    )


def upgrade_forward_selection_policy_before_evidence(
    config: dict[str, Any],
    observation: pd.DataFrame,
) -> dict[str, Path]:
    policy_path = selection_policy_path(config)
    ledger_path = curve_ledger_path(config)
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Forward selection upgrade evidence is unreadable: {type(exc).__name__}") from exc

    records = candidate_set_records(observation)
    set_hash = candidate_set_sha256(records)
    evidence_after = str(policy.get("evidence_after", "") or "").strip()
    cutoff = pd.to_datetime(evidence_after, errors="coerce")
    old_schema = int(policy.get("schema_version", 0) or 0)
    old_policy_hash = str(policy.get("policy_sha256", "") or "").strip()
    new_settings = forward_selection_settings(config, observation)
    new_policy_hash = mapping_sha256(new_settings)
    if old_schema == FORWARD_SELECTION_SCHEMA_VERSION:
        if (
            str(policy.get("candidate_set_sha256", "") or "") == set_hash
            and old_policy_hash == new_policy_hash
            and str(ledger.get("policy_sha256", "") or "") == new_policy_hash
        ):
            return {
                "forward_candidate_selection_policy.json": policy_path,
                "forward_candidate_curve_ledger.json": ledger_path,
            }
        raise RuntimeError("Current forward selection policy does not match configured settings.")
    if old_schema != FORWARD_SELECTION_SCHEMA_VERSION - 1:
        raise RuntimeError(f"Unsupported forward selection policy upgrade: schema={old_schema}.")
    if pd.isna(cutoff) or not records or str(policy.get("candidate_set_sha256", "") or "") != set_hash:
        raise RuntimeError("Forward selection upgrade identity or evidence boundary is invalid.")

    curves: dict[str, pd.DataFrame] = {}
    for record in records:
        curve, error = _load_curve(
            Path(record["candidate_output_dir"]) / "shadow_continuous_nav.csv",
            record,
        )
        if error:
            raise RuntimeError(f"Candidate curve is invalid: {record['candidate_id']}:{error}")
        if (curve["date"] > cutoff).any():
            raise RuntimeError("Post-policy return evidence exists; statistical-policy upgrade is forbidden.")
        curves[record["candidate_id"]] = curve

    ledger_candidates = ledger.get("candidates", {})
    ledger_ok = bool(
        int(ledger.get("schema_version", 0) or 0) == FORWARD_CURVE_LEDGER_SCHEMA_VERSION
        and str(ledger.get("candidate_set_sha256", "") or "") == set_hash
        and str(ledger.get("policy_sha256", "") or "") == old_policy_hash
        and str(ledger.get("evidence_after", "") or "") == evidence_after
        and isinstance(ledger_candidates, dict)
        and set(ledger_candidates) == {record["candidate_id"] for record in records}
        and all(
            isinstance(row, dict)
            and _safe_int(row.get("post_policy_rows")) == 0
            and str(row.get("chain_tail_sha256", "") or "") == ZERO_SHA256
            for row in ledger_candidates.values()
        )
    )
    if not ledger_ok:
        raise RuntimeError("Forward curve ledger is not a zero-evidence ledger bound to the old policy.")

    archive_dir = policy_path.with_name(policy_path.stem + "_archive")
    archive_dir.mkdir(parents=True, exist_ok=True)
    policy_archive = archive_dir / f"upgrade_{old_policy_hash[:16]}_schema{old_schema}.json"
    ledger_archive = archive_dir / f"upgrade_{old_policy_hash[:16]}_curve_ledger.json"
    for payload, archive_path, label in (
        (policy, policy_archive, "policy"),
        (ledger, ledger_archive, "curve ledger"),
    ):
        if archive_path.exists():
            archived = json.loads(archive_path.read_text(encoding="utf-8"))
            if archived != payload:
                raise RuntimeError(f"Existing {label} upgrade archive does not match current evidence.")
        else:
            _atomic_json(payload, archive_path)

    upgraded_at = datetime.now().astimezone().isoformat(timespec="seconds")
    upgraded_policy = dict(policy)
    upgraded_policy.update({
        "schema_version": FORWARD_SELECTION_SCHEMA_VERSION,
        "settings": new_settings,
        "policy_sha256": new_policy_hash,
        "upgraded_at": upgraded_at,
        "upgraded_from_schema_version": old_schema,
        "upgraded_from_policy_sha256": old_policy_hash,
    })
    upgraded_ledger = dict(ledger)
    upgraded_ledger.update({
        "policy_sha256": new_policy_hash,
        "updated_at": upgraded_at,
    })
    try:
        _atomic_json(upgraded_ledger, ledger_path)
        _atomic_json(upgraded_policy, policy_path)
    except OSError:
        _atomic_json(ledger, ledger_path)
        _atomic_json(policy, policy_path)
        raise
    return {
        "forward_candidate_selection_policy.json": policy_path,
        "forward_candidate_selection_policy_archive.json": policy_archive,
        "forward_candidate_curve_ledger.json": ledger_path,
        "forward_candidate_curve_ledger_archive.json": ledger_archive,
    }


def expand_forward_selection_policy_before_evidence(
    config: dict[str, Any],
    observation: pd.DataFrame,
) -> dict[str, Path]:
    path = selection_policy_path(config)
    if not path.exists():
        raise RuntimeError("Forward selection policy is missing.")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Forward selection policy is unreadable: {type(exc).__name__}") from exc

    records = candidate_set_records(observation)
    settings = forward_selection_settings(config, observation)
    policy_hash = mapping_sha256(settings)
    if str(policy.get("policy_sha256", "") or "") != policy_hash:
        raise RuntimeError("Forward selection settings changed; expansion is forbidden.")
    old_candidates = policy.get("candidates", [])
    old_ids = {
        str(row.get("candidate_id", "") or "")
        for row in old_candidates
        if isinstance(row, dict)
    }
    current_ids = {row["candidate_id"] for row in records}
    if not old_ids or not old_ids < current_ids:
        raise RuntimeError("Policy expansion must retain every frozen candidate and add at least one new candidate.")

    evidence_after = pd.to_datetime(policy.get("evidence_after"), errors="coerce")
    if pd.isna(evidence_after):
        raise RuntimeError("Frozen evidence boundary is invalid.")
    new_ids = current_ids - old_ids
    required_curve_columns = {"date", "candidate_id", "strategy_hash", "portfolio_nav", "benchmark_nav"}
    for record in records:
        curve_path = Path(record["candidate_output_dir"]) / "shadow_continuous_nav.csv"
        curve, error = _load_curve(curve_path, record)
        if error:
            empty_at_boundary = False
            if record["candidate_id"] in new_ids:
                try:
                    raw_curve = pd.read_csv(curve_path)
                    candidate_config = load_config(project_path(record["candidate_config"]))
                    manifest_path = project_path(candidate_config["validation"]["candidate_path"])
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    freeze_date = pd.to_datetime(manifest.get("freeze_price_end"), errors="coerce")
                    empty_at_boundary = (
                        raw_curve.empty
                        and required_curve_columns.issubset(raw_curve.columns)
                        and not pd.isna(freeze_date)
                        and freeze_date == evidence_after
                    )
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    empty_at_boundary = False
            if not empty_at_boundary:
                raise RuntimeError(f"Candidate curve is invalid: {record['candidate_id']}:{error}")
            continue
        if (curve["date"] > evidence_after).any():
            raise RuntimeError("Post-policy return evidence already exists; candidate-set expansion is forbidden.")

    archive_dir = path.with_name(path.stem + "_archive")
    archive_dir.mkdir(parents=True, exist_ok=True)
    old_set_hash = str(policy.get("candidate_set_sha256", "") or "")
    archive_path = archive_dir / f"{old_set_hash[:16] or 'unknown'}.json"
    if archive_path.exists():
        archived = json.loads(archive_path.read_text(encoding="utf-8"))
        if archived != policy:
            raise RuntimeError("Existing selection-policy archive does not match the current policy.")
    else:
        _atomic_json(policy, archive_path)

    new_set_hash = candidate_set_sha256(records)
    expanded = dict(policy)
    expanded.update({
        "candidate_set_sha256": new_set_hash,
        "candidates": [
            {
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"],
                "candidate_config": row["candidate_config"],
                "strategy_hash": row["strategy_hash"],
            }
            for row in records
        ],
        "expanded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "expanded_from_candidate_set_sha256": old_set_hash,
    })
    ledger_path, ledger_archive_path = _expand_curve_ledger_before_evidence(
        config,
        records=records,
        old_candidate_ids=old_ids,
        old_candidate_set_hash=old_set_hash,
        new_candidate_set_hash=new_set_hash,
        policy_hash=policy_hash,
        evidence_after=evidence_after.date().isoformat(),
        archive_dir=archive_dir,
    )
    _atomic_json(expanded, path)
    return {
        "forward_candidate_selection_policy.json": path,
        "forward_candidate_selection_policy_archive.json": archive_path,
        "forward_candidate_curve_ledger.json": ledger_path,
        "forward_candidate_curve_ledger_archive.json": ledger_archive_path,
    }


def write_forward_candidate_selection(
    config: dict[str, Any],
    observation: pd.DataFrame,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    pair_path = output_dir / "forward_candidate_selection.csv"
    summary_path = output_dir / "forward_candidate_selection_summary.csv"
    family_path = output_dir / "forward_candidate_structural_families.csv"
    policy_path = selection_policy_path(config)
    records = candidate_set_records(observation)
    settings = forward_selection_settings(config, observation)
    set_hash = candidate_set_sha256(records)
    families = candidate_structural_families(records)
    family_count = int(families["structural_family_sha256"].nunique()) if not families.empty else 0
    largest_family_share = (
        float(families["structural_family_size"].max() / len(families))
        if not families.empty else 1.0
    )
    structural_diversity_ready = family_count >= 2 and len(families) == len(records)
    policy_hash = mapping_sha256(settings)

    curves: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    for record in records:
        curve_path = Path(record["candidate_output_dir"]) / "shadow_continuous_nav.csv"
        curve, error = _load_curve(curve_path, record)
        if error:
            errors.append(f"{record['candidate_id']}:{error}")
        else:
            curves[record["candidate_id"]] = curve

    policy, policy_error = _load_or_create_policy(
        policy_path,
        settings=settings,
        policy_hash=policy_hash,
        candidate_set_hash=set_hash,
        records=records,
        curves=curves,
        allow_create=not summary_path.exists(),
    )
    if policy_error:
        errors.append(policy_error)
    policy_valid = not policy_error
    evidence_after = str(policy.get("evidence_after", "") or "")
    ledger_path = curve_ledger_path(config)
    ledger_payload: dict[str, object] = {}
    ledger_error = ""
    if policy_valid and len(curves) == len(records):
        ledger_payload, ledger_error = _prepare_curve_ledger(
            ledger_path,
            records=records,
            curves=curves,
            candidate_set_hash=set_hash,
            policy_hash=policy_hash,
            evidence_after=evidence_after,
        )
        if ledger_error:
            errors.append(ledger_error)
    pairs = (
        _build_pairs(records, curves, settings, evidence_after)
        if policy_valid and len(curves) == len(records) and not ledger_error
        else []
    )
    pair_frame = pd.DataFrame(pairs, columns=PAIR_COLUMNS)
    ledger_valid = bool(ledger_payload) and not ledger_error
    if ledger_valid and not pair_frame.empty and (pair_frame["status"] == "invalid_evidence").any():
        ledger_valid = False
    if ledger_valid:
        try:
            _atomic_json(ledger_payload, ledger_path)
        except OSError as exc:
            ledger_valid = False
            errors.append(f"curve_ledger_write_failed:{type(exc).__name__}")

    selected_id = ""
    status = "collecting_forward_data"
    message = "Pairwise selection is collecting post-policy forward returns."
    if len(records) < 2:
        status = "invalid_candidate_set"
        message = "At least two identity-bound forward candidates are required."
    elif not structural_diversity_ready:
        status = "structurally_redundant_candidate_set"
        message = (
            "Forward candidates must contain at least two distinct stock-selection "
            "structures; exposure overlays alone are not independent strategies."
        )
    elif errors:
        status = "invalid_evidence"
        message = "; ".join(errors)
    elif len(pair_frame) != len(list(combinations(records, 2))):
        status = "invalid_evidence"
        message = "Pairwise evidence is incomplete."
    elif not pair_frame.empty and (pair_frame["status"] == "collecting_forward_data").any():
        minimum_days = int(pd.to_numeric(pair_frame["common_trading_days"], errors="coerce").min())
        message = (
            f"Collecting common post-policy returns: {minimum_days}/"
            f"{settings['min_common_trading_days']} trading days."
        )
    elif not pair_frame.empty and (pair_frame["status"] == "invalid_evidence").any():
        status = "invalid_evidence"
        message = "At least one pair has invalid benchmark or return evidence."
    elif not pair_frame.empty:
        favored = pair_frame["favored_candidate_id"].fillna("").astype(str)
        wins = {
            record["candidate_id"]: int((favored == record["candidate_id"]).sum())
            for record in records
        }
        required_wins = len(records) - 1
        winners = [candidate_id for candidate_id, count in wins.items() if count == required_wins]
        if len(winners) == 1 and favored.ne("").all():
            status = "ready"
            selected_id = winners[0]
            message = "One candidate dominates every paired comparison under the frozen policy."
        else:
            status = "no_clear_winner"
            message = "No candidate dominates every paired comparison under the frozen policy."

    selected_name = next(
        (row["candidate_name"] for row in records if row["candidate_id"] == selected_id),
        "",
    )
    minimum_days = (
        int(pd.to_numeric(pair_frame["common_trading_days"], errors="coerce").min())
        if not pair_frame.empty
        else 0
    )
    observed_values = pd.to_numeric(
        observation.get("max_observed_trading_days", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    minimum_observed_days = int(observed_values.min()) if not observed_values.empty else 0
    ready_pairs = int((pair_frame["status"] == "dominance_ready").sum()) if not pair_frame.empty else 0
    summary = pd.DataFrame(
        [
            {
                "status": status,
                "selection_ready": status == "ready",
                "selected_candidate_id": selected_id,
                "selected_candidate_name": selected_name,
                "candidate_count": len(records),
                "structural_family_count": family_count,
                "largest_structural_family_share": largest_family_share,
                "structural_diversity_ready": structural_diversity_ready,
                "pair_count": len(pair_frame),
                "ready_pair_count": ready_pairs,
                "minimum_observed_forward_trading_days": minimum_observed_days,
                "minimum_common_trading_days": minimum_days,
                "required_common_trading_days": settings["min_common_trading_days"],
                "evaluation_mode": settings["evaluation_mode"],
                "candidate_set_sha256": set_hash,
                "policy_sha256": policy_hash,
                "policy_valid": policy_valid,
                "curve_ledger_valid": ledger_valid,
                "curve_ledger_sha256": mapping_sha256(ledger_payload) if ledger_valid else "",
                "minimum_curve_ledger_rows": _minimum_ledger_rows(ledger_payload),
                "evidence_after": evidence_after,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "message": message,
            }
        ],
        columns=SUMMARY_COLUMNS,
    )
    atomic_to_csv(pair_frame, pair_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(families, family_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    return {
        "forward_candidate_selection.csv": pair_path,
        "forward_candidate_selection_summary.csv": summary_path,
        "forward_candidate_structural_families.csv": family_path,
        "forward_candidate_selection_policy.json": policy_path,
        "forward_candidate_curve_ledger.json": ledger_path,
    }


def _load_or_create_policy(
    path: Path,
    *,
    settings: dict[str, object],
    policy_hash: str,
    candidate_set_hash: str,
    records: list[dict[str, str]],
    curves: dict[str, pd.DataFrame],
    allow_create: bool,
) -> tuple[dict[str, object], str]:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return {}, f"selection_policy_unreadable:{type(exc).__name__}"
        checks = {
            "schema_version": int(payload.get("schema_version", 0) or 0)
            == FORWARD_SELECTION_SCHEMA_VERSION,
            "candidate_set_sha256": str(payload.get("candidate_set_sha256", "") or "")
            == candidate_set_hash,
            "policy_sha256": str(payload.get("policy_sha256", "") or "") == policy_hash,
            "evidence_after": not pd.isna(
                pd.to_datetime(payload.get("evidence_after"), errors="coerce")
            ),
        }
        failed = [name for name, passed in checks.items() if not passed]
        return payload, "" if not failed else "selection_policy_mismatch:" + ",".join(failed)

    if not allow_create:
        return {}, "selection_policy_missing_after_initialization"
    if len(records) < 2 or len(curves) != len(records):
        return {}, "selection_policy_not_created:complete candidate curves required"
    latest_dates = [
        pd.to_datetime(curve["date"], errors="coerce").max()
        for curve in curves.values()
    ]
    if any(pd.isna(value) for value in latest_dates):
        return {}, "selection_policy_not_created:valid curve dates required"
    evidence_after = max(latest_dates).date().isoformat()
    payload: dict[str, object] = {
        "schema_version": FORWARD_SELECTION_SCHEMA_VERSION,
        "frozen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "evidence_after": evidence_after,
        "candidate_set_sha256": candidate_set_hash,
        "policy_sha256": policy_hash,
        "settings": settings,
        "candidates": [
            {
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"],
                "candidate_config": row["candidate_config"],
                "strategy_hash": row["strategy_hash"],
            }
            for row in records
        ],
    }
    _atomic_json(payload, path)
    return payload, ""


def _load_curve(path: Path, record: dict[str, str]) -> tuple[pd.DataFrame, str]:
    try:
        frame = pd.read_csv(path, dtype={"candidate_id": str, "strategy_hash": str})
    except (FileNotFoundError, pd.errors.EmptyDataError, OSError) as exc:
        return pd.DataFrame(), f"curve_unreadable:{type(exc).__name__}"
    required = {"date", "candidate_id", "strategy_hash", "portfolio_nav", "benchmark_nav"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(), "curve_schema_invalid"
    if frame.empty:
        empty = frame[list(required)].copy()
        empty["date"] = pd.to_datetime(empty["date"], errors="coerce")
        empty["portfolio_nav"] = pd.to_numeric(empty["portfolio_nav"], errors="coerce")
        empty["benchmark_nav"] = pd.to_numeric(empty["benchmark_nav"], errors="coerce")
        empty["candidate_return"] = pd.Series(dtype=float)
        empty["benchmark_return"] = pd.Series(dtype=float)
        return empty, ""
    candidate_ids = set(frame["candidate_id"].fillna("").astype(str).str.strip())
    strategy_hashes = set(frame["strategy_hash"].fillna("").astype(str).str.strip())
    if candidate_ids != {record["candidate_id"]}:
        return pd.DataFrame(), "curve_candidate_id_mismatch"
    if strategy_hashes != {record["strategy_hash"]}:
        return pd.DataFrame(), "curve_strategy_hash_mismatch"
    normalized = frame[list(required)].copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["portfolio_nav"] = pd.to_numeric(normalized["portfolio_nav"], errors="coerce")
    normalized["benchmark_nav"] = pd.to_numeric(normalized["benchmark_nav"], errors="coerce")
    normalized = normalized.dropna(subset=["date", "portfolio_nav", "benchmark_nav"])
    normalized = normalized.sort_values("date").reset_index(drop=True)
    if normalized.empty or normalized["date"].duplicated().any():
        return pd.DataFrame(), "curve_dates_invalid"
    nav_values = normalized[["portfolio_nav", "benchmark_nav"]].to_numpy(dtype=float)
    if not np.isfinite(nav_values).all():
        return pd.DataFrame(), "curve_nav_non_finite"
    if (normalized[["portfolio_nav", "benchmark_nav"]] <= 0).any().any():
        return pd.DataFrame(), "curve_nav_invalid"
    normalized["candidate_return"] = normalized["portfolio_nav"].pct_change()
    normalized["benchmark_return"] = normalized["benchmark_nav"].pct_change()
    return normalized, ""


def _expand_curve_ledger_before_evidence(
    config: dict[str, Any],
    *,
    records: list[dict[str, str]],
    old_candidate_ids: set[str],
    old_candidate_set_hash: str,
    new_candidate_set_hash: str,
    policy_hash: str,
    evidence_after: str,
    archive_dir: Path,
) -> tuple[Path, Path]:
    path = curve_ledger_path(config)
    archive_path = archive_dir / f"{old_candidate_set_hash[:16] or 'unknown'}_curve_ledger.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Forward curve ledger is unreadable: {type(exc).__name__}") from exc
        candidates = existing.get("candidates", {})
        checks = {
            "schema": int(existing.get("schema_version", 0) or 0)
            == FORWARD_CURVE_LEDGER_SCHEMA_VERSION,
            "candidate_set": str(existing.get("candidate_set_sha256", "") or "")
            == old_candidate_set_hash,
            "policy": str(existing.get("policy_sha256", "") or "") == policy_hash,
            "evidence_after": str(existing.get("evidence_after", "") or "")
            == evidence_after,
            "candidate_ids": isinstance(candidates, dict)
            and set(candidates) == old_candidate_ids,
            "zero_evidence": isinstance(candidates, dict)
            and all(
                isinstance(row, dict)
                and int(row.get("post_policy_rows", 0) or 0) == 0
                and str(row.get("chain_tail_sha256", "") or "") == ZERO_SHA256
                for row in candidates.values()
            ),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise RuntimeError(
                "Forward curve ledger cannot expand after evidence: " + ",".join(failed)
            )
        if archive_path.exists():
            archived = json.loads(archive_path.read_text(encoding="utf-8"))
            if archived != existing:
                raise RuntimeError("Existing curve-ledger archive does not match the current ledger.")
        else:
            _atomic_json(existing, archive_path)

    expanded = {
        "schema_version": FORWARD_CURVE_LEDGER_SCHEMA_VERSION,
        "candidate_set_sha256": new_candidate_set_hash,
        "policy_sha256": policy_hash,
        "evidence_after": evidence_after,
        "candidates": {
            row["candidate_id"]: {
                "candidate_id": row["candidate_id"],
                "strategy_hash": row["strategy_hash"],
                "post_policy_rows": 0,
                "last_date": "",
                "chain_tail_sha256": ZERO_SHA256,
            }
            for row in records
        },
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _atomic_json(expanded, path)
    return path, archive_path


def _prepare_curve_ledger(
    path: Path,
    *,
    records: list[dict[str, str]],
    curves: dict[str, pd.DataFrame],
    candidate_set_hash: str,
    policy_hash: str,
    evidence_after: str,
) -> tuple[dict[str, object], str]:
    cutoff = pd.to_datetime(evidence_after, errors="coerce")
    if pd.isna(cutoff):
        return {}, "curve_ledger_evidence_boundary_invalid"

    current: dict[str, dict[str, object]] = {}
    for record in records:
        frame = curves[record["candidate_id"]]
        post_policy = frame[frame["date"] > cutoff].sort_values("date").reset_index(drop=True)
        current[record["candidate_id"]] = {
            "record": record,
            "frame": post_policy,
        }

    existing: dict[str, object]
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return {}, f"curve_ledger_unreadable:{type(exc).__name__}"
        checks = {
            "schema": int(existing.get("schema_version", 0) or 0)
            == FORWARD_CURVE_LEDGER_SCHEMA_VERSION,
            "candidate_set": str(existing.get("candidate_set_sha256", "") or "")
            == candidate_set_hash,
            "policy": str(existing.get("policy_sha256", "") or "") == policy_hash,
            "evidence_after": str(existing.get("evidence_after", "") or "")
            == evidence_after,
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            return {}, "curve_ledger_identity_mismatch:" + ",".join(failed)
        prior_candidates = existing.get("candidates", {})
        if not isinstance(prior_candidates, dict) or set(prior_candidates) != set(current):
            return {}, "curve_ledger_candidate_identity_mismatch"
    else:
        if any(len(value["frame"]) > 0 for value in current.values()):
            return {}, "curve_ledger_missing_after_evidence"
        prior_candidates = {
            candidate_id: {
                "candidate_id": candidate_id,
                "strategy_hash": value["record"]["strategy_hash"],
                "post_policy_rows": 0,
                "last_date": "",
                "chain_tail_sha256": ZERO_SHA256,
            }
            for candidate_id, value in current.items()
        }

    proposed_candidates: dict[str, dict[str, object]] = {}
    for candidate_id, value in current.items():
        record = value["record"]
        frame = value["frame"]
        prior = prior_candidates.get(candidate_id, {})
        if not isinstance(prior, dict):
            return {}, f"curve_ledger_candidate_invalid:{candidate_id}"
        if str(prior.get("strategy_hash", "") or "") != record["strategy_hash"]:
            return {}, f"curve_ledger_strategy_hash_mismatch:{candidate_id}"
        try:
            prior_rows = int(prior.get("post_policy_rows", 0) or 0)
        except (TypeError, ValueError):
            return {}, f"curve_ledger_row_count_invalid:{candidate_id}"
        if prior_rows < 0 or len(frame) < prior_rows:
            return {}, f"curve_ledger_history_rollback:{candidate_id}"
        prefix_tail = _curve_chain_tail(frame.iloc[:prior_rows])
        expected_tail = str(prior.get("chain_tail_sha256", "") or "")
        if prefix_tail != expected_tail:
            return {}, f"curve_ledger_history_rewrite:{candidate_id}"
        full_tail = _curve_chain_tail(frame)
        proposed_candidates[candidate_id] = {
            "candidate_id": candidate_id,
            "strategy_hash": record["strategy_hash"],
            "post_policy_rows": len(frame),
            "last_date": "" if frame.empty else frame["date"].max().date().isoformat(),
            "chain_tail_sha256": full_tail,
        }

    return {
        "schema_version": FORWARD_CURVE_LEDGER_SCHEMA_VERSION,
        "candidate_set_sha256": candidate_set_hash,
        "policy_sha256": policy_hash,
        "evidence_after": evidence_after,
        "candidates": proposed_candidates,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }, ""


def _curve_chain_tail(frame: pd.DataFrame) -> str:
    tail = ZERO_SHA256
    for row in frame.to_dict(orient="records"):
        date = pd.to_datetime(row.get("date"), errors="coerce")
        payload = {
            "previous_row_sha256": tail,
            "date": "" if pd.isna(date) else date.date().isoformat(),
            "candidate_id": str(row.get("candidate_id", "") or ""),
            "strategy_hash": str(row.get("strategy_hash", "") or ""),
            "portfolio_nav": format(float(row.get("portfolio_nav")), ".17g"),
            "benchmark_nav": format(float(row.get("benchmark_nav")), ".17g"),
        }
        tail = mapping_sha256(payload)
    return tail


def _minimum_ledger_rows(payload: dict[str, object]) -> int:
    candidates = payload.get("candidates", {}) if isinstance(payload, dict) else {}
    if not isinstance(candidates, dict) or not candidates:
        return 0
    values: list[int] = []
    for row in candidates.values():
        if not isinstance(row, dict):
            return 0
        try:
            values.append(int(row.get("post_policy_rows", 0) or 0))
        except (TypeError, ValueError):
            return 0
    return min(values) if values else 0


def _build_pairs(
    records: list[dict[str, str]],
    curves: dict[str, pd.DataFrame],
    settings: dict[str, object],
    evidence_after: str,
) -> list[dict[str, object]]:
    cutoff = pd.to_datetime(evidence_after, errors="coerce")
    rows: list[dict[str, object]] = []
    for left, right in combinations(records, 2):
        left_curve = curves[left["candidate_id"]]
        right_curve = curves[right["candidate_id"]]
        left_post = left_curve[left_curve["date"] > cutoff].copy()
        right_post = right_curve[right_curve["date"] > cutoff].copy()
        left_dates = set(left_post["date"])
        right_dates = set(right_post["date"])
        merged = left_post[
            ["date", "candidate_return", "benchmark_return"]
        ].merge(
            right_post[["date", "candidate_return", "benchmark_return"]],
            on="date",
            how="inner",
            suffixes=("_a", "_b"),
        )
        merged = merged.dropna().reset_index(drop=True)
        row = _evaluate_pair(left, right, merged, settings)
        if left_dates != right_dates:
            row["favored_candidate_id"] = ""
            row["status"] = "invalid_evidence"
            row["message"] = (
                "Candidates do not contain identical post-policy dates: "
                f"missing_from_a={len(right_dates - left_dates)};"
                f"missing_from_b={len(left_dates - right_dates)}."
            )
        rows.append(row)
    return rows


def _evaluate_pair(
    left: dict[str, str],
    right: dict[str, str],
    merged: pd.DataFrame,
    settings: dict[str, object],
) -> dict[str, object]:
    days = len(merged)
    required = int(settings["min_common_trading_days"])
    row: dict[str, object] = {
        "candidate_a_id": left["candidate_id"],
        "candidate_b_id": right["candidate_id"],
        "candidate_a_name": left["candidate_name"],
        "candidate_b_name": right["candidate_name"],
        "common_start": "" if merged.empty else merged["date"].min().date().isoformat(),
        "common_end": "" if merged.empty else merged["date"].max().date().isoformat(),
        "common_trading_days": days,
        "evaluated_trading_days": min(days, required),
        "required_common_trading_days": required,
        "candidate_a_return": "",
        "candidate_b_return": "",
        "candidate_a_advantage": "",
        "annualized_mean_return_difference": "",
        "return_correlation": "",
        "bootstrap_samples": int(settings["bootstrap_samples"]),
        "block_days": int(settings["block_days"]),
        "probability_candidate_a_superior": "",
        "lower_95_advantage": "",
        "upper_95_advantage": "",
        "favored_candidate_id": "",
        "status": "collecting_forward_data",
        "message": f"Need {required} common post-policy trading days.",
    }
    if merged.empty:
        return row
    benchmark_gap = (
        merged["benchmark_return_a"] - merged["benchmark_return_b"]
    ).abs().max()
    if pd.isna(benchmark_gap) or float(benchmark_gap) > 1e-10:
        row["status"] = "invalid_evidence"
        row["message"] = "Candidates do not share identical benchmark daily returns."
        return row

    evaluation = merged.iloc[:required]
    evaluation_days = len(evaluation)
    returns_a = evaluation["candidate_return_a"].to_numpy(dtype=float)
    returns_b = evaluation["candidate_return_b"].to_numpy(dtype=float)
    compound_a = _compound(returns_a)
    compound_b = _compound(returns_b)
    advantage = compound_a - compound_b
    difference = returns_a - returns_b
    row.update(
        {
            "candidate_a_return": compound_a,
            "candidate_b_return": compound_b,
            "candidate_a_advantage": advantage,
            "annualized_mean_return_difference": float(difference.mean() * 252.0),
            "return_correlation": (
                float(np.corrcoef(returns_a, returns_b)[0, 1])
                if evaluation_days >= 2
                and float(np.std(returns_a)) > 1e-15
                and float(np.std(returns_b)) > 1e-15
                else ""
            ),
        }
    )
    if days < required:
        return row

    seed_material = (
        f"{settings['bootstrap_seed']}:{left['candidate_id']}:{right['candidate_id']}"
    )
    seed = int.from_bytes(sha256(seed_material.encode("utf-8")).digest()[:8], "big")
    distribution = _paired_block_bootstrap(
        returns_a,
        returns_b,
        samples=int(settings["bootstrap_samples"]),
        block_days=int(settings["block_days"]),
        seed=seed,
    )
    probability = float(np.mean(distribution > 0))
    lower, upper = np.quantile(distribution, [0.025, 0.975])
    row.update(
        {
            "probability_candidate_a_superior": probability,
            "lower_95_advantage": float(lower),
            "upper_95_advantage": float(upper),
        }
    )
    threshold = float(settings["min_superiority_probability"])
    minimum = float(settings["min_cumulative_advantage"])
    if advantage >= minimum and probability >= threshold:
        row["favored_candidate_id"] = left["candidate_id"]
        row["status"] = "dominance_ready"
        row["message"] = "Candidate A meets the frozen superiority threshold."
    elif -advantage >= minimum and probability <= 1.0 - threshold:
        row["favored_candidate_id"] = right["candidate_id"]
        row["status"] = "dominance_ready"
        row["message"] = "Candidate B meets the frozen superiority threshold."
    else:
        row["status"] = "no_clear_winner"
        row["message"] = "Paired evidence does not establish a clear winner."
    return row


def _paired_block_bootstrap(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
    *,
    samples: int,
    block_days: int,
    seed: int,
) -> np.ndarray:
    observations = len(returns_a)
    blocks_needed = int(np.ceil(observations / block_days))
    offsets = np.arange(block_days)
    rng = np.random.default_rng(seed)
    output = np.empty(samples, dtype=float)
    for index in range(samples):
        starts = rng.integers(0, observations, size=blocks_needed)
        selected = ((starts[:, None] + offsets[None, :]) % observations).reshape(-1)
        selected = selected[:observations]
        output[index] = _compound(returns_a[selected]) - _compound(returns_b[selected])
    return output


def _compound(values: np.ndarray) -> float:
    return float(np.prod(1.0 + values) - 1.0)


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _atomic_json(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    replace_with_retry(tmp, path)
