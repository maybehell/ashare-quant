from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .audit import backtest_config_sha256
from .capital_budget import capital_budget_errors
from .capital_sensitivity import capital_sensitivity_needs_refresh
from .config import project_path
from .io_utils import atomic_to_csv


READINESS_COLUMNS = ["check", "passed", "value", "threshold", "blocking", "message"]


def build_small_capital_live_readiness(config: dict[str, Any]) -> pd.DataFrame:
    """Build a fail-closed research-to-forward-candidate admission audit."""
    guard = config.get("small_capital_live_guard", {})
    if not isinstance(guard, dict) or not bool(guard.get("enabled", False)):
        return pd.DataFrame(
            [audit_row("guard_enabled", False, "disabled", "enabled=true",
                       "Small-capital candidate admission is disabled.")],
            columns=READINESS_COLUMNS,
        )

    output_dir = project_path(config["paths"]["output_dir"])
    metrics = read_csv(output_dir / "performance_metrics.csv")
    sensitivity = read_csv(output_dir / "capital_sensitivity.csv")
    stress = read_csv(output_dir / "cost_stress.csv")
    annual = read_csv(output_dir / "annual_performance.csv")
    walk_forward = read_csv(output_dir / "walk_forward_summary.csv")
    manifest = manifest_values(read_csv(output_dir / "run_manifest.csv"))

    rows: list[dict[str, object]] = []
    budget_issues = capital_budget_errors(config)
    budget = config.get("capital_budget", {})
    intended = number(budget.get("intended_account_capital"))
    hard_cap = number(budget.get("max_total_capital"))
    required_intended = number(guard.get("required_intended_capital", 100000))
    max_capital = number(guard.get("max_total_capital", 200000))
    rows.append(audit_row(
        "capital_budget_enforced",
        not budget_issues and 0 < intended <= hard_cap <= max_capital and intended == required_intended,
        f"intended={intended:.2f};hard_cap={hard_cap:.2f};issues={'; '.join(budget_issues) or 'none'}",
        f"intended={required_intended:.2f};hard_cap<={max_capital:.2f}",
        "The research family must remain inside its declared account-capital boundary.",
    ))

    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    auto_live = config.get("auto_live", {}) if isinstance(config.get("auto_live"), dict) else {}
    isolated = (
        bool(validation.get("research_only", False))
        and not bool(validation.get("apply_active_execution", False))
        and not bool(auto_live.get("enabled", False))
        and not bool(auto_live.get("allow_broker_submit", False))
        and bool(auto_live.get("emergency_stop", True))
    )
    rows.append(audit_row(
        "research_execution_isolation", isolated,
        f"research_only={bool(validation.get('research_only', False))};"
        f"auto_live={bool(auto_live.get('enabled', False))};"
        f"submit={bool(auto_live.get('allow_broker_submit', False))};"
        f"stop={bool(auto_live.get('emergency_stop', True))}",
        "research_only=true;auto_live=false;submit=false;emergency_stop=true",
        "Admission evidence must not grant execution or broker-submission authority.",
    ))

    current_hash = backtest_config_sha256(config)
    manifest_hash = str(manifest.get("backtest_config_sha256", ""))
    rows.append(audit_row(
        "backtest_evidence_current", bool(manifest_hash) and manifest_hash == current_hash,
        manifest_hash or "missing", current_hash,
        "The main backtest manifest must match current strategy and execution assumptions.",
    ))

    sensitivity_current = not capital_sensitivity_needs_refresh(config)
    intended_row = select_numeric(sensitivity, "initial_cash", intended)
    intended_passed = parse_bool(intended_row.get("passed")) if intended_row is not None else False
    rows.append(audit_row(
        "capital_fidelity_at_intended_cash", sensitivity_current and intended_passed,
        f"current={sensitivity_current};intended_passed={intended_passed}",
        f"current evidence and passing level at {intended:.2f}",
        "Board-lot implementation must remain viable at the intended account size.",
    ))

    max_drawdown = abs(metric(metrics, "max_drawdown"))
    allowed_drawdown = number(guard.get("max_strategy_drawdown", 0.30))
    rows.append(audit_row(
        "maximum_strategy_drawdown", 0 < max_drawdown <= allowed_drawdown,
        max_drawdown, f"<={allowed_drawdown:.4f}",
        "Historical portfolio drawdown must remain within the declared small-account limit.",
    ))

    annual_excess = numeric_column(annual, "excess_return")
    positive_ratio = float((annual_excess > 0).mean()) if not annual_excess.empty else 0.0
    worst_annual = float(annual_excess.min()) if not annual_excess.empty else float("-inf")
    min_annual_ratio = number(guard.get("min_annual_positive_excess_ratio", 0.75))
    min_worst_annual = number(guard.get("min_worst_annual_excess_return", -0.05))
    rows.append(audit_row(
        "annual_excess_consistency", positive_ratio >= min_annual_ratio,
        positive_ratio, f">={min_annual_ratio:.4f}",
        "Positive excess return should not be concentrated in only one recent regime.",
    ))
    rows.append(audit_row(
        "worst_annual_excess_return", worst_annual >= min_worst_annual,
        worst_annual, f">={min_worst_annual:.4f}",
        "A severely negative calendar-year excess return blocks forward preregistration.",
    ))

    wf = walk_forward.iloc[-1] if not walk_forward.empty else pd.Series(dtype=object)
    wf_ratio = number(wf.get("positive_test_ratio"))
    wf_worst = number(wf.get("worst_test_excess_return"), default=float("-inf"))
    min_wf_ratio = number(guard.get("min_walk_forward_positive_ratio", 0.75))
    min_wf_worst = number(guard.get("min_worst_walk_forward_excess_return", -0.05))
    rows.append(audit_row(
        "walk_forward_positive_ratio", wf_ratio >= min_wf_ratio,
        wf_ratio, f">={min_wf_ratio:.4f}",
        "Fixed-parameter out-of-sample folds must win consistently against the benchmark.",
    ))
    rows.append(audit_row(
        "worst_walk_forward_excess_return", wf_worst >= min_wf_worst,
        wf_worst, f">={min_wf_worst:.4f}",
        "The worst fixed-parameter test fold must stay inside the declared loss tolerance.",
    ))

    base = select_text(stress, "scenario", "base")
    high = select_text(stress, "scenario", str(guard.get("cost_stress_scenario", "high_cost")))
    stress_current = stress_rows_current(base, high, current_hash, manifest)
    rows.append(audit_row(
        "cost_stress_evidence_current", stress_current,
        f"base={base is not None};high_cost={high is not None};identity_match={stress_current}",
        "base and high_cost rows match current config and market data",
        "Cost evidence must use the exact same configuration and market files as the main run.",
    ))
    high_excess = number(high.get("excess_total_return"), default=float("-inf")) if high is not None else float("-inf")
    min_high_excess = number(guard.get("min_high_cost_excess_return", 0.0))
    rows.append(audit_row(
        "high_cost_excess_return", stress_current and high_excess >= min_high_excess,
        high_excess, f">={min_high_excess:.4f}",
        "Conservative execution costs must not eliminate benchmark-relative return.",
    ))
    base_return = number(base.get("total_return")) if base is not None else 0.0
    high_return = number(high.get("total_return")) if high is not None else 0.0
    cost_deviation = abs(high_return - base_return) if base is not None and high is not None else float("inf")
    max_cost_deviation = number(guard.get("max_cost_total_return_deviation", 0.02))
    rows.append(audit_row(
        "cost_path_stability", stress_current and cost_deviation <= max_cost_deviation,
        cost_deviation, f"absolute total-return difference<={max_cost_deviation:.4f}",
        "Large improvement or deterioration under higher costs signals unstable integer-lot trade paths.",
    ))

    blocking_passed = all(bool(item["passed"]) for item in rows)
    rows.append(audit_row(
        "forward_preregistration_ready", blocking_passed,
        "eligible" if blocking_passed else "blocked", "all blocking checks pass",
        "This gate permits only forward observation; it never authorizes live trading.",
    ))
    return pd.DataFrame(rows, columns=READINESS_COLUMNS)


def write_small_capital_live_readiness(config: dict[str, Any]) -> Path:
    output = project_path(config["paths"]["output_dir"]) / "small_capital_live_readiness.csv"
    return atomic_to_csv(
        build_small_capital_live_readiness(config), output, index=False, encoding="utf-8-sig"
    )


def assert_small_capital_preregistration_ready(config: dict[str, Any]) -> None:
    guard = config.get("small_capital_live_guard", {})
    if not isinstance(guard, dict) or not bool(guard.get("enabled", False)):
        return
    audit = build_small_capital_live_readiness(config)
    failed = audit[~audit["passed"].map(parse_bool)]
    failed_checks = [
        str(value) for value in failed["check"] if value != "forward_preregistration_ready"
    ]
    if failed_checks:
        raise RuntimeError(
            "Small-capital forward preregistration gate failed: " + ", ".join(failed_checks)
        )


def audit_row(check: str, passed: bool, value: object, threshold: object, message: str) -> dict[str, object]:
    return {"check": check, "passed": bool(passed), "value": value, "threshold": threshold,
            "blocking": True, "message": message}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except (pd.errors.EmptyDataError, UnicodeDecodeError, OSError):
        return pd.DataFrame()


def manifest_values(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return {}
    return dict(zip(frame["item"].astype(str), frame["value"]))


def metric(frame: pd.DataFrame, name: str) -> float:
    if frame.empty or not {"metric", "value"}.issubset(frame.columns):
        return 0.0
    selected = frame[frame["metric"].astype(str) == name]
    return number(selected.iloc[-1]["value"]) if not selected.empty else 0.0


def numeric_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if frame.empty or name not in frame:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").dropna()


def select_numeric(frame: pd.DataFrame, column: str, value: float) -> pd.Series | None:
    if frame.empty or column not in frame:
        return None
    selected = frame[(pd.to_numeric(frame[column], errors="coerce") - value).abs() < 0.01]
    return selected.iloc[-1] if not selected.empty else None


def select_text(frame: pd.DataFrame, column: str, value: str) -> pd.Series | None:
    if frame.empty or column not in frame:
        return None
    selected = frame[frame[column].astype(str) == value]
    return selected.iloc[-1] if not selected.empty else None


def stress_rows_current(
    base: pd.Series | None, high: pd.Series | None, config_hash: str, manifest: dict[str, object]
) -> bool:
    if base is None or high is None:
        return False
    expected = {
        "config_sha256": config_hash,
        "stocks_sha256": str(manifest.get("stocks_sha256", "")),
        "prices_sha256": str(manifest.get("prices_sha256", "")),
        "fundamentals_sha256": str(manifest.get("fundamentals_sha256", "")),
        "benchmark_sha256": str(manifest.get("benchmark_sha256", "")),
    }
    return all(
        expected_value and str(stress_row.get(key, "")) == expected_value
        for key, expected_value in expected.items()
        for stress_row in (base, high)
    )


def number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if pd.notna(result) else default


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}
