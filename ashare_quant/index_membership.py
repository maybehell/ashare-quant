from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .index_symbols import canonical_index_symbol
from .baostock_source import baostock_session, normalize_baostock_symbol, result_frame
from .config import project_path
from .data import DataStore, scope_market_frames_to_committed_fetch
from .io_utils import atomic_to_csv
from .rules import monthly_rebalance_dates


MEMBERSHIP_COLUMNS = [
    "snapshot_date",
    "update_date",
    "index_symbol",
    "symbol",
    "name",
    "source",
    "retrieved_at",
]


MEMBERSHIP_QUALITY_COLUMNS = [
    "check",
    "passed",
    "value",
    "threshold",
    "severity",
    "message",
]

MEMBERSHIP_SNAPSHOT_COLUMNS = [
    "snapshot_date",
    "constituent_count",
    "stock_catalog_count",
    "price_history_count",
    "price_history_coverage_ratio",
    "source_count",
    "update_date_missing_count",
    "update_date_after_snapshot_count",
    "benchmark_session",
    "passed",
]


def build_index_membership_quality(
    config: dict[str, Any],
    membership: pd.DataFrame,
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = config.get("strategy", {}).get("benchmark_membership", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return (
            pd.DataFrame(columns=MEMBERSHIP_QUALITY_COLUMNS),
            pd.DataFrame(columns=MEMBERSHIP_SNAPSHOT_COLUMNS),
        )

    expected_index = canonical_index_symbol(
        policy.get("index_symbol", config.get("benchmark", {}).get("symbol", "000300"))
    )
    expected_size = max(int(policy.get("expected_constituent_count", 300)), 1)
    max_staleness = max(int(policy.get("max_staleness_days", 120)), 0)
    min_price_coverage = float(policy.get("min_history_price_coverage_ratio", 1.0))
    min_catalog_coverage = float(policy.get("min_stock_catalog_coverage_ratio", 0.98))
    allowed_sources = {"baostock.query_hs300_stocks"}

    normalized = normalize_index_membership(membership)
    selected = normalized[normalized["index_symbol"].astype(str) == expected_index].copy()
    study_start, study_end = _membership_study_bounds(config, prices, benchmark)
    if study_start is not None:
        selected = selected[selected["snapshot_date"] >= study_start]
    if study_end is not None:
        selected = selected[selected["snapshot_date"] <= study_end]
    selected = selected.sort_values(["snapshot_date", "symbol"]).reset_index(drop=True)

    stock_symbols = (
        set(stocks["symbol"].astype(str))
        if not stocks.empty and "symbol" in stocks
        else set()
    )
    first_price_dates: dict[str, date] = {}
    if not prices.empty and {"date", "symbol"}.issubset(prices.columns):
        price_frame = prices[["date", "symbol"]].copy()
        price_frame["date"] = pd.to_datetime(price_frame["date"], errors="coerce").dt.date
        price_frame = price_frame.dropna(subset=["date"])
        first_price_dates = (
            price_frame.groupby(price_frame["symbol"].astype(str))["date"].min().to_dict()
        )
    benchmark_dates = (
        set(pd.to_datetime(benchmark["date"], errors="coerce").dropna().dt.date)
        if not benchmark.empty and "date" in benchmark
        else set()
    )

    snapshot_rows: list[dict[str, object]] = []
    for snapshot_date, frame in selected.groupby("snapshot_date", sort=True):
        symbols = set(frame["symbol"].astype(str))
        catalog_count = len(symbols & stock_symbols)
        price_count = sum(
            symbol in first_price_dates and first_price_dates[symbol] <= snapshot_date
            for symbol in symbols
        )
        update_dates = pd.to_datetime(frame["update_date"], errors="coerce").dt.date
        missing_updates = int(update_dates.isna().sum())
        future_updates = int(
            sum(value > snapshot_date for value in update_dates.dropna())
        )
        source_count = frame["source"].astype(str).nunique()
        coverage = price_count / len(symbols) if symbols else 0.0
        snapshot_passed = bool(
            len(symbols) == expected_size
            and missing_updates == 0
            and future_updates == 0
            and set(frame["source"].astype(str)) <= allowed_sources
            and snapshot_date in benchmark_dates
            and coverage >= min_price_coverage
        )
        snapshot_rows.append(
            {
                "snapshot_date": snapshot_date,
                "constituent_count": len(symbols),
                "stock_catalog_count": catalog_count,
                "price_history_count": price_count,
                "price_history_coverage_ratio": round(coverage, 6),
                "source_count": source_count,
                "update_date_missing_count": missing_updates,
                "update_date_after_snapshot_count": future_updates,
                "benchmark_session": snapshot_date in benchmark_dates,
                "passed": snapshot_passed,
            }
        )
    snapshots = pd.DataFrame(snapshot_rows, columns=MEMBERSHIP_SNAPSHOT_COLUMNS)

    rows: list[dict[str, object]] = []

    def add(
        check: str,
        passed: bool,
        value: object,
        threshold: object,
        message: str,
        severity: str = "error",
    ) -> bool:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "message": message,
            }
        )
        return bool(passed)

    present = add(
        "index_membership_present",
        not selected.empty,
        f"rows={len(selected)};snapshots={selected['snapshot_date'].nunique() if not selected.empty else 0}",
        "non-empty point-in-time snapshots",
        "启用时点指数股票池后，成分缓存必须存在且覆盖研究区间。",
    )
    identity_ok = add(
        "index_membership_identity",
        present and set(selected["index_symbol"].astype(str)) == {expected_index},
        ",".join(sorted(set(selected["index_symbol"].astype(str)))) or "missing",
        expected_index,
        "成分快照必须全部属于配置声明的规范化指数代码。",
    )
    counts = snapshots["constituent_count"] if not snapshots.empty else pd.Series(dtype=float)
    cardinality_ok = add(
        "index_membership_snapshot_cardinality",
        present and not counts.empty and bool((counts == expected_size).all()),
        f"min={int(counts.min()) if not counts.empty else 0};max={int(counts.max()) if not counts.empty else 0}",
        f"every snapshot = {expected_size}",
        "每个沪深300快照必须恰好包含300个唯一成分，不能用部分成分快照回测。",
    )
    sources = set(selected["source"].astype(str)) if not selected.empty else set()
    source_ok = add(
        "index_membership_source_provenance",
        present and bool(sources) and sources <= allowed_sources,
        ",".join(sorted(sources)) or "missing",
        ",".join(sorted(allowed_sources)),
        "成分快照必须来自已声明的历史时点接口，缺失或未知来源会阻断。",
    )
    missing_updates = int(pd.to_datetime(selected["update_date"], errors="coerce").isna().sum()) if not selected.empty else 0
    future_updates = int(
        sum(
            update > snapshot
            for update, snapshot in zip(
                pd.to_datetime(selected["update_date"], errors="coerce"),
                pd.to_datetime(selected["snapshot_date"], errors="coerce"),
                strict=False,
            )
            if pd.notna(update) and pd.notna(snapshot)
        )
    ) if not selected.empty else 0
    update_ok = add(
        "index_membership_no_future_update",
        present and missing_updates == 0 and future_updates == 0,
        f"missing={missing_updates};after_snapshot={future_updates}",
        "missing=0;after_snapshot=0",
        "成分公告/更新日期不得晚于策略使用的快照日期，防止未来成分泄漏。",
    )
    non_sessions = (
        int((~snapshots["benchmark_session"].map(bool)).sum())
        if not snapshots.empty
        else 0
    )
    calendar_ok = add(
        "index_membership_calendar_alignment",
        present and non_sessions == 0,
        f"non_sessions={non_sessions}",
        "0",
        "每个快照日期必须是基准交易日。",
    )

    snapshot_dates = sorted(selected["snapshot_date"].unique()) if not selected.empty else []
    first_snapshot = snapshot_dates[0] if snapshot_dates else None
    latest_snapshot = snapshot_dates[-1] if snapshot_dates else None
    start_ok = add(
        "index_membership_research_start_covered",
        bool(first_snapshot and study_start and first_snapshot <= study_start),
        f"first={first_snapshot or 'missing'};study_start={study_start or 'missing'}",
        "first snapshot <= study start",
        "首个成分快照必须不晚于研究起点，不能用后来的成员回填早期月份。",
    )
    gaps = [
        (right - left).days
        for left, right in zip(snapshot_dates, snapshot_dates[1:], strict=False)
    ]
    max_gap = max(gaps, default=0)
    gap_ok = add(
        "index_membership_snapshot_gap",
        present and max_gap <= max_staleness,
        max_gap,
        f"<= {max_staleness} days",
        "相邻成分快照间隔不得超过策略允许的最大陈旧天数。",
    )
    latest_staleness = (
        (study_end - latest_snapshot).days
        if study_end is not None and latest_snapshot is not None
        else None
    )
    fresh_ok = add(
        "index_membership_latest_snapshot_fresh",
        latest_staleness is not None and 0 <= latest_staleness <= max_staleness,
        "missing" if latest_staleness is None else latest_staleness,
        f"0..{max_staleness} days",
        "最新策略日期只能使用此前且仍在允许陈旧窗口内的成分快照。",
    )

    union_symbols = set(selected["symbol"].astype(str)) if not selected.empty else set()
    catalog_coverage = len(union_symbols & stock_symbols) / len(union_symbols) if union_symbols else 0.0
    catalog_ok = add(
        "index_membership_stock_catalog_coverage",
        bool(union_symbols) and catalog_coverage >= min_catalog_coverage,
        round(catalog_coverage, 6),
        f">= {min_catalog_coverage:.2f}",
        "历史成分必须在股票目录中有足够覆盖，才能可靠应用板块、上市和风险警示过滤。",
    )
    priced_union = {
        symbol
        for symbol in union_symbols
        if symbol in first_price_dates
        and (study_end is None or first_price_dates[symbol] <= study_end)
    }
    union_price_coverage = len(priced_union) / len(union_symbols) if union_symbols else 0.0
    union_price_ok = add(
        "index_membership_historical_member_price_coverage",
        bool(union_symbols) and union_price_coverage >= min_price_coverage,
        f"priced={len(priced_union)}/{len(union_symbols)};ratio={union_price_coverage:.6f}",
        f">= {min_price_coverage:.2f}",
        "研究期内出现过的历史成分必须有行情；缺失退出指数的老成员会重新引入幸存者偏差。",
    )
    minimum_snapshot_coverage = (
        float(snapshots["price_history_coverage_ratio"].min())
        if not snapshots.empty
        else 0.0
    )
    snapshot_price_ok = add(
        "index_membership_minimum_snapshot_price_coverage",
        not snapshots.empty and minimum_snapshot_coverage >= min_price_coverage,
        round(minimum_snapshot_coverage, 6),
        f">= {min_price_coverage:.2f}",
        "每个时点快照都必须拥有足够的当时可用历史行情，不能只保证最新成分完整。",
    )

    required = [
        present,
        identity_ok,
        cardinality_ok,
        source_ok,
        update_ok,
        calendar_ok,
        start_ok,
        gap_ok,
        fresh_ok,
        catalog_ok,
        union_price_ok,
        snapshot_price_ok,
    ]
    add(
        "point_in_time_membership_ready",
        all(required),
        "ready" if all(required) else "blocked",
        "all required membership checks passed",
        "只有时点成分身份、日期、来源、快照完整性和历史成员行情覆盖全部通过，才可称为时点股票池证据。",
    )
    return pd.DataFrame(rows, columns=MEMBERSHIP_QUALITY_COLUMNS), snapshots


def current_index_membership_quality(
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    store = DataStore.from_config(config)
    stocks = store.read_frame("stocks", required=False)
    prices = store.read_frame("prices", required=False)
    fundamentals = store.read_frame("fundamentals", required=False)
    stocks, prices, _, _, _ = scope_market_frames_to_committed_fetch(
        store,
        stocks,
        prices,
        fundamentals,
    )
    return build_index_membership_quality(
        config,
        load_membership_from_config(config),
        stocks,
        prices,
        store.load_benchmark(required=False),
    )


def index_membership_data_quality_rows(audit: pd.DataFrame) -> pd.DataFrame:
    columns = ["item", "value", "severity", "note"]
    if audit.empty or not {"check", "passed", "value", "message"}.issubset(audit.columns):
        return pd.DataFrame(columns=columns)
    wanted = {
        "index_membership_snapshot_cardinality",
        "index_membership_latest_snapshot_fresh",
        "index_membership_stock_catalog_coverage",
        "index_membership_historical_member_price_coverage",
        "index_membership_minimum_snapshot_price_coverage",
        "point_in_time_membership_ready",
    }
    rows = []
    for row in audit.to_dict(orient="records"):
        check = str(row.get("check", ""))
        if check not in wanted:
            continue
        passed = _truthy(row.get("passed", False))
        rows.append(
            {
                "item": check,
                "value": passed if check == "point_in_time_membership_ready" else row.get("value", ""),
                "severity": "ok" if passed else "warning",
                "note": row.get("message", ""),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def index_membership_quality_warnings(audit: pd.DataFrame) -> list[str]:
    if audit.empty or not {"check", "passed", "message"}.issubset(audit.columns):
        return []
    composite = audit[audit["check"].astype(str) == "point_in_time_membership_ready"]
    if composite.empty or _truthy(composite.iloc[-1].get("passed", False)):
        return []
    failed = audit[
        (~audit["passed"].map(_truthy))
        & (audit["check"].astype(str) != "point_in_time_membership_ready")
    ]
    return [str(value) for value in failed["message"].head(6)]


def _membership_study_bounds(
    config: dict[str, Any],
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> tuple[date | None, date | None]:
    backtest = config.get("backtest", {}) if isinstance(config.get("backtest"), dict) else {}
    configured_start = pd.to_datetime(backtest.get("start"), errors="coerce")
    configured_end = pd.to_datetime(backtest.get("end"), errors="coerce")
    price_dates = (
        pd.to_datetime(prices["date"], errors="coerce").dropna()
        if not prices.empty and "date" in prices
        else pd.Series(dtype="datetime64[ns]")
    )
    benchmark_dates = (
        pd.to_datetime(benchmark["date"], errors="coerce").dropna()
        if not benchmark.empty and "date" in benchmark
        else pd.Series(dtype="datetime64[ns]")
    )
    starts = [value for value in [configured_start, price_dates.min() if not price_dates.empty else pd.NaT, benchmark_dates.min() if not benchmark_dates.empty else pd.NaT] if pd.notna(value)]
    ends = [value for value in [configured_end, price_dates.max() if not price_dates.empty else pd.NaT, benchmark_dates.max() if not benchmark_dates.empty else pd.NaT] if pd.notna(value)]
    return (
        max(starts).date() if starts else None,
        min(ends).date() if ends else None,
    )


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def fetch_hs300_membership_baostock(
    snapshot_dates: Iterable[date],
    *,
    module: Any | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    retrieved_at = datetime.now().isoformat(timespec="seconds")
    with baostock_session(module) as client:
        for snapshot_date in sorted(set(snapshot_dates)):
            raw = result_frame(client.query_hs300_stocks(date=snapshot_date.isoformat()))
            for record in raw.to_dict("records"):
                symbol = normalize_baostock_symbol(record.get("code", ""))
                if not symbol:
                    continue
                rows.append(
                    {
                        "snapshot_date": snapshot_date,
                        "update_date": clean_date(record.get("updateDate", "")),
                        "index_symbol": "000300",
                        "symbol": symbol,
                        "name": str(record.get("code_name", "") or "").strip(),
                        "source": "baostock.query_hs300_stocks",
                        "retrieved_at": retrieved_at,
                    }
                )
    return normalize_index_membership(pd.DataFrame(rows, columns=MEMBERSHIP_COLUMNS))


def refresh_benchmark_membership(
    config: dict[str, Any],
    *,
    start: date | None = None,
    end: date | None = None,
    refresh_all: bool = False,
    module: Any | None = None,
) -> dict[str, Path]:
    benchmark_symbol = canonical_index_symbol(config.get("benchmark", {}).get("symbol", "000300"))
    if benchmark_symbol != "000300":
        raise ValueError("BaoStock point-in-time membership currently supports benchmark 000300 only")
    store = DataStore.from_config(config)
    benchmark = store.load_benchmark(required=True)
    if benchmark.empty:
        raise RuntimeError("Benchmark cache is empty; refresh benchmark data first.")

    configured_start = pd.to_datetime(config.get("backtest", {}).get("start")).date()
    configured_end = config.get("backtest", {}).get("end")
    start_date = start or configured_start
    end_date = end or (
        pd.to_datetime(configured_end).date()
        if configured_end
        else max(benchmark["date"])
    )
    usable_dates = sorted(
        value for value in benchmark["date"].unique()
        if start_date <= value <= end_date
    )
    if not usable_dates:
        raise RuntimeError("No benchmark dates are available in the requested membership interval.")
    requested_dates = sorted(monthly_rebalance_dates(usable_dates))
    if usable_dates[-1] not in requested_dates:
        requested_dates.append(usable_dates[-1])

    existing = normalize_index_membership(store.read_frame("index_membership", required=False))
    existing_dates = set(existing["snapshot_date"]) if not existing.empty else set()
    query_dates = requested_dates if refresh_all else [value for value in requested_dates if value not in existing_dates]
    fresh = (
        fetch_hs300_membership_baostock(query_dates, module=module)
        if query_dates
        else pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
    )
    merged = normalize_index_membership(pd.concat([existing, fresh], ignore_index=True))
    membership_path = store.write_frame("index_membership", merged)

    expected_rows = max(1, len(requested_dates) * 300)
    audit = pd.DataFrame(
        [
            {
                "index_symbol": benchmark_symbol,
                "requested_start": start_date,
                "requested_end": end_date,
                "requested_snapshot_count": len(requested_dates),
                "queried_snapshot_count": len(query_dates),
                "cached_snapshot_count": merged["snapshot_date"].nunique() if not merged.empty else 0,
                "cached_symbol_count": merged["symbol"].nunique() if not merged.empty else 0,
                "cached_rows": len(merged),
                "requested_row_coverage": min(1.0, len(merged[merged["snapshot_date"].isin(requested_dates)]) / expected_rows),
                "latest_snapshot_date": max(merged["snapshot_date"]) if not merged.empty else "",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        ]
    )
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "index_membership_audit.csv"
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    stocks = store.read_frame("stocks", required=False)
    prices = store.read_frame("prices", required=False)
    fundamentals = store.read_frame("fundamentals", required=False)
    scoped_stocks, scoped_prices, _, _, _ = scope_market_frames_to_committed_fetch(
        store, stocks, prices, fundamentals
    )
    quality, snapshots = build_index_membership_quality(
        config, merged, scoped_stocks, scoped_prices, benchmark
    )
    quality_path = output_dir / "index_membership_quality.csv"
    snapshots_path = output_dir / "index_membership_snapshots.csv"
    atomic_to_csv(quality, quality_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(snapshots, snapshots_path, index=False, encoding="utf-8-sig")
    store.log_run(
        "index-membership",
        "ok",
        f"snapshots={audit.iloc[0]['cached_snapshot_count']}, rows={len(merged)}",
    )
    clear_membership_cache()
    return {
        "index_membership": membership_path,
        "index_membership_audit.csv": audit_path,
        "index_membership_quality.csv": quality_path,
        "index_membership_snapshots.csv": snapshots_path,
    }


def filter_by_benchmark_membership(
    stocks: pd.DataFrame,
    asof: date,
    config: dict[str, Any],
) -> pd.DataFrame:
    policy = config.get("strategy", {}).get("benchmark_membership", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return stocks
    index_symbol = canonical_index_symbol(
        policy.get("index_symbol", config.get("benchmark", {}).get("symbol", "000300"))
    )
    membership = load_membership_from_config(config)
    if membership.empty:
        return stocks.iloc[0:0].copy()
    eligible_snapshots = membership[
        (membership["index_symbol"].astype(str) == index_symbol)
        & (membership["snapshot_date"] <= asof)
    ]
    if eligible_snapshots.empty:
        return stocks.iloc[0:0].copy()
    snapshot_date = max(eligible_snapshots["snapshot_date"])
    max_staleness_days = int(policy.get("max_staleness_days", 120))
    if (asof - snapshot_date).days > max_staleness_days:
        return stocks.iloc[0:0].copy()
    symbols = set(
        eligible_snapshots.loc[
            eligible_snapshots["snapshot_date"] == snapshot_date,
            "symbol",
        ].astype(str)
    )
    return stocks[stocks["symbol"].astype(str).isin(symbols)].copy()


def load_membership_from_config(config: dict[str, Any]) -> pd.DataFrame:
    cache_dir = project_path(config.get("paths", {}).get("cache_dir", "data/cache"))
    parquet_path = cache_dir / "index_membership.parquet"
    csv_path = cache_dir / "index_membership.csv"
    path = parquet_path if parquet_path.exists() else csv_path
    if not path.exists():
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
    return _load_membership_cached(str(path.resolve()), path.stat().st_mtime_ns).copy()


@lru_cache(maxsize=8)
def _load_membership_cached(path: str, _mtime_ns: int) -> pd.DataFrame:
    source = Path(path)
    frame = pd.read_parquet(source) if source.suffix.lower() == ".parquet" else pd.read_csv(source)
    return normalize_index_membership(frame)


def clear_membership_cache() -> None:
    _load_membership_cached.cache_clear()


def normalize_index_membership(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
    out = frame.copy()
    for column in MEMBERSHIP_COLUMNS:
        if column not in out:
            out[column] = ""
    out["snapshot_date"] = pd.to_datetime(out["snapshot_date"], errors="coerce").dt.date
    out["update_date"] = pd.to_datetime(out["update_date"], errors="coerce").dt.date
    out["index_symbol"] = out["index_symbol"].map(canonical_index_symbol)
    out["symbol"] = out["symbol"].map(normalize_baostock_symbol)
    out = out.dropna(subset=["snapshot_date"])
    out = out[out["symbol"].astype(str).str.fullmatch(r"\d{6}")]
    return (
        out[MEMBERSHIP_COLUMNS]
        .drop_duplicates(["snapshot_date", "index_symbol", "symbol"], keep="last")
        .sort_values(["snapshot_date", "index_symbol", "symbol"])
        .reset_index(drop=True)
    )


def clean_date(value: object) -> date | pd.NaT:
    parsed = pd.to_datetime(value, errors="coerce")
    return pd.NaT if pd.isna(parsed) else parsed.date()
