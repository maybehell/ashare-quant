from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Order:
    date: date
    symbol: str
    side: str
    quantity: int
    reason: str = "rebalance"


@dataclass
class Fill:
    date: date
    symbol: str
    side: str
    quantity: int
    price: float
    fee: float
    amount: float
    reason: str


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    cost: float = 0.0
    locked_quantity: int = 0

    @property
    def sellable_quantity(self) -> int:
        return max(0, self.quantity - self.locked_quantity)
