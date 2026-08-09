from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd


def price_limit_rate(symbol: str, board: str | None = None, is_st: bool = False) -> float:
    if is_st:
        return 0.05
    if board in {"gem", "star"} or symbol.startswith(("300", "301", "302", "688")):
        return 0.20
    return 0.10


def is_limit_up(open_price: float, prev_close: float, limit_rate: float) -> bool:
    if pd.isna(prev_close) or prev_close <= 0:
        return False
    return open_price >= prev_close * (1 + limit_rate) * 0.999


def is_limit_down(open_price: float, prev_close: float, limit_rate: float) -> bool:
    if pd.isna(prev_close) or prev_close <= 0:
        return False
    return open_price <= prev_close * (1 - limit_rate) * 1.001


def round_buy_quantity(quantity: int, lot_size: int) -> int:
    return max(0, int(quantity // lot_size) * lot_size)

def buy_quantity_for_value(value: float, price: float, lot_size: int) -> int:
    """Convert a target cash value to board lots without losing exact lots to float noise."""
    if value <= 0 or price <= 0 or lot_size <= 0:
        return 0
    lot_value = price * lot_size
    tolerance = max(lot_value * 1e-12, 1e-9)
    lots = int((value + tolerance) / lot_value)
    return max(lots, 0) * lot_size


def calc_fee(side: str, amount: float, trading_config: dict[str, Any]) -> float:
    commission = max(
        amount * float(trading_config["commission_rate"]),
        float(trading_config["min_commission"]) if amount > 0 else 0,
    )
    transfer = amount * float(trading_config.get("transfer_fee_rate", 0))
    stamp = amount * float(trading_config.get("stamp_tax_rate", 0)) if side == "sell" else 0.0
    return commission + transfer + stamp


def next_trading_day(dates: list[date], current: date) -> date | None:
    for dt in dates:
        if dt > current:
            return dt
    return None


def next_business_day(current: date) -> date:
    candidate = current + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def monthly_rebalance_dates(dates: list[date]) -> set[date]:
    out: set[date] = set()
    seen: set[tuple[int, int]] = set()
    for dt in dates:
        key = (dt.year, dt.month)
        if key not in seen:
            out.add(dt)
            seen.add(key)
    return out

def configured_rebalance_dates(dates: list[date], frequency: str = "monthly") -> set[date]:
    mode = str(frequency or "monthly").strip().lower()
    if mode == "monthly":
        return monthly_rebalance_dates(dates)
    if mode != "quarterly":
        raise ValueError(f"Unsupported rebalance frequency: {frequency}")
    out: set[date] = set()
    seen: set[tuple[int, int]] = set()
    for dt in dates:
        key = (dt.year, (dt.month - 1) // 3 + 1)
        if key not in seen:
            out.add(dt)
            seen.add(key)
    return out