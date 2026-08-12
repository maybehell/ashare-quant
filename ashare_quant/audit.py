from __future__ import annotations

import hashlib
from copy import deepcopy
import json
from datetime import datetime
from typing import Any


from .index_symbols import canonical_index_symbol
import pandas as pd


def build_run_manifest(
    config: dict[str, Any],
    stocks: pd.DataFrame | None = None,
    prices: pd.DataFrame | None = None,
    fundamentals: pd.DataFrame | None = None,
    benchmark: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows = [
        {"item": "generated_at", "value": datetime.now().isoformat(timespec="seconds")},
        {"item": "config_sha256", "value": mapping_sha256(config)},
        {"item": "backtest_config_sha256", "value": backtest_config_sha256(config)},
        {"item": "strategy_name", "value": str(config.get("strategy", {}).get("name", ""))},
        {"item": "backtest_start", "value": str(config.get("backtest", {}).get("start", ""))},
        {"item": "backtest_end", "value": str(config.get("backtest", {}).get("end", ""))},
    ]
    effective_stocks = effective_stocks_for_prices(stocks, prices)
    frames = {
        "stocks": effective_stocks,
        "stock_catalog": stocks,
        "prices": prices,
        "fundamentals": fundamentals,
        "benchmark": benchmark,
    }
    membership = effective_index_membership(config)
    if membership is not None:
        frames["index_membership"] = membership
    for name, frame in frames.items():
        rows.extend(frame_manifest_rows(name, frame if frame is not None else pd.DataFrame()))
    return pd.DataFrame(rows, columns=["item", "value"])


def effective_stocks_for_prices(
    stocks: pd.DataFrame | None,
    prices: pd.DataFrame | None,
) -> pd.DataFrame:
    stock_frame = stocks if stocks is not None else pd.DataFrame()
    price_frame = prices if prices is not None else pd.DataFrame()
    if stock_frame.empty or price_frame.empty or "symbol" not in stock_frame or "symbol" not in price_frame:
        return stock_frame.copy()
    symbols = set(price_frame["symbol"].astype(str))
    effective = stock_frame[stock_frame["symbol"].astype(str).isin(symbols)].copy()
    return effective.sort_values("symbol").reset_index(drop=True)


def effective_index_membership(config: dict[str, Any]) -> pd.DataFrame | None:
    policy = config.get("strategy", {}).get("benchmark_membership", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return None
    from .index_membership import load_membership_from_config

    membership = load_membership_from_config(config)
    if membership.empty:
        return membership
    index_symbol = canonical_index_symbol(
        policy.get("index_symbol", config.get("benchmark", {}).get("symbol", ""))
    )
    if index_symbol and "index_symbol" in membership:
        membership = membership[
            membership["index_symbol"].map(canonical_index_symbol) == index_symbol
        ].copy()
    end_value = config.get("backtest", {}).get("end")
    if end_value:
        end = pd.to_datetime(end_value).date()
        membership = membership[membership["snapshot_date"] <= end].copy()
    return membership.sort_values(["snapshot_date", "index_symbol", "symbol"]).reset_index(drop=True)


BACKTEST_RELEVANT_CONFIG_KEYS = (
    "capital_budget",
    "data",
    "universe",
    "backtest",
    "benchmark",
    "risk_overlay",
    "breadth_overlay",
    "strategy",
    "trading",
    "rules",
    "stress_test",
)


DATA_OPERATIONAL_CONFIG_KEYS = {
    "checkpoint_every",
    "commit_price_checkpoint",
    "disable_requests_proxy",
    "enrich_metadata",
    "force_cached_universe",
    "benchmark_fallback_to_baostock",
    "history_fallback_to_baostock",
    "history_backfill_tolerance_days",
    "history_allow_estimated_amount_fallback",
    "history_resume_require_trusted_provenance",
    "history_overlap_tail_rows",
    "history_overlap_min_rows",
    "history_overlap_max_close_median_relative_error",
    "history_overlap_max_volume_median_relative_error",
    "history_overlap_max_amount_median_relative_error",
    "history_retries",
    "history_source",
    "history_workers",
    "include_account_positions_on_fetch",
    "incremental_fetch",
    "incremental_overlap_days",
    "max_symbols",
    "metadata_checkpoint_every",
    "metadata_fallback_to_akshare",
    "metadata_priced_only",
    "metadata_source",
    "metadata_workers",
    "request_timeout_seconds",
    "resume_partial_history",
    "spot_retries",
    "use_cached_universe_on_spot_failure",
}


def backtest_relevant_config(config: dict[str, Any]) -> dict[str, Any]:
    relevant = {key: deepcopy(config[key]) for key in BACKTEST_RELEVANT_CONFIG_KEYS if key in config}
    data = relevant.get("data")
    if isinstance(data, dict):
        for key in DATA_OPERATIONAL_CONFIG_KEYS:
            data.pop(key, None)
    return relevant


def backtest_config_sha256(config: dict[str, Any]) -> str:
    return mapping_sha256(backtest_relevant_config(config))


def capital_sensitivity_config_sha256(config: dict[str, Any]) -> str:
    relevant = backtest_relevant_config(config)
    backtest = relevant.get("backtest")
    if isinstance(backtest, dict):
        backtest.pop("initial_cash", None)
    validation = config.get("validation", {})
    sensitivity = validation.get("capital_sensitivity", {}) if isinstance(validation, dict) else {}
    if isinstance(sensitivity, dict):
        relevant["capital_sensitivity_validation"] = deepcopy(sensitivity)
    return mapping_sha256(relevant)


def mapping_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def frame_manifest_rows(name: str, frame: pd.DataFrame) -> list[dict[str, str]]:
    if frame.empty:
        return [
            {"item": f"{name}_rows", "value": "0"},
            {"item": f"{name}_sha256", "value": ""},
        ]
    rows = [
        {"item": f"{name}_rows", "value": str(len(frame))},
        {"item": f"{name}_columns", "value": ",".join(map(str, frame.columns))},
        {"item": f"{name}_sha256", "value": frame_sha256(frame)},
    ]
    if "date" in frame.columns:
        dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
        if not dates.empty:
            rows.append({"item": f"{name}_first_date", "value": dates.min().date().isoformat()})
            rows.append({"item": f"{name}_last_date", "value": dates.max().date().isoformat()})
    if "symbol" in frame.columns:
        rows.append({"item": f"{name}_symbol_count", "value": str(frame["symbol"].astype(str).nunique())})
    return rows


def frame_sha256(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    for column in normalized.columns:
        if pd.api.types.is_object_dtype(normalized[column]) or pd.api.types.is_string_dtype(normalized[column]):
            normalized[column] = normalized[column].map(lambda value: "" if pd.isna(value) else str(value))
    hashed = pd.util.hash_pandas_object(normalized, index=False).values.tobytes()
    return hashlib.sha256(hashed).hexdigest()


def signal_identity_frame(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["signal_date", "execution_date", "symbol", "target_weight"]
    normalized = pd.DataFrame(index=frame.index, columns=columns)
    for column in ("signal_date", "execution_date"):
        values = frame[column] if column in frame else pd.Series("", index=frame.index)
        normalized[column] = values.map(
            lambda value: "" if pd.isna(value) else str(value)
        )
    symbols = frame["symbol"] if "symbol" in frame else pd.Series("", index=frame.index)
    normalized["symbol"] = symbols.map(
        lambda value: "" if pd.isna(value) else str(value).zfill(6)
    )
    weights = (
        frame["target_weight"]
        if "target_weight" in frame
        else pd.Series(0.0, index=frame.index)
    )
    normalized["target_weight"] = (
        pd.to_numeric(weights, errors="coerce").fillna(0.0).round(12)
    )
    return normalized.sort_values(columns[:3]).reset_index(drop=True)


def signal_frame_sha256(frame: pd.DataFrame) -> str:
    return frame_sha256(signal_identity_frame(frame))


def signal_identity_frames_equal(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    return signal_identity_frame(left).equals(signal_identity_frame(right))

def orders_frame_sha256(frame: pd.DataFrame) -> str:
    columns = ["date", "symbol", "side", "quantity", "reason"]
    normalized = pd.DataFrame(index=frame.index, columns=columns)
    if frame.empty:
        return frame_sha256(pd.DataFrame(columns=columns))
    dates = pd.to_datetime(frame["date"], errors="coerce") if "date" in frame else pd.Series(pd.NaT, index=frame.index)
    normalized["date"] = dates.map(lambda value: "" if pd.isna(value) else value.date().isoformat())
    symbols = frame["symbol"] if "symbol" in frame else pd.Series("", index=frame.index)
    normalized["symbol"] = symbols.map(lambda value: "" if pd.isna(value) else str(value).zfill(6))
    sides = frame["side"] if "side" in frame else pd.Series("", index=frame.index)
    normalized["side"] = sides.map(lambda value: "" if pd.isna(value) else str(value).lower())
    quantities = frame["quantity"] if "quantity" in frame else pd.Series(0, index=frame.index)
    normalized["quantity"] = pd.to_numeric(quantities, errors="coerce").fillna(0).astype(int)
    reasons = frame["reason"] if "reason" in frame else pd.Series("", index=frame.index)
    normalized["reason"] = reasons.map(lambda value: "" if pd.isna(value) else str(value))
    return frame_sha256(normalized[columns])
