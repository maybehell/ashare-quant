from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .config import project_path
from .data import DataStore, cached_universe_symbols, committed_fetch_market_scope
from .validation import cache_age_days, complete_market_dates, filter_complete_market_data

SOURCE_HEALTH_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "command"]


def build_data_source_health(config: dict[str, Any], store: DataStore | None = None, today: date | None = None) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, command: str = "") -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": "" if value is None else value,
                "threshold": threshold,
                "severity": severity,
                "next_action": next_action,
                "command": command,
            }
        )

    paths = config.get("paths", {})
    if store is None and ("cache_dir" not in paths or "state_db" not in paths):
        add(
            "data_store_config",
            False,
            "missing cache_dir/state_db",
            "cache_dir and state_db configured",
            "blocker",
            "当前配置不足以读取本地行情缓存；使用完整配置运行。",
            "python -m ashare_quant doctor",
        )
        return pd.DataFrame(rows, columns=SOURCE_HEALTH_COLUMNS)
    store = store or DataStore.from_config(config)

    try:
        stocks, prices, _fundamentals = store.load_market_data()
    except FileNotFoundError as exc:
        add(
            "cached_market_data",
            False,
            str(exc),
            "stocks/prices cache present",
            "blocker",
            "先生成样例数据或成功刷新一次行情缓存。",
            "python -m ashare_quant fetch --sample",
        )
        return pd.DataFrame(rows, columns=SOURCE_HEALTH_COLUMNS)

    benchmark = store.load_benchmark(required=False)
    scoped_prices, fetch_scope_passed, fetch_scope_value = committed_fetch_market_scope(store, prices)
    analysis_prices = scoped_prices if fetch_scope_passed else prices
    filtered_prices, filtered_benchmark = filter_complete_market_data(analysis_prices, benchmark, config)
    complete_dates = complete_market_dates(analysis_prices, benchmark, config)
    complete_end = complete_dates[-1] if complete_dates else None
    raw_latest, raw_count, raw_ratio = raw_latest_coverage(analysis_prices)
    partial = read_cached_frame(store, "prices_partial")
    try:
        partial_latest, partial_count, _partial_ratio = raw_latest_coverage(partial)
    except Exception:
        partial_latest, partial_count = "", None
    min_ratio = float(config.get("data", {}).get("min_daily_price_coverage_ratio", 0.9))
    max_age = int(config.get("data", {}).get("max_cache_age_days", 10))
    age = cache_age_days(filtered_prices, today=today)
    min_symbols = int(config.get("data", {}).get("min_fetch_success_symbols", 20))
    partial_newer = bool(partial_latest and raw_latest and partial_latest > raw_latest and (partial_count or 0) >= min_symbols)

    add(
        "cached_market_data",
        not stocks.empty and not prices.empty,
        f"stocks={len(stocks)}, prices={len(prices)}, fetch_scope={fetch_scope_value}",
        "non-empty stocks/prices",
        "ok" if not stocks.empty and not prices.empty else "blocker",
        "本地行情缓存存在。" if not stocks.empty and not prices.empty else "重新刷新行情缓存。",
        "python -m ashare_quant fetch --cached-universe --max-symbols 800",
    )
    add(
        "complete_market_date",
        complete_end is not None,
        "" if complete_end is None else complete_end,
        "present",
        "ok" if complete_end is not None else "blocker",
        "完整行情日可用于回测、paper 和 shadow。" if complete_end is not None else "最新原始行情日覆盖不足，需重新刷新行情。",
        "python -m ashare_quant fetch --cached-universe --max-symbols 800",
    )
    add(
        "price_cache_age_days",
        age is not None and age <= max_age,
        "" if age is None else age,
        max_age,
        "ok" if age is not None and age <= max_age else "blocker",
        "行情足够新。" if age is not None and age <= max_age else "行情已过期，刷新成功前不要生成实盘辅助订单。",
        "python -m ashare_quant fetch --cached-universe --max-symbols 800",
    )
    add(
        "raw_latest_coverage",
        raw_ratio is not None and raw_ratio >= min_ratio,
        "" if raw_ratio is None else round(raw_ratio, 4),
        min_ratio,
        "ok" if raw_ratio is not None and raw_ratio >= min_ratio else "warning",
        (
            "原始最新日覆盖率达到完整行情阈值。"
            if raw_ratio is not None and raw_ratio >= min_ratio
            else f"原始最新日 {raw_latest or 'missing'} 只有 {raw_count or 0} 只股票，低于完整行情阈值；系统会继续使用上一完整行情日。"
        ),
        "python -m ashare_quant fetch --cached-universe --max-symbols 800",
    )
    add(
        "partial_recovery_available",
        not partial_newer,
        "" if not partial_latest else f"latest={partial_latest}, symbols={partial_count or 0}",
        f"newer partial >= {min_symbols} symbols",
        "warning" if partial_newer else "ok",
        (
            "发现 prices_partial 比正式行情缓存更新；先合并本地 partial，避免重复抓取已成功下载的行情。"
            if partial_newer
            else "没有发现比正式缓存更新的可恢复 partial 行情。"
        ),
        "python -m ashare_quant recover-partial" if partial_newer else "",
    )
    add(
        "benchmark_aligned",
        filtered_benchmark is not None and not filtered_benchmark.empty and complete_end is not None,
        benchmark_end_value(filtered_benchmark),
        "benchmark covers complete date",
        "ok" if filtered_benchmark is not None and not filtered_benchmark.empty and complete_end is not None else "blocker",
        "基准已对齐完整行情日。" if filtered_benchmark is not None and not filtered_benchmark.empty and complete_end is not None else "先刷新基准；基准缺失时不能做相对收益门禁。",
        "python -m ashare_quant fetch --benchmark-only",
    )

    cached = cached_universe_symbols(store, config)
    cached_count = len(cached[1]) if cached is not None else 0
    max_symbols = int(config.get("data", {}).get("max_symbols", 800))
    add(
        "cached_universe_available",
        cached_count >= min_symbols,
        cached_count,
        min_symbols,
        "ok" if cached_count >= min_symbols else "blocker",
        "可绕过股票列表数据源，直接用本地股票池刷新历史行情。" if cached_count >= min_symbols else "本地股票池太小，先跑一次普通 fetch 或样例数据。",
        f"python -m ashare_quant fetch --cached-universe --max-symbols {max_symbols}",
    )

    failures = read_cached_frame(store, "fetch_failures")
    failure_count = len(failures)
    failure_value = failure_summary(failures)
    add(
        "recent_fetch_failures",
        failure_count == 0,
        failure_value,
        "0",
        "ok" if failure_count == 0 else "warning",
        "最近一次刷新没有记录失败股票。" if failure_count == 0 else "最近一次刷新存在失败股票；若刷新过慢或失败率高，先降低 max-symbols 或稍后重试。",
        "python -m ashare_quant fetch --cached-universe --max-symbols 100",
    )
    provenance = read_cached_frame(store, "fetch_history_provenance")
    max_estimated_ratio = float(config.get("data", {}).get("max_estimated_amount_source_ratio", 0.25))
    if provenance.empty or "amount_quality" not in provenance.columns:
        add(
            "history_source_provenance",
            False,
            "missing",
            "per-symbol provenance present",
            "warning",
            "\u5f53\u524d\u7f13\u5b58\u65e9\u4e8e\u9010\u80a1\u7968\u6765\u6e90\u53f0\u8d26\u529f\u80fd\uff1b\u4e0b\u6b21\u6210\u529f\u5237\u65b0\u540e\u4f1a\u81ea\u52a8\u8865\u9f50\uff0c\u4e0d\u5f71\u54cd\u53ea\u8bfb\u8fd0\u884c\u3002",
            "python -m ashare_quant fetch --cached-universe --max-symbols 100",
        )
    else:
        estimated = provenance["amount_quality"].astype(str).eq("estimated_close_x_volume")
        estimated_ratio = float(estimated.mean()) if len(provenance) else 0.0
        source_counts = provenance.get("source", pd.Series(dtype=str)).astype(str).value_counts().to_dict()
        passed = estimated_ratio <= max_estimated_ratio
        add(
            "history_source_provenance",
            passed,
            f"estimated_amount_ratio={estimated_ratio:.4f}; sources={source_counts}",
            f"estimated_amount_ratio <= {max_estimated_ratio:.2f}",
            "ok" if passed else "warning",
            (
                "\u9010\u80a1\u7968\u6765\u6e90\u53f0\u8d26\u5b8c\u6574\uff0c\u4f30\u7b97\u6210\u4ea4\u989d\u5360\u6bd4\u5728\u9608\u503c\u5185\u3002"
                if passed
                else "\u5907\u7528\u6e90\u7528\u6536\u76d8\u4ef7\u4e58\u6210\u4ea4\u91cf\u4f30\u7b97\u6210\u4ea4\u989d\uff1b\u53ef\u7528\u4e8e\u884c\u60c5\u8fde\u7eed\u6027\uff0c\u4f46\u72ec\u7acb\u7b56\u7565\u9a8c\u8bc1\u5e94\u505a\u6765\u6e90\u654f\u611f\u6027\u590d\u6838\u3002"
            ),
        )

    return pd.DataFrame(rows, columns=SOURCE_HEALTH_COLUMNS)


def write_data_source_health(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "data_source_health.csv"
    build_data_source_health(config).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def read_cached_frame(store: DataStore, name: str) -> pd.DataFrame:
    try:
        return store.read_frame(name, required=False)
    except Exception:
        return pd.DataFrame()


def raw_latest_coverage(prices: pd.DataFrame) -> tuple[object, int | None, float | None]:
    if prices.empty or "date" not in prices or "symbol" not in prices:
        return "", None, None
    data = prices.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce").dt.date
    daily_counts = data.dropna(subset=["date"]).groupby("date")["symbol"].nunique()
    if daily_counts.empty:
        return "", None, None
    latest = daily_counts.index.max()
    latest_count = int(daily_counts.loc[latest])
    max_count = int(daily_counts.max())
    ratio = latest_count / max_count if max_count else None
    return latest, latest_count, ratio


def benchmark_end_value(benchmark: pd.DataFrame | None) -> object:
    if benchmark is None or benchmark.empty or "date" not in benchmark:
        return ""
    return max(benchmark["date"])


def failure_summary(failures: pd.DataFrame) -> str:
    if failures.empty:
        return "0"
    if "stage" in failures:
        counts = failures["stage"].astype(str).value_counts().to_dict()
        parts = [f"{stage}:{count}" for stage, count in counts.items()]
        return f"{len(failures)} ({', '.join(parts)})"
    return str(len(failures))


def health_failed_rows(health: pd.DataFrame) -> pd.DataFrame:
    if health.empty or "passed" not in health:
        return pd.DataFrame(columns=SOURCE_HEALTH_COLUMNS)
    passed = health["passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    return health[~passed].copy()