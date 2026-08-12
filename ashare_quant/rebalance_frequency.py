from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import backtest_config_sha256, backtest_relevant_config, mapping_sha256
from .capital_sensitivity import capital_variant_config
from .config import load_config, project_path
from .io_utils import atomic_to_csv


SUMMARY_COLUMNS = [
    "frequency", "status", "research_only", "initial_cash", "total_return",
    "excess_total_return", "out_of_sample_excess_return", "max_drawdown",
    "annualized_turnover", "information_ratio", "sharpe", "trade_count",
    "rebalance_count", "bootstrap_probability_excess_positive",
    "bootstrap_lower_95_excess_return", "config_match", "output_dir",
]
AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "message"]
DATA_HASH_ITEMS = ("stocks_sha256", "prices_sha256", "fundamentals_sha256", "benchmark_sha256")


def write_rebalance_frequency_research(config: dict[str, Any]) -> dict[str, Path]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    settings = validation.get("rebalance_frequency_research", {})
    if not isinstance(settings, dict):
        settings = {}
    baseline_config_path = str(settings.get("baseline_config", "configs/micro_capital_feasibility.yaml"))
    candidate_config_path = str(settings.get("candidate_config", "configs/quarterly_low_turnover_research.yaml"))
    initial_cash = float(settings.get("initial_cash", 200000))

    baseline_config = load_config(project_path(baseline_config_path))
    baseline_cash = float(baseline_config.get("backtest", {}).get("initial_cash", 0))
    if abs(baseline_cash - initial_cash) <= 1e-6:
        baseline_output = project_path(baseline_config["paths"]["output_dir"])
    else:
        baseline_root = project_path(baseline_config["paths"]["output_dir"]) / "capital_sensitivity_runs"
        baseline_config = capital_variant_config(baseline_config, initial_cash, baseline_root)
        baseline_output = project_path(baseline_config["paths"]["output_dir"])
    candidate_config = load_config(project_path(candidate_config_path))
    if abs(float(candidate_config.get("backtest", {}).get("initial_cash", 0)) - initial_cash) > 1e-6:
        raise ValueError("Quarterly research initial_cash must match the baseline research capital.")
    candidate_output = project_path(candidate_config["paths"]["output_dir"])

    experiments = [
        ("monthly", baseline_config, baseline_output),
        ("quarterly", candidate_config, candidate_output),
    ]
    rows = [experiment_row(frequency, cfg, output) for frequency, cfg, output in experiments]
    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    indexed = summary.set_index("frequency")
    monthly = indexed.loc["monthly"]
    quarterly = indexed.loc["quarterly"]

    baseline_manifest = manifest_values(experiments[0][2] / "run_manifest.csv")
    candidate_manifest = manifest_values(experiments[1][2] / "run_manifest.csv")
    same_data = all(
        baseline_manifest.get(item, "")
        and baseline_manifest.get(item) == candidate_manifest.get(item)
        for item in DATA_HASH_ITEMS
    )
    only_frequency_differs = comparable_config_sha256(baseline_config) == comparable_config_sha256(candidate_config)
    candidate_safe = (
        bool(candidate_config.get("validation", {}).get("research_only", False))
        and not bool(candidate_config.get("auto_live", {}).get("allow_broker_submit", False))
        and bool(candidate_config.get("auto_live", {}).get("emergency_stop", True))
    )
    turnover_lower = float(quarterly["annualized_turnover"]) < float(monthly["annualized_turnover"])
    historical_return_higher = float(quarterly["total_return"]) > float(monthly["total_return"])
    oos_improved = float(quarterly["out_of_sample_excess_return"]) > float(monthly["out_of_sample_excess_return"])
    historical_excess_positive = float(quarterly["excess_total_return"]) > 0.0
    oos_excess_positive = float(quarterly["out_of_sample_excess_return"]) > 0.0
    drawdown_delta = float(quarterly["max_drawdown"]) - float(monthly["max_drawdown"])
    structural_ready = bool(summary["config_match"].all()) and same_data and only_frequency_differs and candidate_safe
    status = (
        "historically_promising_oos_improved"
        if structural_ready and turnover_lower and historical_return_higher and oos_improved and historical_excess_positive and oos_excess_positive
        else "historically_promising_oos_unproven"
        if structural_ready and turnover_lower and historical_return_higher and historical_excess_positive
        else "research_rejected"
    )
    summary.loc[summary["frequency"] == "quarterly", "status"] = status

    audit = pd.DataFrame(
        [
            audit_row("experiment_outputs_complete", not summary.isna().any(axis=None), f"rows={len(summary)}", "2 complete experiments", "blocker", "Both lot-aware backtests must be complete."),
            audit_row("experiment_config_identity", bool(summary["config_match"].all()), ";".join(f"{row.frequency}={row.config_match}" for row in summary.itertuples()), "both manifest hashes match expected configs", "blocker", "Stale output cannot be relabeled as a frequency experiment."),
            audit_row("experiment_market_data_identical", same_data, ";".join(DATA_HASH_ITEMS), "all four market-data hashes identical", "blocker", "Monthly and quarterly runs must use identical market evidence."),
            audit_row("only_rebalance_frequency_differs", only_frequency_differs, f"monthly={comparable_config_sha256(baseline_config)[:16]};quarterly={comparable_config_sha256(candidate_config)[:16]}", "normalized backtest identities equal", "blocker", "Strategy, universe, risk and trading assumptions must otherwise be identical."),
            audit_row("quarterly_candidate_research_only", candidate_safe, candidate_safe, "research_only; submit disabled; emergency stop enabled", "blocker", "Frequency research is not an executable candidate."),
            audit_row("quarterly_turnover_lower", turnover_lower, f"monthly={monthly['annualized_turnover']:.6f};quarterly={quarterly['annualized_turnover']:.6f}", "quarterly < monthly", "research", "Lower turnover reduces fee and implementation burden."),
            audit_row("quarterly_historical_return_higher", historical_return_higher, f"delta={quarterly['total_return'] - monthly['total_return']:.6f}", "> 0", "research", "This is same-sample historical evidence, not a promotion result."),
            audit_row("quarterly_oos_excess_improved", oos_improved, f"monthly={monthly['out_of_sample_excess_return']:.6f};quarterly={quarterly['out_of_sample_excess_return']:.6f}", "quarterly > monthly", "warning", "A failed held-out comparison blocks candidate registration."),
            audit_row("quarterly_total_excess_positive", historical_excess_positive, f"{quarterly['excess_total_return']:.6f}", "> 0", "warning", "Being less poor than the monthly reference is insufficient; total excess return must be positive."),
            audit_row("quarterly_oos_excess_positive", oos_excess_positive, f"{quarterly['out_of_sample_excess_return']:.6f}", "> 0", "warning", "Held-out excess return must be positive before a result can be described as promising."),
            audit_row("quarterly_drawdown_not_materially_worse", drawdown_delta >= -0.01, f"delta={drawdown_delta:.6f}", ">= -0.01", "research", "Quarterly drawdown may not deteriorate by more than one percentage point."),
            audit_row("rebalance_frequency_research_ready", structural_ready, status, "structurally comparable evidence; never promotion-ready", "summary", "A promising historical result still requires preregistered forward evidence."),
        ],
        columns=AUDIT_COLUMNS,
    )
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "rebalance_frequency_research.csv"
    audit_path = output_dir / "rebalance_frequency_research_audit.csv"
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    return {summary_path.name: summary_path, audit_path.name: audit_path}


def experiment_row(frequency: str, config: dict[str, Any], output_dir: Path) -> dict[str, object]:
    metrics = metric_values(output_dir / "performance_metrics.csv")
    manifest = manifest_values(output_dir / "run_manifest.csv")
    robustness = read_csv(output_dir / "robustness_split.csv")
    oos = robustness.loc[robustness.get("sample", pd.Series(dtype=str)).eq("out_of_sample_last_30pct")]
    bootstrap = read_csv(output_dir / "excess_bootstrap.csv")
    bootstrap_row = bootstrap.iloc[0] if not bootstrap.empty else pd.Series(dtype=object)
    return {
        "frequency": frequency,
        "status": "reference" if frequency == "monthly" else "evaluating",
        "research_only": frequency != "monthly",
        "initial_cash": float(config.get("backtest", {}).get("initial_cash", 0)),
        "total_return": metrics.get("total_return"),
        "excess_total_return": metrics.get("excess_total_return"),
        "out_of_sample_excess_return": number(oos.iloc[0].get("excess_return")) if not oos.empty else None,
        "max_drawdown": metrics.get("max_drawdown"),
        "annualized_turnover": metrics.get("annualized_turnover"),
        "information_ratio": metrics.get("information_ratio"),
        "sharpe": metrics.get("sharpe"),
        "trade_count": len(read_csv(output_dir / "trades.csv")),
        "rebalance_count": len(read_csv(output_dir / "risk_exposure.csv")),
        "bootstrap_probability_excess_positive": number(bootstrap_row.get("probability_excess_positive")),
        "bootstrap_lower_95_excess_return": number(bootstrap_row.get("lower_95_excess_return")),
        "config_match": manifest.get("backtest_config_sha256", "") == backtest_config_sha256(config),
        "output_dir": str(output_dir.resolve()),
    }


def comparable_config_sha256(config: dict[str, Any]) -> str:
    relevant = deepcopy(backtest_relevant_config(config))
    relevant.setdefault("backtest", {})["initial_cash"] = float(
        relevant.get("backtest", {}).get("initial_cash", 0)
    )
    relevant.setdefault("backtest", {})["rebalance"] = "__frequency__"
    relevant.setdefault("strategy", {})["name"] = "__frequency_research__"
    return mapping_sha256(relevant)


def metric_values(path: Path) -> dict[str, float]:
    frame = read_csv(path)
    if frame.empty or not {"metric", "value"}.issubset(frame.columns):
        return {}
    return {str(row.metric): number(row.value) for row in frame.itertuples(index=False)}


def manifest_values(path: Path) -> dict[str, str]:
    frame = read_csv(path)
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return {}
    return {str(row.item): str(row.value) for row in frame.itertuples(index=False)}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def number(value: object) -> float | None:
    try:
        parsed = float(value)
        return parsed if pd.notna(parsed) else None
    except (TypeError, ValueError):
        return None


def audit_row(check: str, passed: bool, value: object, threshold: object, severity: str, message: str) -> dict[str, object]:
    return {"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "severity": severity, "message": message}
