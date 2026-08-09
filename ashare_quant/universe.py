from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from .index_membership import filter_by_benchmark_membership


def eligible_universe(
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    asof: date,
    config: dict[str, Any],
) -> pd.DataFrame:
    universe_cfg = config["universe"]
    boards = set(universe_cfg["boards"])
    out = stocks[stocks["board"].isin(boards)].copy()
    if universe_cfg.get("exclude_st", True):
        out = out[~out["is_st"]]
    min_listing_days = int(universe_cfg["min_listing_days"])
    out = out[(pd.to_datetime(asof) - pd.to_datetime(out["list_date"])).dt.days >= min_listing_days]
    out = filter_by_benchmark_membership(out, asof, config)

    history = prices[prices["date"] <= asof].copy()
    if history.empty:
        return out.iloc[0:0]
    latest = history.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)
    out = out.merge(latest[["symbol", "date", "close"]], how="inner", on="symbol")

    lookback = int(universe_cfg["liquidity_lookback"])
    min_amount = float(universe_cfg["min_avg_amount"])
    recent = history.sort_values(["symbol", "date"]).groupby("symbol", group_keys=False).tail(lookback)
    avg_amount = recent.groupby("symbol")["amount"].mean()
    out = out.merge(avg_amount.rename("avg_amount"), how="left", on="symbol")
    out = out[out["avg_amount"].fillna(0) >= min_amount]
    return out.reset_index(drop=True)
