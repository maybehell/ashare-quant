from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from ashare_quant.audit import backtest_config_sha256
from ashare_quant.candidate import (
    build_candidate_forward_progress,
    candidate_failure_history_path,
    is_candidate_quarantined,
    live_strategy_hash,
)
from ashare_quant.candidate_history import build_candidate_history_decision
from ashare_quant.candidate_rejection import record_candidate_history_rejection
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


def reject_evidence(config: dict) -> str:
    history_config = json.loads(Path(config["validation"]["authoritative_history_validation_config"]).read_text())
    history = Path(history_config["paths"]["output_dir"])
    metrics = pd.read_csv(history / "performance_metrics.csv")
    metrics.loc[metrics["metric"] == "excess_total_return", "value"] = -0.08
    metrics.to_csv(history / "performance_metrics.csv", index=False)
    return str(build_candidate_history_decision(config).iloc[0]["decision_sha256"])


def test_exact_candidate_with_all_gates_passed_is_supported(tmp_path: Path) -> None:
    row = build_candidate_history_decision(setup_evidence(tmp_path)).iloc[0]
    assert row["decision"] == "supported"
    assert row["failed_checks"] == ""
    assert len(row["decision_sha256"]) == 64


def test_exact_candidate_performance_failure_is_rejected(tmp_path: Path) -> None:
    config = setup_evidence(tmp_path)
    row = build_candidate_history_decision(config).iloc[0]
    assert row["decision"] == "supported"
    decision_sha = reject_evidence(config)
    row = build_candidate_history_decision(config).iloc[0]
    assert row["decision"] == "rejected"
    assert row["decision_sha256"] == decision_sha
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


def test_history_rejection_requires_confirmation_and_exact_hash(tmp_path: Path) -> None:
    config = setup_evidence(tmp_path)
    decision_sha = reject_evidence(config)
    with pytest.raises(RuntimeError, match="explicit --confirm"):
        record_candidate_history_rejection(config, decision_sha256=decision_sha)
    with pytest.raises(RuntimeError, match="does not match current evidence"):
        record_candidate_history_rejection(config, decision_sha256="0" * 64, confirm=True)
    assert not candidate_failure_history_path(config).exists()


def test_supported_history_cannot_be_recorded_as_rejected(tmp_path: Path) -> None:
    config = setup_evidence(tmp_path)
    decision_sha = str(build_candidate_history_decision(config).iloc[0]["decision_sha256"])
    with pytest.raises(RuntimeError, match="current rejected decision"):
        record_candidate_history_rejection(config, decision_sha256=decision_sha, confirm=True)


def test_history_rejection_is_idempotent_and_quarantines_candidate(tmp_path: Path) -> None:
    config = setup_evidence(tmp_path)
    decision_sha = reject_evidence(config)
    candidate_path = Path(config["validation"]["candidate_path"])
    candidate_before = candidate_path.read_bytes()
    path = record_candidate_history_rejection(config, decision_sha256=decision_sha, confirm=True)
    first = path.read_bytes()
    repeated = record_candidate_history_rejection(config, decision_sha256=decision_sha, confirm=True)
    assert repeated == path
    assert path.read_bytes() == first
    assert candidate_path.read_bytes() == candidate_before
    history = pd.read_csv(path, encoding="utf-8-sig")
    assert len(history) == 1
    row = history.iloc[0]
    assert row["status"] == "authoritative_history_rejected"
    assert row["decision_sha256"] == decision_sha
    assert row["evidence_source"] == "candidate_history_decision"
    assert len(row["evidence_sha256"]) == 64
    assert is_candidate_quarantined(config)
    progress = build_candidate_forward_progress(config, pd.DataFrame()).iloc[0]
    assert progress["status"] == "quarantined_history_rejection"


def test_legacy_failure_history_is_upgraded_without_losing_row(tmp_path: Path) -> None:
    config = setup_evidence(tmp_path)
    path = candidate_failure_history_path(config)
    pd.DataFrame([{
        "candidate_id": "legacy", "strategy_hash": "x", "behavior_hash": "y",
        "strategy_name": "legacy", "status": "failed_forward_excess",
        "freeze_price_end": "2026-01-01", "observed_forward_trading_days": 60,
        "forward_excess_return": -0.1, "recorded_at": "2026-01-02T00:00:00+08:00",
    }]).to_csv(path, index=False)
    decision_sha = reject_evidence(config)
    record_candidate_history_rejection(config, decision_sha256=decision_sha, confirm=True)
    history = pd.read_csv(path, encoding="utf-8-sig")
    assert len(history) == 2
    assert history.iloc[0]["candidate_id"] == "legacy"
    assert "decision_sha256" in history.columns
