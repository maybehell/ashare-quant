from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ashare_quant.data import DataStore, normalize_prices
from ashare_quant.turnover_history import (
    require_turnover_backfill_config,
    run_turnover_backfill,
    write_turnover_audit,
)


def _config(tmp_path: Path) -> dict:
    return {
        "operations": {"config_role": "research_turnover_backfill"},
        "paths": {
            "cache_dir": str(tmp_path / "target"),
            "output_dir": str(tmp_path / "outputs"),
            "state_db": str(tmp_path / "state.sqlite3"),
        },
        "turnover_research": {
            "source_cache_dir": str(tmp_path / "source"),
            "requested_start": "2025-01-01",
            "requested_end": "2025-02-28",
            "request_budget": 1,
            "min_symbol_row_coverage_ratio": 0.95,
            "min_turnover_rows": 20,
        },
        "validation": {
            "research_only": True,
            "apply_active_execution": False,
        },
        "auto_live": {
            "enabled": False,
            "allow_broker_submit": False,
            "emergency_stop": True,
        },
        "data": {
            "history_session_retries": 1,
            "history_session_retry_delay_seconds": 0,
        },
    }


def _source_prices() -> pd.DataFrame:
    dates = list(pd.date_range("2025-01-02", periods=25, freq="B").date)
    rows = []
    for symbol_index, symbol in enumerate(("600000", "600001")):
        for index, day in enumerate(dates):
            close = 10.0 + symbol_index + index * 0.01
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1_000_000.0,
                    "amount": close * 1_000_000.0,
                }
            )
    return normalize_prices(pd.DataFrame(rows))


def _write_source(config: dict, prices: pd.DataFrame | None = None) -> DataStore:
    source = DataStore(
        cache_dir=Path(config["turnover_research"]["source_cache_dir"]),
        state_db=Path(config["paths"]["state_db"]),
    )
    frame = _source_prices() if prices is None else prices
    source.write_frame("prices", frame)
    source.write_frame(
        "fetch_summary",
        pd.DataFrame(
            [{
                "requested_symbols": 2,
                "fetched_symbols": 2,
                "failed_items": 0,
                "stage": "complete",
            }]
        ),
    )
    source.write_frame(
        "fetch_history_provenance",
        pd.DataFrame([{"symbol": "600000"}, {"symbol": "600001"}]),
    )
    return source


@contextmanager
def _session():
    yield object()


def _fetcher(client, symbol, start: date, end: date):
    source = _source_prices()
    frame = source[
        (source["symbol"] == symbol)
        & (source["date"] >= start)
        & (source["date"] <= end)
    ].copy()
    frame["turnover"] = 1.0 if symbol == "600000" else 2.0
    return frame, {
        "symbol": symbol,
        "source": "fake_baostock",
        "turnover_quality": "reported_percent",
    }


def test_config_requires_isolated_research_only_fail_closed_role(tmp_path) -> None:
    config = _config(tmp_path)
    config["operations"]["config_role"] = "observation_orchestrator"
    config["auto_live"]["allow_broker_submit"] = True

    with pytest.raises(RuntimeError) as exc_info:
        require_turnover_backfill_config(config)

    message = str(exc_info.value)
    assert "expected=research_turnover_backfill" in message
    assert "allow_broker_submit must be false" in message


def test_budgeted_backfill_does_not_publish_formal_until_complete(tmp_path) -> None:
    config = _config(tmp_path)
    _write_source(config)

    first = run_turnover_backfill(
        config,
        request_budget=1,
        session_factory=_session,
        fetcher=_fetcher,
    )
    target = DataStore.from_config(config)
    first_progress = target.read_frame("turnover_progress")

    assert first_progress.loc[0, "status"] == "in_progress"
    assert int(first_progress.loc[0, "completed_symbols"]) == 1
    assert "turnover_history" not in first
    assert not (target.cache_dir / "turnover_history.parquet").exists()

    blocked = write_turnover_audit(config)
    blocked_summary = pd.read_csv(blocked["turnover_audit_summary"])
    assert blocked_summary.loc[0, "status"] == "blocked"
    assert bool(blocked_summary.loc[0, "checkpoint_identity_valid"])
    assert int(blocked_summary.loc[0, "available_symbols"]) == 1
    assert int(blocked_summary.loc[0, "passed_symbols"]) == 0
    assert int(blocked_summary.loc[0, "blocked_symbols"]) == 2
    assert int(blocked_summary.loc[0, "failed_symbols"]) == 0
    assert int(blocked_summary.loc[0, "turnover_rows"]) > 0

    second = run_turnover_backfill(
        config,
        request_budget=1,
        session_factory=_session,
        fetcher=_fetcher,
    )
    second_progress = target.read_frame("turnover_progress")
    formal = target.read_frame("turnover_history")

    assert second_progress.loc[0, "status"] == "complete"
    assert int(second_progress.loc[0, "completed_symbols"]) == 2
    assert "turnover_history" in second
    assert set(formal["symbol"]) == {"600000", "600001"}

    audit = write_turnover_audit(config)
    summary = pd.read_csv(audit["turnover_audit_summary"])
    detail = pd.read_csv(audit["turnover_audit_detail"])

    assert summary.loc[0, "status"] == "passed"
    assert bool(summary.loc[0, "progress_complete"])
    assert int(summary.loc[0, "passed_symbols"]) == 2
    assert detail["passed"].all()


def test_resume_clears_stale_failure_for_completed_checkpoint(tmp_path) -> None:
    config = _config(tmp_path)
    _write_source(config)
    run_turnover_backfill(
        config,
        request_budget=1,
        session_factory=_session,
        fetcher=_fetcher,
    )
    target = DataStore.from_config(config)
    completed_symbol = target.read_frame("turnover_provenance").loc[0, "symbol"]
    target.write_frame(
        "turnover_failures",
        pd.DataFrame(
            [{
                "symbol": completed_symbol,
                "stage": "turnover_history",
                "error": "stale interrupted checkpoint failure",
                "attempted_at": "2025-01-01T00:00:00",
            }]
        ),
    )

    run_turnover_backfill(
        config,
        request_budget=1,
        session_factory=_session,
        fetcher=_fetcher,
    )

    progress = target.read_frame("turnover_progress")
    failures = target.read_frame("turnover_failures")
    assert progress.loc[0, "status"] == "complete"
    assert failures.empty
    assert (target.cache_dir / "turnover_history.parquet").exists()


def test_resume_refuses_changed_source_scope(tmp_path) -> None:
    config = _config(tmp_path)
    source = _write_source(config)
    run_turnover_backfill(
        config,
        request_budget=1,
        session_factory=_session,
        fetcher=_fetcher,
    )

    changed = _source_prices()
    changed.loc[0, "close"] += 0.5
    source.write_frame("prices", changed)

    with pytest.raises(RuntimeError, match="source_prices_sha256"):
        run_turnover_backfill(
            config,
            request_budget=1,
            session_factory=_session,
            fetcher=_fetcher,
        )


def test_missing_reported_turnover_remains_failed_without_formal_file(tmp_path) -> None:
    config = _config(tmp_path)
    _write_source(config)

    def unavailable_fetcher(client, symbol, start, end):
        frame, provenance = _fetcher(client, symbol, start, end)
        frame["turnover"] = pd.NA
        provenance["turnover_quality"] = "unavailable"
        return frame, provenance

    paths = run_turnover_backfill(
        config,
        request_budget=1,
        session_factory=_session,
        fetcher=unavailable_fetcher,
    )
    target = DataStore.from_config(config)
    failures = target.read_frame("turnover_failures")
    progress = target.read_frame("turnover_progress")

    assert "turnover_history" not in paths
    assert len(failures) == 1
    assert "not reported_percent" in failures.loc[0, "error"]
    assert int(progress.loc[0, "completed_symbols"]) == 0
