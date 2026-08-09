from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import frame_sha256, mapping_sha256
from .config import project_path
from .autolive_integrity import DEFAULT_INTEGRITY_KEY_ENV, MIN_HMAC_KEY_BYTES
from .strategy_activation import current_execution_epoch


ORDER_INTEGRITY_ALGORITHM = "hmac-sha256"
ORDER_INTEGRITY_SCHEMA = "ashare_quant.order_provenance.v1"
ORDER_INTEGRITY_FIELDS = {
    "order_integrity_schema",
    "order_integrity_algorithm",
    "order_integrity_required",
    "order_integrity_key_env",
    "order_integrity_payload_sha256",
    "order_integrity_hmac_sha256",
}

RPA_TICKET_INTEGRITY_SCHEMA = "ashare_quant.rpa_ticket_manifest.v1"
RPA_TICKET_INTEGRITY_FILE = "rpa_orders_integrity.json"


def order_integrity_settings(config: dict[str, Any]) -> dict[str, Any]:
    rpa = config.get("rpa", {}) if isinstance(config.get("rpa"), dict) else {}
    required = _bool(rpa.get("require_order_provenance_hmac", False))
    key_env = str(rpa.get("order_provenance_hmac_key_env", DEFAULT_INTEGRITY_KEY_ENV) or DEFAULT_INTEGRITY_KEY_ENV)
    key = os.environ.get(key_env, "").encode("utf-8") if key_env else b""
    return {
        "required": required,
        "key_env": key_env,
        "key": key,
        "key_bytes": len(key),
        "ready": (not required) or len(key) >= MIN_HMAC_KEY_BYTES,
    }


def attach_order_provenance_integrity(config: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    settings = order_integrity_settings(config)
    required = bool(settings["required"])
    key_env = str(settings["key_env"])
    key = bytes(settings["key"])
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        raise RuntimeError(
            f"order provenance HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes in {key_env}"
        )
    clean = canonical_order_provenance(provenance)
    payload_hash = mapping_sha256(clean)
    signature = _sign(key, _signature_text(required, key_env, payload_hash)) if key else ""
    result = dict(provenance)
    result.update(
        {
            "order_integrity_schema": ORDER_INTEGRITY_SCHEMA,
            "order_integrity_algorithm": ORDER_INTEGRITY_ALGORITHM,
            "order_integrity_required": required,
            "order_integrity_key_env": key_env,
            "order_integrity_payload_sha256": payload_hash,
            "order_integrity_hmac_sha256": signature,
        }
    )
    return result


def verify_order_provenance_integrity(
    config: dict[str, Any], provenance: dict[str, Any]
) -> tuple[bool, str]:
    settings = order_integrity_settings(config)
    required = bool(settings["required"])
    key_env = str(settings["key_env"])
    key = bytes(settings["key"])
    schema = str(provenance.get("order_integrity_schema", "") or "")
    recorded_hash = str(provenance.get("order_integrity_payload_sha256", "") or "")
    signature = str(provenance.get("order_integrity_hmac_sha256", "") or "")
    if not schema and not recorded_hash and not signature:
        return (False, "order_integrity_missing") if required else (True, "order_integrity_optional_missing")
    if schema != ORDER_INTEGRITY_SCHEMA:
        return False, "order_integrity_schema_mismatch"
    if str(provenance.get("order_integrity_algorithm", "")) != ORDER_INTEGRITY_ALGORITHM:
        return False, "order_integrity_algorithm_mismatch"
    recorded_required = _bool(provenance.get("order_integrity_required", False))
    if recorded_required != required:
        return False, "order_integrity_required_flag_mismatch"
    if str(provenance.get("order_integrity_key_env", "")) != key_env:
        return False, "order_integrity_key_env_mismatch"
    actual_hash = mapping_sha256(canonical_order_provenance(provenance))
    if not recorded_hash or not hmac.compare_digest(recorded_hash, actual_hash):
        return False, "order_integrity_payload_hash_mismatch"
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        return False, "order_integrity_key_missing_or_weak"
    if required and not signature:
        return False, "order_integrity_signature_missing"
    if signature:
        if not key:
            return False, "order_integrity_key_missing"
        expected = _sign(key, _signature_text(required, key_env, actual_hash))
        if not hmac.compare_digest(signature, expected):
            return False, "order_integrity_signature_mismatch"
        return True, "order_integrity_hmac_valid"
    return True, "order_integrity_hash_valid"


def write_rpa_ticket_integrity_manifest(
    config: dict[str, Any],
    tickets: pd.DataFrame,
    provenance: dict[str, Any],
    path: Path | None = None,
) -> Path:
    settings = order_integrity_settings(config)
    required = bool(settings["required"])
    key_env = str(settings["key_env"])
    key = bytes(settings["key"])
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        raise RuntimeError(
            f"RPA ticket HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes in {key_env}"
        )
    output_dir = project_path(config["paths"]["output_dir"])
    target = path or output_dir / RPA_TICKET_INTEGRITY_FILE
    payload = {
        "schema": RPA_TICKET_INTEGRITY_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config_sha256": mapping_sha256(config),
        "orders_sha256": str(provenance.get("orders_sha256", "") or ""),
        "order_provenance_payload_sha256": str(provenance.get("order_integrity_payload_sha256", "") or ""),
        "execution_epoch_id": str(provenance.get("execution_epoch_id", "") or ""),
        "tickets_sha256": rpa_tickets_sha256(tickets),
        "ticket_count": int(len(tickets)),
    }
    payload_hash = mapping_sha256(payload)
    payload["integrity"] = {
        "algorithm": ORDER_INTEGRITY_ALGORITHM,
        "required": required,
        "key_env": key_env,
        "payload_sha256": payload_hash,
        "hmac_sha256": _sign(key, _ticket_signature_text(required, key_env, payload_hash)) if key else "",
    }
    _atomic_json(target, payload)
    return target


def verify_rpa_ticket_integrity_manifest(
    config: dict[str, Any],
    tickets: pd.DataFrame,
    path: Path | None = None,
) -> tuple[bool, str]:
    settings = order_integrity_settings(config)
    required = bool(settings["required"])
    key_env = str(settings["key_env"])
    key = bytes(settings["key"])
    output_dir = project_path(config["paths"]["output_dir"])
    target = path or output_dir / RPA_TICKET_INTEGRITY_FILE
    if not target.is_file():
        return (False, "rpa_ticket_integrity_missing") if required else (True, "rpa_ticket_integrity_optional_missing")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "rpa_ticket_integrity_unreadable"
    if not isinstance(payload, dict) or payload.get("schema") != RPA_TICKET_INTEGRITY_SCHEMA:
        return False, "rpa_ticket_integrity_schema_mismatch"
    if str(payload.get("config_sha256", "")) != mapping_sha256(config):
        return False, "rpa_ticket_integrity_config_mismatch"
    if int(pd.to_numeric(payload.get("ticket_count", -1), errors="coerce")) != len(tickets):
        return False, "rpa_ticket_integrity_count_mismatch"
    if str(payload.get("tickets_sha256", "")) != rpa_tickets_sha256(tickets):
        return False, "rpa_ticket_integrity_tickets_hash_mismatch"

    provenance_path = output_dir / "orders_provenance.csv"
    if not provenance_path.is_file():
        return False, "rpa_ticket_integrity_order_provenance_missing"
    try:
        frame = pd.read_csv(provenance_path)
    except Exception:
        return False, "rpa_ticket_integrity_order_provenance_unreadable"
    provenance = {
        str(row.item): "" if pd.isna(row.value) else str(row.value)
        for row in frame.itertuples(index=False)
    } if not frame.empty and {"item", "value"}.issubset(frame.columns) else {}
    provenance_ok, provenance_reason = verify_order_provenance_integrity(config, provenance)
    if not provenance_ok:
        return False, f"rpa_ticket_integrity_{provenance_reason}"
    if str(payload.get("orders_sha256", "")) != str(provenance.get("orders_sha256", "")):
        return False, "rpa_ticket_integrity_orders_hash_mismatch"
    if str(payload.get("order_provenance_payload_sha256", "")) != str(provenance.get("order_integrity_payload_sha256", "")):
        return False, "rpa_ticket_integrity_provenance_hash_mismatch"
    if str(payload.get("execution_epoch_id", "") or "") != str(provenance.get("execution_epoch_id", "") or ""):
        return False, "rpa_ticket_integrity_execution_epoch_mismatch"
    try:
        current_epoch = current_execution_epoch(config)
    except RuntimeError:
        return False, "rpa_ticket_integrity_current_execution_epoch_invalid"
    if (
        not str(payload.get("execution_epoch_id", "") or "")
        or str(payload.get("execution_epoch_id", "") or "") != current_epoch["execution_epoch_id"]
    ):
        return False, "rpa_ticket_integrity_execution_epoch_stale"

    integrity = payload.get("integrity", {}) if isinstance(payload.get("integrity"), dict) else {}
    if str(integrity.get("algorithm", "")) != ORDER_INTEGRITY_ALGORITHM:
        return False, "rpa_ticket_integrity_algorithm_mismatch"
    recorded_required = _bool(integrity.get("required", False))
    if recorded_required != required:
        return False, "rpa_ticket_integrity_required_flag_mismatch"
    if str(integrity.get("key_env", "")) != key_env:
        return False, "rpa_ticket_integrity_key_env_mismatch"
    clean = {str(name): value for name, value in payload.items() if str(name) != "integrity"}
    actual_hash = mapping_sha256(clean)
    recorded_hash = str(integrity.get("payload_sha256", ""))
    if not recorded_hash or not hmac.compare_digest(recorded_hash, actual_hash):
        return False, "rpa_ticket_integrity_payload_hash_mismatch"
    signature = str(integrity.get("hmac_sha256", ""))
    if required and len(key) < MIN_HMAC_KEY_BYTES:
        return False, "rpa_ticket_integrity_key_missing_or_weak"
    if required and not signature:
        return False, "rpa_ticket_integrity_signature_missing"
    if signature:
        if not key:
            return False, "rpa_ticket_integrity_key_missing"
        expected = _sign(key, _ticket_signature_text(required, key_env, actual_hash))
        if not hmac.compare_digest(signature, expected):
            return False, "rpa_ticket_integrity_signature_mismatch"
        return True, "rpa_ticket_integrity_hmac_valid"
    return True, "rpa_ticket_integrity_hash_valid"


def verify_rpa_ticket_execution_subset(
    config: dict[str, Any],
    tickets: pd.DataFrame,
    source_tickets: pd.DataFrame | None = None,
) -> tuple[bool, str]:
    output_dir = project_path(config["paths"]["output_dir"])
    source = source_tickets if source_tickets is not None else _read_ticket_csv(output_dir / "rpa_orders.csv")
    manifest_ok, manifest_reason = verify_rpa_ticket_integrity_manifest(config, source)
    if not manifest_ok:
        return False, manifest_reason
    manifest_path = output_dir / RPA_TICKET_INTEGRITY_FILE
    if not manifest_path.is_file():
        return True, manifest_reason

    source_rows = Counter(map(tuple, canonical_rpa_tickets(source).itertuples(index=False, name=None)))
    selected_rows = Counter(map(tuple, canonical_rpa_tickets(tickets).itertuples(index=False, name=None)))
    if any(count > source_rows.get(row, 0) for row, count in selected_rows.items()):
        return False, "rpa_ticket_execution_subset_mismatch"
    return True, "rpa_ticket_execution_subset_hmac_valid" if manifest_reason.endswith("hmac_valid") else "rpa_ticket_execution_subset_hash_valid"


def rpa_tickets_sha256(tickets: pd.DataFrame) -> str:
    return frame_sha256(canonical_rpa_tickets(tickets))


def canonical_rpa_tickets(tickets: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ticket_id", "date", "symbol", "side", "quantity",
        "limit_price", "estimated_amount", "status", "message",
    ]
    normalized = pd.DataFrame(index=tickets.index, columns=columns)
    if tickets.empty:
        return pd.DataFrame(columns=columns)
    ticket_ids = tickets.get("ticket_id", pd.Series("", index=tickets.index))
    normalized["ticket_id"] = ticket_ids.map(lambda value: "" if pd.isna(value) else str(value))
    dates = pd.to_datetime(tickets.get("date", pd.Series(pd.NaT, index=tickets.index)), errors="coerce")
    normalized["date"] = dates.map(lambda value: "" if pd.isna(value) else value.date().isoformat())
    symbols = tickets.get("symbol", pd.Series("", index=tickets.index))
    normalized["symbol"] = symbols.map(
        lambda value: "" if pd.isna(value) else str(value).split(".")[0].zfill(6)
    )
    sides = tickets.get("side", pd.Series("", index=tickets.index))
    normalized["side"] = sides.map(lambda value: "" if pd.isna(value) else str(value).strip().lower())
    normalized["quantity"] = pd.to_numeric(
        tickets.get("quantity", pd.Series(0, index=tickets.index)), errors="coerce"
    ).fillna(0).astype(int)
    for column in ["limit_price", "estimated_amount"]:
        normalized[column] = pd.to_numeric(
            tickets.get(column, pd.Series(0.0, index=tickets.index)), errors="coerce"
        ).fillna(0.0).astype(float).round(4)
    statuses = tickets.get("status", pd.Series("", index=tickets.index))
    normalized["status"] = statuses.map(lambda value: "" if pd.isna(value) else str(value).strip().lower())
    messages = tickets.get("message", pd.Series("", index=tickets.index))
    normalized["message"] = messages.map(lambda value: "" if pd.isna(value) else str(value))
    return normalized[columns]


def _read_ticket_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"symbol": str})
    except Exception:
        return pd.DataFrame()


def canonical_order_provenance(provenance: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): "" if value is None else str(value)
        for key, value in provenance.items()
        if str(key) not in ORDER_INTEGRITY_FIELDS
    }


def _signature_text(required: bool, key_env: str, payload_hash: str) -> str:
    return f"order_provenance|{ORDER_INTEGRITY_SCHEMA}|{required}|{key_env}|{payload_hash}"


def _ticket_signature_text(required: bool, key_env: str, payload_hash: str) -> str:
    return f"rpa_ticket_manifest|{RPA_TICKET_INTEGRITY_SCHEMA}|{required}|{key_env}|{payload_hash}"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def _sign(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
