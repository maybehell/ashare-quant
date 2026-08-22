from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import frame_sha256, mapping_sha256
from .config import project_path
from .data import (
    DataStore,
    committed_fetch_market_scope,
    fetch_baostock_history_batch,
    normalize_prices,
    normalize_symbol,
)


TURNOVER_BACKFILL_ROLE = "research_turnover_backfill"
TURNOVER_SCHEMA_VERSION = 1
TURNOVER_AUDIT_SCHEMA_VERSION = 2
TURNOVER_COLUMNS = ["date", "symbol", "turnover"]
TURNOVER_PROVENANCE_COLUMNS = [
    "symbol",
    "source",
    "status",
    "requested_start",
    "requested_end",
    "first_date",
    "last_date",
    "expected_rows",
    "turnover_rows",
    "coverage_ratio",
    "turnover_quality",
    "source_prices_sha256",
    "source_scope_sha256",
    "config_sha256",
    "completed_at",
]
TURNOVER_FAILURE_COLUMNS = ["symbol", "stage", "error", "attempted_at"]


def require_turnover_backfill_config(config: dict[str, Any]) -> dict[str, Any]:
    operations = config.get("operations", {})
    validation = config.get("validation", {})
    auto_live = config.get("auto_live", {})
    paths = config.get("paths", {})
    policy = config.get("turnover_research", {})
    errors: list[str] = []

    role = str(operations.get("config_role", "") if isinstance(operations, dict) else "")
    if role != TURNOVER_BACKFILL_ROLE:
        errors.append(
            f"config_role={role or 'missing'};expected={TURNOVER_BACKFILL_ROLE}"
        )
    if not isinstance(validation, dict) or not bool(validation.get("research_only", False)):
        errors.append("validation.research_only must be true")
    if isinstance(validation, dict) and bool(validation.get("apply_active_execution", False)):
        errors.append("validation.apply_active_execution must be false")
    if not isinstance(auto_live, dict):
        errors.append("auto_live must be a mapping")
    else:
        if bool(auto_live.get("enabled", False)):
            errors.append("auto_live.enabled must be false")
        if bool(auto_live.get("allow_broker_submit", False)):
            errors.append("auto_live.allow_broker_submit must be false")
        if not bool(auto_live.get("emergency_stop", True)):
            errors.append("auto_live.emergency_stop must be true")
    if not isinstance(policy, dict):
        errors.append("turnover_research must be a mapping")
        policy = {}

    source_cache = project_path(str(policy.get("source_cache_dir", "") or ""))
    target_cache = project_path(
        str(paths.get("cache_dir", "") if isinstance(paths, dict) else "")
    )
    if not str(policy.get("source_cache_dir", "") or "").strip():
        errors.append("turnover_research.source_cache_dir is required")
    if not str(paths.get("cache_dir", "") if isinstance(paths, dict) else "").strip():
        errors.append("paths.cache_dir is required")
    if source_cache.resolve() == target_cache.resolve():
        errors.append("turnover target cache must be isolated from source cache")

    try:
        requested_start = pd.to_datetime(policy.get("requested_start"), errors="raise").date()
        requested_end = pd.to_datetime(policy.get("requested_end"), errors="raise").date()
        if requested_start > requested_end:
            errors.append("turnover requested_start must not exceed requested_end")
    except (TypeError, ValueError):
        requested_start = date.min
        requested_end = date.min
        errors.append("turnover requested_start/requested_end must be valid dates")

    try:
        min_coverage = float(policy.get("min_symbol_row_coverage_ratio", 0.95))
    except (TypeError, ValueError):
        min_coverage = -1.0
    if not 0 < min_coverage <= 1:
        errors.append("turnover min_symbol_row_coverage_ratio must be in (0, 1]")

    try:
        min_rows = int(policy.get("min_turnover_rows", 20))
    except (TypeError, ValueError):
        min_rows = 0
    if min_rows < 20:
        errors.append("turnover min_turnover_rows must be at least 20")

    if errors:
        raise RuntimeError("Invalid turnover backfill configuration: " + "; ".join(errors))
    return {
        "source_cache_dir": source_cache,
        "target_cache_dir": target_cache,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "min_symbol_row_coverage_ratio": min_coverage,
        "min_turnover_rows": min_rows,
        "request_budget": max(0, int(policy.get("request_budget", 25))),
        "config_sha256": turnover_backfill_config_sha256(config),
    }


def turnover_backfill_config_sha256(config: dict[str, Any]) -> str:
    return mapping_sha256(
        {
            "schema_version": TURNOVER_SCHEMA_VERSION,
            "operations": config.get("operations", {}),
            "paths": {
                "cache_dir": config.get("paths", {}).get("cache_dir", ""),
                "output_dir": config.get("paths", {}).get("output_dir", ""),
            },
            "turnover_research": config.get("turnover_research", {}),
        }
    )


def normalize_turnover_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=TURNOVER_COLUMNS)
    missing = set(TURNOVER_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"turnover history missing columns: {sorted(missing)}")
    out = frame[TURNOVER_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["symbol"] = out["symbol"].map(normalize_symbol)
    out["turnover"] = pd.to_numeric(out["turnover"], errors="coerce")
    out = out.dropna(subset=TURNOVER_COLUMNS)
    out = out[out["turnover"] >= 0]
    return (
        out.drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def _source_scope(
    settings: dict[str, Any],
    state_db: Path,
) -> tuple[DataStore, pd.DataFrame, list[str], str, str]:
    source_store = DataStore(
        cache_dir=settings["source_cache_dir"],
        state_db=state_db,
    )
    prices = normalize_prices(source_store.read_frame("prices"))
    scoped, passed, evidence = committed_fetch_market_scope(source_store, prices)
    if not passed:
        raise RuntimeError(
            "Turnover source cache is not a completed committed fetch: " + evidence
        )
    start = settings["requested_start"]
    end = settings["requested_end"]
    scoped = scoped[(scoped["date"] >= start) & (scoped["date"] <= end)].copy()
    if scoped.empty:
        raise RuntimeError("Turnover source cache has no rows in the requested interval")
    scoped = scoped.sort_values(["date", "symbol"]).reset_index(drop=True)
    symbols = sorted(scoped["symbol"].map(normalize_symbol).unique().tolist())
    source_prices_sha256 = frame_sha256(scoped)
    scope_frame = (
        scoped.groupby("symbol")["date"]
        .agg(["min", "max", "count"])
        .reset_index()
        .sort_values("symbol")
        .reset_index(drop=True)
    )
    source_scope_sha256 = frame_sha256(scope_frame)
    return source_store, scoped, symbols, source_prices_sha256, source_scope_sha256


def _read_optional(store: DataStore, name: str) -> pd.DataFrame:
    try:
        value = store.read_frame(name, required=False)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return pd.DataFrame()
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _validate_resume_identity(
    partial: pd.DataFrame,
    provenance: pd.DataFrame,
    source_prices_sha256: str,
    source_scope_sha256: str,
    config_sha256: str,
) -> None:
    if partial.empty and provenance.empty:
        return
    if partial.empty != provenance.empty:
        raise RuntimeError(
            "Turnover checkpoint is incomplete; preserve it for audit and start a new namespace"
        )
    required = {
        "source_prices_sha256",
        "source_scope_sha256",
        "config_sha256",
    }
    if not required.issubset(provenance.columns):
        raise RuntimeError(
            "Turnover checkpoint lacks identity fields; preserve it and start a new namespace"
        )
    expected = {
        "source_prices_sha256": source_prices_sha256,
        "source_scope_sha256": source_scope_sha256,
        "config_sha256": config_sha256,
    }
    for column, value in expected.items():
        observed = set(provenance[column].dropna().astype(str))
        if observed != {value}:
            raise RuntimeError(
                f"Turnover checkpoint identity mismatch: {column}"
            )


def _completed_symbols(partial: pd.DataFrame, provenance: pd.DataFrame) -> set[str]:
    if partial.empty or provenance.empty or "status" not in provenance:
        return set()
    data_symbols = set(partial["symbol"].map(normalize_symbol))
    passed = provenance[provenance["status"].astype(str).eq("complete")]
    return set(passed["symbol"].map(normalize_symbol)) & data_symbols


def _failure_frame(
    existing: pd.DataFrame,
    attempted: list[str],
    new_errors: dict[str, str],
) -> pd.DataFrame:
    attempted_set = {normalize_symbol(value) for value in attempted}
    retained = existing.copy()
    if not retained.empty and "symbol" in retained:
        retained = retained[
            ~retained["symbol"].map(normalize_symbol).isin(attempted_set)
        ].copy()
    now = datetime.now().isoformat(timespec="seconds")
    incoming = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "stage": "turnover_history",
                "error": error,
                "attempted_at": now,
            }
            for symbol, error in sorted(new_errors.items())
        ],
        columns=TURNOVER_FAILURE_COLUMNS,
    )
    merged = pd.concat([retained, incoming], ignore_index=True)
    return merged.reindex(columns=TURNOVER_FAILURE_COLUMNS)


def _provenance_frame(
    existing: pd.DataFrame,
    new_rows: list[dict[str, Any]],
) -> pd.DataFrame:
    incoming = pd.DataFrame(new_rows, columns=TURNOVER_PROVENANCE_COLUMNS)
    merged = pd.concat([existing, incoming], ignore_index=True)
    if merged.empty:
        return pd.DataFrame(columns=TURNOVER_PROVENANCE_COLUMNS)
    merged["symbol"] = merged["symbol"].map(normalize_symbol)
    return (
        merged.drop_duplicates("symbol", keep="last")
        .sort_values("symbol")
        .reset_index(drop=True)
        .reindex(columns=TURNOVER_PROVENANCE_COLUMNS)
    )


def _progress_frame(
    *,
    status: str,
    symbols: list[str],
    completed: set[str],
    attempted: list[str],
    failures: pd.DataFrame,
    settings: dict[str, Any],
    source_prices_sha256: str,
    source_scope_sha256: str,
    formal_sha256: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "schema_version": TURNOVER_SCHEMA_VERSION,
            "status": status,
            "requested_symbols": len(symbols),
            "completed_symbols": len(completed),
            "remaining_symbols": max(0, len(symbols) - len(completed)),
            "attempted_symbols": len(attempted),
            "failed_items": len(failures),
            "requested_start": settings["requested_start"],
            "requested_end": settings["requested_end"],
            "source_prices_sha256": source_prices_sha256,
            "source_scope_sha256": source_scope_sha256,
            "config_sha256": settings["config_sha256"],
            "formal_turnover_sha256": formal_sha256,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }]
    )


def run_turnover_backfill(
    config: dict[str, Any],
    *,
    request_budget: int | None = None,
    session_factory: Any | None = None,
    fetcher: Any | None = None,
) -> dict[str, Path]:
    settings = require_turnover_backfill_config(config)
    target_store = DataStore.from_config(config)
    target_store.init_db()
    _source_store, scoped, symbols, source_hash, scope_hash = _source_scope(
        settings, target_store.state_db
    )

    partial = normalize_turnover_history(
        _read_optional(target_store, "turnover_history_partial")
    )
    provenance = _read_optional(target_store, "turnover_provenance")
    failures = _read_optional(target_store, "turnover_failures").reindex(
        columns=TURNOVER_FAILURE_COLUMNS
    )
    _validate_resume_identity(
        partial,
        provenance,
        source_hash,
        scope_hash,
        settings["config_sha256"],
    )
    completed_before = _completed_symbols(partial, provenance)
    remaining = [symbol for symbol in symbols if symbol not in completed_before]
    budget = settings["request_budget"] if request_budget is None else max(0, request_budget)
    attempted = remaining[:budget]

    source_groups = {
        symbol: frame.sort_values("date").copy()
        for symbol, frame in scoped.groupby("symbol", sort=False)
    }
    fetch_starts = {
        symbol: max(settings["requested_start"], source_groups[symbol]["date"].min())
        for symbol in attempted
    }
    frames: list[pd.DataFrame] = []
    source_provenance: list[dict[str, Any]] = []
    fetch_errors: dict[str, str] = {}
    if attempted:
        frames, source_provenance, fetch_errors = fetch_baostock_history_batch(
            attempted,
            fetch_starts,
            settings["requested_end"],
            config,
            session_factory=session_factory,
            fetcher=fetcher,
        )

    fetched_by_symbol = {
        normalize_symbol(frame["symbol"].iloc[0]): frame
        for frame in frames
        if not frame.empty and "symbol" in frame
    }
    source_provenance_by_symbol = {
        normalize_symbol(row.get("symbol", "")): row
        for row in source_provenance
    }
    accepted_frames: list[pd.DataFrame] = []
    accepted_provenance: list[dict[str, Any]] = []
    validation_errors = dict(fetch_errors)
    completed_at = datetime.now().isoformat(timespec="seconds")
    for symbol in attempted:
        if symbol in validation_errors:
            continue
        frame = fetched_by_symbol.get(symbol)
        provider = source_provenance_by_symbol.get(symbol, {})
        if frame is None or frame.empty:
            validation_errors[symbol] = "turnover source returned no frame"
            continue
        turnover = normalize_turnover_history(frame)
        source_rows = source_groups[symbol]
        expected_dates = set(source_rows["date"])
        turnover = turnover[turnover["date"].isin(expected_dates)].copy()
        matched_dates = set(turnover["date"])
        expected_rows = len(expected_dates)
        turnover_rows = len(matched_dates)
        coverage = turnover_rows / expected_rows if expected_rows else 0.0
        quality = str(provider.get("turnover_quality", "") or "")
        if quality != "reported_percent":
            validation_errors[symbol] = (
                f"turnover quality {quality or 'missing'} is not reported_percent"
            )
            continue
        if turnover_rows < settings["min_turnover_rows"]:
            validation_errors[symbol] = (
                f"turnover rows {turnover_rows} below {settings['min_turnover_rows']}"
            )
            continue
        if coverage < settings["min_symbol_row_coverage_ratio"]:
            validation_errors[symbol] = (
                f"turnover coverage {coverage:.6f} below "
                f"{settings['min_symbol_row_coverage_ratio']:.6f}"
            )
            continue
        accepted_frames.append(turnover)
        accepted_provenance.append(
            {
                "symbol": symbol,
                "source": provider.get("source", ""),
                "status": "complete",
                "requested_start": source_rows["date"].min(),
                "requested_end": source_rows["date"].max(),
                "first_date": turnover["date"].min(),
                "last_date": turnover["date"].max(),
                "expected_rows": expected_rows,
                "turnover_rows": turnover_rows,
                "coverage_ratio": coverage,
                "turnover_quality": quality,
                "source_prices_sha256": source_hash,
                "source_scope_sha256": scope_hash,
                "config_sha256": settings["config_sha256"],
                "completed_at": completed_at,
            }
        )

    if accepted_frames:
        partial = normalize_turnover_history(
            pd.concat([partial, *accepted_frames], ignore_index=True)
        )
    provenance = _provenance_frame(provenance, accepted_provenance)
    failures = _failure_frame(failures, attempted, validation_errors)
    completed = _completed_symbols(partial, provenance)
    if not failures.empty:
        failures = failures[
            ~failures["symbol"].map(normalize_symbol).isin(completed)
        ].reset_index(drop=True)
    status = "complete" if len(completed) == len(symbols) and failures.empty else "in_progress"

    paths: dict[str, Path] = {}
    paths["turnover_provenance"] = target_store.write_frame(
        "turnover_provenance", provenance
    )
    paths["turnover_history_partial"] = target_store.write_frame(
        "turnover_history_partial", partial
    )
    paths["turnover_failures"] = target_store.write_frame(
        "turnover_failures", failures
    )
    formal_sha256 = ""
    if status == "complete":
        formal = partial[partial["symbol"].isin(symbols)].copy()
        formal_sha256 = frame_sha256(formal)
        paths["turnover_history"] = target_store.write_frame(
            "turnover_history", formal
        )
    progress = _progress_frame(
        status=status,
        symbols=symbols,
        completed=completed,
        attempted=attempted,
        failures=failures,
        settings=settings,
        source_prices_sha256=source_hash,
        source_scope_sha256=scope_hash,
        formal_sha256=formal_sha256,
    )
    paths["turnover_progress"] = target_store.write_frame(
        "turnover_progress", progress
    )
    return paths


def write_turnover_audit(config: dict[str, Any]) -> dict[str, Path]:
    settings = require_turnover_backfill_config(config)
    target_store = DataStore.from_config(config)
    _source_store, scoped, symbols, source_hash, scope_hash = _source_scope(
        settings, target_store.state_db
    )
    progress = _read_optional(target_store, "turnover_progress")
    provenance = _read_optional(target_store, "turnover_provenance")
    failures = _read_optional(target_store, "turnover_failures")
    formal_raw = _read_optional(target_store, "turnover_history")
    formal = normalize_turnover_history(formal_raw)
    partial = normalize_turnover_history(
        _read_optional(target_store, "turnover_history_partial")
    )
    checkpoint_identity_valid = bool(
        len(progress) == 1
        and str(progress.iloc[0].get("source_prices_sha256", "")) == source_hash
        and str(progress.iloc[0].get("source_scope_sha256", "")) == scope_hash
        and str(progress.iloc[0].get("config_sha256", ""))
        == settings["config_sha256"]
    )
    progress_complete = bool(
        checkpoint_identity_valid
        and str(progress.iloc[0].get("status", "")) == "complete"
    )
    observed = formal if progress_complete else (
        partial if checkpoint_identity_valid else pd.DataFrame(columns=TURNOVER_COLUMNS)
    )
    provenance_by_symbol = {
        normalize_symbol(row.get("symbol", "")): row
        for row in provenance.to_dict(orient="records")
    }
    observed_groups = {
        symbol: frame
        for symbol, frame in observed.groupby("symbol", sort=False)
    }
    details: list[dict[str, Any]] = []
    for symbol in symbols:
        expected = set(scoped.loc[scoped["symbol"] == symbol, "date"])
        observed = set(
            observed_groups.get(symbol, pd.DataFrame(columns=TURNOVER_COLUMNS)).get(
                "date", pd.Series(dtype=object)
            )
        )
        matched = len(expected & observed)
        coverage = matched / len(expected) if expected else 0.0
        provider = provenance_by_symbol.get(symbol, {})
        provider_identity_valid = bool(
            str(provider.get("source_prices_sha256", "")) == source_hash
            and str(provider.get("source_scope_sha256", "")) == scope_hash
            and str(provider.get("config_sha256", ""))
            == settings["config_sha256"]
        )
        quality = (
            str(provider.get("turnover_quality", "") or "")
            if provider_identity_valid
            else ""
        )
        available = bool(
            checkpoint_identity_valid
            and quality == "reported_percent"
            and matched >= settings["min_turnover_rows"]
            and coverage >= settings["min_symbol_row_coverage_ratio"]
        )
        passed = bool(progress_complete and available)
        details.append(
            {
                "symbol": symbol,
                "expected_rows": len(expected),
                "turnover_rows": matched,
                "coverage_ratio": coverage,
                "turnover_quality": quality or "missing",
                "available": available,
                "passed": passed,
            }
        )
    detail = pd.DataFrame(details)
    available_symbols = int(detail["available"].sum()) if not detail.empty else 0
    passed_symbols = int(detail["passed"].sum()) if not detail.empty else 0
    expected_rows = int(detail["expected_rows"].sum()) if not detail.empty else 0
    turnover_rows = int(detail["turnover_rows"].sum()) if not detail.empty else 0
    formal_sha256 = (
        frame_sha256(formal) if progress_complete and not formal.empty else ""
    )
    failed_symbols = (
        failures["symbol"].map(normalize_symbol).nunique()
        if not failures.empty and "symbol" in failures
        else 0
    )
    overall_passed = bool(progress_complete and passed_symbols == len(symbols))
    summary = pd.DataFrame(
        [{
            "schema_version": TURNOVER_AUDIT_SCHEMA_VERSION,
            "status": "passed" if overall_passed else "blocked",
            "progress_complete": progress_complete,
            "checkpoint_identity_valid": checkpoint_identity_valid,
            "requested_symbols": len(symbols),
            "available_symbols": available_symbols,
            "remaining_symbols": len(symbols) - available_symbols,
            "passed_symbols": passed_symbols,
            "blocked_symbols": len(symbols) - passed_symbols,
            "failed_symbols": failed_symbols,
            "expected_rows": expected_rows,
            "turnover_rows": turnover_rows,
            "overall_row_coverage_ratio": (
                turnover_rows / expected_rows if expected_rows else 0.0
            ),
            "source_prices_sha256": source_hash,
            "source_scope_sha256": scope_hash,
            "config_sha256": settings["config_sha256"],
            "formal_turnover_sha256": formal_sha256,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "message": (
                "turnover_history_ready_for_preregistration_review"
                if overall_passed
                else "turnover_history_incomplete_no_research_authority"
            ),
        }]
    )
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "turnover_coverage_detail": target_store.write_frame(
            "turnover_coverage_detail", detail
        ),
        "turnover_coverage_summary": target_store.write_frame(
            "turnover_coverage_summary", summary
        ),
        "turnover_audit_detail": _write_output_csv(
            detail, output_dir / "turnover_coverage_detail.csv"
        ),
        "turnover_audit_summary": _write_output_csv(
            summary, output_dir / "turnover_coverage_summary.csv"
        ),
    }


def _write_output_csv(frame: pd.DataFrame, path: Path) -> Path:
    from .io_utils import atomic_to_csv

    return atomic_to_csv(frame, path, index=False, encoding="utf-8-sig")
