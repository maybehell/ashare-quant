from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .data import normalize_symbol
from .io_utils import atomic_to_csv


EXECUTION_QUALITY_COLUMNS = ["check", "passed", "value", "threshold", "message"]
EXECUTION_QUALITY_DETAIL_COLUMNS = [
    "date",
    "symbol",
    "side",
    "fill_quantity",
    "order_quantity",
    "fill_price",
    "reference_price",
    "adverse_slippage_bps",
    "matched",
    "status",
    "message",
]


def build_execution_quality(
    config: dict[str, Any],
    fills: pd.DataFrame | None = None,
    order_match: pd.DataFrame | None = None,
    rpa_orders: pd.DataFrame | None = None,
    orders: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    output_dir = project_path(config["paths"]["output_dir"])
    fill_frame = normalize_fills(fills if fills is not None else read_csv(output_dir / "fill_import_trades.csv"))
    match_frame = normalize_order_match(order_match if order_match is not None else read_csv(output_dir / "fill_order_match.csv"))
    rpa_frame = normalize_reference_orders(rpa_orders if rpa_orders is not None else read_csv(output_dir / "rpa_orders.csv"), price_column="limit_price")
    order_frame = normalize_reference_orders(orders if orders is not None else read_csv(output_dir / "orders.csv"), price_column="")
    detail = build_execution_quality_detail(config, fill_frame, match_frame, rpa_frame, order_frame)
    summary = build_execution_quality_summary(config, fill_frame, detail, match_frame)
    return summary, detail


def write_execution_quality(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, detail = build_execution_quality(config)
    summary_path = output_dir / "execution_quality.csv"
    detail_path = output_dir / "execution_quality_detail.csv"
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(detail, detail_path, index=False, encoding="utf-8-sig")
    return {"execution_quality.csv": summary_path, "execution_quality_detail.csv": detail_path}


def build_execution_quality_detail(
    config: dict[str, Any],
    fills: pd.DataFrame,
    order_match: pd.DataFrame,
    rpa_orders: pd.DataFrame,
    orders: pd.DataFrame,
) -> pd.DataFrame:
    if fills.empty:
        return pd.DataFrame(columns=EXECUTION_QUALITY_DETAIL_COLUMNS)
    grouped = aggregate_fills(fills)
    references = build_reference_frame(rpa_orders, orders)
    rows: list[dict[str, object]] = []
    max_slippage = max_adverse_slippage_bps(config)
    for fill in grouped.itertuples(index=False):
        date_text = str(fill.date)
        symbol = normalize_symbol(fill.symbol)
        side = str(fill.side).lower()
        reference = reference_row(references, date_text, symbol, side)
        match = match_row(order_match, date_text, symbol, side)
        order_quantity = int(reference.get("quantity", 0) or 0)
        if match:
            order_quantity = int(match.get("order_quantity", order_quantity) or 0)
        matched = bool(match.get("matched", False)) if match else (order_quantity >= int(fill.quantity) and order_quantity > 0)
        reference_price = float(reference.get("reference_price", 0.0) or 0.0)
        slippage = adverse_slippage_bps(side, float(fill.price), reference_price)
        status, message = detail_status(order_quantity, matched, reference_price, slippage, max_slippage)
        rows.append(
            {
                "date": date_text,
                "symbol": symbol,
                "side": side,
                "fill_quantity": int(fill.quantity),
                "order_quantity": order_quantity,
                "fill_price": round(float(fill.price), 4),
                "reference_price": round(reference_price, 4) if reference_price > 0 else "",
                "adverse_slippage_bps": round(slippage, 4) if slippage is not None else "",
                "matched": bool(matched),
                "status": status,
                "message": message,
            }
        )
    return pd.DataFrame(rows, columns=EXECUTION_QUALITY_DETAIL_COLUMNS)


def build_execution_quality_summary(
    config: dict[str, Any],
    fills: pd.DataFrame,
    detail: pd.DataFrame,
    order_match: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    max_slippage = max_adverse_slippage_bps(config)
    if fills.empty:
        add("fills_imported", True, "none", "optional after execution", "No imported broker fills were found yet.")
        add("execution_quality_status", True, "no_fills_imported", "review after fill-import", "Execution quality review is pending until fills are imported.")
        return pd.DataFrame(rows, columns=EXECUTION_QUALITY_COLUMNS)

    matched = detail["matched"].map(parse_bool_like) if "matched" in detail else pd.Series(dtype=bool)
    unmatched_count = int((~matched).sum()) if not matched.empty else len(detail)
    missing_reference_count = int((detail["reference_price"].astype(str).str.strip() == "").sum()) if "reference_price" in detail else len(detail)
    slippage_values = pd.to_numeric(detail.get("adverse_slippage_bps", pd.Series(dtype=float)), errors="coerce").dropna()
    max_observed_slippage = float(slippage_values.max()) if not slippage_values.empty else 0.0
    avg_observed_slippage = float(slippage_values.mean()) if not slippage_values.empty else 0.0
    bad_slippage_count = int((slippage_values > max_slippage).sum()) if not slippage_values.empty else 0
    blocked_count = int((detail["status"].astype(str) != "ok").sum()) if "status" in detail else len(detail)
    imported_amount = float(pd.to_numeric(fills.get("amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    order_match_rows = len(order_match)
    explicit_match_failures = int((~order_match["matched"].map(parse_bool_like)).sum()) if "matched" in order_match else 0

    add("fill_count", True, len(fills), "> 0", "Imported broker fill row count.")
    add("fill_notional", True, round(imported_amount, 2), "informational", "Imported broker fill notional amount.")
    add("fill_order_match_passed", unmatched_count == 0 and explicit_match_failures == 0, unmatched_count + explicit_match_failures, 0, "Imported fills must not exceed or miss expected orders.")
    add("reference_price_present", missing_reference_count == 0, missing_reference_count, 0, "Every fill should have a system reference price from RPA tickets or orders.")
    add("max_adverse_slippage_bps", max_observed_slippage <= max_slippage, round(max_observed_slippage, 4), f"<= {max_slippage}", "Worst adverse fill slippage versus system reference price.")
    add("avg_adverse_slippage_bps", True, round(avg_observed_slippage, 4), "informational", "Average adverse fill slippage versus system reference price.")
    add("bad_slippage_count", bad_slippage_count == 0, bad_slippage_count, 0, "Number of fills beyond configured adverse slippage threshold.")
    add("fill_order_match_rows", order_match_rows > 0, order_match_rows, "> 0", "Order match audit rows produced by fill-import.")
    add("execution_quality_status", blocked_count == 0, "passed" if blocked_count == 0 else f"review_required:{blocked_count}", "passed", "Post-trade execution quality summary.")
    return pd.DataFrame(rows, columns=EXECUTION_QUALITY_COLUMNS)


def aggregate_fills(fills: pd.DataFrame) -> pd.DataFrame:
    if fills.empty:
        return pd.DataFrame(columns=["date", "symbol", "side", "quantity", "price", "amount"])
    out = fills.copy()
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(pd.to_numeric(out["quantity"], errors="coerce") * pd.to_numeric(out["price"], errors="coerce"))
    grouped = out.groupby(["date", "symbol", "side"], as_index=False).agg(quantity=("quantity", "sum"), amount=("amount", "sum"))
    grouped["price"] = grouped.apply(lambda row: float(row["amount"]) / float(row["quantity"]) if float(row["quantity"]) > 0 else 0.0, axis=1)
    return grouped[["date", "symbol", "side", "quantity", "price", "amount"]]


def build_reference_frame(rpa_orders: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if not rpa_orders.empty:
        frames.append(rpa_orders[["date", "symbol", "side", "quantity", "reference_price"]].copy())
    if not orders.empty:
        basic = orders[["date", "symbol", "side", "quantity"]].copy()
        basic["reference_price"] = 0.0
        frames.append(basic)
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "side", "quantity", "reference_price"])
    combined = pd.concat(frames, ignore_index=True)
    combined["has_price"] = pd.to_numeric(combined["reference_price"], errors="coerce").fillna(0.0) > 0
    combined = combined.sort_values(["date", "symbol", "side", "has_price"])
    grouped = combined.groupby(["date", "symbol", "side"], as_index=False).agg(quantity=("quantity", "max"), reference_price=("reference_price", "max"))
    return grouped.drop(columns=[column for column in ["has_price"] if column in grouped])


def reference_row(frame: pd.DataFrame, date_text: str, symbol: str, side: str) -> dict[str, object]:
    if frame.empty:
        return {}
    mask = (frame["date"].astype(str) == date_text) & (frame["symbol"].astype(str) == symbol) & (frame["side"].astype(str).str.lower() == side)
    rows = frame[mask]
    return rows.iloc[0].to_dict() if not rows.empty else {}


def match_row(frame: pd.DataFrame, date_text: str, symbol: str, side: str) -> dict[str, object]:
    if frame.empty:
        return {}
    mask = (frame["date"].astype(str) == date_text) & (frame["symbol"].astype(str) == symbol) & (frame["side"].astype(str).str.lower() == side)
    rows = frame[mask]
    return rows.iloc[0].to_dict() if not rows.empty else {}


def detail_status(order_quantity: int, matched: bool, reference_price: float, slippage: float | None, max_slippage: float) -> tuple[str, str]:
    if order_quantity <= 0:
        return "review", "missing_reference_order"
    if not matched:
        return "review", "fill_quantity_exceeds_order"
    if reference_price <= 0:
        return "review", "missing_reference_price"
    if slippage is not None and slippage > max_slippage:
        return "review", "adverse_slippage_exceeds_threshold"
    return "ok", "execution_quality_ok"


def adverse_slippage_bps(side: str, fill_price: float, reference_price: float) -> float | None:
    if reference_price <= 0 or fill_price <= 0:
        return None
    if side == "buy":
        return (fill_price - reference_price) / reference_price * 10000.0
    if side == "sell":
        return (reference_price - fill_price) / reference_price * 10000.0
    return None


def max_adverse_slippage_bps(config: dict[str, Any]) -> float:
    execution = config.get("execution", {})
    return float(execution.get("max_fill_adverse_slippage_bps", execution.get("max_fill_slippage_bps", 50.0)))


def normalize_fills(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol", "side", "quantity", "price", "fee", "amount", "reason"])
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["symbol"] = out["symbol"].map(normalize_symbol)
    out["side"] = out["side"].astype(str).str.lower().str.strip()
    out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce").fillna(0).astype(int)
    out["price"] = pd.to_numeric(out["price"], errors="coerce").fillna(0.0)
    out["fee"] = pd.to_numeric(out.get("fee", 0.0), errors="coerce").fillna(0.0)
    out["amount"] = pd.to_numeric(out.get("amount", out["quantity"] * out["price"]), errors="coerce").fillna(out["quantity"] * out["price"])
    out["reason"] = out.get("reason", "manual_fill")
    return out.dropna(subset=["date", "symbol"])[["date", "symbol", "side", "quantity", "price", "fee", "amount", "reason"]]


def normalize_order_match(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol", "side", "fill_quantity", "order_quantity", "matched", "message"])
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["symbol"] = out["symbol"].map(normalize_symbol)
    out["side"] = out["side"].astype(str).str.lower().str.strip()
    out["fill_quantity"] = pd.to_numeric(out.get("fill_quantity", 0), errors="coerce").fillna(0).astype(int)
    out["order_quantity"] = pd.to_numeric(out.get("order_quantity", 0), errors="coerce").fillna(0).astype(int)
    out["matched"] = out.get("matched", False).map(parse_bool_like)
    out["message"] = out.get("message", "")
    return out[["date", "symbol", "side", "fill_quantity", "order_quantity", "matched", "message"]]


def normalize_reference_orders(frame: pd.DataFrame, price_column: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "symbol", "side", "quantity", "reference_price"])
    required = {"date", "symbol", "side", "quantity"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=["date", "symbol", "side", "quantity", "reference_price"])
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["symbol"] = out["symbol"].map(normalize_symbol)
    out["side"] = out["side"].astype(str).str.lower().str.strip()
    out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce").fillna(0).astype(int)
    if price_column and price_column in out:
        out["reference_price"] = pd.to_numeric(out[price_column], errors="coerce").fillna(0.0)
    else:
        out["reference_price"] = 0.0
    return out.groupby(["date", "symbol", "side"], as_index=False).agg(quantity=("quantity", "sum"), reference_price=("reference_price", "max"))


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"symbol": str})
    except EmptyDataError:
        return pd.DataFrame()
