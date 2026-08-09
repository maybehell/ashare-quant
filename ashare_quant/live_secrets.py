from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DESCRIPTION = "ashare_quant live credentials v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
ENTROPY = b"ashare_quant.live_credentials.v1"


def write_live_secrets(path: str | Path, payload: dict[str, Any]) -> Path:
    import win32crypt

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    protected = win32crypt.CryptProtectData(
        raw,
        DESCRIPTION,
        ENTROPY,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
    )
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(protected)
    temporary.replace(target)
    return target


def read_live_secrets(path: str | Path) -> dict[str, Any]:
    import win32crypt

    target = Path(path)
    if not target.exists():
        raise RuntimeError(f"encrypted credential file not found: {target}")
    try:
        _, raw = win32crypt.CryptUnprotectData(
            target.read_bytes(),
            ENTROPY,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
        )
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("encrypted credential file cannot be decrypted by the current Windows user") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("encrypted credential payload must be a JSON object")
    return payload
