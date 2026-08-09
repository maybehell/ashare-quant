from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "default.yaml"
ACTIVE_EXECUTION_SCHEMA_VERSION = 1
ACTIVE_EXECUTION_KEYS = {
    "benchmark",
    "breadth_overlay",
    "implementation",
    "risk_overlay",
    "rules",
    "strategy",
    "trading",
    "universe",
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                f"{path} is not JSON-compatible YAML and PyYAML is not installed."
            ) from exc
        loaded = yaml.safe_load(text)
        return loaded or {}


def _load_override(path: Path, stack: tuple[Path, ...] = ()) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved in stack:
        chain = " -> ".join(str(item) for item in (*stack, resolved))
        raise ValueError(f"Config inheritance cycle detected: {chain}")
    loaded = _load_mapping(resolved)
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must contain a mapping: {resolved}")
    current = deepcopy(loaded)
    parents = current.pop("extends", None)
    if parents is None:
        return current
    parent_values = parents if isinstance(parents, list) else [parents]
    merged: dict[str, Any] = {}
    for value in parent_values:
        parent_text = str(value or "").strip()
        if not parent_text:
            raise ValueError(f"Config extends contains an empty path: {resolved}")
        parent_path = Path(parent_text)
        if not parent_path.is_absolute():
            parent_path = resolved.parent / parent_path
        merged = deep_merge(merged, _load_override(parent_path, (*stack, resolved)))
    return deep_merge(merged, current)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config = _load_mapping(DEFAULT_CONFIG_PATH)
    if path:
        user_config = _load_override(Path(path))
        config = deep_merge(config, user_config)
        validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
        if bool(validation.get("apply_active_execution", False)):
            config = apply_active_execution(config)
    else:
        config = apply_active_execution(config)
    _hydrate_secure_runtime(config)
    return config


def _hydrate_secure_runtime(config: dict[str, Any]) -> None:
    security = config.get("security", {}) if isinstance(config.get("security"), dict) else {}
    value = str(security.get("live_secret_file", "") or "").strip()
    if not value:
        return
    target = Path(value)
    if not target.is_absolute():
        target = Path.cwd() / target
    required = bool(security.get("required", False))
    try:
        from .live_secrets import read_live_secrets
        payload = read_live_secrets(target)
        integrity = payload.get("integrity", {}) if isinstance(payload.get("integrity"), dict) else {}
        key = str(integrity.get("hmac_key", "") or "")
        key_env = str(security.get("hmac_key_env", "ASHARE_QUANT_ADAPTER_HMAC_KEY") or "ASHARE_QUANT_ADAPTER_HMAC_KEY")
        if len(key.encode("utf-8")) < 32:
            raise RuntimeError("encrypted HMAC key is missing or shorter than 32 bytes")
        os.environ.setdefault(key_env, key)
    except Exception:
        if required:
            raise


def project_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def apply_active_execution(config: dict[str, Any]) -> dict[str, Any]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured_path = str(
        validation.get(
            "active_execution_path",
            "data/active_execution_strategy.json",
        )
        or ""
    ).strip()
    if not configured_path:
        return config
    target = project_path(configured_path)
    if not target.exists():
        return config
    try:
        payload = _load_mapping(target)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Active execution strategy record is unreadable: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Active execution strategy record must be a mapping.")
    schema_version = int(payload.get("schema_version", 0) or 0)
    if schema_version != ACTIVE_EXECUTION_SCHEMA_VERSION:
        raise RuntimeError("Active execution strategy schema version is unsupported.")
    override = payload.get("execution_override")
    if not isinstance(override, dict) or not override:
        raise RuntimeError("Active execution strategy override is missing.")
    unexpected = sorted(set(override) - ACTIVE_EXECUTION_KEYS)
    if unexpected:
        raise RuntimeError(
            "Active execution strategy contains unsupported sections: " + ",".join(unexpected)
        )
    expected_hash = str(payload.get("strategy_hash", "") or "").strip()
    actual_hash = hashlib.sha256(
        json.dumps(override, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        raise RuntimeError("Active execution strategy hash does not match its immutable override.")
    if payload.get("broker_submit_enabled") is not False:
        raise RuntimeError("Active execution strategy record must not authorize broker submission.")
    merged = deepcopy(config)
    for key, value in override.items():
        if key == "implementation":
            continue
        merged[key] = deepcopy(value)
    merged["_active_execution"] = {
        "schema_version": schema_version,
        "activation_id": str(payload.get("activation_id", "") or ""),
        "candidate_id": str(payload.get("candidate_id", "") or ""),
        "candidate_config": str(payload.get("candidate_config", "") or ""),
        "strategy_hash": expected_hash,
        "activated_at": str(payload.get("activated_at", "") or ""),
        "path": str(target.resolve()),
    }
    return merged
