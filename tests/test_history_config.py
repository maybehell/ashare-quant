from copy import deepcopy

from ashare_quant.candidate import live_strategy_hash
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


def test_current_candidate_history_config_preserves_identity_and_isolates_state() -> None:
    forward = load_config("configs/hs300_small_capital_defensive_trend_v1_forward.yaml")
    history = load_config("configs/hs300_small_capital_defensive_trend_v1_history.yaml")

    assert live_strategy_hash(history) == live_strategy_hash(forward)
    assert history_validation_config_issues(
        history,
        output_dir="outputs_hs300_small_capital_defensive_trend_v1_history",
    ) == []
    assert history["paths"]["cache_dir"] == "data/cache"
    assert history["paths"]["state_db"] != forward["paths"]["state_db"]
    assert history["paths"]["output_dir"] != forward["paths"]["output_dir"]
    assert history["validation"]["history_input_binding"]["required"] is True


def test_required_history_input_binding_rejects_missing_or_different_cache() -> None:
    config = load_config("configs/hs300_small_capital_defensive_trend_v1_history.yaml")
    missing = deepcopy(config)
    missing["validation"]["history_input_binding"].pop("prices_sha256")
    different = deepcopy(config)
    different["validation"]["history_input_binding"]["cache_dir"] = "data/other"

    assert any(
        issue.startswith("history_input_binding_missing:prices_sha256")
        for issue in history_validation_config_issues(
            missing,
            output_dir="outputs_hs300_small_capital_defensive_trend_v1_history",
        )
    )
    assert "history_input_binding_cache_dir_mismatch" in history_validation_config_issues(
        different,
        output_dir="outputs_hs300_small_capital_defensive_trend_v1_history",
    )
