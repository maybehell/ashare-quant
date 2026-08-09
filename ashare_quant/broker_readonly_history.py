from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv


HISTORY_COLUMNS = [
    "history_sequence",
    "snapshot_run_id",
    "broker",
    "account_id",
    "environment",
    "cash",
    "available_cash",
    "market_value",
    "total_asset",
    "frozen_cash",
    "unclassified_asset_value",
    "positions_count",
    "positions_sha256",
    "broker_time",
    "generated_at",
    "observation_date",
    "auto_live_config_sha256",
    "previous_row_sha256",
    "row_sha256",
    "recorded_at",
]

AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]
ZERO_HASH = "0" * 64
NUMERIC_FIELDS = [
    "cash",
    "available_cash",
    "market_value",
    "total_asset",
    "frozen_cash",
    "unclassified_asset_value",
]


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _decimal_text(value: Any) -> str:
    try:
        number = Decimal(_text(value) or "0")
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric account value: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"non-finite account value: {value!r}")
    return format(number.quantize(Decimal("0.01")), "f")


def positions_sha256(positions: pd.DataFrame) -> str:
    columns = [
        "snapshot_run_id",
        "symbol",
        "quantity",
        "sellable_quantity",
        "cost",
        "market_value",
        "last_price",
    ]
    records: list[dict[str, str]] = []
    if not positions.empty:
        available = [column for column in columns if column in positions.columns]
        ordered = positions[available].copy()
        if "symbol" in ordered.columns:
            ordered = ordered.sort_values("symbol", kind="stable")
        for record in ordered.to_dict("records"):
            records.append({column: _text(record.get(column, "")) for column in columns})
    payload = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_sha256(row: dict[str, Any]) -> str:
    payload = {column: _text(row.get(column, "")) for column in HISTORY_COLUMNS if column != "row_sha256"}
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def append_history_frame(
    history: pd.DataFrame,
    snapshot: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    recorded_at: str | None = None,
) -> pd.DataFrame:
    if len(snapshot) != 1:
        raise ValueError("exactly one successful broker account snapshot is required")
    current = snapshot.iloc[0]
    run_id = _text(current.get("snapshot_run_id"))
    account_id = _text(current.get("account_id"))
    if not run_id or not account_id or _text(current.get("environment")).lower() != "live":
        raise ValueError("snapshot must contain a live environment, run ID, and masked account ID")

    existing = history.copy()
    if not existing.empty and "snapshot_run_id" in existing.columns:
        if run_id in existing["snapshot_run_id"].map(_text).tolist():
            return existing.reindex(columns=HISTORY_COLUMNS)

    generated_at = _text(current.get("generated_at"))
    try:
        observation_date = pd.Timestamp(generated_at).date().isoformat()
    except (TypeError, ValueError):
        observation_date = ""
    recorded = recorded_at or datetime.now().astimezone().isoformat(timespec="seconds")
    previous_hash = ZERO_HASH if existing.empty else _text(existing.iloc[-1].get("row_sha256"))
    row: dict[str, Any] = {
        "history_sequence": len(existing) + 1,
        "snapshot_run_id": run_id,
        "broker": _text(current.get("broker")),
        "account_id": account_id,
        "environment": _text(current.get("environment")),
        "positions_count": int(Decimal(_text(current.get("positions_count")) or "0")),
        "positions_sha256": positions_sha256(positions),
        "broker_time": _text(current.get("broker_time")),
        "generated_at": generated_at,
        "observation_date": observation_date,
        "auto_live_config_sha256": _text(current.get("auto_live_config_sha256")),
        "previous_row_sha256": previous_hash,
        "recorded_at": recorded,
    }
    for field in NUMERIC_FIELDS:
        row[field] = _decimal_text(current.get(field))
    row["row_sha256"] = _row_sha256(row)
    if existing.empty:
        return pd.DataFrame([row], columns=HISTORY_COLUMNS)
    return pd.concat([existing.reindex(columns=HISTORY_COLUMNS), pd.DataFrame([row])], ignore_index=True)


def build_history_audit(
    history: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    current_positions: pd.DataFrame,
    *,
    minimum_samples: int = 3,
    minimum_dates: int = 2,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(check: str, passed: bool, value: Any, threshold: Any, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    present = not history.empty and set(HISTORY_COLUMNS).issubset(history.columns)
    add("history_present", present, len(history), ">= 1", "At least one successful read-only snapshot must be retained.")

    chain_valid = present
    sequence_valid = present
    if present:
        expected_previous = ZERO_HASH
        for index, record in enumerate(history.to_dict("records"), start=1):
            sequence_valid &= _text(record.get("history_sequence")) == str(index)
            chain_valid &= _text(record.get("previous_row_sha256")) == expected_previous
            chain_valid &= _text(record.get("row_sha256")) == _row_sha256(record)
            expected_previous = _text(record.get("row_sha256"))
    add("hash_chain_valid", chain_valid, chain_valid, True, "Every retained row must authenticate itself and its predecessor.")
    add("history_sequence_contiguous", sequence_valid, sequence_valid, True, "History sequence numbers must be contiguous.")

    run_ids = history.get("snapshot_run_id", pd.Series(dtype=str)).map(_text)
    unique_runs = present and run_ids.nunique() == len(history)
    add("snapshot_run_ids_unique", unique_runs, f"{run_ids.nunique()}/{len(history)}", f"{len(history)}/{len(history)}", "Snapshot run IDs cannot be reused.")

    account_ids = history.get("account_id", pd.Series(dtype=str)).map(_text)
    account_stable = present and account_ids.ne("").all() and account_ids.nunique() == 1
    add("account_identity_stable", account_stable, account_ids.nunique(), 1, "All observations must belong to one masked broker account.")
    environments = history.get("environment", pd.Series(dtype=str)).map(lambda value: _text(value).lower())
    live_only = present and environments.eq("live").all()
    add("live_environment_only", live_only, sorted(environments.unique().tolist()), ["live"], "Only real-account read-only observations count.")

    numeric_valid = present
    if present:
        for record in history.to_dict("records"):
            try:
                values = {field: Decimal(_text(record.get(field)) or "0") for field in NUMERIC_FIELDS}
                numeric_valid &= all(value.is_finite() and value >= 0 for value in values.values())
                numeric_valid &= values["available_cash"] <= values["cash"]
                reconciled = values["cash"] + values["market_value"] + values["unclassified_asset_value"]
                numeric_valid &= abs(values["total_asset"] - reconciled) <= Decimal("0.02")
            except InvalidOperation:
                numeric_valid = False
    add("account_values_reconcile", numeric_valid, numeric_valid, True, "Cash, market value, and unclassified assets must reconcile to total assets.")

    current_match = False
    if present and len(current_snapshot) == 1:
        latest = history.iloc[-1]
        current = current_snapshot.iloc[0]
        current_match = (
            _text(latest.get("snapshot_run_id")) == _text(current.get("snapshot_run_id"))
            and _text(latest.get("account_id")) == _text(current.get("account_id"))
            and _text(latest.get("positions_sha256")) == positions_sha256(current_positions)
            and _text(latest.get("auto_live_config_sha256")) == _text(current.get("auto_live_config_sha256"))
        )
    add("latest_snapshot_current", current_match, current_match, True, "The history tail must match the current snapshot, positions, and configuration.")

    integrity_checks = {
        "history_present",
        "hash_chain_valid",
        "history_sequence_contiguous",
        "snapshot_run_ids_unique",
        "account_identity_stable",
        "live_environment_only",
        "account_values_reconcile",
        "latest_snapshot_current",
    }
    integrity = all(row["passed"] for row in rows if row["check"] in integrity_checks)
    add("broker_readonly_history_integrity", integrity, integrity, True, "The append-only read-only history must pass every integrity check.")

    dates = history.get("observation_date", pd.Series(dtype=str)).map(_text)
    distinct_dates = dates[dates.ne("")].nunique()
    depth = integrity and len(history) >= minimum_samples and distinct_dates >= minimum_dates
    add(
        "broker_readonly_history_depth",
        depth,
        f"samples={len(history)};dates={distinct_dates}",
        f"samples>={minimum_samples};dates>={minimum_dates}",
        "Repeated successful observations on different dates are required before execution review.",
    )
    return pd.DataFrame(rows, columns=AUDIT_COLUMNS)


def write_broker_readonly_history(live_config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(live_config["paths"]["output_dir"])
    snapshot_path = output_dir / "auto_live_broker_account_snapshot.csv"
    positions_path = output_dir / "auto_live_broker_positions.csv"
    history_path = output_dir / "huatai_read_only_account_history.csv"
    audit_path = output_dir / "huatai_read_only_history_audit.csv"

    snapshot = pd.read_csv(snapshot_path, dtype=str, keep_default_na=False)
    positions = pd.read_csv(positions_path, dtype=str, keep_default_na=False)
    history = (
        pd.read_csv(history_path, dtype=str, keep_default_na=False)
        if history_path.exists()
        else pd.DataFrame(columns=HISTORY_COLUMNS)
    )
    updated = append_history_frame(history, snapshot, positions)
    audit = build_history_audit(updated, snapshot, positions)
    atomic_to_csv(updated, history_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    return {
        "huatai_read_only_account_history.csv": history_path,
        "huatai_read_only_history_audit.csv": audit_path,
    }
