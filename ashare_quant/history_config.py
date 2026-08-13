from __future__ import annotations

from pathlib import Path
from typing import Any

from .candidate import live_strategy_hash
from .config import project_path


RESEARCH_HISTORY_ROLE = "research_history_validation"


def history_validation_config_issues(
    config: dict[str, Any],
    *,
    output_dir: str | Path,
    allow_output_override: bool = False,
) -> list[str]:
    issues: list[str] = []
    operations = config.get("operations", {})
    role = str(
        operations.get("config_role", "")
        if isinstance(operations, dict)
        else ""
    ).strip()
    if role != RESEARCH_HISTORY_ROLE:
        issues.append(
            f"config_role={role or 'missing'};expected={RESEARCH_HISTORY_ROLE}"
        )

    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    configured_output = str(paths.get("output_dir", "") or "").strip()
    if not configured_output:
        issues.append("history_output_dir_missing")
    elif (
        project_path(configured_output).resolve() != project_path(output_dir).resolve()
        and not allow_output_override
    ):
        issues.append(
            "output_dir_mismatch:"
            f"configured={project_path(configured_output).resolve()};"
            f"requested={project_path(output_dir).resolve()}"
        )

    validation = (
        config.get("validation", {})
        if isinstance(config.get("validation"), dict)
        else {}
    )
    auto_live = (
        config.get("auto_live", {})
        if isinstance(config.get("auto_live"), dict)
        else {}
    )
    if not bool(validation.get("research_only", False)):
        issues.append("validation.research_only=false")
    if bool(validation.get("apply_active_execution", False)):
        issues.append("validation.apply_active_execution=true")
    if bool(auto_live.get("allow_broker_submit", False)):
        issues.append("auto_live.allow_broker_submit=true")
    if not bool(auto_live.get("emergency_stop", True)):
        issues.append("auto_live.emergency_stop=false")
    fixed_hash = str(
        validation.get("fixed_strategy_sha256", "") or ""
    ).strip().lower()
    if not fixed_hash:
        issues.append("validation.fixed_strategy_sha256=missing")
    elif fixed_hash != live_strategy_hash(config):
        issues.append("validation.fixed_strategy_sha256=mismatch")
    return issues


def require_history_validation_config(
    config: dict[str, Any],
    *,
    output_dir: str | Path,
    allow_output_override: bool = False,
) -> str:
    issues = history_validation_config_issues(
        config,
        output_dir=output_dir,
        allow_output_override=allow_output_override,
    )
    if issues:
        raise RuntimeError(
            "History validation config check failed: " + ";".join(issues)
        )
    return "research_history_validation_ready"
