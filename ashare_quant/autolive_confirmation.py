from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import secrets
from typing import Any

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv


CONFIRMATION_COLUMNS = [
    "status",
    "token_sha256",
    "tickets_sha256",
    "auto_live_config_sha256",
    "created_at",
    "expires_at",
    "consumed_at",
    "message",
]


@dataclass(frozen=True)
class ActionConfirmation:
    token: str
    path: Path
    expires_at: str


def action_confirmation_config(config: dict[str, Any]) -> dict[str, Any]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    value = auto.get("action_confirmation", {})
    return value if isinstance(value, dict) else {}


def action_confirmation_required(config: dict[str, Any]) -> bool:
    return bool(action_confirmation_config(config).get("required_before_submit", False))


def issue_action_confirmation(
    config: dict[str, Any],
    *,
    tickets_sha256: str,
    auto_live_config_sha256: str,
    now: datetime | None = None,
) -> ActionConfirmation:
    cfg = action_confirmation_config(config)
    ttl_seconds = int(cfg.get("ttl_seconds", 600))
    if ttl_seconds < 30 or ttl_seconds > 900:
        raise RuntimeError("action confirmation ttl_seconds must be between 30 and 900")
    current = _utc_now(now)
    expires = current + timedelta(seconds=ttl_seconds)
    token = secrets.token_urlsafe(24)
    path = confirmation_path(config)
    frame = pd.DataFrame(
        [
            {
                "status": "issued",
                "token_sha256": _token_sha256(token),
                "tickets_sha256": str(tickets_sha256),
                "auto_live_config_sha256": str(auto_live_config_sha256),
                "created_at": current.isoformat(timespec="seconds"),
                "expires_at": expires.isoformat(timespec="seconds"),
                "consumed_at": "",
                "message": "Single-use action confirmation issued; plaintext token is not persisted.",
            }
        ],
        columns=CONFIRMATION_COLUMNS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_to_csv(frame, path, index=False, encoding="utf-8-sig")
    return ActionConfirmation(token=token, path=path, expires_at=expires.isoformat(timespec="seconds"))


def validate_action_confirmation(
    config: dict[str, Any],
    token: str | None,
    *,
    tickets_sha256: str,
    auto_live_config_sha256: str,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if not action_confirmation_required(config):
        return True, "not_required"
    if not token:
        return False, "action_confirmation_token_missing"
    frame = _read_confirmation(config)
    if frame.empty:
        return False, "action_confirmation_not_issued"
    row = frame.iloc[0]
    if str(row.get("status", "")) != "issued":
        return False, "action_confirmation_already_consumed"
    if not secrets.compare_digest(str(row.get("token_sha256", "")), _token_sha256(token)):
        return False, "action_confirmation_token_mismatch"
    if not secrets.compare_digest(str(row.get("tickets_sha256", "")), str(tickets_sha256)):
        return False, "action_confirmation_tickets_changed"
    if not secrets.compare_digest(str(row.get("auto_live_config_sha256", "")), str(auto_live_config_sha256)):
        return False, "action_confirmation_config_changed"
    try:
        expires = datetime.fromisoformat(str(row.get("expires_at", "")))
    except ValueError:
        return False, "action_confirmation_expiry_invalid"
    if expires.tzinfo is None:
        return False, "action_confirmation_expiry_invalid"
    if _utc_now(now) > expires.astimezone(timezone.utc):
        return False, "action_confirmation_expired"
    return True, "valid"


def consume_action_confirmation(
    config: dict[str, Any],
    token: str | None,
    *,
    tickets_sha256: str,
    auto_live_config_sha256: str,
    now: datetime | None = None,
) -> tuple[bool, str, Path]:
    valid, reason = validate_action_confirmation(
        config,
        token,
        tickets_sha256=tickets_sha256,
        auto_live_config_sha256=auto_live_config_sha256,
        now=now,
    )
    path = confirmation_path(config)
    if not valid or not action_confirmation_required(config):
        return valid, reason, path
    frame = _read_confirmation(config)
    frame.loc[0, "status"] = "consumed"
    frame.loc[0, "consumed_at"] = _utc_now(now).isoformat(timespec="seconds")
    frame.loc[0, "message"] = "Action confirmation consumed before broker adapter invocation."
    atomic_to_csv(frame[CONFIRMATION_COLUMNS], path, index=False, encoding="utf-8-sig")
    return True, "consumed", path


def confirmation_path(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    return output_dir / "auto_live_action_confirmation.csv"


def _read_confirmation(config: dict[str, Any]) -> pd.DataFrame:
    path = confirmation_path(config)
    if not path.exists():
        return pd.DataFrame(columns=CONFIRMATION_COLUMNS)
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame(columns=CONFIRMATION_COLUMNS)
    if len(frame) != 1 or not set(CONFIRMATION_COLUMNS).issubset(frame.columns):
        return pd.DataFrame(columns=CONFIRMATION_COLUMNS)
    return frame[CONFIRMATION_COLUMNS].copy()


def _token_sha256(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("confirmation time must be timezone-aware")
    return current.astimezone(timezone.utc)
