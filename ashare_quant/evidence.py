from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .candidate import live_strategy_hash
from .config import project_path
from .data import normalize_symbol
from .io_utils import atomic_to_csv
from .readiness import shadow_observation_values


EVIDENCE_PLAN_COLUMNS = [
    "area",
    "status",
    "current",
    "required",
    "remaining",
    "blocker",
    "command",
    "next_action",
]


def write_evidence_plan(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "evidence_plan.csv"
    atomic_to_csv(build_evidence_plan(config), path, index=False, encoding="utf-8-sig")
    return path


def build_evidence_plan(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    live_guard = config.get("live_guard", {})
    forward = read_csv(output_dir / "live_candidate_forward_progress.csv")
    formal_forward = read_csv(output_dir / "forward_only_candidate_observation.csv")
    shadow = read_csv(output_dir / "shadow_forward_audit.csv")
    account_audit = read_csv(output_dir / "account_state_audit.csv")
    account_missing = read_csv(output_dir / "account_state_missing_prices.csv", dtype={"symbol": str})
    account_paths = read_csv(output_dir / "account_path_decision.csv")
    rpa_checks = read_csv(output_dir / "rpa_pre_trade_checks.csv")
    execution_modes = read_csv(output_dir / "execution_modes.csv")
    auto_summary = read_csv(output_dir / "auto_live_summary.csv")
    rows: list[dict[str, object]] = []

    def add(area: str, status: str, current: object, required: object, remaining: object, blocker: str, command: str, next_action: str) -> None:
        rows.append(
            {
                "area": area,
                "status": status,
                "current": current,
                "required": required,
                "remaining": remaining,
                "blocker": blocker,
                "command": command,
                "next_action": next_action,
            }
        )

    forward_row = first_row(forward)
    formal_rows = formal_forward.to_dict("records") if not formal_forward.empty else []
    if formal_rows:
        required_values = [
            int(number(row.get("required_forward_trading_days"), live_guard.get("min_forward_trading_days", 60)) or 60)
            for row in formal_rows
        ]
        observed_values = [
            int(number(row.get("max_observed_trading_days"), 0) or 0)
            for row in formal_rows
        ]
        required_forward = max(required_values, default=60)
        observed_forward = min(observed_values, default=0)
        remaining_forward = max(
            (max(0, required - observed) for required, observed in zip(required_values, observed_values)),
            default=required_forward,
        )
        formal_status_ok = all(str(row.get("status", "")).strip().lower() == "observed" for row in formal_rows)
        windows_complete = all(
            str(row.get("forward_observation_status", "")).strip().lower() == "shadow_window_complete"
            for row in formal_rows
        )
        forward_passed = formal_status_ok and windows_complete and remaining_forward == 0
        forward_status = "passed" if forward_passed else ("collecting" if formal_status_ok else "blocked")
        forward_blocker = "" if forward_passed else (
            "formal_candidate_set_forward_trading_days" if formal_status_ok else "formal_candidate_set_invalid"
        )
        candidate_count = len(formal_rows)
        forward_command = "python -m ashare_quant forward-daily --quiet"
        forward_action = (
            "正式前向候选集已满足天数门槛。"
            if forward_passed
            else f"继续每日观察 {candidate_count} 个正式冻结候选；以最慢候选计还差 {remaining_forward} 个交易日。"
        )
    else:
        required_forward = int(number(forward_row.get("required_forward_trading_days"), live_guard.get("min_forward_trading_days", 60)) or 60)
        observed_forward = int(number(forward_row.get("observed_forward_trading_days"), 0) or 0)
        remaining_forward = max(0, required_forward - observed_forward)
        legacy_status = str(forward_row.get("status", "missing")) if forward_row else "missing"
        forward_passed = legacy_status == "validated" or remaining_forward == 0
        forward_status = "passed" if forward_passed else ("blocked" if legacy_status.startswith("failed") else "collecting")
        forward_blocker = "" if forward_passed else "candidate_forward_trading_days"
        forward_command = "python -m ashare_quant observe-candidates --quiet"
        forward_action = "前向验证已满足天数门槛。" if forward_passed else f"继续每日观察；还差 {remaining_forward} 个冻结后交易日。"
    add(
        "forward_validation",
        forward_status,
        observed_forward,
        required_forward,
        remaining_forward,
        forward_blocker,
        forward_command,
        forward_action,
    )

    required_shadow = int(live_guard.get("min_shadow_observed_signals", 20))
    if formal_rows:
        formal_shadow_values = [
            int(number(row.get("observable_signal_count"), 0) or 0)
            for row in formal_rows
        ]
        observed_shadow = min(formal_shadow_values, default=0)
        remaining_shadow = max(0, required_shadow - observed_shadow)
        shadow_passed = formal_status_ok and all(value >= required_shadow for value in formal_shadow_values)
        shadow_status = "passed" if shadow_passed else ("collecting" if formal_status_ok else "blocked")
        shadow_blocker = "" if shadow_passed else (
            "formal_candidate_set_shadow_observed_signal_count" if formal_status_ok else "formal_candidate_set_invalid"
        )
        shadow_command = "python -m ashare_quant forward-daily --quiet"
        shadow_action = (
            "正式候选集的 Shadow 样本均已满足数量门槛。"
            if shadow_passed
            else f"继续记录 {len(formal_rows)} 个正式冻结候选的 Shadow 信号；以最慢候选计还差 {remaining_shadow} 个可评估样本。"
        )
    else:
        candidate_id = str(forward_row.get("candidate_id", "")) if forward_row else ""
        require_current_shadow = bool(live_guard.get("require_current_candidate_shadow", True))
        min_shadow_days = int(live_guard.get("min_shadow_observed_days_per_signal", 1))
        shadow_stats = shadow_observation_values(
            shadow,
            candidate_id=candidate_id,
            strategy_hash=live_strategy_hash(config),
            require_current=require_current_shadow,
            min_observed_days=min_shadow_days,
        )
        observed_shadow = int(shadow_stats["observed_signal_count"] or 0)
        remaining_shadow = max(0, required_shadow - observed_shadow)
        shadow_passed = observed_shadow >= required_shadow
        shadow_status = "passed" if shadow_passed else "collecting"
        shadow_blocker = "" if shadow_passed else "shadow_observed_signal_count"
        shadow_command = "python -m ashare_quant run --allow-stale"
        shadow_action = "Shadow 样本已满足当前候选参数数量门槛。" if shadow_passed else f"继续记录当前候选参数的 Shadow 信号；还差 {remaining_shadow} 个可评估样本。旧候选样本不会计入当前自动实盘门禁。"
    add(
        "shadow_observation",
        shadow_status,
        observed_shadow,
        required_shadow,
        remaining_shadow,
        shadow_blocker,
        shadow_command,
        shadow_action,
    )

    unique_missing = unique_missing_symbols(account_missing)
    missing_count = len(unique_missing)
    missing_without_price_cache_symbols = false_symbols(account_missing, "symbol_in_price_cache")
    missing_without_price_cache = len(missing_without_price_cache_symbols)
    unresolved_fetch_failures = missing_without_price_cache_symbols & recent_fetch_failed_symbols(config)
    account_failed = failed_checks(account_audit)
    priced_only = path_status(account_paths, "real_account_priced_only_import")
    if account_audit.empty:
        account_status = "blocked"
        account_current: object = "missing"
        account_remaining: object = 1
        account_blocker = "account_state_audit_missing"
        account_command = "python -m ashare_quant account-state"
        account_action = "先生成当前账户状态审计，再处理缺价或持仓不一致。"
    elif missing_count > 0:
        account_status = "blocked"
        account_current = missing_count
        account_remaining = missing_count
        account_blocker = "missing_position_prices"
        account_command = "python -m ashare_quant account-price-template"
        account_action = "补齐缺价持仓价格，或人工确认排除缺价持仓后导入可估值模板。"
        if missing_without_price_cache > 0:
            if unresolved_fetch_failures and unresolved_fetch_failures == missing_without_price_cache_symbols:
                unresolved = ",".join(sorted(unresolved_fetch_failures))
                if priced_only.startswith("precheck_passed"):
                    account_command = "python -m ashare_quant account-import-check --file outputs/account_import_priced_only_template.csv"
                    account_action = f"最近行情补齐已确认这些代码无可用行情：{unresolved}；可估值模板 dry-run 已通过。人工确认排除清单后，才执行 account-import --confirm --ack-exclusions。"
                elif priced_only == "available_with_exclusions":
                    account_command = "python -m ashare_quant account-import-check --file outputs/account_import_priced_only_template.csv"
                    account_action = f"最近行情补齐已确认这些代码无可用行情：{unresolved}；先 dry-run 校验可估值模板，人工确认排除清单后才允许导入。"
                else:
                    account_command = "python -m ashare_quant account-repair-template"
                    account_action = f"最近行情补齐已确认这些代码无可用行情：{unresolved}；请核对是否旧模拟持仓、退市或代码错误，真实账户导入前修正或排除。"
            else:
                account_command = "python -m ashare_quant fetch-account-prices"
                account_action = f"先刷新行情池；当前数据刷新会自动追加 SQLite 持仓代码，仍缺价时再走 account-price-template。缺少价格缓存的证券数：{missing_without_price_cache}。"
        elif priced_only.startswith("precheck_passed") or priced_only == "available_with_exclusions":
            account_command = "python -m ashare_quant account-import-check --file outputs/account_import_priced_only_template.csv"
            account_action = "可估值模板 dry-run 已通过；人工确认排除清单后，才执行 account-import --confirm --ack-exclusions。"
    elif account_failed:
        account_status = "blocked"
        account_current = len(account_failed)
        account_remaining = len(account_failed)
        account_blocker = ";".join(account_failed)
        account_command = "python -m ashare_quant account-state"
        account_action = "账户审计仍有失败项；先按 account_state_audit.csv 修复。"
    else:
        account_status = "passed"
        account_current = 0
        account_remaining = 0
        account_blocker = ""
        account_command = "python -m ashare_quant account-state"
        account_action = "账户审计已通过。"
    add(
        "account_audit",
        account_status,
        account_current,
        0,
        account_remaining,
        account_blocker,
        account_command,
        account_action,
    )

    if rpa_checks.empty:
        rpa_status = "blocked"
        rpa_current: object = "missing"
        rpa_remaining: object = 1
        rpa_blocker = "rpa_pre_trade_checks_missing"
        rpa_command = "python -m ashare_quant export-orders"
        rpa_action = "先生成 RPA 工单和下单前检查。"
    else:
        rpa_failed = failed_checks(rpa_checks)
        rpa_status = "passed" if not rpa_failed else "blocked"
        rpa_current = 0 if not rpa_failed else len(rpa_failed)
        rpa_remaining = 0 if not rpa_failed else len(rpa_failed)
        rpa_blocker = ";".join(rpa_failed)
        rpa_command = "python -m ashare_quant export-orders" if not rpa_failed else "python -m ashare_quant account-state"
        rpa_action = "RPA 前置检查已通过。" if not rpa_failed else "先修复 RPA 前置失败项；当前主要依赖账户审计。"
    add(
        "rpa_pre_trade",
        rpa_status,
        rpa_current,
        0,
        rpa_remaining,
        rpa_blocker,
        rpa_command,
        rpa_action,
    )

    strict_mode = mode_status(execution_modes, "strict_live_execution")
    add(
        "strict_live_gate",
        "passed" if strict_mode == "ready" else "blocked",
        strict_mode or "missing",
        "ready",
        "",
        mode_blockers(execution_modes, "strict_live_execution") or ("execution_modes_missing" if not strict_mode else ""),
        "python -m ashare_quant observe-candidates --quiet",
        "严格实盘门控已通过。" if strict_mode == "ready" else "继续累计前向/Shadow 证据，并处理账户与 RPA 阻断。",
    )

    auto_row = first_row(auto_summary)
    auto_allowed = parse_bool(auto_row.get("auto_live_allowed")) if auto_row else False
    add(
        "auto_live_config",
        "passed" if auto_allowed else "blocked",
        bool(auto_allowed),
        True,
        int(not auto_allowed),
        "" if auto_allowed else str(auto_row.get("blockers", "auto_live_not_ready") if auto_row else "auto_live_not_ready"),
        "python -m ashare_quant auto-live-check",
        "自动实盘配置已允许。" if auto_allowed else "只有执行、严格实盘、账户、RPA 与显式券商适配器全部通过后才允许自动实盘。",
    )
    return pd.DataFrame(rows, columns=EVIDENCE_PLAN_COLUMNS)


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except EmptyDataError:
        return pd.DataFrame()


def first_row(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    return frame.iloc[0].to_dict()


def number(value: object, default: object = None) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_numeric(default, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def unique_missing_symbols(frame: pd.DataFrame) -> set[str]:
    if frame.empty or "symbol" not in frame.columns:
        return set()
    return {normalize_symbol(value) for value in frame["symbol"].dropna() if normalize_symbol(value)}


def false_symbols(frame: pd.DataFrame, column: str) -> set[str]:
    if frame.empty or column not in frame.columns or "symbol" not in frame.columns:
        return set()
    data = frame.copy()
    false_rows = data[~data[column].map(parse_bool)]
    return {normalize_symbol(value) for value in false_rows["symbol"].dropna() if normalize_symbol(value)}


def false_symbol_count(frame: pd.DataFrame, column: str) -> int:
    return len(false_symbols(frame, column))


def recent_fetch_failed_symbols(config: dict[str, Any]) -> set[str]:
    cache_dir = project_path(config.get("paths", {}).get("cache_dir", "data/cache"))
    failures = read_table(cache_dir / "fetch_failures")
    if failures.empty or "symbol" not in failures.columns:
        return set()
    return {normalize_symbol(value) for value in failures["symbol"].dropna() if normalize_symbol(value)}


def read_table(base_path: Path) -> pd.DataFrame:
    parquet_path = base_path.with_suffix(".parquet")
    csv_path = base_path.with_suffix(".csv")
    if parquet_path.exists():
        try:
            return pd.read_parquet(parquet_path)
        except Exception:
            return pd.DataFrame()
    return read_csv(csv_path)


def failed_checks(frame: pd.DataFrame) -> list[str]:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return []
    failed = frame[~frame["passed"].map(parse_bool)]
    return failed["check"].dropna().astype(str).tolist()


def path_status(frame: pd.DataFrame, path_name: str) -> str:
    if frame.empty or not {"path", "status"}.issubset(frame.columns):
        return ""
    matched = frame[frame["path"].astype(str) == path_name]
    return "" if matched.empty else str(matched["status"].iloc[0])


def mode_status(frame: pd.DataFrame, mode: str) -> str:
    if frame.empty or not {"mode", "status"}.issubset(frame.columns):
        return ""
    matched = frame[frame["mode"].astype(str) == mode]
    return "" if matched.empty else str(matched["status"].iloc[0])


def mode_blockers(frame: pd.DataFrame, mode: str) -> str:
    if frame.empty or not {"mode", "blockers"}.issubset(frame.columns):
        return ""
    matched = frame[frame["mode"].astype(str) == mode]
    return "" if matched.empty else str(matched["blockers"].iloc[0] or "")
