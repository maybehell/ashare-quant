from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from importlib import import_module
from typing import Any, Iterator

import pandas as pd
from .index_symbols import canonical_index_symbol, provider_index_symbol


PROVENANCE_COLUMNS = [
    "symbol",
    "source",
    "list_date_source",
    "industry_source",
    "industry_update_date",
    "retrieved_at",
]

HISTORY_FIELDS = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "tradestatus",
]


def result_frame(result: Any) -> pd.DataFrame:
    error_code = str(getattr(result, "error_code", ""))
    if error_code != "0":
        raise RuntimeError(str(getattr(result, "error_msg", "BaoStock query failed")))
    rows: list[list[str]] = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=list(getattr(result, "fields", [])))


@contextmanager
def baostock_session(module: Any | None = None) -> Iterator[Any]:
    client = module if module is not None else import_module("baostock")
    login = client.login()
    if str(getattr(login, "error_code", "")) != "0":
        raise RuntimeError(str(getattr(login, "error_msg", "BaoStock login failed")))
    try:
        yield client
    finally:
        client.logout()


def fetch_trade_calendar_frame_baostock(
    client: Any,
    start: date,
    end: date,
) -> pd.DataFrame:
    raw = result_frame(
        client.query_trade_dates(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
    )
    if raw.empty:
        raise RuntimeError("BaoStock returned empty trade calendar")
    dates = pd.to_datetime(raw.get("calendar_date"), errors="coerce").dt.date
    flags = raw.get("is_trading_day", pd.Series("0", index=raw.index)).astype(str).eq("1")
    retrieved_at = datetime.now().isoformat(timespec="seconds")
    out = pd.DataFrame(
        {
            "date": dates,
            "is_trading_day": flags,
            "source": "baostock_query_trade_dates",
            "retrieved_at": retrieved_at,
        }
    )
    return out.dropna(subset=["date"]).drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)

def enrich_stock_metadata_baostock(
    stocks: pd.DataFrame,
    symbols: list[str] | None = None,
    module: Any | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = stocks.copy()
    out["symbol"] = out["symbol"].map(normalize_baostock_symbol)
    out = out.drop_duplicates("symbol").set_index("symbol", drop=False)
    wanted = set(out.index if symbols is None else (normalize_baostock_symbol(value) for value in symbols))

    with baostock_session(module) as client:
        basic = result_frame(client.query_stock_basic())
        industry = result_frame(client.query_stock_industry())

    if not basic.empty:
        basic["symbol"] = basic["code"].map(normalize_baostock_symbol)
        if "type" in basic:
            basic = basic[basic["type"].astype(str) == "1"]
        basic = basic[basic["symbol"].isin(wanted)].drop_duplicates("symbol", keep="last").set_index("symbol")
    if not industry.empty:
        industry["symbol"] = industry["code"].map(normalize_baostock_symbol)
        industry = industry[industry["symbol"].isin(wanted)]
        if "updateDate" in industry:
            industry = industry.sort_values("updateDate")
        industry = industry.drop_duplicates("symbol", keep="last").set_index("symbol")

    retrieved_at = datetime.now().isoformat(timespec="seconds")
    provenance: list[dict[str, object]] = []
    for symbol in sorted(wanted & set(out.index)):
        list_source = ""
        industry_source = ""
        industry_update = ""
        if symbol in basic.index:
            row = basic.loc[symbol]
            ipo_date = clean_date(row.get("ipoDate", ""))
            if ipo_date:
                out.at[symbol, "list_date"] = ipo_date
                list_source = "query_stock_basic"
            current_name = str(out.at[symbol, "name"] or "").strip()
            code_name = str(row.get("code_name", "") or "").strip()
            if code_name and (not current_name or current_name.lower() == "unknown"):
                out.at[symbol, "name"] = code_name
        if symbol in industry.index:
            row = industry.loc[symbol]
            value = str(row.get("industry", "") or "").strip()
            if value:
                out.at[symbol, "industry"] = value
                industry_source = "query_stock_industry"
                industry_update = str(row.get("updateDate", "") or "").strip()
        provenance.append(
            {
                "symbol": symbol,
                "source": "baostock",
                "list_date_source": list_source,
                "industry_source": industry_source,
                "industry_update_date": industry_update,
                "retrieved_at": retrieved_at,
            }
        )
    return out.reset_index(drop=True), pd.DataFrame(provenance, columns=PROVENANCE_COLUMNS)


def fetch_history_frame_baostock(
    client: Any,
    symbol: str,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, dict[str, object]]:
    clean_symbol = normalize_baostock_symbol(symbol)
    result = client.query_history_k_data_plus(
        baostock_code(clean_symbol),
        ",".join(HISTORY_FIELDS),
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag="2",
    )
    raw = result_frame(result)
    if raw.empty:
        raise RuntimeError("BaoStock returned empty history")
    if "tradestatus" in raw:
        raw = raw[raw["tradestatus"].astype(str) == "1"].copy()
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw.get("date"), errors="coerce").dt.date,
            "symbol": clean_symbol,
            "open": pd.to_numeric(raw.get("open"), errors="coerce"),
            "high": pd.to_numeric(raw.get("high"), errors="coerce"),
            "low": pd.to_numeric(raw.get("low"), errors="coerce"),
            "close": pd.to_numeric(raw.get("close"), errors="coerce"),
            "volume": pd.to_numeric(raw.get("volume"), errors="coerce"),
            "amount": pd.to_numeric(raw.get("amount"), errors="coerce"),
        }
    )
    out = out.dropna(subset=["date", "open", "high", "low", "close", "volume", "amount"])
    out = out.drop_duplicates(["date", "symbol"]).sort_values("date").reset_index(drop=True)
    if out.empty:
        raise RuntimeError("BaoStock history has no active trading rows")
    provenance = {
        "symbol": clean_symbol,
        "source": "baostock_query_history_k_data_plus_qfq",
        "first_date": out["date"].min(),
        "last_date": out["date"].max(),
        "rows": len(out),
        "amount_quality": "reported",
    }
    return out, provenance


def fetch_benchmark_frame_baostock(
    client: Any,
    symbol: str,
    name: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    clean_symbol = canonical_index_symbol(symbol)
    result = client.query_history_k_data_plus(
        baostock_index_code(clean_symbol),
        "date,code,close",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag="3",
    )
    raw = result_frame(result)
    if raw.empty:
        raise RuntimeError("BaoStock returned empty benchmark history")
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(raw.get("date"), errors="coerce").dt.date,
            "symbol": clean_symbol,
            "name": name,
            "close": pd.to_numeric(raw.get("close"), errors="coerce"),
        }
    )
    return out.dropna(subset=["date", "close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def baostock_code(symbol: object) -> str:
    clean = normalize_baostock_symbol(symbol)
    exchange = "sh" if clean.startswith(("5", "6", "9")) else "sz"
    return f"{exchange}.{clean}"


def baostock_index_code(symbol: object) -> str:
    return provider_index_symbol(symbol, "baostock")


def normalize_baostock_symbol(value: object) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    digits = "".join(char for char in text if char.isdigit())
    return digits.zfill(6) if digits and len(digits) <= 6 else text


def clean_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    return "" if pd.isna(parsed) else parsed.date().isoformat()
