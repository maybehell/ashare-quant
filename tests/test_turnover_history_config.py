from pathlib import Path

from ashare_quant.config import load_config, project_path
from ashare_quant.turnover_history import require_turnover_backfill_config
from ashare_quant.validation import validate_config


CONFIG_PATH = Path("configs/turnover_research_data_v1.yaml")


def test_turnover_history_config_is_isolated_and_research_only() -> None:
    config = load_config(CONFIG_PATH)
    settings = require_turnover_backfill_config(config)

    assert validate_config(config) == []
    assert config["operations"]["config_role"] == "research_turnover_backfill"
    assert settings["source_cache_dir"] == project_path("data/cache")
    assert settings["target_cache_dir"] == project_path(
        "data/cache_turnover_research_v1"
    )
    assert settings["source_cache_dir"] != settings["target_cache_dir"]
    assert config["validation"]["research_only"] is True
    assert config["validation"]["apply_active_execution"] is False
    assert config["auto_live"]["enabled"] is False
    assert config["auto_live"]["allow_broker_submit"] is False
    assert config["auto_live"]["emergency_stop"] is True


def test_turnover_history_config_preserves_small_capital_ceiling() -> None:
    config = load_config(CONFIG_PATH)

    assert config["backtest"]["initial_cash"] == 100000
    assert config["capital_budget"]["max_total_capital"] == 200000
    assert config["observation_task"]["required"] is False
    assert config["observation_task"]["startup_required"] is False
