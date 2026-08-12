from __future__ import annotations

from typing import Any

import pandas as pd

from .rules import calc_fee


def capital_aware_enabled(config: dict[str, Any]) -> bool:
    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    policy = strategy.get("capital_aware", {}) if isinstance(strategy.get("capital_aware"), dict) else {}
    return bool(policy.get("enabled", False))


def build_capital_aware_target_weights(
    scores: pd.DataFrame,
    close_prices: pd.Series | dict[str, float],
    equity: float,
    exposure: float,
    config: dict[str, Any],
    target_weights: pd.Series | dict[str, float] | None = None,
) -> pd.Series:
    """Build an integer-lot target while preserving cash, stock and industry caps."""
    if scores.empty or equity <= 0 or exposure <= 0:
        return pd.Series(dtype=float)

    strategy = config.get("strategy", {}) if isinstance(config.get("strategy"), dict) else {}
    policy = strategy.get("capital_aware", {}) if isinstance(strategy.get("capital_aware"), dict) else {}
    trading = config.get("trading", {}) if isinstance(config.get("trading"), dict) else {}
    min_positions = max(int(policy.get("min_positions", 3)), 1)
    max_positions = max(int(policy.get("max_positions", min_positions)), min_positions)
    max_candidate_rank = max(int(policy.get("max_candidate_rank", max_positions * 20)), max_positions)
    lot_size = max(int(trading.get("lot_size", 100)), 1)
    reserve = min(max(float(trading.get("reserve_cash_ratio", 0.0)), 0.0), 1.0)
    exposure = min(max(float(exposure), 0.0), 1.0)
    equity_step = max(float(policy.get("planning_equity_step", 0.0)), 0.0)
    planning_equity = float(equity)
    if equity_step > 0:
        planning_equity = (planning_equity // equity_step) * equity_step
    actual_investable = float(equity) * (1.0 - reserve)
    planning_investable = planning_equity * (1.0 - reserve)
    budget = planning_investable * exposure
    if budget <= 0 or actual_investable <= 0 or planning_investable <= 0:
        return pd.Series(dtype=float)

    max_stock_weight = min(max(float(strategy.get("max_stock_weight", 1.0)), 0.0), 1.0)
    max_industry_weight = min(max(float(strategy.get("max_industry_weight", 1.0)), 0.0), 1.0)
    stock_value_cap = planning_investable * exposure * max_stock_weight
    industry_value_cap = planning_investable * exposure * max_industry_weight
    require_industry = bool(policy.get("require_industry", True))
    allow_additional_lots = bool(policy.get("allow_additional_lots", True))
    respect_target_weights = bool(policy.get("respect_target_weights", False))
    planning_trading = dict(trading)
    if "planning_commission_rate" in policy:
        planning_trading["commission_rate"] = float(policy["planning_commission_rate"])
    if "planning_min_commission" in policy:
        planning_trading["min_commission"] = float(policy["planning_min_commission"])
    if "planning_transfer_fee_rate" in policy:
        planning_trading["transfer_fee_rate"] = float(policy["planning_transfer_fee_rate"])

    frame = scores.copy()
    if not {"symbol", "score"}.issubset(frame.columns):
        return pd.Series(dtype=float)
    frame["symbol"] = frame["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    prices = pd.Series(close_prices, dtype=float)
    prices.index = prices.index.astype(str).str.split(".").str[0].str.zfill(6)
    frame["price"] = pd.to_numeric(frame["symbol"].map(prices), errors="coerce")
    if "industry" not in frame:
        frame["industry"] = ""
    frame["industry"] = frame["industry"].fillna("").astype(str).str.strip()
    if respect_target_weights:
        targets = pd.Series(target_weights, dtype=float)
        targets.index = targets.index.astype(str).str.split(".").str[0].str.zfill(6)
        targets = pd.to_numeric(targets, errors="coerce").fillna(0.0).clip(lower=0.0)
        targets = targets.groupby(level=0).sum()
        if targets.empty or float(targets.sum()) <= 0:
            return pd.Series(dtype=float)
        frame["target_weight"] = frame["symbol"].map(targets).fillna(0.0)
        frame = frame[frame["target_weight"] > 0]
    frame = frame.dropna(subset=["score", "price"])
    frame = frame[frame["price"] > 0]
    sort_columns = ["target_weight", "score", "symbol"] if respect_target_weights else ["score", "symbol"]
    ascending = [False, False, True] if respect_target_weights else [False, True]
    frame = frame.sort_values(sort_columns, ascending=ascending).drop_duplicates("symbol")
    frame = frame.head(max_candidate_rank).reset_index(drop=True)

    quantities: dict[str, int] = {}
    selected_rows: dict[str, pd.Series] = {}
    industry_values: dict[str, float] = {}
    total_cost = 0.0

    def order_cost(symbol: str, quantity: int, price: float) -> float:
        amount = quantity * price
        return amount + calc_fee("buy", amount, planning_trading)

    for _, row in frame.iterrows():
        if len(quantities) >= max_positions:
            break
        symbol = str(row["symbol"])
        price = float(row["price"])
        industry = str(row["industry"] or "").strip()
        if require_industry and not industry:
            continue
        industry_key = industry or f"unknown:{symbol}"
        market_value = price * lot_size
        cost = order_cost(symbol, lot_size, price)
        if market_value > stock_value_cap + 1e-8:
            continue
        if industry_values.get(industry_key, 0.0) + market_value > industry_value_cap + 1e-8:
            continue
        if total_cost + cost > budget + 1e-8:
            continue
        quantities[symbol] = lot_size
        selected_rows[symbol] = row
        industry_values[industry_key] = industry_values.get(industry_key, 0.0) + market_value
        total_cost += cost

    if len(quantities) < min_positions:
        return pd.Series(dtype=float)

    if allow_additional_lots:
        desired_values: dict[str, float] = {}
        if respect_target_weights:
            selected_target = {
                symbol: max(float(row.get("target_weight", 0.0)), 0.0)
                for symbol, row in selected_rows.items()
            }
            selected_total = sum(selected_target.values())
            if selected_total <= 0:
                return pd.Series(dtype=float)
            desired_values = {
                symbol: budget * weight / selected_total
                for symbol, weight in selected_target.items()
            }
        while True:
            added = False
            candidates = []
            for symbol, row in selected_rows.items():
                price = float(row["price"])
                industry = str(row["industry"] or "").strip()
                industry_key = industry or f"unknown:{symbol}"
                old_quantity = quantities[symbol]
                new_quantity = old_quantity + lot_size
                old_value = old_quantity * price
                new_value = new_quantity * price
                incremental_cost = order_cost(symbol, new_quantity, price) - order_cost(symbol, old_quantity, price)
                if new_value > stock_value_cap + 1e-8:
                    continue
                if industry_values[industry_key] - old_value + new_value > industry_value_cap + 1e-8:
                    continue
                if total_cost + incremental_cost > budget + 1e-8:
                    continue
                if respect_target_weights:
                    desired = desired_values[symbol]
                    improvement = abs(old_value - desired) - abs(new_value - desired)
                    if improvement > 1e-8:
                        candidates.append((improvement, symbol, row, incremental_cost))
                    continue
                quantities[symbol] = new_quantity
                industry_values[industry_key] += new_value - old_value
                total_cost += incremental_cost
                added = True
            if respect_target_weights and candidates:
                _, symbol, row, incremental_cost = max(
                    candidates, key=lambda item: (item[0], item[1])
                )
                price = float(row["price"])
                industry = str(row["industry"] or "").strip()
                industry_key = industry or f"unknown:{symbol}"
                quantities[symbol] += lot_size
                industry_values[industry_key] += price * lot_size
                total_cost += incremental_cost
                added = True
            if not added:
                break

    weights = {
        symbol: quantities[symbol] * float(selected_rows[symbol]["price"]) / actual_investable
        for symbol in quantities
    }
    result = pd.Series(weights, dtype=float).sort_index()
    if float(result.sum()) > exposure + 1e-8:
        return pd.Series(dtype=float)
    return result
