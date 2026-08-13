from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import backtest_config_sha256, mapping_sha256
from .candidate import live_strategy_hash, load_candidate_manifest
from .config import load_config, project_path
from .io_utils import atomic_to_csv


CANDIDATE_HISTORY_DECISION_COLUMNS = [
    "candidate_id",
    "strategy_sha256",
    "status",
    "decision",
    "failed_checks",
    "history_config_path",
    "history_status_path",
    "history_status_sha256",
    "formal_prices_sha256",
    "history_input_binding_sha256",
    "total_excess_return",
    "min_total_excess_return",
    "information_ratio",
    "min_information_ratio",
    "max_drawdown",
    "max_strategy_drawdown",
    "out_of_sample_excess_return",
    "min_out_of_sample_excess_return",
    "walk_forward_folds",
    "min_walk_forward_folds",
    "walk_forward_positive_ratio",
    "min_walk_forward_positive_ratio",
    "bootstrap_probability_positive",
    "min_bootstrap_probability_positive",
    "bootstrap_lower_95_excess_return",
    "min_bootstrap_lower_95_excess_return",
    "decision_sha256",
    "message",
]


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except (FileNotFoundError, OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def _last_row(path: Path) -> dict[str, Any]:
    frame = _read_csv(path)
    return {} if frame.empty else frame.iloc[-1].to_dict()


def _thresholds(config: dict[str, Any]) -> dict[str, float]:
    guard = config.get("live_guard", {}) if isinstance(config.get("live_guard"), dict) else {}
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    return {
        "min_total_excess_return": float(guard.get("min_total_excess_return", 0.02)),
        "min_information_ratio": float(guard.get("min_information_ratio", 0.30)),
        "max_strategy_drawdown": float(guard.get("max_strategy_drawdown", 0.25)),
        "min_out_of_sample_excess_return": float(guard.get("min_out_of_sample_excess_return", 0.02)),
        "min_walk_forward_folds": float(validation.get("min_walk_forward_folds", 3)),
        "min_walk_forward_positive_ratio": float(
            guard.get("min_walk_forward_positive_ratio", validation.get("min_rolling_positive_ratio", 0.60))
        ),
        "min_bootstrap_probability_positive": float(guard.get("min_bootstrap_probability_positive", 0.95)),
        "min_bootstrap_lower_95_excess_return": float(guard.get("min_bootstrap_lower_95_excess_return", 0.0)),
    }


def build_candidate_history_decision(config: dict[str, Any]) -> pd.DataFrame:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured = str(validation.get("authoritative_history_validation_config", "") or "").strip()
    manifest = load_candidate_manifest(config) or {}
    current_strategy = live_strategy_hash(config)
    identity_failures: list[str] = []

    if not configured:
        identity_failures.append("history_config_missing")
        history_config: dict[str, Any] = {}
        history_config_path = Path("")
    else:
        history_config_path = project_path(configured)
        try:
            history_config = load_config(str(history_config_path))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            history_config = {}
            identity_failures.append("history_config_unreadable")

    history_output = project_path(history_config.get("paths", {}).get("output_dir", "")) if history_config else Path("")
    status_path = history_output / "history_validation_status.csv" if history_config else Path("")
    status_row = _last_row(status_path) if history_config else {}
    if not status_row:
        identity_failures.append("history_status_missing")

    manifest_strategy = str(manifest.get("strategy_hash", "") or "")
    fixed_strategy = str(history_config.get("validation", {}).get("fixed_strategy_sha256", "") or "") if history_config else ""
    status_strategy = str(status_row.get("strategy_sha256", "") or "")
    if not manifest or not str(manifest.get("candidate_id", "") or ""):
        identity_failures.append("candidate_manifest_missing")
    if not manifest_strategy or manifest_strategy != current_strategy:
        identity_failures.append("candidate_strategy_mismatch")
    if not fixed_strategy or fixed_strategy != current_strategy:
        identity_failures.append("history_fixed_strategy_mismatch")
    if status_strategy != current_strategy:
        identity_failures.append("history_status_strategy_mismatch")
    if history_config and str(status_row.get("backtest_config_sha256", "") or "") != backtest_config_sha256(history_config):
        identity_failures.append("history_backtest_config_mismatch")
    if str(status_row.get("status", "") or "") != "completed":
        identity_failures.append("history_not_completed")

    binding = history_config.get("validation", {}).get("history_input_binding", {}) if history_config else {}
    expected_prices = str(binding.get("prices_sha256", "") or "") if isinstance(binding, dict) else ""
    observed_prices = str(status_row.get("formal_prices_sha256", "") or "")
    binding_sha = str(status_row.get("history_input_binding_sha256", "") or "")
    if not expected_prices or observed_prices != expected_prices:
        identity_failures.append("history_prices_mismatch")
    if not binding_sha:
        identity_failures.append("history_input_binding_missing")

    metric_frame = _read_csv(history_output / "performance_metrics.csv") if history_config else pd.DataFrame()
    split = _read_csv(history_output / "robustness_split.csv") if history_config else pd.DataFrame()
    oos_rows = split[split.get("sample", pd.Series(dtype=str)).astype(str) == "out_of_sample_last_30pct"] if not split.empty else pd.DataFrame()
    oos = {} if oos_rows.empty else oos_rows.iloc[-1].to_dict()
    walk = _last_row(history_output / "walk_forward_summary.csv") if history_config else {}
    bootstrap = _last_row(history_output / "excess_bootstrap.csv") if history_config else {}
    thresholds = _thresholds(config)
    values = {
        "total_excess_return": None,
        "information_ratio": None,
        "max_drawdown": None,
        "out_of_sample_excess_return": _number(oos.get("excess_return")),
        "walk_forward_folds": _number(walk.get("folds")),
        "walk_forward_positive_ratio": _number(walk.get("positive_test_ratio")),
        "bootstrap_probability_positive": _number(bootstrap.get("probability_excess_positive")),
        "bootstrap_lower_95_excess_return": _number(bootstrap.get("lower_95_excess_return")),
    }
    if not metric_frame.empty and {"metric", "value"}.issubset(metric_frame.columns):
        metric_map = dict(zip(metric_frame["metric"].astype(str), metric_frame["value"]))
        values["total_excess_return"] = _number(metric_map.get("excess_total_return"))
        values["information_ratio"] = _number(metric_map.get("information_ratio"))
        values["max_drawdown"] = _number(metric_map.get("max_drawdown"))

    performance_checks = {
        "total_excess_return": values["total_excess_return"] is not None and values["total_excess_return"] >= thresholds["min_total_excess_return"],
        "information_ratio": values["information_ratio"] is not None and values["information_ratio"] >= thresholds["min_information_ratio"],
        "max_drawdown": values["max_drawdown"] is not None and values["max_drawdown"] >= -thresholds["max_strategy_drawdown"],
        "out_of_sample_excess_return": values["out_of_sample_excess_return"] is not None and values["out_of_sample_excess_return"] >= thresholds["min_out_of_sample_excess_return"],
        "walk_forward_folds": values["walk_forward_folds"] is not None and values["walk_forward_folds"] >= thresholds["min_walk_forward_folds"],
        "walk_forward_positive_ratio": values["walk_forward_positive_ratio"] is not None and values["walk_forward_positive_ratio"] >= thresholds["min_walk_forward_positive_ratio"],
        "bootstrap_probability_positive": values["bootstrap_probability_positive"] is not None and values["bootstrap_probability_positive"] >= thresholds["min_bootstrap_probability_positive"],
        "bootstrap_lower_95_excess_return": values["bootstrap_lower_95_excess_return"] is not None and values["bootstrap_lower_95_excess_return"] >= thresholds["min_bootstrap_lower_95_excess_return"],
    }
    failed_performance = [name for name, passed in performance_checks.items() if not passed]
    if identity_failures:
        decision = "blocked"
        status = "insufficient_authoritative_evidence"
        failed = identity_failures
        message = "Identity or input evidence is incomplete; no candidate decision is allowed."
    elif failed_performance:
        decision = "rejected"
        status = "authoritative_history_rejected"
        failed = failed_performance
        message = "The exact frozen candidate failed one or more fixed historical gates."
    else:
        decision = "supported"
        status = "authoritative_history_supported"
        failed = []
        message = "The exact frozen candidate passed all configured historical gates; forward evidence is still required."

    status_sha = _file_sha256(status_path) if status_path.is_file() else ""
    identity = {
        "candidate_id": str(manifest.get("candidate_id", "") or ""),
        "strategy_sha256": current_strategy,
        "history_status_sha256": status_sha,
        "formal_prices_sha256": observed_prices,
        "history_input_binding_sha256": binding_sha,
        "thresholds": thresholds,
        "values": values,
        "decision": decision,
        "failed_checks": failed,
    }
    row = {
        **identity,
        **values,
        **thresholds,
        "status": status,
        "failed_checks": "|".join(failed),
        "history_config_path": str(history_config_path.resolve()) if configured else "",
        "history_status_path": str(status_path.resolve()) if history_config else "",
        "decision_sha256": mapping_sha256(identity),
        "message": message,
    }
    return pd.DataFrame([row], columns=CANDIDATE_HISTORY_DECISION_COLUMNS)


def write_candidate_history_decision(config: dict[str, Any]) -> Path:
    output = project_path(config["paths"]["output_dir"]) / "candidate_history_decision.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    return atomic_to_csv(build_candidate_history_decision(config), output, index=False, encoding="utf-8-sig")
