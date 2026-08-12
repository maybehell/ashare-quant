from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import backtest_config_sha256, capital_sensitivity_config_sha256, mapping_sha256
from .backtest import run_backtest
from .capital_budget import capital_budget_errors, capital_budget_limit
from .config import project_path
from .io_utils import atomic_to_csv


CAPITAL_SENSITIVITY_COLUMNS = [
    "initial_cash",
    "total_return",
    "excess_total_return",
    "max_drawdown",
    "annualized_turnover",
    "average_cash_ratio",
    "excess_cash_ratio_vs_reference",
    "fill_count",
    "rejected_order_count",
    "final_position_count",
    "return_drag_vs_reference",
    "passed",
    "run_output_dir",
]
CAPITAL_SENSITIVITY_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]


def run_capital_sensitivity(config: dict[str, Any]) -> dict[str, Path]:
    """Rerun the full lot-aware backtest across isolated capital levels."""
    budget_errors = capital_budget_errors(config)
    if budget_errors:
        raise ValueError("; ".join(budget_errors))
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    run_root = output_dir / "capital_sensitivity_runs"
    reference_data_fingerprint = market_data_fingerprint(
        read_csv(output_dir / "run_manifest.csv")
    )
    settings = config.get("validation", {}).get("capital_sensitivity", {})
    if not isinstance(settings, dict):
        settings = {}
    configured_levels = settings.get("cash_levels", [30000, 50000, 100000, 200000])
    levels = sorted({float(value) for value in configured_levels if number(value) > 0})
    if not levels:
        raise ValueError("validation.capital_sensitivity.cash_levels must contain positive values")

    rows: list[dict[str, object]] = []
    data_fingerprints: list[str] = []
    reused_levels: list[float] = []
    for level in levels:
        variant = capital_variant_config(config, level, run_root)
        paths = reusable_backtest_paths(
            variant,
            expected_market_data_fingerprint=reference_data_fingerprint,
        )
        if paths:
            reused_levels.append(level)
        else:
            paths = run_backtest(variant)
        metrics = read_csv(paths.get("performance_metrics.csv", Path()))
        equity = read_csv(paths.get("equity_curve.csv", Path()))
        trades = read_csv(paths.get("trades.csv", Path()))
        rejects = read_csv(paths.get("rejected_orders.csv", Path()))
        positions = read_csv(paths.get("positions.csv", Path()))
        data_fingerprints.append(market_data_fingerprint(read_csv(paths.get("run_manifest.csv", Path()))))
        values = metric_values(metrics)
        rows.append(
            {
                "initial_cash": level,
                "total_return": number(values.get("total_return")),
                "excess_total_return": number(values.get("excess_total_return")),
                "max_drawdown": number(values.get("max_drawdown")),
                "annualized_turnover": number(values.get("annualized_turnover")),
                "average_cash_ratio": average_cash_ratio(equity),
                "excess_cash_ratio_vs_reference": 0.0,
                "fill_count": len(trades),
                "rejected_order_count": len(rejects),
                "final_position_count": len(positions),
                "return_drag_vs_reference": 0.0,
                "passed": False,
                "run_output_dir": str(project_path(variant["paths"]["output_dir"]).resolve()),
            }
        )

    summary = pd.DataFrame(rows, columns=CAPITAL_SENSITIVITY_COLUMNS)
    reference_idx = summary["initial_cash"].idxmax()
    reference_return = float(summary.loc[reference_idx, "total_return"])
    reference_cash_ratio = float(summary.loc[reference_idx, "average_cash_ratio"])
    summary["return_drag_vs_reference"] = summary["total_return"] - reference_return
    summary["excess_cash_ratio_vs_reference"] = summary["average_cash_ratio"] - reference_cash_ratio
    max_abs_drag = max(float(settings.get("max_abs_return_drag", 0.02)), 0.0)
    max_excess_cash_ratio = min(max(float(settings.get("max_excess_cash_ratio_vs_reference", 0.06)), 0.0), 1.0)
    min_positions = max(int(settings.get("min_final_positions", 20)), 0)
    summary["passed"] = (
        summary["return_drag_vs_reference"].abs().le(max_abs_drag + 1e-12)
        & summary["excess_cash_ratio_vs_reference"].le(max_excess_cash_ratio + 1e-12)
        & summary["final_position_count"].ge(min_positions)
    )
    passing = summary.loc[summary["passed"].astype(bool), "initial_cash"]
    minimum_passing = float(passing.min()) if not passing.empty else None
    strategy_config_hash = capital_sensitivity_config_sha256(config)
    valid_fingerprints = [value for value in data_fingerprints if value]
    data_consistent = len(valid_fingerprints) == len(levels) and len(set(valid_fingerprints)) == 1
    data_current = (
        bool(reference_data_fingerprint)
        and data_consistent
        and valid_fingerprints[0] == reference_data_fingerprint
    )
    audit_rows = [
        audit_row(
            "capital_sensitivity_levels_complete",
            len(summary) == len(levels),
            f"completed={len(summary)};levels={','.join(format_level(value) for value in levels)}",
            f"{len(levels)} isolated backtests",
            "Every capital level uses the same data and strategy with real board-lot execution.",
        ),
        audit_row(
            "capital_sensitivity_resume_evidence",
            True,
            f"reused={len(reused_levels)};levels={','.join(format_level(value) for value in reused_levels)}",
            "reuse only complete runs with matching backtest_config_sha256",
            "Interrupted sensitivity jobs resume without trusting stale or partial outputs.",
        ),        audit_row(
            "capital_sensitivity_strategy_config_sha256",
            True,
            strategy_config_hash,
            "current strategy/execution config and sensitivity criteria excluding initial_cash",
            "Changing strategy, universe, rules, trading assumptions or sensitivity criteria invalidates this evidence.",
        ),
        audit_row(
            "capital_sensitivity_market_data_consistent",
            data_consistent,
            valid_fingerprints[0] if data_consistent else f"valid={len(valid_fingerprints)};unique={len(set(valid_fingerprints))}",
            "one non-empty market-data fingerprint across all levels",
            "Capital levels are comparable only when every run uses identical market data.",
        ),
        audit_row(
            "capital_sensitivity_reference_market_data_sha256",
            data_current,
            valid_fingerprints[0] if data_consistent else "missing",
            reference_data_fingerprint or "current main run_manifest market-data fingerprint",
            "Capital-sensitivity runs must use the exact market-data identity from the current main backtest.",
        ),
        audit_row(
            "capital_sensitivity_reference",
            True,
            f"cash={max(levels):.2f};return={reference_return:.6f};cash_ratio={reference_cash_ratio:.6f}",
            "largest configured capital level",
            "Return drag is measured against the largest configured capital level, not against a fractional model.",
        ),
        audit_row(
            "capital_sensitivity_minimum_passing_capital",
            minimum_passing is not None,
            "missing" if minimum_passing is None else round(minimum_passing, 2),
            f"abs_drag<={max_abs_drag:.2%};excess_cash<={max_excess_cash_ratio:.2%};positions>={min_positions}",
            "This is an execution-fidelity threshold, not a recommendation to deposit funds.",
        ),
    ]
    audit_rows.append(
        audit_row(
            "capital_sensitivity_ready",
            minimum_passing is not None and data_consistent and data_current,
            "blocked"
            if minimum_passing is None or not data_consistent or not data_current
            else f"minimum_passing_capital={minimum_passing:.2f}",
            "at least one tested capital level passes",
            "Live deployment must also pass fresh broker-capital and strategy-health gates.",
        )
    )
    summary_path = output_dir / "capital_sensitivity.csv"
    audit_path = output_dir / "capital_sensitivity_audit.csv"
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(pd.DataFrame(audit_rows, columns=CAPITAL_SENSITIVITY_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
    paths = {"capital_sensitivity.csv": summary_path, "capital_sensitivity_audit.csv": audit_path}
    publish_value = str(settings.get("publish_output_dir", "") or "").strip()
    if publish_value:
        publish_dir = project_path(publish_value)
        publish_dir.mkdir(parents=True, exist_ok=True)
        prefix = str(settings.get("publish_prefix", "research_") or "research_").strip()
        if not prefix or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in prefix):
            raise ValueError("validation.capital_sensitivity.publish_prefix contains unsupported characters")
        published_summary = publish_dir / f"{prefix}capital_sensitivity.csv"
        published_audit = publish_dir / f"{prefix}capital_sensitivity_audit.csv"
        atomic_to_csv(summary, published_summary, index=False, encoding="utf-8-sig")
        atomic_to_csv(pd.DataFrame(audit_rows, columns=CAPITAL_SENSITIVITY_AUDIT_COLUMNS), published_audit, index=False, encoding="utf-8-sig")
        paths[published_summary.name] = published_summary
        paths[published_audit.name] = published_audit
    return paths


def capital_variant_config(config: dict[str, Any], initial_cash: float, run_root: Path) -> dict[str, Any]:
    limit = capital_budget_limit(config)
    if limit is None or initial_cash > limit:
        raise ValueError(
            f"capital sensitivity initial_cash {initial_cash:.2f} exceeds effective maximum "
            f"total capital {limit if limit is not None else 'invalid'}"
        )
    variant = deepcopy(config)
    label = format_level(initial_cash)
    variant.setdefault("backtest", {})["initial_cash"] = float(initial_cash)
    variant.setdefault("paths", {})["output_dir"] = str(run_root / f"cash_{label}")
    variant["paths"]["state_db"] = str(run_root / f"cash_{label}" / "state.sqlite3")
    variant.setdefault("runtime", {})["skip_expensive_diagnostics"] = True
    variant.setdefault("auto_live", {}).update({"enabled": False, "allow_broker_submit": False, "emergency_stop": True})
    variant.setdefault("practical_guard", {})["require_capital_sensitivity"] = False
    return variant


def reusable_backtest_paths(
    config: dict[str, Any],
    expected_market_data_fingerprint: str = "",
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    required = [
        "performance_metrics.csv",
        "equity_curve.csv",
        "trades.csv",
        "positions.csv",
        "run_manifest.csv",
    ]
    paths = {name: output_dir / name for name in [*required, "rejected_orders.csv"]}
    if not all(paths[name].exists() and paths[name].is_file() for name in required):
        return {}
    manifest = read_csv(paths["run_manifest.csv"])
    if manifest.empty or not {"item", "value"}.issubset(manifest.columns):
        return {}
    values = {str(row.item): str(row.value) for row in manifest.itertuples(index=False)}
    if values.get("backtest_config_sha256", "") != backtest_config_sha256(config):
        return {}
    if (
        expected_market_data_fingerprint
        and market_data_fingerprint(manifest) != expected_market_data_fingerprint
    ):
        return {}
    return paths


def capital_sensitivity_needs_refresh(config: dict[str, Any]) -> bool:
    output_dir = project_path(config["paths"]["output_dir"])
    summary = read_csv(output_dir / "capital_sensitivity.csv")
    audit = read_csv(output_dir / "capital_sensitivity_audit.csv")
    manifest = read_csv(output_dir / "run_manifest.csv")
    if summary.empty or audit.empty or manifest.empty:
        return True
    if not {"check", "passed", "value"}.issubset(audit.columns):
        return True

    def audit_value(check: str) -> tuple[bool, str]:
        selected = audit[audit["check"].astype(str) == check]
        if selected.empty:
            return False, ""
        row = selected.iloc[-1]
        return parse_bool(row.get("passed", False)), str(row.get("value", "") or "")

    config_passed, config_hash = audit_value("capital_sensitivity_strategy_config_sha256")
    if not config_passed or config_hash != capital_sensitivity_config_sha256(config):
        return True
    levels_passed, _ = audit_value("capital_sensitivity_levels_complete")
    consistent_passed, _ = audit_value("capital_sensitivity_market_data_consistent")
    reference_passed, reference_hash = audit_value(
        "capital_sensitivity_reference_market_data_sha256"
    )
    expected_reference = market_data_fingerprint(manifest)
    return not (
        levels_passed
        and consistent_passed
        and reference_passed
        and bool(expected_reference)
        and reference_hash == expected_reference
    )


def average_cash_ratio(equity: pd.DataFrame) -> float:
    if equity.empty or not {"cash", "equity", "fills"}.issubset(equity.columns):
        return 1.0
    fills = pd.to_numeric(equity["fills"], errors="coerce").fillna(0.0)
    deployed = equity.loc[fills.cumsum() > 0].copy()
    if deployed.empty:
        return 1.0
    cash = pd.to_numeric(deployed["cash"], errors="coerce")
    total = pd.to_numeric(deployed["equity"], errors="coerce").replace(0, pd.NA)
    ratios = (cash / total).dropna().clip(lower=0.0, upper=1.0)
    return float(ratios.mean()) if not ratios.empty else 1.0


def deployed_cash_ratio(equity: pd.DataFrame) -> float:
    """Backward-compatible alias; the returned value is the average cash ratio."""
    return average_cash_ratio(equity)


def metric_values(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty or not {"metric", "value"}.issubset(frame.columns):
        return {}
    return {str(row.metric): number(row.value) for row in frame.itertuples(index=False)}


def market_data_fingerprint(manifest: pd.DataFrame) -> str:
    if manifest.empty or not {"item", "value"}.issubset(manifest.columns):
        return ""
    values = {str(row.item): str(row.value) for row in manifest.itertuples(index=False)}
    keys = ["stocks_sha256", "prices_sha256", "fundamentals_sha256", "benchmark_sha256"]
    selected = {key: values.get(key, "") for key in keys}
    if any(not value for value in selected.values()):
        return ""
    return mapping_sha256(selected)


def read_csv(path: Path) -> pd.DataFrame:
    if not path or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def audit_row(check: str, passed: bool, value: object, threshold: object, message: str) -> dict[str, object]:
    return {"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message}


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def number(value: object) -> float:
    try:
        result = float(value)
        return result if pd.notna(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def format_level(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", "_")
