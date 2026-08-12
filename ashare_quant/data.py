from __future__ import annotations
import json

import math
import os
import re
import socket
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .config import project_path
from .baostock_source import (
    baostock_session,
    enrich_stock_metadata_baostock,
    fetch_benchmark_frame_baostock,
    fetch_history_frame_baostock,
    fetch_trade_calendar_frame_baostock,
)
from .index_symbols import canonical_index_symbol, provider_index_symbol


PRICE_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "amount"]
STOCK_COLUMNS = ["symbol", "name", "board", "industry", "list_date", "is_st"]
FUNDAMENTAL_COLUMNS = ["date", "symbol", "pe", "pb", "roe"]
BENCHMARK_COLUMNS = ["date", "symbol", "name", "close"]
TRADE_CALENDAR_COLUMNS = ["date", "is_trading_day", "source", "retrieved_at"]
HISTORY_PROVENANCE_COLUMNS = [
    "symbol", "source", "first_date", "last_date", "rows", "amount_quality",
    "overlap_status", "overlap_rows",
    "overlap_close_median_relative_error",
    "overlap_volume_median_relative_error",
    "overlap_amount_median_relative_error",
    "alternate_overlap_status", "alternate_overlap_source",
    "alternate_overlap_rows",
    "alternate_overlap_close_median_relative_error",
    "alternate_overlap_volume_median_relative_error",
    "alternate_overlap_amount_median_relative_error",
    "start_coverage_status", "start_coverage_expected_date",
    "start_coverage_observed_date", "start_coverage_verifier_source",
    "start_coverage_verifier_first_date",
]


@dataclass
class DataStore:
    cache_dir: Path
    state_db: Path

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "DataStore":
        paths = config["paths"]
        cache_dir = project_path(paths["cache_dir"])
        state_db = project_path(paths["state_db"])
        cache_dir.mkdir(parents=True, exist_ok=True)
        state_db.parent.mkdir(parents=True, exist_ok=True)
        return cls(cache_dir=cache_dir, state_db=state_db)

    def init_db(self) -> None:
        with sqlite3.connect(self.state_db) as conn:
            conn.executescript(
                """
                create table if not exists runs (
                  id integer primary key autoincrement,
                  command text not null,
                  started_at text not null,
                  status text not null,
                  message text
                );
                create table if not exists account_snapshots (
                  date text primary key,
                  cash real not null,
                  equity real not null
                );
                create table if not exists positions (
                  symbol text primary key,
                  quantity integer not null,
                  cost real not null,
                  locked_quantity integer not null
                );
                create table if not exists orders (
                  date text,
                  symbol text,
                  side text,
                  quantity integer,
                  reason text
                );
                create table if not exists trades (
                  date text,
                  symbol text,
                  side text,
                  quantity integer,
                  price real,
                  fee real,
                  amount real,
                  reason text
                );
                create table if not exists execution_imports (
                  id integer primary key autoincrement,
                  imported_at text not null,
                  source_file text not null,
                  source_sha256 text not null unique,
                  trade_date text not null,
                  fill_count integer not null
                );
                create table if not exists execution_fill_keys (
                  event_key text primary key,
                  imported_at text not null,
                  source_sha256 text not null
                );
                """
            )

    def log_run(self, command: str, status: str, message: str = "") -> None:
        self.init_db()
        with sqlite3.connect(self.state_db) as conn:
            conn.execute(
                "insert into runs(command, started_at, status, message) values (?, ?, ?, ?)",
                (command, datetime.now().isoformat(timespec="seconds"), status, message),
            )

    def _paths(self, name: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{name}.parquet", self.cache_dir / f"{name}.csv"

    def write_frame(self, name: str, frame: pd.DataFrame) -> Path:
        parquet_path, csv_path = self._paths(name)
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            frame.to_parquet(parquet_path, index=False)
            if csv_path.exists():
                csv_path.unlink()
            return parquet_path
        except Exception:
            frame.to_csv(csv_path, index=False, encoding="utf-8")
            return csv_path

    def read_frame(self, name: str, required: bool = True) -> pd.DataFrame:
        parquet_path, csv_path = self._paths(name)
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        if csv_path.exists():
            return pd.read_csv(csv_path)
        if required:
            raise FileNotFoundError(f"Missing cached dataset '{name}'. Run `python -m ashare_quant fetch --sample` first.")
        return pd.DataFrame()

    def load_market_data(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        stocks = self.read_frame("stocks")
        prices = self.read_frame("prices")
        fundamentals = self.read_frame("fundamentals", required=False)
        return normalize_stocks(stocks), normalize_prices(prices), normalize_fundamentals(fundamentals)

    def load_benchmark(self, required: bool = False) -> pd.DataFrame:
        return normalize_benchmark(self.read_frame("benchmark", required=required))


def normalize_prices(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    missing = set(PRICE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"prices missing columns: {sorted(missing)}")
    out = frame[PRICE_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["symbol"] = out["symbol"].map(normalize_symbol)
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "symbol", "open", "close"]).drop_duplicates(["date", "symbol"])
    return out.sort_values(["date", "symbol"]).reset_index(drop=True)


def normalize_stocks(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=STOCK_COLUMNS)
    missing = set(STOCK_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"stocks missing columns: {sorted(missing)}")
    out = frame[STOCK_COLUMNS].copy()
    out["symbol"] = out["symbol"].map(normalize_symbol)
    out["industry"] = out["industry"].map(normalize_industry)
    out["list_date"] = pd.to_datetime(out["list_date"]).dt.date
    out["is_st"] = out["is_st"].astype(bool)
    return out.drop_duplicates(["symbol"]).reset_index(drop=True)


UNKNOWN_INDUSTRY_VALUES = {
    "",
    "--",
    "n/a",
    "nan",
    "none",
    "null",
    "unknown",
    "未分类",
    "未知",
    "未知行业",
}


def repair_gb18030_mojibake(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if not text or re.search(r"[\u4e00-\u9fff]", text):
        return text
    try:
        repaired = text.encode("latin1").decode("gb18030").strip()
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if re.search(r"[\u4e00-\u9fff]", repaired):
        return repaired
    return text


def normalize_industry(value: object) -> str:
    industry = repair_gb18030_mojibake(value)
    if industry.casefold() in UNKNOWN_INDUSTRY_VALUES:
        return "unknown"
    industry = re.sub(
        r"^[A-Z]\d{2}(?=[\u4e00-\u9fff])",
        "",
        industry,
        flags=re.IGNORECASE,
    ).strip()
    return industry if industry.casefold() not in UNKNOWN_INDUSTRY_VALUES else "unknown"

def normalize_fundamentals(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)
    missing = set(FUNDAMENTAL_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"fundamentals missing columns: {sorted(missing)}")
    out = frame[FUNDAMENTAL_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["symbol"] = out["symbol"].map(normalize_symbol)
    for col in ["pe", "pb", "roe"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.drop_duplicates(["date", "symbol"]).reset_index(drop=True)


def normalize_benchmark(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=BENCHMARK_COLUMNS)
    missing = set(BENCHMARK_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"benchmark missing columns: {sorted(missing)}")
    out = frame[BENCHMARK_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["symbol"] = out["symbol"].map(canonical_index_symbol)
    out["name"] = out["name"].astype(str)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    return out.dropna(subset=["date", "close"]).drop_duplicates(["date"]).sort_values("date").reset_index(drop=True)


def normalize_symbol(value: object) -> str:
    symbol = str(value).strip()
    if symbol.endswith(".0"):
        symbol = symbol[:-2]
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if digits and len(digits) <= 6:
        return digits.zfill(6)
    return symbol


def _accepted_retired_history_failure_symbols(
    store: DataStore,
    failures: pd.DataFrame,
    prices: pd.DataFrame,
    provenance: pd.DataFrame,
) -> set[str]:
    required_failure_columns = {"symbol", "stage"}
    if failures.empty or not required_failure_columns.issubset(failures.columns):
        return set()
    failure_symbols = {normalize_symbol(value) for value in failures["symbol"]}
    failure_symbols.discard("")
    if len(failure_symbols) != len(failures):
        return set()
    if set(failures["stage"].astype(str).str.strip().str.lower()) != {"history"}:
        return set()
    try:
        stocks = store.read_frame("stocks", required=False)
        membership = store.read_frame("index_membership", required=False)
    except (AttributeError, FileNotFoundError, OSError, ValueError, TypeError):
        return set()
    if (
        stocks.empty
        or "symbol" not in stocks
        or membership.empty
        or not {"snapshot_date", "symbol"}.issubset(membership.columns)
        or prices.empty
        or "symbol" not in prices
        or "symbol" not in provenance
    ):
        return set()
    current_stock_symbols = {normalize_symbol(value) for value in stocks["symbol"]}
    membership = membership.copy()
    membership["snapshot_date"] = pd.to_datetime(
        membership["snapshot_date"], errors="coerce"
    )
    membership = membership.dropna(subset=["snapshot_date"])
    if membership.empty:
        return set()
    membership["symbol"] = membership["symbol"].map(normalize_symbol)
    historical_member_symbols = set(membership["symbol"])
    latest_snapshot = membership["snapshot_date"].max()
    latest_member_symbols = set(
        membership.loc[membership["snapshot_date"] == latest_snapshot, "symbol"]
    )
    priced_symbols = {normalize_symbol(value) for value in prices["symbol"]}
    bound_symbols = {normalize_symbol(value) for value in provenance["symbol"]}
    accepted = failure_symbols & historical_member_symbols & priced_symbols & bound_symbols
    return accepted - current_stock_symbols - latest_member_symbols


def committed_fetch_market_scope(
    store: DataStore,
    prices: pd.DataFrame,
) -> tuple[pd.DataFrame, bool, str]:
    try:
        summary = store.read_frame("fetch_summary")
        provenance = store.read_frame("fetch_history_provenance")
    except (AttributeError, FileNotFoundError, OSError, ValueError, TypeError) as exc:
        return prices.copy(), False, f"manifest_unreadable:{type(exc).__name__}"
    required = {"requested_symbols", "fetched_symbols", "failed_items", "stage"}
    if not isinstance(summary, pd.DataFrame) or not isinstance(provenance, pd.DataFrame):
        return prices.copy(), False, "manifest_schema_invalid"
    if len(summary) != 1 or not required.issubset(summary.columns) or "symbol" not in provenance:
        return prices.copy(), False, "manifest_schema_invalid"
    row = summary.iloc[0]
    try:
        requested = int(float(row.get("requested_symbols", 0) or 0))
        fetched = int(float(row.get("fetched_symbols", 0) or 0))
        failed = int(float(row.get("failed_items", 0) or 0))
    except (TypeError, ValueError):
        return prices.copy(), False, "manifest_counts_invalid"
    stage = str(row.get("stage", "") or "").strip().lower()
    symbols = {normalize_symbol(value) for value in provenance["symbol"]}
    symbols.discard("")
    accepted_retired_failures: set[str] = set()
    if failed:
        try:
            failures = store.read_frame("fetch_failures", required=False)
        except (AttributeError, FileNotFoundError, OSError, ValueError, TypeError):
            failures = pd.DataFrame()
        if isinstance(failures, pd.DataFrame) and len(failures) == failed:
            accepted_retired_failures = _accepted_retired_history_failure_symbols(
                store, failures, prices, provenance
            )
    unexplained_failures = failed - len(accepted_retired_failures)
    passed = bool(
        stage == "complete"
        and requested > 0
        and fetched == requested
        and unexplained_failures == 0
        and len(symbols) == fetched
    )
    value = (
        f"stage={stage or 'missing'};requested={requested};fetched={fetched};"
        f"failed={failed};accepted_retired_history_failures="
        f"{len(accepted_retired_failures)};unexplained_failures={unexplained_failures};"
        f"bound_symbols={len(symbols)}"
    )
    if not passed:
        return prices.copy(), False, value
    scoped = prices.copy()
    scoped["symbol"] = scoped["symbol"].map(normalize_symbol)
    scoped = scoped[scoped["symbol"].isin(symbols)].copy()
    return scoped, not scoped.empty, value


def scope_market_frames_to_committed_fetch(
    store: DataStore,
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, bool, str]:
    scoped_prices, passed, value = committed_fetch_market_scope(store, prices)
    if not passed:
        return stocks.copy(), prices.copy(), fundamentals.copy(), False, value

    symbols = set(scoped_prices["symbol"].astype(str))
    scoped_stocks = stocks.copy()
    if not scoped_stocks.empty and "symbol" in scoped_stocks.columns:
        stock_symbols = scoped_stocks["symbol"].map(normalize_symbol)
        scoped_stocks = scoped_stocks[stock_symbols.isin(symbols)].copy()
    scoped_fundamentals = fundamentals.copy()
    if not scoped_fundamentals.empty and "symbol" in scoped_fundamentals.columns:
        fundamental_symbols = scoped_fundamentals["symbol"].map(normalize_symbol)
        scoped_fundamentals = scoped_fundamentals[
            fundamental_symbols.isin(symbols)
        ].copy()
    return scoped_stocks, scoped_prices, scoped_fundamentals, True, value


def fetch_akshare(store: DataStore, config: dict[str, Any]) -> list[Path]:
    try:
        import akshare as ak  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("AkShare is not installed. Use --sample or install ashare-quant[data].") from exc

    with akshare_network_context(config):
        ensure_benchmark_membership_fetch_prerequisites(store, config, ak)
        if config.get("data", {}).get("force_cached_universe", False):
            cached = cached_universe_symbols(store, config)
            if cached is None:
                raise RuntimeError("本地股票池缓存不可用，无法执行 --cached-universe 刷新。请先成功运行一次普通 fetch 或 fetch --sample。")
            stocks, symbols = cached
            print(f"Using cached universe ({len(symbols)} symbols).")
        else:
            try:
                spot = fetch_stock_spot(ak, retries=int(config.get("data", {}).get("spot_retries", 3)))
                stocks = stocks_from_spot(spot)
                symbols = (
                    []
                    if benchmark_membership_only_fetch(config)
                    else select_fetch_symbols(spot, config).head(max_symbols_from_config(config)).tolist()
                )
                symbols = ensure_required_fetch_symbols(symbols, store, config)
            except Exception as exc:
                if not config.get("data", {}).get("use_cached_universe_on_spot_failure", True):
                    raise RuntimeError(
                        "刷新行情池失败：无法连接股票列表数据源。系统已保留原有缓存。"
                        f"详情：{exc}"
                    ) from exc
                cached = cached_universe_symbols(store, config)
                if cached is None:
                    raise RuntimeError(
                        "刷新行情池失败：无法连接股票列表数据源，且本地没有可用股票池缓存。"
                        f"详情：{exc}"
                    ) from exc
                stocks, symbols = cached
                print(f"Stock spot source unavailable; using cached universe ({len(symbols)} symbols). Details: {exc}")

        formal_symbols = list(symbols)
        end = finalized_market_data_end(config)
        configured_end = config.get("backtest", {}).get("end")
        if configured_end:
            end = min(end, pd.to_datetime(configured_end).date())
        base_start = pd.to_datetime(config["backtest"]["start"]).date()
        freeze_cutoff = immutable_candidate_freeze_date(config)
        if freeze_cutoff is not None and end <= freeze_cutoff:
            cached_benchmark = store.load_benchmark(required=False)
            print(
                f"Candidate market cache is immutable through {freeze_cutoff}; "
                f"no post-freeze session is available by {end}."
            )
            return [store.write_frame("benchmark", cached_benchmark)]
        end = resolve_finalized_trading_date(store, config, end)
        if freeze_cutoff is not None and end <= freeze_cutoff:
            cached_benchmark = store.load_benchmark(required=False)
            print(
                f"Candidate market cache is immutable through {freeze_cutoff}; "
                f"trade-calendar preflight found no later session by {end}."
            )
            return [store.write_frame("benchmark", cached_benchmark)]
        incremental = bool(config.get("data", {}).get("incremental_fetch", True))
        cached_ranges = cached_price_date_ranges_by_symbol(store) if incremental else {}
        cached_latest = {symbol: bounds[1] for symbol, bounds in cached_ranges.items()}
        cached_earliest = {symbol: bounds[0] for symbol, bounds in cached_ranges.items()}
        if (
            immutable_candidate_freeze_date(config) is not None
            and bool(config.get("data", {}).get("candidate_incremental_gap_only", True))
        ):
            requested_count = len(symbols)
            try:
                provenance = store.read_frame("fetch_history_provenance", required=False)
                proven_symbols = {
                    normalize_symbol(symbol)
                    for symbol in provenance.get("symbol", pd.Series(dtype=str))
                }
            except (AttributeError, FileNotFoundError, OSError, ValueError, TypeError):
                proven_symbols = set()
            symbols = candidate_incremental_gap_symbols(
                symbols, cached_latest, end, proven_symbols
            )
            print(f"Candidate incremental gap: pending={len(symbols)}/{requested_count}, target={end}.")
            if not symbols:
                cached_benchmark = store.load_benchmark(required=False)
                benchmark_dates = (
                    pd.to_datetime(cached_benchmark["date"], errors="coerce").dropna()
                    if not cached_benchmark.empty and "date" in cached_benchmark
                    else pd.Series(dtype="datetime64[ns]")
                )
                benchmark_latest = (
                    benchmark_dates.max().date() if not benchmark_dates.empty else None
                )
                if benchmark_latest is not None and benchmark_latest >= end:
                    print(
                        f"Candidate market cache already current through {end}; no history request needed."
                    )
                    return [store.write_frame("benchmark", cached_benchmark)]
                fetched_benchmark = normalize_benchmark(fetch_benchmark_akshare(ak, config))
                if fetched_benchmark.empty and bool(
                    config.get("data", {}).get("benchmark_fallback_to_baostock", True)
                ):
                    benchmark_cfg = config.get("benchmark", {})
                    with baostock_session() as client:
                        fetched_benchmark = normalize_benchmark(
                            fetch_benchmark_frame_baostock(
                                client,
                                str(benchmark_cfg.get("symbol", "000300")),
                                str(benchmark_cfg.get("name", "000300")),
                                immutable_fetch_start(config, base_start),
                                end,
                            )
                        )
                benchmark = merge_benchmark_cache(store, fetched_benchmark, config)
                return [store.write_frame("benchmark", benchmark)]

        fetch_starts: dict[str, date] = {}
        historical_backfill_symbols: list[str] = []
        price_frames = []
        failures = []
        history_provenance: list[dict[str, object]] = []
        history_overlap_reference = normalize_prices(
            store.read_frame("prices", required=False)
        )
        partial_frame_name = history_staging_frame_name(config, "prices_partial")
        provenance_frame_name = history_staging_frame_name(
            config,
            "fetch_history_partial_provenance",
        )
        progress_frame_name = history_staging_frame_name(
            config,
            "history_backfill_progress",
        )
        failure_frame_name = history_staging_frame_name(config, "fetch_failures")
        fallback_history_count = 0
        history_batch_source_status = "not_attempted"
        history_attempted_symbols = 0
        history_successful_symbols = 0
        checkpoint_every = int(config.get("data", {}).get("checkpoint_every", 25))
        history_workers = max(1, int(config.get("data", {}).get("history_workers", 1)))
        history_retries = max(1, int(config.get("data", {}).get("history_retries", 1)))
        history_source = str(config.get("data", {}).get("history_source", "auto")).strip().lower()
        if history_source not in {"auto", "akshare", "baostock"}:
            raise ValueError("data.history_source must be auto, akshare, or baostock")
        expected_starts = expected_history_start_dates(stocks, symbols, base_start)
        for symbol in symbols:
            expected_start = expected_starts.get(normalize_symbol(symbol), base_start)
            fetch_starts[str(symbol)] = (
                fetch_start_for_symbol(
                    symbol,
                    expected_start,
                    cached_latest,
                    config,
                    cached_earliest=cached_earliest,
                )
                if incremental
                else expected_start
            )
            fetch_starts[str(symbol)] = immutable_fetch_start(config, fetch_starts[str(symbol)])
            earliest = cached_earliest.get(normalize_symbol(symbol))
            if earliest is not None and fetch_starts[str(symbol)] < earliest:
                historical_backfill_symbols.append(normalize_symbol(symbol))

        resumed_frames, resumed_provenance, resumed_symbols = resumable_partial_history(
            store,
            symbols,
            fetch_starts,
            end,
            config,
        )
        price_frames.extend(resumed_frames)
        history_provenance.extend(resumed_provenance)
        pending_symbols = [
            symbol for symbol in symbols if normalize_symbol(symbol) not in resumed_symbols
        ]
        if resumed_symbols:
            print(
                f"Resumed validated partial history: {len(resumed_symbols)}/{len(symbols)} symbols; "
                f"pending={len(pending_symbols)}.",
                flush=True,
            )
        history_budget = max(
            0,
            int(config.get("data", {}).get("history_request_budget", 0) or 0),
        )
        deferred_symbols = (
            pending_symbols[history_budget:]
            if history_budget and len(pending_symbols) > history_budget
            else []
        )
        if deferred_symbols:
            pending_symbols = pending_symbols[:history_budget]
            print(
                f"History request budget: processing={len(pending_symbols)}; "
                f"deferred={len(deferred_symbols)}.",
                flush=True,
            )
        history_attempted_symbols = len(pending_symbols)

        def handle_history_result(idx: int, symbol: str, result: tuple[pd.DataFrame, dict[str, object]] | None, error: Exception | None) -> None:
            nonlocal fallback_history_count
            if error is not None or result is None:
                failures.append({"symbol": symbol, "stage": "history", "error": str(error or "empty result")})
            else:
                normalized, provenance = result
                overlap, overlap_issues = history_overlap_quality(
                    normalized,
                    history_overlap_reference,
                    config,
                )
                provenance.update(overlap)
                start_coverage, start_coverage_issues = history_start_coverage_quality(
                    ak,
                    symbol,
                    normalized,
                    str(provenance.get("source", "")),
                    fetch_starts[str(symbol)],
                    end,
                    config,
                )
                provenance.update(start_coverage)
                allow_estimated = bool(
                    config.get("data", {}).get(
                        "history_allow_estimated_amount_fallback",
                        True,
                    )
                )
                alternate_source_issues = list(overlap_issues)
                if (
                    alternate_source_issues
                    and provenance.get("source") == "stock_zh_a_daily"
                    and provenance.get("amount_quality") == "reported"
                ):
                    alternate_overlap, alternate_overlap_issues = (
                        history_alternate_overlap_quality(
                            ak,
                            symbol,
                            normalized,
                            str(provenance.get("source", "")),
                            fetch_starts[str(symbol)],
                            end,
                            config,
                        )
                    )
                    provenance.update(alternate_overlap)
                    if not alternate_overlap_issues:
                        alternate_source_issues = []
                    else:
                        alternate_source_issues.extend(
                            f"alternate source validation: {issue}"
                            for issue in alternate_overlap_issues
                        )
                if provenance.get("amount_quality") != "reported" and not allow_estimated:
                    alternate_source_issues.append(
                        f"amount quality {provenance.get('amount_quality')} is not allowed"
                    )
                trust_issues = list(start_coverage_issues)
                if provenance["source"] != "stock_zh_a_hist":
                    trust_issues.extend(alternate_source_issues)
                if trust_issues:
                    failures.append(
                        {
                            "symbol": symbol,
                            "stage": "history",
                            "error": f"{provenance['source']} trust validation: "
                            + "; ".join(trust_issues),
                        }
                    )
                else:
                    price_frames.append(normalized)
                    history_provenance.append(provenance)
                    if provenance["source"] != "stock_zh_a_hist":
                        fallback_history_count += 1
            if checkpoint_every > 0 and idx % checkpoint_every == 0:
                if price_frames:
                    partial = normalize_prices(pd.concat(price_frames, ignore_index=True))
                    store.write_frame(
                        provenance_frame_name,
                        pd.DataFrame(history_provenance, columns=HISTORY_PROVENANCE_COLUMNS),
                    )
                    store.write_frame(partial_frame_name, partial)
                    partial_rows = len(partial)
                else:
                    partial_rows = 0
                print(f"Fetched {idx + len(resumed_symbols)}/{len(symbols)} symbols; success={len(price_frames)}, failed={len(failures)}, partial rows={partial_rows}", flush=True)

        if history_source != "baostock" and (history_workers == 1 or len(pending_symbols) <= 1):
            for idx, symbol in enumerate(pending_symbols, start=1):
                try:
                    result = fetch_symbol_history(ak, symbol, fetch_starts[str(symbol)], end, retries=history_retries)
                    handle_history_result(idx, symbol, result, None)
                except Exception as exc:
                    handle_history_result(idx, symbol, None, exc)
        elif history_source != "baostock":
            with ThreadPoolExecutor(max_workers=history_workers) as executor:
                future_map = {
                    executor.submit(fetch_symbol_history, ak, symbol, fetch_starts[str(symbol)], end, history_retries): symbol
                    for symbol in pending_symbols
                }
                for idx, future in enumerate(as_completed(future_map), start=1):
                    symbol = future_map[future]
                    try:
                        result = future.result()
                        handle_history_result(idx, symbol, result, None)
                    except Exception as exc:
                        handle_history_result(idx, symbol, None, exc)

        baostock_targets = (
            list(pending_symbols)
            if history_source == "baostock"
            else [
                str(row.get("symbol", ""))
                for row in failures
                if row.get("stage") == "history"
            ]
            if bool(config.get("data", {}).get("history_fallback_to_baostock", True))
            else []
        )
        if baostock_targets:
            previous_errors = {
                str(row.get("symbol", "")): str(row.get("error", ""))
                for row in failures
                if row.get("stage") == "history"
            }
            target_set = set(baostock_targets)
            failures = [
                row
                for row in failures
                if row.get("stage") != "history" or str(row.get("symbol", "")) not in target_set
            ]
            def baostock_checkpoint(
                request_count: int,
                batch_frames: list[pd.DataFrame],
                batch_provenance_rows: list[dict[str, object]],
                error_count: int,
            ) -> None:
                if checkpoint_every <= 0 or request_count % checkpoint_every != 0:
                    return
                combined = price_frames + batch_frames
                partial = normalize_prices(pd.concat(combined, ignore_index=True)) if combined else pd.DataFrame(columns=PRICE_COLUMNS)
                if not partial.empty:
                    store.write_frame(
                        provenance_frame_name,
                        pd.DataFrame(
                            history_provenance + batch_provenance_rows,
                            columns=HISTORY_PROVENANCE_COLUMNS,
                        ),
                    )
                    store.write_frame(partial_frame_name, partial)
                print(
                    f"BaoStock requests={request_count}; completed={len(batch_frames)}/{len(baostock_targets)}; "
                    f"pending_errors={error_count}; partial_rows={len(partial)}",
                    flush=True,
                )

            batch_frames, batch_provenance, batch_errors = fetch_baostock_history_batch(
                baostock_targets,
                fetch_starts,
                end,
                config,
                on_progress=baostock_checkpoint,
            )
            batch_frames, batch_provenance, coverage_errors = (
                validate_history_start_coverage_batch(
                    ak,
                    batch_frames,
                    batch_provenance,
                    fetch_starts,
                    end,
                    config,
                )
            )
            batch_errors.update(coverage_errors)
            price_frames.extend(batch_frames)
            history_provenance.extend(batch_provenance)
            fallback_history_count += len(batch_frames)
            for symbol, error in batch_errors.items():
                prior = previous_errors.get(symbol, "")

                detail = f"{prior}; {error}".strip("; ")
                failures.append({"symbol": symbol, "stage": "history", "error": detail})
        attempted_set = {normalize_symbol(symbol) for symbol in pending_symbols}
        successful_set = {
            normalize_symbol(frame["symbol"].iloc[0])
            for frame in price_frames
            if not frame.empty and "symbol" in frame
        } - resumed_symbols
        history_successful_symbols = len(attempted_set & successful_set)
        history_errors = {
            normalize_symbol(row.get("symbol", "")): str(row.get("error", ""))
            for row in failures
            if row.get("stage") == "history"
            and normalize_symbol(row.get("symbol", "")) in attempted_set
        }
        history_batch_source_status = classify_history_batch_source_status(
            pending_symbols,
            successful_set,
            history_errors,
        )
        history_provenance.sort(key=lambda row: str(row.get("symbol", "")))
        failures.sort(key=lambda row: (str(row.get("stage", "")), str(row.get("symbol", ""))))

        partial = pd.DataFrame(columns=PRICE_COLUMNS)
        if price_frames:
            partial = normalize_prices(pd.concat(price_frames, ignore_index=True))
            store.write_frame(partial_frame_name, partial)
            print(
                f"Finished history batch: fetched={len(price_frames)}/{len(symbols)}, "
                f"failed={len(failures)}, partial rows={len(partial)}."
            )

        unfinished_symbols = unfinished_history_symbols(symbols, price_frames)
        if history_budget and unfinished_symbols:
            progress = build_history_backfill_progress(
                symbols,
                price_frames,
                failures,
                resumed_symbols,
                deferred_symbols,
                base_start,
                end,
                history_budget,
                status="in_progress",
                source_status=history_batch_source_status,
                attempted_symbols=history_attempted_symbols,
                successful_symbols=history_successful_symbols,
            )
            paths = [
                store.write_frame(partial_frame_name, partial),
                store.write_frame(
                    provenance_frame_name,
                    pd.DataFrame(history_provenance),
                ),
                store.write_frame(progress_frame_name, progress),
                store.write_frame(
                    failure_frame_name,
                    pd.DataFrame(
                        failures,
                        columns=["symbol", "stage", "error"],
                    ),
                ),
            ]
            return paths

        if not price_frames:
            if failures:
                store.write_frame("fetch_failures", pd.DataFrame(failures))
            raise RuntimeError("AkShare returned no price data. Try --sample or check network/source availability.")
        required_success = min(
            len(symbols),
            max(
                1,
                int(len(symbols) * float(config.get("data", {}).get("min_fetch_success_ratio", 0.5))),
                int(config.get("data", {}).get("min_fetch_success_symbols", 20)),
            ),
        )
        if len(price_frames) < required_success:
            if failures:
                store.write_frame("fetch_failures", pd.DataFrame(failures))
            raise RuntimeError(
                f"刷新行情池失败：成功股票数 {len(price_frames)}/{len(symbols)}，低于安全门槛 {required_success}。"
                "系统已保留原有正式缓存；请降低行情池数量或稍后重试。"
            )


        fetched_prices = normalize_prices(pd.concat(price_frames, ignore_index=True))
        fetched_stocks = normalize_stocks(stocks)
        if config.get("data", {}).get("commit_price_checkpoint", True):
            stocks, prices, benchmark = merge_fetched_cache(
                store,
                fetched_stocks,
                fetched_prices,
                pd.DataFrame(columns=BENCHMARK_COLUMNS),
                config,
            )
            fundamentals = merge_generated_fundamentals_cache(
                store, make_fundamentals_from_prices(prices), config
            )
            write_fetch_outputs(
                store,
                stocks,
                prices,
                fundamentals,
                benchmark,
                symbols,
                price_frames,
                failures,
                fallback_history_count,
                fetch_starts,
                incremental,
                "price_checkpoint",
                history_provenance=history_provenance,
                historical_backfill_symbols=historical_backfill_symbols,
                scope_symbols=formal_symbols,
            )
            print(f"Committed price checkpoint: fetched_symbols={len(price_frames)}, merged_rows={len(prices)}.")
        if config.get("data", {}).get("enrich_metadata", True):
            metadata_symbols = sorted(set(fetched_prices["symbol"].astype(str)))
            metadata_source = str(config.get("data", {}).get("metadata_source", "baostock")).lower()
            metadata_errors: list[str] = []
            metadata_success, metadata_cache_value = reusable_metadata_cache(
                store, stocks, metadata_symbols, config
            )
            if metadata_success:
                print(f"BaoStock metadata cache: reused ({metadata_cache_value}).")
            if not metadata_success and metadata_source == "baostock":
                try:
                    stocks, provenance = enrich_stock_metadata_baostock(stocks, metadata_symbols)
                    store.write_frame("stock_metadata_provenance", provenance)
                    metadata_success = True
                    print(f"BaoStock metadata source: ok ({len(provenance)} symbols).")
                except Exception as exc:
                    metadata_errors.append(f"baostock: {exc}")
            if metadata_source == "akshare" or (
                not metadata_success and config.get("data", {}).get("metadata_fallback_to_akshare", True)
            ):
                try:
                    stocks = enrich_stock_metadata(ak, stocks, metadata_symbols)
                    metadata_success = True
                    print(f"AkShare metadata source: ok ({len(metadata_symbols)} symbols).")
                except Exception as exc:
                    metadata_errors.append(f"akshare: {exc}")
            if not metadata_success:
                detail = "; ".join(metadata_errors) or "no configured metadata source"
                failures.append({"symbol": "metadata", "stage": "metadata", "error": detail})
                print(f"Metadata enrichment failed; preserved price checkpoint. Details: {detail}")
        fetched_stocks = normalize_stocks(stocks)
        try:
            fetched_benchmark = normalize_benchmark(fetch_benchmark_akshare(ak, config))
        except Exception as exc:
            fetched_benchmark = pd.DataFrame(columns=BENCHMARK_COLUMNS)
            failures.append({"symbol": str(config.get("benchmark", {}).get("symbol", "000300")), "stage": "benchmark", "error": str(exc)})
        if fetched_benchmark.empty and bool(config.get("data", {}).get("benchmark_fallback_to_baostock", True)):
            benchmark_cfg = config.get("benchmark", {})
            try:
                with baostock_session() as client:
                    fetched_benchmark = normalize_benchmark(
                        fetch_benchmark_frame_baostock(
                            client,
                            str(benchmark_cfg.get("symbol", "000300")),
                            str(benchmark_cfg.get("name", "000300")),
                            immutable_fetch_start(config, base_start),
                            end,
                        )
                    )
                print(f"BaoStock benchmark source: ok ({len(fetched_benchmark)} rows).")
            except Exception as exc:
                failures.append({
                    "symbol": str(benchmark_cfg.get("symbol", "000300")),
                    "stage": "benchmark_baostock",
                    "error": str(exc),
                })
        if fetched_benchmark.empty:
            failures.append({"symbol": str(config.get("benchmark", {}).get("symbol", "000300")), "stage": "benchmark", "error": "empty"})
        stocks, prices, benchmark = merge_fetched_cache(store, fetched_stocks, fetched_prices, fetched_benchmark, config)
        fundamentals = merge_generated_fundamentals_cache(
            store, make_fundamentals_from_prices(prices), config
        )
        if fallback_history_count:
            print(f"Used non-primary history sources for {fallback_history_count}/{len(price_frames)} symbols.")

    completion = (
        build_history_backfill_progress(
            symbols,
            price_frames,
            failures,
            resumed_symbols,
            [],
            base_start,
            end,
            history_budget,
            status=history_backfill_completion_status(failures),
            source_status=history_batch_source_status,
            attempted_symbols=history_attempted_symbols,
            successful_symbols=history_successful_symbols,
        )
        if history_budget
        else None
    )
    return commit_formal_fetch_then_progress(
        lambda: write_fetch_outputs(
            store,
            stocks,
            prices,
            fundamentals,
            benchmark,
            symbols,
            price_frames,
            failures,
            fallback_history_count,
            fetch_starts,
            incremental,
            "complete",
            history_provenance=history_provenance,
            historical_backfill_symbols=historical_backfill_symbols,
            scope_symbols=formal_symbols,
        ),
        store,
        progress_frame_name,
        completion,
    )


def commit_formal_fetch_then_progress(
    formal_writer: Callable[[], list[Path]],
    store: DataStore,
    progress_frame_name: str,
    completion: pd.DataFrame | None,
) -> list[Path]:
    paths = list(formal_writer())
    if completion is not None:
        paths.append(store.write_frame(progress_frame_name, completion))
    return paths


def merge_fetch_history_provenance(
    store: DataStore,
    current_rows: list[dict[str, object]],
    scope_symbols: list[str],
) -> pd.DataFrame:
    try:
        cached = store.read_frame("fetch_history_provenance", required=False)
    except (AttributeError, FileNotFoundError, OSError, ValueError, TypeError):
        cached = pd.DataFrame(columns=HISTORY_PROVENANCE_COLUMNS)
    current = pd.DataFrame(current_rows, columns=HISTORY_PROVENANCE_COLUMNS)
    merged = pd.concat([cached, current], ignore_index=True)
    if merged.empty:
        return pd.DataFrame(columns=HISTORY_PROVENANCE_COLUMNS)
    merged["symbol"] = merged["symbol"].map(normalize_symbol)
    allowed = {normalize_symbol(symbol) for symbol in scope_symbols}
    return (
        merged[merged["symbol"].isin(allowed)]
        .drop_duplicates(["symbol"], keep="last")
        .sort_values(["symbol"])
        .reset_index(drop=True)
        .reindex(columns=HISTORY_PROVENANCE_COLUMNS)
    )


def write_fetch_outputs(
    store: DataStore,
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    symbols: list[str],
    price_frames: list[pd.DataFrame],
    failures: list[dict[str, object]],
    fallback_history_count: int,
    fetch_starts: dict[str, date] | None,
    incremental: bool,
    stage: str,
    history_provenance: list[dict[str, object]] | None = None,
    historical_backfill_symbols: list[str] | None = None,
    scope_symbols: list[str] | None = None,
) -> list[Path]:
    formal_symbols = list(scope_symbols if scope_symbols is not None else symbols)
    formal_provenance = merge_fetch_history_provenance(
        store, history_provenance or [], formal_symbols
    )
    fetch_summary = build_fetch_summary(
        formal_symbols,
        price_frames,
        failures,
        prices,
        fallback_history_count,
        fetch_starts=fetch_starts,
        incremental=incremental,
        stage=stage,
        historical_backfill_symbols=historical_backfill_symbols,
        attempted_symbols=symbols,
        formal_provenance=formal_provenance,
    )
    paths = [
        store.write_frame("stocks", normalize_stocks(stocks)),
        store.write_frame("prices", normalize_prices(prices)),
        store.write_frame("fundamentals", normalize_fundamentals(fundamentals)),
        store.write_frame("benchmark", normalize_benchmark(benchmark)),
        store.write_frame("fetch_summary", fetch_summary),
        store.write_frame("fetch_history_provenance", formal_provenance),
    ]
    if failures:
        paths.append(store.write_frame("fetch_failures", pd.DataFrame(failures)))
    else:
        paths.append(store.write_frame("fetch_failures", pd.DataFrame(columns=["symbol", "stage", "error"])))
    return paths


def recover_partial_prices(store: DataStore, config: dict[str, Any]) -> list[Path]:
    partial = normalize_prices(store.read_frame("prices_partial", required=False))
    if partial.empty:
        raise RuntimeError("没有可恢复的 prices_partial 缓存。请先运行 fetch，或检查 data/cache/prices_partial.* 是否存在。")

    fetched_symbols = sorted(partial["symbol"].astype(str).unique().tolist())
    min_symbols = int(
        config.get("data", {}).get(
            "min_partial_recovery_symbols",
            config.get("data", {}).get("min_fetch_success_symbols", 20),
        )
    )
    if len(fetched_symbols) < max(1, min_symbols):
        raise RuntimeError(
            f"partial 行情只包含 {len(fetched_symbols)} 只股票，低于恢复门槛 {min_symbols}；正式缓存未修改。"
        )

    stocks, _cached_prices, _fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    cached = cached_universe_symbols(store, config)
    requested_symbols = cached[1] if cached is not None else fetched_symbols
    stocks, prices, benchmark = merge_fetched_cache(
        store,
        stocks,
        partial,
        pd.DataFrame(columns=BENCHMARK_COLUMNS),
        config,
    )
    fundamentals = merge_generated_fundamentals_cache(
        store, make_fundamentals_from_prices(prices), config
    )
    price_frames = [frame.copy() for _symbol, frame in partial.groupby("symbol", sort=False)]
    fetch_starts = partial.groupby("symbol")["date"].min().to_dict()
    failures_frame = store.read_frame("fetch_failures", required=False)
    failures = failures_frame.to_dict("records") if not failures_frame.empty else []
    paths = write_fetch_outputs(
        store,
        stocks,
        prices,
        fundamentals,
        benchmark,
        requested_symbols,
        price_frames,
        failures,
        0,
        {str(symbol): value for symbol, value in fetch_starts.items()},
        True,
        "partial_recovery",
    )
    print(
        "Recovered partial prices: "
        f"partial_symbols={len(fetched_symbols)}, partial_rows={len(partial)}, "
        f"merged_rows={len(prices)}, latest_price_date={max(prices['date']) if not prices.empty else ''}, "
        f"benchmark_latest={max(benchmark['date']) if not benchmark.empty else ''}."
    )
    return paths

def cached_price_date_ranges_by_symbol(store: DataStore) -> dict[str, tuple[date, date]]:
    try:
        prices = normalize_prices(store.read_frame("prices", required=False))
    except Exception:
        return {}
    if prices.empty:
        return {}
    ranges = prices.groupby("symbol")["date"].agg(["min", "max"])
    return {
        str(symbol): (row["min"], row["max"])
        for symbol, row in ranges.iterrows()
        if not pd.isna(row["min"]) and not pd.isna(row["max"])
    }


def cached_latest_dates_by_symbol(store: DataStore) -> dict[str, date]:
    return {
        symbol: bounds[1]
        for symbol, bounds in cached_price_date_ranges_by_symbol(store).items()
    }


def expected_history_start_dates(
    stocks: pd.DataFrame,
    symbols: list[str],
    base_start: date,
) -> dict[str, date]:
    expected = {normalize_symbol(symbol): base_start for symbol in symbols}
    if stocks.empty or not {"symbol", "list_date"}.issubset(stocks.columns):
        return expected
    frame = stocks[["symbol", "list_date"]].copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce").dt.date
    for row in frame.dropna(subset=["list_date"]).itertuples(index=False):
        if row.symbol in expected:
            expected[row.symbol] = max(base_start, row.list_date)
    return expected


def historical_backfill_required(
    symbol: str,
    expected_start: date,
    cached_earliest: dict[str, date],
    config: dict[str, Any],
) -> bool:
    earliest = cached_earliest.get(normalize_symbol(symbol))
    if earliest is None:
        return False
    tolerance = max(
        0,
        int(config.get("data", {}).get("history_backfill_tolerance_days", 31)),
    )
    return earliest > expected_start + timedelta(days=tolerance)


def history_staging_frame_name(config: dict[str, Any], base_name: str) -> str:
    namespace = str(
        config.get("data", {}).get("history_staging_namespace", "")
    ).strip()
    if not namespace:
        return base_name
    if not re.fullmatch(r"[A-Za-z0-9_]+", namespace):
        raise ValueError(
            "data.history_staging_namespace must contain only letters, digits, and underscores"
        )
    return f"{base_name}_{namespace}"


def resumable_partial_history(
    store: DataStore,
    symbols: list[str],
    fetch_starts: dict[str, date],
    end: date,
    config: dict[str, Any],
) -> tuple[list[pd.DataFrame], list[dict[str, object]], set[str]]:
    if not bool(config.get("data", {}).get("resume_partial_history", True)):
        return [], [], set()
    try:
        partial = normalize_prices(
            store.read_frame(
                history_staging_frame_name(config, "prices_partial"),
                required=False,
            )
        )
    except Exception:
        return [], [], set()
    if partial.empty:
        return [], [], set()

    requested = {normalize_symbol(symbol) for symbol in symbols}
    normalized_starts = {
        normalize_symbol(symbol): start for symbol, start in fetch_starts.items()
    }
    tolerance = max(
        0,
        int(config.get("data", {}).get("history_backfill_tolerance_days", 31)),
    )
    frames: list[pd.DataFrame] = []
    provenance: list[dict[str, object]] = []
    resumed: set[str] = set()
    try:
        provenance_frame = store.read_frame(
            history_staging_frame_name(
                config,
                "fetch_history_partial_provenance",
            ),
            required=False,
        )
    except Exception:
        provenance_frame = pd.DataFrame()
    provenance_by_symbol = {}
    if not provenance_frame.empty and "symbol" in provenance_frame:
        provenance_by_symbol = {
            normalize_symbol(row["symbol"]): row
            for row in provenance_frame.to_dict(orient="records")
        }
    for symbol, frame in partial.groupby("symbol", sort=False):
        clean_symbol = normalize_symbol(symbol)
        if clean_symbol not in requested or clean_symbol not in normalized_starts:
            continue
        candidate = normalize_prices(frame)
        first_date = candidate["date"].min()
        last_date = candidate["date"].max()
        restored = dict(provenance_by_symbol.get(clean_symbol, {}))
        start_covered = first_date <= normalized_starts[clean_symbol] + timedelta(days=tolerance)
        if not start_covered:
            expected_date = pd.to_datetime(
                restored.get("start_coverage_expected_date"), errors="coerce"
            )
            observed_date = pd.to_datetime(
                restored.get("start_coverage_observed_date"), errors="coerce"
            )
            start_covered = (
                restored.get("start_coverage_status") == "cross_source_confirmed_gap"
                and not pd.isna(expected_date)
                and expected_date.date() == normalized_starts[clean_symbol]
                and not pd.isna(observed_date)
                and observed_date.date() == first_date
            )
        end_covered = last_date >= end
        if not start_covered or not end_covered or price_frame_issues(candidate):
            continue
        restored.setdefault("source", "resumed_partial_unknown_source")
        restored.setdefault("amount_quality", "preserved_normalized")
        require_trusted_provenance = bool(
            config.get("data", {}).get(
                "history_resume_require_trusted_provenance",
                False,
            )
        )
        if require_trusted_provenance and restored.get("amount_quality") != "reported":
            continue
        frames.append(candidate)
        resumed.add(clean_symbol)
        restored.update(
            {
                "symbol": clean_symbol,
                "first_date": first_date,
                "last_date": last_date,
                "rows": len(candidate),
            }
        )
        provenance.append(restored)
    return frames, provenance, resumed

def fetch_baostock_history_batch(
    symbols: list[str],
    fetch_starts: dict[str, date],
    end: date,
    config: dict[str, Any],
    *,
    on_progress: Any | None = None,
    session_factory: Any | None = None,
    fetcher: Any | None = None,
) -> tuple[list[pd.DataFrame], list[dict[str, object]], dict[str, str]]:
    make_session = session_factory or baostock_session
    fetch_history = fetcher or fetch_history_frame_baostock
    retries = max(1, int(config.get("data", {}).get("history_session_retries", 2)))
    retry_delay = max(0.0, float(config.get("data", {}).get("history_session_retry_delay_seconds", 1.0)))
    ordered = [str(symbol) for symbol in symbols]
    completed: set[str] = set()
    frames: list[pd.DataFrame] = []
    provenance_rows: list[dict[str, object]] = []
    errors: dict[str, str] = {}
    request_count = 0

    for attempt in range(1, retries + 1):
        pending = [symbol for symbol in ordered if symbol not in completed]
        if not pending:
            break
        try:
            with make_session() as client:
                for symbol in pending:
                    request_count += 1
                    try:
                        normalized, provenance = fetch_history(
                            client, symbol, fetch_starts[symbol], end
                        )
                        issues = price_frame_issues(normalized)
                        if issues:
                            raise ValueError("; ".join(issues))
                        frames.append(normalized)
                        provenance_rows.append(provenance)
                        completed.add(symbol)
                        errors.pop(symbol, None)
                    except Exception as exc:
                        errors[symbol] = f"baostock: {exc}"
                    if on_progress is not None:
                        on_progress(request_count, frames, provenance_rows, len(errors))
        except Exception as exc:
            for symbol in pending:
                if symbol not in completed:
                    errors[symbol] = f"baostock session attempt {attempt}/{retries}: {exc}"

        pending_after = [symbol for symbol in ordered if symbol not in completed]
        if pending_after and attempt < retries:
            print(
                f"BaoStock reconnect {attempt + 1}/{retries}: pending={len(pending_after)}.",
                flush=True,
            )
            if retry_delay > 0:
                time.sleep(retry_delay * attempt)

    final_errors = {
        symbol: errors.get(symbol, "baostock: no result")
        for symbol in ordered
        if symbol not in completed
    }
    return frames, provenance_rows, final_errors


def merge_benchmark_cache(store: DataStore, fetched: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    cached = normalize_benchmark(store.read_frame("benchmark", required=False))
    incoming = normalize_benchmark(fetched)
    cutoff = immutable_candidate_freeze_date(config)
    if cutoff is not None:
        incoming = rows_strictly_after(incoming, cutoff)
    if cached.empty:
        return incoming
    if incoming.empty:
        return cached
    return normalize_benchmark(
        pd.concat([cached, incoming], ignore_index=True)
        .drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["date", "symbol"])
    )


def merge_generated_fundamentals_cache(
    store: DataStore,
    generated: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    incoming = normalize_fundamentals(generated)
    cutoff = immutable_candidate_freeze_date(config)
    if cutoff is None:
        return incoming

    cached = normalize_fundamentals(store.read_frame("fundamentals", required=False))
    if cached.empty:
        raise RuntimeError(
            "Immutable candidate cache has no frozen fundamentals; refusing refresh."
        )
    frozen = cached.loc[
        pd.to_datetime(cached["date"], errors="coerce").dt.date <= cutoff
    ].copy()
    forward = pd.concat(
        [
            rows_strictly_after(cached, cutoff),
            rows_strictly_after(incoming, cutoff),
        ],
        ignore_index=True,
    )
    return normalize_fundamentals(
        pd.concat([frozen, forward], ignore_index=True)
        .drop_duplicates(["date", "symbol"], keep="last")
        .sort_values(["date", "symbol"])
    )

def fetch_start_for_symbol(
    symbol: str,
    base_start: date,
    cached_latest: dict[str, date],
    config: dict[str, Any],
    *,
    cached_earliest: dict[str, date] | None = None,
) -> date:
    earliest = cached_earliest or {}
    if historical_backfill_required(symbol, base_start, earliest, config):
        return base_start
    latest = cached_latest.get(normalize_symbol(symbol))
    if latest is None:
        return base_start
    overlap = int(config.get("data", {}).get("incremental_overlap_days", 20))
    candidate = latest - timedelta(days=max(0, overlap))
    return max(base_start, candidate)

def candidate_incremental_gap_symbols(
    symbols: list[str],
    cached_latest: dict[str, date],
    end: date,
    proven_symbols: set[str] | None = None,
) -> list[str]:
    proven = (
        {normalize_symbol(symbol) for symbol in proven_symbols}
        if proven_symbols is not None
        else None
    )
    return [
        symbol
        for symbol in symbols
        if (
            cached_latest.get(normalize_symbol(symbol), date.min) < end
            or (proven is not None and normalize_symbol(symbol) not in proven)
        )
    ]


def immutable_fetch_start(config: dict[str, Any], start: date) -> date:
    cutoff = immutable_candidate_freeze_date(config)
    if cutoff is None:
        return start
    return max(start, cutoff + timedelta(days=1))

def finalized_market_data_end(config: dict[str, Any], now: datetime | None = None) -> date:
    current = now or datetime.now()
    raw_cutoff = config.get("data", {}).get("same_day_close_available_after", "16:00")
    try:
        hour_text, minute_text = str(raw_cutoff).strip().split(":", 1)
        cutoff = dt_time(int(hour_text), int(minute_text))
    except (TypeError, ValueError):
        cutoff = dt_time(16, 0)
    target = current.date() if current.time() >= cutoff else current.date() - timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target


def normalize_trade_calendar(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=TRADE_CALENDAR_COLUMNS)
    missing = set(TRADE_CALENDAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"trade_calendar missing columns: {sorted(missing)}")
    out = frame[TRADE_CALENDAR_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["is_trading_day"] = out["is_trading_day"].map(
        lambda value: value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes"}
    )
    out["source"] = out["source"].fillna("").astype(str)
    out["retrieved_at"] = out["retrieved_at"].fillna("").astype(str)
    return out.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def resolve_finalized_trading_date(
    store: DataStore,
    config: dict[str, Any],
    target: date,
) -> date:
    if not bool(config.get("data", {}).get("trade_calendar_preflight", True)):
        return target

    benchmark = store.load_benchmark(required=False)
    if not benchmark.empty and "date" in benchmark:
        benchmark_dates = set(pd.to_datetime(benchmark["date"], errors="coerce").dt.date.dropna())
        if target in benchmark_dates:
            return target

    try:
        cached = normalize_trade_calendar(store.read_frame("trade_calendar", required=False))
    except (FileNotFoundError, ValueError):
        cached = pd.DataFrame(columns=TRADE_CALENDAR_COLUMNS)
    covered = not cached.empty and cached["date"].max() >= target
    if not covered:
        start = target - timedelta(days=31)
        try:
            with baostock_session() as client:
                fetched = fetch_trade_calendar_frame_baostock(client, start, target)
            cached = normalize_trade_calendar(pd.concat([cached, fetched], ignore_index=True))
            store.write_frame("trade_calendar", cached)
            covered = not cached.empty and cached["date"].max() >= target
        except Exception as exc:
            print(f"Trade-calendar preflight unavailable; using weekday target {target}. Details: {exc}")
            return target

    if not covered:
        return target
    sessions = cached[(cached["date"] <= target) & cached["is_trading_day"].astype(bool)]
    if sessions.empty:
        return target
    resolved = sessions["date"].max()
    if resolved != target:
        print(f"Trade-calendar preflight: {target} is closed; latest finalized session is {resolved}.")
    return resolved

def immutable_candidate_freeze_date(config: dict[str, Any]) -> date | None:
    if not bool(config.get("data", {}).get("immutable_before_candidate_freeze", False)):
        return None
    candidate_path = str(config.get("validation", {}).get("candidate_path", "") or "").strip()
    if not candidate_path:
        return None
    path = project_path(candidate_path)
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cutoff = pd.to_datetime(manifest.get("freeze_price_end"), errors="coerce")
    return None if pd.isna(cutoff) else cutoff.date()


def rows_strictly_after(frame: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return frame
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    return frame.loc[dates > cutoff].copy()

def merge_fetched_cache(
    store: DataStore,
    fetched_stocks: pd.DataFrame,
    fetched_prices: pd.DataFrame,
    fetched_benchmark: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not config.get("data", {}).get("preserve_cached_history_on_fetch", True):
        benchmark = fetched_benchmark
        if benchmark.empty:
            benchmark = store.load_benchmark(required=False)
        return fetched_stocks, fetched_prices, benchmark

    freeze_cutoff = immutable_candidate_freeze_date(config)
    if freeze_cutoff is not None:
        original_price_rows = len(fetched_prices)
        original_benchmark_rows = len(fetched_benchmark)
        fetched_prices = normalize_prices(fetched_prices)
        fetched_benchmark = normalize_benchmark(fetched_benchmark)
        fetched_prices = rows_strictly_after(fetched_prices, freeze_cutoff)
        fetched_benchmark = rows_strictly_after(fetched_benchmark, freeze_cutoff)
        print(
            f"Candidate cache is immutable through {freeze_cutoff}: "
            f"accepted prices={len(fetched_prices)}/{original_price_rows}, "
            f"benchmark={len(fetched_benchmark)}/{original_benchmark_rows}."
        )
    cached_stocks, cached_prices, cached_benchmark = load_cached_market_frames(store)
    if freeze_cutoff is not None:
        if cached_stocks.empty:
            raise RuntimeError(
                "Immutable candidate cache has no frozen stock catalog; refusing refresh."
            )
        stocks = cached_stocks.copy()
    else:
        stocks = merge_cache_frame(cached_stocks, fetched_stocks, ["symbol"], ["symbol"])
    prices = merge_cache_frame(cached_prices, fetched_prices, ["date", "symbol"], ["date", "symbol"])
    benchmark = merge_cache_frame(cached_benchmark, fetched_benchmark, ["date"], ["date"])
    if len(prices) > len(fetched_prices):
        print(f"Merged fetched prices with cached history: new_rows={len(fetched_prices)}, total_rows={len(prices)}.")
    if fetched_benchmark.empty and not cached_benchmark.empty:
        print("Benchmark source unavailable; preserved cached benchmark history.")
    return stocks, prices, benchmark


def load_cached_market_frames(store: DataStore) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    try:
        stocks = normalize_stocks(store.read_frame("stocks", required=False))
    except Exception:
        stocks = pd.DataFrame(columns=STOCK_COLUMNS)
    try:
        prices = normalize_prices(store.read_frame("prices", required=False))
    except Exception:
        prices = pd.DataFrame(columns=PRICE_COLUMNS)
    try:
        benchmark = normalize_benchmark(store.read_frame("benchmark", required=False))
    except Exception:
        benchmark = pd.DataFrame(columns=BENCHMARK_COLUMNS)
    return stocks, prices, benchmark


def merge_cache_frame(existing: pd.DataFrame, fresh: pd.DataFrame, keys: list[str], sort_keys: list[str]) -> pd.DataFrame:
    if existing.empty:
        return fresh.copy()
    if fresh.empty:
        return existing.copy()
    merged = pd.concat([existing, fresh], ignore_index=True)
    return merged.drop_duplicates(keys, keep="last").sort_values(sort_keys).reset_index(drop=True)


def history_backfill_completion_status(
    failures: list[dict[str, object]],
) -> str:
    return "complete" if not failures else "in_progress"


def unfinished_history_symbols(
    symbols: list[str],
    price_frames: list[pd.DataFrame],
) -> list[str]:
    completed = {
        normalize_symbol(frame["symbol"].iloc[0])
        for frame in price_frames
        if not frame.empty and "symbol" in frame
    }
    return [
        normalize_symbol(symbol)
        for symbol in symbols
        if normalize_symbol(symbol) not in completed
    ]


def classify_history_batch_source_status(
    attempted_symbols: list[str],
    successful_symbols: set[str],
    errors: dict[str, str],
) -> str:
    attempted = {normalize_symbol(symbol) for symbol in attempted_symbols}
    successful = {normalize_symbol(symbol) for symbol in successful_symbols} & attempted
    if not attempted:
        return "not_attempted"
    if successful == attempted:
        return "ok"
    if successful:
        return "degraded"
    unsuccessful = attempted - successful
    error_texts = [str(errors.get(symbol, "") or "").lower() for symbol in unsuccessful]
    if error_texts and all("baostock session attempt" in value for value in error_texts):
        return "source_unavailable"
    return "failed"


def build_history_backfill_progress(
    symbols: list[str],
    price_frames: list[pd.DataFrame],
    failures: list[dict[str, object]],
    resumed_symbols: set[str],
    deferred_symbols: list[str],
    start: date,
    end: date,
    request_budget: int,
    *,
    status: str,
    source_status: str = "unknown",
    attempted_symbols: int = 0,
    successful_symbols: int = 0,
) -> pd.DataFrame:
    requested = {normalize_symbol(symbol) for symbol in symbols}
    completed = requested - set(unfinished_history_symbols(symbols, price_frames))
    return pd.DataFrame(
        [
            {
                "status": status,
                "requested_symbols": len(requested),
                "completed_symbols": len(completed),
                "remaining_symbols": max(0, len(requested - completed)),
                "resumed_symbols": len(resumed_symbols),
                "deferred_symbols": len({normalize_symbol(value) for value in deferred_symbols}),
                "failed_items": len(failures),
                "request_budget": request_budget,
                "source_status": str(source_status or "unknown"),
                "attempted_symbols": max(int(attempted_symbols), 0),
                "successful_symbols": max(int(successful_symbols), 0),
                "requested_start": start,
                "requested_end": end,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        ]
    )

def build_fetch_summary(
    symbols: list[str],
    price_frames: list[pd.DataFrame],
    failures: list[dict[str, object]],
    merged_prices: pd.DataFrame,
    fallback_history_count: int,
    fetch_starts: dict[str, date] | None = None,
    incremental: bool = True,
    stage: str = "complete",
    historical_backfill_symbols: list[str] | None = None,
    attempted_symbols: list[str] | None = None,
    formal_provenance: pd.DataFrame | None = None,
) -> pd.DataFrame:
    refreshed_symbols = sorted(
        {
            normalize_symbol(frame["symbol"].iloc[0])
            for frame in price_frames
            if not frame.empty and "symbol" in frame
        }
    )
    if formal_provenance is None:
        fetched_symbols = refreshed_symbols
    else:
        requested_set = {normalize_symbol(symbol) for symbol in symbols}
        fetched_symbols = sorted(
            set(formal_provenance.get("symbol", pd.Series(dtype=str)).map(normalize_symbol))
            & requested_set
        )
    latest = max(merged_prices["date"]) if not merged_prices.empty and "date" in merged_prices else ""
    starts = list((fetch_starts or {}).values())
    return pd.DataFrame(
        [
            {
                "requested_symbols": len(symbols),
                "fetched_symbols": len(fetched_symbols),
                "attempted_symbols": len(attempted_symbols if attempted_symbols is not None else symbols),
                "refreshed_symbols": len(refreshed_symbols),
                "failed_items": len(failures),
                "fallback_history_symbols": fallback_history_count,
                "historical_backfill_symbols": len(set(historical_backfill_symbols or [])),
                "merged_price_rows": len(merged_prices),
                "merged_latest_date": latest,
                "preserved_failed_symbols": max(0, len(symbols) - len(fetched_symbols)),
                "stage": stage,
                "fetch_mode": "incremental" if incremental else "full_history",
                "min_fetch_start": min(starts) if starts else "",
                "max_fetch_start": max(starts) if starts else "",
            }
        ]
    )
def max_symbols_from_config(config: dict[str, Any]) -> int:
    return int(config.get("data", {}).get("max_symbols", 800))


def benchmark_membership_only_fetch(config: dict[str, Any]) -> bool:
    return bool(config.get("data", {}).get("fetch_benchmark_membership_only", False))


def ensure_benchmark_membership_fetch_prerequisites(
    store: DataStore,
    config: dict[str, Any],
    ak: Any,
) -> list[Path]:
    if not benchmark_membership_only_fetch(config):
        return []
    if benchmark_membership_fetch_symbols(store, config):
        return []

    paths: list[Path] = []
    benchmark = store.load_benchmark(required=False)
    if benchmark.empty:
        benchmark = normalize_benchmark(fetch_benchmark_akshare(ak, config))
        if benchmark.empty and bool(
            config.get("data", {}).get("benchmark_fallback_to_baostock", True)
        ):
            benchmark_cfg = config.get("benchmark", {})
            start = pd.to_datetime(config["backtest"]["start"]).date()
            end = finalized_market_data_end(config)
            configured_end = config.get("backtest", {}).get("end")
            if configured_end:
                end = min(end, pd.to_datetime(configured_end).date())
            with baostock_session() as client:
                benchmark = normalize_benchmark(
                    fetch_benchmark_frame_baostock(
                        client,
                        str(benchmark_cfg.get("symbol", "000300")),
                        str(benchmark_cfg.get("name", "000300")),
                        start,
                        end,
                    )
                )
        if benchmark.empty:
            raise RuntimeError(
                "Point-in-time membership cold start requires benchmark history, "
                "but all configured benchmark sources failed."
            )
        paths.append(store.write_frame("benchmark", benchmark))

    from .index_membership import refresh_benchmark_membership

    refreshed = refresh_benchmark_membership(config)
    paths.extend(refreshed.values())
    if not benchmark_membership_fetch_symbols(store, config):
        raise RuntimeError(
            "Point-in-time membership refresh completed without usable research-period symbols."
        )
    return paths


def benchmark_membership_fetch_symbols(store: DataStore, config: dict[str, Any]) -> list[str]:
    policy = config.get("strategy", {}).get("benchmark_membership", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return []
    membership = store.read_frame("index_membership", required=False)
    if membership.empty or not {"snapshot_date", "symbol"}.issubset(membership.columns):
        return []
    membership = membership.copy()
    membership["snapshot_date"] = pd.to_datetime(membership["snapshot_date"], errors="coerce")
    index_symbol = canonical_index_symbol(policy.get("index_symbol", "") or "")
    if index_symbol and "index_symbol" in membership.columns:
        membership = membership[
            membership["index_symbol"].map(canonical_index_symbol) == index_symbol
        ]
    membership = membership.dropna(subset=["snapshot_date"])
    if membership.empty:
        return []
    if benchmark_membership_only_fetch(config):
        start_value = config.get("backtest", {}).get("start")
        end_value = config.get("backtest", {}).get("end")
        if start_value:
            membership = membership[
                membership["snapshot_date"] >= pd.to_datetime(start_value)
            ]
        if end_value:
            membership = membership[
                membership["snapshot_date"] <= pd.to_datetime(end_value)
            ]
    else:
        latest_snapshot = membership["snapshot_date"].max()
        membership = membership[membership["snapshot_date"] == latest_snapshot]
    return (
        membership["symbol"]
        .map(normalize_symbol)
        .drop_duplicates()
        .sort_values()
        .tolist()
    )


HISTORY_AKSHARE_SOURCES = (
    "stock_zh_a_hist",
    "stock_zh_a_daily",
    "stock_zh_a_hist_tx",
)


def fetch_history_source_frame(
    ak: Any,
    source: str,
    symbol: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    if source == "stock_zh_a_hist":
        return ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_text,
            end_date=end_text,
            adjust="qfq",
        )
    if source == "stock_zh_a_daily":
        return ak.stock_zh_a_daily(
            symbol=tx_symbol(symbol),
            start_date=start_text,
            end_date=end_text,
            adjust="qfq",
        )
    if source == "stock_zh_a_hist_tx":
        return ak.stock_zh_a_hist_tx(
            symbol=tx_symbol(symbol),
            start_date=start_text,
            end_date=end_text,
            adjust="qfq",
        )
    raise ValueError(f"unsupported history source: {source}")


def fetch_history_frame(
    ak: Any,
    symbol: str,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, str]:
    errors = []
    for source in HISTORY_AKSHARE_SOURCES:
        try:
            hist = fetch_history_source_frame(ak, source, symbol, start, end)
            if not hist.empty:
                return hist, source
            errors.append(f"{source}: empty")
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    raise RuntimeError("; ".join(errors))


def fetch_symbol_history(
    ak: Any,
    symbol: str,
    start: date,
    end: date,
    retries: int = 1,
) -> tuple[pd.DataFrame, dict[str, object]]:
    errors: list[str] = []
    reported_amount_sources = {"stock_zh_a_hist", "stock_zh_a_daily"}
    for attempt in range(1, max(1, retries) + 1):
        for requested_source in HISTORY_AKSHARE_SOURCES:
            source = requested_source
            try:
                hist = fetch_history_source_frame(
                    ak, requested_source, symbol, start, end
                )
                if hist.empty:
                    raise ValueError("empty history")
                normalized = (
                    history_from_akshare(hist, symbol)
                    if requested_source in reported_amount_sources
                    else history_from_tx(hist, symbol)
                )
                issues = price_frame_issues(normalized)
                if issues and requested_source == "stock_zh_a_hist_tx":
                    normalized = repair_tx_history_with_hfq(
                        ak, symbol, start, end, normalized
                    )
                    source = "stock_zh_a_hist_tx_hfq_rebased"
                    issues = price_frame_issues(normalized)
                if issues:
                    raise ValueError("; ".join(issues))
                return normalized, {
                    "symbol": str(symbol),
                    "source": source,
                    "first_date": normalized["date"].min(),
                    "last_date": normalized["date"].max(),
                    "rows": len(normalized),
                    "amount_quality": (
                        "reported"
                        if requested_source in reported_amount_sources
                        else "estimated_close_x_volume"
                    ),
                }
            except Exception as exc:
                errors.append(f"attempt {attempt} {requested_source}: {exc}")
        if attempt < retries:
            time.sleep(min(2.0, 0.5 * attempt))
    raise RuntimeError("; ".join(errors))

def tx_symbol(symbol: str) -> str:
    clean = normalize_symbol(symbol)
    prefix = "sh" if clean.startswith(("600", "601", "603", "605", "688")) else "sz"
    return f"{prefix}{clean}"


def fetch_stock_spot(ak: Any, retries: int = 3) -> pd.DataFrame:
    errors = []
    for attempt in range(1, max(1, retries) + 1):
        for func_name in ["stock_zh_a_spot_em", "stock_zh_a_spot"]:
            func = getattr(ak, func_name, None)
            if func is None:
                continue
            try:
                spot = func()
            except Exception as exc:
                errors.append(f"attempt {attempt} {func_name}: {exc}")
                continue
            if not spot.empty:
                if func_name != "stock_zh_a_spot_em":
                    print(f"AkShare primary spot source failed; using fallback {func_name}.")
                return spot
            errors.append(f"attempt {attempt} {func_name}: empty")
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise RuntimeError("; ".join(errors) if errors else "no AkShare spot source available")


def cached_universe_symbols(store: DataStore, config: dict[str, Any]) -> tuple[pd.DataFrame, list[str]] | None:
    try:
        stocks, prices, _ = store.load_market_data()
    except FileNotFoundError:
        return None
    if stocks.empty:
        return None
    stocks = stocks.copy()
    stocks["symbol"] = stocks["symbol"].astype(str).map(normalize_symbol)
    eligible = stocks[
        stocks["board"].isin(config["universe"]["boards"])
        & stocks["symbol"].map(is_supported_stock_symbol)
        & ~stocks["is_st"].astype(bool)
    ].copy()
    if eligible.empty:
        return None
    if benchmark_membership_only_fetch(config):
        return stocks, ensure_required_fetch_symbols([], store, config)
    max_symbols = max_symbols_from_config(config)
    if prices.empty:
        symbols = eligible["symbol"].astype(str).drop_duplicates().head(max_symbols).tolist()
        return stocks, ensure_required_fetch_symbols(symbols, store, config)
    latest_amount = (
        prices.sort_values(["symbol", "date"])
        .groupby("symbol", as_index=False)
        .tail(int(config.get("universe", {}).get("liquidity_lookback", 20)))
        .groupby("symbol")["amount"]
        .mean()
        .reset_index()
    )
    ranked = eligible.merge(latest_amount, how="left", on="symbol")
    ranked["amount"] = pd.to_numeric(ranked["amount"], errors="coerce").fillna(0.0)
    symbols = ranked.sort_values("amount", ascending=False)["symbol"].astype(str).drop_duplicates().head(max_symbols).tolist()
    return stocks, ensure_required_fetch_symbols(symbols, store, config)


def ensure_required_fetch_symbols(symbols: list[str], store: DataStore, config: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in list(symbols) + required_fetch_symbols(store, config):
        clean = normalize_symbol(symbol)
        if not clean or clean in seen or not is_supported_stock_symbol(clean):
            continue
        ordered.append(clean)
        seen.add(clean)
    return ordered


def required_fetch_symbols(store: DataStore, config: dict[str, Any]) -> list[str]:
    configured = []
    for section in [config.get("data", {}), config.get("universe", {})]:
        value = section.get("required_symbols", []) if isinstance(section, dict) else []
        if isinstance(value, str):
            configured.extend(part.strip() for part in value.split(","))
        else:
            configured.extend(value or [])

    membership_symbols = benchmark_membership_fetch_symbols(store, config)
    account_symbols: list[str] = []
    if bool(config.get("data", {}).get("include_account_positions_on_fetch", True)):
        state_db = getattr(store, "state_db", None)
        try:
            if state_db is not None and Path(state_db).exists():
                with sqlite3.connect(state_db) as conn:
                    rows = conn.execute("select symbol from positions where quantity > 0").fetchall()
                account_symbols = [row[0] for row in rows]
        except sqlite3.Error:
            account_symbols = []

    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in list(configured) + membership_symbols + account_symbols:
        clean = normalize_symbol(symbol)
        if clean and clean not in seen and is_supported_stock_symbol(clean):
            ordered.append(clean)
            seen.add(clean)
    return ordered


@contextmanager
def akshare_network_context(config: dict[str, Any]):
    if not config.get("data", {}).get("disable_requests_proxy", True):
        yield
        return

    import requests

    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    old_env = {key: os.environ.get(key) for key in proxy_keys}
    original_request = requests.sessions.Session.request
    original_socket_timeout = socket.getdefaulttimeout()
    timeout_seconds = float(config.get("data", {}).get("request_timeout_seconds", 20))

    def request_without_proxy(self, method, url, **kwargs):
        self.trust_env = False
        kwargs["proxies"] = {}
        if timeout_seconds > 0 and kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout_seconds
        return original_request(self, method, url, **kwargs)

    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        if timeout_seconds > 0:
            socket.setdefaulttimeout(timeout_seconds)
        requests.sessions.Session.request = request_without_proxy
        yield
    finally:
        requests.sessions.Session.request = original_request
        socket.setdefaulttimeout(original_socket_timeout)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def stocks_from_spot(spot: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["symbol"] = pick_column(spot, ["代码", "symbol", "code"], 1).map(normalize_symbol)
    out["name"] = pick_column(spot, ["名称", "name"], 2).astype(str)
    out["board"] = out["symbol"].map(infer_board)
    out["industry"] = "unknown"
    out["list_date"] = "2000-01-01"
    out["is_st"] = out["name"].str.contains("ST", case=False, na=False)
    return out[STOCK_COLUMNS]


def is_supported_stock_symbol(symbol: str) -> bool:
    return symbol.startswith(("000", "001", "002", "003", "300", "301", "302", "600", "601", "603", "605", "688"))


def select_fetch_symbols(spot: pd.DataFrame, config: dict[str, Any]) -> pd.Series:
    stocks = stocks_from_spot(spot)
    sort_amount = pd.to_numeric(pick_column(spot, ["成交额", "amount"], None), errors="coerce").fillna(0)
    ranked = stocks.assign(sort_amount=sort_amount)
    return (
        ranked[
            ranked["board"].isin(config["universe"]["boards"])
            & ~ranked["is_st"]
            & (ranked["sort_amount"] > 0)
        ]
        .sort_values("sort_amount", ascending=False)["symbol"]
        .drop_duplicates()
    )


def history_from_akshare(hist: pd.DataFrame, symbol: str) -> pd.DataFrame:
    out = pd.DataFrame()
    out["date"] = pick_column(hist, ["日期", "date"], 0)
    out["open"] = pick_column(hist, ["开盘", "open"], 1)
    out["high"] = pick_column(hist, ["最高", "high"], 2)
    out["low"] = pick_column(hist, ["最低", "low"], 3)
    out["close"] = pick_column(hist, ["收盘", "close"], 4)
    out["volume"] = pick_column(hist, ["成交量", "volume"], 5)
    out["amount"] = pick_column(hist, ["成交额", "amount"], 6)
    out["symbol"] = symbol
    return out[PRICE_COLUMNS]


def history_from_tx(hist: pd.DataFrame, symbol: str) -> pd.DataFrame:
    out = pd.DataFrame()
    out["date"] = pick_column(hist, ["date", "日期"], 0)
    out["open"] = pick_column(hist, ["open", "开盘"], 1)
    out["close"] = pick_column(hist, ["close", "收盘"], 2)
    out["high"] = pick_column(hist, ["high", "最高"], 3)
    out["low"] = pick_column(hist, ["low", "最低"], 4)
    raw_volume = pd.to_numeric(pick_column(hist, ["amount", "成交量", "volume"], 5), errors="coerce").fillna(0)
    # Tencent reports STAR Market volume in shares and other supported boards in lots.
    volume_multiplier = 1 if normalize_symbol(symbol).startswith("688") else 100
    out["volume"] = raw_volume * volume_multiplier
    out["amount"] = out["volume"] * pd.to_numeric(out["close"], errors="coerce").fillna(0)
    out["symbol"] = symbol
    return out[PRICE_COLUMNS]


def history_overlap_quality(
    candidate: pd.DataFrame,
    reference: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[str, object], list[str]]:
    metrics: dict[str, object] = {
        "overlap_status": "not_available",
        "overlap_rows": 0,
        "overlap_close_median_relative_error": None,
        "overlap_volume_median_relative_error": None,
        "overlap_amount_median_relative_error": None,
    }
    if candidate.empty:
        return {**metrics, "overlap_status": "invalid_candidate_scope"}, [
            "candidate history is empty"
        ]
    if reference.empty:
        return metrics, ["overlap reference is unavailable"]
    symbols = candidate["symbol"].map(normalize_symbol).dropna().unique()
    if len(symbols) != 1:
        return {**metrics, "overlap_status": "invalid_candidate_scope"}, [
            "candidate history must contain exactly one symbol"
        ]
    symbol = str(symbols[0])
    baseline = reference[reference["symbol"].map(normalize_symbol).eq(symbol)].copy()
    if baseline.empty:
        return metrics, [f"overlap reference has no rows for {symbol}"]
    left = normalize_prices(candidate)
    right = normalize_prices(baseline)
    joined = left.merge(right, on=["date", "symbol"], suffixes=("_new", "_reference"))
    tail_rows = max(1, int(config.get("data", {}).get("history_overlap_tail_rows", 60)))
    joined = joined.sort_values("date").tail(tail_rows)
    metrics["overlap_rows"] = len(joined)
    minimum_rows = max(1, int(config.get("data", {}).get("history_overlap_min_rows", 20)))
    if len(joined) < minimum_rows:
        metrics["overlap_status"] = "insufficient"
        return metrics, [f"overlap rows={len(joined)} below {minimum_rows}"]

    thresholds = {
        "close": float(config.get("data", {}).get("history_overlap_max_close_median_relative_error", 0.01)),
        "volume": float(config.get("data", {}).get("history_overlap_max_volume_median_relative_error", 0.01)),
        "amount": float(config.get("data", {}).get("history_overlap_max_amount_median_relative_error", 0.10)),
    }
    issues: list[str] = []
    for column, threshold in thresholds.items():
        incoming = pd.to_numeric(joined[f"{column}_new"], errors="coerce")
        existing = pd.to_numeric(joined[f"{column}_reference"], errors="coerce")
        usable = incoming.notna() & existing.notna() & (existing.abs() > 1e-12)
        if not usable.any():
            issues.append(f"{column} overlap has no comparable rows")
            continue
        relative = (incoming[usable] - existing[usable]).abs() / existing[usable].abs()
        median_error = float(relative.median())
        metrics[f"overlap_{column}_median_relative_error"] = median_error
        if median_error > threshold:
            issues.append(
                f"{column} median relative error={median_error:.6f} exceeds {threshold:.6f}"
            )
    metrics["overlap_status"] = "failed" if issues else "passed"
    return metrics, issues


def history_alternate_overlap_quality(
    ak: Any,
    symbol: str,
    candidate: pd.DataFrame,
    source: str,
    start: date,
    end: date,
    config: dict[str, Any],
) -> tuple[dict[str, object], list[str]]:
    metrics: dict[str, object] = {
        "alternate_overlap_status": "not_available",
        "alternate_overlap_source": "",
        "alternate_overlap_rows": 0,
        "alternate_overlap_close_median_relative_error": None,
        "alternate_overlap_volume_median_relative_error": None,
        "alternate_overlap_amount_median_relative_error": None,
    }
    verifier_sources = (
        ("stock_zh_a_hist_tx",)
        if source == "stock_zh_a_daily"
        else ("stock_zh_a_daily",)
    )
    errors: list[str] = []
    for verifier_source in verifier_sources:
        try:
            raw = fetch_history_source_frame(
                ak, verifier_source, symbol, start, end
            )
            if raw.empty:
                raise ValueError("empty history")
            verifier = (
                history_from_tx(raw, symbol)
                if verifier_source == "stock_zh_a_hist_tx"
                else history_from_akshare(raw, symbol)
            )
            structural_issues = price_frame_issues(verifier)
            if structural_issues:
                raise ValueError("; ".join(structural_issues))
            overlap, issues = history_overlap_quality(candidate, verifier, config)
            metrics.update(
                {
                    "alternate_overlap_status": overlap["overlap_status"],
                    "alternate_overlap_source": verifier_source,
                    "alternate_overlap_rows": overlap["overlap_rows"],
                    "alternate_overlap_close_median_relative_error": overlap[
                        "overlap_close_median_relative_error"
                    ],
                    "alternate_overlap_volume_median_relative_error": overlap[
                        "overlap_volume_median_relative_error"
                    ],
                    "alternate_overlap_amount_median_relative_error": overlap[
                        "overlap_amount_median_relative_error"
                    ],
                }
            )
            if not issues:
                return metrics, []
            errors.extend(f"{verifier_source}: {issue}" for issue in issues)
        except Exception as exc:
            errors.append(f"{verifier_source}: {exc}")
    metrics["alternate_overlap_status"] = "failed"
    return metrics, errors or ["no independent alternate source was available"]


def history_start_coverage_quality(
    ak: Any,
    symbol: str,
    candidate: pd.DataFrame,
    source: str,
    expected_start: date,
    end: date,
    config: dict[str, Any],
) -> tuple[dict[str, object], list[str]]:
    normalized_candidate = normalize_prices(candidate)
    observed_start = (
        normalized_candidate["date"].min() if not normalized_candidate.empty else None
    )
    metrics: dict[str, object] = {
        "start_coverage_status": "invalid_candidate",
        "start_coverage_expected_date": expected_start,
        "start_coverage_observed_date": observed_start,
        "start_coverage_verifier_source": "",
        "start_coverage_verifier_first_date": None,
    }
    if observed_start is None:
        return metrics, ["history has no observed start date"]
    tolerance = max(
        0,
        int(config.get("data", {}).get("history_backfill_tolerance_days", 31)),
    )
    if observed_start <= expected_start + timedelta(days=tolerance):
        metrics["start_coverage_status"] = "within_tolerance"
        return metrics, []

    verifiers: list[
        tuple[
            str,
            Callable[[], pd.DataFrame],
            Callable[[pd.DataFrame, str], pd.DataFrame],
        ]
    ] = []
    start_text = expected_start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    if source != "stock_zh_a_daily":
        verifiers.append(
            (
                "stock_zh_a_daily",
                lambda: ak.stock_zh_a_daily(
                    symbol=tx_symbol(symbol),
                    start_date=start_text,
                    end_date=end_text,
                    adjust="qfq",
                ),
                history_from_akshare,
            )
        )
    if source not in {"stock_zh_a_hist_tx", "stock_zh_a_hist_tx_hfq_rebased"}:
        verifiers.append(
            (
                "stock_zh_a_hist_tx",
                lambda: ak.stock_zh_a_hist_tx(
                    symbol=tx_symbol(symbol),
                    start_date=start_text,
                    end_date=end_text,
                    adjust="qfq",
                ),
                history_from_tx,
            )
        )

    verifier_errors: list[str] = []
    for verifier_source, fetch, normalize in verifiers:
        try:
            raw = fetch()
            verified = normalize_prices(normalize(raw, symbol))
            verifier_first = verified["date"].min() if not verified.empty else None
            if verifier_first is None:
                verifier_errors.append(f"{verifier_source}: empty")
                continue
            if verifier_first == observed_start:
                metrics.update(
                    {
                        "start_coverage_status": "cross_source_confirmed_gap",
                        "start_coverage_verifier_source": verifier_source,
                        "start_coverage_verifier_first_date": verifier_first,
                    }
                )
                return metrics, []
            verifier_errors.append(
                f"{verifier_source}: first date {verifier_first} != {observed_start}"
            )
        except Exception as exc:
            verifier_errors.append(f"{verifier_source}: {exc}")
    metrics["start_coverage_status"] = "unverified_gap"
    detail = f" ({'; '.join(verifier_errors)})" if verifier_errors else ""
    return metrics, [
        f"history starts {observed_start} after expected {expected_start} plus "
        f"{tolerance} days without matching alternate source{detail}"
    ]


def validate_history_start_coverage_batch(
    ak: Any,
    frames: list[pd.DataFrame],
    provenance_rows: list[dict[str, object]],
    fetch_starts: dict[str, date],
    end: date,
    config: dict[str, Any],
) -> tuple[list[pd.DataFrame], list[dict[str, object]], dict[str, str]]:
    provenance_by_symbol = {
        normalize_symbol(row.get("symbol", "")): dict(row)
        for row in provenance_rows
        if normalize_symbol(row.get("symbol", ""))
    }
    validated_frames: list[pd.DataFrame] = []
    validated_provenance: list[dict[str, object]] = []
    errors: dict[str, str] = {}
    for frame in frames:
        candidate = normalize_prices(frame)
        if candidate.empty or "symbol" not in candidate:
            continue
        symbol = normalize_symbol(candidate["symbol"].iloc[0])
        provenance = provenance_by_symbol.get(symbol)
        expected_start = fetch_starts.get(symbol)
        if provenance is None or expected_start is None:
            errors[symbol] = "missing history provenance or expected start"
            continue
        metrics, issues = history_start_coverage_quality(
            ak,
            symbol,
            candidate,
            str(provenance.get("source", "")),
            expected_start,
            end,
            config,
        )
        provenance.update(metrics)
        if issues:
            errors[symbol] = "; ".join(issues)
            continue
        validated_frames.append(candidate)
        validated_provenance.append(provenance)
    return validated_frames, validated_provenance, errors


def price_frame_issues(frame: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    if frame.empty:
        return ["empty normalized history"]
    price_columns = ["open", "high", "low", "close"]
    prices = frame[price_columns].apply(pd.to_numeric, errors="coerce")
    if prices.isna().any().any():
        issues.append("missing or non-numeric OHLC")
    nonpositive = int((prices <= 0).any(axis=1).sum())
    if nonpositive:
        issues.append(f"nonpositive OHLC rows={nonpositive}")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    amount = pd.to_numeric(frame["amount"], errors="coerce")
    if volume.isna().any() or amount.isna().any():
        issues.append("missing or non-numeric volume/amount")
    if (volume < 0).any() or (amount < 0).any():
        issues.append("negative volume/amount")
    return issues


def repair_tx_history_with_hfq(
    ak: Any,
    symbol: str,
    start: date,
    end: date,
    qfq_frame: pd.DataFrame,
) -> pd.DataFrame:
    valid_reference = pd.to_numeric(qfq_frame.get("close"), errors="coerce")
    valid_reference = valid_reference[valid_reference > 0]
    if valid_reference.empty:
        raise ValueError("Tencent qfq history has no positive reference close")
    reference_close = float(valid_reference.iloc[-1])
    hist = ak.stock_zh_a_hist_tx(
        symbol=tx_symbol(symbol),
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="hfq",
    )
    repaired = history_from_tx(hist, symbol)
    hfq_close = pd.to_numeric(repaired["close"], errors="coerce")
    valid_hfq = hfq_close[hfq_close > 0]
    if valid_hfq.empty:
        raise ValueError("Tencent hfq history has no positive close")
    scale = reference_close / float(valid_hfq.iloc[-1])
    for column in ["open", "high", "low", "close"]:
        repaired[column] = pd.to_numeric(repaired[column], errors="coerce") * scale
    repaired["amount"] = (
        pd.to_numeric(repaired["volume"], errors="coerce").fillna(0)
        * pd.to_numeric(repaired["close"], errors="coerce").fillna(0)
    )
    return repaired


def enrich_stock_metadata(ak: Any, stocks: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    out = normalize_stocks(stocks).copy().set_index("symbol", drop=False)
    for idx, symbol in enumerate(symbols, start=1):
        enrich_symbol_metadata(ak, out, symbol)
        if idx % 50 == 0:
            print(f"Enriched metadata {idx}/{len(symbols)} symbols")
    return out.reset_index(drop=True)[STOCK_COLUMNS]


def refresh_cached_metadata(store: DataStore, config: dict[str, Any], max_symbols: int | None = None) -> list[Path]:
    stocks, prices, _ = store.load_market_data()
    if stocks.empty:
        raise RuntimeError("本地股票列表为空，无法刷新元数据。请先运行 fetch。")
    symbols = metadata_refresh_symbols(stocks, prices, config, max_symbols=max_symbols)
    if not symbols:
        raise RuntimeError("没有可刷新的股票代码。")
    source = str(config.get("data", {}).get("metadata_source", "baostock")).lower()
    failures: list[dict[str, str]] = []
    if source == "baostock":
        try:
            enriched, provenance = enrich_stock_metadata_baostock(stocks, symbols)
            path = store.write_frame("stocks", normalize_stocks(enriched))
            provenance_path = store.write_frame("stock_metadata_provenance", provenance)
            print(f"BaoStock metadata source: ok ({len(provenance)} symbols).")
            return [path, provenance_path]
        except Exception as exc:
            failures.append({"symbol": "metadata", "error": f"baostock: {exc}"})
            if not config.get("data", {}).get("metadata_fallback_to_akshare", True):
                raise RuntimeError(f"BaoStock metadata refresh failed: {exc}") from exc
            print(f"BaoStock metadata source failed; using AkShare fallback. Details: {exc}")
    try:
        import akshare as ak  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("AkShare is not installed. Install ashare-quant[data] before refreshing metadata.") from exc
    with akshare_network_context(config):
        enriched, ak_failures = enrich_stock_metadata_checkpointed(ak, store, stocks, symbols, config)
    failures.extend(ak_failures)
    path = store.write_frame("stocks", normalize_stocks(enriched))
    paths = [path]
    if failures:
        paths.append(store.write_frame("metadata_failures", pd.DataFrame(failures)))
    return paths


def reusable_metadata_cache(
    store: DataStore,
    stocks: pd.DataFrame,
    symbols: list[str],
    config: dict[str, Any],
    *,
    now: pd.Timestamp | None = None,
) -> tuple[bool, str]:
    settings = config.get("data", {}) if isinstance(config.get("data"), dict) else {}
    max_age_days = float(settings.get("metadata_cache_max_age_days", 30))
    min_coverage = float(settings.get("metadata_cache_min_coverage", 0.95))
    if max_age_days <= 0 or not 0 < min_coverage <= 1:
        return False, "disabled_or_invalid_policy"
    wanted = {normalize_symbol(value) for value in symbols if normalize_symbol(value)}
    if not wanted:
        return True, "symbols=0;coverage=1.0000"
    provenance = store.read_frame("stock_metadata_provenance", required=False)
    if provenance.empty or not {"symbol", "retrieved_at"}.issubset(provenance.columns):
        return False, "provenance_missing"
    checked_at = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if checked_at.tzinfo is None:
        checked_at = checked_at.tz_localize("UTC")
    else:
        checked_at = checked_at.tz_convert("UTC")
    cutoff = checked_at - pd.Timedelta(days=max_age_days)
    retrieved = pd.to_datetime(provenance["retrieved_at"], errors="coerce", utc=True)
    fresh_symbols = set(
        provenance.loc[retrieved.ge(cutoff), "symbol"].map(normalize_symbol).astype(str)
    )
    normalized = normalize_stocks(stocks)
    normalized = normalized[normalized["symbol"].astype(str).isin(wanted)].copy()
    list_dates = pd.to_datetime(normalized["list_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    industries = normalized["industry"].fillna("").astype(str).str.strip().str.casefold()
    complete_symbols = set(
        normalized.loc[
            list_dates.notna()
            & list_dates.ne("2000-01-01")
            & industries.ne("")
            & industries.ne("unknown"),
            "symbol",
        ].astype(str)
    )
    covered = wanted & fresh_symbols & complete_symbols
    coverage = len(covered) / len(wanted)
    value = (
        f"symbols={len(wanted)};covered={len(covered)};coverage={coverage:.4f};"
        f"max_age_days={max_age_days:g}"
    )
    return coverage >= min_coverage, value

def metadata_refresh_symbols(
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    config: dict[str, Any],
    max_symbols: int | None = None,
) -> list[str]:
    stocks = normalize_stocks(stocks).copy()
    eligible = stocks[
        stocks["board"].isin(config.get("universe", {}).get("boards", ["main", "gem", "star"]))
        & stocks["symbol"].map(is_supported_stock_symbol)
        & ~stocks["is_st"].astype(bool)
    ].copy()
    if bool(config.get("data", {}).get("metadata_priced_only", True)) and not prices.empty:
        priced_symbols = set(normalize_prices(prices)["symbol"].astype(str))
        eligible = eligible[eligible["symbol"].astype(str).isin(priced_symbols)].copy()
        if eligible.empty:
            return []
    missing_metadata = (eligible["industry"].astype(str).str.lower() == "unknown") | (
        pd.to_datetime(eligible["list_date"]).dt.date.astype(str) == "2000-01-01"
    )
    eligible = eligible.assign(metadata_missing=missing_metadata)
    if prices.empty:
        ranked = eligible.assign(amount=0.0)
    else:
        latest_amount = (
            normalize_prices(prices)
            .sort_values(["symbol", "date"])
            .groupby("symbol", as_index=False)
            .tail(int(config.get("universe", {}).get("liquidity_lookback", 20)))
            .groupby("symbol")["amount"]
            .mean()
            .reset_index()
        )
        ranked = eligible.merge(latest_amount, how="left", on="symbol")
        ranked["amount"] = pd.to_numeric(ranked["amount"], errors="coerce").fillna(0.0)
    symbols = (
        ranked.sort_values(["metadata_missing", "amount"], ascending=[False, False])["symbol"]
        .astype(str)
        .drop_duplicates()
        .tolist()
    )
    limit = max_symbols if max_symbols is not None else int(config.get("data", {}).get("max_symbols", len(symbols)))
    return symbols[: max(0, limit)]


def enrich_stock_metadata_checkpointed(
    ak: Any,
    store: DataStore,
    stocks: pd.DataFrame,
    symbols: list[str],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    out = normalize_stocks(stocks).copy().set_index("symbol", drop=False)
    failures: list[dict[str, str]] = []
    checkpoint_every = int(config.get("data", {}).get("metadata_checkpoint_every", 25))
    workers = max(1, int(config.get("data", {}).get("metadata_workers", 1)))

    def handle_result(idx: int, symbol: str, fields: dict[str, str], errors: list[str]) -> None:
        apply_metadata_fields(out, symbol, fields)
        if errors and not has_complete_stock_metadata(out, symbol):
            failures.append({"symbol": symbol, "error": "; ".join(errors)})
        if checkpoint_every > 0 and idx % checkpoint_every == 0:
            store.write_frame("stocks", normalize_stocks(out.reset_index(drop=True)[STOCK_COLUMNS]))
            if failures:
                store.write_frame("metadata_failures", pd.DataFrame(failures))
            print(f"Refreshed metadata {idx}/{len(symbols)} symbols")

    if workers == 1 or len(symbols) <= 1:
        for idx, symbol in enumerate(symbols, start=1):
            fields, errors = fetch_symbol_metadata_fields(ak, symbol)
            handle_result(idx, symbol, fields, errors)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(fetch_symbol_metadata_fields, ak, symbol): symbol for symbol in symbols}
            for idx, future in enumerate(as_completed(future_map), start=1):
                symbol = future_map[future]
                try:
                    fields, errors = future.result()
                except Exception as exc:
                    fields, errors = {}, [str(exc)]
                handle_result(idx, symbol, fields, errors)
    return out.reset_index(drop=True)[STOCK_COLUMNS], failures


def enrich_symbol_metadata(ak: Any, stocks_by_symbol: pd.DataFrame, symbol: str) -> list[str]:
    fields, errors = fetch_symbol_metadata_fields(ak, symbol)
    apply_metadata_fields(stocks_by_symbol, symbol, fields)
    return errors


def fetch_symbol_metadata_fields(ak: Any, symbol: str) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    errors: list[str] = []
    profile_func = getattr(ak, "stock_profile_cninfo", None)
    if profile_func is None:
        errors.append("stock_profile_cninfo: unavailable")
    else:
        try:
            profile = profile_func(symbol=symbol)
            fields.update(metadata_from_cninfo_profile(profile))
        except Exception as exc:
            errors.append(f"stock_profile_cninfo: {exc}")

    if has_complete_metadata_fields(fields):
        return fields, errors

    try:
        info = ak.stock_individual_info_em(symbol=symbol)
        fields.update({key: value for key, value in metadata_from_stock_info(info).items() if value})
    except Exception as exc:
        errors.append(f"stock_individual_info_em: {exc}")
    return fields, errors


def has_complete_metadata_fields(fields: dict[str, str]) -> bool:
    industry = normalize_industry(fields.get("industry", "")).lower()
    list_date = str(fields.get("list_date", "")).strip()
    return bool(industry and industry != "unknown" and list_date and list_date != "2000-01-01")


def has_complete_stock_metadata(stocks_by_symbol: pd.DataFrame, symbol: str) -> bool:
    if symbol not in stocks_by_symbol.index:
        return False
    row = stocks_by_symbol.loc[symbol]
    industry = normalize_industry(row.get("industry", "")).lower()
    list_date = str(row.get("list_date", "")).strip()
    return bool(industry and industry != "unknown" and list_date and list_date != "2000-01-01")


def apply_metadata_fields(stocks_by_symbol: pd.DataFrame, symbol: str, fields: dict[str, str]) -> bool:
    if symbol not in stocks_by_symbol.index:
        return False
    changed = False
    industry = normalize_industry(fields.get("industry", ""))
    if industry != "unknown":
        stocks_by_symbol.loc[symbol, "industry"] = industry
        changed = True
    list_date = str(fields.get("list_date", "")).strip()
    if list_date:
        parsed = pd.to_datetime(list_date, errors="coerce")
        if pd.notna(parsed):
            stocks_by_symbol.loc[symbol, "list_date"] = parsed.date().isoformat()
            changed = True
    return changed


def apply_stock_info(stocks_by_symbol: pd.DataFrame, symbol: str, info: pd.DataFrame) -> bool:
    if symbol not in stocks_by_symbol.index or info.empty:
        return False
    return apply_metadata_fields(stocks_by_symbol, symbol, metadata_from_stock_info(info))


def apply_stock_profile_cninfo(stocks_by_symbol: pd.DataFrame, symbol: str, profile: pd.DataFrame) -> bool:
    if symbol not in stocks_by_symbol.index or profile.empty:
        return False
    return apply_metadata_fields(stocks_by_symbol, symbol, metadata_from_cninfo_profile(profile))


def metadata_from_stock_info(info: pd.DataFrame) -> dict[str, str]:
    if info.empty:
        return {}
    values = dict(zip(info["item"].astype(str), info["value"]))
    fields: dict[str, str] = {}
    industry = first_present(values, ["行业", "琛屼笟"])
    if industry is not None and pd.notna(industry) and str(industry).strip():
        fields["industry"] = str(industry).strip()
    list_date = first_present(values, ["上市时间", "涓婂競鏃堕棿"])
    if list_date is not None and pd.notna(list_date):
        parsed = pd.to_datetime(str(list_date), format="%Y%m%d", errors="coerce")
        if pd.notna(parsed):
            fields["list_date"] = parsed.date().isoformat()
    return fields


def metadata_from_cninfo_profile(profile: pd.DataFrame) -> dict[str, str]:
    if profile.empty:
        return {}
    row = profile.iloc[0]
    fields: dict[str, str] = {}
    industry = first_present(row, ["所属行业"])
    if industry is not None and pd.notna(industry) and str(industry).strip():
        fields["industry"] = str(industry).strip()
    list_date = first_present(row, ["上市日期", "涓婂競鏃ユ湡"])
    if list_date is not None and pd.notna(list_date):
        parsed = pd.to_datetime(str(list_date), errors="coerce")
        if pd.notna(parsed):
            fields["list_date"] = parsed.date().isoformat()
    return fields


def first_present(values: Any, keys: list[str]) -> object | None:
    for key in keys:
        try:
            value = values.get(key)
        except AttributeError:
            value = values[key] if key in values else None
        if value is not None and pd.notna(value):
            return value
    return None


def pick_column(frame: pd.DataFrame, candidates: list[str], fallback_index: int | None) -> pd.Series:
    for candidate in candidates:
        if candidate in frame.columns:
            return frame[candidate]
    if fallback_index is not None and fallback_index < len(frame.columns):
        return frame.iloc[:, fallback_index]
    return pd.Series([None] * len(frame), index=frame.index)


def fetch_benchmark_akshare(ak: Any, config: dict[str, Any]) -> pd.DataFrame:
    benchmark_cfg = config.get("benchmark", {})
    symbol = canonical_index_symbol(benchmark_cfg.get("symbol", "000300"))
    name = str(benchmark_cfg.get("name", symbol))
    start = pd.to_datetime(config["backtest"]["start"]).date()
    end_value = config["backtest"].get("end")
    start = immutable_fetch_start(config, start)
    end = pd.to_datetime(end_value).date() if end_value else date.today()
    raw = pd.DataFrame()
    try:
        raw = ak.index_zh_a_hist(symbol=symbol, period="daily", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    except Exception:
        raw = pd.DataFrame()
    if not raw.empty:
        renamed = raw.rename(columns={"日期": "date", "收盘": "close"})
        if "date" not in renamed.columns:
            renamed["date"] = raw.iloc[:, 0]
        if "close" not in renamed.columns:
            close_candidates = [col for col in raw.columns if str(col).lower() in {"close", "收盘"}]
            renamed["close"] = raw[close_candidates[0]] if close_candidates else raw.iloc[:, 2]
        renamed["symbol"] = symbol
        renamed["name"] = name
        return renamed[BENCHMARK_COLUMNS]

    try:
        fallback_symbol = index_symbol_for_sina(symbol)
        fallback = ak.stock_zh_index_daily(symbol=fallback_symbol)
    except Exception:
        return pd.DataFrame(columns=BENCHMARK_COLUMNS)
    if fallback.empty:
        return pd.DataFrame(columns=BENCHMARK_COLUMNS)
    fallback = fallback.rename(columns={"date": "date", "close": "close"})
    fallback["date"] = pd.to_datetime(fallback["date"]).dt.date
    fallback = fallback[(fallback["date"] >= start) & (fallback["date"] <= end)].copy()
    fallback["symbol"] = symbol
    fallback["name"] = name
    print(f"AkShare primary benchmark source failed; using fallback stock_zh_index_daily({fallback_symbol}).")
    return fallback[BENCHMARK_COLUMNS]


def index_symbol_for_sina(symbol: str) -> str:
    return provider_index_symbol(symbol, "sina")


def infer_board(symbol: str) -> str:
    if symbol.startswith(("8", "4", "920")):
        return "bse"
    if symbol.startswith("688"):
        return "star"
    if symbol.startswith(("300", "301", "302")):
        return "gem"
    if symbol.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        return "main"
    return "other"


def make_sample_data(store: DataStore) -> list[Path]:
    start = date(2023, 1, 2)
    days = business_days(start, date(2024, 12, 31))
    industries = ["bank", "software", "medicine", "consumer", "energy", "equipment"]
    symbols = [
        ("600001", "样例银行", "main"),
        ("600002", "样例能源", "main"),
        ("000001", "样例平安", "main"),
        ("000002", "样例地产", "main"),
        ("300001", "样例科技", "gem"),
        ("300002", "样例医疗", "gem"),
        ("301001", "样例消费", "gem"),
        ("688001", "样例芯片", "star"),
        ("688002", "样例装备", "star"),
        ("600003", "ST样例", "main"),
        ("830001", "北交样例", "bse"),
        ("600004", "新股样例", "main"),
    ]
    stocks = []
    prices = []
    fundamentals = []
    for idx, (symbol, name, board) in enumerate(symbols):
        list_dt = date(2022, 1, 1) if symbol != "600004" else date(2024, 12, 1)
        industry = industries[idx % len(industries)]
        stocks.append(
            {
                "symbol": symbol,
                "name": name,
                "board": board,
                "industry": industry,
                "list_date": list_dt.isoformat(),
                "is_st": "ST" in name,
            }
        )
        base = 8 + idx * 1.7
        trend = 0.0003 * (idx % 5 - 1)
        for t, dt in enumerate(days):
            seasonal = math.sin((t + idx) / 19) * 0.018
            close = max(2.0, base * (1 + trend * t + seasonal + 0.0008 * (t % 13)))
            prev = close / (1 + 0.002 + seasonal / 20)
            open_price = max(1.5, prev * (1 + 0.001 * ((idx % 3) - 1)))
            high = max(open_price, close) * 1.012
            low = min(open_price, close) * 0.988
            volume = 2_000_000 + idx * 200_000 + (t % 20) * 10_000
            amount = volume * close
            prices.append(
                {
                    "date": dt.isoformat(),
                    "symbol": symbol,
                    "open": round(open_price, 3),
                    "high": round(high, 3),
                    "low": round(low, 3),
                    "close": round(close, 3),
                    "volume": volume,
                    "amount": round(amount, 2),
                }
            )
            if t % 21 == 0:
                fundamentals.append(
                    {
                        "date": dt.isoformat(),
                        "symbol": symbol,
                        "pe": 8 + (idx % 7) * 4 + (t % 5),
                        "pb": 0.8 + (idx % 6) * 0.45,
                        "roe": 0.05 + (idx % 5) * 0.025,
                    }
                )

    return [
        store.write_frame("stocks", normalize_stocks(pd.DataFrame(stocks))),
        store.write_frame("prices", normalize_prices(pd.DataFrame(prices))),
        store.write_frame("fundamentals", normalize_fundamentals(pd.DataFrame(fundamentals))),
        store.write_frame("benchmark", make_sample_benchmark(days)),
    ]


def make_sample_benchmark(days: list[date]) -> pd.DataFrame:
    rows = []
    close = 4000.0
    for t, dt in enumerate(days):
        close *= 1 + 0.00015 + math.sin(t / 23) * 0.001
        rows.append({"date": dt, "symbol": "000300", "name": "沪深300", "close": round(close, 3)})
    return normalize_benchmark(pd.DataFrame(rows))


def make_proxy_benchmark(prices: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=BENCHMARK_COLUMNS)
    benchmark_cfg = config.get("benchmark", {})
    symbol = str(benchmark_cfg.get("symbol", "proxy"))
    name = str(benchmark_cfg.get("name", "等权股票池基准"))
    close = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    returns = close.pct_change().mean(axis=1).fillna(0.0)
    nav = (1 + returns).cumprod() * 1000
    out = pd.DataFrame({"date": nav.index, "symbol": symbol, "name": name, "close": nav.values})
    return normalize_benchmark(out)


def make_fundamentals_from_prices(prices: pd.DataFrame) -> pd.DataFrame:
    latest_dates = pd.to_datetime(prices["date"]).dt.date
    unique_dates = sorted(set(latest_dates))[::21]
    rows = []
    symbols = sorted(prices["symbol"].astype(str).unique())
    for idx, symbol in enumerate(symbols):
        for dt in unique_dates:
            rows.append(
                {
                    "date": dt,
                    "symbol": symbol,
                    "pe": 10 + idx % 20,
                    "pb": 1 + (idx % 8) * 0.3,
                    "roe": 0.06 + (idx % 10) * 0.01,
                }
            )
    return pd.DataFrame(rows)


def business_days(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days
