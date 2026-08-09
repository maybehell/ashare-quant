from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .autolive_alerts import ALERT_OUTBOX_FILE, send_auto_live_alert
from .config import project_path
from .io_utils import atomic_to_csv


OBSERVATION_ALERT_STATUS_COLUMNS = [
    "status",
    "event_type",
    "severity",
    "fingerprint",
    "delivery_status",
    "observation_status",
    "strategy_health_status",
    "recovery_candidate",
    "recovery_decision",
    "forward_days",
    "required_forward_days",
    "forward_excess_return",
    "forward_selection_status",
    "formal_candidate_count",
    "structural_family_count",
    "selected_candidate_name",
    "broker_refresh_status",
    "broker_refresh_stage",
    "message",
    "generated_at",
]
OBSERVATION_ALERT_STATE_COLUMNS = [
    "fingerprint",
    "active",
    "event_type",
    "severity",
    "updated_at",
]


def build_observation_monitor_event(
    observation: pd.DataFrame,
    strategy_health: pd.DataFrame,
    promotion_decision: pd.DataFrame,
    source_health: pd.DataFrame,
    forward_selection: pd.DataFrame | None = None,
    broker_refresh: pd.DataFrame | None = None,
) -> dict[str, Any] | None:
    observation_status = _first(observation, "status")
    health_status = _check_value(strategy_health, "strategy_health_status")
    promotion = promotion_decision.iloc[-1].to_dict() if not promotion_decision.empty else {}
    decision = str(promotion.get("decision", "") or "").strip()
    candidate = str(promotion.get("candidate_strategy_name", "") or "").strip()
    days = _text_number(promotion.get("forward_days", ""), integer=True)
    required_days = _text_number(promotion.get("required_forward_days", ""), integer=True)
    forward_excess = _number(promotion.get("forward_excess_return", ""))
    selection = forward_selection if forward_selection is not None else pd.DataFrame()
    selection_status = _first(selection, "status")
    selection_days = _text_number(_first(selection, "minimum_common_trading_days"), integer=True)
    selection_required = _text_number(_first(selection, "required_common_trading_days"), integer=True)
    formal_candidate_count = _text_number(_first(selection, "candidate_count"), integer=True)
    structural_family_count = _text_number(_first(selection, "structural_family_count"), integer=True)
    selected_candidate_name = _first(selection, "selected_candidate_name")
    broker = broker_refresh if broker_refresh is not None else pd.DataFrame()
    broker_refresh_status = _first(broker, "status")
    broker_refresh_stage = _first(broker, "stage")
    if selection_days:
        days = selection_days
    if selection_required:
        required_days = selection_required
    context = {
        "observation_status": observation_status or "missing",
        "strategy_health_status": health_status or "missing",
        "recovery_candidate": candidate,
        "recovery_decision": decision,
        "forward_days": days,
        "required_forward_days": required_days,
        "forward_excess_return": forward_excess,
        "forward_selection_status": selection_status,
        "formal_candidate_count": formal_candidate_count,
        "structural_family_count": structural_family_count,
        "selected_candidate_name": selected_candidate_name,
        "source": "daily_observation",
        "broker_submission_capability": False,
        "broker_refresh_status": broker_refresh_status,
        "broker_refresh_stage": broker_refresh_stage,
    }

    if observation_status in {"failed", "stale_lock"}:
        return _event(
            "daily_observation_failed",
            "critical",
            "\u6bcf\u65e5\u89c2\u5bdf\u4efb\u52a1\u5931\u8d25\u6216\u7559\u4e0b\u5f02\u5e38\u8fd0\u884c\u9501\uff1b\u8bf7\u68c0\u67e5\u65e5\u5fd7\u548c\u6570\u636e\u6e90\u3002",
            context,
            active=True,
        )

    if broker_refresh_status == "failed":
        return _event(
            "broker_readonly_refresh_failed",
            "critical",
            f"华泰账户只读快照刷新失败（阶段：{broker_refresh_stage or 'unknown'}）；请检查客户端登录和资金股票页面。",
            context,
            active=True,
        )
    invalid_selection_statuses = {
        "invalid_evidence",
        "invalid_candidate_set",
        "structurally_redundant_candidate_set",
    }
    if selection_status in invalid_selection_statuses:
        return _event(
            "forward_candidate_selection_invalid",
            "critical" if selection_status != "structurally_redundant_candidate_set" else "warning",
            (
                f"正式候选决赛状态异常：{selection_status}；"
                f"候选={formal_candidate_count or '?'}，独立策略族={structural_family_count or '?'}。"
                "系统保持禁止生成工单。"
            ),
            context,
            active=True,
        )

    if decision.startswith("do_not_promote") and not selection_status:
        return _event(
            "recovery_candidate_blocked",
            "warning",
            f"\u6062\u590d\u5019\u9009 {candidate or 'unknown'} \u5df2\u89e6\u53d1\u5931\u8d25\u6216\u9694\u79bb\u6761\u4ef6\uff0c\u7981\u6b62\u664b\u7ea7\u3002",
            context,
            active=True,
        )

    if health_status == "pause_paper_orders":
        if selection_status:
            message = (
                "主策略仍处于工单暂停状态。"
                f"正式候选={formal_candidate_count or '?'}，独立策略族={structural_family_count or '?'}，"
                f"共同前向={days or '0'}/{required_days or '?'}，候选决赛={selection_status}。"
            )
        else:
            message = (
                "主策略仍处于工单暂停状态。"
                f"恢复候选={candidate or 'missing'}，阶段={decision or 'missing'}，"
                f"前向={days or '0'}/{required_days or '?'}。"
            )
        return _event(
            "strategy_orders_paused",
            "warning",
            message,
            context,
            active=True,
        )
    failed_sources = _failed_source_checks(source_health)
    if failed_sources:
        context["failed_source_checks"] = failed_sources
        return _event(
            "observation_data_source_warning",
            "warning",
            "\u6bcf\u65e5\u89c2\u5bdf\u5b8c\u6210\uff0c\u4f46\u6570\u636e\u6e90\u5b58\u5728\u5931\u8d25\u9879\uff1a" + ", ".join(failed_sources[:5]),
            context,
            active=True,
        )

    if decision in {"paper_trade_only", "allow_manual_live_review"}:
        return _event(
            "recovery_candidate_stage_changed",
            "info",
            f"\u6062\u590d\u5019\u9009 {candidate or 'unknown'} \u5df2\u8fdb\u5165 {decision}\uff1b\u4ecd\u4e0d\u4ee3\u8868\u81ea\u52a8\u4e0b\u5355\u6388\u6743\u3002",
            context,
            active=False,
        )
    if selection_status == "ready":
        return _event(
            "forward_candidate_selected",
            "info",
            (
                f"冻结候选决赛已选出 {selected_candidate_name or 'unknown'}；"
                "仍需通过策略、资金、账户与当次交易门禁，不代表自动下单授权。"
            ),
            context,
            active=False,
        )
    if selection_status == "no_clear_winner":
        return _event(
            "forward_candidate_no_clear_winner",
            "warning",
            f"冻结候选达到共同前向窗口后仍无明确胜者（{days or '?'}/{required_days or '?'}）。",
            context,
            active=True,
        )
    return None


def write_observation_alert_status(
    alert_config: dict[str, Any],
    strategy_output: str | Path,
    *,
    sender: Callable[..., dict[str, Path]] = send_auto_live_alert,
) -> dict[str, Path]:
    strategy_dir = project_path(strategy_output)
    output_dir = project_path(alert_config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "observation_alert_status.csv"
    state_path = output_dir / "observation_alert_state.csv"
    previous = _latest(_read_csv(state_path))
    event = build_observation_monitor_event(
        _observation_status(strategy_dir),
        _read_csv(strategy_dir / "strategy_health.csv"),
        _read_csv(strategy_dir / "strategy_promotion_decision.csv"),
        _read_csv(strategy_dir / "data_source_health.csv"),
        _read_csv(strategy_dir / "forward_candidate_selection_summary.csv"),
        _read_csv(output_dir / "huatai_read_only_refresh.csv"),
    )
    if event is None and _bool(previous.get("active", False)):
        event = _event(
            "observation_recovered",
            "info",
            "\u6bcf\u65e5\u89c2\u5bdf\u548c\u7b56\u7565\u8fd0\u884c\u72b6\u6001\u5df2\u4ece\u4e0a\u6b21\u544a\u8b66\u4e2d\u6062\u590d\u3002",
            {
                "observation_status": "completed",
                "strategy_health_status": "healthy",
                "recovery_candidate": "",
                "recovery_decision": "",
                "forward_days": "",
                "required_forward_days": "",
                "forward_excess_return": None,
                "source": "daily_observation",
                "broker_submission_capability": False,
            },
            active=False,
        )
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if event is None:
        row = _status_row("no_alert", {}, "", "not_needed", generated_at)
        atomic_to_csv(pd.DataFrame([row], columns=OBSERVATION_ALERT_STATUS_COLUMNS), status_path, index=False, encoding="utf-8-sig")
        return {"observation_alert_status.csv": status_path}

    fingerprint = _fingerprint(event["identity"])
    if str(previous.get("fingerprint", "")) == fingerprint:
        row = _status_row("suppressed_duplicate", event, fingerprint, "already_delivered", generated_at)
        atomic_to_csv(pd.DataFrame([row], columns=OBSERVATION_ALERT_STATUS_COLUMNS), status_path, index=False, encoding="utf-8-sig")
        return {"observation_alert_status.csv": status_path, "observation_alert_state.csv": state_path}

    alert_id = f"observation_{fingerprint[:20]}"
    paths: dict[str, Path] = {}
    delivery_status = "failed"
    try:
        paths.update(
            sender(
                alert_config,
                event_type=str(event["event_type"]),
                severity=str(event["severity"]),
                message=str(event["message"]),
                context=dict(event["context"]),
                strict=False,
                alert_id=alert_id,
            )
        )
        delivery_status = _delivery_status(paths, alert_id)
    except Exception as exc:
        delivery_status = f"failed:{type(exc).__name__}:{exc}"

    delivered = delivery_status in {"delivered", "local_only", "sent"}
    if delivered:
        state = {
            "fingerprint": fingerprint,
            "active": bool(event["active"]),
            "event_type": event["event_type"],
            "severity": event["severity"],
            "updated_at": generated_at,
        }
        atomic_to_csv(pd.DataFrame([state], columns=OBSERVATION_ALERT_STATE_COLUMNS), state_path, index=False, encoding="utf-8-sig")
        paths["observation_alert_state.csv"] = state_path
    row = _status_row("delivered" if delivered else "pending_retry", event, fingerprint, delivery_status, generated_at)
    atomic_to_csv(pd.DataFrame([row], columns=OBSERVATION_ALERT_STATUS_COLUMNS), status_path, index=False, encoding="utf-8-sig")
    paths["observation_alert_status.csv"] = status_path
    return paths



def _observation_status(strategy_dir: Path) -> pd.DataFrame:
    raw = _read_csv(strategy_dir / "daily_observation_status.csv")
    if not raw.empty:
        return raw
    return _read_csv(strategy_dir / "daily_observation_summary.csv")

def _event(event_type: str, severity: str, message: str, context: dict[str, Any], *, active: bool) -> dict[str, Any]:
    identity = {
        "event_type": event_type,
        "observation_status": context.get("observation_status", ""),
        "strategy_health_status": context.get("strategy_health_status", ""),
        "recovery_candidate": context.get("recovery_candidate", ""),
        "recovery_decision": context.get("recovery_decision", ""),
        "failed_source_checks": context.get("failed_source_checks", []),
        "forward_selection_status": context.get("forward_selection_status", ""),
        "selected_candidate_name": context.get("selected_candidate_name", ""),
        "broker_refresh_status": context.get("broker_refresh_status", ""),
        "broker_refresh_stage": context.get("broker_refresh_stage", ""),
    }
    return {
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "context": context,
        "identity": identity,
        "active": active,
    }


def _status_row(status: str, event: dict[str, Any], fingerprint: str, delivery_status: str, generated_at: str) -> dict[str, object]:
    context = event.get("context", {}) if isinstance(event.get("context"), dict) else {}
    return {
        "status": status,
        "event_type": event.get("event_type", ""),
        "severity": event.get("severity", ""),
        "fingerprint": fingerprint,
        "delivery_status": delivery_status,
        "observation_status": context.get("observation_status", ""),
        "strategy_health_status": context.get("strategy_health_status", ""),
        "recovery_candidate": context.get("recovery_candidate", ""),
        "recovery_decision": context.get("recovery_decision", ""),
        "forward_days": context.get("forward_days", ""),
        "required_forward_days": context.get("required_forward_days", ""),
        "forward_excess_return": context.get("forward_excess_return", ""),
        "forward_selection_status": context.get("forward_selection_status", ""),
        "formal_candidate_count": context.get("formal_candidate_count", ""),
        "structural_family_count": context.get("structural_family_count", ""),
        "selected_candidate_name": context.get("selected_candidate_name", ""),
        "broker_refresh_status": context.get("broker_refresh_status", ""),
        "broker_refresh_stage": context.get("broker_refresh_stage", ""),
        "message": event.get("message", ""),
        "generated_at": generated_at,
    }


def _delivery_status(paths: dict[str, Path], alert_id: str) -> str:
    path = paths.get(ALERT_OUTBOX_FILE)
    if path is None:
        return "sent"
    outbox = _read_csv(Path(path))
    if outbox.empty or "alert_id" not in outbox:
        return "sent"
    selected = outbox[outbox["alert_id"].astype(str) == alert_id]
    return str(selected.iloc[-1].get("status", "sent") or "sent") if not selected.empty else "sent"


def _failed_source_checks(frame: pd.DataFrame) -> list[str]:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return []
    passed = frame["passed"].map(_bool)
    severity = frame.get("severity", pd.Series("warning", index=frame.index)).astype(str).str.lower()
    return frame.loc[~passed & severity.isin({"warning", "critical", "error"}), "check"].astype(str).tolist()


def _check_value(frame: pd.DataFrame, check: str) -> str:
    if frame.empty or not {"check", "value"}.issubset(frame.columns):
        return ""
    selected = frame[frame["check"].astype(str) == check]
    return _first(selected, "value")


def _first(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame:
        return ""
    value = frame.iloc[-1].get(column, "")
    return "" if pd.isna(value) else str(value).strip()


def _latest(frame: pd.DataFrame) -> dict[str, object]:
    return frame.iloc[-1].to_dict() if not frame.empty else {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False)
    except (OSError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _number(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def _text_number(value: object, *, integer: bool = False) -> str:
    parsed = _number(value)
    if parsed is None:
        return ""
    return str(int(parsed)) if integer else str(parsed)


def _fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"true", "1", "yes", "on"}
