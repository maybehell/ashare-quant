from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3
from typing import Any

import pandas as pd

from .autolive import query_auto_live_orders, read_csv
from .autolive_alerts import send_auto_live_incident_alert
from .config import project_path
from .fills import FILL_IMPORT_COLUMNS, import_fills
from .io_utils import atomic_to_csv

AUTO_LIVE_FILL_SYNC_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_SETTLEMENT_AUDIT_COLUMNS = ["check", "passed", "value", "threshold", "severity", "next_action", "message"]
AUTO_LIVE_FILL_IMPORT_COLUMNS = FILL_IMPORT_COLUMNS
AUTO_LIVE_FILL_PROGRESS_COLUMNS = [
    "fill_key", "source_run_id", "broker_order_id", "ticket_id", "trade_date",
    "cumulative_quantity", "imported_quantity", "delta_quantity",
    "cumulative_amount", "imported_amount", "delta_amount",
    "cumulative_fee", "imported_fee", "delta_fee", "fee_status", "fee_source", "status",
]
FILLED_BROKER_STATUSES = {"filled", "partially_filled"}



def settle_auto_live_fills(config: dict[str, Any], confirm_import: bool = False) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    try:
        paths.update(query_auto_live_orders(config, raise_on_blocked=True))
        query_ok = True
        query_value = "ok"
    except Exception as exc:
        query_ok = False
        query_value = str(exc)
        audit_path = write_auto_live_settlement_audit(config, pd.DataFrame(), confirm_import, False, query_ok, query_value, False, "not_run", False, "not_run")
        alert_paths = send_auto_live_incident_alert(
            config,
            event_type="settlement_query_failed",
            incident_key=_settlement_incident_key(output_dir, "query"),
            severity="critical",
            message=f"Broker order query failed during settlement: {query_value}",
            context={"confirm_import": bool(confirm_import), "audit_path": str(audit_path)},
        )
        return {"auto_live_settlement_audit.csv": audit_path, **alert_paths}

    paths.update(write_auto_live_fill_sync(config))
    sync_audit = read_csv(paths["auto_live_fill_sync_audit.csv"])
    fills = read_csv(paths["auto_live_fill_import.csv"])
    sync_ok = not sync_audit.empty and "passed" in sync_audit.columns and bool(sync_audit["passed"].map(parse_bool_like).all())
    sync_value = "missing" if sync_audit.empty or "passed" not in sync_audit.columns else f"{int(sync_audit['passed'].map(parse_bool_like).sum())}/{len(sync_audit)}"
    import_allowed = bool(config.get("auto_live", {}).get("allow_account_sync", False))
    import_ok = False
    import_value = "not_requested"
    import_error: Exception | None = None

    if confirm_import:
        if not import_allowed:
            import_value = "auto_live.allow_account_sync=false"
        elif not sync_ok:
            import_value = "fill_sync_audit_failed"
        elif fills.empty:
            import_ok = True
            import_value = "no_fills"
        else:
            try:
                imported = import_fills(config, paths["auto_live_fill_import.csv"], confirm=True)
                paths.update(imported)
                import_ok = True
                import_value = f"imported={len(fills)}"
            except Exception as exc:
                import_error = exc
                import_ok = False
                import_value = f"{type(exc).__name__}: {exc}"
    else:
        import_ok = True

    audit_path = write_auto_live_settlement_audit(config, fills, confirm_import, import_allowed, query_ok, query_value, sync_ok, sync_value, import_ok, import_value)
    paths["auto_live_settlement_audit.csv"] = audit_path
    if import_error is not None:
        paths.update(send_auto_live_incident_alert(
            config,
            event_type="fill_import_failed",
            incident_key=_settlement_incident_key(output_dir, "import"),
            severity="critical",
            message=f"Local live-account fill import failed: {import_value}",
            context={"fill_rows": len(fills), "audit_path": str(audit_path)},
        ))
        raise RuntimeError(f"auto-live fill import blocked: {import_value}; audit={audit_path}") from import_error
    return paths


def write_auto_live_settlement_audit(config: dict[str, Any], fills: pd.DataFrame, confirm_import: bool, import_allowed: bool, query_ok: bool, query_value: object, sync_ok: bool, sync_value: object, import_ok: bool, import_value: object) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "auto_live_settlement_audit.csv"
    audit = build_auto_live_settlement_audit(config, fills, confirm_import, import_allowed, query_ok, query_value, sync_ok, sync_value, import_ok, import_value)
    atomic_to_csv(audit, path, index=False, encoding="utf-8-sig")
    return path


def build_auto_live_settlement_audit(config: dict[str, Any], fills: pd.DataFrame, confirm_import: bool, import_allowed: bool, query_ok: bool, query_value: object, sync_ok: bool, sync_value: object, import_ok: bool, import_value: object) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "severity": "ok" if passed else severity, "next_action": "" if passed else next_action, "message": message})

    add("broker_order_query_completed", query_ok, query_value, "auto-live-query passed", "settle_blocker", "先修复委托状态查询。", "结算流程必须先成功查询券商委托状态。")
    add("fill_sync_audit_passed", sync_ok, sync_value, "all checks passed", "settle_blocker", "先修复 auto_live_fill_sync_audit.csv。", "成交候选通过审计后才能导入账户。")
    add("fill_candidate_rows", True, len(fills), ">=0", "info", "", "没有成交时允许结算流程不导入账户。")
    add("account_import_confirmed", bool(confirm_import), bool(confirm_import), True, "manual_gate", "如需自动写入本地账户，显式传入 --confirm-import。", "默认只生成成交候选，不改账户。")
    add("account_sync_config_allowed", (not confirm_import) or import_allowed, import_allowed, True, "settle_blocker", "确认后设置 auto_live.allow_account_sync=true。", "账户写入需要配置和命令行双重确认。")
    import_passed = import_ok if confirm_import else True
    add("fill_import_completed", import_passed, import_value, "imported or no_fills", "settle_blocker", "检查 fill-import 审计和账户状态。", "确认导入时必须成功写入或明确无成交。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_SETTLEMENT_AUDIT_COLUMNS)

def write_auto_live_fill_sync(config: dict[str, Any]) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    statuses = read_csv(output_dir / "auto_live_order_status.csv")
    status_audit = read_csv(output_dir / "auto_live_order_status_audit.csv")
    progress_load_ok = True
    progress_load_value = "ok"
    try:
        imported = load_auto_live_imported_fill_progress(config)
    except RuntimeError as exc:
        imported = {}
        progress_load_ok = False
        progress_load_value = str(exc)
    require_exact_fees = exact_fill_fees_required(config)
    fills, progress = build_auto_live_incremental_fills(statuses, imported, require_exact_fees=require_exact_fees)
    source_integrity_ok, source_integrity_value = order_status_file_integrity(config, output_dir, status_audit)
    audit = build_auto_live_fill_sync_audit(
        config,
        statuses,
        status_audit,
        fills,
        progress,
        source_integrity_passed=source_integrity_ok,
        source_integrity_value=source_integrity_value,
        progress_load_passed=progress_load_ok,
        progress_load_value=progress_load_value,
    )
    fill_path = output_dir / "auto_live_fill_import.csv"
    progress_path = output_dir / "auto_live_fill_progress.csv"
    audit_path = output_dir / "auto_live_fill_sync_audit.csv"
    atomic_to_csv(fills, fill_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(progress, progress_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    paths = {
        "auto_live_fill_import.csv": fill_path,
        "auto_live_fill_progress.csv": progress_path,
        "auto_live_fill_sync_audit.csv": audit_path,
    }
    if not progress_load_ok:
        paths.update(send_auto_live_incident_alert(
            config,
            event_type="fill_progress_database_unreadable",
            incident_key=_settlement_incident_key(output_dir, "progress_database"),
            severity="critical",
            message=f"Existing live fill progress could not be read: {progress_load_value}",
            context={"audit_path": str(audit_path)},
        ))
    return paths


def _settlement_incident_key(output_dir: Path, stage: str) -> str:
    status = read_csv(output_dir / "auto_live_order_status.csv")
    query_ids = status["query_run_id"].astype(str).str.strip().unique().tolist() if not status.empty and "query_run_id" in status else []
    if len(query_ids) == 1 and query_ids[0]:
        source_id = query_ids[0]
    else:
        results = read_csv(output_dir / "auto_live_order_results.csv")
        run_ids = results["run_id"].astype(str).str.strip().unique().tolist() if not results.empty and "run_id" in results else []
        source_id = run_ids[0] if len(run_ids) == 1 and run_ids[0] else "no_source_batch"
    return f"{stage}:{source_id}"


def order_status_file_integrity(
    config: dict[str, Any],
    output_dir: Path,
    status_audit: pd.DataFrame,
) -> tuple[bool, str]:
    adapter = str(config.get("auto_live", {}).get("broker_adapter", "") or "").strip().lower()
    if adapter != "command":
        return True, "not_required_for_noncommand_adapter"
    if status_audit.empty or not {"check", "value"}.issubset(status_audit.columns):
        return False, "order_status_audit_missing"
    hashes = status_audit.loc[status_audit["check"].astype(str) == "order_status_file_sha256", "value"]
    expected = str(hashes.iloc[-1] if not hashes.empty else "").strip()
    status_path = output_dir / "auto_live_order_status.csv"
    try:
        current = hashlib.sha256(status_path.read_bytes()).hexdigest()
    except OSError:
        return False, "order_status_file_missing"
    if not expected or current != expected:
        return False, "order_status_file_sha256_mismatch"
    return True, "order_status_file_sha256_valid"


def build_auto_live_fill_sync(
    config: dict[str, Any],
    statuses: pd.DataFrame,
    status_audit: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    imported = load_auto_live_imported_fill_progress(config)
    require_exact_fees = exact_fill_fees_required(config)
    fills, progress = build_auto_live_incremental_fills(statuses, imported, require_exact_fees=require_exact_fees)
    audit = build_auto_live_fill_sync_audit(config, statuses, status_audit, fills, progress)
    return fills, audit


def build_auto_live_fill_import_frame(
    statuses: pd.DataFrame,
    imported_progress: dict[str, dict[str, float]] | None = None,
    *,
    require_exact_fees: bool = False,
) -> pd.DataFrame:
    fills, _ = build_auto_live_incremental_fills(
        statuses, imported_progress or {}, require_exact_fees=require_exact_fees
    )
    return fills


def build_auto_live_incremental_fills(
    statuses: pd.DataFrame,
    imported_progress: dict[str, dict[str, float]],
    *,
    require_exact_fees: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if statuses.empty:
        return (
            pd.DataFrame(columns=AUTO_LIVE_FILL_IMPORT_COLUMNS),
            pd.DataFrame(columns=AUTO_LIVE_FILL_PROGRESS_COLUMNS),
        )
    frame = statuses.copy()
    columns = [
        "trade_date", "symbol", "side", "filled_quantity", "avg_fill_price",
        "filled_amount", "fee", "fee_status", "fee_source", "broker_status",
        "source_run_id", "broker_order_id", "ticket_id",
    ]
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    broker_status = frame["broker_status"].astype(str).str.lower().str.strip()
    cumulative_quantity = pd.to_numeric(frame["filled_quantity"], errors="coerce").fillna(0)
    filled = frame[broker_status.isin(FILLED_BROKER_STATUSES) & (cumulative_quantity > 0)].copy()
    if filled.empty:
        return (
            pd.DataFrame(columns=AUTO_LIVE_FILL_IMPORT_COLUMNS),
            pd.DataFrame(columns=AUTO_LIVE_FILL_PROGRESS_COLUMNS),
        )

    fill_rows: list[dict[str, object]] = []
    progress_rows: list[dict[str, object]] = []
    tolerance = 1e-6
    for row in filled.itertuples(index=False):
        source_run_id = str(getattr(row, "source_run_id", "") or "").strip()
        broker_order_id = str(getattr(row, "broker_order_id", "") or "").strip()
        ticket_id = str(getattr(row, "ticket_id", "") or "").strip()
        fill_key = f"auto_live:{source_run_id}:{broker_order_id}"
        current_quantity = int(_safe_number(getattr(row, "filled_quantity", 0)))
        avg_price = _safe_number(getattr(row, "avg_fill_price", 0.0))
        current_amount = _safe_number(getattr(row, "filled_amount", 0.0))
        if current_amount <= 0 and current_quantity > 0 and avg_price > 0:
            current_amount = current_quantity * avg_price
        current_fee = _safe_number(getattr(row, "fee", 0.0))
        fee_status = str(getattr(row, "fee_status", "") or "").strip().lower()
        fee_source = str(getattr(row, "fee_source", "") or "").strip()
        previous = imported_progress.get(fill_key, {})
        previous_quantity = int(previous.get("quantity", 0) or 0)
        previous_amount = float(previous.get("amount", 0.0) or 0.0)
        previous_fee = float(previous.get("fee", 0.0) or 0.0)
        delta_quantity = current_quantity - previous_quantity
        delta_amount = current_amount - previous_amount
        delta_fee = current_fee - previous_fee

        if not source_run_id or not broker_order_id:
            status = "missing_fill_identity"
        elif delta_quantity < 0 or delta_amount < -tolerance or delta_fee < -tolerance:
            status = "cumulative_regression"
        elif delta_quantity == 0 and (abs(delta_amount) > tolerance or abs(delta_fee) > tolerance):
            status = "cumulative_values_changed_without_quantity"
        elif delta_quantity == 0:
            status = "already_imported"
        elif require_exact_fees and fee_status != "exact":
            status = "fee_not_exact"
        elif delta_amount <= 0:
            status = "invalid_incremental_amount"
        else:
            status = "new_increment"
            fill_rows.append(
                {
                    "date": "" if pd.isna(pd.to_datetime(getattr(row, "trade_date", ""), errors="coerce")) else pd.to_datetime(getattr(row, "trade_date", "")).date().isoformat(),
                    "symbol": str(getattr(row, "symbol", "")).split(".")[0].zfill(6),
                    "side": str(getattr(row, "side", "")).lower().strip(),
                    "quantity": delta_quantity,
                    "price": delta_amount / delta_quantity,
                    "fee": max(delta_fee, 0.0),
                    "amount": delta_amount,
                    "reason": f"{fill_key}:cum{current_quantity}",
                }
            )
        progress_rows.append(
            {
                "fill_key": fill_key,
                "source_run_id": source_run_id,
                "broker_order_id": broker_order_id,
                "ticket_id": ticket_id,
                "trade_date": getattr(row, "trade_date", ""),
                "cumulative_quantity": current_quantity,
                "imported_quantity": previous_quantity,
                "delta_quantity": delta_quantity,
                "cumulative_amount": round(current_amount, 6),
                "imported_amount": round(previous_amount, 6),
                "delta_amount": round(delta_amount, 6),
                "cumulative_fee": round(current_fee, 6),
                "imported_fee": round(previous_fee, 6),
                "delta_fee": round(delta_fee, 6),
                "fee_status": fee_status or ("unverified" if current_quantity > 0 else "not_applicable"),
                "fee_source": fee_source or ("adapter_unspecified" if current_quantity > 0 else "none"),
                "status": status,
            }
        )
    fills = pd.DataFrame(fill_rows, columns=AUTO_LIVE_FILL_IMPORT_COLUMNS)
    progress = pd.DataFrame(progress_rows, columns=AUTO_LIVE_FILL_PROGRESS_COLUMNS)
    return fills, progress


def _safe_number(value: object) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(number) else float(number)


def load_auto_live_imported_fill_progress(config: dict[str, Any]) -> dict[str, dict[str, float]]:
    paths = config.get("paths", {}) if isinstance(config.get("paths", {}), dict) else {}
    state_value = paths.get("state_db", "")
    if not state_value:
        return {}
    state_db = project_path(state_value)
    if not state_db.is_file():
        return {}
    try:
        with sqlite3.connect(state_db) as conn:
            rows = conn.execute(
                "select reason, quantity, amount, fee from trades where reason like 'auto_live:%'"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Unable to load imported live-fill progress from {state_db}: {exc}") from exc
    progress: dict[str, dict[str, float]] = {}
    for reason, quantity, amount, fee in rows:
        text = str(reason or "").strip()
        if not text.startswith("auto_live:"):
            continue
        fill_key = text.rsplit(":cum", 1)[0] if ":cum" in text else text
        item = progress.setdefault(fill_key, {"quantity": 0.0, "amount": 0.0, "fee": 0.0})
        item["quantity"] += float(quantity or 0)
        item["amount"] += float(amount or 0.0)
        item["fee"] += float(fee or 0.0)
    return progress


def build_auto_live_fill_sync_audit(
    config: dict[str, Any],
    statuses: pd.DataFrame,
    status_audit: pd.DataFrame,
    fills: pd.DataFrame,
    progress: pd.DataFrame | None = None,
    *,
    source_integrity_passed: bool = True,
    source_integrity_value: str = "not_evaluated",
    progress_load_passed: bool = True,
    progress_load_value: str = "not_evaluated",
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "severity": "ok" if passed else severity, "next_action": "" if passed else next_action, "message": message})

    status_audit_passed = not status_audit.empty and "passed" in status_audit.columns and bool(status_audit["passed"].map(parse_bool_like).all())
    status_audit_value = "missing" if status_audit.empty or "passed" not in status_audit.columns else f"{int(status_audit['passed'].map(parse_bool_like).sum())}/{len(status_audit)}"
    add("order_status_present", not statuses.empty, len(statuses), ">0 rows", "sync_blocker", "先运行 auto-live-query 查询券商委托状态。", "成交同步必须基于最新券商委托状态。")
    add("order_status_audit_passed", status_audit_passed, status_audit_value, "all passed", "sync_blocker", "先处理 auto_live_order_status_audit.csv 中的阻断项。", "委托状态审计通过后才可生成成交导入候选。")
    add(
        "fill_source_status_file_integrity",
        source_integrity_passed,
        source_integrity_value,
        "current order status file matches audited SHA-256",
        "sync_blocker",
        "重新运行 auto-live-query，不得使用被修改的委托状态文件生成成交。",
        "真实适配器的成交增量必须来自与查询审计逐字节绑定的券商状态文件。",
    )
    add(
        "fill_progress_database_readable",
        progress_load_passed,
        progress_load_value,
        "existing live-fill progress loaded from SQLite",
        "sync_blocker",
        "修复或恢复实盘账本后重新运行；禁止把读取失败当作零进度。",
        "无法读取既有成交进度时必须失败关闭，否则累计成交可能被重复导入。",
    )

    filled_count = len(fills)

    progress = progress if progress is not None else pd.DataFrame(columns=AUTO_LIVE_FILL_PROGRESS_COLUMNS)
    require_exact_fees = exact_fill_fees_required(config)
    pending_progress = progress[pd.to_numeric(progress.get("delta_quantity", 0), errors="coerce").fillna(0) > 0] if not progress.empty else progress
    exact_fee_progress = pending_progress["fee_status"].astype(str).str.lower().eq("exact") if not pending_progress.empty and "fee_status" in pending_progress else pd.Series(dtype=bool)
    fee_provenance_ok = (not require_exact_fees) or pending_progress.empty or bool(exact_fee_progress.all())
    add(
        "filled_fee_provenance_exact",
        fee_provenance_ok,
        "not_required" if not require_exact_fees else ("ok" if fee_provenance_ok else ";".join(pending_progress.get("fee_status", pd.Series(dtype=str)).astype(str))),
        "exact broker cumulative fee for every new live fill",
        "sync_blocker",
        "从券商成交或交割数据读取精确累计费用；不得用 0 或估算值代替。",
        "真实 command 适配器的成交必须携带 fee_status=exact 和可审计 fee_source，才允许写入本地账户。",
    )
    invalid_progress = progress[~progress["status"].isin({"new_increment", "already_imported"})] if not progress.empty else progress
    add(
        "cumulative_fill_progress_non_regressive",
        invalid_progress.empty,
        ";".join(invalid_progress["status"].astype(str).tolist()) if not invalid_progress.empty else "ok",
        "no cumulative quantity/amount/fee regression or identity gaps",
        "sync_blocker",
        "检查券商查询适配器是否返回累计成交量、累计金额、累计费用和稳定委托号。",
        "累计成交回退或同一成交量对应金额变化时禁止写入账户。",
    )
    add(
        "incremental_fill_rows",
        True,
        f"new={int((progress['status'] == 'new_increment').sum()) if not progress.empty else 0};already_imported={int((progress['status'] == 'already_imported').sum()) if not progress.empty else 0}",
        "informational",
        "info",
        "",
        "只导入相对本地账本新增的成交差额，重复查询保持幂等。",
    )
    add("filled_rows_detected", True, filled_count, ">=0", "info", "", "没有成交时允许输出空成交候选文件。")
    if fills.empty:
        return pd.DataFrame(rows, columns=AUTO_LIVE_FILL_SYNC_AUDIT_COLUMNS)

    dates = pd.to_datetime(fills["date"], errors="coerce")
    quantities = pd.to_numeric(fills["quantity"], errors="coerce").fillna(0)
    prices = pd.to_numeric(fills["price"], errors="coerce").fillna(0)
    fees = pd.to_numeric(fills["fee"], errors="coerce").fillna(-1)
    amounts = pd.to_numeric(fills["amount"], errors="coerce").fillna(0)
    tolerance = float(config.get("reconciliation", {}).get("cash_tolerance", 1.0))
    amount_diff = (amounts - quantities * prices).abs()

    add("filled_trade_dates_present", bool((~dates.isna()).all()), int(dates.isna().sum()), 0, "sync_blocker", "适配器 query_orders 必须返回 trade_date/date。", "成交导入需要真实成交日期。")
    add("filled_symbols_valid", bool(fills["symbol"].astype(str).str.fullmatch(r"\d{6}").all()), fills["symbol"].astype(str).tolist(), "6 digit symbols", "sync_blocker", "检查适配器 symbol 字段。", "成交股票代码必须是 6 位 A 股代码。")
    add("filled_sides_valid", bool(fills["side"].isin({"buy", "sell"}).all()), ",".join(sorted(set(fills["side"].astype(str)))), "buy/sell", "sync_blocker", "检查适配器 side 字段。", "成交方向必须是 buy 或 sell。")
    add("filled_quantities_positive", bool((quantities > 0).all()), f"min={float(quantities.min()):g}", ">0", "sync_blocker", "检查成交数量字段。", "成交数量必须为正。")
    add("filled_prices_positive", bool((prices > 0).all()), f"min={float(prices.min()):g}", ">0", "sync_blocker", "检查成交均价字段。", "成交均价必须为正。")
    add("filled_fees_non_negative", bool((fees >= 0).all()), f"min={float(fees.min()):g}", ">=0", "sync_blocker", "检查费用字段。", "成交费用必须非负。")
    add("filled_amount_matches_quantity_price", bool((amount_diff <= tolerance).all()), round(float(amount_diff.max()), 4), f"<= {tolerance}", "sync_blocker", "检查成交金额、数量和均价是否一致。", "成交金额必须与数量乘均价匹配。")
    duplicates = int(fills["reason"].astype(str).duplicated().sum())
    add("fill_reason_keys_unique", duplicates == 0, duplicates, 0, "sync_blocker", "检查同一券商委托是否被重复映射。", "成交候选 reason 键必须唯一，降低重复导入风险。")
    add("fill_import_columns_ready", set(AUTO_LIVE_FILL_IMPORT_COLUMNS).issubset(fills.columns), ",".join(fills.columns.astype(str)), ",".join(AUTO_LIVE_FILL_IMPORT_COLUMNS), "sync_blocker", "修正成交候选文件列。", "输出必须兼容 fill-import 的导入格式。")
    return pd.DataFrame(rows, columns=AUTO_LIVE_FILL_SYNC_AUDIT_COLUMNS)


def exact_fill_fees_required(config: dict[str, Any]) -> bool:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    configured = auto.get("require_exact_fill_fees")
    if configured is not None:
        return parse_bool_like(configured)
    return str(auto.get("broker_adapter", "")).strip().lower() == "command"


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}
