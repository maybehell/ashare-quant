from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from ashare_quant.audit import backtest_config_sha256
from ashare_quant.candidate import preregister_forward_candidate
from ashare_quant.small_capital_readiness import (
    assert_small_capital_preregistration_ready,
    build_small_capital_live_readiness,
)


def config_for(root: Path) -> dict:
    return {
        "paths": {"output_dir": str(root)},
        "capital_budget": {
            "enabled": True,
            "intended_account_capital": 100000,
            "max_total_capital": 200000,
        },
        "backtest": {"initial_cash": 100000},
        "validation": {
            "research_only": True,
            "apply_active_execution": False,
            "optimized_on_current_cache": False,
            "experiment_status": "preregistered_forward_only",
            "candidate_path": str(root / "candidate.json"),
            "capital_sensitivity": {"cash_levels": [100000, 200000]},
        },
        "auto_live": {
            "enabled": False,
            "allow_broker_submit": False,
            "emergency_stop": True,
        },
        "small_capital_live_guard": {
            "enabled": True,
            "required_intended_capital": 100000,
            "max_total_capital": 200000,
            "max_strategy_drawdown": 0.30,
            "min_annual_positive_excess_ratio": 0.75,
            "min_worst_annual_excess_return": -0.05,
            "min_walk_forward_positive_ratio": 0.75,
            "min_worst_walk_forward_excess_return": -0.05,
            "cost_stress_scenario": "high_cost",
            "min_high_cost_excess_return": 0.0,
            "max_cost_total_return_deviation": 0.02,
            "max_final_odd_lot_positions": 0,
            "max_cash_buy_reject_ratio": 0.05,
        },
        "strategy": {"name": "small-test", "top_n": 10},
        "trading": {"rebalance_execution_schema_version": 2, "lot_size": 100, "slippage_bps": 5},
        "universe": {"boards": ["main"]},
        "benchmark": {"symbol": "000300"},
        "risk_overlay": {"enabled": False},
        "rules": {"effective_date": "2026-07-06"},
    }


def write_passing_evidence(config: dict) -> None:
    root = Path(config["paths"]["output_dir"])
    root.mkdir(parents=True, exist_ok=True)
    config_hash = backtest_config_sha256(config)
    hashes = {
        "stocks_sha256": "stocks",
        "prices_sha256": "prices",
        "fundamentals_sha256": "fundamentals",
        "benchmark_sha256": "benchmark",
    }
    pd.DataFrame(
        [{"item": "backtest_config_sha256", "value": config_hash}]
        + [{"item": key, "value": value} for key, value in hashes.items()]
    ).to_csv(root / "run_manifest.csv", index=False)
    pd.DataFrame([
        {"metric": "max_drawdown", "value": -0.20},
        {"metric": "total_return", "value": 0.20},
    ]).to_csv(root / "performance_metrics.csv", index=False)
    pd.DataFrame([
        {"initial_cash": 100000, "passed": True},
        {"initial_cash": 200000, "passed": True},
    ]).to_csv(root / "capital_sensitivity.csv", index=False)
    pd.DataFrame([
        {"year": 2023, "excess_return": 0.01},
        {"year": 2024, "excess_return": 0.02},
        {"year": 2025, "excess_return": 0.03},
        {"year": 2026, "excess_return": -0.01},
    ]).to_csv(root / "annual_performance.csv", index=False)
    pd.DataFrame([{
        "positive_test_ratio": 0.80,
        "worst_test_excess_return": -0.02,
    }]).to_csv(root / "walk_forward_summary.csv", index=False)
    pd.DataFrame([
        {"symbol": "600000", "side": "sell", "quantity": 100},
        {"symbol": "000001", "side": "buy", "quantity": 100},
    ]).to_csv(root / "backtest_orders.csv", index=False)
    pd.DataFrame(columns=["symbol", "side", "quantity", "reason"]).to_csv(
        root / "rejected_orders.csv", index=False
    )
    pd.DataFrame([{"symbol": "000001", "quantity": 100}]).to_csv(
        root / "positions.csv", index=False
    )
    pd.DataFrame([
        {
            "scenario": "base",
            "total_return": 0.20,
            "excess_total_return": 0.10,
            "config_sha256": config_hash,
            **hashes,
        },
        {
            "scenario": "high_cost",
            "total_return": 0.19,
            "excess_total_return": 0.09,
            "config_sha256": config_hash,
            **hashes,
        },
    ]).to_csv(root / "cost_stress.csv", index=False)


def test_readiness_passes_only_when_all_evidence_is_current_and_stable(tmp_path):
    config = config_for(tmp_path)
    write_passing_evidence(config)

    with patch(
        "ashare_quant.small_capital_readiness.capital_sensitivity_needs_refresh",
        return_value=False,
    ):
        audit = build_small_capital_live_readiness(config).set_index("check")
        assert_small_capital_preregistration_ready(config)

    assert bool(audit.loc["forward_preregistration_ready", "passed"])


def test_cost_improvement_still_fails_when_integer_lot_path_changes_too_much(tmp_path):
    config = config_for(tmp_path)
    write_passing_evidence(config)
    stress_path = tmp_path / "cost_stress.csv"
    stress = pd.read_csv(stress_path)
    stress.loc[stress["scenario"] == "high_cost", "total_return"] = 0.24
    stress.to_csv(stress_path, index=False)

    with patch(
        "ashare_quant.small_capital_readiness.capital_sensitivity_needs_refresh",
        return_value=False,
    ):
        audit = build_small_capital_live_readiness(config).set_index("check")
        with pytest.raises(RuntimeError, match="cost_path_stability"):
            assert_small_capital_preregistration_ready(config)

    assert not bool(audit.loc["cost_path_stability", "passed"])
    assert not bool(audit.loc["forward_preregistration_ready", "passed"])


def test_candidate_preregistration_fails_before_market_store_when_guard_is_blocked(tmp_path):
    config = config_for(tmp_path)

    class UnexpectedStore:
        def load_market_data(self):
            raise AssertionError("market store must not be read before the admission gate")

    with pytest.raises(RuntimeError, match="Small-capital forward preregistration gate failed"):
        preregister_forward_candidate(config, UnexpectedStore(), force=True)


def test_current_cache_optimization_blocks_preregistration(tmp_path):
    config = config_for(tmp_path)
    write_passing_evidence(config)
    config["validation"]["optimized_on_current_cache"] = True

    with patch(
        "ashare_quant.small_capital_readiness.capital_sensitivity_needs_refresh",
        return_value=False,
    ):
        audit = build_small_capital_live_readiness(config).set_index("check")
        with pytest.raises(RuntimeError, match="research_cache_optimization_absent"):
            assert_small_capital_preregistration_ready(config)

    assert not bool(audit.loc["research_cache_optimization_absent", "passed"])


def test_missing_execution_artifacts_fail_closed(tmp_path):
    config = config_for(tmp_path)
    write_passing_evidence(config)
    (tmp_path / "backtest_orders.csv").unlink()

    with patch(
        "ashare_quant.small_capital_readiness.capital_sensitivity_needs_refresh",
        return_value=False,
    ):
        audit = build_small_capital_live_readiness(config).set_index("check")

    assert not bool(audit.loc["execution_fidelity_evidence_present", "passed"])
    assert not bool(audit.loc["forward_preregistration_ready", "passed"])


def test_odd_lot_residue_blocks_preregistration(tmp_path):
    config = config_for(tmp_path)
    write_passing_evidence(config)
    pd.DataFrame([{"symbol": "000001", "quantity": 1}]).to_csv(
        tmp_path / "positions.csv", index=False
    )

    with patch(
        "ashare_quant.small_capital_readiness.capital_sensitivity_needs_refresh",
        return_value=False,
    ):
        audit = build_small_capital_live_readiness(config).set_index("check")

    assert not bool(audit.loc["final_odd_lot_positions", "passed"])


def test_excessive_cash_rejects_block_preregistration(tmp_path):
    config = config_for(tmp_path)
    write_passing_evidence(config)
    pd.DataFrame([
        {"symbol": f"{index:06d}", "side": "buy", "quantity": 100}
        for index in range(10)
    ]).to_csv(tmp_path / "backtest_orders.csv", index=False)
    pd.DataFrame([{
        "symbol": "000001",
        "side": "buy",
        "quantity": 100,
        "reason": "insufficient_cash_position_or_quantity",
    }]).to_csv(tmp_path / "rejected_orders.csv", index=False)

    with patch(
        "ashare_quant.small_capital_readiness.capital_sensitivity_needs_refresh",
        return_value=False,
    ):
        audit = build_small_capital_live_readiness(config).set_index("check")

    assert audit.loc["cash_constrained_buy_reject_ratio", "value"] == "0.100000 (1/10)"
    assert not bool(audit.loc["cash_constrained_buy_reject_ratio", "passed"])
