from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import backtest_config_sha256, mapping_sha256
from .backtest import run_backtest
from .candidate import live_strategy_hash
from .config import project_path
from .data import DataStore, history_staging_frame_name
from .experiment_registry import require_history_experiment_registration
from .experiment_decision import evaluate_registered_history_experiment
from .io_utils import atomic_to_csv
from .report import regenerate_report
from .survivorship import survivorship_evidence_sha256, write_survivorship_bias_audit


STATUS_COLUMNS = [
    "status",
    "generated_at",
    "progress_generated_at",
    "requested_symbols",
    "completed_symbols",
    "requested_start",
    "requested_end",
    "formal_prices_sha256",
    "backtest_config_sha256",
    "strategy_sha256",
    "history_input_binding_sha256",
    "experiment_id",
    "experiment_sequence",
    "experiment_family_id",
    "experiment_nominal_alpha",
    "experiment_preregistration_sha256",
    "experiment_decision",
    "experiment_decision_failures",
    "experiment_oos_excess_return",
    "experiment_walk_forward_folds",
    "experiment_walk_forward_positive_ratio",
    "experiment_bootstrap_two_sided_p_value",
    "survivorship_audit_sha256",
    "effective_trading_days",
    "minimum_trading_days",
    "total_return",
    "benchmark_total_return",
    "excess_total_return",
    "max_drawdown",
    "information_ratio",
    "report_path",
    "message",
]


def run_extended_history_validation(config: dict[str, Any]) -> dict[str, Path]:
    assert_research_only_history_config(config)
    experiment_registration = require_history_experiment_registration(config)
    store = DataStore.from_config(config)
    progress = require_completed_history_progress(store, config)
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "history_validation_status.csv"
    report_path = output_dir / "report.html"
    prices_path = formal_prices_path(store)
    prices_sha256 = file_sha256(prices_path)
    input_binding_sha256 = require_history_input_binding(
        config, store, progress, prices_path, prices_sha256
    )
    config_sha256 = backtest_config_sha256(config)
    strategy_sha256 = live_strategy_hash(config)
    survivorship_paths = write_survivorship_bias_audit(config)
    survivorship_audit = read_optional_csv(survivorship_paths["survivorship_bias_audit.csv"])
    survivorship_snapshots = read_optional_csv(survivorship_paths["survivorship_bias_snapshots.csv"])
    survivorship_sha256 = survivorship_evidence_sha256(
        survivorship_audit,
        survivorship_snapshots,
    )
    minimum_trading_days = max(
        0,
        int(config.get("validation", {}).get("min_backtest_trading_days", 0)),
    )

    if reusable_completed_validation(
        status_path,
        report_path,
        prices_sha256,
        config_sha256,
        strategy_sha256,
        input_binding_sha256,
        survivorship_sha256,
        str(experiment_registration.get("experiment_preregistration_sha256", "")),
        minimum_trading_days,
        str(progress["generated_at"]),
    ):
        return {
            **survivorship_paths,
            "history_validation_status.csv": status_path,
            "report.html": report_path,
        }

    base = {
        **status_base(
            progress,
            prices_sha256,
            config_sha256,
            strategy_sha256,
            input_binding_sha256,
        ),
        **experiment_registration,
        "survivorship_audit_sha256": survivorship_sha256,
        "minimum_trading_days": minimum_trading_days,
    }
    effective_trading_days = 0
    write_status(status_path, {**base, "status": "running", "message": "fixed_strategy_validation_started"})
    try:
        paths = run_backtest(config)
        report_path = regenerate_report(config)
        forbidden = [
            output_dir / "orders.csv",
            output_dir / "rpa_orders.csv",
        ]
        created = [str(path) for path in forbidden if path.exists()]
        if created:
            raise RuntimeError(
                "Research-only history validation created forbidden execution artifacts: "
                + ", ".join(created)
            )
        metrics = read_metrics(output_dir / "performance_metrics.csv")
        experiment_decision = evaluate_registered_history_experiment(
            output_dir,
            experiment_registration,
            metrics,
        )
        equity = read_optional_csv(output_dir / "equity_curve.csv")
        effective_trading_days = len(equity)
        if effective_trading_days < minimum_trading_days:
            raise RuntimeError(
                "Fixed strategy validation has "
                f"{effective_trading_days} effective trading days; "
                f"minimum is {minimum_trading_days}."
            )
        completed = {
            **base,
            **experiment_decision,
            "status": "completed",
            "effective_trading_days": effective_trading_days,
            "total_return": metrics.get("total_return", ""),
            "benchmark_total_return": metrics.get("benchmark_total_return", ""),
            "excess_total_return": metrics.get("excess_total_return", ""),
            "max_drawdown": metrics.get("max_drawdown", ""),
            "information_ratio": metrics.get("information_ratio", ""),
            "report_path": str(report_path.resolve()),
            "message": "fixed_strategy_validation_completed_without_promotion",
        }
        write_status(status_path, completed)
        return {
            **paths,
            **survivorship_paths,
            "history_validation_status.csv": status_path,
            "report.html": report_path,
        }
    except Exception as exc:
        write_status(
            status_path,
            {
                **base,
                "status": "failed",
                "effective_trading_days": effective_trading_days,
                "message": str(exc)[:1000],
            },
        )
        raise


def assert_research_only_history_config(config: dict[str, Any]) -> None:
    validation = config.get("validation", {})
    auto_live = config.get("auto_live", {})
    output_dir = project_path(config["paths"]["output_dir"]).resolve()
    state_db = project_path(config["paths"]["state_db"]).resolve()
    forbidden_outputs = {
        project_path("outputs").resolve(),
        project_path("outputs_live").resolve(),
    }
    errors = []
    if not bool(validation.get("research_only", False)):
        errors.append("validation.research_only must be true")
    if bool(validation.get("apply_active_execution", False)):
        errors.append("validation.apply_active_execution must be false")
    if bool(auto_live.get("allow_broker_submit", False)):
        errors.append("auto_live.allow_broker_submit must be false")
    if not bool(auto_live.get("emergency_stop", True)):
        errors.append("auto_live.emergency_stop must remain true")
    expected_strategy_sha256 = str(
        validation.get("fixed_strategy_sha256", "") or ""
    ).strip().lower()
    current_strategy_sha256 = live_strategy_hash(config)
    if not expected_strategy_sha256:
        errors.append("validation.fixed_strategy_sha256 is required")
    elif current_strategy_sha256 != expected_strategy_sha256:
        errors.append(
            "validation.fixed_strategy_sha256 does not match current strategy"
        )
    if output_dir in forbidden_outputs:
        errors.append("history validation output_dir must be isolated")
    if state_db == project_path("data/huatai_live_state.sqlite3").resolve():
        errors.append("history validation cannot use the live ledger")
    if errors:
        raise ValueError("; ".join(errors))


def require_completed_history_progress(
    store: DataStore,
    config: dict[str, Any],
) -> pd.Series:
    name = history_staging_frame_name(config, "history_backfill_progress")
    frame = store.read_frame(name, required=False)
    if frame.empty:
        raise RuntimeError("Historical backfill progress is missing.")
    row = frame.iloc[-1]
    requested = int(row.get("requested_symbols", 0))
    completed = int(row.get("completed_symbols", 0))
    remaining = int(row.get("remaining_symbols", requested))
    failures = int(row.get("failed_items", 0))
    if (
        str(row.get("status", "")) != "complete"
        or requested <= 0
        or completed != requested
        or remaining != 0
        or failures != 0
    ):
        raise RuntimeError(
            "Historical backfill is not authoritatively complete: "
            f"status={row.get('status')};completed={completed}/{requested};"
            f"remaining={remaining};failures={failures}"
        )
    return row


def formal_prices_path(store: DataStore) -> Path:
    parquet = store.cache_dir / "prices.parquet"
    csv = store.cache_dir / "prices.csv"
    if parquet.exists():
        return parquet
    if csv.exists():
        return csv
    raise RuntimeError("Formal prices cache is missing.")


def require_history_input_binding(
    config: dict[str, Any],
    store: DataStore,
    progress: pd.Series,
    prices_path: Path,
    prices_sha256: str,
) -> str:
    validation = config.get("validation", {})
    configured = (
        validation.get("history_input_binding", {})
        if isinstance(validation, dict)
        else {}
    )
    if not isinstance(configured, dict) or not configured:
        return ""
    if not bool(configured.get("required", False)):
        return ""

    progress_frame = history_staging_frame_name(config, "history_backfill_progress")
    actual = {
        "schema_version": 1,
        "cache_dir": str(store.cache_dir.resolve()),
        "prices_path": str(prices_path.resolve()),
        "prices_sha256": prices_sha256,
        "progress_frame": progress_frame,
        "progress_generated_at": str(progress.get("generated_at", "")),
        "requested_symbols": int(progress.get("requested_symbols", 0)),
        "completed_symbols": int(progress.get("completed_symbols", 0)),
        "requested_start": str(progress.get("requested_start", "")),
        "requested_end": str(progress.get("requested_end", "")),
    }
    expected = {
        "schema_version": int(configured.get("schema_version", 0) or 0),
        "cache_dir": str(project_path(configured.get("cache_dir", "")).resolve()),
        "prices_sha256": str(configured.get("prices_sha256", "") or "").lower(),
        "progress_frame": str(configured.get("progress_frame", "") or ""),
        "progress_generated_at": str(
            configured.get("progress_generated_at", "") or ""
        ),
        "requested_symbols": int(configured.get("requested_symbols", 0) or 0),
        "completed_symbols": int(configured.get("completed_symbols", 0) or 0),
        "requested_start": str(configured.get("requested_start", "") or ""),
        "requested_end": str(configured.get("requested_end", "") or ""),
    }
    comparisons = {
        "schema_version": actual["schema_version"] == expected["schema_version"],
        "cache_dir": actual["cache_dir"].casefold() == expected["cache_dir"].casefold(),
        "prices_sha256": actual["prices_sha256"] == expected["prices_sha256"],
        "progress_frame": actual["progress_frame"] == expected["progress_frame"],
        "progress_generated_at": actual["progress_generated_at"]
        == expected["progress_generated_at"],
        "requested_symbols": actual["requested_symbols"]
        == expected["requested_symbols"],
        "completed_symbols": actual["completed_symbols"]
        == expected["completed_symbols"],
        "requested_start": actual["requested_start"] == expected["requested_start"],
        "requested_end": actual["requested_end"] == expected["requested_end"],
    }
    mismatches = [name for name, matches in comparisons.items() if not matches]
    if mismatches:
        raise RuntimeError(
            "Historical validation input binding mismatch: " + ",".join(mismatches)
        )
    return mapping_sha256(actual)

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reusable_completed_validation(
    status_path: Path,
    report_path: Path,
    prices_sha256: str,
    config_sha256: str,
    strategy_sha256: str,
    history_input_binding_sha256: str,
    survivorship_audit_sha256: str,
    experiment_preregistration_sha256: str,
    minimum_trading_days: int,
    progress_generated_at: str,
) -> bool:
    frame = read_optional_csv(status_path)
    if frame.empty or not report_path.exists():
        return False
    row = frame.iloc[-1]
    return (
        str(row.get("status", "")) == "completed"
        and str(row.get("formal_prices_sha256", "")) == prices_sha256
        and str(row.get("backtest_config_sha256", "")) == config_sha256
        and str(row.get("strategy_sha256", "")) == strategy_sha256
        and (
            str(row.get("history_input_binding_sha256", ""))
            == history_input_binding_sha256
            or (
                not history_input_binding_sha256
                and pd.isna(row.get("history_input_binding_sha256", ""))
            )
        )
        and str(row.get("survivorship_audit_sha256", "")) == survivorship_audit_sha256
        and (
            str(row.get("experiment_preregistration_sha256", ""))
            == experiment_preregistration_sha256
            or (
                not experiment_preregistration_sha256
                and pd.isna(row.get("experiment_preregistration_sha256", ""))
            )
        )
        and (
            not experiment_preregistration_sha256
            or str(row.get("experiment_decision", "")) in {"passed", "rejected"}
        )
        and pd.notna(
            pd.to_numeric(row.get("effective_trading_days", 0), errors="coerce")
        )
        and float(
            pd.to_numeric(row.get("effective_trading_days", 0), errors="coerce")
        )
        >= minimum_trading_days
        and str(row.get("progress_generated_at", "")) == progress_generated_at
    )


def status_base(
    progress: pd.Series,
    prices_sha256: str,
    config_sha256: str,
    strategy_sha256: str,
    history_input_binding_sha256: str = "",
) -> dict[str, object]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "progress_generated_at": str(progress.get("generated_at", "")),
        "requested_symbols": int(progress.get("requested_symbols", 0)),
        "completed_symbols": int(progress.get("completed_symbols", 0)),
        "requested_start": str(progress.get("requested_start", "")),
        "requested_end": str(progress.get("requested_end", "")),
        "formal_prices_sha256": prices_sha256,
        "backtest_config_sha256": config_sha256,
        "strategy_sha256": strategy_sha256,
        "history_input_binding_sha256": history_input_binding_sha256,
        "effective_trading_days": "",
        "total_return": "",
        "benchmark_total_return": "",
        "excess_total_return": "",
        "max_drawdown": "",
        "information_ratio": "",
        "report_path": "",
    }


def read_metrics(path: Path) -> dict[str, object]:
    frame = read_optional_csv(path)
    if frame.empty or not {"metric", "value"}.issubset(frame.columns):
        raise RuntimeError(f"Performance metrics are missing or invalid: {path}")
    return dict(zip(frame["metric"].astype(str), frame["value"]))


def read_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def write_status(path: Path, values: dict[str, object]) -> Path:
    row = {column: values.get(column, "") for column in STATUS_COLUMNS}
    return atomic_to_csv(pd.DataFrame([row], columns=STATUS_COLUMNS), path, index=False)
