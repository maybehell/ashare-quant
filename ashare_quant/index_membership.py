from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .index_symbols import canonical_index_symbol
from .baostock_source import baostock_session, normalize_baostock_symbol, result_frame
from .config import project_path
from .data import DataStore
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
    audit.to_csv(audit_path, index=False, encoding="utf-8-sig")
    store.log_run(
        "index-membership",
        "ok",
        f"snapshots={audit.iloc[0]['cached_snapshot_count']}, rows={len(merged)}",
    )
    clear_membership_cache()
    return {
        "index_membership": membership_path,
        "index_membership_audit.csv": audit_path,
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
