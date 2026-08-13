from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def evaluate_registered_history_experiment(
    output_dir: Path,
    registration: dict[str, Any],
    metrics: dict[str, object],
) -> dict[str, object]:
    thresholds = registration.get("_experiment_thresholds", {})
    if not isinstance(thresholds, dict) or not thresholds:
        return {}

    failures: list[str] = []

    def numeric(value: object, name: str) -> float | None:
        parsed = pd.to_numeric(value, errors="coerce")
        if pd.isna(parsed):
            failures.append(f"missing:{name}")
            return None
        return float(parsed)

    total_excess = numeric(metrics.get("excess_total_return"), "excess_total_return")
    information_ratio = numeric(metrics.get("information_ratio"), "information_ratio")
    max_drawdown = numeric(metrics.get("max_drawdown"), "max_drawdown")
    min_total_excess = float(thresholds["min_total_excess_return"])
    min_information_ratio = float(thresholds["min_information_ratio"])
    max_allowed_drawdown = float(thresholds["max_drawdown"])
    if total_excess is not None and total_excess < min_total_excess:
        failures.append("threshold:total_excess_return")
    if information_ratio is not None and information_ratio < min_information_ratio:
        failures.append("threshold:information_ratio")
    if max_drawdown is not None and max_drawdown < -max_allowed_drawdown:
        failures.append("threshold:max_drawdown")

    robustness = read_csv(output_dir / "robustness_split.csv")
    oos_rows = robustness[
        robustness.get("sample", pd.Series(dtype=str)).astype(str)
        == "out_of_sample_last_30pct"
    ]
    oos_excess = numeric(
        oos_rows.iloc[-1].get("excess_return") if not oos_rows.empty else None,
        "out_of_sample_excess_return",
    )
    min_oos = float(thresholds["min_out_of_sample_excess_return"])
    if oos_excess is not None and oos_excess < min_oos:
        failures.append("threshold:out_of_sample_excess_return")

    walk_forward = read_csv(output_dir / "walk_forward_summary.csv")
    walk_row = walk_forward.iloc[-1] if not walk_forward.empty else pd.Series(dtype=object)
    folds = numeric(walk_row.get("folds"), "walk_forward_folds")
    positive_ratio = numeric(
        walk_row.get("positive_test_ratio"), "walk_forward_positive_ratio"
    )
    min_folds = int(thresholds["min_walk_forward_folds"])
    min_positive_ratio = float(thresholds["min_walk_forward_positive_ratio"])
    if folds is not None and int(folds) < min_folds:
        failures.append("threshold:walk_forward_folds")
    if positive_ratio is not None and positive_ratio < min_positive_ratio:
        failures.append("threshold:walk_forward_positive_ratio")

    bootstrap = read_csv(output_dir / "excess_bootstrap.csv")
    bootstrap_row = bootstrap.iloc[-1] if not bootstrap.empty else pd.Series(dtype=object)
    observed_excess = numeric(
        bootstrap_row.get("observed_excess_return"), "bootstrap_observed_excess"
    )
    p_value = numeric(bootstrap_row.get("two_sided_p_value"), "bootstrap_two_sided_p_value")
    nominal_alpha = float(registration.get("experiment_nominal_alpha", 0) or 0)
    if observed_excess is not None and observed_excess <= 0:
        failures.append("significance:observed_excess_not_positive")
    if p_value is not None and p_value > nominal_alpha:
        failures.append("significance:bonferroni_p_value")

    decision = "passed" if not failures else "rejected"
    return {
        "experiment_decision": decision,
        "experiment_decision_failures": "|".join(failures),
        "experiment_oos_excess_return": "" if oos_excess is None else oos_excess,
        "experiment_walk_forward_folds": "" if folds is None else int(folds),
        "experiment_walk_forward_positive_ratio": (
            "" if positive_ratio is None else positive_ratio
        ),
        "experiment_bootstrap_two_sided_p_value": "" if p_value is None else p_value,
    }


def read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()
