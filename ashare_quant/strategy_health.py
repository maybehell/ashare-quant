from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .config import project_path
from .io_utils import atomic_to_csv
from .readiness import shadow_observation_values

STRATEGY_HEALTH_COLUMNS = ["check", "passed", "value", "threshold", "severity", "action", "note"]


def configured_promotion_decision(config: dict[str, Any], output_dir: Path | None = None) -> pd.DataFrame:
    validation = config.get("validation", {})
    configured = validation.get("promotion_decision_path", "") if isinstance(validation, dict) else ""
    if configured:
        return read_csv(project_path(str(configured)))
    directory = output_dir or project_path(config["paths"]["output_dir"])
    return read_csv(directory / "strategy_promotion_decision.csv")


def build_strategy_health(
    config: dict[str, Any],
    strategy_reliability: pd.DataFrame | None = None,
    rolling_validation: pd.DataFrame | None = None,
    forward_progress: pd.DataFrame | None = None,
    shadow_forward_audit: pd.DataFrame | None = None,
    promotion_decision: pd.DataFrame | None = None,
    practical_readiness: pd.DataFrame | None = None,
    live_readiness: pd.DataFrame | None = None,
    factor_robustness_summary: pd.DataFrame | None = None,
    forward_factor_monitor: pd.DataFrame | None = None,
) -> pd.DataFrame:
    cfg = config.get("strategy_health", {})
    rows: list[dict[str, object]] = []
    reliability = pd.DataFrame() if strategy_reliability is None else strategy_reliability
    rolling = pd.DataFrame() if rolling_validation is None else rolling_validation
    forward = pd.DataFrame() if forward_progress is None else forward_progress
    shadow = pd.DataFrame() if shadow_forward_audit is None else shadow_forward_audit
    promotion = pd.DataFrame() if promotion_decision is None else promotion_decision
    practical = pd.DataFrame() if practical_readiness is None else practical_readiness
    live = pd.DataFrame() if live_readiness is None else live_readiness
    factor_summary = pd.DataFrame() if factor_robustness_summary is None else factor_robustness_summary
    factor_monitor = pd.DataFrame() if forward_factor_monitor is None else forward_factor_monitor

    def add(check: str, passed: bool, value: object, threshold: object, severity: str, action: str, note: str) -> None:
        rows.append(
            {
                "check": check,
                "passed": bool(passed),
                "value": value,
                "threshold": threshold,
                "severity": "ok" if passed else severity,
                "action": "" if passed else action,
                "note": note,
            }
        )

    min_score = float(cfg.get("min_reliability_score", 70.0))
    score = item_float(reliability, "reliability_score")
    add(
        "reliability_score",
        score is not None and score >= min_score,
        "missing" if score is None else round(score, 4),
        f">= {min_score}",
        "paper_blocker",
        "重新运行 backtest/report；若仍低于门槛，暂停 paper/RPA 并回到研究。",
        "综合回测长度、样本内外、走步、滚动窗口和调参污染标记。",
    )

    min_latest_rolling = float(cfg.get("min_latest_rolling_excess_return", -0.05))
    latest_rolling = latest_numeric(rolling, "excess_return")
    add(
        "latest_rolling_excess_return",
        latest_rolling is not None and latest_rolling >= min_latest_rolling,
        "missing" if latest_rolling is None else round(latest_rolling, 6),
        f">= {min_latest_rolling}",
        "paper_blocker",
        "最近滚动窗口跑输过多；暂停生成可执行工单，先观察或重新研究。",
        "用最近一个滚动窗口检查策略是否已经明显衰退。",
    )

    rolling_windows = int(cfg.get("recent_rolling_windows", 6))
    min_recent_positive = float(cfg.get("min_recent_rolling_positive_ratio", 0.5))
    recent_ratio = recent_positive_ratio(rolling, "excess_return", rolling_windows)
    add(
        "recent_rolling_positive_ratio",
        recent_ratio is not None and recent_ratio >= min_recent_positive,
        "missing" if recent_ratio is None else round(recent_ratio, 4),
        f">= {min_recent_positive} over last {rolling_windows}",
        "paper_blocker",
        "最近滚动超额持续性不足；暂停 paper/RPA，等待更多样本或重新研究。",
        "防止只因全区间表现好而忽视近期持续跑输。",
    )

    practical_passed, practical_value = all_checks_passed(practical)
    add(
        "practical_readiness_passed",
        practical_passed,
        practical_value,
        "all practical checks passed",
        "paper_blocker",
        "先处理 practical_readiness.csv 中的失败项。",
        "模拟盘和人工工单至少需要实用门禁通过。",
    )

    current_strategy = str(config.get("strategy", {}).get("name", "") or "")
    evidence_strategy = first_text(promotion, "current_strategy_name")
    current_candidate_id = first_text(promotion, "current_candidate_id")
    promotion_candidate_strategy = first_text(promotion, "candidate_strategy_name")
    promotion_candidate_id = first_text(promotion, "candidate_id")
    if promotion_candidate_strategy == current_strategy:
        evidence_strategy = promotion_candidate_strategy
        current_candidate_id = promotion_candidate_id
    forward_candidate_id = first_text(forward, "candidate_id")
    candidate_exists_ok, candidate_exists_value = check_passed_value(live, "candidate_exists")
    candidate_hash_ok, _ = check_passed_value(live, "candidate_hash_match")
    isolated_candidate_verified = bool(
        promotion.empty
        and current_strategy
        and forward_candidate_id
        and candidate_exists_ok
        and candidate_exists_value == forward_candidate_id
        and candidate_hash_ok
    )
    if isolated_candidate_verified:
        evidence_strategy = current_strategy
        current_candidate_id = forward_candidate_id
    forward_matches_current = bool(
        current_strategy
        and evidence_strategy == current_strategy
        and current_candidate_id
        and forward_candidate_id == current_candidate_id
    )
    add(
        "forward_evidence_matches_current_strategy",
        forward_matches_current,
        (
            f"strategy={evidence_strategy or 'missing'}; "
            f"candidate={forward_candidate_id or 'missing'}; expected={current_candidate_id or 'missing'}"
        ),
        "strategy and candidate_id must match current strategy evidence",
        "live_blocker",
        "重新生成候选比较和策略健康报告；不得把其他候选的前向样本计入当前策略。",
        "前向证据必须同时属于当前策略名称和当前冻结候选 ID。",
    )
    current_forward = forward if forward_matches_current else pd.DataFrame()
    forward_days = first_numeric(current_forward, "observed_forward_trading_days")
    required_forward = first_numeric(current_forward, "required_forward_trading_days")
    forward_excess = first_numeric(current_forward, "forward_excess_return")
    required_forward_excess = first_numeric(current_forward, "required_forward_excess_return")
    if required_forward is None:
        required_forward = float(config.get("live_guard", {}).get("min_forward_trading_days", 60))
    if required_forward_excess is None:
        required_forward_excess = float(config.get("live_guard", {}).get("min_forward_excess_return", 0.0))
    forward_complete = forward_days is not None and forward_days >= required_forward
    forward_confirmed_ok = not forward_complete or (forward_excess is not None and forward_excess >= required_forward_excess)
    forward_status = first_text(current_forward, "status")
    forward_confirmed_ok = forward_confirmed_ok and forward_status not in {
        "failed_forward_excess", "failed_forward_risk"
    }
    early_forward_ok = forward_status != "early_forward_degradation"
    add(
        "forward_no_early_degradation",
        early_forward_ok,
        forward_status or "missing",
        "not early_forward_degradation",
        "paper_blocker",
        "候选触发早期前向退化风险线；暂停 paper/RPA，不得通过延长等待来忽略已发生的严重跑输。",
        "早期退化门禁只能收紧权限，不用于候选排名或调参。",
    )
    early_warning_clear = forward_status != "early_forward_warning"
    add(
        "forward_early_warning_clear",
        early_warning_clear,
        "missing" if forward_excess is None else round(forward_excess, 6),
        "status != early_forward_warning",
        "warning",
        "保持参数冻结并继续观察；预警不用于追逐近期行情重新调参。",
        "预警带位于早期硬停线之前，只提高可见性，不单独暂停模拟盘。",
    )
    add(
        "forward_evidence_complete",
        forward_complete,
        "missing" if forward_days is None else int(forward_days),
        f">= {int(required_forward)} trading days",
        "live_blocker",
        "继续 observe-candidates 收集冻结参数后的新增交易日。",
        "前向验证未完成时不应晋级严格实盘。",
    )
    add(
        "forward_no_confirmed_failure",
        forward_confirmed_ok,
        "collecting" if not forward_complete else ("missing" if forward_excess is None else round(forward_excess, 6)),
        f">= {required_forward_excess} after enough days",
        "paper_blocker",
        "冻结参数前向样本已足够且跑输；暂停 paper/RPA 并重新研究。",
        "样本不足时只阻断严格实盘，不阻断模拟盘观察。",
    )

    shadow_has_identity = bool(current_candidate_id and "candidate_id" in shadow.columns)
    current_shadow = (
        shadow[shadow["candidate_id"].fillna("").astype(str) == current_candidate_id].copy()
        if shadow_has_identity
        else pd.DataFrame()
    )
    add(
        "shadow_evidence_matches_current_candidate",
        shadow_has_identity,
        f"candidate={current_candidate_id or 'missing'}; matched_rows={len(current_shadow)}",
        "candidate_id column present and current candidate_id known",
        "live_blocker",
        "重新生成 Shadow 观察记录并保留 candidate_id；历史候选记录不得计入当前策略。",
        "Shadow 样本按当前冻结候选 ID 过滤，避免历史策略样本污染计数。",
    )
    guard = config.get("live_guard", {})
    min_shadow_days = int(guard.get("min_shadow_observed_days_per_signal", 1))
    min_shadow_gap = int(guard.get("min_shadow_distinct_cohort_gap_days", 0))
    shadow_stats = shadow_observation_values(
        current_shadow,
        require_current=False,
        min_observed_days=min_shadow_days,
        min_distinct_gap_days=min_shadow_gap,
    )
    observed_shadow = int(shadow_stats["observed_signal_count"])
    required_shadow = int(guard.get("min_shadow_observed_signals", 20))
    shadow_complete = observed_shadow >= required_shadow
    shadow_excess = shadow_stats["avg_excess_return"]
    min_shadow_excess = float(guard.get("min_shadow_avg_excess_return", 0.0))
    shadow_confirmed_ok = not shadow_complete or (shadow_excess is not None and shadow_excess >= min_shadow_excess)
    add(
        "shadow_evidence_complete",
        shadow_complete,
        observed_shadow,
        f">= {required_shadow} observed signals with >= {min_shadow_days} days",
        "live_blocker",
        "继续运行 shadow/observe，积累可评估信号。",
        "Shadow 观察不足时不应晋级严格实盘。",
    )
    add(
        "shadow_no_confirmed_failure",
        shadow_confirmed_ok,
        "collecting" if not shadow_complete else ("missing" if shadow_excess is None else round(shadow_excess, 6)),
        f">= {min_shadow_excess} after enough signals",
        "paper_blocker",
        "Shadow 样本已足够且平均超额不足；暂停 paper/RPA 并重新研究。",
        "样本不足时只阻断严格实盘，不阻断模拟盘观察。",
    )

    optimized = item_bool(reliability, "optimized_on_current_cache")
    optimized_allowed = bool(config.get("live_guard", {}).get("allow_optimized_on_current_cache", False))
    optimized_redeemed, _ = check_passed_value(live, "optimized_on_current_cache")
    optimized_guard_ok = optimized is False or optimized_allowed or optimized_redeemed
    add(
        "optimized_on_current_cache_guard",
        optimized_guard_ok,
        "candidate_validated" if optimized and optimized_redeemed else ("missing" if optimized is None else optimized),
        "False, explicitly allowed, or redeemed by strict frozen-candidate validation",
        "live_blocker",
        "继续使用冻结参数做前向验证；未解除调参污染前不晋级严格实盘。",
        "防止把参考当前缓存调出来的参数直接当成实盘级证据。",
    )

    factor_required = bool(config.get("live_guard", {}).get("require_factor_robustness", True))
    factor_passed, factor_value = check_passed_value(factor_summary, "factor_robustness_status")
    add(
        "factor_robustness_status",
        (not factor_required) or factor_passed,
        "not_required" if not factor_required else factor_value,
        "passed" if factor_required else "not required",
        "live_blocker",
        "运行 aq factor-robustness，检查当前策略是否过度依赖单一因子。",
        "因子扰动或删因子后表现过度恶化，说明策略可能存在因子集中或参数脆弱性；严格实盘前需要复核。",
    )

    factor_min_days = int(cfg.get("forward_factor_min_trading_days", 20))
    factor_adverse_limit = float(cfg.get("forward_factor_adverse_weight_blocker", 0.67))
    factor_health_ok, factor_health_value = forward_factor_degradation_status(
        factor_monitor,
        current_candidate_id,
        min_trading_days=factor_min_days,
        adverse_weight_limit=factor_adverse_limit,
    )
    add(
        "forward_factor_degradation_clear",
        factor_health_ok,
        factor_health_value,
        f"adverse effective weight <= {factor_adverse_limit:.0%} after >= {factor_min_days} trading days",
        "paper_blocker",
        "冻结后主导权重因子持续逆风；保持参数冻结并暂停生成工单，等待独立样本或重新登记结构性策略。",
        "该门禁只收紧执行权限，不根据短期结果自动调权，避免把前向监控变成追涨杀跌式调参。",
    )
    validation = config.get("validation", {}) if isinstance(config.get("validation", {}), dict) else {}
    paper_promotion_required = bool(validation.get("require_central_promotion_for_paper", False))
    paper_promotion_passed, paper_promotion_value = promotion_allows_paper(
        promotion,
        config,
        forward_candidate_id,
    )
    add(
        "promotion_decision_allows_paper",
        (not paper_promotion_required) or paper_promotion_passed,
        paper_promotion_value if paper_promotion_required else "not_required",
        "can_paper_trade=True and strategy/candidate_id match" if paper_promotion_required else "not required",
        "paper_blocker",
        "中央晋级决策尚未允许该冻结候选进入模拟工单链；继续前向和 Shadow 观察。",
        "候选策略可以继续回测和观察，但不能绕过主系统直接生成 paper/RPA 工单。",
    )

    promotion_passed, promotion_value = promotion_allows_strict(promotion, config)
    add(
        "promotion_decision_allows_strict",
        promotion_passed,
        promotion_value,
        "can_promote=True and candidate=current strategy",
        "live_blocker",
        "继续 observe-candidates，等待晋级决策通过。",
        "严格实盘必须通过候选策略晋级治理。",
    )

    live_passed, live_value = all_checks_passed(live)
    add(
        "strict_live_readiness_passed",
        live_passed,
        live_value,
        "all strict checks passed",
        "live_blocker",
        "继续前向验证、Shadow 观察和严格门禁修复。",
        "严格实盘状态，不影响 paper_only_observe。",
    )

    paper_allowed = not has_failed_severity(rows, "paper_blocker")
    strict_allowed = paper_allowed and not has_failed_severity(rows, "live_blocker")
    if strict_allowed:
        status = "strict_live_allowed"
    elif paper_allowed:
        status = "paper_only_observe"
    else:
        status = "pause_paper_orders"
    add(
        "paper_orders_allowed",
        paper_allowed,
        paper_allowed,
        True,
        "paper_blocker",
        "暂停生成/导出可执行工单，先处理策略健康失败项。",
        "只有无 paper_blocker 时才允许 paper/RPA 工单链路继续。",
    )
    add(
        "strict_live_allowed",
        strict_allowed,
        strict_allowed,
        True,
        "live_blocker",
        "继续收集前向和 Shadow 证据，或等待策略晋级。",
        "严格实盘要求 paper 和 live 两类门禁都通过。",
    )
    add(
        "strategy_health_status",
        paper_allowed,
        status,
        "not pause_paper_orders",
        "paper_blocker",
        "策略健康处于暂停状态；不要导出或填入券商客户端。",
        "运行态结论：strict_live_allowed / paper_only_observe / pause_paper_orders。",
    )
    if strict_allowed:
        deployment_cap = 100.0
        deployment_grade = "\u4e25\u683c\u5b9e\u76d8\u95e8\u7981\u901a\u8fc7"
    elif paper_allowed:
        deployment_cap = float(cfg.get("paper_only_deployment_score_cap", 55.0))
        deployment_grade = "\u89c2\u5bdf\u7ea7"
    else:
        deployment_cap = float(cfg.get("paused_deployment_score_cap", 30.0))
        deployment_grade = "\u6682\u505c\u7ea7"
    historical_score = max(float(score or 0.0), 0.0)
    deployment_score = min(historical_score, max(min(deployment_cap, 100.0), 0.0))
    add(
        "deployment_evidence_grade",
        strict_allowed,
        deployment_grade,
        "\u4e25\u683c\u5b9e\u76d8\u95e8\u7981\u901a\u8fc7",
        "info",
        "",
        "\u5f53\u524d\u90e8\u7f72\u7b49\u7ea7\u7531\u5386\u53f2\u56de\u6d4b\u3001\u51bb\u7ed3\u540e\u524d\u5411\u8bc1\u636e\u3001Shadow \u89c2\u5bdf\u548c\u5b9e\u76d8\u95e8\u7981\u5171\u540c\u51b3\u5b9a\u3002",
    )
    add(
        "deployment_evidence_score",
        strict_allowed,
        round(deployment_score, 1),
        "\u5386\u53f2\u8bc4\u5206\u4e14\u4e0d\u8d85\u8fc7\u5f53\u524d\u8fd0\u884c\u72b6\u6001\u4e0a\u9650",
        "info",
        "",
        "\u8fd9\u662f\u90e8\u7f72\u8bc1\u636e\u5206\uff0c\u4e0d\u662f\u6536\u76ca\u6982\u7387\uff1b\u5de5\u5355\u6682\u505c\u65f6\u4e0a\u9650\u4e3a 30\uff0c\u4ec5\u89c2\u5bdf\u65f6\u4e0a\u9650\u4e3a 55\u3002",
    )
    return pd.DataFrame(rows, columns=STRATEGY_HEALTH_COLUMNS)


def write_strategy_health(config: dict[str, Any]) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = build_strategy_health(
        config,
        read_csv(output_dir / "strategy_reliability.csv"),
        read_csv(output_dir / "rolling_validation.csv"),
        read_csv(output_dir / "live_candidate_forward_progress.csv"),
        read_csv(output_dir / "shadow_forward_audit.csv"),
        configured_promotion_decision(config, output_dir),
        read_csv(output_dir / "practical_readiness.csv"),
        read_csv(output_dir / "live_readiness.csv"),
        read_csv(output_dir / "factor_robustness_summary.csv"),
        read_csv(output_dir / "forward_factor_monitor.csv"),
    )
    path = output_dir / "strategy_health.csv"
    atomic_to_csv(frame, path, index=False, encoding="utf-8-sig")
    return path


def forward_factor_degradation_status(
    frame: pd.DataFrame,
    candidate_id: str,
    *,
    min_trading_days: int,
    adverse_weight_limit: float,
) -> tuple[bool, str]:
    required = {
        "candidate_id",
        "forward_trading_days",
        "configured_weight",
        "member_blend_weight",
        "status",
    }
    if frame.empty or not required.issubset(frame.columns):
        return True, "not_available"
    if not candidate_id:
        return False, "current_candidate_unbound"
    data = frame[frame["candidate_id"].fillna("").astype(str) == str(candidate_id)].copy()
    if data.empty:
        return False, "candidate_identity_mismatch"
    days = pd.to_numeric(data["forward_trading_days"], errors="coerce").dropna()
    observed_days = int(days.max()) if not days.empty else 0
    if observed_days < max(int(min_trading_days), 1):
        return True, f"collecting;days={observed_days}/{max(int(min_trading_days), 1)}"
    data["configured_weight"] = pd.to_numeric(data["configured_weight"], errors="coerce").fillna(0.0).abs()
    data["member_blend_weight"] = pd.to_numeric(data["member_blend_weight"], errors="coerce").fillna(0.0).abs()
    data["effective_weight"] = data["configured_weight"] * data["member_blend_weight"]
    total_weight = float(data["effective_weight"].sum())
    if total_weight <= 0:
        return False, f"invalid_weights;days={observed_days}"
    adverse_weight = float(
        data.loc[data["status"].fillna("").astype(str).str.lower() == "adverse", "effective_weight"].sum()
    ) / total_weight
    passed = adverse_weight <= max(min(float(adverse_weight_limit), 1.0), 0.0)
    return passed, f"days={observed_days};adverse_weight={adverse_weight:.4f}"

def strategy_health_allows_paper(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return False, "missing"
    rows = frame[frame["check"].astype(str) == "paper_orders_allowed"]
    if rows.empty:
        return False, "missing"
    passed = parse_bool_like(rows["passed"].iloc[0])
    return passed, str(rows["value"].iloc[0])


def check_passed_value(frame: pd.DataFrame, check: str) -> tuple[bool, str]:
    if frame.empty or not {"check", "passed", "value"}.issubset(frame.columns):
        return False, "missing"
    rows = frame[frame["check"].astype(str) == check]
    if rows.empty:
        return False, "missing"
    return parse_bool_like(rows["passed"].iloc[0]), str(rows["value"].iloc[0])


def has_failed_severity(rows: list[dict[str, object]], token: str) -> bool:
    for row in rows:
        if not bool(row.get("passed")) and token in str(row.get("severity", "")):
            return True
    return False


def item_float(frame: pd.DataFrame, item: str) -> float | None:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return None
    rows = frame[frame["item"].astype(str) == item]
    if rows.empty:
        return None
    return to_float(rows["value"].iloc[0])


def item_bool(frame: pd.DataFrame, item: str) -> bool | None:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return None
    rows = frame[frame["item"].astype(str) == item]
    if rows.empty:
        return None
    return parse_bool_like(rows["value"].iloc[0])


def latest_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return None if values.empty else float(values.iloc[-1])


def first_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return None if values.empty else float(values.iloc[0])


def first_text(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame:
        return ""
    values = frame[column].dropna().astype(str)
    return "" if values.empty else str(values.iloc[0])


def recent_positive_ratio(frame: pd.DataFrame, column: str, count: int) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna().tail(count)
    if values.empty:
        return None
    return float((values > 0).mean())



def promotion_allows_paper(
    frame: pd.DataFrame,
    config: dict[str, Any],
    forward_candidate_id: str,
) -> tuple[bool, str]:
    if frame.empty:
        return False, "missing"
    row = frame.iloc[0]
    can_paper = parse_bool_like(row.get("can_paper_trade"))
    candidate = str(row.get("candidate_strategy_name", "") or "")
    current = str(config.get("strategy", {}).get("name", "") or "")
    strategy_match = bool(candidate and current and candidate == current)
    decision_candidate_id = str(row.get("candidate_id", "") or "")
    candidate_id_match = bool(
        decision_candidate_id
        and forward_candidate_id
        and decision_candidate_id == forward_candidate_id
    )
    decision = str(row.get("decision", "") or "missing")
    return (
        can_paper and strategy_match and candidate_id_match,
        (
            f"{decision}; can_paper_trade={can_paper}; "
            f"strategy_match={strategy_match}; candidate_id_match={candidate_id_match}"
        ),
    )


def promotion_allows_strict(frame: pd.DataFrame, config: dict[str, Any]) -> tuple[bool, str]:
    if frame.empty:
        return False, "missing"
    row = frame.iloc[0]
    can_promote = parse_bool_like(row.get("can_promote"))
    candidate = str(row.get("candidate_strategy_name", "") or "")
    current = str(config.get("strategy", {}).get("name", "") or row.get("current_strategy_name", "") or "")
    match = bool(candidate and current and candidate == current)
    decision = str(row.get("decision", "") or "missing")
    return can_promote and match, f"{decision}; can_promote={can_promote}; strategy_match={match}"


def all_checks_passed(frame: pd.DataFrame) -> tuple[bool, str]:
    if frame.empty or "passed" not in frame:
        return False, "missing"
    passed = frame["passed"].map(parse_bool_like)
    return bool(len(frame) > 0 and passed.all()), f"{int(passed.sum())}/{len(frame)}"


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def to_float(value: object) -> float | None:
    try:
        out = pd.to_numeric(value, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(out):
        return None
    return float(out)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()
