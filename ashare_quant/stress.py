from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .audit import backtest_config_sha256, effective_stocks_for_prices, frame_sha256
from .backtest import run_backtest
from .data import DataStore, scope_market_frames_to_committed_fetch
from .report import read_csv
from .validation import filter_complete_market_data


def run_cost_stress(config: dict[str, Any], scenario_names: Iterable[str] | None = None) -> pd.DataFrame:
    cfg = config.get("stress_test", {})
    if not cfg.get("enabled", False):
        return pd.DataFrame(columns=["scenario", "slippage_bps", "commission_rate", "total_return", "max_drawdown", "excess_total_return", "information_ratio"])
    rows = []
    selected_names = {str(name) for name in scenario_names} if scenario_names is not None else None
    base_output = config["paths"]["output_dir"]
    config_hash = backtest_config_sha256(config)
    data_hashes = stress_data_hashes(config)
    for scenario in cfg.get("cost_scenarios", []):
        scenario_config = deepcopy(config)
        name = str(scenario.get("name", "scenario"))
        if selected_names is not None and name not in selected_names:
            continue
        scenario_config["paths"]["output_dir"] = f"{base_output}_stress_{name}"
        scenario_config.setdefault("stress_test", {})["enabled"] = False
        scenario_config.setdefault("runtime", {})["skip_expensive_diagnostics"] = True
        scenario_config["trading"]["slippage_bps"] = scenario.get("slippage_bps", scenario_config["trading"]["slippage_bps"])
        scenario_config["trading"]["commission_rate"] = scenario.get("commission_rate", scenario_config["trading"]["commission_rate"])
        run_backtest(scenario_config)
        metrics = read_csv(Path(scenario_config["paths"]["output_dir"]) / "performance_metrics.csv")
        values = dict(zip(metrics["metric"], metrics["value"])) if not metrics.empty else {}
        rows.append(
            {
                "scenario": name,
                "slippage_bps": scenario_config["trading"]["slippage_bps"],
                "commission_rate": scenario_config["trading"]["commission_rate"],
                "total_return": values.get("total_return", 0.0),
                "max_drawdown": values.get("max_drawdown", 0.0),
                "excess_total_return": values.get("excess_total_return", 0.0),
                "information_ratio": values.get("information_ratio", 0.0),
                "config_sha256": config_hash,
                **data_hashes,
            }
        )
    return pd.DataFrame(rows)


def stress_data_hashes(config: dict[str, Any]) -> dict[str, str]:
    store = DataStore.from_config(config)
    stocks, prices, fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    stocks, prices, fundamentals, _scope_passed, _scope_value = (
        scope_market_frames_to_committed_fetch(store, stocks, prices, fundamentals)
    )
    start = pd.to_datetime(config["backtest"]["start"]).date()
    end_value = config["backtest"].get("end")
    end = pd.to_datetime(end_value).date() if end_value else max(prices["date"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()
    if not benchmark.empty:
        benchmark = benchmark[(benchmark["date"] >= start) & (benchmark["date"] <= end)].copy()
    prices, benchmark = filter_complete_market_data(prices, benchmark, config)
    return {
        "stocks_sha256": frame_sha256(effective_stocks_for_prices(stocks, prices)) if not stocks.empty else "",
        "prices_sha256": frame_sha256(prices) if not prices.empty else "",
        "fundamentals_sha256": frame_sha256(fundamentals) if not fundamentals.empty else "",
        "benchmark_sha256": frame_sha256(benchmark) if not benchmark.empty else "",
    }
