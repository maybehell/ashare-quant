from copy import deepcopy

from ashare_quant.config import load_config
from ashare_quant.history_config import history_validation_config_issues


def test_extended_history_config_owns_its_isolated_research_output() -> None:
    config = load_config("configs/extended_history_validation.yaml")

    assert history_validation_config_issues(
        config, output_dir="outputs_extended_history_validation"
    ) == []


def test_default_and_broker_configs_cannot_run_history_daemon() -> None:
    default = load_config("configs/default.yaml")
    broker = load_config("configs/huatai_desktop_canary.yaml")

    assert any(
        issue.startswith("config_role=observation_orchestrator")
        for issue in history_validation_config_issues(default, output_dir="outputs")
    )
    assert any(
        issue.startswith("config_role=broker_live")
        for issue in history_validation_config_issues(broker, output_dir="outputs_live")
    )


def test_history_config_rejects_output_redirection_and_live_capability() -> None:
    config = load_config("configs/extended_history_validation.yaml")
    unsafe = deepcopy(config)
    unsafe["auto_live"]["allow_broker_submit"] = True

    issues = history_validation_config_issues(unsafe, output_dir="outputs")

    assert any(issue.startswith("output_dir_mismatch:") for issue in issues)
    assert "auto_live.allow_broker_submit=true" in issues
    assert not any(
        issue.startswith("output_dir_mismatch:")
        for issue in history_validation_config_issues(
            config, output_dir="test_outputs/history", allow_output_override=True
        )
    )
