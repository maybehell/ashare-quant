from pathlib import Path

from ashare_quant.config import load_config
from ashare_quant.validation import validate_config


CANDIDATE_CONFIG = Path("configs/hs300_point_in_time_low_risk_quarterly_v1_forward.yaml")


def test_rejected_low_risk_candidate_remains_isolated_and_fail_closed():
    config = load_config(CANDIDATE_CONFIG)

    assert validate_config(config) == []
    assert config["validation"]["research_only"] is True
    assert config["validation"]["experiment_status"] == "rejected"
    assert config["validation"]["optimized_on_current_cache"] is True
    assert config["backtest"]["rebalance"] == "quarterly"
    assert config["risk_overlay"]["enabled"] is False
    assert config["paths"]["cache_dir"].endswith("low_risk_quarterly_v1_forward")
    assert config["paths"]["output_dir"].endswith("low_risk_quarterly_v1_forward")
    assert config["paths"]["state_db"].endswith("low_risk_quarterly_v1_forward.sqlite3")
    assert config["auto_live"]["enabled"] is False
    assert config["auto_live"]["allow_broker_submit"] is False
    assert config["auto_live"]["emergency_stop"] is True


def test_rejected_low_risk_candidate_is_not_observed_by_default():
    default = load_config(Path("configs/default.yaml"))

    assert CANDIDATE_CONFIG.as_posix() not in default["validation"]["forward_only_candidate_configs"]
