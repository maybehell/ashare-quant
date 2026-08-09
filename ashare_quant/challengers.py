from __future__ import annotations

from typing import Any


def primary_challenger_config_paths(config: dict[str, Any]) -> list[str]:
    """Return the pre-registered challenger set while preserving legacy configs."""
    validation = config.get("validation", {})
    if not isinstance(validation, dict):
        return []
    configured = validation.get("primary_challenger_configs")
    if configured is None:
        configured = [validation.get("primary_challenger_config", "")]
    elif isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, list):
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for value in configured:
        path = str(value or "").strip()
        key = path.replace(chr(92), "/").casefold()
        if path and key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def comparison_candidate_config_paths(config: dict[str, Any]) -> list[str]:
    """Return formal candidate configs retained for model reconciliation only."""
    validation = config.get("validation", {})
    if not isinstance(validation, dict):
        return []
    configured = validation.get("comparison_candidate_configs", [])
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, list):
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for value in configured:
        path = str(value or "").strip()
        key = path.replace(chr(92), "/").casefold()
        if path and key not in seen:
            seen.add(key)
            paths.append(path)
    return paths
