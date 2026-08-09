from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Any

import pandas as pd

from .config import project_path
from .io_utils import atomic_to_csv


CAPITAL_MODE_COLUMNS = [
    "mode",
    "mode_label_cn",
    "snapshot_fresh",
    "current_total_asset",
    "available_cash",
    "strategy_target_positions",
    "minimum_diversified_positions",
    "nearest_tested_capital",
    "nearest_tested_positions",
    "nearest_tested_cash_ratio",
    "nearest_tested_return_drag",
    "minimum_full_strategy_equity",
    "capital_gap",
    "capital_ratio",
    "canary_ready",
    "full_strategy_ready",
    "strategy_deployment_allowed",
    "reason_cn",
]


def build_capital_mode_assessment(
    transition: pd.DataFrame,
    sensitivity: pd.DataFrame,
    strategy_config: dict[str, Any],
    *,
    snapshot_current: bool | None = None,
) -> pd.DataFrame:
    """Classify capital usability without adapting or promoting the strategy."""
    account = transition.iloc[-1] if not transition.empty else pd.Series(dtype=object)
    current = _number(account.get("current_total_asset"))
    available = _number(account.get("available_cash"))
    transition_fresh = _truth(account.get("snapshot_fresh"))
    snapshot_fresh = transition_fresh if snapshot_current is None else transition_fresh and snapshot_current
    canary_ready = _truth(account.get("canary_ready"))
    full_ready = _truth(account.get("full_strategy_ready")) and snapshot_fresh
    minimum_equity = _number(account.get("minimum_full_strategy_equity"))

    strategy = strategy_config.get("strategy", {})
    strategy = strategy if isinstance(strategy, dict) else {}
    target_positions = max(int(_number(strategy.get("top_n"), 0)), 0)
    max_weight = _number(strategy.get("max_stock_weight"), 0.0)
    minimum_diversified = ceil(1.0 / max_weight) if 0 < max_weight <= 1 else target_positions
    minimum_diversified = min(minimum_diversified, target_positions) if target_positions else minimum_diversified

    nearest = _nearest_tested_level(sensitivity, current)
    tested_capital = _number(nearest.get("initial_cash"))
    tested_positions = max(int(_number(nearest.get("final_position_count"), 0)), 0)
    tested_cash_ratio = _number(nearest.get("average_cash_ratio"))
    tested_drag = _number(nearest.get("return_drag_vs_reference"))

    capital_gap = max(minimum_equity - current, 0.0) if minimum_equity > 0 else float("nan")
    capital_ratio = current / minimum_equity if minimum_equity > 0 else float("nan")
    evidence_ready = not transition.empty and not sensitivity.empty and minimum_equity > 0

    if not snapshot_fresh:
        mode = "refresh_required"
        label = "需刷新账户"
        reason = "券商资金快照已过期或不可读；系统不会使用旧余额判断可运行模式。"
    elif full_ready and evidence_ready:
        mode = "full_strategy_review"
        label = "完整策略可复核"
        reason = "当前资产通过冻结策略的整手复制门槛；仍需其余策略、券商与安全门禁全部通过。"
    elif canary_ready:
        mode = "canary_review_only"
        label = "仅可复核 Canary"
        reason = "资金只支持隔离的一手执行链验证，不支持部署或缩减冻结策略。"
    else:
        mode = "observation_only"
        label = "仅观察"
        reason = "当前资金无法按冻结权重形成可信组合；继续研究与模拟，不生成实盘订单。"

    deployment_allowed = mode == "full_strategy_review" and full_ready
    row = {
        "mode": mode,
        "mode_label_cn": label,
        "snapshot_fresh": snapshot_fresh,
        "current_total_asset": current,
        "available_cash": available,
        "strategy_target_positions": target_positions,
        "minimum_diversified_positions": minimum_diversified,
        "nearest_tested_capital": tested_capital,
        "nearest_tested_positions": tested_positions,
        "nearest_tested_cash_ratio": tested_cash_ratio,
        "nearest_tested_return_drag": tested_drag,
        "minimum_full_strategy_equity": minimum_equity,
        "capital_gap": capital_gap,
        "capital_ratio": capital_ratio,
        "canary_ready": canary_ready,
        "full_strategy_ready": full_ready,
        "strategy_deployment_allowed": deployment_allowed,
        "reason_cn": reason,
    }
    return pd.DataFrame([row], columns=CAPITAL_MODE_COLUMNS)


def write_capital_mode_assessment(
    strategy_config: dict[str, Any],
    transition: pd.DataFrame,
    sensitivity: pd.DataFrame,
) -> Path:
    output_dir = project_path(strategy_config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "capital_mode_assessment.csv"
    atomic_to_csv(
        build_capital_mode_assessment(transition, sensitivity, strategy_config),
        path,
        index=False,
        encoding="utf-8-sig",
    )
    return path


def _nearest_tested_level(sensitivity: pd.DataFrame, current: float) -> pd.Series:
    if sensitivity.empty or "initial_cash" not in sensitivity.columns:
        return pd.Series(dtype=object)
    frame = sensitivity.copy()
    frame["initial_cash"] = pd.to_numeric(frame["initial_cash"], errors="coerce")
    frame = frame.dropna(subset=["initial_cash"]).sort_values("initial_cash")
    if frame.empty:
        return pd.Series(dtype=object)
    affordable = frame[frame["initial_cash"] <= current + 1e-6]
    return affordable.iloc[-1] if not affordable.empty else frame.iloc[0]


def _truth(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if pd.notna(result) else default
    except (TypeError, ValueError):
        return default
