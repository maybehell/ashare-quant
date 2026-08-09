from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any

import pandas as pd

from .autolive import (
    AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS,
    AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS,
    AUTO_LIVE_BROKER_POSITION_COLUMNS,
    COMMAND_ADAPTER_SCHEMA,
    all_passed,
    auto_live_account_snapshot_binding_status,
    auto_live_account_snapshot_freshness_status,
    auto_live_config_sha256,
    auto_live_account_adapter_ready,
    build_command_adapter_invocation,
    command_result_integrity_status,
    parse_bool_like,
    read_csv,
    truncate_text,
)
from .autolive_integrity import attach_request_integrity
from .autolive_journal import record_auto_live_event
from .autolive_binding import write_auto_live_account_binding_audit
from .config import project_path
from .io_utils import atomic_to_csv


def query_auto_live_account(config: dict[str, Any], raise_on_blocked: bool = True) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_run_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    adapter = str(config.get("auto_live", {}).get("broker_adapter", "") or "").strip().lower()
    adapter_ready, adapter_value = auto_live_account_adapter_ready(config)
    snapshot_path = output_dir / "auto_live_broker_account_snapshot.csv"
    positions_path = output_dir / "auto_live_broker_positions.csv"
    audit_path = output_dir / "auto_live_broker_account_audit.csv"

    if not adapter_ready:
        snapshot = pd.DataFrame(columns=AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS)
        positions = pd.DataFrame(columns=AUTO_LIVE_BROKER_POSITION_COLUMNS)
    elif adapter == "mock":
        snapshot, positions = account_snapshot_with_mock_adapter(config, snapshot_run_id)
    elif adapter == "command":
        snapshot, positions = account_snapshot_with_command_adapter(config, snapshot_run_id)
    else:
        snapshot = pd.DataFrame(columns=AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS)
        positions = pd.DataFrame(columns=AUTO_LIVE_BROKER_POSITION_COLUMNS)

    audit = build_auto_live_broker_account_audit(config, snapshot, positions, snapshot_run_id, adapter_ready, adapter_value)
    atomic_to_csv(snapshot, snapshot_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(positions, positions_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    journal_paths = record_auto_live_event(
        config,
        "account_snapshot",
        snapshot_run_id,
        {
            "adapter": adapter,
            "snapshot_rows": len(snapshot),
            "position_rows": len(positions),
            "audit_passed": all_passed(audit),
            "environment": str(snapshot.iloc[-1].get("environment", "")) if not snapshot.empty else "",
            "account_id": str(snapshot.iloc[-1].get("account_id", "")) if not snapshot.empty else "",
        },
        strict=False,
    )
    binding_paths = write_auto_live_account_binding_audit(config)
    if raise_on_blocked and not all_passed(audit):
        failed = ";".join(audit.loc[~audit["passed"].map(parse_bool_like), "check"].astype(str).tolist())
        raise RuntimeError(f"auto-live account blocked: {failed or 'broker_account_audit_failed'}")
    return {
        "auto_live_broker_account_snapshot.csv": snapshot_path,
        "auto_live_broker_positions.csv": positions_path,
        "auto_live_broker_account_audit.csv": audit_path,
        **binding_paths,
        **journal_paths,
    }


def account_snapshot_with_mock_adapter(config: dict[str, Any], snapshot_run_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    output_dir = project_path(config["paths"]["output_dir"])
    local_snapshot = read_csv(output_dir / "account_state_snapshot.csv")
    local_positions = read_csv(output_dir / "account_state_positions.csv")
    if local_snapshot.empty:
        snapshot = pd.DataFrame([account_snapshot_row(snapshot_run_id, "mock", "mock", "simulation", 0.0, 0.0, 0.0, 0.0, 0.0, 0, "Mock account snapshot requires local account_state first.", auto_live_config_sha256(config))], columns=AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS)
        positions = pd.DataFrame(columns=AUTO_LIVE_BROKER_POSITION_COLUMNS)
        return snapshot, positions
    row = local_snapshot.iloc[0]
    cash = to_float(row.get("cash", 0.0))
    market_value = to_float(row.get("market_value", 0.0))
    total_asset = to_float(row.get("marked_equity", row.get("recorded_equity", cash + market_value)))
    positions = normalize_broker_positions(local_positions.to_dict(orient="records"), snapshot_run_id) if not local_positions.empty else pd.DataFrame(columns=AUTO_LIVE_BROKER_POSITION_COLUMNS)
    snapshot = pd.DataFrame([
        account_snapshot_row(snapshot_run_id, "mock", "mock", "simulation", cash, cash, market_value, total_asset, 0.0, len(positions), "Mock adapter mirrored local account_state.", auto_live_config_sha256(config))
    ], columns=AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS)
    return snapshot, positions


def account_snapshot_with_command_adapter(config: dict[str, Any], snapshot_run_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    output_dir = project_path(config["paths"]["output_dir"])
    request_path = output_dir / f"auto_live_account_request_{snapshot_run_id}.json"
    result_path = output_dir / f"auto_live_account_result_{snapshot_run_id}.json"
    write_command_adapter_account_request(config, snapshot_run_id, request_path, result_path)
    argv = build_command_adapter_invocation(config, request_path, result_path, snapshot_run_id)
    adapter_cfg = config.get("auto_live", {}).get("command_adapter", {})
    timeout = float(adapter_cfg.get("timeout_seconds", 30))
    cwd_value = adapter_cfg.get("working_dir", "")
    cwd = project_path(cwd_value) if cwd_value else None
    try:
        completed = subprocess.run(argv, cwd=str(cwd) if cwd else None, timeout=timeout, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        return account_error_frames(snapshot_run_id, "command", f"Command adapter account_snapshot timed out after {timeout:g}s.")
    except OSError as exc:
        return account_error_frames(snapshot_run_id, "command", f"Command adapter account_snapshot failed to start: {exc}")
    if completed.returncode != 0:
        message = truncate_text((completed.stderr or completed.stdout or "").strip(), 300)
        return account_error_frames(snapshot_run_id, "command", f"Command adapter account_snapshot exited {completed.returncode}: {message}")
    if not result_path.exists():
        message = truncate_text((completed.stdout or "").strip(), 300)
        return account_error_frames(snapshot_run_id, "command", f"Command adapter account_snapshot did not write result file. {message}")
    integrity_passed, integrity_value = command_result_integrity_status(config, request_path, result_path)
    if not integrity_passed:
        return account_error_frames(snapshot_run_id, "command", f"Command adapter account result integrity failed: {integrity_value}")
    return normalize_command_adapter_account_snapshot(snapshot_run_id, result_path)


def write_command_adapter_account_request(config: dict[str, Any], snapshot_run_id: str, request_path: Path, result_path: Path) -> None:
    payload = {
        "schema_version": COMMAND_ADAPTER_SCHEMA,
        "mode": "account_snapshot",
        "run_id": snapshot_run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result_path": str(result_path),
        "auto_live_config_sha256": auto_live_config_sha256(config),
        "orders": [],
    }
    payload = attach_request_integrity(config, payload)
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_command_adapter_account_snapshot(snapshot_run_id: str, result_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return account_error_frames(snapshot_run_id, "command", f"Command adapter account_snapshot result is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        return account_error_frames(snapshot_run_id, "command", "Command adapter account_snapshot result must be a JSON object.")
    account = payload.get("account", payload.get("snapshot", payload))
    if not isinstance(account, dict):
        return account_error_frames(snapshot_run_id, "command", "Command adapter account_snapshot account payload must be an object.")
    broker = str(payload.get("broker", account.get("broker", "command")) or "command")
    account_id = str(account.get("account_id", payload.get("account_id", "")) or "")
    environment = str(payload.get("environment", account.get("environment", "unknown")) or "unknown").strip().lower()
    cash = auditable_float(first_present(account, "cash", "balance"))
    available_cash = auditable_float(first_present(account, "available_cash", "available"))
    market_value = auditable_float(first_present(account, "market_value", "positions_value"))
    total_asset = auditable_float(first_present(account, "total_asset", "equity"))
    frozen_cash = auditable_float(first_present(account, "frozen_cash", "frozen"))
    unclassified_raw = first_present(account, "unclassified_asset_value", "other_asset_value")
    unclassified_asset_value = auditable_float(unclassified_raw)
    if unclassified_raw is None and all(pd.notna(value) for value in (cash, market_value, total_asset)):
        unclassified_asset_value = total_asset - cash - market_value
    positions_raw = payload.get("positions", account.get("positions", []))
    if not isinstance(positions_raw, list):
        return account_error_frames(snapshot_run_id, broker, "Command adapter account_snapshot positions must be a list.")
    if any(not isinstance(item, dict) for item in positions_raw):
        return account_error_frames(snapshot_run_id, broker, "Command adapter account_snapshot positions must contain only objects.")
    positions = normalize_broker_positions(positions_raw, snapshot_run_id)
    broker_time = str(payload.get("broker_time", account.get("broker_time", "")) or "").strip()
    generated_at = str(payload.get("generated_at", account.get("generated_at", datetime.now().isoformat(timespec="seconds"))) or "")
    config_fingerprint = str(payload.get("auto_live_config_sha256", account.get("auto_live_config_sha256", "")) or "").strip()
    message = "" if payload.get("message") is None else str(payload.get("message"))
    snapshot = pd.DataFrame([
        {
            "snapshot_run_id": snapshot_run_id,
            "broker": broker,
            "account_id": account_id,
            "environment": environment,
            "cash": cash,
            "available_cash": available_cash,
            "market_value": market_value,
            "total_asset": total_asset,
            "frozen_cash": frozen_cash,
            "unclassified_asset_value": unclassified_asset_value,
            "positions_count": len(positions),
            "broker_time": broker_time,
            "generated_at": generated_at,
            "auto_live_config_sha256": config_fingerprint,
            "message": message,
        }
    ], columns=AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS)
    return snapshot, positions


def account_error_frames(snapshot_run_id: str, broker: str, message: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    snapshot = pd.DataFrame([account_snapshot_row(snapshot_run_id, broker, "", "unknown", -1.0, -1.0, -1.0, -1.0, -1.0, 0, message)], columns=AUTO_LIVE_BROKER_ACCOUNT_SNAPSHOT_COLUMNS)
    positions = pd.DataFrame(columns=AUTO_LIVE_BROKER_POSITION_COLUMNS)
    return snapshot, positions


def account_snapshot_row(snapshot_run_id: str, broker: str, account_id: str, environment: str, cash: float, available_cash: float, market_value: float, total_asset: float, frozen_cash: float, positions_count: int, message: str, config_fingerprint: str = "") -> dict[str, object]:
    return {
        "snapshot_run_id": snapshot_run_id,
        "broker": broker,
        "account_id": account_id,
        "environment": environment,
        "cash": cash,
        "available_cash": available_cash,
        "market_value": market_value,
        "total_asset": total_asset,
        "frozen_cash": frozen_cash,
        "unclassified_asset_value": 0.0,
        "positions_count": positions_count,
        "broker_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "auto_live_config_sha256": config_fingerprint,
        "message": message,
    }


def normalize_broker_positions(raw_positions: list[object], snapshot_run_id: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", item.get("code", "")) or "").strip().zfill(6)
        rows.append(
            {
                "snapshot_run_id": snapshot_run_id,
                "symbol": symbol,
                "quantity": auditable_float(first_present(item, "quantity", "qty")),
                "sellable_quantity": auditable_float(first_present(item, "sellable_quantity", "available_quantity")),
                "cost": auditable_float(first_present(item, "cost", "cost_price")),
                "market_value": auditable_float(first_present(item, "market_value")),
                "last_price": auditable_float(first_present(item, "last_price", "close")),
                "message": "" if item.get("message") is None else str(item.get("message")),
            }
        )
    return pd.DataFrame(rows, columns=AUTO_LIVE_BROKER_POSITION_COLUMNS)


def live_ledger_initialization_status(config: dict[str, Any]) -> tuple[bool, str]:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    configured = str(paths.get("state_db", "") or "").strip()
    if not configured:
        return True, "state_db_not_configured"
    path = project_path(configured)
    if not path.is_file():
        return False, f"live_ledger_not_initialized;path={path}"
    conn: sqlite3.Connection | None = None
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        table = conn.execute(
            "select count(*) from sqlite_master where type='table' and name='account_snapshots'"
        ).fetchone()
        if not table or int(table[0]) != 1:
            return False, f"live_ledger_not_initialized;account_snapshots_table=missing;path={path}"
        row = conn.execute("select count(*) from account_snapshots").fetchone()
    except (OSError, sqlite3.Error) as exc:
        return False, f"live_ledger_unreadable;path={path};error={truncate_text(str(exc), 160)}"
    finally:
        if conn is not None:
            conn.close()
    count = int(row[0]) if row else 0
    return (
        count > 0,
        f"path={path};account_snapshots={count}"
        if count > 0
        else f"live_ledger_not_initialized;account_snapshots=0;path={path}",
    )


def build_auto_live_broker_account_audit(config: dict[str, Any], snapshot: pd.DataFrame, positions: pd.DataFrame, snapshot_run_id: str, adapter_ready: bool, adapter_value: str) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    local_snapshot = read_csv(output_dir / "account_state_snapshot.csv")
    local_positions = read_csv(output_dir / "account_state_positions.csv")
    ledger_initialized, ledger_value = live_ledger_initialization_status(config)
    state_db_configured = bool(str(config.get("paths", {}).get("state_db", "") or "").strip())
    local_state_available = ledger_initialized and not local_snapshot.empty
    local_state_value = (
        "present"
        if local_state_available
        else "live_ledger_not_initialized"
        if state_db_configured and not ledger_initialized
        else "missing"
    )
    cash_tolerance = account_cash_tolerance(config)
    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, next_action: str, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "severity": "ok" if passed else severity, "next_action": "" if passed else next_action, "message": message})

    add("account_adapter_ready", adapter_ready, adapter_value, "adapter account_snapshot ready", "account_blocker", "先通过 command self-test 并确认 account_snapshot 能力。", "券商账户快照必须由已审计适配器读取。")
    add("local_live_ledger_initialized", ledger_initialized, ledger_value, "configured SQLite contains at least one account_snapshots row", "account_blocker", "经操作员确认后运行 broker-account-init 初始化独立实盘账本。", "只读券商快照有效并不等于本地实盘账本已经初始化；未初始化时禁止数值对账和下单。")
    add("broker_account_snapshot_present", len(snapshot) == 1, len(snapshot), "exactly one snapshot row", "account_blocker", "检查适配器 account_snapshot 返回。", "每次账户快照必须返回唯一券商账户汇总。")
    run_ids = snapshot["snapshot_run_id"].astype(str).tolist() if "snapshot_run_id" in snapshot.columns else []
    add("snapshot_run_id_match", bool(run_ids) and all(value == str(snapshot_run_id) for value in run_ids), ",".join(sorted(set(run_ids))) if run_ids else "missing", snapshot_run_id, "account_blocker", "确认账户快照来自本次 snapshot_run_id。", "账户快照必须绑定本次运行号。")

    environments = snapshot["environment"].astype(str).str.strip().str.lower().tolist() if "environment" in snapshot.columns else []
    environment_value = ",".join(sorted(set(environments))) if environments else "missing"
    environment_present = len(snapshot) == 1 and len(environments) == 1 and environments[0] not in {"", "unknown", "missing"}
    add("broker_account_environment_present", environment_present, environment_value, "explicit live or simulation environment", "account_blocker", "让券商桥接器在 account_snapshot 顶层返回 environment。", "账户快照必须明确声明来自真实柜台还是模拟环境。")
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    allow_simulation = parse_bool_like(auto.get("allow_simulated_account_snapshot", False))
    is_live = len(environments) == 1 and environments[0] == "live"
    is_allowed_simulation = allow_simulation and len(environments) == 1 and environments[0] in {"simulation", "paper", "test"}
    add("broker_account_environment_live", is_live or is_allowed_simulation, f"environment={environment_value};simulation_override={allow_simulation}", "live", "account_blocker", "真实接入必须返回 environment=live；模拟演练需显式设置 auto_live.allow_simulated_account_snapshot=true。", "数值一致的模拟账户不能充当真实券商账户快照。")
    account_ids = snapshot["account_id"].astype(str).str.strip().tolist() if "account_id" in snapshot.columns else []
    account_id_present = len(snapshot) == 1 and len(account_ids) == 1 and bool(account_ids[0])
    add("broker_account_id_present", account_id_present, account_ids[0] if account_ids else "missing", "non-empty masked broker account id", "account_blocker", "让券商桥接器返回脱敏但稳定的 account_id。", "真实账户对账必须绑定明确账户，不能只比较资金数值。")
    binding_passed, binding_value = auto_live_account_snapshot_binding_status(config, snapshot)
    add("broker_account_config_match", binding_passed, binding_value, "snapshot fingerprint equals current auto_live config", "account_blocker", "重新运行适配器 self-test 和 auto-live-account。", "账户快照必须绑定当前自动实盘配置指纹。")
    freshness_passed, freshness_value, freshness_threshold = auto_live_account_snapshot_freshness_status(config, snapshot)
    add("broker_account_snapshot_fresh", freshness_passed, freshness_value, freshness_threshold, "account_blocker", "提交前重新运行 auto-live-account。", "账户快照超过有效期后必须重新读取券商端状态。")
    clock_passed, clock_value, clock_threshold = broker_clock_status(config, snapshot)
    add("broker_clock_synchronized", clock_passed, clock_value, clock_threshold, "account_blocker", "同步本机时钟，并让真实桥接器返回带时区的券商服务器时间。", "交易窗口和委托时序依赖本机与券商服务器时间保持在配置误差内。")

    cash = numeric_series(snapshot, "cash")
    available = numeric_series(snapshot, "available_cash")
    market_value = numeric_series(snapshot, "market_value")
    total_asset = numeric_series(snapshot, "total_asset")
    frozen = numeric_series(snapshot, "frozen_cash")
    unclassified = numeric_series(snapshot, "unclassified_asset_value")
    if "unclassified_asset_value" not in snapshot.columns and len(snapshot) == 1:
        unclassified = total_asset - cash - market_value
    numeric_non_negative = len(snapshot) == 1 and bool(((cash >= 0) & (available >= 0) & (market_value >= 0) & (total_asset >= 0) & (frozen >= 0) & (unclassified >= 0)).all())
    add("broker_account_money_non_negative", numeric_non_negative, money_range_text(cash, available, market_value, total_asset, frozen, unclassified), ">=0", "account_blocker", "修正适配器账户资金字段。", "券商账户现金、市值、总资产、冻结资金和未分类资产必须为非负。")
    cash_consistent = len(snapshot) == 1 and bool(
        (
            (available <= cash + cash_tolerance)
            & (frozen <= cash + cash_tolerance)
            & (available + frozen <= cash + cash_tolerance)
        ).all()
    )
    add("broker_cash_fields_consistent", cash_consistent, f"cash={first_number(cash):.2f};available={first_number(available):.2f};frozen={first_number(frozen):.2f}", f"available + frozen <= cash + {cash_tolerance:g}", "account_blocker", "核对券商账户字段映射和资金单位。", "可用资金与冻结资金均属于现金余额，两者合计不应超过现金余额。")
    unclassified_value = first_number(unclassified)
    add("broker_unclassified_asset_zero", len(snapshot) == 1 and unclassified_value <= cash_tolerance, f"unclassified={unclassified_value:.2f}", f"<= {cash_tolerance:g}", "account_blocker", "在券商客户端确认该余额的资产类别；现金、理财或基金需分别映射后再初始化。", "未分类资产不得计入可交易现金；存在未分类余额时禁止初始化账本和下单。")
    asset_gap = abs(first_number(total_asset) - (first_number(cash) + first_number(market_value) + unclassified_value))
    asset_tolerance = max(cash_tolerance, abs(first_number(total_asset)) * 0.005)
    add("broker_total_asset_consistent", len(snapshot) == 1 and asset_gap <= asset_tolerance, f"gap={asset_gap:.2f}", f"<= {asset_tolerance:.2f}", "account_blocker", "核对 total_asset/cash/market_value/unclassified_asset_value 字段定义。", "券商账户总资产应与现金、持仓市值和未分类资产之和基本一致。")

    position_symbols = positions["symbol"].astype(str) if "symbol" in positions.columns else pd.Series(dtype=str)
    valid_symbols = position_symbols.empty or bool(position_symbols.str.fullmatch(r"\d{6}").all())
    add("broker_position_symbols_valid", valid_symbols, int(position_symbols.str.fullmatch(r"\d{6}").sum()) if not position_symbols.empty else 0, len(position_symbols), "account_blocker", "修正适配器持仓 symbol 映射。", "券商持仓代码必须归一化为 6 位 A 股代码。")
    unique_symbols = position_symbols.empty or not bool(position_symbols.duplicated().any())
    add("broker_position_symbols_unique", unique_symbols, f"rows={len(position_symbols)};unique={position_symbols.nunique()}", "one row per symbol", "account_blocker", "检查持仓表是否重复抓取或重复解析。", "同一账户快照中的证券代码必须唯一，防止持仓和市值重复计算。")
    qty = numeric_series(positions, "quantity")
    sellable = numeric_series(positions, "sellable_quantity")
    qty_valid = positions.empty or bool(
        (
            (qty >= 0)
            & (sellable >= 0)
            & (sellable <= qty)
            & ((qty % 1).abs() <= 1e-9)
            & ((sellable % 1).abs() <= 1e-9)
        ).all()
    )
    add("broker_position_quantities_valid", qty_valid, f"positions={len(positions)}", "integer; 0 <= sellable <= quantity", "account_blocker", "修正适配器持仓数量字段和股/手单位映射。", "券商持仓数量和可卖数量必须为非负整数，且可卖不超过总持仓。")
    expected_count = int(first_number(numeric_series(snapshot, "positions_count")))
    count_consistent = len(snapshot) == 1 and expected_count >= 0 and expected_count == len(positions)
    add("broker_positions_count_consistent", count_consistent, f"summary={expected_count};rows={len(positions)}", "summary equals parsed rows", "account_blocker", "核对持仓汇总数量，确认持仓表未漏行或多读。", "账户汇总的持仓数必须与持仓明细行数一致。")

    costs = numeric_series(positions, "cost")
    prices = numeric_series(positions, "last_price")
    position_values = numeric_series(positions, "market_value")
    position_money_valid = positions.empty or bool(
        ((costs >= 0) & (prices > 0) & (position_values > 0) & (qty > 0)).all()
    )
    add("broker_position_money_valid", position_money_valid, f"positions={len(positions)}", "cost >= 0; quantity/price/market_value > 0", "account_blocker", "修正持仓价格、市值字段或过滤无效空行。", "有效持仓必须具有正股数、正现价和正市值，成本不得为负。")
    calculated_values = qty * prices
    row_value_tolerance = position_values.abs().mul(0.005).clip(lower=1.0)
    row_values_consistent = positions.empty or bool(
        ((position_values - calculated_values).abs() <= row_value_tolerance).all()
    )
    max_row_gap = float((position_values - calculated_values).abs().max()) if not positions.empty else 0.0
    add("broker_position_row_values_consistent", row_values_consistent, f"max_gap={max_row_gap:.2f}", "<= max(1 yuan, 0.5% row market value)", "account_blocker", "核对数量、现价、市值列及股/手、元/万元单位。", "每行持仓市值应与持仓数量乘以现价基本一致。")
    position_total = float(position_values.sum()) if not positions.empty else 0.0
    position_total_gap = abs(position_total - first_number(market_value))
    position_total_tolerance = max(cash_tolerance, abs(first_number(market_value)) * 0.001)
    add("broker_position_market_value_reconciles", len(snapshot) == 1 and position_total_gap <= position_total_tolerance, f"positions={position_total:.2f};summary={first_number(market_value):.2f};gap={position_total_gap:.2f}", f"<= {position_total_tolerance:.2f}", "account_blocker", "检查持仓表是否漏行、重复，或市值单位是否错误。", "持仓明细市值合计必须与账户汇总持仓市值一致。")

    add("local_account_snapshot_present", local_state_available, local_state_value, "initialized live ledger and account_state_snapshot.csv present", "account_blocker", "经操作员确认后初始化实盘账本并重新生成账户状态。", "券商快照必须与已初始化的独立实盘账本对账；旧模拟 CSV 不能替代实盘账本。")
    if local_state_available and len(snapshot) == 1:
        local_cash = to_float(local_snapshot.iloc[0].get("cash", 0.0))
        local_equity = to_float(local_snapshot.iloc[0].get("marked_equity", local_snapshot.iloc[0].get("recorded_equity", 0.0)))
        cash_gap = abs(first_number(cash) - local_cash)
        equity_gap = abs(first_number(total_asset) - local_equity)
        add("local_vs_broker_cash_match", cash_gap <= cash_tolerance, f"gap={cash_gap:.2f}", f"<= {cash_tolerance:g}", "account_blocker", "先同步/导入真实券商账户现金，再提交订单。", "本地现金必须与券商现金一致或在容差内。")
        add("local_vs_broker_total_asset_match", equity_gap <= max(cash_tolerance, abs(local_equity) * 0.005), f"gap={equity_gap:.2f}", "<= max(cash_tolerance, 0.5% local equity)", "account_blocker", "核对本地估值和券商账户总资产差异。", "本地账户总资产应与券商账户总资产基本一致。")
    else:
        add("local_vs_broker_cash_match", False, local_state_value, f"<= {cash_tolerance:g}", "account_blocker", "经操作员确认后初始化实盘账本，再同步券商现金。", "实盘账本未初始化或缺少本地账户快照时，不计算误导性的现金差额。")
        add("local_vs_broker_total_asset_match", False, local_state_value, "<= max(cash_tolerance, 0.5% local equity)", "account_blocker", "经操作员确认后初始化实盘账本，再核对总资产。", "实盘账本未初始化或缺少本地账户快照时，不计算误导性的总资产差额。")

    local_map = position_quantity_map(local_positions) if local_state_available else {}
    broker_map = position_quantity_map(positions)
    if local_state_available:
        symbols_match = set(local_map) == set(broker_map)
        quantity_match = symbols_match and all(abs(local_map[symbol] - broker_map[symbol]) <= 0 for symbol in local_map)
        local_sellable = position_value_map(local_positions, "sellable_quantity")
        broker_sellable = position_value_map(positions, "sellable_quantity")
        sellable_match = symbols_match and all(
            abs(local_sellable.get(symbol, 0.0) - broker_sellable.get(symbol, 0.0)) <= 0
            for symbol in set(local_map) | set(broker_map)
        )
        symbol_value = f"matched={len(set(local_map) & set(broker_map))};local={len(local_map)};broker={len(broker_map)}"
        quantity_value = position_gap_text(local_map, broker_map)
        sellable_value = position_gap_text(local_sellable, broker_sellable)
    else:
        symbols_match = quantity_match = sellable_match = False
        symbol_value = quantity_value = sellable_value = local_state_value
    add("local_vs_broker_position_symbols_match", symbols_match, symbol_value, "same non-zero position symbols", "account_blocker", "初始化实盘账本后同步/导入真实券商持仓。", "本地持仓证券集合必须来自已初始化实盘账本并与券商端一致。")
    add("local_vs_broker_position_quantities_match", quantity_match, quantity_value, "same quantities", "account_blocker", "初始化实盘账本后同步/导入真实券商持仓数量。", "本地持仓数量必须来自已初始化实盘账本并与券商端一致。")
    add(
        "local_vs_broker_sellable_quantities_match",
        sellable_match,
        sellable_value,
        "same sellable quantities for every held symbol",
        "account_blocker",
        "初始化实盘账本后核对 T+1 锁定、在途委托和券商可用股份。",
        "本地可卖数量必须来自已初始化实盘账本并与券商端一致。",
    )
    return pd.DataFrame(rows, columns=AUTO_LIVE_BROKER_ACCOUNT_AUDIT_COLUMNS)


def broker_clock_status(
    config: dict[str, Any],
    snapshot: pd.DataFrame,
    now: datetime | None = None,
) -> tuple[bool, str, str]:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    required = parse_bool_like(auto.get("require_broker_clock_sync", False))
    max_skew = max(to_float(auto.get("max_broker_clock_skew_seconds", 5.0)), 0.0)
    threshold = f"required={required};timezone-aware;abs(skew_seconds)<={max_skew:g}"
    if len(snapshot) != 1 or "broker_time" not in snapshot.columns:
        return (not required), f"broker_time=missing;required={required}", threshold

    raw = str(snapshot.iloc[0].get("broker_time", "") or "").strip()
    if not raw:
        return (not required), f"broker_time=missing;required={required}", threshold
    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return False, f"broker_time={raw};parse=failed", threshold
    if parsed.tzinfo is None:
        return False, f"broker_time={raw};timezone=missing", threshold
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    current = current.tz_convert("UTC")
    broker_utc = parsed.tz_convert("UTC")
    skew = abs(float((current - broker_utc).total_seconds()))
    return skew <= max_skew, f"skew_seconds={skew:.3f};broker_time={parsed.isoformat()}", threshold


def account_cash_tolerance(config: dict[str, Any]) -> float:
    auto = config.get("auto_live", {}) if isinstance(config.get("auto_live", {}), dict) else {}
    reconciliation = config.get("reconciliation", {}) if isinstance(config.get("reconciliation", {}), dict) else {}
    return max(to_float(auto.get("account_snapshot_cash_tolerance", reconciliation.get("cash_tolerance", 1.0))), 0.0)


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(-1.0)


def to_float(value: object) -> float:
    try:
        parsed = pd.to_numeric(value, errors="coerce")
    except Exception:
        return 0.0
    if pd.isna(parsed):
        return 0.0
    return float(parsed)

def first_present(mapping: dict[str, object], *keys: str) -> object:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def auditable_float(value: object) -> float:
    if value is None or isinstance(value, bool):
        return float("nan")
    try:
        parsed = pd.to_numeric(value, errors="coerce")
    except Exception:
        return float("nan")
    if pd.isna(parsed):
        return float("nan")
    return float(parsed)


def first_number(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return float(series.iloc[0])


def money_range_text(*series_list: pd.Series) -> str:
    values = [first_number(series) for series in series_list if not series.empty]
    if not values:
        return "missing"
    return f"min={min(values):.2f};max={max(values):.2f}"


def position_quantity_map(frame: pd.DataFrame) -> dict[str, float]:
    return position_value_map(frame, "quantity", positive_only=True)


def position_value_map(
    frame: pd.DataFrame,
    column: str,
    *,
    positive_only: bool = False,
) -> dict[str, float]:
    if frame.empty or "symbol" not in frame.columns or column not in frame.columns:
        return {}
    result: dict[str, float] = {}
    for row in frame.itertuples(index=False):
        symbol = str(getattr(row, "symbol", "")).strip().zfill(6)
        value = to_float(getattr(row, column, 0.0))
        if symbol and symbol != "000000" and (value > 0 or not positive_only):
            result[symbol] = result.get(symbol, 0.0) + value
    return result


def position_gap_text(local_map: dict[str, float], broker_map: dict[str, float]) -> str:
    symbols = sorted(set(local_map) | set(broker_map))
    if not symbols:
        return "no_positions"
    gaps = [abs(local_map.get(symbol, 0.0) - broker_map.get(symbol, 0.0)) for symbol in symbols]
    return f"symbols={len(symbols)};max_gap={max(gaps):.0f}"
