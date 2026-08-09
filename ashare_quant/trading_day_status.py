from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .baostock_source import baostock_session, fetch_trade_calendar_frame_baostock
from .config import project_path
from .data import DataStore, TRADE_CALENDAR_COLUMNS, normalize_trade_calendar
from .io_utils import atomic_to_csv


TRADING_DAY_STATUS_COLUMNS = [
    "date",
    "status",
    "is_trading_day",
    "calendar_covered",
    "refresh_attempted",
    "refresh_succeeded",
    "calendar_start",
    "calendar_end",
    "source",
    "message",
    "generated_at",
]


def write_trading_day_status(
    config: dict[str, Any],
    *,
    target: date | None = None,
    refresh_missing: bool = False,
) -> tuple[Path, str]:
    target = target or datetime.now().astimezone().date()
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "trading_day_status.csv"
    store = DataStore.from_config(config)

    calendar = _cached_calendar(store)
    refresh_attempted = False
    refresh_succeeded = False
    refresh_error = ""
    if target.weekday() < 5 and not _calendar_has_date(calendar, target) and refresh_missing:
        refresh_attempted = True
        try:
            with baostock_session() as client:
                fetched = fetch_trade_calendar_frame_baostock(
                    client,
                    target - timedelta(days=45),
                    target,
                )
            calendar = normalize_trade_calendar(pd.concat([calendar, fetched], ignore_index=True))
            store.write_frame("trade_calendar", calendar)
            refresh_succeeded = _calendar_has_date(calendar, target)
        except Exception as exc:
            refresh_error = f"{type(exc).__name__}: {exc}"

    row = _status_row(
        calendar,
        target,
        refresh_attempted=refresh_attempted,
        refresh_succeeded=refresh_succeeded,
        refresh_error=refresh_error,
    )
    atomic_to_csv(
        pd.DataFrame([row], columns=TRADING_DAY_STATUS_COLUMNS),
        path,
        index=False,
        encoding="utf-8-sig",
    )
    return path, str(row["status"])


def _status_row(
    calendar: pd.DataFrame,
    target: date,
    *,
    refresh_attempted: bool,
    refresh_succeeded: bool,
    refresh_error: str,
) -> dict[str, object]:
    start = calendar["date"].min() if not calendar.empty else None
    end = calendar["date"].max() if not calendar.empty else None
    if target.weekday() >= 5:
        status = "closed"
        is_trading_day = False
        covered = True
        source = "weekday_rule"
        message = "Weekend; observation is not scheduled."
    else:
        selected = calendar[calendar["date"] == target] if not calendar.empty else pd.DataFrame()
        covered = len(selected) == 1
        if covered:
            is_trading_day = bool(selected.iloc[-1]["is_trading_day"])
            status = "open" if is_trading_day else "closed"
            source = str(selected.iloc[-1].get("source", "") or "cached_calendar")
            message = "Confirmed trading session." if is_trading_day else "Exchange calendar marks this date closed."
        else:
            is_trading_day = False
            status = "unknown"
            source = "calendar_unavailable"
            message = "Trading calendar does not cover the target date; observation remains stopped."
            if refresh_error:
                message += f" Refresh failed: {refresh_error}"
    return {
        "date": target.isoformat(),
        "status": status,
        "is_trading_day": is_trading_day,
        "calendar_covered": covered,
        "refresh_attempted": refresh_attempted,
        "refresh_succeeded": refresh_succeeded,
        "calendar_start": "" if start is None else start.isoformat(),
        "calendar_end": "" if end is None else end.isoformat(),
        "source": source,
        "message": message,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _cached_calendar(store: DataStore) -> pd.DataFrame:
    try:
        return normalize_trade_calendar(store.read_frame("trade_calendar", required=False))
    except (FileNotFoundError, ValueError, OSError):
        return pd.DataFrame(columns=TRADE_CALENDAR_COLUMNS)


def _calendar_has_date(calendar: pd.DataFrame, target: date) -> bool:
    return not calendar.empty and int((calendar["date"] == target).sum()) == 1
