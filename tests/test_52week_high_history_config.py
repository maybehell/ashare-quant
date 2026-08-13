import json
from pathlib import Path

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.config import load_config
from ashare_quant.experiment_registry import require_history_experiment_registration
from ashare_quant.validation import validate_config


CONFIG_PATH = Path("configs/hs300_small_capital_52week_high_v1_history.yaml")
REGISTRY_PATH = Path("configs/research_experiment_registry_52week_high_v1.json")
STRATEGY_HASH = "2f8b788f99df6a7e2829887f0b25602939f002af54b3df6183ba12783eaf9d72"
REGISTRATION_HASH = "6b0b0dc33a71492a345c74a4fc9c6ee9186f3b9c39c4c8507433030d4f295099"


def test_52week_high_history_experiment_identity_is_frozen() -> None:
    config = load_config(CONFIG_PATH)
    registration = require_history_experiment_registration(config)

    assert validate_config(config) == []
    assert live_strategy_hash(config) == STRATEGY_HASH
    assert config["validation"]["fixed_strategy_sha256"] == STRATEGY_HASH
    assert registration["experiment_preregistration_sha256"] == REGISTRATION_HASH
    assert registration["experiment_nominal_alpha"] == 0.025


def test_52week_high_experiment_is_research_only_and_small_capital() -> None:
    config = load_config(CONFIG_PATH)

    assert config["operations"]["config_role"] == "research_history_validation"
    assert config["validation"]["research_only"] is True
    assert config["validation"]["apply_active_execution"] is False
    assert config["validation"]["optimized_on_current_cache"] is False
    assert config["validation"]["experiment_status"] == "preregistered_history_validation"
    assert config["backtest"]["initial_cash"] == 100000
    assert config["capital_budget"]["intended_account_capital"] == 100000
    assert config["capital_budget"]["max_total_capital"] == 200000
    assert config["auto_live"]["enabled"] is False
    assert config["auto_live"]["allow_broker_submit"] is False
    assert config["auto_live"]["emergency_stop"] is True


def test_52week_high_family_reserves_only_two_experiments() -> None:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    assert registry["family_id"] == "small_capital_52week_high_v1"
    assert registry["max_experiments"] == 2
    assert registry["multiple_testing_method"] == "bonferroni_fixed_family"
    assert len(registry["experiments"]) == 1
    assert registry["experiments"][0]["status"] == "preregistered"


def test_default_daily_observation_does_not_include_unevaluated_experiment() -> None:
    default = load_config("configs/default.yaml")

    assert CONFIG_PATH.as_posix() not in default["validation"]["forward_only_candidate_configs"]
