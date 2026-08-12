from pathlib import Path

from ashare_quant.config import load_config
from ashare_quant.observation_config import (
    observation_config_issues,
    require_observation_orchestrator_config,
)


def test_default_config_is_the_observation_orchestrator() -> None:
    config = load_config("configs/default.yaml")

    result = require_observation_orchestrator_config(config, output_dir="outputs")

    assert result == "observation_orchestrator_ready;candidates=1"


def test_broker_live_config_cannot_be_used_as_observation_orchestrator() -> None:
    config = load_config("configs/huatai_desktop_canary.yaml")

    issues = observation_config_issues(config, output_dir="outputs_live")

    assert "config_role=broker_live;expected=observation_orchestrator" in issues
    assert not any(issue.startswith("output_dir_mismatch:") for issue in issues)


def test_dry_run_can_redirect_output_without_changing_config_role() -> None:
    config = load_config("configs/default.yaml")

    issues = observation_config_issues(
        config,
        output_dir="test_outputs/isolated_observation",
        allow_output_override=True,
    )

    assert issues == []


def test_output_directory_must_match_orchestrator_ownership() -> None:
    config = load_config("configs/default.yaml")

    issues = observation_config_issues(config, output_dir="outputs_live")

    assert any(issue.startswith("output_dir_mismatch:") for issue in issues)


def test_candidate_must_be_explicitly_research_only_and_isolated(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        "operations:\n  config_role: broker_live\n"
        "paths:\n  cache_dir: data/cache\n  output_dir: outputs\n"
        "validation:\n  research_only: false\n  experiment_status: invalid\n",
        encoding="utf-8",
    )
    config = load_config("configs/default.yaml")
    config["validation"]["forward_only_candidate_configs"] = [str(candidate)]

    issues = observation_config_issues(config, output_dir="outputs")

    assert any(issue.startswith("candidate_role_invalid:") for issue in issues)
    assert any(issue.startswith("candidate_not_research_only:") for issue in issues)
    assert any(issue.startswith("candidate_experiment_status_invalid:") for issue in issues)
    assert any(issue.startswith("candidate_cache_shared_with_orchestrator:") for issue in issues)
    assert any(issue.startswith("candidate_output_shared_with_orchestrator:") for issue in issues)
