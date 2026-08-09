from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .candidate import load_candidate_manifest
from .config import project_path
from .data import DataStore
from .factors import compute_factor_values, effective_factor_weights, strategy_member_configs
from .io_utils import atomic_to_csv
from .universe import eligible_universe
from .validation import complete_market_dates, latest_complete_market_date


FORWARD_FACTOR_COLUMNS = [
    "candidate_id",
    "strategy_name",
    "member_name",
    "member_blend_weight",
    "factor",
    "configured_weight",
    "freeze_date",
    "forward_start",
    "forward_end",
    "forward_trading_days",
    "covered_symbols",
    "top_bucket_return",
    "bottom_bucket_return",
    "long_short_spread",
    "benchmark_return",
    "top_bucket_excess_return",
    "rank_ic",
    "status",
]


def build_forward_factor_monitor(
    config: dict[str, Any],
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    manifest: dict[str, Any] | None = None,
) -> pd.DataFrame:
    candidate = manifest if manifest is not None else load_candidate_manifest(config)
    if not candidate or prices.empty:
        return pd.DataFrame(columns=FORWARD_FACTOR_COLUMNS)

    freeze_date = parse_date(candidate.get("freeze_price_end"))
    if freeze_date is None:
        return pd.DataFrame(columns=FORWARD_FACTOR_COLUMNS)

    market = prices.copy()
    market["date"] = pd.to_datetime(market["date"], errors="coerce").dt.date
    market["symbol"] = market["symbol"].astype(str)
    market["close"] = pd.to_numeric(market["close"], errors="coerce")
    latest = latest_complete_market_date(market, benchmark, config)
    if latest is None or latest <= freeze_date:
        return pd.DataFrame(columns=FORWARD_FACTOR_COLUMNS)

    valid_dates = complete_market_dates(market, benchmark, config)
    forward_dates = [value for value in valid_dates if freeze_date < value <= latest]
    if not forward_dates:
        return pd.DataFrame(columns=FORWARD_FACTOR_COLUMNS)

    start_prices = exact_close_frame(market, freeze_date, "start_close")
    end_prices = exact_close_frame(market, latest, "end_close")
    realized = start_prices.merge(end_prices, on="symbol", how="inner")
    realized = realized[(realized["start_close"] > 0) & realized["end_close"].notna()].copy()
    realized["forward_return"] = realized["end_close"] / realized["start_close"] - 1.0
    benchmark_return = exact_benchmark_return(benchmark, freeze_date, latest)

    monitor_cfg = config.get("strategy_health", {})
    min_symbols = max(int(monitor_cfg.get("forward_factor_min_symbols", 30)), 10)
    quantile = min(max(float(monitor_cfg.get("forward_factor_quantile", 0.20)), 0.05), 0.45)
    rows: list[dict[str, object]] = []

    for blend_weight, member_config in strategy_member_configs(config):
        strategy = member_config.get("strategy", {})
        member_name = str(strategy.get("name", "") or config.get("strategy", {}).get("name", "strategy"))
        weights = effective_factor_weights(member_config, benchmark, freeze_date)
        active_factors = [
            (str(name), float(weight))
            for name, weight in weights.items()
            if abs(float(weight)) > 1e-12
        ]
        if not active_factors:
            continue

        universe = eligible_universe(stocks, market, freeze_date, member_config)
        universe_symbols = set(universe["symbol"].astype(str)) if not universe.empty else set()
        factors = compute_factor_values(
            market,
            fundamentals,
            freeze_date,
            strategy.get("lookbacks", {}),
            benchmark,
        )
        base = factors.merge(realized, on="symbol", how="inner")
        if universe_symbols:
            base = base[base["symbol"].astype(str).isin(universe_symbols)].copy()

        for factor_name, configured_weight in active_factors:
            row = summarize_forward_factor(
                base,
                factor_name,
                quantile=quantile,
                min_symbols=min_symbols,
                benchmark_return=benchmark_return,
            )
            if row is None:
                continue
            rows.append(
                {
                    "candidate_id": str(candidate.get("candidate_id", "")),
                    "strategy_name": str(candidate.get("strategy_name", config.get("strategy", {}).get("name", ""))),
                    "member_name": member_name,
                    "member_blend_weight": float(blend_weight),
                    "factor": factor_name,
                    "configured_weight": configured_weight,
                    "freeze_date": freeze_date.isoformat(),
                    "forward_start": forward_dates[0].isoformat(),
                    "forward_end": latest.isoformat(),
                    "forward_trading_days": len(forward_dates),
                    **row,
                }
            )

    return pd.DataFrame(rows, columns=FORWARD_FACTOR_COLUMNS)


def summarize_forward_factor(
    frame: pd.DataFrame,
    factor: str,
    *,
    quantile: float = 0.20,
    min_symbols: int = 30,
    benchmark_return: float | None = None,
) -> dict[str, object] | None:
    if factor not in frame or "forward_return" not in frame:
        return None
    data = frame[[factor, "forward_return"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(data) < max(int(min_symbols), 10):
        return None

    bucket_size = max(int(len(data) * float(quantile)), 1)
    top_return = float(data.nlargest(bucket_size, factor)["forward_return"].mean())
    bottom_return = float(data.nsmallest(bucket_size, factor)["forward_return"].mean())
    spread = top_return - bottom_return
    rank_ic = float(data[factor].rank(method="average").corr(data["forward_return"].rank(method="average")))
    benchmark_value = float(benchmark_return) if benchmark_return is not None and not pd.isna(benchmark_return) else float("nan")
    if spread >= 0 and rank_ic >= 0:
        status = "supportive"
    elif spread < 0 and rank_ic < 0:
        status = "adverse"
    else:
        status = "mixed"
    return {
        "covered_symbols": int(len(data)),
        "top_bucket_return": top_return,
        "bottom_bucket_return": bottom_return,
        "long_short_spread": spread,
        "benchmark_return": benchmark_value,
        "top_bucket_excess_return": top_return - benchmark_value if not pd.isna(benchmark_value) else float("nan"),
        "rank_ic": rank_ic,
        "status": status,
    }


def forward_factor_warnings(frame: pd.DataFrame, adverse_weight_threshold: float = 0.50) -> list[str]:
    required = {"factor", "configured_weight", "member_blend_weight", "status"}
    if frame.empty or not required.issubset(frame.columns):
        return []

    data = frame.copy()
    data["configured_weight"] = pd.to_numeric(data["configured_weight"], errors="coerce").fillna(0.0).abs()
    data["member_blend_weight"] = pd.to_numeric(data["member_blend_weight"], errors="coerce").fillna(0.0).abs()
    data["effective_weight"] = data["configured_weight"] * data["member_blend_weight"]
    total_weight = float(data["effective_weight"].sum())
    if total_weight <= 0:
        return []

    adverse = data[data["status"].astype(str).str.lower().eq("adverse")].copy()
    adverse_weight = float(adverse["effective_weight"].sum()) / total_weight
    if adverse.empty or adverse_weight < float(adverse_weight_threshold):
        return []

    grouped = adverse.groupby("factor", dropna=False)["effective_weight"].sum().sort_values(ascending=False)
    details = "\u3001".join(f"{factor} {weight / total_weight:.1%}" for factor, weight in grouped.items())
    return [
        f"\u51bb\u7ed3\u540e\u56e0\u5b50\u8bca\u65ad\uff1a\u4e0d\u5229\u56e0\u5b50\u6743\u91cd\u5360 {adverse_weight:.1%}\uff08{details}\uff09\uff1b"
        "\u8be5\u7ed3\u679c\u53ea\u7528\u4e8e\u524d\u5411\u76d1\u63a7\uff0c\u4e0d\u7528\u4e8e\u56de\u770b\u8c03\u53c2\u3002"
    ]


def write_forward_factor_monitor(config: dict[str, Any]) -> Path:
    store = DataStore.from_config(config)
    stocks, prices, fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    result = build_forward_factor_monitor(config, stocks, prices, fundamentals, benchmark)
    path = project_path(config["paths"]["output_dir"]) / "forward_factor_monitor.csv"
    atomic_to_csv(result, path, index=False, encoding="utf-8-sig")
    return path


def exact_close_frame(prices: pd.DataFrame, asof: date, column: str) -> pd.DataFrame:
    frame = prices[prices["date"] == asof][["symbol", "close"]].copy()
    frame = frame.dropna(subset=["close"]).drop_duplicates("symbol", keep="last")
    return frame.rename(columns={"close": column})


def exact_benchmark_return(benchmark: pd.DataFrame, start: date, end: date) -> float | None:
    if benchmark.empty or not {"date", "close"}.issubset(benchmark.columns):
        return None
    frame = benchmark.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    values = frame[frame["date"].isin([start, end])].dropna(subset=["close"]).drop_duplicates("date", keep="last")
    mapping = dict(zip(values["date"], values["close"]))
    if start not in mapping or end not in mapping or float(mapping[start]) <= 0:
        return None
    return float(mapping[end] / mapping[start] - 1.0)


def parse_date(value: object) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()
