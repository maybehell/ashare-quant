from __future__ import annotations

from copy import deepcopy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import effective_index_membership, frame_sha256, mapping_sha256
from .config import project_path
from .data import DataStore
from .index_symbols import benchmark_index_identity, canonical_index_symbol
from .io_utils import atomic_to_csv, replace_with_retry
from .shadow_policy import shadow_evaluation_evidence, shadow_evaluation_identity
from .validation import latest_complete_market_date


CANDIDATE_COLUMNS = ["item", "value"]
VALIDATION_COLUMNS = ["check", "passed", "value", "threshold", "message"]
ENSEMBLE_EXECUTION_SCHEMA_VERSION = 2
FORWARD_RISK_EVALUATION_SCHEMA_VERSION = 1
FORWARD_RISK_EVALUATION_FIELDS = (
    "forward_risk_evaluation_schema_version",
    "forward_active_nav_basis",
    "forward_active_return_basis",
    "max_forward_active_drawdown",
    "max_forward_tracking_error",
    "min_forward_information_ratio",
)
CANDIDATE_FAILURE_COLUMNS = [
    "candidate_id",
    "strategy_hash",
    "behavior_hash",
    "strategy_name",
    "status",
    "freeze_price_end",
    "observed_forward_trading_days",
    "forward_excess_return",
    "recorded_at",
]
QUARANTINE_STATUSES = {"early_forward_degradation", "failed_forward_excess", "failed_forward_risk"}


FORWARD_PROGRESS_COLUMNS = [
    "candidate_id",
    "status",
    "freeze_price_end",
    "latest_comparison_date",
    "forward_start",
    "forward_end",
    "pending_signal_count",
    "latest_pending_signal_date",
    "next_pending_execution_date",
    "first_attribution_requires_close_after",
    "observed_forward_trading_days",
    "required_forward_trading_days",
    "remaining_forward_trading_days",
    "progress_ratio",
    "forward_strategy_return",
    "forward_benchmark_return",
    "forward_excess_return",
    "required_forward_excess_return",
    "forward_active_max_drawdown",
    "max_forward_active_drawdown",
    "forward_tracking_error",
    "max_forward_tracking_error",
    "forward_information_ratio",
    "min_forward_information_ratio",
    "config_hash_match",
    "optimized_on_current_cache_at_freeze",
    "overfit_status",
    "next_action",
]


def forward_risk_evaluation_identity(config: dict[str, Any]) -> dict[str, object]:
    guard = config.get("live_guard", {}) if isinstance(config.get("live_guard"), dict) else {}
    return {
        "forward_risk_evaluation_schema_version": FORWARD_RISK_EVALUATION_SCHEMA_VERSION,
        "forward_active_nav_basis": "portfolio_nav_divided_by_benchmark_nav",
        "forward_active_return_basis": "daily_portfolio_return_minus_daily_benchmark_return",
        "max_forward_active_drawdown": float(guard.get("max_forward_active_drawdown", 0.10)),
        "max_forward_tracking_error": float(guard.get("max_forward_tracking_error", 0.20)),
        "min_forward_information_ratio": float(guard.get("min_forward_information_ratio", 0.0)),
    }


def forward_risk_evaluation_evidence(
    config: dict[str, Any], manifest: dict[str, Any]
) -> tuple[bool, dict[str, object], dict[str, object]]:
    current = forward_risk_evaluation_identity(config)
    frozen = {key: manifest.get(key, "") for key in FORWARD_RISK_EVALUATION_FIELDS}
    frozen_hash = str(manifest.get("forward_risk_evaluation_sha256", "") or "")
    expected_hash = mapping_sha256(frozen)
    passed = (
        bool(frozen_hash)
        and frozen_hash == expected_hash
        and all(str(frozen.get(key, "")) == str(value) for key, value in current.items())
    )
    return passed, current, frozen


def forward_risk_metrics_from_nav(strategy_nav: pd.Series, benchmark_nav: pd.Series) -> dict[str, float | str]:
    valid = pd.DataFrame({
        "strategy_nav": pd.to_numeric(strategy_nav, errors="coerce"),
        "benchmark_nav": pd.to_numeric(benchmark_nav, errors="coerce"),
    }).dropna()
    valid = valid[(valid["strategy_nav"] > 0) & (valid["benchmark_nav"] > 0)]
    if len(valid) < 2:
        return {"active_max_drawdown": "", "tracking_error": "", "information_ratio": ""}
    active_nav = valid["strategy_nav"] / valid["benchmark_nav"]
    active_drawdown = active_nav / active_nav.cummax() - 1.0
    strategy_returns = valid["strategy_nav"].pct_change()
    benchmark_returns = valid["benchmark_nav"].pct_change()
    active_returns = (strategy_returns - benchmark_returns).dropna()
    result: dict[str, float | str] = {
        "active_max_drawdown": float(max(0.0, -active_drawdown.min())),
        "tracking_error": "",
        "information_ratio": "",
    }
    if len(active_returns) >= 2:
        active_std = float(active_returns.std(ddof=1))
        result["tracking_error"] = active_std * (252.0 ** 0.5)
        result["information_ratio"] = (
            float(active_returns.mean()) / active_std * (252.0 ** 0.5)
            if active_std > 1e-12
            else 0.0
        )
    return result


def candidate_forward_risk_state(
    config: dict[str, Any],
    manifest: dict[str, Any],
    observation: dict[str, Any] | None,
    days_ok: bool,
) -> dict[str, object]:
    defaults = forward_risk_evaluation_identity(config)
    max_drawdown = float(manifest.get("max_forward_active_drawdown", defaults["max_forward_active_drawdown"]))
    max_tracking = float(manifest.get("max_forward_tracking_error", defaults["max_forward_tracking_error"]))
    min_ir = float(manifest.get("min_forward_information_ratio", defaults["min_forward_information_ratio"]))
    values: dict[str, float | str] = {}
    for key in ("active_max_drawdown", "tracking_error", "information_ratio"):
        raw = "" if observation is None else observation.get(key, "")
        numeric = pd.to_numeric(raw, errors="coerce")
        values[key] = "" if pd.isna(numeric) else float(numeric)
    available = all(values[key] != "" for key in values)
    drawdown_meets = bool(available and float(values["active_max_drawdown"]) <= max_drawdown)
    tracking_meets = bool(available and float(values["tracking_error"]) <= max_tracking)
    ir_meets = bool(available and float(values["information_ratio"]) >= min_ir)
    drawdown_ok = days_ok and drawdown_meets
    tracking_ok = days_ok and tracking_meets
    ir_ok = days_ok and ir_meets
    return {
        **values,
        "max_active_drawdown": max_drawdown,
        "max_tracking_error": max_tracking,
        "min_information_ratio": min_ir,
        "drawdown_ok": drawdown_ok,
        "tracking_ok": tracking_ok,
        "information_ratio_ok": ir_ok,
        "risk_ok": bool(days_ok and drawdown_meets and tracking_meets and ir_meets),
    }


def candidate_manifest_path(config: dict[str, Any]) -> Path:
    configured = config.get("validation", {}).get("candidate_path", "data/live_candidate.json")
    return project_path(configured)


def candidate_csv_path(config: dict[str, Any]) -> Path:
    return project_path(config["paths"]["output_dir"]) / "live_candidate.csv"


def candidate_validation_path(config: dict[str, Any]) -> Path:
    return project_path(config["paths"]["output_dir"]) / "live_candidate_validation.csv"


def candidate_failure_history_path(config: dict[str, Any]) -> Path:
    configured = config.get("validation", {}).get("candidate_failure_history_path", "")
    if configured:
        return project_path(configured)
    return candidate_manifest_path(config).with_name("candidate_failure_history.csv")


def candidate_failure_history_report_path(config: dict[str, Any]) -> Path:
    return project_path(config["paths"]["output_dir"]) / "candidate_failure_history.csv"


def mirror_candidate_failure_history(config: dict[str, Any], history: pd.DataFrame) -> None:
    report_path = candidate_failure_history_report_path(config)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_to_csv(history[CANDIDATE_FAILURE_COLUMNS], report_path, index=False, encoding="utf-8-sig")


def load_candidate_failure_history(config: dict[str, Any]) -> pd.DataFrame:
    path = candidate_failure_history_path(config)
    if not path.exists():
        return pd.DataFrame(columns=CANDIDATE_FAILURE_COLUMNS)
    try:
        history = pd.read_csv(path, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=CANDIDATE_FAILURE_COLUMNS)
    for column in CANDIDATE_FAILURE_COLUMNS:
        if column not in history.columns:
            history[column] = ""
    return history[CANDIDATE_FAILURE_COLUMNS]


def is_candidate_quarantined(config: dict[str, Any], strategy_hash: str | None = None) -> bool:
    history = load_candidate_failure_history(config)
    if history.empty:
        return False
    target = str(strategy_hash or live_strategy_hash(config))
    behavior_target = live_strategy_behavior_hash(config)
    return bool(
        (
            (
                history["strategy_hash"].fillna("").astype(str).eq(target)
                | history["behavior_hash"].fillna("").astype(str).eq(behavior_target)
            )
            & history["status"].fillna("").astype(str).isin(QUARANTINE_STATUSES)
        ).any()
    )


def quarantined_ensemble_members(config: dict[str, Any]) -> list[str]:
    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    members = strategy.get("ensemble_members", [])
    if not isinstance(members, list):
        return []
    base = {key: deepcopy(value) for key, value in strategy.items() if key != "ensemble_members"}
    quarantined: list[str] = []
    for index, member in enumerate(members):
        if not isinstance(member, dict):
            continue
        member_config = deepcopy(config)
        member_strategy = deepcopy(base)
        member_strategy.update(deepcopy(member))
        member_strategy.pop("blend_weight", None)
        member_config["strategy"] = member_strategy
        if is_candidate_quarantined(member_config):
            quarantined.append(str(member.get("name", "") or f"member_{index + 1}"))
    return quarantined


def record_candidate_failure(config: dict[str, Any], progress: pd.DataFrame) -> Path | None:
    if progress.empty:
        return None
    row = progress.iloc[-1]
    status = str(row.get("status", "") or "")
    if status not in QUARANTINE_STATUSES:
        return None
    manifest = load_candidate_manifest(config) or {}
    candidate_id = str(row.get("candidate_id", "") or manifest.get("candidate_id", ""))
    strategy_hash = str(manifest.get("strategy_hash", "") or live_strategy_hash(config))
    behavior_hash = live_strategy_behavior_hash(config)
    if not candidate_id or not strategy_hash:
        return None
    history = load_candidate_failure_history(config)
    duplicate = (
        history["candidate_id"].fillna("").astype(str).eq(candidate_id)
        & history["status"].fillna("").astype(str).eq(status)
    )
    if duplicate.any():
        history.loc[duplicate, "behavior_hash"] = behavior_hash
        atomic_to_csv(history[CANDIDATE_FAILURE_COLUMNS], candidate_failure_history_path(config), index=False, encoding="utf-8-sig")
        mirror_candidate_failure_history(config, history)
        return candidate_failure_history_path(config)
    failure = {
        "candidate_id": candidate_id,
        "strategy_hash": strategy_hash,
        "behavior_hash": behavior_hash,
        "strategy_name": str(manifest.get("strategy_name", config.get("strategy", {}).get("name", ""))),
        "status": status,
        "freeze_price_end": str(row.get("freeze_price_end", "") or manifest.get("freeze_price_end", "")),
        "observed_forward_trading_days": row.get("observed_forward_trading_days", ""),
        "forward_excess_return": row.get("forward_excess_return", ""),
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    result = pd.concat([history, pd.DataFrame([failure])], ignore_index=True)
    path = candidate_failure_history_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_to_csv(result[CANDIDATE_FAILURE_COLUMNS], path, index=False, encoding="utf-8-sig")
    mirror_candidate_failure_history(config, result)
    return path


def freeze_live_candidate(config: dict[str, Any], store: DataStore, force: bool = False) -> list[Path]:
    validation = config.get("validation", {}) if isinstance(config.get("validation", {}), dict) else {}
    if bool(validation.get("research_only", False)):
        status = str(validation.get("experiment_status", "research_only") or "research_only")
        reason = str(validation.get("experiment_reason", "isolated research configuration") or "isolated research configuration")
        raise RuntimeError(f"Cannot freeze research-only strategy ({status}): {reason}")
    path = candidate_manifest_path(config)
    if path.exists() and not force:
        raise RuntimeError(f"Live candidate already exists at {path}. Use --force to replace it.")
    if is_candidate_quarantined(config):
        raise RuntimeError(
            "Cannot freeze this strategy because the same strategy fingerprint is quarantined after forward failure. "
            "Create and review a materially different strategy configuration instead of resetting its evidence clock."
        )
    failed_members = quarantined_ensemble_members(config)
    if failed_members:
        raise RuntimeError(
            "Cannot freeze this ensemble because it contains quarantined member strategies: "
            + ", ".join(failed_members)
        )

    stocks, prices, fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    if prices.empty:
        raise RuntimeError("Cannot freeze live candidate because cached prices are empty.")

    stability = load_candidate_stability(config)
    if config.get("live_guard", {}).get("require_candidate_stability", False) and not candidate_stability_passed(stability):
        failed = candidate_stability_failed_items(stability)
        detail = ", ".join(failed) if failed else "missing strategy_candidate_stability.csv"
        raise RuntimeError(
            "Cannot freeze live candidate because candidate stability evidence is incomplete: "
            f"{detail}. Run research and report with current data first."
        )

    manifest = build_candidate_manifest(
        config,
        stocks,
        prices,
        fundamentals,
        benchmark,
        candidate_stability=stability,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_candidate_manifest(path, manifest)

    csv_path = candidate_csv_path(config)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_frame(manifest).to_csv(csv_path, index=False, encoding="utf-8-sig")
    return [path, csv_path]


def preregister_forward_candidate(
    config: dict[str, Any],
    store: DataStore,
    *,
    force: bool = False,
) -> list[Path]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    status = str(validation.get("experiment_status", "") or "")
    if not bool(validation.get("research_only", False)) or status != "preregistered_forward_only":
        raise RuntimeError(
            "Forward-only preregistration requires validation.research_only=true and "
            "validation.experiment_status=preregistered_forward_only."
        )
    path = candidate_manifest_path(config)
    if path.exists() and not force:
        raise RuntimeError(f"Forward candidate already exists at {path}. Use --force to replace it.")
    if is_candidate_quarantined(config):
        raise RuntimeError(
            "Cannot preregister a quarantined strategy fingerprint; change the strategy behavior first."
        )
    failed_members = quarantined_ensemble_members(config)
    if failed_members:
        raise RuntimeError(
            "Cannot preregister a strategy containing quarantined members: "
            + ", ".join(failed_members)
        )

    stocks, prices, fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    if prices.empty:
        raise RuntimeError("Cannot preregister a forward candidate because cached prices are empty.")
    manifest = build_candidate_manifest(
        config,
        stocks,
        prices,
        fundamentals,
        benchmark,
        candidate_stability=pd.DataFrame(),
    )
    manifest.update(
        {
            "freeze_mode": "forward_only_preregistration",
            "promotion_eligible_at_freeze": False,
            "historical_selection_evidence_allowed": False,
            "preregistration_reason": str(
                validation.get("research_rationale")
                or validation.get("experiment_reason")
                or "forward-only structural candidate"
            ),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_candidate_manifest(path, manifest)
    csv_path = candidate_csv_path(config)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_frame(manifest).to_csv(csv_path, index=False, encoding="utf-8-sig")
    return [path, csv_path]


def load_candidate_stability(config: dict[str, Any]) -> pd.DataFrame:
    path = project_path(config["paths"]["output_dir"]) / "strategy_candidate_stability.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def candidate_stability_passed(stability: pd.DataFrame) -> bool:
    if stability.empty or not {"item", "passed"}.issubset(stability.columns):
        return False
    return bool(stability["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"}).all())


def candidate_stability_failed_items(stability: pd.DataFrame) -> list[str]:
    if stability.empty or not {"item", "passed"}.issubset(stability.columns):
        return []
    passed = stability["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    return stability.loc[~passed, "item"].astype(str).tolist()

def build_candidate_manifest(
    config: dict[str, Any],
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    candidate_stability: pd.DataFrame | None = None,
) -> dict[str, Any]:
    complete_end = latest_complete_market_date(prices, benchmark, config)
    raw_price_end = pd.to_datetime(prices["date"], errors="coerce").dropna().max().date()
    benchmark_end = ""
    benchmark_end_date = None
    if not benchmark.empty and "date" in benchmark:
        benchmark_dates = pd.to_datetime(benchmark["date"], errors="coerce").dropna()
        if not benchmark_dates.empty:
            benchmark_end_date = benchmark_dates.max().date()
            benchmark_end = benchmark_end_date.isoformat()
    known_data_end = max(
        value for value in (raw_price_end, benchmark_end_date) if value is not None
    )
    price_end = known_data_end.isoformat()
    complete_market_end = (complete_end or raw_price_end).isoformat()

    strategy_hash = live_strategy_hash(config)
    membership = candidate_index_membership_at(config, price_end)
    membership_hash = candidate_index_membership_sha256(
        membership, INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION
    )
    benchmark_identity = benchmark_index_identity(
        config.get("benchmark", {}).get("symbol", "000300")
    )
    benchmark_identity.update(candidate_benchmark_data_identity(benchmark, price_end))
    shadow_identity = shadow_evaluation_identity(config)
    forward_risk_identity = forward_risk_evaluation_identity(config)
    candidate_identity = {
        "strategy_hash": strategy_hash,
        "freeze_price_end": price_end,
        "shadow_evaluation_sha256": mapping_sha256(shadow_identity),
    }
    if membership_hash:
        candidate_identity["index_membership_sha256_at_freeze"] = membership_hash
    candidate_id = mapping_sha256(candidate_identity)[:16]
    guard = config.get("live_guard", {})
    stability = candidate_stability if candidate_stability is not None else pd.DataFrame()
    stability_ok = candidate_stability_passed(stability)
    frozen_inputs = candidate_frozen_input_fields(stocks, prices, fundamentals, benchmark, price_end)
    return {
        "candidate_id": candidate_id,
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "freeze_price_end": price_end,
        "freeze_boundary_schema_version": CANDIDATE_FREEZE_BOUNDARY_SCHEMA_VERSION,
        "freeze_known_data_end": price_end,
        "freeze_raw_price_end": raw_price_end.isoformat(),
        "freeze_complete_market_end": complete_market_end,
        "benchmark_end": benchmark_end,
        "strategy_hash": strategy_hash,
        "strategy_name": str(config.get("strategy", {}).get("name", "")),
        "price_symbol_count": int(prices["symbol"].astype(str).nunique()) if "symbol" in prices else 0,
        "stock_count": int(len(stocks)),
        "fundamental_rows": int(len(fundamentals)),
        "index_membership_hash_schema_version": INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION,
        "benchmark_rows": int(len(benchmark)),
        "index_membership_rows_at_freeze": candidate_index_membership_rows(
            membership, INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION
        ),
        "index_membership_sha256_at_freeze": membership_hash,
        **candidate_effective_index_membership_fields(membership),
        **benchmark_identity,
        **shadow_identity,
        **forward_risk_identity,
        "forward_risk_evaluation_sha256": mapping_sha256(forward_risk_identity),
        "required_forward_trading_days": int(guard.get("min_forward_trading_days", 60)),
        "required_forward_excess_return": float(guard.get("min_forward_excess_return", 0.0)),
        "min_early_forward_trading_days": int(guard.get("min_early_forward_trading_days", 5)),
        "min_early_forward_excess_return": float(guard.get("min_early_forward_excess_return", -0.05)),
        "early_forward_warning_ratio": float(guard.get("early_forward_warning_ratio", 0.60)),
        "optimized_on_current_cache_at_freeze": bool(config.get("validation", {}).get("optimized_on_current_cache", False)),
        "candidate_stability_rows": int(len(stability)),
        "candidate_stability_sha256": frame_sha256(stability) if not stability.empty else "",
        "candidate_stability_passed_at_freeze": stability_ok,
        "live_strategy_config": live_strategy_config(config),
        **frozen_inputs,
    }


FROZEN_INPUT_SCHEMA_VERSION = 1
CANDIDATE_FREEZE_BOUNDARY_SCHEMA_VERSION = 2
SEMANTIC_INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION = 2
INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION = 3
FROZEN_INPUT_SPECS = (
    ("stocks", None),
    ("prices", "date"),
    ("fundamentals", "date"),
    ("benchmark", "date"),
)


CANDIDATE_EVIDENCE_IDENTITY_FIELDS = (
    "candidate_id",
    "strategy_hash",
    "freeze_mode",
    "freeze_boundary_schema_version",
    "freeze_known_data_end",
    "freeze_price_end",
    "frozen_input_schema_version",
    "frozen_stocks_rows",
    "frozen_stocks_sha256",
    "frozen_prices_rows",
    "frozen_prices_sha256",
    "frozen_fundamentals_rows",
    "frozen_fundamentals_sha256",
    "frozen_benchmark_rows",
    "frozen_benchmark_sha256",
)


def candidate_evidence_identity_sha256(manifest: dict[str, Any] | None) -> str:
    if not isinstance(manifest, dict) or not str(manifest.get("candidate_id", "") or ""):
        return ""
    return mapping_sha256({field: manifest.get(field, "") for field in CANDIDATE_EVIDENCE_IDENTITY_FIELDS})

def canonical_frozen_input(frame: pd.DataFrame, cutoff: object, date_column: str | None) -> pd.DataFrame:
    out = frame.copy()
    if date_column is not None:
        if date_column not in out.columns:
            return out.iloc[0:0].copy()
        dates = pd.to_datetime(out[date_column], errors="coerce")
        cutoff_date = pd.to_datetime(cutoff, errors="coerce")
        if pd.isna(cutoff_date):
            return out.iloc[0:0].copy()
        out = out[dates.notna() & (dates <= cutoff_date)].copy()
        out[date_column] = pd.to_datetime(out[date_column], errors="coerce").dt.date
    columns = sorted(str(column) for column in out.columns)
    out = out[columns]
    sort_columns = [
        column for column in ("date", "snapshot_date", "index_symbol", "symbol")
        if column in out.columns
    ]
    if sort_columns:
        out = out.sort_values(sort_columns, kind="stable")
    return out.reset_index(drop=True)


def candidate_frozen_input_fields(
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    cutoff: object,
) -> dict[str, Any]:
    frames = {
        "stocks": stocks,
        "prices": prices,
        "fundamentals": fundamentals,
        "benchmark": benchmark,
    }
    result: dict[str, Any] = {"frozen_input_schema_version": FROZEN_INPUT_SCHEMA_VERSION}
    for name, date_column in FROZEN_INPUT_SPECS:
        frozen = canonical_frozen_input(frames[name], cutoff, date_column)
        result[f"frozen_{name}_rows"] = int(len(frozen))
        result[f"frozen_{name}_sha256"] = frame_sha256(frozen)
    return result


def candidate_frozen_input_evidence(
    config: dict[str, Any],
    manifest: dict[str, Any],
    store: DataStore | None = None,
) -> pd.DataFrame:
    columns = ["input", "passed", "current_rows", "frozen_rows", "current_sha256", "frozen_sha256"]
    try:
        source = store or DataStore.from_config(config)
        stocks, prices, fundamentals = source.load_market_data()
        benchmark = source.load_benchmark(required=False)
    except (FileNotFoundError, ValueError):
        stocks = prices = fundamentals = benchmark = pd.DataFrame()
    current = candidate_frozen_input_fields(
        stocks,
        prices,
        fundamentals,
        benchmark,
        manifest.get("freeze_price_end"),
    )
    schema_ok = int(manifest.get("frozen_input_schema_version", 0) or 0) == FROZEN_INPUT_SCHEMA_VERSION
    rows = []
    for name, _date_column in FROZEN_INPUT_SPECS:
        current_rows = int(current[f"frozen_{name}_rows"])
        current_hash = str(current[f"frozen_{name}_sha256"])
        frozen_rows = int(manifest.get(f"frozen_{name}_rows", -1) or 0)
        frozen_hash = str(manifest.get(f"frozen_{name}_sha256", "") or "")
        rows.append(
            {
                "input": name,
                "passed": bool(
                    schema_ok
                    and frozen_rows >= 0
                    and bool(frozen_hash)
                    and current_rows == frozen_rows
                    and current_hash == frozen_hash
                ),
                "current_rows": current_rows,
                "frozen_rows": frozen_rows,
                "current_sha256": current_hash,
                "frozen_sha256": frozen_hash,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def write_candidate_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def seal_candidate_frozen_inputs(config: dict[str, Any], store: DataStore) -> list[Path]:
    manifest = load_candidate_manifest(config)
    if not manifest:
        raise RuntimeError("Cannot seal frozen inputs because the candidate manifest is missing.")
    if not matching_candidate_id(config, manifest):
        raise RuntimeError("Cannot seal frozen inputs because the candidate strategy hash no longer matches.")
    legacy_membership_hash = str(manifest.get("index_membership_sha256_at_freeze", "") or "")
    membership_schema = int(manifest.get("index_membership_hash_schema_version", 1) or 1)
    mapping_ok, _, _ = candidate_benchmark_mapping_evidence(config, manifest)
    identity_migration_needed = (
        membership_schema < INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION or not mapping_ok
    )
    if identity_migration_needed and candidate_observed_shadow_days(config, manifest) > 0:
        raise RuntimeError(
            "Cannot migrate benchmark or membership identity after forward observations exist."
        )
    membership_ok, *_ = candidate_index_membership_evidence(config, manifest)
    if not membership_ok:
        raise RuntimeError("Cannot seal frozen inputs because frozen index membership no longer matches.")
    stocks, prices, fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    fields = candidate_frozen_input_fields(
        stocks, prices, fundamentals, benchmark, manifest.get("freeze_price_end")
    )
    if int(manifest.get("frozen_input_schema_version", 0) or 0) == FROZEN_INPUT_SCHEMA_VERSION:
        evidence = candidate_frozen_input_evidence(config, manifest, store)
        if evidence.empty or not bool(evidence["passed"].all()):
            raise RuntimeError("Refusing to reseal candidate inputs because existing frozen hashes do not match.")
    membership = candidate_index_membership_at(config, manifest.get("freeze_price_end"))
    semantic_hash = candidate_index_membership_sha256(
        membership, INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION
    )
    if membership_schema < INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION:
        if membership_schema < SEMANTIC_INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION:
            manifest["index_membership_legacy_sha256_at_freeze"] = legacy_membership_hash
        manifest["index_membership_hash_migrated_at"] = datetime.now().isoformat(timespec="seconds")
        manifest["index_membership_hash_migration_from_schema"] = membership_schema
    manifest["index_membership_hash_schema_version"] = INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION
    manifest["index_membership_rows_at_freeze"] = int(len(canonical_candidate_index_membership(membership)))
    manifest["index_membership_sha256_at_freeze"] = semantic_hash
    manifest.update(candidate_effective_index_membership_fields(membership))
    benchmark_identity = benchmark_index_identity(
        config.get("benchmark", {}).get("symbol", "000300")
    )
    benchmark_identity.update(
        candidate_benchmark_data_identity(benchmark, manifest.get("freeze_price_end"))
    )
    if not mapping_ok:
        manifest["benchmark_index_mapping_migrated_at"] = datetime.now().isoformat(timespec="seconds")
        manifest["benchmark_index_mapping_migration_from_schema"] = int(
            manifest.get("benchmark_index_mapping_schema_version", 0) or 0
        )
    manifest.update(benchmark_identity)
    shadow_ok, _, _ = shadow_evaluation_evidence(config, manifest)
    if not shadow_ok:
        observed_days = candidate_observed_shadow_days(config, manifest)
        if observed_days > 0:
            raise RuntimeError(
                "Cannot migrate shadow evaluation policy after forward observations exist."
            )
        manifest["shadow_evaluation_migrated_at"] = datetime.now().isoformat(timespec="seconds")
    manifest.update(shadow_evaluation_identity(config))
    forward_risk_identity = forward_risk_evaluation_identity(config)
    frozen_risk_schema = int(manifest.get("forward_risk_evaluation_schema_version", 0) or 0)
    frozen_risk = {key: manifest.get(key, "") for key in forward_risk_identity}
    risk_matches = all(str(frozen_risk.get(key, "")) == str(value) for key, value in forward_risk_identity.items())
    if not risk_matches:
        observed_days = candidate_observed_shadow_days(config, manifest)
        if observed_days > 0:
            raise RuntimeError(
                "Cannot migrate forward risk evaluation after forward observations exist."
            )
        manifest["forward_risk_evaluation_migrated_at"] = datetime.now().isoformat(timespec="seconds")
        manifest["forward_risk_evaluation_migration_from_schema"] = frozen_risk_schema
    manifest.update(forward_risk_identity)
    manifest["forward_risk_evaluation_sha256"] = mapping_sha256(forward_risk_identity)
    manifest.update(fields)
    path = candidate_manifest_path(config)
    write_candidate_manifest(path, manifest)
    csv_path = candidate_csv_path(config)
    atomic_to_csv(candidate_frame(manifest), csv_path, index=False, encoding="utf-8-sig")
    return [path, csv_path]

def candidate_observed_shadow_days(
    config: dict[str, Any], manifest: dict[str, Any]
) -> int:
    path = project_path(config["paths"]["output_dir"]) / "shadow_forward_audit.csv"
    if not path.exists():
        return 0
    try:
        audit = pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError):
        return 0
    if audit.empty or "observed_trading_days" not in audit:
        return 0
    candidate_id = str(manifest.get("candidate_id", "") or "")
    if "candidate_id" in audit and candidate_id:
        audit = audit[audit["candidate_id"].fillna("").astype(str) == candidate_id]
    if "evidence_type" in audit and audit["evidence_type"].fillna("").eq("continuous_portfolio").any():
        audit = audit[audit["evidence_type"].fillna("").eq("continuous_portfolio")]
        accepted_status = "continuous_ok"
    else:
        accepted_status = "ok"
    if "status" in audit:
        audit = audit[audit["status"].fillna("").astype(str).str.lower() == accepted_status]
    days = pd.to_numeric(audit["observed_trading_days"], errors="coerce").dropna()
    return int(days.max()) if not days.empty else 0

def candidate_index_membership_at(
    config: dict[str, Any],
    cutoff: object,
) -> pd.DataFrame:
    membership = effective_index_membership(config)
    if membership is None:
        from .index_membership import load_membership_from_config

        membership = load_membership_from_config(config)
        index_symbol = canonical_index_symbol(
            config.get("benchmark", {}).get("symbol", "000300")
        )
        if index_symbol and "index_symbol" in membership:
            membership = membership[
                membership["index_symbol"].map(canonical_index_symbol) == index_symbol
            ].copy()
    if membership.empty:
        return pd.DataFrame()
    cutoff_date = pd.to_datetime(cutoff, errors="coerce")
    if pd.isna(cutoff_date):
        return pd.DataFrame(columns=membership.columns)
    return (
        membership[membership["snapshot_date"] <= cutoff_date.date()]
        .sort_values(["snapshot_date", "index_symbol", "symbol"])
        .reset_index(drop=True)
    )


def canonical_candidate_index_membership(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["snapshot_date", "index_symbol", "symbol"]
    if frame.empty or not set(columns).issubset(frame.columns):
        return pd.DataFrame(columns=columns)
    from .index_membership import normalize_index_membership

    normalized = normalize_index_membership(frame)
    return (
        normalized[columns]
        .drop_duplicates(columns)
        .sort_values(columns, kind="stable")
        .reset_index(drop=True)
    )


def effective_candidate_index_membership(frame: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical_candidate_index_membership(frame)
    if canonical.empty:
        return canonical
    latest = canonical["snapshot_date"].max()
    return canonical[canonical["snapshot_date"] == latest].reset_index(drop=True)


def candidate_effective_index_membership_fields(frame: pd.DataFrame) -> dict[str, Any]:
    effective = effective_candidate_index_membership(frame)
    return {
        "index_membership_effective_date_at_freeze": (
            "" if effective.empty else str(effective["snapshot_date"].max())
        ),
        "index_membership_effective_rows_at_freeze": int(len(effective)),
        "index_membership_effective_sha256_at_freeze": (
            "" if effective.empty else frame_sha256(effective)
        ),
    }


def candidate_benchmark_data_identity(
    benchmark: pd.DataFrame,
    cutoff: object,
) -> dict[str, Any]:
    frame = benchmark.copy()
    cutoff_date = pd.to_datetime(cutoff, errors="coerce")
    if "date" in frame and not pd.isna(cutoff_date):
        dates = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame[dates.notna() & (dates <= cutoff_date)].copy()
    symbols = (
        sorted({canonical_index_symbol(value) for value in frame["symbol"] if canonical_index_symbol(value)})
        if "symbol" in frame
        else []
    )
    return {
        "benchmark_data_identity_schema_version": 1,
        "benchmark_data_symbol_canonical_at_freeze": ",".join(symbols),
        "benchmark_data_rows_at_freeze": int(len(frame)),
    }

def candidate_index_membership_sha256(frame: pd.DataFrame, schema_version: int) -> str:
    if frame.empty:
        return ""
    if schema_version >= SEMANTIC_INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION:
        frame = canonical_candidate_index_membership(frame)
    return frame_sha256(frame)


def candidate_index_membership_rows(frame: pd.DataFrame, schema_version: int) -> int:
    if schema_version >= SEMANTIC_INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION:
        frame = canonical_candidate_index_membership(frame)
    return int(len(frame))


def candidate_freeze_boundary_evidence(
    manifest: dict[str, Any],
) -> tuple[bool, str, str]:
    schema_version = int(manifest.get("freeze_boundary_schema_version", 0) or 0)
    freeze_date = pd.to_datetime(manifest.get("freeze_price_end"), errors="coerce")
    known_dates = pd.to_datetime(
        [
            manifest.get("freeze_known_data_end"),
            manifest.get("freeze_raw_price_end"),
            manifest.get("benchmark_end"),
        ],
        errors="coerce",
    )
    valid_known_dates = known_dates[~pd.isna(known_dates)]
    known_end = valid_known_dates.max() if len(valid_known_dates) else pd.NaT
    passed = bool(
        schema_version >= CANDIDATE_FREEZE_BOUNDARY_SCHEMA_VERSION
        and not pd.isna(freeze_date)
        and not pd.isna(known_end)
        and freeze_date >= known_end
    )
    return (
        passed,
        "" if pd.isna(freeze_date) else freeze_date.date().isoformat(),
        "" if pd.isna(known_end) else known_end.date().isoformat(),
    )


def candidate_index_membership_evidence(
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[bool, int, str, int, str]:
    policy = config.get("strategy", {}).get("benchmark_membership", {})
    required = (
        (isinstance(policy, dict) and bool(policy.get("enabled", False)))
        or bool(manifest.get("index_membership_sha256_at_freeze"))
    )
    frozen_rows = int(manifest.get("index_membership_rows_at_freeze", 0) or 0)
    frozen_hash = str(manifest.get("index_membership_sha256_at_freeze", "") or "")
    current = candidate_index_membership_at(config, manifest.get("freeze_price_end"))
    schema_version = int(manifest.get("index_membership_hash_schema_version", 1) or 1)
    current_rows = candidate_index_membership_rows(current, schema_version)
    current_hash = candidate_index_membership_sha256(current, schema_version)
    effective = candidate_effective_index_membership_fields(current)
    effective_required = schema_version >= 3
    effective_ok = (
        not effective_required
        or (
            bool(effective["index_membership_effective_date_at_freeze"])
            and int(effective["index_membership_effective_rows_at_freeze"]) > 0
            and bool(effective["index_membership_effective_sha256_at_freeze"])
            and str(effective["index_membership_effective_date_at_freeze"])
            == str(manifest.get("index_membership_effective_date_at_freeze", "") or "")
            and int(effective["index_membership_effective_rows_at_freeze"])
            == int(manifest.get("index_membership_effective_rows_at_freeze", 0) or 0)
            and str(effective["index_membership_effective_sha256_at_freeze"])
            == str(manifest.get("index_membership_effective_sha256_at_freeze", "") or "")
        )
    )
    passed = not required or (
        frozen_rows > 0
        and bool(frozen_hash)
        and current_rows == frozen_rows
        and current_hash == frozen_hash
        and effective_ok
    )
    return passed, current_rows, current_hash, frozen_rows, frozen_hash


def candidate_benchmark_mapping_evidence(
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[bool, dict[str, object], dict[str, object]]:
    current = benchmark_index_identity(config.get("benchmark", {}).get("symbol", "000300"))
    current.update(
        {
            "benchmark_data_identity_schema_version": 1,
            "benchmark_data_symbol_canonical_at_freeze": current["benchmark_symbol_canonical"],
        }
    )
    frozen = {key: manifest.get(key, "") for key in current}
    frozen_rows = int(manifest.get("benchmark_data_rows_at_freeze", 0) or 0)
    passed = bool(
        frozen_rows > 0
        and all(str(frozen.get(key, "")) == str(value) for key, value in current.items())
    )
    return passed, current, frozen


def live_strategy_config(config: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "benchmark",
        "risk_overlay",
        "rules",
        "strategy",
        "trading",
        "universe",
    ]
    payload = {key: config.get(key, {}) for key in keys}
    # Preserve legacy hashes for strategies without a breadth overlay while
    # ensuring exposure-rule changes cannot inherit another candidate's evidence.
    if "breadth_overlay" in config:
        payload["breadth_overlay"] = config.get("breadth_overlay", {})
    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    members = strategy.get("ensemble_members", [])
    if isinstance(members, list) and members:
        payload["implementation"] = {
            "ensemble_execution_schema": ENSEMBLE_EXECUTION_SCHEMA_VERSION,
        }
    return payload


def live_strategy_hash(config: dict[str, Any]) -> str:
    return mapping_sha256(live_strategy_config(config))


def live_strategy_behavior_config(config: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(live_strategy_config(config))
    strategy = payload.get("strategy", {}) if isinstance(payload.get("strategy"), dict) else {}
    strategy.pop("name", None)
    members = strategy.get("ensemble_members", [])
    if isinstance(members, list):
        for member in members:
            if isinstance(member, dict):
                member.pop("name", None)
                normalize_zero_factor_weights(member)
    normalize_zero_factor_weights(strategy)
    return payload


def normalize_zero_factor_weights(strategy: dict[str, Any]) -> None:
    weights = strategy.get("weights")
    if isinstance(weights, dict):
        strategy["weights"] = {
            key: value
            for key, value in weights.items()
            if pd.to_numeric(value, errors="coerce") != 0
        }
    adaptive = strategy.get("adaptive_weights")
    if isinstance(adaptive, dict):
        risk_on = adaptive.get("risk_on_weights")
        if isinstance(risk_on, dict):
            adaptive["risk_on_weights"] = {
                key: value
                for key, value in risk_on.items()
                if pd.to_numeric(value, errors="coerce") != 0
            }


def live_strategy_behavior_hash(config: dict[str, Any]) -> str:
    return mapping_sha256(live_strategy_behavior_config(config))


def matching_candidate_id(config: dict[str, Any], manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return ""
    frozen_hash = str(manifest.get("strategy_hash", "") or "")
    if not frozen_hash or frozen_hash != live_strategy_hash(config):
        return ""
    return str(manifest.get("candidate_id", "") or "")


def load_candidate_manifest(config: dict[str, Any]) -> dict[str, Any] | None:
    configured = str(
        config.get("validation", {}).get("candidate_path", "data/live_candidate.json")
        or ""
    ).strip()
    if not configured:
        return None
    path = candidate_manifest_path(config)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_frame(manifest: dict[str, Any] | None) -> pd.DataFrame:
    if not manifest:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    rows = []
    for key in [
        "candidate_id",
        "frozen_at",
        "freeze_price_end",
        "freeze_boundary_schema_version",
        "freeze_known_data_end",
        "freeze_raw_price_end",
        "freeze_complete_market_end",
        "benchmark_end",
        "strategy_hash",
        "strategy_name",
        "price_symbol_count",
        "stock_count",
        "fundamental_rows",
        "benchmark_rows",
        "benchmark_index_mapping_schema_version",
        "benchmark_symbol_canonical",
        "benchmark_symbol_akshare",
        "benchmark_symbol_baostock",
        "benchmark_symbol_sina",
        "benchmark_data_identity_schema_version",
        "benchmark_data_symbol_canonical_at_freeze",
        "benchmark_data_rows_at_freeze",
        "shadow_evaluation_schema_version",
        "shadow_entry_price_basis",
        "shadow_return_basis",
        "shadow_benchmark_basis",
        "shadow_entry_cost_rate",
        "shadow_candidate_return_basis",
        "shadow_candidate_rebalance_rule",
        "shadow_candidate_execution_price_basis",
        "shadow_candidate_benchmark_basis",
        "shadow_candidate_buy_cost_rate",
        "shadow_candidate_sell_cost_rate",
        "required_forward_trading_days",
        "required_forward_excess_return",
        "forward_risk_evaluation_schema_version",
        "forward_risk_evaluation_sha256",
        "forward_active_nav_basis",
        "forward_active_return_basis",
        "max_forward_active_drawdown",
        "max_forward_tracking_error",
        "min_forward_information_ratio",
        "index_membership_hash_schema_version",
        "index_membership_rows_at_freeze",
        "index_membership_sha256_at_freeze",
        "index_membership_effective_date_at_freeze",
        "index_membership_effective_rows_at_freeze",
        "index_membership_effective_sha256_at_freeze",
        "frozen_input_schema_version",
        "frozen_stocks_rows",
        "frozen_stocks_sha256",
        "frozen_prices_rows",
        "frozen_prices_sha256",
        "frozen_fundamentals_rows",
        "frozen_fundamentals_sha256",
        "frozen_benchmark_rows",
        "frozen_benchmark_sha256",
        "min_early_forward_trading_days",
        "min_early_forward_excess_return",
        "early_forward_warning_ratio",
        "optimized_on_current_cache_at_freeze",
        "candidate_stability_rows",
        "candidate_stability_sha256",
        "candidate_stability_passed_at_freeze",
    ]:
        rows.append({"item": key, "value": manifest.get(key, "")})
    return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)

def candidate_shadow_observation(
    shadow_audit: pd.DataFrame | None,
    candidate_id: object,
    freeze_date: object = None,
) -> dict[str, Any] | None:
    required = {
        "candidate_id",
        "observed_trading_days",
        "portfolio_return",
        "benchmark_return",
    }
    if shadow_audit is None or shadow_audit.empty or not required.issubset(shadow_audit.columns):
        return None
    data = shadow_audit.copy()
    data = data[data["candidate_id"].fillna("").astype(str) == str(candidate_id or "")]
    if "evidence_type" in data.columns:
        continuous = data["evidence_type"].fillna("").astype(str).eq("continuous_portfolio")
        if continuous.any():
            data = data[continuous]
            accepted_status = "continuous_ok"
        else:
            accepted_status = "ok"
    else:
        accepted_status = "ok"
    if "status" in data.columns:
        data = data[data["status"].fillna("").astype(str).str.lower() == accepted_status]
    data["observed_trading_days"] = pd.to_numeric(data["observed_trading_days"], errors="coerce")
    data["portfolio_return"] = pd.to_numeric(data["portfolio_return"], errors="coerce")
    data["benchmark_return"] = pd.to_numeric(data["benchmark_return"], errors="coerce")
    for column in ("active_max_drawdown", "tracking_error", "information_ratio"):
        values = data[column] if column in data else pd.Series(pd.NA, index=data.index)
        data[column] = pd.to_numeric(values, errors="coerce")
    data["execution_date_parsed"] = pd.to_datetime(data.get("execution_date"), errors="coerce")
    data["latest_date_parsed"] = pd.to_datetime(data.get("latest_date"), errors="coerce")
    freeze_date_parsed = pd.to_datetime(freeze_date, errors="coerce")
    valid_dates = (
        data["execution_date_parsed"].notna()
        & data["latest_date_parsed"].notna()
        & (data["latest_date_parsed"] >= data["execution_date_parsed"])
    )
    if not pd.isna(freeze_date_parsed):
        valid_dates &= data["execution_date_parsed"] > freeze_date_parsed
    calendar_span = (
        data["latest_date_parsed"] - data["execution_date_parsed"]
    ).dt.days
    valid_dates &= data["observed_trading_days"] <= calendar_span
    data = data[
        (data["observed_trading_days"] > 0)
        & data["portfolio_return"].notna()
        & data["benchmark_return"].notna()
        & valid_dates
    ].copy()
    if data.empty:
        return None
    data["signal_date_parsed"] = pd.to_datetime(data.get("signal_date"), errors="coerce")
    data = data.sort_values(
        ["observed_trading_days", "signal_date_parsed"],
        ascending=[False, True],
        kind="stable",
    )
    row = data.iloc[0]
    strategy_return = float(row["portfolio_return"])
    benchmark_return = float(row["benchmark_return"])
    return {
        "days": int(row["observed_trading_days"]),
        "strategy_return": strategy_return,
        "benchmark_return": benchmark_return,
        "excess_return": strategy_return - benchmark_return,
        "active_max_drawdown": (
            float(row["active_max_drawdown"]) if pd.notna(row["active_max_drawdown"]) else ""
        ),
        "tracking_error": (
            float(row["tracking_error"]) if pd.notna(row["tracking_error"]) else ""
        ),
        "information_ratio": (
            float(row["information_ratio"]) if pd.notna(row["information_ratio"]) else ""
        ),
        "forward_start": str(row.get("execution_date", "") or ""),
        "forward_end": str(row.get("latest_date", "") or ""),
        "latest_date": str(row.get("latest_date", "") or ""),
    }



def build_candidate_validation(
    config: dict[str, Any],
    comparison: pd.DataFrame,
    shadow_audit: pd.DataFrame | None = None,
) -> pd.DataFrame:
    manifest = load_candidate_manifest(config)
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    if not manifest:
        add("candidate_exists", False, "missing", "present", "缺少冻结候选参数清单；无法消除当前缓存调参污染。")
        add("forward_validation_status", False, "missing_candidate", "validated", "请先运行 freeze-candidate，然后等待新增行情后重新回测验证。")
        return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)

    add("candidate_exists", True, manifest.get("candidate_id", ""), "present", "已找到冻结候选参数清单。")

    current_hash = live_strategy_hash(config)
    frozen_hash = str(manifest.get("strategy_hash", ""))
    hash_match = bool(frozen_hash and frozen_hash == current_hash)
    add("candidate_hash_match", hash_match, current_hash[:16], frozen_hash[:16], "当前实盘相关配置必须与冻结候选参数一致。")
    membership_ok, current_rows, current_membership_hash, frozen_rows, frozen_membership_hash = (
        candidate_index_membership_evidence(config, manifest)
    )
    mapping_ok, current_mapping, frozen_mapping = candidate_benchmark_mapping_evidence(
        config, manifest
    )
    add(
        "candidate_benchmark_index_mapping",
        mapping_ok,
        json.dumps(current_mapping, ensure_ascii=False, sort_keys=True),
        json.dumps(frozen_mapping, ensure_ascii=False, sort_keys=True),
        "Benchmark identity must map to the same canonical, BaoStock, and Sina symbols frozen with the candidate.",
    )
    shadow_policy_ok, current_shadow_policy, frozen_shadow_policy = (
        shadow_evaluation_evidence(config, manifest)
    )
    add(
        "candidate_shadow_evaluation_policy",
        shadow_policy_ok,
        json.dumps(current_shadow_policy, ensure_ascii=False, sort_keys=True),
        json.dumps(frozen_shadow_policy, ensure_ascii=False, sort_keys=True),
        "Forward shadow returns must use the evaluation policy frozen before observations.",
    )
    forward_risk_policy_ok, current_forward_risk_policy, frozen_forward_risk_policy = (
        forward_risk_evaluation_evidence(config, manifest)
    )
    add(
        "candidate_forward_risk_evaluation_policy",
        forward_risk_policy_ok,
        json.dumps(current_forward_risk_policy, ensure_ascii=False, sort_keys=True),
        json.dumps(frozen_forward_risk_policy, ensure_ascii=False, sort_keys=True),
        "Forward risk definitions and thresholds must match the policy frozen before observations.",
    )
    frozen_input_required = bool(
        config.get("validation", {}).get("require_frozen_input_evidence", False)
    )
    frozen_input_evidence = (
        candidate_frozen_input_evidence(config, manifest)
        if frozen_input_required
        else pd.DataFrame(columns=["input", "passed"])
    )
    frozen_input_ok = bool(
        not frozen_input_required
        or (not frozen_input_evidence.empty and frozen_input_evidence["passed"].all())
    )
    if frozen_input_required:
        add(
            "candidate_frozen_input_evidence",
            frozen_input_ok,
            ",".join(
                frozen_input_evidence.loc[~frozen_input_evidence["passed"], "input"].astype(str)
            ),
            "all frozen inputs match",
            "Stocks, prices, fundamentals, and benchmark through the freeze date must remain unchanged.",
        )
    membership_required = bool(
        config.get("strategy", {}).get("benchmark_membership", {}).get("enabled", False)
        or manifest.get("index_membership_sha256_at_freeze")
    )
    if membership_required:
        add(
            "candidate_index_membership_evidence",
            membership_ok,
            f"rows={current_rows};hash={current_membership_hash[:16]}",
            f"rows={frozen_rows};hash={frozen_membership_hash[:16]}",
            "Frozen point-in-time index membership through the freeze date must remain unchanged.",
        )
    stability_ok = True
    forward_only = str(manifest.get("freeze_mode", "")) == "forward_only_preregistration"
    require_stability = bool(
        config.get("live_guard", {}).get("require_candidate_stability", False)
        and not forward_only
    )
    if require_stability:
        stability_rows = int(manifest.get("candidate_stability_rows", 0) or 0)
        stability_hash = str(manifest.get("candidate_stability_sha256", ""))
        stability_present = stability_rows > 0 and bool(stability_hash)
        stability_passed = bool(manifest.get("candidate_stability_passed_at_freeze", False))
        stability_ok = stability_present and stability_passed
        add(
            "candidate_stability_evidence_at_freeze",
            stability_present,
            stability_rows,
            "non-empty frozen stability hash",
            "冻结候选必须包含参数邻域稳定性报告指纹。",
        )
        add(
            "candidate_stability_passed_at_freeze",
            stability_passed,
            stability_passed,
            True,
            "冻结时参数邻域稳定性、研究数据一致性和产物指纹必须全部通过。",
        )

    if forward_only:
        boundary_ok, freeze_boundary, known_boundary = candidate_freeze_boundary_evidence(
            manifest
        )
        add(
            "candidate_freeze_boundary_evidence",
            boundary_ok,
            f"freeze={freeze_boundary};known={known_boundary}",
            "schema>=2 and freeze>=known data end",
            "前向冻结边界必须覆盖登记时已经可见的全部行情和基准数据。",
        )
        add(
            "candidate_forward_only_preregistration",
            True,
            manifest.get("freeze_mode", ""),
            "forward_only_preregistration",
            "结构性前向候选不使用历史参数邻域结果晋级，只接受冻结后的不可改写影子证据。",
        )
        observation = (
            candidate_shadow_observation(
                shadow_audit,
                manifest.get("candidate_id", ""),
                manifest.get("freeze_price_end"),
            )
            if boundary_ok
            else None
        )
        required_days = int(
            manifest.get(
                "required_forward_trading_days",
                config.get("live_guard", {}).get("min_forward_trading_days", 60),
            )
        )
        min_excess = float(
            manifest.get(
                "required_forward_excess_return",
                config.get("live_guard", {}).get("min_forward_excess_return", 0.0),
            )
        )
        days = int(observation["days"]) if observation else 0
        excess_return: float | str = observation["excess_return"] if observation else "not_available"
        days_ok = days >= required_days
        excess_ok = bool(
            days_ok
            and observation is not None
            and float(observation["excess_return"]) >= min_excess
        )
        add(
            "candidate_forward_trading_days",
            days_ok,
            days,
            required_days,
            "前向候选只统计冻结后不可改写影子信号的真实可观测交易日。",
        )
        add(
            "candidate_forward_excess_return",
            excess_ok,
            excess_return,
            min_excess,
            "前向超额来自同一冻结影子信号的策略与基准累计收益。",
        )
        risk = candidate_forward_risk_state(config, manifest, observation, days_ok)
        add(
            "candidate_forward_active_max_drawdown",
            bool(risk["drawdown_ok"]),
            risk["active_max_drawdown"],
            risk["max_active_drawdown"],
            "满观察期后，主动净值从历史高点的最大回撤必须受控。",
        )
        add(
            "candidate_forward_tracking_error",
            bool(risk["tracking_ok"]),
            risk["tracking_error"],
            risk["max_tracking_error"],
            "满观察期后，策略相对基准的年化跟踪误差不得超过冻结门槛。",
        )
        add(
            "candidate_forward_information_ratio",
            bool(risk["information_ratio_ok"]),
            risk["information_ratio"],
            risk["min_information_ratio"],
            "满观察期后，前向主动收益的信息比率必须达到冻结门槛。",
        )
        early_degraded, early_days, early_threshold = candidate_early_forward_degraded(
            config, manifest, days, excess_return
        )
        early_warning, warning_threshold = candidate_early_forward_warning(
            config, manifest, days, excess_return
        )
        add(
            "candidate_early_forward_degradation",
            not early_degraded,
            excess_return if days >= early_days else f"collecting:{days}/{early_days}",
            f">= {early_threshold} after {early_days} trading days",
            "前向候选达到最小观察期后若严重退化，立即暂停。",
        )
        add(
            "candidate_early_forward_warning_clear",
            not early_warning,
            excess_return if days >= early_days else f"collecting:{days}/{early_days}",
            f">= {warning_threshold} after {early_days} trading days",
            "前向候选进入预警带后保持冻结观察，不允许据此调参。",
        )
        if not forward_risk_policy_ok:
            status = "forward_risk_evaluation_policy_mismatch"
        elif not shadow_policy_ok:
            status = "shadow_evaluation_policy_mismatch"
        elif not mapping_ok:
            status = "benchmark_index_mapping_mismatch"
        elif not hash_match:
            status = "config_mismatch"
        elif not boundary_ok:
            status = "unsafe_freeze_boundary"
        elif not membership_ok:
            status = "index_membership_evidence_mismatch"
        elif not frozen_input_ok:
            status = "frozen_input_evidence_mismatch"
        elif early_degraded:
            status = "early_forward_degradation"
        elif early_warning:
            status = "early_forward_warning"
        elif not days_ok:
            status = "collecting_forward_data" if observation else "waiting_for_post_freeze_market_data"
        elif not excess_ok:
            status = "failed_forward_excess"
        elif not bool(risk["risk_ok"]):
            status = "failed_forward_risk"
        else:
            status = "validated"
        add(
            "forward_validation_status",
            status == "validated",
            status,
            "validated",
            "结构性前向候选只能由冻结后的不可改写影子观察完成验证。",
        )
        return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)

    if comparison.empty or not {"date", "strategy_nav", "benchmark_nav"}.issubset(comparison.columns):
        add("candidate_forward_trading_days", False, 0, int(manifest.get("required_forward_trading_days", 60)), "缺少策略与基准对比净值，无法做冻结后前向验证。")
        add("candidate_forward_excess_return", False, "not_available", float(manifest.get("required_forward_excess_return", 0.0)), "缺少策略与基准对比净值，无法计算冻结后前向超额。")
        add("forward_validation_status", False, "missing_comparison", "validated", "请先运行 backtest/report 生成 benchmark_comparison.csv。")
        return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)

    data = comparison.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    freeze_date = pd.to_datetime(manifest.get("freeze_price_end"), errors="coerce")
    if pd.isna(freeze_date):
        add("candidate_forward_trading_days", False, 0, int(manifest.get("required_forward_trading_days", 60)), "冻结日期无效，无法做前向验证。")
        add("candidate_forward_excess_return", False, "not_available", float(manifest.get("required_forward_excess_return", 0.0)), "冻结日期无效，无法计算前向超额。")
        add("forward_validation_status", False, "invalid_freeze_date", "validated", "冻结清单需要有效的 freeze_price_end。")
        return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)

    forward = data[data["date"] > freeze_date].copy()
    required_days = int(manifest.get("required_forward_trading_days", config.get("live_guard", {}).get("min_forward_trading_days", 60)))
    min_excess = float(manifest.get("required_forward_excess_return", config.get("live_guard", {}).get("min_forward_excess_return", 0.0)))
    days = int(len(forward))
    days_ok = days >= required_days
    add("candidate_forward_trading_days", days_ok, days, required_days, "冻结参数后新增交易日必须达到门槛，才算真正前向验证。")

    excess_return: float | str = "not_available"
    excess_ok = False
    if len(forward) >= 2:
        strategy_nav = pd.to_numeric(forward["strategy_nav"], errors="coerce").dropna()
        benchmark_nav = pd.to_numeric(forward["benchmark_nav"], errors="coerce").dropna()
        if len(strategy_nav) >= 2 and len(benchmark_nav) >= 2 and strategy_nav.iloc[0] and benchmark_nav.iloc[0]:
            strategy_return = float(strategy_nav.iloc[-1] / strategy_nav.iloc[0] - 1)
            benchmark_return = float(benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1)
            excess_return = strategy_return - benchmark_return
            excess_ok = days_ok and excess_return >= min_excess
            risk_observation = forward_risk_metrics_from_nav(strategy_nav, benchmark_nav)
    add("candidate_forward_excess_return", excess_ok, excess_return, min_excess, "冻结参数后的前向超额收益必须达到门槛。")
    risk = candidate_forward_risk_state(
        config, manifest, locals().get("risk_observation"), days_ok
    )
    add("candidate_forward_active_max_drawdown", bool(risk["drawdown_ok"]), risk["active_max_drawdown"], risk["max_active_drawdown"], "满观察期后主动最大回撤必须受控。")
    add("candidate_forward_tracking_error", bool(risk["tracking_ok"]), risk["tracking_error"], risk["max_tracking_error"], "满观察期后年化跟踪误差必须受控。")
    add("candidate_forward_information_ratio", bool(risk["information_ratio_ok"]), risk["information_ratio"], risk["min_information_ratio"], "满观察期后信息比率必须达到门槛。")
    early_degraded, early_days, early_threshold = candidate_early_forward_degraded(config, manifest, days, excess_return)
    early_warning, warning_threshold = candidate_early_forward_warning(config, manifest, days, excess_return)
    add(
        "candidate_early_forward_degradation",
        not early_degraded,
        excess_return if days >= early_days else f"collecting:{days}/{early_days}",
        f">= {early_threshold} after {early_days} trading days",
        "冻结候选在最小早期观察期后若出现严重超额回撤，应立即暂停，而不是等待完整验证期结束。",
    )
    add(
        "candidate_early_forward_warning_clear",
        not early_warning,
        excess_return if days >= early_days else f"collecting:{days}/{early_days}",
        f">= {warning_threshold} after {early_days} trading days",
        "早期前向超额已进入硬停线之前的预警带；该提示只收紧观察，不用于重新调参。",
    )

    if not hash_match:
        status = "config_mismatch"
    elif not membership_ok:
        status = "index_membership_evidence_mismatch"
    elif not frozen_input_ok:
        status = "frozen_input_evidence_mismatch"
    elif not stability_ok:
        status = "candidate_stability_failed"
    elif early_degraded:
        status = "early_forward_degradation"
    elif early_warning:
        status = "early_forward_warning"
    elif not days_ok:
        status = "collecting_forward_data"
    elif not excess_ok:
        status = "failed_forward_excess"
    elif not bool(risk["risk_ok"]):
        status = "failed_forward_risk"
    else:
        status = "validated"
    add("forward_validation_status", status == "validated", status, "validated", "冻结后前向验证状态。")
    return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)


def build_candidate_forward_progress(
    config: dict[str, Any],
    comparison: pd.DataFrame,
    shadow_audit: pd.DataFrame | None = None,
) -> pd.DataFrame:
    stability = load_candidate_stability(config)
    stability_ok = candidate_stability_passed(stability)
    manifest = load_candidate_manifest(config)
    if not manifest:
        return pd.DataFrame(
            [
                {
                    "candidate_id": "",
                    "status": "missing_candidate",
                    "freeze_price_end": "",
                    "latest_comparison_date": "",
                    "forward_start": "",
                    "forward_end": "",
                    "pending_signal_count": 0,
                    "latest_pending_signal_date": "",
                    "next_pending_execution_date": "",
                    "first_attribution_requires_close_after": "",
                    "observed_forward_trading_days": 0,
                    "required_forward_trading_days": int(config.get("live_guard", {}).get("min_forward_trading_days", 60)),
                    "remaining_forward_trading_days": int(config.get("live_guard", {}).get("min_forward_trading_days", 60)),
                    "progress_ratio": 0.0,
                    "forward_strategy_return": "",
                    "forward_benchmark_return": "",
                    "forward_excess_return": "",
                    "required_forward_excess_return": float(config.get("live_guard", {}).get("min_forward_excess_return", 0.0)),
                    "forward_active_max_drawdown": "",
                    "max_forward_active_drawdown": float(config.get("live_guard", {}).get("max_forward_active_drawdown", 0.10)),
                    "forward_tracking_error": "",
                    "max_forward_tracking_error": float(config.get("live_guard", {}).get("max_forward_tracking_error", 0.20)),
                    "forward_information_ratio": "",
                    "min_forward_information_ratio": float(config.get("live_guard", {}).get("min_forward_information_ratio", 0.0)),
                    "config_hash_match": False,
                    "optimized_on_current_cache_at_freeze": bool(config.get("validation", {}).get("optimized_on_current_cache", False)),
        "candidate_stability_rows": int(len(stability)),
        "candidate_stability_sha256": frame_sha256(stability) if not stability.empty else "",
        "candidate_stability_passed_at_freeze": stability_ok,
                    "overfit_status": "not_frozen",
                    "next_action": "先运行 freeze-candidate 固定当前候选参数，再等待新增行情做前向验证。",
                }
            ],
            columns=FORWARD_PROGRESS_COLUMNS,
        )

    required_days = int(manifest.get("required_forward_trading_days", config.get("live_guard", {}).get("min_forward_trading_days", 60)))
    min_excess = float(manifest.get("required_forward_excess_return", config.get("live_guard", {}).get("min_forward_excess_return", 0.0)))
    current_hash = live_strategy_hash(config)
    frozen_hash = str(manifest.get("strategy_hash", ""))
    hash_match = bool(frozen_hash and frozen_hash == current_hash)
    optimized_at_freeze = bool(manifest.get("optimized_on_current_cache_at_freeze", False))
    forward_only = str(manifest.get("freeze_mode", "")) == "forward_only_preregistration"
    boundary_ok, _, _ = candidate_freeze_boundary_evidence(manifest)
    freeze_date = pd.to_datetime(manifest.get("freeze_price_end"), errors="coerce")
    pending_signal_count = 0
    latest_pending_signal_date = ""
    next_pending_execution_date = ""
    first_attribution_requires_close_after = ""
    if (
        forward_only
        and boundary_ok
        and shadow_audit is not None
        and not shadow_audit.empty
    ):
        pending_rows = shadow_audit.copy()
        candidate_id = str(manifest.get("candidate_id", "") or "")
        if "candidate_id" in pending_rows.columns:
            pending_rows = pending_rows[
                pending_rows["candidate_id"].fillna("").astype(str) == candidate_id
            ]
        execution_dates = pd.to_datetime(
            pending_rows.get("execution_date"), errors="coerce"
        )
        statuses = pending_rows.get(
            "status", pd.Series("", index=pending_rows.index)
        ).fillna("").astype(str).str.lower()
        pending = statuses.eq("not_observable") & execution_dates.gt(freeze_date)
        pending_signal_count = int(pending.sum())
        if pending_signal_count:
            signal_dates = pd.to_datetime(
                pending_rows.get("signal_date"), errors="coerce"
            )
            latest_signal = signal_dates[pending].max()
            next_execution = execution_dates[pending].min()
            latest_pending_signal_date = (
                "" if pd.isna(latest_signal) else latest_signal.date().isoformat()
            )
            next_pending_execution_date = (
                "" if pd.isna(next_execution) else next_execution.date().isoformat()
            )
            first_attribution_requires_close_after = next_pending_execution_date
    observation = (
        candidate_shadow_observation(
            shadow_audit,
            manifest.get("candidate_id", ""),
            manifest.get("freeze_price_end"),
        )
        if not forward_only or boundary_ok
        else None
    )
    if comparison.empty or not {"date", "strategy_nav", "benchmark_nav"}.issubset(comparison.columns):
        latest_date = ""
        forward = pd.DataFrame()
    else:
        data = comparison.copy()
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        data = data.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        latest_date = "" if data.empty else data["date"].max().date().isoformat()
        forward = data[data["date"] > freeze_date].copy() if not pd.isna(freeze_date) else pd.DataFrame()
    if forward_only and not boundary_ok:
        forward = pd.DataFrame()

    days = int(observation["days"]) if observation else int(len(forward))
    remaining = max(required_days - days, 0)
    progress_ratio = 1.0 if required_days <= 0 else min(days / required_days, 1.0)
    strategy_return: float | str = observation["strategy_return"] if observation else ""
    benchmark_return: float | str = observation["benchmark_return"] if observation else ""
    excess_return: float | str = observation["excess_return"] if observation else ""
    risk_observation = observation
    if observation:
        latest_date = observation["latest_date"]
        excess_ok = float(observation["excess_return"]) >= min_excess
    elif len(forward) >= 2:
        excess_ok = False
        strategy_nav = pd.to_numeric(forward["strategy_nav"], errors="coerce")
        benchmark_nav = pd.to_numeric(forward["benchmark_nav"], errors="coerce")
        valid = pd.DataFrame({"strategy_nav": strategy_nav, "benchmark_nav": benchmark_nav}).dropna()
        if len(valid) >= 2 and valid["strategy_nav"].iloc[0] and valid["benchmark_nav"].iloc[0]:
            strategy_return = float(valid["strategy_nav"].iloc[-1] / valid["strategy_nav"].iloc[0] - 1)
            benchmark_return = float(valid["benchmark_nav"].iloc[-1] / valid["benchmark_nav"].iloc[0] - 1)
            excess_return = strategy_return - benchmark_return
            excess_ok = excess_return >= min_excess
            risk_observation = forward_risk_metrics_from_nav(strategy_nav, benchmark_nav)

    else:
        excess_ok = False
    days_ok = days >= required_days
    risk = candidate_forward_risk_state(config, manifest, risk_observation, days_ok)
    early_degraded, early_days, early_threshold = candidate_early_forward_degraded(config, manifest, days, excess_return)
    early_warning, warning_threshold = candidate_early_forward_warning(config, manifest, days, excess_return)
    if not hash_match:
        status = "config_mismatch"
        overfit_status = "invalid_candidate"
        next_action = "当前配置与冻结候选参数不一致；若是有意修改策略，应重新 freeze-candidate 并重新累计前向验证。"
    elif forward_only and not boundary_ok:
        status = "unsafe_freeze_boundary"
        overfit_status = "invalid_candidate"
        next_action = "旧候选冻结边界未覆盖登记时已知数据；重新登记候选并从新数据开始累计，禁止迁移旧前向天数。"
    elif pd.isna(freeze_date):
        status = "invalid_freeze_date"
        overfit_status = "invalid_candidate"
        next_action = "冻结清单缺少有效 freeze_price_end；重新冻结候选参数。"
    elif early_degraded:
        status = "early_forward_degradation"
        overfit_status = "early_failure"
        next_action = f"早期前向超额低于 {early_threshold:.2%} 风险线；暂停该候选的 paper/RPA，继续观察或重新研究，不得晋级。"
    elif early_warning:
        status = "early_forward_warning"
        overfit_status = "warning"
        next_action = f"早期前向超额低于 {warning_threshold:.2%} 预警线但尚未触发 {early_threshold:.2%} 硬停线；保持冻结参数观察，不得据此调参或晋级。"
    elif not days_ok:
        status = (
            "waiting_for_post_freeze_market_data"
            if forward_only and days == 0
            else "collecting_forward_data"
        )
        overfit_status = "not_cleared" if optimized_at_freeze else "collecting"
        if forward_only and days == 0 and pending_signal_count:
            next_action = (
                f"已记录 {pending_signal_count} 个冻结后 Shadow 信号，"
                f"最早执行日为 {next_pending_execution_date or '待定'}；"
                f"首日收益只能在 {first_attribution_requires_close_after or '该执行日'} "
                "之后的首个完整交易日收盘后归因。"
            )
        else:
            next_action = f"继续使用冻结参数观察新增行情，还需 {remaining} 个交易日达到前向验证天数门槛。"
    elif not excess_ok:
        status = "failed_forward_excess"
        overfit_status = "failed"
        next_action = "冻结后前向超额未达标；不要把当前参数升级为严格实盘级，需复盘策略或延长观察。"
    elif not bool(risk["risk_ok"]):
        status = "failed_forward_risk"
        overfit_status = "failed"
        next_action = "前向期末超额虽可能达标，但主动回撤、跟踪误差或信息比率未通过冻结门槛；候选不得晋级。"
    else:
        status = "validated"
        overfit_status = "cleared" if optimized_at_freeze else "validated"
        next_action = "前向验证通过；仍需结合 shadow、成本压力和执行门禁再判断是否可实盘。"

    return pd.DataFrame(
        [
            {
                "candidate_id": manifest.get("candidate_id", ""),
                "status": status,
                "freeze_price_end": manifest.get("freeze_price_end", ""),
                "latest_comparison_date": latest_date,
                "forward_start": (
                    observation["forward_start"]
                    if observation
                    else "" if forward.empty else forward["date"].min().date().isoformat()
                ),
                "forward_end": (
                    observation["forward_end"]
                    if observation
                    else "" if forward.empty else forward["date"].max().date().isoformat()
                ),
                "pending_signal_count": pending_signal_count,
                "latest_pending_signal_date": latest_pending_signal_date,
                "next_pending_execution_date": next_pending_execution_date,
                "first_attribution_requires_close_after": first_attribution_requires_close_after,
                "observed_forward_trading_days": days,
                "required_forward_trading_days": required_days,
                "remaining_forward_trading_days": remaining,
                "progress_ratio": round(progress_ratio, 4),
                "forward_strategy_return": strategy_return,
                "forward_benchmark_return": benchmark_return,
                "forward_excess_return": excess_return,
                "required_forward_excess_return": min_excess,
                "forward_active_max_drawdown": risk["active_max_drawdown"],
                "max_forward_active_drawdown": risk["max_active_drawdown"],
                "forward_tracking_error": risk["tracking_error"],
                "max_forward_tracking_error": risk["max_tracking_error"],
                "forward_information_ratio": risk["information_ratio"],
                "min_forward_information_ratio": risk["min_information_ratio"],
                "config_hash_match": hash_match,
                "optimized_on_current_cache_at_freeze": optimized_at_freeze,
                "overfit_status": overfit_status,
                "next_action": next_action,
            }
        ],
        columns=FORWARD_PROGRESS_COLUMNS,
    )


def candidate_early_forward_degraded(
    config: dict[str, Any],
    manifest: dict[str, Any],
    days: int,
    excess_return: float | str,
) -> tuple[bool, int, float]:
    guard = config.get("live_guard", {}) if isinstance(config.get("live_guard"), dict) else {}
    min_days = int(manifest.get("min_early_forward_trading_days", guard.get("min_early_forward_trading_days", 5)))
    threshold = float(manifest.get("min_early_forward_excess_return", guard.get("min_early_forward_excess_return", -0.05)))
    numeric = pd.to_numeric(excess_return, errors="coerce")
    degraded = days >= max(min_days, 1) and not pd.isna(numeric) and float(numeric) < threshold
    return bool(degraded), max(min_days, 1), threshold


def candidate_early_forward_warning(
    config: dict[str, Any],
    manifest: dict[str, Any],
    days: int,
    excess_return: float | str,
) -> tuple[bool, float]:
    guard = config.get("live_guard", {}) if isinstance(config.get("live_guard"), dict) else {}
    degraded, min_days, stop_threshold = candidate_early_forward_degraded(config, manifest, days, excess_return)
    ratio = float(manifest.get("early_forward_warning_ratio", guard.get("early_forward_warning_ratio", 0.60)))
    ratio = min(max(ratio, 0.0), 1.0)
    warning_threshold = stop_threshold * ratio
    numeric = pd.to_numeric(excess_return, errors="coerce")
    warning = days >= min_days and not degraded and not pd.isna(numeric) and float(numeric) < warning_threshold
    return bool(warning), warning_threshold


def candidate_validation_passed(validation: pd.DataFrame) -> bool:
    if validation.empty or "check" not in validation:
        return False
    status = validation[validation["check"] == "forward_validation_status"]
    if status.empty:
        return False
    return bool(status["passed"].astype(bool).iloc[0])
