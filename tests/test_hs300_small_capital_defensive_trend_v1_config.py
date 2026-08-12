from pathlib import Path

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.config import load_config
from ashare_quant.validation import validate_config


CONFIG_PATH = Path("configs/hs300_small_capital_defensive_trend_v1_forward.yaml")
FROZEN_STRATEGY_HASH = "1af970df4011e6b8bc0bce601c813b42d8c58105ae8ab587a3f1f0a5ffcaadd9"


def test_forward_candidate_keeps_its_predeclared_strategy_identity():
    config = load_config(CONFIG_PATH)

    assert validate_config(config) == []
    assert live_strategy_hash(config) == FROZEN_STRATEGY_HASH
    assert config["validation"]["fixed_strategy_sha256"] == FROZEN_STRATEGY_HASH
    assert config["validation"]["research_only"] is True
    assert config["validation"]["optimized_on_current_cache"] is False
    assert config["validation"]["experiment_status"] == "preregistered_forward_only"


def test_forward_candidate_is_small_capital_implementable_and_fail_closed():
    config = load_config(CONFIG_PATH)

    assert config["backtest"]["initial_cash"] == 100000
    assert config["capital_budget"]["intended_account_capital"] == 100000
    assert config["capital_budget"]["max_total_capital"] == 200000
    assert config["strategy"]["top_n"] == 20
    assert config["strategy"]["max_stock_weight"] == 0.05
    assert config["strategy"]["capital_aware"]["min_positions"] == 15
    assert config["strategy"]["capital_aware"]["max_positions"] == 20
    assert config["strategy"]["capital_aware"]["planning_schema_version"] == 2
    assert config["strategy"]["capital_aware"]["respect_target_weights"] is True
    assert config["auto_live"]["enabled"] is False
    assert config["auto_live"]["allow_broker_submit"] is False
    assert config["auto_live"]["emergency_stop"] is True
