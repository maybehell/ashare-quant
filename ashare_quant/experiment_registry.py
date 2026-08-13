from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audit import mapping_sha256
from .candidate import live_strategy_hash
from .config import project_path


REGISTRY_SCHEMA_VERSION = 1
REQUIRED_THRESHOLDS = {
    "min_total_excess_return",
    "min_information_ratio",
    "max_drawdown",
    "min_out_of_sample_excess_return",
    "min_walk_forward_folds",
    "min_walk_forward_positive_ratio",
}


def load_experiment_registry(path: str | Path) -> dict[str, Any]:
    target = project_path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Research experiment registry is missing: {target}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Research experiment registry is unreadable: {target}") from exc
    if not isinstance(value, dict):
        raise ValueError("Research experiment registry must contain a mapping.")
    return value


def require_history_experiment_registration(config: dict[str, Any]) -> dict[str, Any]:
    validation = config.get("validation", {})
    registration = validation.get("experiment_registration", {}) if isinstance(validation, dict) else {}
    if not isinstance(registration, dict) or not bool(registration.get("required", False)):
        return {}

    registry_path = str(registration.get("registry_path", "") or "").strip()
    experiment_id = str(registration.get("experiment_id", "") or "").strip()
    config_path = str(registration.get("config_path", "") or "").replace("\\", "/").strip()
    errors: list[str] = []
    if not registry_path:
        errors.append("registry_path is required")
    if not experiment_id:
        errors.append("experiment_id is required")
    if not config_path:
        errors.append("config_path is required")
    if errors:
        raise ValueError("Invalid experiment registration: " + "; ".join(errors))

    registry = load_experiment_registry(registry_path)
    if int(registry.get("schema_version", 0) or 0) != REGISTRY_SCHEMA_VERSION:
        errors.append("unsupported registry schema_version")
    family_id = str(registry.get("family_id", "") or "").strip()
    max_experiments = int(registry.get("max_experiments", 0) or 0)
    family_alpha = float(registry.get("family_wise_alpha", 0) or 0)
    if not family_id:
        errors.append("family_id is required")
    if max_experiments <= 0:
        errors.append("max_experiments must be positive")
    if not 0 < family_alpha <= 0.05:
        errors.append("family_wise_alpha must be in (0, 0.05]")
    if str(registry.get("multiple_testing_method", "")) != "bonferroni_fixed_family":
        errors.append("multiple_testing_method must be bonferroni_fixed_family")

    entries = registry.get("experiments", [])
    if not isinstance(entries, list) or not entries:
        errors.append("experiments must be a non-empty list")
        entries = []
    sequences = [int(item.get("sequence", 0) or 0) for item in entries if isinstance(item, dict)]
    identifiers = [str(item.get("experiment_id", "") or "") for item in entries if isinstance(item, dict)]
    if sequences != list(range(1, len(entries) + 1)):
        errors.append("experiment sequences must be contiguous and ordered from 1")
    if len(set(identifiers)) != len(identifiers) or any(not value for value in identifiers):
        errors.append("experiment ids must be non-empty and unique")
    if len(entries) > max_experiments:
        errors.append("registered experiment count exceeds max_experiments")

    matches = [item for item in entries if isinstance(item, dict) and item.get("experiment_id") == experiment_id]
    if len(matches) != 1:
        errors.append("experiment_id must match exactly one registry entry")
        entry: dict[str, Any] = {}
    else:
        entry = matches[0]

    sequence = int(entry.get("sequence", 0) or 0)
    expected_alpha = family_alpha / max_experiments if max_experiments else 0.0
    nominal_alpha = float(entry.get("nominal_alpha", 0) or 0)
    if sequence <= 0:
        errors.append("experiment sequence is invalid")
    if abs(nominal_alpha - expected_alpha) > 1e-12:
        errors.append("nominal_alpha does not match the fixed Bonferroni budget")
    if str(entry.get("config_path", "") or "").replace("\\", "/") != config_path:
        errors.append("registered config_path does not match configuration")
    if str(entry.get("strategy_sha256", "") or "").lower() != live_strategy_hash(config):
        errors.append("registered strategy_sha256 does not match current strategy")
    thresholds = entry.get("thresholds", {})
    if not isinstance(thresholds, dict) or not REQUIRED_THRESHOLDS.issubset(thresholds):
        errors.append("registered decision thresholds are incomplete")
    if str(entry.get("status", "") or "") not in {"preregistered", "rejected", "completed"}:
        errors.append("registered status is unsupported")
    if errors:
        raise ValueError("Invalid experiment registration: " + "; ".join(errors))

    immutable_entries = []
    for item in entries[:sequence]:
        immutable_entries.append({key: item.get(key) for key in (
            "sequence", "experiment_id", "config_path", "strategy_sha256",
            "hypothesis", "registered_at", "nominal_alpha", "thresholds",
        )})
    identity = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "family_id": family_id,
        "family_wise_alpha": family_alpha,
        "multiple_testing_method": "bonferroni_fixed_family",
        "max_experiments": max_experiments,
        "experiment_sequence": sequence,
        "experiments": immutable_entries,
    }
    return {
        "experiment_id": experiment_id,
        "experiment_sequence": sequence,
        "experiment_family_id": family_id,
        "experiment_nominal_alpha": nominal_alpha,
        "experiment_preregistration_sha256": mapping_sha256(identity),
    }
