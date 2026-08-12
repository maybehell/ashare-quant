from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_config, project_path


OBSERVATION_ORCHESTRATOR_ROLE = "observation_orchestrator"
RESEARCH_CANDIDATE_ROLE = "research_candidate"


def observation_config_issues(
    config: dict[str, Any],
    *,
    output_dir: str | Path,
    allow_output_override: bool = False,
    validate_candidates: bool = True,
) -> list[str]:
    issues: list[str] = []
    operations = (
        config.get("operations", {})
        if isinstance(config.get("operations"), dict)
        else {}
    )
    role = str(operations.get("config_role", "") or "").strip()
    if role != OBSERVATION_ORCHESTRATOR_ROLE:
        issues.append(
            f"config_role={role or 'missing'};expected={OBSERVATION_ORCHESTRATOR_ROLE}"
        )

    configured_output_value = str(
        config.get("paths", {}).get("output_dir", "") or ""
    ).strip()
    if not configured_output_value:
        issues.append("orchestrator_output_dir_missing")
    else:
        configured_output = project_path(configured_output_value).resolve()
        requested_output = project_path(output_dir).resolve()
        if configured_output != requested_output and not allow_output_override:
            issues.append(
                f"output_dir_mismatch:configured={configured_output};requested={requested_output}"
            )

    if not validate_candidates:
        return issues

    base_cache_value = str(config.get("paths", {}).get("cache_dir", "") or "").strip()
    if not base_cache_value:
        issues.append("orchestrator_cache_dir_missing")
        base_cache = None
    else:
        base_cache = project_path(base_cache_value).resolve()

    validation = (
        config.get("validation", {})
        if isinstance(config.get("validation"), dict)
        else {}
    )
    candidate_values = validation.get("forward_only_candidate_configs", [])
    if not isinstance(candidate_values, list) or not candidate_values:
        issues.append("forward_only_candidate_configs_missing")
        return issues

    seen_paths: set[Path] = set()
    seen_caches: set[Path] = set()
    seen_outputs: set[Path] = set()
    for value in candidate_values:
        candidate_text = str(value or "").strip()
        if not candidate_text:
            issues.append("candidate_config_path_empty")
            continue
        candidate_path = project_path(candidate_text).resolve()
        if candidate_path in seen_paths:
            issues.append(f"candidate_config_duplicate:{candidate_path}")
            continue
        seen_paths.add(candidate_path)
        if not candidate_path.is_file():
            issues.append(f"candidate_config_missing:{candidate_path}")
            continue
        try:
            candidate = load_config(candidate_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            issues.append(
                f"candidate_config_unreadable:{candidate_path}:{type(exc).__name__}"
            )
            continue

        candidate_operations = (
            candidate.get("operations", {})
            if isinstance(candidate.get("operations"), dict)
            else {}
        )
        candidate_role = str(
            candidate_operations.get("config_role", "") or ""
        ).strip()
        if candidate_role != RESEARCH_CANDIDATE_ROLE:
            issues.append(
                f"candidate_role_invalid:{candidate_path}:{candidate_role or 'missing'}"
            )
        candidate_validation = (
            candidate.get("validation", {})
            if isinstance(candidate.get("validation"), dict)
            else {}
        )
        if not bool(candidate_validation.get("research_only", False)):
            issues.append(f"candidate_not_research_only:{candidate_path}")
        if (
            str(candidate_validation.get("experiment_status", "") or "").strip()
            != "preregistered_forward_only"
        ):
            issues.append(f"candidate_experiment_status_invalid:{candidate_path}")

        candidate_cache_value = str(
            candidate.get("paths", {}).get("cache_dir", "") or ""
        ).strip()
        candidate_output_value = str(
            candidate.get("paths", {}).get("output_dir", "") or ""
        ).strip()
        if not candidate_cache_value:
            issues.append(f"candidate_cache_dir_missing:{candidate_path}")
        else:
            candidate_cache = project_path(candidate_cache_value).resolve()
            if candidate_cache == base_cache:
                issues.append(f"candidate_cache_shared_with_orchestrator:{candidate_path}")
            if candidate_cache in seen_caches:
                issues.append(f"candidate_cache_duplicate:{candidate_path}")
            seen_caches.add(candidate_cache)
        if not candidate_output_value:
            issues.append(f"candidate_output_dir_missing:{candidate_path}")
        else:
            candidate_output = project_path(candidate_output_value).resolve()
            if configured_output_value and candidate_output == project_path(
                configured_output_value
            ).resolve():
                issues.append(f"candidate_output_shared_with_orchestrator:{candidate_path}")
            if candidate_output in seen_outputs:
                issues.append(f"candidate_output_duplicate:{candidate_path}")
            seen_outputs.add(candidate_output)
    return issues


def require_observation_orchestrator_config(
    config: dict[str, Any],
    *,
    output_dir: str | Path,
    allow_output_override: bool = False,
    validate_candidates: bool = True,
) -> str:
    issues = observation_config_issues(
        config,
        output_dir=output_dir,
        allow_output_override=allow_output_override,
        validate_candidates=validate_candidates,
    )
    if issues:
        raise RuntimeError("Observation config role check failed: " + ";".join(issues))
    candidates = config["validation"]["forward_only_candidate_configs"]
    return f"observation_orchestrator_ready;candidates={len(candidates)}"
