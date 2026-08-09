from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .io_utils import atomic_to_csv

try:  # pragma: no cover - fallback only matters on runtimes without tzdata.
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


AUTO_LIVE_WINDOW_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
DEFAULT_AUTO_LIVE_WINDOWS = ["09:25-11:30", "13:00-14:57"]
DEFAULT_AUTO_LIVE_WEEKDAYS = [0, 1, 2, 3, 4]
DEFAULT_AUTO_LIVE_TIMEZONE = "Asia/Shanghai"


def write_auto_live_window_audit(
    config: dict[str, Any],
    tickets: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    if tickets is None:
        tickets = read_csv(output_dir / "rpa_orders.csv")
    audit = build_auto_live_window_audit(config, tickets, now=now)
    path = output_dir / "auto_live_window_audit.csv"
    atomic_to_csv(audit, path, index=False, encoding="utf-8-sig")
    return path


def build_auto_live_window_audit(config: dict[str, Any], tickets: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    window_cfg = auto.get("trading_window", {}) if isinstance(auto.get("trading_window", {}), dict) else {}
    clock = resolve_clock(config, now)
    windows, window_errors = parse_trading_windows(window_cfg.get("windows", DEFAULT_AUTO_LIVE_WINDOWS))
    weekdays, weekday_errors = parse_weekdays(window_cfg.get("weekdays", DEFAULT_AUTO_LIVE_WEEKDAYS))
    allow_outside_window = bool(auto.get("allow_outside_trading_window", False))
    allow_stale_tickets = bool(auto.get("allow_stale_tickets", False))
    max_ticket_age_days = max(parse_int(auto.get("max_ticket_age_days", 0), 0), 0)

    ticket_dates = parsed_ticket_dates(tickets)
    unique_dates = sorted(set(ticket_dates))
    today = clock.date()
    date_ages = [(today - item).days for item in ticket_dates]
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "next_action": "" if passed else next_action,
                "message": message,
            }
        )

    config_valid = bool(windows) and not window_errors and bool(weekdays) and not weekday_errors
    add(
        "submit_clock_timezone",
        True,
        clock.isoformat(timespec="seconds"),
        window_timezone_name(config),
        "auto_blocker",
        "",
        "提交时间窗按配置时区计算。默认使用 Asia/Shanghai。",
    )
    add(
        "trading_window_config_valid",
        config_valid,
        window_config_value(windows, weekdays, window_errors, weekday_errors),
        "valid weekdays and HH:MM-HH:MM windows",
        "auto_blocker",
        "修正 auto_live.trading_window.weekdays/windows 配置。",
        "提交前必须能解析交易日和交易时段配置。",
    )

    weekday_ok = config_valid and clock.weekday() in weekdays
    add(
        "within_trading_weekday",
        allow_outside_window or weekday_ok,
        f"weekday={clock.weekday()};override={allow_outside_window}",
        "weekday in configured weekdays",
        "auto_blocker",
        "只在 A 股交易日提交，或显式设置 auto_live.allow_outside_trading_window=true 用于演练。",
        "真实自动提交默认只允许配置的交易日。",
    )

    window_ok = config_valid and within_any_window(clock.time(), windows)
    add(
        "within_trading_window",
        allow_outside_window or window_ok,
        f"time={clock.strftime('%H:%M')};override={allow_outside_window}",
        ",".join(format_window(start, end) for start, end in windows) or "configured windows",
        "auto_blocker",
        "在交易时段内运行 auto-live-run，或显式设置 auto_live.allow_outside_trading_window=true 用于演练。",
        "真实自动提交默认只允许常规竞价/连续竞价时段，避免盘外误提交。",
    )

    add(
        "rpa_tickets_present",
        not tickets.empty,
        len(tickets),
        ">0 ready tickets",
        "auto_blocker",
        "先运行 python -m ashare_quant export-orders 生成当日可执行工单。",
        "自动提交必须基于明确的 RPA 工单。",
    )
    date_column_present = not tickets.empty and "date" in tickets.columns
    add(
        "ticket_date_column_present",
        date_column_present,
        "present" if date_column_present else "missing",
        "rpa_orders.csv date column",
        "auto_blocker",
        "重新导出 RPA 工单，确保包含执行日期。",
        "提交前必须知道订单对应的执行日。",
    )

    parseable = date_column_present and len(ticket_dates) == len(tickets)
    add(
        "ticket_dates_parseable",
        parseable,
        f"parsed={len(ticket_dates)};rows={len(tickets)}",
        "all ticket dates parseable",
        "auto_blocker",
        "修正 rpa_orders.csv 的 date 字段后重新审计。",
        "所有工单日期必须可解析，避免执行旧票据或错误日期票据。",
    )
    single_day = parseable and len(unique_dates) == 1
    add(
        "ticket_date_single_execution_day",
        single_day,
        ",".join(item.isoformat() for item in unique_dates) or "missing",
        "one execution date",
        "auto_blocker",
        "重新生成单一执行日的 RPA 工单。",
        "一次自动提交只允许同一执行日的工单。",
    )

    date_fresh = parseable and bool(date_ages) and all(0 <= age <= max_ticket_age_days for age in date_ages)
    add(
        "ticket_dates_not_stale",
        allow_stale_tickets or date_fresh,
        ticket_date_value(unique_dates, today, max_ticket_age_days, allow_stale_tickets),
        f"ticket date age 0..{max_ticket_age_days} days",
        "auto_blocker",
        "重新运行 python -m ashare_quant run 生成今天执行的工单，或仅演练时设置 auto_live.allow_stale_tickets=true。",
        "真实自动提交默认不允许过期或未来执行日票据。",
    )

    statuses = tickets["status"].astype(str).str.lower() if not tickets.empty and "status" in tickets.columns else pd.Series(dtype=str)
    ready = not tickets.empty and not statuses.empty and bool((statuses == "ready").all())
    status_counts = statuses.value_counts().to_dict() if not statuses.empty else {}
    status_value = ";".join(f"{key}={value}" for key, value in sorted(status_counts.items())) or "missing"
    add(
        "ticket_status_ready",
        ready,
        status_value,
        "all tickets ready",
        "auto_blocker",
        "处理 blocked 工单后重新运行 export-orders。",
        "进入自动提交的工单必须全部为 ready。",
    )
    return pd.DataFrame(rows, columns=AUTO_LIVE_WINDOW_AUDIT_COLUMNS)


def auto_live_submit_window_enforced(config: dict[str, Any], dry_run: bool = False) -> bool:
    if dry_run:
        return False
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    adapter = str(auto.get("broker_adapter", "") or "").strip().lower()
    return adapter == "command"


def auto_live_window_audit_passed(frame: pd.DataFrame) -> bool:
    return not frame.empty and "passed" in frame.columns and bool(frame["passed"].map(parse_bool_like).all())


def auto_live_window_blockers(frame: pd.DataFrame) -> str:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return "auto_live_window_audit_missing"
    failed = frame[~frame["passed"].map(parse_bool_like)]
    return ";".join(failed["check"].astype(str).tolist())


def resolve_clock(config: dict[str, Any], now: datetime | None) -> datetime:
    tz = configured_timezone(config)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def configured_timezone(config: dict[str, Any]) -> timezone:
    name = window_timezone_name(config)
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[misc]
        except Exception:
            pass
    return timezone(timedelta(hours=8), name)


def window_timezone_name(config: dict[str, Any]) -> str:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    window_cfg = auto.get("trading_window", {}) if isinstance(auto.get("trading_window", {}), dict) else {}
    return str(window_cfg.get("timezone", DEFAULT_AUTO_LIVE_TIMEZONE) or DEFAULT_AUTO_LIVE_TIMEZONE)


def parse_trading_windows(raw: object) -> tuple[list[tuple[time, time]], list[str]]:
    if raw is None:
        raw = DEFAULT_AUTO_LIVE_WINDOWS
    items = raw if isinstance(raw, list) else [raw]
    windows: list[tuple[time, time]] = []
    errors: list[str] = []
    for item in items:
        try:
            if isinstance(item, dict):
                start_text = str(item.get("start", ""))
                end_text = str(item.get("end", ""))
            else:
                text = str(item)
                if "-" not in text:
                    raise ValueError("missing '-' separator")
                start_text, end_text = [part.strip() for part in text.split("-", 1)]
            start = parse_hhmm(start_text)
            end = parse_hhmm(end_text)
            if start >= end:
                raise ValueError("start must be before end")
            windows.append((start, end))
        except Exception as exc:  # noqa: BLE001 - convert config issues into audit rows.
            errors.append(f"{item}:{exc}")
    return windows, errors


def parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("expected HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    return time(hour=hour, minute=minute)


def parse_weekdays(raw: object) -> tuple[set[int], list[str]]:
    if raw is None:
        raw = DEFAULT_AUTO_LIVE_WEEKDAYS
    items = raw if isinstance(raw, list) else [raw]
    weekdays: set[int] = set()
    errors: list[str] = []
    names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    for item in items:
        try:
            text = str(item).strip().lower()
            value = names[text[:3]] if text[:3] in names else int(text)
            if value < 0 or value > 6:
                raise ValueError("weekday must be 0..6")
            weekdays.add(value)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{item}:{exc}")
    return weekdays, errors


def within_any_window(value: time, windows: list[tuple[time, time]]) -> bool:
    return any(start <= value <= end for start, end in windows)


def parsed_ticket_dates(tickets: pd.DataFrame) -> list[Any]:
    if tickets.empty or "date" not in tickets.columns:
        return []
    parsed = pd.to_datetime(tickets["date"], errors="coerce")
    return [item.date() for item in parsed.dropna()]


def ticket_date_value(unique_dates: list[Any], today: Any, max_age_days: int, allow_stale_tickets: bool) -> str:
    dates = ",".join(item.isoformat() for item in unique_dates) or "missing"
    return f"dates={dates};today={today.isoformat()};max_age_days={max_age_days};override={allow_stale_tickets}"


def window_config_value(windows: list[tuple[time, time]], weekdays: set[int], window_errors: list[str], weekday_errors: list[str]) -> str:
    parts = [",".join(format_window(start, end) for start, end in windows) or "no_windows", ",".join(str(item) for item in sorted(weekdays)) or "no_weekdays"]
    if window_errors:
        parts.append("window_errors=" + "|".join(window_errors))
    if weekday_errors:
        parts.append("weekday_errors=" + "|".join(weekday_errors))
    return ";".join(parts)


def format_window(start: time, end: time) -> str:
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


def parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def read_csv(path: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()
