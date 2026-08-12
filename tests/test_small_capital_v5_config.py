from pathlib import Path

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.config import load_config
from ashare_quant.validation import validate_config


CONFIG_PATH = Path("configs/small_capital_100k_v5_extended_history.yaml")
FROZEN_STRATEGY_HASH = "95310e6619d742d340f03e2ebc3a667e6b4408ca1652d983aaf55bc7f450b406"


def test_rejected_small_capital_v5_keeps_its_predeclared_strategy_identity():
    config = load_config(CONFIG_PATH)

    assert validate_config(config) == []
    assert live_strategy_hash(config) == FROZEN_STRATEGY_HASH
    assert config["validation"]["fixed_strategy_sha256"] == FROZEN_STRATEGY_HASH
    assert config["validation"]["research_only"] is True
    assert config["validation"]["optimized_on_current_cache"] is True
    assert config["validation"]["experiment_status"] == "rejected"


def test_rejected_small_capital_v5_remains_concentrated_capped_and_fail_closed():
    config = load_config(CONFIG_PATH)

    assert config["backtest"]["initial_cash"] == 100000
    assert config["capital_budget"]["intended_account_capital"] == 100000
    assert config["capital_budget"]["max_total_capital"] == 200000
    assert config["strategy"]["top_n"] == 10
    assert config["strategy"]["max_stock_weight"] == 0.10
    assert config["strategy"]["capital_aware"]["max_positions"] == 10
    assert config["strategy"]["capital_aware"]["planning_schema_version"] == 2
    assert config["auto_live"]["enabled"] is False
    assert config["auto_live"]["allow_broker_submit"] is False
    assert config["auto_live"]["emergency_stop"] is True
