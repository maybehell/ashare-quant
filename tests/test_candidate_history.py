from copy import deepcopy
import json
from pathlib import Path

import pandas as pd

from ashare_quant.audit import backtest_config_sha256
from ashare_quant.candidate import live_strategy_hash
from ashare_quant.candidate_history import build_candidate_history_decision
from ashare_quant.config import load_config


def setup_evidence(tmp_path: Path) -> dict:
    output = tmp_path / "candidate_output"
    history_output = tmp_path / "history_output"
    output.mkdir()
    history_output.mkdir()
    config = load_config()
    config.update({
        "paths": {"output_dir": str(output)},
        "validation": {
            "candidate_path": str(tmp_path / "candidate.json"),
            "authoritative_history_validation_config": str(tmp_path / "history.json"),
            "min_walk_forward_folds": 3,
        },
        "live_guard": {
            "min_total_excess_return": 0.02,
            "min_information_ratio": 0.30,
            "max_strategy_drawdown": 0.25,
            "min_out_of_sample_excess_return": 0.02,
            "min_walk_forward_positive_ratio": 0.60,
            "min_bootstrap_probability_positive": 0.95,
            "min_bootstrap_lower_95_excess_return": 0.0,
        },
        "strategy": {"name": "fixed", "top_n": 20},
        "trading": {"lot_size": 100},
        "universe": {"scope": "curated"},
        "benchmark": {"symbol": "000300"},
        "risk_overlay": {"enabled": False},
        "rules": {},
    })
    strategy_hash = live_strategy_hash(config)
    Path(config["validation"]["candidate_path"]).write_text(
        json.dumps({"candidate_id": "candidate-1", "strategy_hash": strategy_hash}), encoding="utf-8"
    )
    history_config = deepcopy(config)
    history_config.update({
        "paths": {"output_dir": str(history_output)},
        "validation": {
            "fixed_strategy_sha256": strategy_hash,
            "history_input_binding": {"prices_sha256": "a" * 64},
        },
    })
    history_path = Path(config["validation"]["authoritative_history_validation_config"])
    history_path.write_text(json.dumps(history_config), encoding="utf-8")
    loaded_history_config = load_config(str(history_path))
    pd.DataFrame([{
        "status": "completed", "strategy_sha256": strategy_hash,
        "backtest_config_sha256": backtest_config_sha256(loaded_history_config),
        "formal_prices_sha256": "a" * 64, "history_input_binding_sha256": "b" * 64,
    }]).to_csv(history_output / "history_validation_status.csv", index=False)
    pd.DataFrame([
        {"metric": "excess_total_return", "value": 0.04},
        {"metric": "information_ratio", "value": 0.40},
        {"metric": "max_drawdown", "value": -0.15},
    ]).to_csv(history_output / "performance_metrics.csv", index=False)
    pd.DataFrame([{"sample": "out_of_sample_last_30pct", "excess_return": 0.03}]).to_csv(
        history_output / "robustness_split.csv", index=False
    )
    pd.DataFrame([{"folds": 5, "positive_test_ratio": 0.8}]).to_csv(
        history_output / "walk_forward_summary.csv", index=False
    )
    pd.DataFrame([{"probability_excess_positive": 0.98, "lower_95_excess_return": 0.01}]).to_csv(
        history_output / "excess_bootstrap.csv", index=False
    )
    return config


def test_exact_candidate_with_all_gates_passed_is_supported(tmp_path: Path) -> None:
    row = build_candidate_history_decision(setup_evidence(tmp_path)).iloc[0]
    assert row["decision"] == "supported"
    assert row["failed_checks"] == ""
    assert len(row["decision_sha256"]) == 64


def test_exact_candidate_performance_failure_is_rejected(tmp_path: Path) -> None:
    config = setup_evidence(tmp_path)
    history_config = json.loads(Path(config["validation"]["authoritative_history_validation_config"]).read_text())
    history = Path(history_config["paths"]["output_dir"])
    metrics = pd.read_csv(history / "performance_metrics.csv")
    metrics.loc[metrics["metric"] == "excess_total_return", "value"] = -0.08
    metrics.to_csv(history / "performance_metrics.csv", index=False)
    row = build_candidate_history_decision(config).iloc[0]
    assert row["decision"] == "rejected"
    assert "total_excess_return" in row["failed_checks"]


def test_identity_mismatch_blocks_instead_of_rejecting(tmp_path: Path) -> None:
    config = setup_evidence(tmp_path)
    candidate = Path(config["validation"]["candidate_path"])
    payload = json.loads(candidate.read_text())
    payload["strategy_hash"] = "0" * 64
    candidate.write_text(json.dumps(payload), encoding="utf-8")
    row = build_candidate_history_decision(config).iloc[0]
    assert row["decision"] == "blocked"
    assert "candidate_strategy_mismatch" in row["failed_checks"]
