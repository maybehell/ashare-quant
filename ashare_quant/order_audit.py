
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .data import DataStore
from .io_utils import atomic_to_csv

ORDER_LIQUIDITY_COLUMNS = [
    "ticket_id",
    "date",
    "symbol",
    "side",
    "quantity",
    "limit_price",
    "estimated_amount",
    "avg_volume",
    "avg_amount",
    "volume_participation",
    "amount_participation",
    "max_volume_participation",
    "max_amount_participation",
    "passed",
    "status",
    "message",
]


def build_order_liquidity_audit(
    config: dict[str, Any],
    tickets: pd.DataFrame,
    prices: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if tickets.empty:
        return pd.DataFrame(columns=ORDER_LIQUIDITY_COLUMNS)
    price_frame = normalize_prices(prices if prices is not None else load_prices(config))
    lookback = int(config.get("execution", {}).get("order_liquidity_lookback_days", 20))
    max_volume_participation = float(
        config.get("execution", {}).get(
            "max_order_avg_volume_participation",
            config.get("trading", {}).get("max_volume_participation", 0.10),
        )
    )
    max_amount_participation = float(config.get("execution", {}).get("max_order_avg_amount_participation", 0.05))
    rows: list[dict[str, object]] = []
    normalized_tickets = tickets.copy()
    normalized_tickets["symbol"] = normalized_tickets["symbol"].astype(str).str.zfill(6)
    for ticket in normalized_tickets.itertuples(index=False):
        symbol = str(ticket.symbol).zfill(6)
        history = price_frame[price_frame["symbol"] == symbol].sort_values("date")
        ticket_date = pd.to_datetime(getattr(ticket, "date", pd.NaT), errors="coerce")
        if not pd.isna(ticket_date):
            history = history[history["date"] <= ticket_date.date()]
        recent = history.tail(lookback)
        avg_volume = numeric_mean(recent, "volume")
        avg_amount = numeric_mean(recent, "amount")
        quantity = int(pd.to_numeric(getattr(ticket, "quantity", 0), errors="coerce") or 0)
        amount = float(pd.to_numeric(getattr(ticket, "estimated_amount", 0.0), errors="coerce") or 0.0)
        volume_participation = safe_ratio(quantity, avg_volume)
        amount_participation = safe_ratio(amount, avg_amount)
        existing_status = str(getattr(ticket, "status", "") or "").lower()
        passed, status, message = liquidity_status(
            existing_status,
            avg_volume,
            avg_amount,
            volume_participation,
            amount_participation,
            max_volume_participation,
            max_amount_participation,
        )
        rows.append(
            {
                "ticket_id": getattr(ticket, "ticket_id", ""),
                "date": getattr(ticket, "date", ""),
                "symbol": symbol,
                "side": str(getattr(ticket, "side", "")).lower(),
                "quantity": quantity,
                "limit_price": round(float(pd.to_numeric(getattr(ticket, "limit_price", 0.0), errors="coerce") or 0.0), 4),
                "estimated_amount": round(amount, 2),
                "avg_volume": round(avg_volume, 4) if avg_volume is not None else "",
                "avg_amount": round(avg_amount, 4) if avg_amount is not None else "",
                "volume_participation": round(volume_participation, 6) if volume_participation is not None else "",
                "amount_participation": round(amount_participation, 6) if amount_participation is not None else "",
                "max_volume_participation": max_volume_participation,
                "max_amount_participation": max_amount_participation,
                "passed": bool(passed),
                "status": status,
                "message": message,
            }
        )
    return pd.DataFrame(rows, columns=ORDER_LIQUIDITY_COLUMNS)


def write_order_liquidity_audit(config: dict[str, Any], tickets: pd.DataFrame | None = None) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    if tickets is None:
        tickets = read_csv(output_dir / "rpa_orders.csv")
    audit = build_order_liquidity_audit(config, tickets)
    path = output_dir / "order_liquidity_audit.csv"
    atomic_to_csv(audit, path, index=False, encoding="utf-8-sig")
    return path


def order_liquidity_audit_passed(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or "passed" not in frame:
        return False, "missing"
    passed = frame["passed"].map(parse_bool_like)
    blocked = int((~passed).sum())
    total = int(len(frame))
    max_amount = max_numeric(frame, "amount_participation")
    max_volume = max_numeric(frame, "volume_participation")
    value = f"{int(passed.sum())}/{total}; max_amount={max_amount:.4f}; max_volume={max_volume:.4f}"
    return blocked == 0 and total > 0, value


def load_prices(config: dict[str, Any]) -> pd.DataFrame:
    store = DataStore.from_config(config)
    _, prices, _ = store.load_market_data()
    return prices


def normalize_prices(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol", "volume", "amount"])
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    for column in ["volume", "amount"]:
        out[column] = pd.to_numeric(out.get(column, 0.0), errors="coerce")
    return out.dropna(subset=["date", "symbol"])


def liquidity_status(
    existing_status: str,
    avg_volume: float | None,
    avg_amount: float | None,
    volume_participation: float | None,
    amount_participation: float | None,
    max_volume_participation: float,
    max_amount_participation: float,
) -> tuple[bool, str, str]:
    if existing_status and existing_status != "ready":
        return False, "blocked", f"ticket_status_{existing_status}"
    if avg_volume is None or avg_volume <= 0:
        return False, "blocked", "missing_average_volume"
    if avg_amount is None or avg_amount <= 0:
        return False, "blocked", "missing_average_amount"
    if volume_participation is None or volume_participation > max_volume_participation:
        return False, "blocked", "exceeds_average_volume_participation"
    if amount_participation is None or amount_participation > max_amount_participation:
        return False, "blocked", "exceeds_average_amount_participation"
    return True, "ready", "liquidity_ok"


def numeric_mean(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.mean())


def safe_ratio(numerator: float, denominator: float | None) -> float | None:
    if denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def max_numeric(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.max()) if not values.empty else 0.0


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"symbol": str})
    except EmptyDataError:
        return pd.DataFrame()
