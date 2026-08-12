from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from .capital_budget import strategy_deployable_equity
from .models import Fill, Order, Position
from .rules import buy_quantity_for_value, calc_fee, is_limit_down, is_limit_up, price_limit_rate, round_buy_quantity


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    rejected_orders: list[dict[str, Any]] = field(default_factory=list)

    def unlock_t1(self) -> None:
        for position in self.positions.values():
            position.locked_quantity = 0

    def market_value(self, close_prices: dict[str, float]) -> float:
        return sum(pos.quantity * close_prices.get(symbol, 0.0) for symbol, pos in self.positions.items())

    def equity(self, close_prices: dict[str, float]) -> float:
        return self.cash + self.market_value(close_prices)

    def snapshot_positions(self, close_prices: dict[str, float]) -> pd.DataFrame:
        rows = []
        for symbol, pos in sorted(self.positions.items()):
            if pos.quantity <= 0:
                continue
            close = close_prices.get(symbol, 0.0)
            rows.append(
                {
                    "symbol": symbol,
                    "quantity": pos.quantity,
                    "sellable_quantity": pos.sellable_quantity,
                    "cost": round(pos.cost, 4),
                    "close": close,
                    "market_value": round(pos.quantity * close, 2),
                    "pnl": round(pos.quantity * (close - pos.cost), 2),
                }
            )
        return pd.DataFrame(rows)

    def execute_orders(
        self,
        orders: list[Order],
        trade_date: date,
        prices_for_date: pd.DataFrame,
        stocks: pd.DataFrame,
        prev_close: dict[str, float],
        config: dict[str, Any],
    ) -> list[Fill]:
        stock_info = stocks.set_index("symbol").to_dict("index")
        price_rows = prices_for_date.set_index("symbol").to_dict("index")
        fills: list[Fill] = []
        for order in orders:
            symbol = order.symbol
            row = price_rows.get(symbol)
            if row is None or pd.isna(row.get("open")):
                self.reject(order, trade_date, "suspended_or_missing_open")
                continue
            info = stock_info.get(symbol, {})
            limit_rate = price_limit_rate(symbol, info.get("board"), bool(info.get("is_st", False)))
            open_price = float(row["open"])
            previous_close = prev_close.get(symbol)
            if order.side == "buy" and previous_close and is_limit_up(open_price, previous_close, limit_rate):
                self.reject(order, trade_date, "limit_up_buy_blocked")
                continue
            if order.side == "sell" and previous_close and is_limit_down(open_price, previous_close, limit_rate):
                self.reject(order, trade_date, "limit_down_sell_blocked")
                continue

            adjusted_price = open_price * (
                1 + float(config["trading"]["slippage_bps"]) / 10000 * (1 if order.side == "buy" else -1)
            )
            quantity = self._executable_quantity(order, adjusted_price, row, config)
            if quantity <= 0:
                self.reject(order, trade_date, "insufficient_cash_position_or_quantity")
                continue
            amount = quantity * adjusted_price
            fee = calc_fee(order.side, amount, config["trading"])
            fill = self._apply_fill(order, trade_date, quantity, adjusted_price, fee)
            fills.append(fill)
        self.fills.extend(fills)
        return fills

    def reject(self, order: Order, trade_date: date, reason: str) -> None:
        self.rejected_orders.append(
            {
                "date": trade_date,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.quantity,
                "reason": reason,
            }
        )

    def _executable_quantity(
        self,
        order: Order,
        price: float,
        price_row: dict[str, Any],
        config: dict[str, Any],
    ) -> int:
        volume_cap = int(float(price_row.get("volume", 0)) * float(config["trading"]["max_volume_participation"]))
        requested = min(order.quantity, volume_cap) if volume_cap > 0 else order.quantity
        if order.side == "buy":
            requested = round_buy_quantity(requested, int(config["trading"]["lot_size"]))
            if requested <= 0:
                return 0
            unit_cost = price * requested + calc_fee("buy", price * requested, config["trading"])
            if unit_cost <= self.cash:
                return requested
            affordable = int(self.cash / max(price * 1.01, 0.01))
            return round_buy_quantity(min(affordable, requested), int(config["trading"]["lot_size"]))
        position = self.positions.get(order.symbol)
        if not position:
            return 0
        return min(requested, position.sellable_quantity)

    def _apply_fill(self, order: Order, trade_date: date, quantity: int, price: float, fee: float) -> Fill:
        amount = quantity * price
        position = self.positions.setdefault(order.symbol, Position(symbol=order.symbol))
        if order.side == "buy":
            total_cost = position.cost * position.quantity + amount + fee
            position.quantity += quantity
            position.locked_quantity += quantity
            position.cost = total_cost / position.quantity
            self.cash -= amount + fee
        else:
            position.quantity -= quantity
            self.cash += amount - fee
            if position.quantity <= 0:
                position.quantity = 0
                position.cost = 0.0
                position.locked_quantity = 0
        return Fill(
            date=trade_date,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=round(price, 4),
            fee=round(fee, 4),
            amount=round(amount, 2),
            reason=order.reason,
        )


def generate_rebalance_orders(
    trade_date: date,
    target_weights: pd.Series,
    portfolio: Portfolio,
    close_prices: dict[str, float],
    config: dict[str, Any],
) -> list[Order]:
    equity = strategy_deployable_equity(portfolio.equity(close_prices), config)
    reserve = float(config["trading"]["reserve_cash_ratio"])
    investable = equity * (1 - reserve)
    symbols = set(target_weights.index.astype(str)) | set(portfolio.positions)
    sell_orders: list[Order] = []
    buy_orders: list[Order] = []
    for symbol in sorted(symbols):
        price = close_prices.get(symbol)
        if not price or price <= 0:
            continue
        current_qty = portfolio.positions.get(symbol, Position(symbol)).quantity
        current_value = current_qty * price
        target_value = investable * float(target_weights.get(symbol, 0.0))
        diff_value = target_value - current_value
        qty = buy_quantity_for_value(diff_value, price, int(config["trading"]["lot_size"]))
        if qty > 0:
            buy_orders.append(Order(date=trade_date, symbol=symbol, side="buy", quantity=qty))
        elif diff_value < -price:
            position = portfolio.positions.get(symbol, Position(symbol))
            target_weight = float(target_weights.get(symbol, 0.0))
            if target_weight <= 0:
                qty = min(current_qty, position.sellable_quantity)
            else:
                tolerance = max(price * 1e-12, 1e-9)
                target_qty = max(0, int((target_value + tolerance) / price))
                desired_sell = max(0, current_qty - target_qty)
                lot_size = int(config["trading"].get("lot_size", 100))
                qty = min((desired_sell // lot_size) * lot_size, position.sellable_quantity)
            if qty > 0:
                sell_orders.append(Order(date=trade_date, symbol=symbol, side="sell", quantity=qty))
    return sell_orders + buy_orders
