from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any

import pandas as pd

from .autolive_integrity import DEFAULT_INTEGRITY_KEY_ENV, MIN_HMAC_KEY_BYTES
from .config import project_path
from .io_utils import atomic_to_csv


RECOVERY_INDEX_COLUMNS = [
    "recovery_id", "run_id", "status", "state_db_sha256", "tickets_sha256",
    "auto_live_config_sha256", "file_count", "path", "created_at",
]
RECOVERY_AUDIT_COLUMNS = [
    "check", "passed", "value", "threshold", "severity", "next_action", "message",
]
RECOVERY_INDEX_FILE = "auto_live_recovery_points.csv"
RECOVERY_AUDIT_FILE = "auto_live_recovery_audit.csv"
MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1
DIRECTORY_COMMIT_ATTEMPTS = 5
DIRECTORY_COMMIT_DELAY_SECONDS = 0.05


def create_auto_live_recovery_point(
    config: dict[str, Any],
    *,
    run_id: str,
    tickets: pd.DataFrame,
    tickets_sha256: str,
    auto_live_config_sha256: str,
) -> dict[str, Path]:
    settings = recovery_settings(config)
    if not settings["enabled"]:
        if settings["required"]:
            raise RuntimeError("auto-live recovery is required but disabled")
        return write_auto_live_recovery_audit(config)

    root = Path(settings["root"])
    root.mkdir(parents=True, exist_ok=True)
    state_db = project_path(config.get("paths", {}).get("state_db", "data/state.sqlite3"))
    if not state_db.exists() or not state_db.is_file():
        raise RuntimeError(f"auto-live recovery requires an existing state_db: {state_db}")
    key = _key(settings["key_env"])
    if settings["require_hmac"] and len(key) < MIN_HMAC_KEY_BYTES:
        raise RuntimeError(
            f"recovery HMAC key missing or shorter than {MIN_HMAC_KEY_BYTES} bytes "
            f"in {settings['key_env']}"
        )

    recovery_id = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{run_id}"
    final_dir = _safe_child(root, recovery_id)
    staging = _safe_child(root, f".{recovery_id}.tmp")
    if final_dir.exists() or staging.exists():
        raise RuntimeError(f"recovery point already exists: {recovery_id}")
    staging.mkdir()
    try:
        database_copy = staging / "state.sqlite3"
        _backup_sqlite(state_db, database_copy)
        tickets_path = staging / "submission_tickets.csv"
        tickets.to_csv(tickets_path, index=False, encoding="utf-8-sig")

        output_dir = project_path(config["paths"]["output_dir"])
        required_files = [str(value) for value in settings["required_files"]]
        missing = [name for name in required_files if not (output_dir / name).is_file()]
        if missing:
            raise RuntimeError(f"required recovery evidence missing: {';'.join(missing)}")

        copied: list[dict[str, object]] = []
        copied.extend([
            _file_entry(database_copy, str(state_db.resolve())),
            _file_entry(tickets_path, "runtime:submission_tickets"),
        ])
        names = list(dict.fromkeys([*required_files, *settings["critical_files"]]))
        evidence_dir = staging / "evidence"
        for name in names:
            source = output_dir / name
            if not source.is_file():
                continue
            destination = evidence_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(_file_entry(destination, str(source.resolve()), base=staging))

        created_at = datetime.now().isoformat(timespec="seconds")
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "recovery_id": recovery_id,
            "run_id": run_id,
            "created_at": created_at,
            "state_db_source": str(state_db.resolve()),
            "tickets_sha256": tickets_sha256,
            "auto_live_config_sha256": auto_live_config_sha256,
            "files": copied,
            "hmac_required": bool(settings["require_hmac"]),
            "hmac_key_env": str(settings["key_env"]),
        }
        payload_hash = _payload_hash(manifest)
        manifest["payload_sha256"] = payload_hash
        manifest["hmac_sha256"] = _sign(key, payload_hash) if key else ""
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        _replace_directory_with_retry(staging, final_dir)
    except Exception:
        _safe_remove_tree(staging, root)
        raise

    index_path = _append_index(
        config,
        {
            "recovery_id": recovery_id,
            "run_id": run_id,
            "status": "created",
            "state_db_sha256": _sha256(final_dir / "state.sqlite3"),
            "tickets_sha256": tickets_sha256,
            "auto_live_config_sha256": auto_live_config_sha256,
            "file_count": len(copied),
            "path": str(final_dir.resolve()),
            "created_at": created_at,
        },
    )
    _enforce_retention(root, int(settings["retention_points"]))
    paths = write_auto_live_recovery_audit(config, recovery_id=recovery_id)
    paths[RECOVERY_INDEX_FILE] = index_path
    audit = pd.read_csv(paths[RECOVERY_AUDIT_FILE])
    if audit.empty or not audit["passed"].astype(bool).all():
        raise RuntimeError("new auto-live recovery point failed post-write verification")
    return paths


def _replace_directory_with_retry(
    staging: Path,
    final_dir: Path,
    *,
    attempts: int = DIRECTORY_COMMIT_ATTEMPTS,
    delay_seconds: float = DIRECTORY_COMMIT_DELAY_SECONDS,
) -> None:
    attempts = max(int(attempts), 1)
    for attempt in range(attempts):
        if final_dir.exists():
            raise FileExistsError(f"recovery destination already exists: {final_dir}")
        if not staging.exists():
            raise FileNotFoundError(f"recovery staging directory is missing: {staging}")
        try:
            os.replace(staging, final_dir)
            return
        except PermissionError as exc:
            winerror = getattr(exc, "winerror", None)
            transient_denied = winerror == 5 or (
                winerror is None and exc.errno == 13
            )
            if not transient_denied or attempt + 1 >= attempts:
                raise
            time.sleep(max(float(delay_seconds), 0.0) * (attempt + 1))


def write_auto_live_recovery_audit(
    config: dict[str, Any],
    *,
    recovery_id: str | None = None,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / RECOVERY_INDEX_FILE
    audit_path = output_dir / RECOVERY_AUDIT_FILE
    audit = build_auto_live_recovery_audit(config, recovery_id=recovery_id)
    if not index_path.exists():
        atomic_to_csv(pd.DataFrame(columns=RECOVERY_INDEX_COLUMNS), index_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    return {RECOVERY_INDEX_FILE: index_path, RECOVERY_AUDIT_FILE: audit_path}


def build_auto_live_recovery_audit(
    config: dict[str, Any],
    *,
    recovery_id: str | None = None,
) -> pd.DataFrame:
    settings = recovery_settings(config)
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, action: str, message: str) -> None:
        rows.append({
            "check": check, "passed": bool(passed), "value": value, "threshold": threshold,
            "severity": "ok" if passed else "recovery_blocker",
            "next_action": "" if passed else action, "message": message,
        })

    add("recovery_enabled_when_required", bool(settings["enabled"]) or not settings["required"], settings["enabled"], "enabled when required", "启用 auto_live.recovery.enabled。", "正式提交前必须启用恢复点。")
    state_db = project_path(config.get("paths", {}).get("state_db", "data/state.sqlite3"))
    add("recovery_state_db_present", state_db.is_file() or not settings["required"], str(state_db), "existing SQLite state_db when required", "初始化并核对本地状态库。", "恢复点必须包含 SQLite 账户状态。")
    key = _key(settings["key_env"])
    key_ready = (not settings["require_hmac"]) or len(key) >= MIN_HMAC_KEY_BYTES
    add("recovery_hmac_key_ready", key_ready, f"required={settings['require_hmac']};key_bytes={len(key)}", "optional or >=32 bytes", f"在 {settings['key_env']} 配置至少 32 字节密钥。", "正式恢复清单必须防篡改。")
    if not settings["enabled"]:
        return pd.DataFrame(rows, columns=RECOVERY_AUDIT_COLUMNS)

    point = _resolve_point(config, recovery_id)
    add("recovery_point_present", point is not None, str(point) if point else "missing", "latest recovery point present", "运行 auto-live-backup 创建恢复点。", "启用恢复功能后应存在可验证恢复点。")
    if point is None:
        return pd.DataFrame(rows, columns=RECOVERY_AUDIT_COLUMNS)
    payload, error = _read_manifest(point / MANIFEST_NAME)
    add("recovery_manifest_readable", not error, error or "readable", "valid JSON object", "保留损坏目录并创建新的恢复点。", "恢复清单必须可解析。")
    if error:
        return pd.DataFrame(rows, columns=RECOVERY_AUDIT_COLUMNS)

    clean = {k: v for k, v in payload.items() if k not in {"payload_sha256", "hmac_sha256"}}
    actual_payload_hash = _payload_hash(clean)
    recorded_hash = str(payload.get("payload_sha256", ""))
    payload_ok = bool(recorded_hash) and hmac.compare_digest(actual_payload_hash, recorded_hash)
    add("recovery_manifest_hash_valid", payload_ok, actual_payload_hash[:16], recorded_hash[:16] or "missing", "禁止手工修改恢复清单。", "清单载荷哈希必须匹配。")
    signature = str(payload.get("hmac_sha256", ""))
    signature_ok = bool(key) and bool(signature) and payload_ok and hmac.compare_digest(signature, _sign(key, actual_payload_hash))
    hmac_ok = signature_ok if settings["require_hmac"] or signature else True
    add("recovery_manifest_hmac_valid", hmac_ok, f"required={settings['require_hmac']};signed={bool(signature)}", "valid when required/present", "使用原密钥验证，或创建新的恢复点。", "恢复清单 HMAC 必须有效。")

    entries = payload.get("files", [])
    entries = entries if isinstance(entries, list) else []
    file_checks = [_verify_file_entry(point, entry) for entry in entries]
    files_ok = bool(entries) and all(file_checks)
    add("recovery_files_hash_valid", files_ok, f"{sum(file_checks)}/{len(file_checks)}", "all files match manifest", "保留损坏恢复点并创建新恢复点。", "恢复点内每个文件都必须与清单哈希一致。")
    sqlite_ok, sqlite_value = _sqlite_integrity(point / "state.sqlite3")
    add("recovery_sqlite_integrity", sqlite_ok, sqlite_value, "PRAGMA quick_check=ok", "创建新恢复点并调查状态库损坏。", "备份后的 SQLite 必须可独立打开并通过完整性检查。")
    return pd.DataFrame(rows, columns=RECOVERY_AUDIT_COLUMNS)


def auto_live_recovery_config_status(config: dict[str, Any]) -> tuple[bool, str, bool]:
    settings = recovery_settings(config)
    audit = build_auto_live_recovery_audit(config)
    config_checks = audit[audit["check"].isin({
        "recovery_enabled_when_required", "recovery_state_db_present", "recovery_hmac_key_ready",
    })]
    passed = bool(not config_checks.empty and config_checks["passed"].astype(bool).all())
    failed = config_checks.loc[~config_checks["passed"].astype(bool), "check"].astype(str).tolist()
    return passed, f"required={settings['required']};enabled={settings['enabled']};passed={int(config_checks['passed'].sum())}/{len(config_checks)};failed={';'.join(failed)}", bool(settings["required"])


def recovery_settings(config: dict[str, Any]) -> dict[str, object]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    recovery = auto.get("recovery", {}) if isinstance(auto.get("recovery", {}), dict) else {}
    adapter = auto.get("command_adapter", {}) if isinstance(auto.get("command_adapter", {}), dict) else {}
    output_dir = project_path(config["paths"]["output_dir"])
    root_value = recovery.get("backup_dir", "")
    root = project_path(root_value) if root_value else output_dir / "auto_live_backups"
    return {
        "enabled": _bool(recovery.get("enabled", False)),
        "required": _bool(recovery.get("required_before_submit", False)),
        "root": str(root.resolve()),
        "retention_points": max(_integer(recovery.get("retention_points", 30)), 1),
        "require_hmac": _bool(recovery.get("require_hmac", adapter.get("require_hmac_integrity", False))),
        "key_env": str(recovery.get("hmac_key_env", adapter.get("integrity_key_env", DEFAULT_INTEGRITY_KEY_ENV)) or DEFAULT_INTEGRITY_KEY_ENV),
        "critical_files": _string_list(recovery.get("critical_files", [])),
        "required_files": _string_list(recovery.get("required_files", [])),
    }


def _backup_sqlite(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source_conn:
        with closing(sqlite3.connect(destination)) as target_conn:
            source_conn.backup(target_conn)
            target_conn.commit()
    ok, value = _sqlite_integrity(destination)
    if not ok:
        raise RuntimeError(f"SQLite recovery copy failed integrity check: {value}")


def _sqlite_integrity(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "state.sqlite3 missing"
    try:
        with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)) as conn:
            value = str(conn.execute("pragma quick_check").fetchone()[0])
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return value.lower() == "ok", value


def _file_entry(path: Path, source: str, *, base: Path | None = None) -> dict[str, object]:
    return {
        "path": str(path.relative_to(base or path.parent)).replace("\\", "/"),
        "source": source,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _verify_file_entry(point: Path, entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    relative = str(entry.get("path", ""))
    candidate = (point / relative).resolve()
    try:
        candidate.relative_to(point.resolve())
    except ValueError:
        return False
    expected = str(entry.get("sha256", ""))
    return candidate.is_file() and len(expected) == 64 and hmac.compare_digest(_sha256(candidate), expected)


def _append_index(config: dict[str, Any], row: dict[str, object]) -> Path:
    path = project_path(config["paths"]["output_dir"]) / RECOVERY_INDEX_FILE
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=RECOVERY_INDEX_COLUMNS)
    frame = pd.concat([existing, pd.DataFrame([row], columns=RECOVERY_INDEX_COLUMNS)], ignore_index=True)
    atomic_to_csv(frame, path, index=False, encoding="utf-8-sig")
    return path


def _resolve_point(config: dict[str, Any], recovery_id: str | None) -> Path | None:
    settings = recovery_settings(config)
    root = Path(settings["root"])
    if recovery_id:
        candidate = _safe_child(root, recovery_id)
        return candidate if candidate.is_dir() else None
    index = project_path(config["paths"]["output_dir"]) / RECOVERY_INDEX_FILE
    if index.exists():
        try:
            frame = pd.read_csv(index)
            if not frame.empty:
                candidate = Path(str(frame.iloc[-1].get("path", "")))
                if candidate.is_dir():
                    candidate.resolve().relative_to(root.resolve())
                    return candidate
        except Exception:
            pass
    points = sorted((p for p in root.glob("*") if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name) if root.exists() else []
    return points[-1] if points else None


def _enforce_retention(root: Path, keep: int) -> None:
    points = sorted((p for p in root.glob("*") if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name)
    for candidate in points[:-keep]:
        _safe_remove_tree(candidate, root)


def _safe_remove_tree(path: Path, root: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != root.resolve() or not path.name:
        raise RuntimeError(f"refusing to remove recovery path outside backup root: {path}")
    if path.exists():
        shutil.rmtree(path)


def _safe_child(root: Path, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise RuntimeError(f"invalid recovery point name: {name}")
    candidate = (root / name).resolve()
    if candidate.parent != root.resolve():
        raise RuntimeError(f"recovery point escapes backup root: {name}")
    return candidate


def _read_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    return (payload, "") if isinstance(payload, dict) else ({}, "manifest root must be object")


def _payload_hash(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sign(key: bytes, payload_hash: str) -> str:
    return hmac.new(key, f"auto_live_recovery|{payload_hash}".encode("utf-8"), hashlib.sha256).hexdigest()


def _key(key_env: object) -> bytes:
    return os.environ.get(str(key_env), "").encode("utf-8") if str(key_env) else b""


def _string_list(value: object) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _integer(value: object) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return int(parsed) if not pd.isna(parsed) else 0


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
