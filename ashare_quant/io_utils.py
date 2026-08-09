from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

import pandas as pd


def atomic_to_csv(frame: pd.DataFrame, path: str | Path, **kwargs) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(tmp, **kwargs)
        replace_with_retry(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def replace_with_retry(tmp: Path, target: Path, attempts: int = 8, delay_seconds: float = 0.05) -> None:
    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            tmp.replace(target)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(delay_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error
