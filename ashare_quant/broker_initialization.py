from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
from typing import Any

import pandas as pd

from .account import (
    STALE_EXECUTION_ARTIFACTS,
    check_account_import_file,
    clear_stale_execution_artifacts,
    import_account_state,
)
from .audit import frame_sha256, mapping_sha256
from .autolive import (
    auto_live_account_snapshot_binding_status,
    auto_live_account_snapshot_environment_status,
    auto_live_account_snapshot_freshness_status,
    parse_bool_like,
    read_csv,
)
from .autolive_account import build_auto_live_broker_account_audit
from .broker_preview import positions_content_valid, write_broker_account_import_preview
from .config import project_path
from .io_utils import atomic_to_csv


INITIALIZATION_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "message"]
PROMOTED_ACCOUNT_FILES = [
    "account_import_audit.csv",
    "account_import_positions.csv",
    "account_import_snapshot.csv",
    "account_state_audit.csv",
    "account_state_snapshot.csv",
    "account_state_positions.csv",
    "account_state_missing_prices.csv",
]


def check_broker_account_initialization(
    config: dict[str, Any],
    *,
    expected_snapshot_run_id: str,
    expected_account_id: str,
) -> dict[str, Path]:
    """Rebuild and audit the broker-derived account import without writing SQLite state."""
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "broker_account_initialization_audit.csv"
    provenance_path = output_dir / "broker_account_initialization_provenance.csv"
    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    positions = read_csv(output_dir / "auto_live_broker_positions.csv")
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    preview_paths: dict[str, Path] = {}
    preview_error = ""
    try:
        preview_paths = write_broker_account_import_preview(config)
    except Exception as exc:
        preview_error = f"{type(exc).__name__}: {exc}"

    snapshot_present = len(snapshot) == 1
    add("broker_snapshot_present", snapshot_present, len(snapshot), 1, "Exactly one current broker account snapshot is required.")
    row = snapshot.iloc[0] if snapshot_present else pd.Series(dtype=object)
    observed_run_id = str(row.get("snapshot_run_id", "") or "")
    observed_account_id = str(row.get("account_id", "") or "")
    add(
        "snapshot_run_id_confirmed",
        bool(expected_snapshot_run_id) and observed_run_id == expected_snapshot_run_id,
        observed_run_id or "missing",
        expected_snapshot_run_id or "explicit --snapshot-run-id",
        "The operator must confirm the exact snapshot batch being initialized.",
    )
    add(
        "broker_account_id_confirmed",
        bool(expected_account_id) and observed_account_id == expected_account_id,
        observed_account_id or "missing",
        expected_account_id or "explicit --account-id",
        "The operator must confirm the stable masked broker account id.",
    )
    environment_ok, environment_value = auto_live_account_snapshot_environment_status(config, snapshot)
    add("broker_environment_live", environment_ok, environment_value, "live", "Simulation snapshots cannot initialize the live ledger.")
    binding_ok, binding_value = auto_live_account_snapshot_binding_status(config, snapshot)
    add("broker_snapshot_config_match", binding_ok, binding_value, "current auto_live config", "The snapshot must match the current live configuration.")
    freshness_ok, freshness_value, freshness_threshold = auto_live_account_snapshot_freshness_status(config, snapshot)
    add("broker_snapshot_fresh", freshness_ok, freshness_value, freshness_threshold, "Initialization cannot reuse a stale account balance.")
    position_run_ids = set(positions.get("snapshot_run_id", pd.Series(dtype=str)).dropna().astype(str))
    batch_ok = bool(observed_run_id) and (positions.empty or position_run_ids == {observed_run_id})
    add("broker_positions_snapshot_match", batch_ok, ";".join(sorted(position_run_ids)) or "empty", observed_run_id or "present", "Position rows must belong to the confirmed snapshot batch.")
    add("broker_positions_valid", positions_content_valid(positions), len(positions), "valid integer broker positions", "Invalid or fractional broker quantities cannot initialize the ledger.")

    preview_audit = read_csv(output_dir / "broker_account_import_preview_audit.csv")
    preview_ready = specific_check_passed(preview_audit, "broker_account_preview_ready")
    add("broker_account_preview_ready", preview_ready, preview_error or passed_count(preview_audit), "all preview checks passed", "The total-cash, positions, valuation, and no-write preview must pass.")

    state_db = project_path(config["paths"]["state_db"])
    simulation_db = project_path("data/state.sqlite3")
    isolated = state_db.resolve() != simulation_db.resolve()
    add("live_state_db_isolated", isolated, str(state_db.resolve()), f"not {simulation_db.resolve()}", "The live ledger cannot share the paper-account database.")
    state_absent = not state_db.exists()
    add("live_state_db_absent", state_absent, "absent" if state_absent else str(state_db.resolve()), "absent before one-time initialization", "Existing live state is never overwritten by this initialization command.")

    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live"), dict) else {}
    emergency_stop = parse_bool_like(auto.get("emergency_stop", True))
    submit_disabled = not parse_bool_like(auto.get("allow_broker_submit", False))
    add("emergency_stop_active", emergency_stop, emergency_stop, True, "Ledger initialization is allowed only while the emergency stop is active.")
    add("broker_submit_disabled", submit_disabled, not submit_disabled, False, "Ledger initialization must not run while broker submission is enabled.")

    ready = all(bool(item["passed"]) for item in rows)
    add("broker_account_initialization_ready", ready, "ready_for_explicit_commit" if ready else "blocked", "all initialization checks passed", "Readiness permits only the explicit one-time local ledger commit.")
    atomic_to_csv(pd.DataFrame(rows, columns=INITIALIZATION_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")

    preview = read_csv(output_dir / "broker_account_import_preview.csv")
    provenance = pd.DataFrame(
        [
            {"item": "status", "value": "ready_for_explicit_commit" if ready else "blocked"},
            {"item": "generated_at", "value": datetime.now().astimezone().isoformat(timespec="seconds")},
            {"item": "snapshot_run_id", "value": observed_run_id},
            {"item": "account_id", "value": observed_account_id},
            {"item": "expected_snapshot_run_id", "value": expected_snapshot_run_id},
            {"item": "expected_account_id", "value": expected_account_id},
            {"item": "live_config_sha256", "value": mapping_sha256(config)},
            {"item": "snapshot_sha256", "value": frame_sha256(snapshot)},
            {"item": "positions_sha256", "value": frame_sha256(positions)},
            {"item": "import_preview_sha256", "value": frame_sha256(preview)},
            {"item": "state_db", "value": str(state_db.resolve())},
            {"item": "committed", "value": False},
            {"item": "account_binding_created", "value": False},
        ]
    )
    atomic_to_csv(provenance, provenance_path, index=False, encoding="utf-8-sig")
    return {
        **preview_paths,
        "broker_account_initialization_audit.csv": audit_path,
        "broker_account_initialization_provenance.csv": provenance_path,
    }


def initialize_broker_account_from_snapshot(
    config: dict[str, Any],
    *,
    expected_snapshot_run_id: str,
    expected_account_id: str,
    confirm: bool = False,
) -> dict[str, Path]:
    """Atomically initialize an absent live ledger from a verified broker snapshot."""
    paths = check_broker_account_initialization(
        config,
        expected_snapshot_run_id=expected_snapshot_run_id,
        expected_account_id=expected_account_id,
    )
    if not confirm:
        return paths

    output_dir = project_path(config["paths"]["output_dir"])
    audit_path = output_dir / "broker_account_initialization_audit.csv"
    provenance_path = output_dir / "broker_account_initialization_provenance.csv"
    audit = read_csv(audit_path)
    if not specific_check_passed(audit, "broker_account_initialization_ready"):
        raise RuntimeError("broker account initialization blocked; review broker_account_initialization_audit.csv")

    final_db = project_path(config["paths"]["state_db"])
    if final_db.exists():
        raise RuntimeError("live state database already exists; one-time broker initialization refuses to overwrite it")
    run_token = safe_token(expected_snapshot_run_id)
    attempt_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    staging_output = output_dir / "broker_account_initialization" / run_token / attempt_id
    staging_db = final_db.parent / f".{final_db.name}.{attempt_id}.staging"
    staging_config = deepcopy(config)
    staging_config["paths"]["output_dir"] = str(staging_output.resolve())
    staging_config["paths"]["state_db"] = str(staging_db.resolve())
    source = output_dir / "broker_account_import_preview.csv"
    snapshot = read_csv(output_dir / "auto_live_broker_account_snapshot.csv")
    positions = read_csv(output_dir / "auto_live_broker_positions.csv")
    promotion_temps: list[tuple[Path, Path]] = []
    committed = False
    commit_rows = audit.to_dict(orient="records")

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        commit_rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    try:
        staging_output.mkdir(parents=True, exist_ok=True)
        check_paths = check_account_import_file(staging_config, source)
        check_audit = read_csv(check_paths["account_import_check_audit.csv"])
        check_ok = all_passed(check_audit)
        add("staging_import_precheck_passed", check_ok, passed_count(check_audit), "all staging import checks passed", "The exact preview is rechecked inside the isolated staging workspace.")
        if not check_ok:
            raise RuntimeError("staging account import precheck failed")

        import_paths = import_account_state(staging_config, source, confirm=True)
        import_audit = read_csv(import_paths["account_import_audit.csv"])
        import_ok = all_passed(import_audit)
        add("staging_account_import_passed", import_ok, passed_count(import_audit), "all account import checks passed", "The temporary SQLite ledger must import cleanly before promotion.")
        if not import_ok or not staging_db.exists():
            raise RuntimeError("staging account import did not create a valid SQLite ledger")

        post_audit = build_auto_live_broker_account_audit(
            staging_config,
            snapshot,
            positions,
            expected_snapshot_run_id,
            True,
            "verified broker snapshot used for atomic initialization",
        )
        post_ok = all_passed(post_audit)
        failed_post = ";".join(post_audit.loc[~post_audit["passed"].map(parse_bool_like), "check"].astype(str).tolist()) if not post_audit.empty else "missing"
        add("post_import_broker_reconciliation_passed", post_ok, passed_count(post_audit) if post_ok else failed_post, "all broker account checks passed", "The staged ledger must reconcile back to the same broker snapshot.")
        if not post_ok:
            raise RuntimeError(f"staged broker reconciliation failed: {failed_post}")

        for name in PROMOTED_ACCOUNT_FILES:
            source_path = staging_output / name
            if not source_path.exists():
                raise RuntimeError(f"staging account artifact is missing: {name}")
            destination = output_dir / name
            temporary = output_dir / f".{name}.{attempt_id}.tmp"
            shutil.copyfile(source_path, temporary)
            promotion_temps.append((temporary, destination))

        final_db.parent.mkdir(parents=True, exist_ok=True)
        if final_db.exists():
            raise RuntimeError("live state database appeared during staging; refusing concurrent initialization")
        cleared = clear_stale_execution_artifacts(output_dir)
        os.replace(staging_db, final_db)
        committed = True
        for temporary, destination in promotion_temps:
            os.replace(temporary, destination)
        atomic_to_csv(post_audit, output_dir / "auto_live_broker_account_audit.csv", index=False, encoding="utf-8-sig")
        add("live_state_db_atomically_committed", True, str(final_db.resolve()), "verified staging database promoted", "The final live ledger appeared only after all staging checks passed.")
        add("stale_execution_artifacts_cleared", True, cleared, f"all {len(STALE_EXECUTION_ARTIFACTS)} known names checked", "Initialization invalidates any old paper or RPA execution artifacts.")
        add("account_binding_deferred", True, False, "explicit auto-live-bind-account --confirm required", "Initialization never creates the signed broker-account binding automatically.")
        add("broker_account_initialization_committed", True, expected_snapshot_run_id, "explicitly confirmed snapshot", "The one-time local live ledger initialization completed.")
        atomic_to_csv(pd.DataFrame(commit_rows, columns=INITIALIZATION_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")

        provenance = read_csv(provenance_path)
        values = {str(row.item): row.value for row in provenance.itertuples(index=False)} if not provenance.empty else {}
        values.update(
            {
                "status": "committed_requires_explicit_account_binding",
                "committed": True,
                "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "staging_output": str(staging_output.resolve()),
                "state_db": str(final_db.resolve()),
                "account_binding_created": False,
            }
        )
        atomic_to_csv(pd.DataFrame([{"item": key, "value": value} for key, value in values.items()]), provenance_path, index=False, encoding="utf-8-sig")
        return {
            **paths,
            **{name: output_dir / name for name in PROMOTED_ACCOUNT_FILES},
            "auto_live_broker_account_audit.csv": output_dir / "auto_live_broker_account_audit.csv",
            "broker_account_initialization_audit.csv": audit_path,
            "broker_account_initialization_provenance.csv": provenance_path,
        }
    except Exception as exc:
        add("broker_account_initialization_committed", False, f"{type(exc).__name__}: {exc}", "no final state on failure", "Initialization failed closed; review the preceding staging check.")
        atomic_to_csv(pd.DataFrame(commit_rows, columns=INITIALIZATION_AUDIT_COLUMNS), audit_path, index=False, encoding="utf-8-sig")
        raise
    finally:
        for temporary, _ in promotion_temps:
            if temporary.exists():
                temporary.unlink()
        if not committed and staging_db.exists():
            staging_db.unlink()


def all_passed(frame: pd.DataFrame) -> bool:
    return not frame.empty and "passed" in frame.columns and bool(frame["passed"].map(parse_bool_like).all())


def specific_check_passed(frame: pd.DataFrame, check: str) -> bool:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False
    selected = frame[frame["check"].astype(str) == check]
    return not selected.empty and parse_bool_like(selected.iloc[-1].get("passed", False))


def passed_count(frame: pd.DataFrame) -> str:
    if frame.empty or "passed" not in frame.columns:
        return "missing"
    passed = frame["passed"].map(parse_bool_like)
    return f"{int(passed.sum())}/{len(frame)}"


def safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return token[:80] or "snapshot"
