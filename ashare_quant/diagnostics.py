from __future__ import annotations

from typing import Any

import pandas as pd

from .validation import cache_age_days, complete_market_dates
from .candidate import live_strategy_hash
from .fundamental_policy import fundamental_factor_policy
from .selection_bias import build_strategy_selection_bias


def data_quality_report(
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
    raw_prices: pd.DataFrame | None = None,
    raw_benchmark: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    price_symbols = set(prices["symbol"].astype(str)) if not prices.empty else set()
    stock_symbols = set(stocks["symbol"].astype(str)) if not stocks.empty else set()
    priced_stocks = stocks[stocks["symbol"].astype(str).isin(price_symbols)].copy() if not stocks.empty else pd.DataFrame()
    universe_cfg = config.get("universe", {})
    universe_scope = str(universe_cfg.get("scope", "curated")).lower()

    def add(item: str, value: object, severity: str, note: str) -> None:
        rows.append({"item": item, "value": value, "severity": severity, "note": note})

    add("universe_scope", universe_scope, "info", "股票池范围；curated 表示只验证当前精选子股票池，不代表全市场 A 股。")
    add("stock_count", len(stocks), "info", "股票列表数量。")
    add("price_symbol_count", len(price_symbols), "info", "有历史行情的股票数量。")
    add("price_rows", len(prices), "info", "历史行情行数。")

    if not prices.empty:
        add("price_start", min(prices["date"]), "info", "行情起始日期。")
        add("price_end", max(prices["date"]), "info", "行情结束日期。")
        age_days = cache_age_days(prices)
        max_age = int(config.get("data", {}).get("max_cache_age_days", 10))
        add(
            "price_cache_age_days",
            age_days if age_days is not None else "",
            "warning" if age_days is not None and age_days > max_age else "ok",
            f"行情缓存距今天的自然日天数；超过 {max_age} 天时不应生成实盘辅助订单。",
        )
    raw_prices = raw_prices if raw_prices is not None else prices
    raw_benchmark = raw_benchmark if raw_benchmark is not None else benchmark
    history_coverage = latest_universe_history_coverage(stocks, raw_prices, config)
    if history_coverage:
        min_history_coverage = float(
            config.get("live_guard", {}).get("min_latest_universe_history_coverage_ratio", 0.9)
        )
        add(
            "latest_universe_history_coverage_ratio",
            round(float(history_coverage["ratio"]), 4),
            "warning" if float(history_coverage["ratio"]) < min_history_coverage else "ok",
            f"最新行情股票池中拥有自上市或回测起点附近历史的比例；低于 {min_history_coverage:.0%} 时存在幸存者偏差风险。",
        )
        add("latest_universe_history_covered_symbols", history_coverage["covered"], "info", "长期历史覆盖标的数。")
        add("latest_universe_history_expected_symbols", history_coverage["expected"], "info", "最新行情日标的数。")
    add_complete_market_diagnostics(add, raw_prices, raw_benchmark, prices, config)

    coverage = len(price_symbols) / len(stock_symbols) if stock_symbols else 0.0
    min_full_coverage = float(config.get("live_guard", {}).get("min_full_market_priced_stock_coverage", 0.7))
    coverage_severity = "warning" if universe_scope == "full_market" and coverage < min_full_coverage else "info"
    add(
        "priced_stock_coverage",
        round(coverage, 4),
        coverage_severity,
        "有行情股票占股票列表比例；精选股票池可低于全市场覆盖率，但报告结论只能解释为子股票池策略。",
    )

    if universe_scope == "full_market":
        add(
            "stock_pool_coverage_interpretation",
            "full_market",
            "warning" if coverage < min_full_coverage else "ok",
            f"当前配置声明为全市场股票池；有行情股票覆盖率低于 {min_full_coverage:.0%} 时，不能视为全市场回测证据。",
        )
    else:
        add(
            "stock_pool_coverage_interpretation",
            "curated_subset",
            "info",
            "当前配置声明为精选股票池；priced_stock_coverage 低不代表策略失效，但结论只适用于当前有行情的精选子股票池。",
        )

    if not priced_stocks.empty:
        unknown_industry_ratio = (priced_stocks["industry"].astype(str).str.lower() == "unknown").mean()
        add(
            "unknown_industry_ratio",
            round(float(unknown_industry_ratio), 4),
            "warning" if unknown_industry_ratio > 0.2 else "ok",
            "行业缺失比例；过高会降低行业暴露和行业上限风控可信度。",
        )
        imputed_listing_ratio = (pd.to_datetime(priced_stocks["list_date"]).dt.date.astype(str) == "2000-01-01").mean()
        add(
            "imputed_list_date_ratio",
            round(float(imputed_listing_ratio), 4),
            "warning" if imputed_listing_ratio > 0.2 else "ok",
            "上市日期占位比例；过高说明新股、次新股和退市过滤不可靠。",
        )

    add("fundamental_rows", len(fundamentals), "info", "基本面数据行数。")
    if fundamentals.empty:
        add("fundamental_status", "missing", "warning", "估值/质量类策略需要真实历史基本面；当前默认策略不依赖基本面。")

    add("benchmark_rows", len(benchmark), "ok" if len(benchmark) else "warning", "基准指数行情行数。")
    if not benchmark.empty:
        add("benchmark_start", min(benchmark["date"]), "info", "基准起始日期。")
        add("benchmark_end", max(benchmark["date"]), "info", "基准结束日期。")

    add("configured_max_symbols", int(config.get("data", {}).get("max_symbols", 0)), "info", "fetch 时最多抓取的股票数量。")
    add("strategy_name", config.get("strategy", {}).get("name", ""), "info", "当前策略配置名称。")
    if config.get("risk_overlay", {}).get("enabled", False):
        add("risk_overlay_enabled", True, "info", "已启用大盘趋势/波动风险覆盖层。")
    if config.get("validation", {}).get("optimized_on_current_cache", False):
        add(
            "optimized_on_current_cache",
            True,
            "warning",
            "当前默认参数曾参考本缓存回测结果选择；需冻结候选参数，并用冻结后新增行情完成前向验证后才可视为实盘级证据。",
        )
    else:
        add("optimized_on_current_cache", False, "ok", "当前配置未标记为基于本缓存调参。")
    fundamental_policy = fundamental_factor_policy(config)
    active_factors = ",".join(fundamental_policy.active_factors)
    add("fundamental_source", fundamental_policy.source, "info", "\u57fa\u672c\u9762\u7f13\u5b58\u7684\u6570\u636e\u6765\u6e90\u58f0\u660e\u3002")
    add(
        "fundamental_point_in_time",
        fundamental_policy.point_in_time,
        "ok" if fundamental_policy.point_in_time else "info",
        "\u662f\u5426\u4fdd\u8bc1\u6bcf\u4e2a\u5386\u53f2\u622a\u9762\u53ea\u4f7f\u7528\u5f53\u65f6\u5df2\u516c\u5f00\u7684\u6570\u636e\u3002",
    )
    add("fundamental_active_factors", active_factors or "none", "info", "\u5f53\u524d\u7b56\u7565\u4e2d\u975e\u96f6\u7684\u57fa\u672c\u9762\u56e0\u5b50\u3002")
    add("fundamental_factor_max_abs_weight", fundamental_policy.max_abs_weight, "info", "\u57fa\u672c\u9762\u56e0\u5b50\u7684\u6700\u5927\u7edd\u5bf9\u914d\u7f6e\u6743\u91cd\u3002")
    if not fundamental_policy.active:
        status = "inactive"
        severity = "ok"
        note = "\u4f30\u503c\u548c\u8d28\u91cf\u56e0\u5b50\u6743\u91cd\u5747\u4e3a\u96f6\uff1b\u4ee3\u7406\u57fa\u672c\u9762\u4e0d\u4f1a\u53c2\u4e0e\u6253\u5206\u3002"
    elif not fundamental_policy.point_in_time:
        status = "blocked_non_point_in_time"
        severity = "warning"
        note = "\u57fa\u672c\u9762\u56e0\u5b50\u5df2\u542f\u7528\uff0c\u4f46\u6570\u636e\u4e0d\u662f\u65f6\u70b9\u53ef\u7528\u6570\u636e\uff1b\u7cfb\u7edf\u4f1a\u62d2\u7edd\u8fd0\u884c\u3002"
    elif fundamentals.empty:
        status = "blocked_missing"
        severity = "warning"
        note = "\u57fa\u672c\u9762\u56e0\u5b50\u5df2\u542f\u7528\uff0c\u4f46\u57fa\u672c\u9762\u7f13\u5b58\u4e3a\u7a7a\u3002"
    else:
        status = "point_in_time_usable"
        severity = "ok"
        note = "\u57fa\u672c\u9762\u56e0\u5b50\u4f7f\u7528\u5df2\u58f0\u660e\u7684\u65f6\u70b9\u53ef\u7528\u6570\u636e\u3002"
    add("fundamental_factor_status", status, severity, note)
    return pd.DataFrame(rows)


def latest_universe_history_coverage(
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, object]:
    if prices.empty or not {"date", "symbol"}.issubset(prices.columns):
        return {}
    frame = prices[["date", "symbol"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["symbol"] = frame["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
    frame = frame.dropna(subset=["date"])
    if frame.empty:
        return {}
    latest = frame["date"].max()
    latest_symbols = set(frame.loc[frame["date"] == latest, "symbol"])
    if not latest_symbols:
        return {}
    first_dates = frame[frame["symbol"].isin(latest_symbols)].groupby("symbol")["date"].min()
    listing_dates: dict[str, pd.Timestamp] = {}
    if not stocks.empty and {"symbol", "list_date"}.issubset(stocks.columns):
        stock_frame = stocks[["symbol", "list_date"]].copy()
        stock_frame["symbol"] = stock_frame["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
        stock_frame["list_date"] = pd.to_datetime(stock_frame["list_date"], errors="coerce")
        listing_dates = dict(zip(stock_frame["symbol"], stock_frame["list_date"]))
    configured_start = pd.to_datetime(config.get("backtest", {}).get("start"), errors="coerce")
    backtest_start = configured_start if pd.notna(configured_start) else frame["date"].min()
    tolerance_days = max(int(config.get("data", {}).get("history_start_tolerance_days", 45)), 0)
    tolerance = pd.Timedelta(days=tolerance_days)
    covered = 0
    for symbol in latest_symbols:
        listing = listing_dates.get(symbol)
        expected_start = max(backtest_start, listing) if listing is not None and pd.notna(listing) else backtest_start
        first = first_dates.get(symbol)
        if first is not None and pd.notna(first) and first <= expected_start + tolerance:
            covered += 1
    expected = len(latest_symbols)
    return {
        "latest": latest.date().isoformat(),
        "covered": covered,
        "expected": expected,
        "ratio": covered / expected if expected else 0.0,
        "tolerance_days": tolerance_days,
    }


def add_complete_market_diagnostics(
    add,
    raw_prices: pd.DataFrame,
    raw_benchmark: pd.DataFrame,
    filtered_prices: pd.DataFrame,
    config: dict[str, Any],
) -> None:
    if raw_prices.empty or "date" not in raw_prices or "symbol" not in raw_prices:
        return
    raw = raw_prices.copy()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce").dt.date
    daily_counts = raw.dropna(subset=["date"]).groupby("date")["symbol"].nunique()
    if daily_counts.empty:
        return
    max_symbols = int(daily_counts.max())
    latest_raw_date = daily_counts.index.max()
    latest_raw_count = int(daily_counts.loc[latest_raw_date])
    ratio = latest_raw_count / max_symbols if max_symbols else 0.0
    min_ratio = float(config.get("data", {}).get("min_daily_price_coverage_ratio", 0.9))
    complete_dates = complete_market_dates(raw_prices, raw_benchmark, config)
    complete_end = complete_dates[-1] if complete_dates else ""
    filtered_end = max(filtered_prices["date"]) if not filtered_prices.empty and "date" in filtered_prices else ""
    add("raw_price_end", latest_raw_date, "info", "原始缓存中的最新行情日期，可能是部分股票先更新的日期。")
    add("raw_latest_price_symbol_count", latest_raw_count, "info", "原始缓存最新行情日的股票覆盖数量。")
    add(
        "raw_latest_price_coverage_ratio",
        round(float(ratio), 4),
        "warning" if ratio < min_ratio else "ok",
        f"原始缓存最新行情日覆盖率；低于 {min_ratio:.0%} 时不作为完整行情日。",
    )
    add("complete_price_end", complete_end, "ok" if complete_end else "warning", "满足覆盖率和基准约束的最新完整行情日期。")
    if complete_end and latest_raw_date > complete_end:
        add(
            "excluded_partial_price_date",
            latest_raw_date,
            "warning",
            f"原始缓存最新日未满足完整行情日约束，已从回测、paper 和 shadow 中排除；当前完整行情日为 {complete_end}。",
        )
    add("backtest_price_end", filtered_end, "info", "当前报告和门禁实际使用的行情结束日期。")


def robustness_split_report(comparison: pd.DataFrame, split_ratio: float = 0.7, warmup_days: int = 0) -> pd.DataFrame:
    if comparison.empty:
        return pd.DataFrame(columns=["sample", "start", "end", "strategy_return", "benchmark_return", "excess_return", "information_ratio"])
    data = comparison.sort_values("date").reset_index(drop=True)
    if warmup_days > 0 and len(data) > warmup_days + 2:
        data = data.iloc[warmup_days:].reset_index(drop=True)
    split_idx = max(1, min(len(data) - 1, int(len(data) * split_ratio)))
    parts = {
        "in_sample_first_70pct": data.iloc[:split_idx].copy(),
        "out_of_sample_last_30pct": data.iloc[split_idx:].copy(),
        "full_period": data,
    }
    rows = []
    for sample, frame in parts.items():
        if frame.empty:
            continue
        normalized = frame.copy()
        normalized["strategy_nav"] = normalized["strategy_nav"] / normalized["strategy_nav"].iloc[0]
        normalized["benchmark_nav"] = normalized["benchmark_nav"] / normalized["benchmark_nav"].iloc[0]
        metrics = _benchmark_metrics(normalized)
        rows.append(
            {
                "sample": sample,
                "start": normalized["date"].iloc[0],
                "end": normalized["date"].iloc[-1],
                "strategy_return": normalized["strategy_nav"].iloc[-1] - 1,
                "benchmark_return": normalized["benchmark_nav"].iloc[-1] - 1,
                "excess_return": metrics["excess_total_return"],
                "information_ratio": metrics["information_ratio"],
            }
        )
    return pd.DataFrame(rows)


def annual_report(comparison: pd.DataFrame) -> pd.DataFrame:
    if comparison.empty:
        return pd.DataFrame(columns=["year", "strategy_return", "benchmark_return", "excess_return", "max_strategy_drawdown"])
    data = comparison.copy()
    data["date"] = pd.to_datetime(data["date"])
    rows = []
    for year, frame in data.groupby(data["date"].dt.year):
        if frame.empty:
            continue
        strategy = frame["strategy_nav"] / frame["strategy_nav"].iloc[0]
        benchmark = frame["benchmark_nav"] / frame["benchmark_nav"].iloc[0]
        drawdown = strategy / strategy.cummax() - 1
        rows.append(
            {
                "year": int(year),
                "strategy_return": float(strategy.iloc[-1] - 1),
                "benchmark_return": float(benchmark.iloc[-1] - 1),
                "excess_return": float(strategy.iloc[-1] - benchmark.iloc[-1]),
                "max_strategy_drawdown": float(drawdown.min()),
            }
        )
    return pd.DataFrame(rows)


def rolling_validation_report(comparison: pd.DataFrame, window: int = 252, step: int = 21) -> pd.DataFrame:
    columns = [
        "end_date",
        "window_days",
        "strategy_return",
        "benchmark_return",
        "excess_return",
        "max_strategy_drawdown",
        "information_ratio",
    ]
    if comparison.empty or len(comparison) < window:
        return pd.DataFrame(columns=columns)
    data = comparison.sort_values("date").reset_index(drop=True)
    rows = []
    for end in range(window, len(data) + 1, step):
        frame = data.iloc[end - window : end].copy()
        normalized = frame.copy()
        normalized["strategy_nav"] = normalized["strategy_nav"] / normalized["strategy_nav"].iloc[0]
        normalized["benchmark_nav"] = normalized["benchmark_nav"] / normalized["benchmark_nav"].iloc[0]
        strategy = normalized["strategy_nav"]
        drawdown = strategy / strategy.cummax() - 1
        metrics = _benchmark_metrics(normalized)
        rows.append(
            {
                "end_date": normalized["date"].iloc[-1],
                "window_days": window,
                "strategy_return": float(normalized["strategy_nav"].iloc[-1] - 1),
                "benchmark_return": float(normalized["benchmark_nav"].iloc[-1] - 1),
                "excess_return": metrics["excess_total_return"],
                "max_strategy_drawdown": float(drawdown.min()),
                "information_ratio": metrics["information_ratio"],
            }
        )
    return pd.DataFrame(rows, columns=columns)


def regime_validation_report(
    comparison: pd.DataFrame,
    config: dict[str, Any] | None = None,
    warmup_days: int = 0,
) -> pd.DataFrame:
    columns = [
        "regime",
        "description",
        "observations",
        "start",
        "end",
        "strategy_return",
        "benchmark_return",
        "excess_return",
        "avg_daily_excess",
        "win_day_ratio",
        "information_ratio",
        "max_strategy_drawdown",
    ]
    required = {"date", "strategy_nav", "benchmark_nav"}
    if comparison.empty or not required.issubset(comparison.columns):
        return pd.DataFrame(columns=columns)

    config = config or {}
    data = comparison.sort_values("date").reset_index(drop=True).copy()
    if warmup_days > 0 and len(data) > warmup_days + 2:
        data = data.iloc[warmup_days:].reset_index(drop=True)
    if len(data) < 2:
        return pd.DataFrame(columns=columns)

    data["strategy_nav"] = pd.to_numeric(data["strategy_nav"], errors="coerce")
    data["benchmark_nav"] = pd.to_numeric(data["benchmark_nav"], errors="coerce")
    data = data.dropna(subset=["strategy_nav", "benchmark_nav"]).copy()
    if data.empty:
        return pd.DataFrame(columns=columns)

    data["strategy_daily_return"] = (
        pd.to_numeric(data["strategy_return"], errors="coerce")
        if "strategy_return" in data
        else data["strategy_nav"].pct_change()
    ).fillna(0.0)
    data["benchmark_daily_return"] = (
        pd.to_numeric(data["benchmark_return"], errors="coerce")
        if "benchmark_return" in data
        else data["benchmark_nav"].pct_change()
    ).fillna(0.0)
    data["daily_excess"] = data["strategy_daily_return"] - data["benchmark_daily_return"]

    risk_cfg = config.get("risk_overlay", {})
    ma_days = int(risk_cfg.get("benchmark_ma_days", 200))
    vol_days = int(risk_cfg.get("benchmark_vol_days", 60))
    high_volatility_threshold = float(risk_cfg.get("high_volatility_threshold", 0.30))
    benchmark = data["benchmark_nav"]
    benchmark_ma = benchmark.rolling(ma_days).mean().shift(1)
    benchmark_prev = benchmark.shift(1)
    benchmark_vol = data["benchmark_daily_return"].rolling(vol_days).std().shift(1) * (252**0.5)
    data["bull_trend"] = benchmark_prev >= benchmark_ma
    data["bear_trend"] = benchmark_prev < benchmark_ma
    data.loc[benchmark_ma.isna() | benchmark_prev.isna(), ["bull_trend", "bear_trend"]] = False
    data["high_volatility"] = benchmark_vol > high_volatility_threshold
    data["normal_volatility"] = benchmark_vol.notna() & ~data["high_volatility"]

    regimes = [
        ("full_after_warmup", "扣除因子预热后的完整样本", pd.Series(True, index=data.index)),
        ("benchmark_up_days", "基准上涨交易日；收益为条件复合，不是连续持有区间", data["benchmark_daily_return"] > 0),
        ("benchmark_down_days", "基准下跌交易日；收益为条件复合，不是连续持有区间", data["benchmark_daily_return"] < 0),
        ("bull_trend", "上一日基准位于长期均线之上；收益为状态日条件复合", data["bull_trend"]),
        ("bear_trend", "上一日基准位于长期均线之下；收益为状态日条件复合", data["bear_trend"]),
        ("normal_volatility", "上一日基准波动率未超过阈值；收益为状态日条件复合", data["normal_volatility"]),
        ("high_volatility", "上一日基准波动率超过阈值；收益为状态日条件复合", data["high_volatility"]),
        ("bull_high_volatility", "牛市趋势且高波动；收益为状态日条件复合", data["bull_trend"] & data["high_volatility"]),
        ("bear_high_volatility", "熊市趋势且高波动；收益为状态日条件复合", data["bear_trend"] & data["high_volatility"]),
    ]
    rows = [regime_row(data[mask.fillna(False)].copy(), name, description) for name, description, mask in regimes]
    return pd.DataFrame(rows, columns=columns)


def regime_row(frame: pd.DataFrame, name: str, description: str) -> dict[str, object]:
    if frame.empty:
        return {
            "regime": name,
            "description": description,
            "observations": 0,
            "start": "",
            "end": "",
            "strategy_return": "",
            "benchmark_return": "",
            "excess_return": "",
            "avg_daily_excess": "",
            "win_day_ratio": "",
            "information_ratio": "",
            "max_strategy_drawdown": "",
        }
    strategy_returns = pd.to_numeric(frame["strategy_daily_return"], errors="coerce").fillna(0.0)
    benchmark_returns = pd.to_numeric(frame["benchmark_daily_return"], errors="coerce").fillna(0.0)
    daily_excess = strategy_returns - benchmark_returns
    strategy_nav = (1.0 + strategy_returns).cumprod()
    benchmark_nav = (1.0 + benchmark_returns).cumprod()
    drawdown = strategy_nav / strategy_nav.cummax() - 1.0
    tracking_error = float(daily_excess.std(ddof=0) * (252**0.5)) if len(daily_excess) else 0.0
    annual_excess = float(daily_excess.mean() * 252) if len(daily_excess) else 0.0
    return {
        "regime": name,
        "description": description,
        "observations": int(len(frame)),
        "start": frame["date"].iloc[0],
        "end": frame["date"].iloc[-1],
        "strategy_return": float(strategy_nav.iloc[-1] - 1.0),
        "benchmark_return": float(benchmark_nav.iloc[-1] - 1.0),
        "excess_return": float(strategy_nav.iloc[-1] - benchmark_nav.iloc[-1]),
        "avg_daily_excess": float(daily_excess.mean()),
        "win_day_ratio": float((daily_excess > 0).mean()),
        "information_ratio": annual_excess / tracking_error if tracking_error else 0.0,
        "max_strategy_drawdown": float(drawdown.min()),
    }


def strategy_reliability_report(
    comparison: pd.DataFrame,
    robustness: pd.DataFrame,
    rolling: pd.DataFrame,
    walk_forward_summary: pd.DataFrame,
    data_quality: pd.DataFrame,
    config: dict[str, Any],
    regime_validation: pd.DataFrame | None = None,
    excess_bootstrap: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = ["item", "value", "threshold", "passed", "severity", "note"]
    validation = config.get("validation", {})
    live_guard = config.get("live_guard", {})
    rows: list[dict[str, object]] = []

    def add(item: str, value: object, threshold: object, passed: bool, note: str, severity: str | None = None) -> None:
        rows.append(
            {
                "item": item,
                "value": value,
                "threshold": threshold,
                "passed": bool(passed),
                "severity": severity or ("ok" if passed else "warning"),
                "note": note,
            }
        )

    trading_days = len(comparison)
    min_days = int(validation.get("min_backtest_trading_days", 756))
    add(
        "backtest_trading_days",
        trading_days,
        min_days,
        trading_days >= min_days,
        "回测交易日数量；少于约三年交易日时，长期稳定性证据不足。",
    )
    if not comparison.empty and "date" in comparison:
        add("backtest_start", str(comparison["date"].iloc[0]), "info", True, "当前回测起始日期。", "info")
        add("backtest_end", str(comparison["date"].iloc[-1]), "info", True, "当前回测结束日期。", "info")

    total_excess = comparison_total_excess(comparison)
    min_total = float(live_guard.get("min_total_excess_return", validation.get("min_total_excess_return", 0.0)))
    add(
        "total_excess_return",
        total_excess,
        min_total,
        total_excess >= min_total,
        "全区间相对基准的净值超额；长期高于基准至少应在全区间为正。",
    )

    in_sample = sample_excess(robustness, "in_sample_first_70pct")
    min_in_sample = float(validation.get("min_in_sample_excess_return", live_guard.get("min_in_sample_excess_return", 0.0)))
    if in_sample is not None:
        add(
            "in_sample_excess_return",
            in_sample,
            min_in_sample,
            in_sample >= min_in_sample,
            "前 70% 样本超额；如果这里长期跑输，说明收益可能集中在最近行情。",
        )

    out_sample = sample_excess(robustness, "out_of_sample_last_30pct")
    min_out_sample = float(live_guard.get("min_out_of_sample_excess_return", validation.get("min_out_of_sample_excess_return", 0.0)))
    if out_sample is not None:
        add(
            "out_of_sample_excess_return",
            out_sample,
            min_out_sample,
            out_sample >= min_out_sample,
            "最近 30% 样本外超额；用于检测参数在较新行情中是否仍有效。",
        )

    if total_excess > 0 and out_sample is not None and out_sample > 0:
        concentration = out_sample / total_excess
        max_concentration = float(validation.get("max_recent_excess_concentration", 0.85))
        add(
            "recent_excess_concentration",
            concentration,
            max_concentration,
            concentration <= max_concentration,
            "最近 30% 样本外超额占全区间超额的比例；过高说明收益过度依赖近期行情。",
        )

    folds = walk_forward_folds(walk_forward_summary)
    min_folds = int(validation.get("min_walk_forward_folds", live_guard.get("min_walk_forward_folds", 3)))
    if folds is not None:
        add(
            "walk_forward_folds",
            folds,
            min_folds,
            folds >= min_folds,
            "走步验证折数；折数过少时，滚动样本外结论不够稳。",
        )
    wf_avg = summary_value(walk_forward_summary, "avg_test_excess_return")
    min_wf_avg = float(live_guard.get("min_walk_forward_avg_excess_return", validation.get("min_walk_forward_avg_excess_return", 0.0)))
    if wf_avg is not None:
        add(
            "walk_forward_avg_excess_return",
            wf_avg,
            min_wf_avg,
            wf_avg >= min_wf_avg,
            "走步验证平均样本外超额收益。",
        )
    wf_ratio = summary_value(walk_forward_summary, "positive_test_ratio")
    min_wf_ratio = float(live_guard.get("min_walk_forward_positive_ratio", validation.get("min_walk_forward_positive_ratio", 0.6)))
    if wf_ratio is not None:
        add(
            "walk_forward_positive_ratio",
            wf_ratio,
            min_wf_ratio,
            wf_ratio >= min_wf_ratio,
            "走步验证样本外正超额折数比例。",
        )

    if not rolling.empty and "excess_return" in rolling:
        rolling_excess = pd.to_numeric(rolling["excess_return"], errors="coerce").dropna()
        if not rolling_excess.empty:
            positive_ratio = float((rolling_excess > 0).mean())
            min_positive = float(validation.get("min_rolling_positive_ratio", 0.5))
            add(
                "rolling_positive_ratio",
                positive_ratio,
                min_positive,
                positive_ratio >= min_positive,
                "最近一年滚动窗口跑赢基准的比例；用于观察超额收益是否持续。",
            )
            worst = float(rolling_excess.min())
            min_worst = float(validation.get("min_worst_rolling_excess_return", -0.15))
            add(
                "worst_rolling_excess_return",
                worst,
                min_worst,
                worst >= min_worst,
                "最差滚动窗口超额收益；过低说明策略在部分市场阶段失效明显。",
            )

    annual = annual_report(comparison)
    if not annual.empty and "excess_return" in annual:
        annual_excess = pd.to_numeric(annual["excess_return"], errors="coerce").dropna()
        if not annual_excess.empty:
            annual_ratio = float((annual_excess > 0).mean())
            min_annual_ratio = float(validation.get("min_annual_positive_ratio", 0.5))
            add(
                "annual_positive_ratio",
                annual_ratio,
                min_annual_ratio,
                annual_ratio >= min_annual_ratio,
                "年度跑赢基准的比例；用于检查跨年份稳定性。",
            )

    if regime_validation is not None and not regime_validation.empty and {"regime", "observations", "excess_return"}.issubset(regime_validation.columns):
        min_observations = int(validation.get("min_regime_observations", 60))
        core_regimes = {
            "bull_trend",
            "bear_trend",
            "normal_volatility",
            "high_volatility",
        }
        regimes = regime_validation[regime_validation["regime"].astype(str).isin(core_regimes)].copy()
        regimes["observations"] = pd.to_numeric(regimes["observations"], errors="coerce").fillna(0)
        regimes["excess_return"] = pd.to_numeric(regimes["excess_return"], errors="coerce")
        regimes = regimes[(regimes["observations"] >= min_observations) & regimes["excess_return"].notna()]
        if not regimes.empty:
            positive_ratio = float((regimes["excess_return"] > 0).mean())
            min_positive = float(validation.get("min_regime_positive_ratio", 0.5))
            add(
                "regime_positive_ratio",
                positive_ratio,
                min_positive,
                positive_ratio >= min_positive,
                "不同市场状态下跑赢基准的比例；用于检查策略是否只依赖单一行情环境。",
            )
            worst_regime = float(regimes["excess_return"].min())
            min_worst_regime = float(validation.get("min_worst_regime_excess_return", -0.15))
            add(
                "worst_regime_excess_return",
                worst_regime,
                min_worst_regime,
                worst_regime >= min_worst_regime,
                "最差市场状态分段超额收益；过低说明策略在某类行情下失效明显。",
            )

    unknown_industry = data_quality_float(data_quality, "unknown_industry_ratio")
    if unknown_industry is not None:
        max_unknown_industry = float(live_guard.get("max_unknown_industry_ratio", 0.20))
        add(
            "unknown_industry_ratio",
            unknown_industry,
            max_unknown_industry,
            unknown_industry <= max_unknown_industry,
            "\u884c\u4e1a\u5143\u6570\u636e\u7f3a\u5931\u4f1a\u964d\u4f4e\u884c\u4e1a\u4e2d\u6027\u5316\u548c\u96c6\u4e2d\u5ea6\u7ea6\u675f\u7684\u53ef\u4fe1\u5ea6\u3002",
        )

    imputed_list_date = data_quality_float(data_quality, "imputed_list_date_ratio")
    if imputed_list_date is not None:
        max_imputed_list_date = float(live_guard.get("max_imputed_list_date_ratio", 0.20))
        add(
            "imputed_list_date_ratio",
            imputed_list_date,
            max_imputed_list_date,
            imputed_list_date <= max_imputed_list_date,
            "\u4e0a\u5e02\u65e5\u671f\u5360\u4f4d\u8fc7\u591a\u65f6\uff0c\u65b0\u80a1\u8fc7\u6ee4\u548c\u9000\u5e02\u5e78\u5b58\u8005\u504f\u5dee\u65e0\u6cd5\u53ef\u9760\u8bc4\u4f30\u3002",
        )

    universe_scope = data_quality_text(data_quality, "universe_scope").lower()
    priced_coverage = data_quality_float(data_quality, "priced_stock_coverage")
    if universe_scope == "full_market" and priced_coverage is not None:
        min_coverage = float(live_guard.get("min_full_market_priced_stock_coverage", 0.70))
        add(
            "priced_stock_coverage",
            priced_coverage,
            min_coverage,
            priced_coverage >= min_coverage,
            "\u5168\u5e02\u573a\u58f0\u660e\u4e0b\u7684\u884c\u60c5\u8986\u76d6\u5fc5\u987b\u8db3\u591f\uff0c\u5426\u5219\u56de\u6d4b\u53ea\u80fd\u4ee3\u8868\u5b50\u80a1\u7968\u6c60\u3002",
        )

    optimized = data_quality_text(data_quality, "optimized_on_current_cache").lower()
    optimized_flag = optimized in {"true", "1", "yes"}
    add(
        "optimized_on_current_cache",
        optimized_flag,
        False,
        not optimized_flag,
        "当前参数是否参考过本缓存回测结果；为 True 时必须依赖冻结后前向验证解除调参污染。",
        "warning" if optimized_flag else "ok",
    )

    bootstrap_probability_passed: bool | None = None
    bootstrap_lower_passed: bool | None = None
    if excess_bootstrap is not None and not excess_bootstrap.empty:
        bootstrap_row = excess_bootstrap.iloc[-1]
        bootstrap_status = str(bootstrap_row.get("status", "") or "").strip().lower()
        probability = pd.to_numeric(
            pd.Series([bootstrap_row.get("probability_excess_positive")]),
            errors="coerce",
        ).iloc[0]
        lower_95 = pd.to_numeric(
            pd.Series([bootstrap_row.get("lower_95_excess_return")]),
            errors="coerce",
        ).iloc[0]
        min_probability = float(
            validation.get("min_reliability_bootstrap_probability_positive", 0.80)
        )
        min_lower_95 = float(
            validation.get("min_reliability_bootstrap_lower_95", -0.10)
        )
        bootstrap_probability_passed = bool(
            bootstrap_status == "ok"
            and pd.notna(probability)
            and float(probability) >= min_probability
        )
        bootstrap_lower_passed = bool(
            bootstrap_status == "ok"
            and pd.notna(lower_95)
            and float(lower_95) >= min_lower_95
        )
        add(
            "bootstrap_probability_excess_positive",
            probability if pd.notna(probability) else "missing",
            min_probability,
            bootstrap_probability_passed,
            "区块 Bootstrap 中超额收益为正的概率；用于防止把高噪声点估计解释为稳定优势。",
        )
        add(
            "bootstrap_lower_95_excess_return",
            lower_95 if pd.notna(lower_95) else "missing",
            min_lower_95,
            bootstrap_lower_passed,
            "区块 Bootstrap 超额收益区间下界；过低说明策略优势的统计不确定性仍然很大。",
        )

    critical_gaps = [
        trading_days < min_days,
        optimized_flag,
        bootstrap_probability_passed is False,
        bootstrap_lower_passed is False,
    ]
    critical_gap_count = sum(bool(value) for value in critical_gaps)
    evidence_cap = 54.0 if critical_gap_count >= 2 else (69.0 if critical_gap_count == 1 else 100.0)
    add(
        "reliability_evidence_cap",
        evidence_cap,
        100.0,
        evidence_cap >= 100.0,
        f"关键证据缺口数量={critical_gap_count}；两个及以上缺口最高为低可信，一个缺口最高为观察级。",
        "info",
    )
    scored = [row for row in rows if row["severity"] != "info"]
    raw_score = 0.0 if not scored else 100.0 * sum(1 for row in scored if row["passed"]) / len(scored)
    score = min(raw_score, evidence_cap)
    grade = reliability_grade(score)
    head = [
        {
            "item": "reliability_grade",
            "value": grade,
            "threshold": "高可信 >= 85；中等可信 >= 70；观察级 >= 55",
            "passed": score >= 70.0,
            "severity": "ok" if score >= 70.0 else "warning",
            "note": "综合回测长度、样本内外、走步、滚动窗口、年度稳定性和调参污染标记。",
        },
        {
            "item": "reliability_score",
            "value": round(score, 1),
            "threshold": 70.0,
            "passed": score >= 70.0,
            "severity": "ok" if score >= 70.0 else "warning",
            "note": "简单规则评分，不是收益预测；用于暴露证据缺口。",
        },
    ]
    return pd.DataFrame(head + rows, columns=columns)


def reliability_grade(score: float) -> str:
    if score >= 85:
        return "高可信"
    if score >= 70:
        return "中等可信"
    if score >= 55:
        return "观察级"
    return "低可信"


def strategy_reliability_warnings(reliability: pd.DataFrame) -> list[str]:
    if reliability.empty or "passed" not in reliability:
        return []
    warnings: list[str] = []
    ignored = {"reliability_grade", "reliability_score", "backtest_start", "backtest_end"}
    failed = reliability[
        (~reliability["passed"].astype(bool))
        & (~reliability["item"].astype(str).isin(ignored))
    ]
    for row in failed.itertuples(index=False):
        warnings.append(f"{row.item}: {row.note} (value={row.value}, threshold={row.threshold})")
    return warnings


def strategy_candidate_stability_report(strategy_research: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    columns = ["item", "value", "threshold", "passed", "severity", "note"]
    if strategy_research.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []

    def add(item: str, value: object, threshold: object, passed: bool, note: str, severity: str | None = None) -> None:
        rows.append(
            {
                "item": item,
                "value": value,
                "threshold": threshold,
                "passed": bool(passed),
                "severity": severity or ("ok" if passed else "warning"),
                "note": note,
            }
        )

    data = strategy_research.copy()
    candidate_count = len(data)
    min_candidates = int(config.get("validation", {}).get("min_strategy_research_candidates", 5))
    add("candidate_count", candidate_count, min_candidates, candidate_count >= min_candidates, "候选策略数量；过少时无法判断参数族稳定性。")

    anti = data["anti_overfit_passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"}) if "anti_overfit_passed" in data else pd.Series(False, index=data.index)
    anti_count = int(anti.sum())
    min_anti = int(config.get("validation", {}).get("min_anti_overfit_candidates", 2))
    add(
        "anti_overfit_passed_count",
        anti_count,
        min_anti,
        anti_count >= min_anti,
        "通过反过拟合约束的候选数量；只有一个候选通过时，说明当前参数可能较孤立。",
    )

    if "out_of_sample_excess_return" in data:
        oos = pd.to_numeric(data["out_of_sample_excess_return"], errors="coerce").dropna()
        if not oos.empty:
            positive_ratio = float((oos > 0).mean())
            min_ratio = float(config.get("validation", {}).get("min_candidate_oos_positive_ratio", 0.5))
            add(
                "candidate_oos_positive_ratio",
                positive_ratio,
                min_ratio,
                positive_ratio >= min_ratio,
                "候选策略样本外正超额比例；用于观察是否只有单一参数在最近样本有效。",
            )
            add(
                "candidate_median_oos_excess_return",
                float(oos.median()),
                0.0,
                float(oos.median()) >= 0.0,
                "候选策略样本外超额中位数；为负说明策略族整体不稳。",
            )


    if "time_fold_stability_passed" in data:
        fold_stable = data["time_fold_stability_passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
        fold_count = int(fold_stable.sum())
        min_fold_stable = int(config.get("validation", {}).get("min_time_fold_stable_candidates", 2))
        add(
            "time_fold_stable_candidate_count",
            fold_count,
            min_fold_stable,
            fold_count >= min_fold_stable,
            "跨连续时间折保持稳定的候选数量；用于避免只在某一段历史有效的参数获胜。",
        )
        if {"rank", "name", "time_fold_top_half_ratio", "time_fold_positive_ratio", "time_fold_worst_excess_return"}.issubset(data.columns):
            current_name = str(config.get("strategy", {}).get("name", ""))
            current_fold = data[data["name"].astype(str) == current_name]
            if not current_fold.empty:
                row = current_fold.iloc[0]
                passed = str(row.get("time_fold_stability_passed", "")).strip().lower() in {"true", "1", "yes"}
                add(
                    "current_strategy_time_fold_stability",
                    f"positive={float(row['time_fold_positive_ratio']):.3f};top_half={float(row['time_fold_top_half_ratio']):.3f};worst={float(row['time_fold_worst_excess_return']):.6f}",
                    "configured time-fold thresholds",
                    passed,
                    "当前正式策略必须在多个连续历史阶段保持基本稳定，而不是仅依赖总区间收益。",
                )

    if {"rank", "name"}.issubset(data.columns):
        current_name = str(config.get("strategy", {}).get("name", ""))
        current = data[data["name"].astype(str) == current_name]
        if not current.empty:
            expected_strategy_hash = live_strategy_hash(config)
            observed_strategy_hash = str(current["strategy_hash"].iloc[0]) if "strategy_hash" in current else ""
            add(
                "current_strategy_research_hash",
                observed_strategy_hash or "missing",
                expected_strategy_hash,
                bool(observed_strategy_hash) and observed_strategy_hash == expected_strategy_hash,
                "\u7814\u7a76\u6392\u540d\u4e2d\u7684\u7b56\u7565\u6307\u7eb9\u5fc5\u987b\u4e0e\u5f53\u524d\u5b8c\u6574\u7b56\u7565\u914d\u7f6e\u4e00\u81f4\uff0c\u7ec4\u5408\u6210\u5458\u6216\u98ce\u9669\u89c4\u5219\u53d8\u5316\u540e\u4e0d\u5f97\u7ee7\u627f\u65e7\u6392\u540d\u3002",
            )
            rank = int(pd.to_numeric(current["rank"].iloc[0], errors="coerce"))
            max_freeze_rank = int(config.get("validation", {}).get("max_candidate_freeze_rank", 3))
            add("current_strategy_rank", rank, max_freeze_rank, rank <= max_freeze_rank, "当前策略必须位于配置允许的候选排名内才可冻结观察；冻结不代表允许模拟盘或实盘。")
            if "out_of_sample_excess_return" in current:
                current_oos = pd.to_numeric(current["out_of_sample_excess_return"].iloc[0], errors="coerce")
                if not pd.isna(current_oos):
                    add(
                        "current_strategy_oos_excess_return",
                        float(current_oos),
                        0.0,
                        float(current_oos) >= 0.0,
                        "当前正式策略在最近样本外区间的超额收益。",
                    )
        else:
            add(
                "current_strategy_in_research",
                current_name or "missing",
                "present",
                False,
                "当前正式策略应出现在候选研究表中；否则研究输出可能与当前配置不一致。",
            )

    if {"score", "rank"}.issubset(data.columns) and len(data) >= 2:
        ranked = data.sort_values("rank")
        top_score = pd.to_numeric(ranked["score"].iloc[0], errors="coerce")
        second_score = pd.to_numeric(ranked["score"].iloc[1], errors="coerce")
        if not pd.isna(top_score) and not pd.isna(second_score):
            margin = float(top_score - second_score)
            max_margin = float(config.get("validation", {}).get("max_candidate_score_margin_warning", 0.20))
            add(
                "top_candidate_score_margin",
                margin,
                f"<= {max_margin} advisory",
                margin <= max_margin,
                "第一名与第二名评分差距；差距过大可能表示参数族中只有孤立方案有效。",
                "ok" if margin <= max_margin else "warning",
            )

    if "time_fold_excess_returns" in data.columns:
        selection_bias, _ = build_strategy_selection_bias(data, config)
        if not selection_bias.empty:
            rows.extend(selection_bias.to_dict(orient="records"))

    return pd.DataFrame(rows, columns=columns)


def strategy_candidate_stability_warnings(stability: pd.DataFrame) -> list[str]:
    if stability.empty or "passed" not in stability:
        return []
    warnings: list[str] = []
    failed = stability[~stability["passed"].astype(bool)]
    for row in failed.itertuples(index=False):
        warnings.append(f"{row.item}: {row.note} (value={row.value}, threshold={row.threshold})")
    return warnings


def comparison_total_excess(comparison: pd.DataFrame) -> float:
    if comparison.empty or not {"strategy_nav", "benchmark_nav"}.issubset(comparison.columns):
        return 0.0
    strategy = pd.to_numeric(comparison["strategy_nav"], errors="coerce").dropna()
    benchmark = pd.to_numeric(comparison["benchmark_nav"], errors="coerce").dropna()
    if strategy.empty or benchmark.empty:
        return 0.0
    return float(strategy.iloc[-1] - benchmark.iloc[-1])


def sample_excess(robustness: pd.DataFrame, sample: str) -> float | None:
    if robustness.empty or not {"sample", "excess_return"}.issubset(robustness.columns):
        return None
    row = robustness[robustness["sample"].astype(str) == sample]
    if row.empty:
        return None
    value = pd.to_numeric(row["excess_return"].iloc[0], errors="coerce")
    return None if pd.isna(value) else float(value)


def summary_value(summary: pd.DataFrame, column: str) -> float | None:
    if summary.empty or column not in summary:
        return None
    value = pd.to_numeric(summary[column].iloc[0], errors="coerce")
    return None if pd.isna(value) else float(value)


def walk_forward_folds(summary: pd.DataFrame) -> int | None:
    value = summary_value(summary, "folds")
    return None if value is None else int(value)


def data_quality_text(data_quality: pd.DataFrame, item: str) -> str:
    if data_quality.empty or not {"item", "value"}.issubset(data_quality.columns):
        return ""
    row = data_quality[data_quality["item"].astype(str) == item]
    if row.empty:
        return ""
    value = row["value"].iloc[0]
    return "" if pd.isna(value) else str(value).strip()

def data_quality_float(data_quality: pd.DataFrame, item: str) -> float | None:
    value = pd.to_numeric(data_quality_text(data_quality, item), errors="coerce")
    return None if pd.isna(value) else float(value)





def bias_warnings(data_quality: pd.DataFrame, robustness: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    if not data_quality.empty:
        warning_rows = data_quality[data_quality["severity"] == "warning"]
        for row in warning_rows.itertuples(index=False):
            warnings.append(f"{row.item}: {row.note} (value={row.value})")
    if not robustness.empty:
        out = robustness[robustness["sample"] == "out_of_sample_last_30pct"]
        if not out.empty and float(out["excess_return"].iloc[0]) < 0:
            warnings.append("最近 30% 样本外区间没有跑赢基准，当前高超额可能主要来自早期样本。")
    return warnings


def rolling_warnings(rolling: pd.DataFrame) -> list[str]:
    if rolling.empty:
        return []
    recent = rolling.tail(3)
    if (recent["excess_return"] < 0).all():
        return ["最近 3 个滚动窗口均未跑赢基准，策略近期稳定性不足。"]
    negative_ratio = float((rolling["excess_return"] < 0).mean())
    if negative_ratio > 0.5:
        return [f"滚动窗口中 {negative_ratio:.0%} 未跑赢基准，超额收益持续性不足。"]
    return []


def regime_validation_warnings(regime_validation: pd.DataFrame, min_observations: int = 60) -> list[str]:
    if regime_validation.empty or not {"regime", "observations", "excess_return"}.issubset(regime_validation.columns):
        return []
    data = regime_validation.copy()
    data["observations"] = pd.to_numeric(data["observations"], errors="coerce").fillna(0)
    data["excess_return"] = pd.to_numeric(data["excess_return"], errors="coerce")
    core = data[
        data["regime"].astype(str).isin(
            ["benchmark_down_days", "bear_trend", "high_volatility", "bull_trend", "normal_volatility"]
        )
        & (data["observations"] >= min_observations)
        & data["excess_return"].notna()
    ].copy()
    if core.empty:
        return ["市场状态分段验证有效样本不足，暂不能判断策略是否只适用于单一行情环境。"]
    warnings: list[str] = []
    by_regime = data.set_index(data["regime"].astype(str))
    if {"benchmark_up_days", "benchmark_down_days"}.issubset(set(by_regime.index)) and "avg_daily_excess" in data:
        up_excess = pd.to_numeric(by_regime.loc["benchmark_up_days", "avg_daily_excess"], errors="coerce")
        down_excess = pd.to_numeric(by_regime.loc["benchmark_down_days", "avg_daily_excess"], errors="coerce")
        if not pd.isna(up_excess) and not pd.isna(down_excess) and float(up_excess) < 0 < float(down_excess):
            warnings.append("市场状态分段显示策略偏防御：基准上涨日平均落后，下跌日平均占优；强牛市中可能跑输。")
    negative = core[core["excess_return"] < 0].sort_values("excess_return")
    for row in negative.head(3).itertuples(index=False):
        warnings.append(
            f"市场状态 {row.regime} 分段跑输基准，超额收益={float(row.excess_return):.2%}，样本={int(row.observations)} 天。"
        )
    return warnings


def walk_forward_warnings(summary: pd.DataFrame) -> list[str]:
    if summary.empty:
        return []
    row = summary.iloc[0]
    warnings = []
    if float(row.get("positive_test_ratio", 1.0)) < 0.6:
        warnings.append("走步验证中样本外胜率低于 60%，参数稳定性不足。")
    if float(row.get("avg_test_excess_return", 0.0)) <= 0:
        warnings.append("走步验证平均样本外超额收益不为正，不建议直接进入实盘。")
    return warnings


def _benchmark_metrics(comparison: pd.DataFrame) -> dict[str, float]:
    benchmark_nav = pd.to_numeric(comparison["benchmark_nav"], errors="coerce").dropna()
    strategy_nav = pd.to_numeric(comparison["strategy_nav"], errors="coerce").dropna()
    excess = pd.to_numeric(comparison["excess_return"], errors="coerce").dropna()
    tracking_error = float(excess.std(ddof=0) * (252**0.5)) if not excess.empty else 0.0
    annual_excess = float(excess.mean() * 252) if not excess.empty else 0.0
    information_ratio = annual_excess / tracking_error if tracking_error else 0.0
    return {
        "benchmark_total_return": float(benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1) if len(benchmark_nav) else 0.0,
        "excess_total_return": float(strategy_nav.iloc[-1] - benchmark_nav.iloc[-1]) if len(strategy_nav) and len(benchmark_nav) else 0.0,
        "information_ratio": float(information_ratio),
    }
