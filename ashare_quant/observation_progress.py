from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv


OBSERVATION_PROGRESS_COLUMNS = [
    "output_dir",
    "strategy_name",
    "candidate_id",
    "before_latest_market_date",
    "after_latest_market_date",
    "market_date_delta_days",
    "before_forward_days",
    "after_forward_days",
    "forward_days_delta",
    "before_shadow_signals",
    "after_shadow_signals",
    "shadow_signals_delta",
    "status",
    "message",
    "generated_at",
]
OBSERVATION_PROGRESS_AUDIT_COLUMNS = [
    "check",
    "passed",
    "value",
    "threshold",
    "severity",
    "message",
]
SNAPSHOT_COLUMNS = [
    "output_dir",
    "strategy_name",
    "candidate_id",
    "latest_market_date",
    "forward_days",
    "shadow_signals",
]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except (OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _text(value: object) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _number(value: object) -> int | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else int(parsed)


def _date(value: object) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed).normalize()


def _deduplicated_configs(configs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for config in configs:
        try:
            output_dir = str(project_path(config["paths"]["output_dir"]).resolve())
        except (KeyError, TypeError, ValueError):
            continue
        if output_dir in seen:
            continue
        seen.add(output_dir)
        result.append(config)
    return result


def capture_observation_snapshot(configs: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for config in _deduplicated_configs(configs):
        output_dir = project_path(config["paths"]["output_dir"]).resolve()
        progress = _read_csv(output_dir / "live_candidate_forward_progress.csv")
        current = progress.iloc[-1] if not progress.empty else pd.Series(dtype=object)
        candidate_id = _text(current.get("candidate_id", ""))
        latest_market_date = _text(current.get("latest_comparison_date", ""))
        forward_days = _number(current.get("observed_forward_trading_days"))

        shadow = _read_csv(output_dir / "shadow_forward_audit.csv")
        if not shadow.empty:
            if candidate_id and "candidate_id" in shadow.columns:
                shadow = shadow[shadow["candidate_id"].fillna("").astype(str).str.strip() == candidate_id]
            if "status" in shadow.columns:
                shadow = shadow[shadow["status"].fillna("").astype(str).str.lower() == "ok"]
            if "observed_trading_days" in shadow.columns:
                observed = pd.to_numeric(shadow["observed_trading_days"], errors="coerce").fillna(0)
                shadow = shadow[observed >= 1]
            shadow_signals = int(shadow["signal_hash"].nunique()) if "signal_hash" in shadow.columns else len(shadow)
        else:
            shadow_signals = 0

        rows.append(
            {
                "output_dir": str(output_dir),
                "strategy_name": str(config.get("strategy", {}).get("name", "") or ""),
                "candidate_id": candidate_id,
                "latest_market_date": latest_market_date,
                "forward_days": forward_days,
                "shadow_signals": shadow_signals,
            }
        )
    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)


def build_observation_progress(
    before: pd.DataFrame,
    after: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    generated_at = (now or datetime.now(timezone.utc)).astimezone().isoformat()
    before_rows = {
        _text(row.output_dir): row._asdict()
        for row in before.itertuples(index=False)
        if _text(row.output_dir)
    }
    rows: list[dict[str, object]] = []

    for after_row in after.itertuples(index=False):
        current = after_row._asdict()
        output_dir = _text(current.get("output_dir"))
        previous = before_rows.get(output_dir, {})
        before_id = _text(previous.get("candidate_id", ""))
        after_id = _text(current.get("candidate_id", ""))
        before_date = _date(previous.get("latest_market_date", ""))
        after_date = _date(current.get("latest_market_date", ""))
        before_forward = _number(previous.get("forward_days"))
        after_forward = _number(current.get("forward_days"))
        before_shadow = _number(previous.get("shadow_signals"))
        after_shadow = _number(current.get("shadow_signals"))

        market_delta = None if before_date is None or after_date is None else int((after_date - before_date).days)
        forward_delta = None if before_forward is None or after_forward is None else after_forward - before_forward
        shadow_delta = None if before_shadow is None or after_shadow is None else after_shadow - before_shadow

        if not after_id or after_date is None or after_forward is None:
            status = "incomplete_snapshot"
            message = "候选前向快照缺少候选 ID、市场日期或前向交易日。"
        elif before_id and before_id != after_id:
            status = "candidate_identity_changed"
            message = "刷新过程中候选 ID 发生变化；旧证据不得自动继承。"
        elif market_delta is not None and market_delta < 0:
            status = "market_date_regressed"
            message = "最新可比较市场日期发生倒退。"
        elif forward_delta is not None and forward_delta < 0:
            status = "forward_evidence_regressed"
            message = "候选前向交易日计数发生倒退。"
        elif shadow_delta is not None and shadow_delta < 0:
            status = "shadow_evidence_regressed"
            message = "候选 Shadow 信号计数发生倒退。"
        elif market_delta is not None and market_delta > 0 and (forward_delta is None or forward_delta <= 0):
            status = "forward_evidence_stalled"
            message = "市场日期已经前进，但冻结候选的前向证据没有增长。"
        elif market_delta is not None and market_delta > 0:
            status = "progressed"
            message = "市场日期与候选前向证据均已推进。"
        elif market_delta == 0:
            status = "no_new_market_day"
            message = "没有新的完整市场日期，重复观察未增加证据。"
        elif not previous:
            status = "baseline_created"
            message = "已创建候选观察进度基线。"
        else:
            status = "incomplete_comparison"
            message = "运行前快照不完整，暂不能判断证据是否推进。"

        rows.append(
            {
                "output_dir": output_dir,
                "strategy_name": _text(current.get("strategy_name")),
                "candidate_id": after_id,
                "before_latest_market_date": "" if before_date is None else before_date.date().isoformat(),
                "after_latest_market_date": "" if after_date is None else after_date.date().isoformat(),
                "market_date_delta_days": market_delta,
                "before_forward_days": before_forward,
                "after_forward_days": after_forward,
                "forward_days_delta": forward_delta,
                "before_shadow_signals": before_shadow,
                "after_shadow_signals": after_shadow,
                "shadow_signals_delta": shadow_delta,
                "status": status,
                "message": message,
                "generated_at": generated_at,
            }
        )

    progress = pd.DataFrame(rows, columns=OBSERVATION_PROGRESS_COLUMNS)
    statuses = set(progress["status"].astype(str)) if not progress.empty else set()
    missing_outputs = sorted(set(before_rows) - set(progress["output_dir"].astype(str))) if not progress.empty else sorted(before_rows)

    def audit_row(check: str, passed: bool, value: object, threshold: str, severity: str, message: str) -> dict[str, object]:
        return {
            "check": check,
            "passed": passed,
            "value": value,
            "threshold": threshold,
            "severity": severity if not passed else "info",
            "message": message,
        }

    audit = pd.DataFrame(
        [
            audit_row(
                "observation_snapshot_complete",
                bool(rows) and not missing_outputs and "incomplete_snapshot" not in statuses,
                f"after={len(rows)}; missing_after={len(missing_outputs)}",
                "all observed candidates have complete post-refresh snapshots",
                "warning",
                "无人值守观察必须为每个候选保留可比较的候选 ID、市场日期和前向天数。",
            ),
            audit_row(
                "observation_candidate_identity_stable",
                "candidate_identity_changed" not in statuses,
                ";".join(sorted(statuses)) or "missing",
                "candidate_id unchanged during refresh",
                "critical",
                "候选 ID 改变会重置证据归属，必须人工复核而不能静默继续。",
            ),
            audit_row(
                "observation_market_date_not_regressed",
                "market_date_regressed" not in statuses,
                ";".join(sorted(statuses)) or "missing",
                "latest market date does not regress",
                "critical",
                "行情日期倒退通常表示缓存覆盖或数据源异常。",
            ),
            audit_row(
                "observation_forward_evidence_progress",
                not ({"forward_evidence_stalled", "forward_evidence_regressed"} & statuses),
                ";".join(sorted(statuses)) or "missing",
                "forward days grow when market date advances and never regress",
                "warning",
                "市场已更新时，冻结候选的前向交易日也必须推进。",
            ),
            audit_row(
                "observation_shadow_evidence_not_regressed",
                "shadow_evidence_regressed" not in statuses,
                ";".join(sorted(statuses)) or "missing",
                "shadow signal count never regresses",
                "critical",
                "Shadow 历史不得在日常刷新中被覆盖或丢失。",
            ),
        ],
        columns=OBSERVATION_PROGRESS_AUDIT_COLUMNS,
    )
    return progress, audit


def write_observation_progress(
    config: dict[str, Any],
    before: pd.DataFrame,
    after: pd.DataFrame,
) -> tuple[dict[str, Path], pd.DataFrame]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    progress, audit = build_observation_progress(before, after)
    progress_path = output_dir / "daily_observation_progress.csv"
    audit_path = output_dir / "daily_observation_progress_audit.csv"
    atomic_to_csv(progress, progress_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    return {progress_path.name: progress_path, audit_path.name: audit_path}, audit


def observation_progress_blockers(audit: pd.DataFrame) -> list[str]:
    if audit.empty or not {"check", "passed"}.issubset(audit.columns):
        return ["observation_progress_audit_missing"]
    failed = audit[~audit["passed"].astype(str).str.lower().isin({"true", "1", "yes"})]
    return failed["check"].astype(str).tolist()
