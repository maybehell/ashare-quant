from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


EXCESS_BOOTSTRAP_COLUMNS = [
    "status",
    "observations",
    "block_days",
    "bootstrap_samples",
    "seed",
    "observed_excess_return",
    "bootstrap_median_excess_return",
    "lower_95_excess_return",
    "upper_95_excess_return",
    "probability_excess_positive",
    "two_sided_p_value",
    "message",
]


def block_bootstrap_excess(comparison: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    validation = config.get("validation", {}) if isinstance(config.get("validation", {}), dict) else {}
    settings = validation.get("excess_bootstrap", {})
    settings = settings if isinstance(settings, dict) else {}
    block_days = max(int(settings.get("block_days", 20)), 1)
    samples = max(int(settings.get("samples", 2000)), 100)
    seed = int(settings.get("seed", 20260712))
    min_observations = max(int(settings.get("min_observations", 252)), 20)
    required = {"strategy_return", "benchmark_return"}
    if comparison.empty or not required.issubset(comparison.columns):
        return _result("missing", 0, block_days, samples, seed, message="Benchmark comparison returns are unavailable.")
    paired = comparison[["strategy_return", "benchmark_return"]].apply(pd.to_numeric, errors="coerce").dropna()
    paired = paired[(paired > -1).all(axis=1)]
    observations = len(paired)
    if observations < min_observations:
        return _result(
            "insufficient_observations",
            observations,
            block_days,
            samples,
            seed,
            message=f"At least {min_observations} paired trading days are required.",
        )
    strategy = paired["strategy_return"].to_numpy(dtype=float)
    benchmark = paired["benchmark_return"].to_numpy(dtype=float)
    observed = _compounded(strategy) - _compounded(benchmark)
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(samples, dtype=float)
    blocks_needed = int(np.ceil(observations / block_days))
    offsets = np.arange(block_days)
    for index in range(samples):
        starts = rng.integers(0, observations, size=blocks_needed)
        selected = ((starts[:, None] + offsets[None, :]) % observations).reshape(-1)[:observations]
        bootstrap[index] = _compounded(strategy[selected]) - _compounded(benchmark[selected])
    lower, median, upper = np.quantile(bootstrap, [0.025, 0.5, 0.975])
    probability_positive = float(np.mean(bootstrap > 0))
    two_sided = min(1.0, 2 * min(probability_positive, 1 - probability_positive))
    return _result(
        "ok",
        observations,
        block_days,
        samples,
        seed,
        observed=observed,
        median=float(median),
        lower=float(lower),
        upper=float(upper),
        probability_positive=probability_positive,
        p_value=float(two_sided),
        message="Circular moving-block bootstrap on paired daily strategy and benchmark returns.",
    )


def _compounded(returns: np.ndarray) -> float:
    return float(np.prod(1.0 + returns) - 1.0)


def _result(
    status: str,
    observations: int,
    block_days: int,
    samples: int,
    seed: int,
    *,
    observed: float | None = None,
    median: float | None = None,
    lower: float | None = None,
    upper: float | None = None,
    probability_positive: float | None = None,
    p_value: float | None = None,
    message: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "status": status,
                "observations": observations,
                "block_days": block_days,
                "bootstrap_samples": samples,
                "seed": seed,
                "observed_excess_return": observed,
                "bootstrap_median_excess_return": median,
                "lower_95_excess_return": lower,
                "upper_95_excess_return": upper,
                "probability_excess_positive": probability_positive,
                "two_sided_p_value": p_value,
                "message": message,
            }
        ],
        columns=EXCESS_BOOTSTRAP_COLUMNS,
    )
