from copy import deepcopy

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.config import load_config
from ashare_quant.experiment_registry import require_history_experiment_registration
from ashare_quant.history_config import history_validation_config_issues
from ashare_quant.validation import validate_config


CONFIG_PATH = "configs/hs300_small_capital_medium_reversal_v1_history.yaml"
STRATEGY_SHA256 = "a7833559e04e85a9931f6e3fb3eee68664ebfcd431e61106583b435e77ed9352"


def test_fourth_experiment_is_frozen_registered_and_isolated() -> None:
    config = load_config(CONFIG_PATH)
    registration = require_history_experiment_registration(config)

    assert validate_config(config) == []
    assert live_strategy_hash(config) == STRATEGY_SHA256
    assert config["validation"]["fixed_strategy_sha256"] == STRATEGY_SHA256
    assert registration["experiment_sequence"] == 4
    assert registration["experiment_nominal_alpha"] == 0.0125
    assert history_validation_config_issues(
        config, output_dir="outputs_hs300_small_capital_medium_reversal_v1_history"
    ) == []
    assert config["paths"]["cache_dir"] == "data/cache"
    assert config["paths"]["state_db"] != "data/huatai_live_state.sqlite3"
    assert config["validation"]["history_input_binding"]["prices_sha256"] == (
        "b1eb8529bcd3b948e705bdac27859bf6a21b6f91921c05329f4479f553b84c5c"
    )
    assert config["backtest"]["initial_cash"] == 100000
    assert config["capital_budget"]["max_total_capital"] == 200000
    assert config["auto_live"]["allow_broker_submit"] is False
    assert config["auto_live"]["emergency_stop"] is True


def test_medium_reversal_parameters_fail_closed() -> None:
    config = load_config(CONFIG_PATH)
    invalid = deepcopy(config)
    invalid["strategy"]["lookbacks"]["medium_reversal"] = 5
    invalid["strategy"]["lookbacks"]["medium_reversal_skip"] = 5
    errors = validate_config(invalid)

    assert "strategy.lookbacks.medium_reversal must be at least 20" in errors
    assert (
        "strategy.lookbacks.medium_reversal_skip must be less than medium_reversal"
        in errors
    )


def test_medium_reversal_weight_requires_both_explicit_lookbacks() -> None:
    config = load_config(CONFIG_PATH)
    config["strategy"]["lookbacks"].pop("medium_reversal_skip")

    assert (
        "strategy medium_reversal factor requires medium_reversal and "
        "medium_reversal_skip lookbacks"
        in validate_config(config)
    )
