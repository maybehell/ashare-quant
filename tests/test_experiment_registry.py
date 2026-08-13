import json
from pathlib import Path

import pytest

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.experiment_registry import require_history_experiment_registration


def registered_config(tmp_path: Path) -> dict:
    config = {
        "benchmark": {"symbol": "000300"},
        "risk_overlay": {"enabled": False},
        "rules": {},
        "strategy": {"name": "fixed", "top_n": 20},
        "trading": {"lot_size": 100},
        "universe": {"mode": "curated"},
        "validation": {"experiment_registration": {
            "required": True,
            "registry_path": str(tmp_path / "registry.json"),
            "experiment_id": "exp_1",
            "config_path": "configs/fixed.yaml",
        }},
    }
    entry = {
        "sequence": 1, "experiment_id": "exp_1",
        "config_path": "configs/fixed.yaml",
        "strategy_sha256": live_strategy_hash(config),
        "hypothesis": "fixed before evaluation",
        "registered_at": "2026-08-13T15:00:00+08:00",
        "nominal_alpha": 0.0125, "status": "preregistered",
        "thresholds": {
            "min_total_excess_return": 0.02,
            "min_information_ratio": 0.30,
            "max_drawdown": 0.25,
            "min_out_of_sample_excess_return": 0.02,
            "min_walk_forward_folds": 3,
            "min_walk_forward_positive_ratio": 0.60,
        },
    }
    registry = {
        "schema_version": 1, "family_id": "family",
        "family_wise_alpha": 0.05,
        "multiple_testing_method": "bonferroni_fixed_family",
        "max_experiments": 4, "experiments": [entry],
    }
    (tmp_path / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    return config


def test_accepts_exact_preregistered_experiment(tmp_path: Path) -> None:
    evidence = require_history_experiment_registration(registered_config(tmp_path))
    assert evidence["experiment_id"] == "exp_1"
    assert evidence["experiment_sequence"] == 1
    assert evidence["experiment_nominal_alpha"] == 0.0125
    assert len(evidence["experiment_preregistration_sha256"]) == 64


@pytest.mark.parametrize(("field", "value", "message"), [
    ("strategy_sha256", "0" * 64, "strategy_sha256"),
    ("nominal_alpha", 0.05, "Bonferroni"),
    ("config_path", "configs/other.yaml", "config_path"),
])
def test_rejects_registration_identity_drift(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    config = registered_config(tmp_path)
    path = tmp_path / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    registry["experiments"][0][field] = value
    path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        require_history_experiment_registration(config)


def test_rejects_missing_decision_threshold(tmp_path: Path) -> None:
    config = registered_config(tmp_path)
    path = tmp_path / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    del registry["experiments"][0]["thresholds"]["min_information_ratio"]
    path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="thresholds are incomplete"):
        require_history_experiment_registration(config)


def test_legacy_history_config_without_required_registration_is_compatible() -> None:
    assert require_history_experiment_registration({"validation": {}}) == {}
