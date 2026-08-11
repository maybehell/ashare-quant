from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .audit import frame_sha256
from .config import project_path
from .data import DataStore, normalize_symbol
from .io_utils import atomic_to_csv


SURVIVORSHIP_AUDIT_COLUMNS = [
    "item",
    "value",
    "threshold",
    "passed",
    "severity",
    "note",
]

SURVIVORSHIP_SNAPSHOT_COLUMNS = [
    "year",
    "snapshot_date",
    "all_cached_priced_symbols",
    "current_pool_priced_symbols",
    "current_pool_listed_symbols",
    "current_pool_not_yet_listed_symbols",
    "current_pool_priced_share_of_cache",
]


def build_survivorship_bias_audit(
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    committed_symbols: Iterable[object],
    start: object = None,
    end: object = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized_prices = _normalized_price_presence(prices, start, end)
    cached_symbols = set(normalized_prices["symbol"]) if not normalized_prices.empty else set()
    current_pool = {normalize_symbol(value) for value in committed_symbols}
    current_pool.discard("")
    committed_source = "fetch_history_provenance"
    if not current_pool:
        current_pool = set(cached_symbols)
        committed_source = "fallback_all_cached_symbols"

    stock_dates = _stock_list_dates(stocks)
    known_list_dates = {symbol: date for symbol, date in stock_dates.items() if pd.notna(date)}
    placeholder_count = sum(date == pd.Timestamp("2000-01-01") for date in known_list_dates.values())
    known_current_dates = {symbol: known_list_dates[symbol] for symbol in current_pool if symbol in known_list_dates}
    unknown_current_dates = len(current_pool) - len(known_current_dates)
    placeholder_current = sum(date == pd.Timestamp("2000-01-01") for date in known_current_dates.values())
    unreliable_current_dates = unknown_current_dates + placeholder_current

    study_start = normalized_prices["date"].min() if not normalized_prices.empty else pd.NaT
    study_end = normalized_prices["date"].max() if not normalized_prices.empty else pd.NaT
    cached_count = len(cached_symbols)
    pool_count = len(current_pool)
    excluded_count = len(cached_symbols - current_pool)
    pool_share = pool_count / cached_count if cached_count else 0.0
    unreliable_ratio = unreliable_current_dates / pool_count if pool_count else 1.0

    snapshots = _annual_snapshots(normalized_prices, current_pool, known_current_dates)
    earliest_share = (
        float(snapshots.iloc[0]["current_pool_priced_share_of_cache"])
        if not snapshots.empty
        else 0.0
    )

    rows: list[dict[str, object]] = []

    def add(
        item: str,
        value: object,
        threshold: object,
        passed: bool,
        severity: str,
        note: str,
    ) -> None:
        rows.append(
            {
                "item": item,
                "value": value,
                "threshold": threshold,
                "passed": bool(passed),
                "severity": severity,
                "note": note,
            }
        )

    add(
        "point_in_time_universe_available",
        False,
        True,
        False,
        "error",
        "缺少逐日可投资股票池、退市证券和历史风险警示状态，无法构造无幸存者偏差的历史股票池。",
    )
    add(
        "survivorship_bias_corrected",
        False,
        True,
        False,
        "error",
        "当前回测仍是用现时成员和流动性筛出的股票池回看历史；本审计只量化暴露，不修正偏差。",
    )
    add(
        "committed_universe_source",
        committed_source,
        "fetch_history_provenance",
        committed_source == "fetch_history_provenance",
        "ok" if committed_source == "fetch_history_provenance" else "warning",
        "长期验证股票池应由完成记录的逐股票来源清单绑定。",
    )
    add(
        "current_pool_symbols",
        pool_count,
        "> 0",
        pool_count > 0,
        "info" if pool_count > 0 else "error",
        "当前冻结并完成历史抓取的股票数量。",
    )
    add(
        "all_cached_historical_symbols",
        cached_count,
        "informational",
        True,
        "info",
        "同一缓存和研究区间内至少出现过一条行情的全部股票数量，不等同于完整历史全市场。",
    )
    add(
        "cached_symbols_excluded_from_current_pool",
        excluded_count,
        "0 for point-in-time completeness",
        excluded_count == 0,
        "warning" if excluded_count else "ok",
        "缓存中存在但未进入当前冻结池的股票；这些股票不能据此认定为退市股，但说明回测不是全缓存覆盖。",
    )
    add(
        "current_pool_share_of_cached_symbols",
        round(pool_share, 6),
        "1.0 for point-in-time completeness",
        cached_count > 0 and excluded_count == 0,
        "warning" if excluded_count else "ok",
        "当前冻结池占缓存历史股票的比例。该比例不是策略优劣指标，只描述样本选择范围。",
    )
    add(
        "earliest_snapshot_current_pool_priced_share",
        round(earliest_share, 6),
        "informational",
        True,
        "info",
        "研究区间首个年度末，当前冻结池行情数占当日缓存行情股票数的比例。",
    )
    add(
        "unreliable_current_pool_list_date_ratio",
        round(unreliable_ratio, 6),
        "<= 0.05",
        unreliable_ratio <= 0.05,
        "warning" if unreliable_ratio > 0.05 else "ok",
        "上市日期缺失或使用 2000-01-01 占位的比例；过高时只能做粗略上市可交易检查。",
    )
    add(
        "delisted_security_history_available",
        False,
        True,
        False,
        "error",
        "当前股票列表不能证明已包含研究期内退市、长期停牌或曾被风险警示后退出的证券。",
    )
    add(
        "survivorship_risk_level",
        "high",
        "low",
        False,
        "error",
        "在取得逐日成分、退市证券和可靠上市状态前，长期超额收益必须按高幸存者偏差风险解释。",
    )
    add(
        "study_range",
        f"{_date_text(study_start)}..{_date_text(study_end)}",
        "informational",
        True,
        "info",
        f"股票列表中共有 {placeholder_count} 条 2000-01-01 占位上市日期；该计数覆盖整个当前股票列表。",
    )
    return pd.DataFrame(rows, columns=SURVIVORSHIP_AUDIT_COLUMNS), snapshots


def write_survivorship_bias_audit(config: dict[str, Any]) -> dict[str, Path]:
    store = DataStore.from_config(config)
    stocks = store.read_frame("stocks", required=False)
    prices = store.read_frame("prices", required=False)
    provenance = store.read_frame("fetch_history_provenance", required=False)
    committed = provenance["symbol"].tolist() if "symbol" in provenance.columns else []
    backtest = config.get("backtest", {}) if isinstance(config.get("backtest"), dict) else {}
    audit, snapshots = build_survivorship_bias_audit(
        stocks,
        prices,
        committed,
        start=backtest.get("start"),
        end=backtest.get("end"),
    )
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "survivorship_bias_audit.csv"
    snapshot_path = output_dir / "survivorship_bias_snapshots.csv"
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(snapshots, snapshot_path, index=False, encoding="utf-8-sig")
    return {
        "survivorship_bias_audit.csv": audit_path,
        "survivorship_bias_snapshots.csv": snapshot_path,
    }


def survivorship_evidence_sha256(audit: pd.DataFrame, snapshots: pd.DataFrame) -> str:
    payload = f"{frame_sha256(audit)}:{frame_sha256(snapshots)}".encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def survivorship_warnings(audit: pd.DataFrame) -> list[str]:
    if audit.empty or not {"item", "passed", "note"}.issubset(audit.columns):
        return []
    failed = audit[~audit["passed"].map(_truthy)]
    priorities = {
        "point_in_time_universe_available",
        "survivorship_bias_corrected",
        "delisted_security_history_available",
        "survivorship_risk_level",
    }
    return [str(row["note"]) for _, row in failed.iterrows() if str(row["item"]) in priorities]


def _normalized_price_presence(prices: pd.DataFrame, start: object, end: object) -> pd.DataFrame:
    if prices.empty or not {"date", "symbol"}.issubset(prices.columns):
        return pd.DataFrame(columns=["date", "symbol"])
    frame = prices[["date", "symbol"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame = frame.dropna(subset=["date"])
    frame = frame[frame["symbol"] != ""]
    start_date = pd.to_datetime(start, errors="coerce")
    end_date = pd.to_datetime(end, errors="coerce")
    if pd.notna(start_date):
        frame = frame[frame["date"] >= start_date]
    if pd.notna(end_date):
        frame = frame[frame["date"] <= end_date]
    return frame.drop_duplicates().sort_values(["date", "symbol"]).reset_index(drop=True)


def _stock_list_dates(stocks: pd.DataFrame) -> dict[str, pd.Timestamp]:
    if stocks.empty or not {"symbol", "list_date"}.issubset(stocks.columns):
        return {}
    frame = stocks[["symbol", "list_date"]].copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    frame = frame[frame["symbol"] != ""].drop_duplicates("symbol", keep="last")
    return dict(zip(frame["symbol"], frame["list_date"], strict=False))


def _annual_snapshots(
    prices: pd.DataFrame,
    current_pool: set[str],
    list_dates: dict[str, pd.Timestamp],
) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=SURVIVORSHIP_SNAPSHOT_COLUMNS)
    dated = prices.assign(year=prices["date"].dt.year)
    snapshot_dates = dated.groupby("year", sort=True)["date"].max()
    rows: list[dict[str, object]] = []
    for year, snapshot_date in snapshot_dates.items():
        day_symbols = set(dated.loc[dated["date"] == snapshot_date, "symbol"])
        pool_priced = day_symbols & current_pool
        listed = {
            symbol
            for symbol in current_pool
            if symbol in list_dates and list_dates[symbol] <= snapshot_date
        }
        rows.append(
            {
                "year": int(year),
                "snapshot_date": snapshot_date.date().isoformat(),
                "all_cached_priced_symbols": len(day_symbols),
                "current_pool_priced_symbols": len(pool_priced),
                "current_pool_listed_symbols": len(listed),
                "current_pool_not_yet_listed_symbols": len(current_pool - listed),
                "current_pool_priced_share_of_cache": round(
                    len(pool_priced) / len(day_symbols) if day_symbols else 0.0,
                    6,
                ),
            }
        )
    return pd.DataFrame(rows, columns=SURVIVORSHIP_SNAPSHOT_COLUMNS)


def _date_text(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "missing" if pd.isna(parsed) else parsed.date().isoformat()


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}
