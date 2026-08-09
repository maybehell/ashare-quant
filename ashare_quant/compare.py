from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import build_run_manifest
from .candidate import is_candidate_quarantined, quarantined_ensemble_members
from .challengers import comparison_candidate_config_paths, primary_challenger_config_paths
from .config import load_config, project_path
from .execution_reconciliation import COMPARISON_COLUMNS as EXECUTION_COMPARISON_COLUMNS
from .execution_reconciliation import write_execution_model_reconciliation
from .readiness import current_manifest_frames
from .report import read_csv


COMPARE_COLUMNS = [
    "output_dir",
    "strategy_name",
    "total_return",
    "benchmark_total_return",
    "excess_total_return",
    "out_of_sample_excess_return",
    "max_drawdown",
    "information_ratio",
    "bootstrap_probability_excess_positive",
    "bootstrap_lower_95_excess_return",
    "walk_forward_avg_excess_return",
    "walk_forward_positive_ratio",
    "live_readiness_passed",
    "data_current",
    "prices_last_date",
    "generated_at",
    *EXECUTION_COMPARISON_COLUMNS,
]

PROMOTION_COLUMNS = [
    "rank",
    "output_dir",
    "strategy_name",
    "promotion_status",
    "recommendation",
    "anti_overfit_status",
    "data_current",
    "practical_passed",
    "strict_live_passed",
    "candidate_id",
    "forward_status",
    "forward_days",
    "required_forward_days",
    "remaining_forward_days",
    "forward_excess_return",
    "shadow_observed_signals",
    "shadow_required_signals",
    "shadow_avg_excess_return",
    "high_cost_excess_return",
    "total_return",
    "benchmark_total_return",
    "excess_total_return",
    "out_of_sample_excess_return",
    "information_ratio",
    "bootstrap_probability_excess_positive",
    "bootstrap_lower_95_excess_return",
    "max_drawdown",
    *EXECUTION_COMPARISON_COLUMNS,
    "failed_live_checks",
    "generated_at",
]

DECISION_COLUMNS = [
    "decision",
    "can_promote",
    "can_paper_trade",
    "current_strategy_name",
    "candidate_strategy_name",
    "candidate_output_dir",
    "candidate_id",
    "current_candidate_id",
    "current_forward_days",
    "candidate_evidence_is_separate",
    "selection_basis",
    "promotion_status",
    "anti_overfit_status",
    "strict_live_passed",
    "practical_passed",
    "reason",
    "required_action",
    "forward_days",
    "required_forward_days",
    "remaining_forward_days",
    "forward_excess_return",
    "shadow_observed_signals",
    "shadow_required_signals",
    "failed_live_checks",
    "generated_at",
]

_STATUS_PRIORITY = {
    "strict_live_ready": 0,
    "collecting_evidence": 1,
    "paper_ready": 2,
    "research_only": 3,
    "forward_underperforming": 4,
    "stale_evidence": 5,
}


def compare_backtest_outputs(config: dict[str, Any], output_dirs: list[str] | None = None) -> Path:
    base_output = project_path(config["paths"]["output_dir"])
    dirs = [project_path(path) for path in output_dirs] if output_dirs else discover_output_dirs(base_output)
    for candidate_path in comparison_candidate_config_paths(config):
        try:
            candidate_config = load_config(project_path(candidate_path))
            dirs.append(project_path(candidate_config["paths"]["output_dir"]))
        except (KeyError, OSError, ValueError):
            continue
    dirs = list(dict.fromkeys(path.resolve() for path in dirs))
    dirs = [path for path in dirs if (path / "performance_metrics.csv").exists()]
    fallback_manifest = key_values(read_csv(base_output / "run_manifest.csv"), "item", "value")
    current_manifest = current_data_manifest(config, fallback_manifest)
    rows = [summary_row(path, current_manifest=current_manifest) for path in dirs]
    basic_columns = [column for column in COMPARE_COLUMNS if column not in EXECUTION_COMPARISON_COLUMNS]
    result = pd.DataFrame(rows, columns=basic_columns)
    if not result.empty:
        result = result.sort_values(
            ["data_current", "out_of_sample_excess_return", "bootstrap_probability_excess_positive", "bootstrap_lower_95_excess_return", "excess_total_return", "total_return"],
            ascending=[False, False, False, False, False, False],
            na_position="last",
        ).reset_index(drop=True)
    base_output.mkdir(parents=True, exist_ok=True)
    result, _, _ = write_execution_model_reconciliation(config, result)
    result = result.reindex(columns=COMPARE_COLUMNS)
    path = base_output / "strategy_backtest_compare.csv"
    result.to_csv(path, index=False, encoding="utf-8-sig")

    promotion = strategy_promotion_report(result, config=config)
    promotion.to_csv(base_output / "strategy_promotion.csv", index=False, encoding="utf-8-sig")
    write_strategy_promotion_decision(config, promotion=promotion)
    return path


def current_data_manifest(
    config: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    try:
        manifest = build_run_manifest(config, **current_manifest_frames(config))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return fallback
    current = key_values(manifest, "item", "value")
    return current or fallback


def write_strategy_promotion_decision(config: dict[str, Any], promotion: pd.DataFrame | None = None) -> Path:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    if promotion is None:
        promotion = read_csv(output_dir / "strategy_promotion.csv")
    decision = strategy_promotion_decision(config, promotion)
    path = output_dir / "strategy_promotion_decision.csv"
    decision.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def strategy_promotion_decision(config: dict[str, Any], promotion: pd.DataFrame) -> pd.DataFrame:
    current_strategy = str(config.get("strategy", {}).get("name", ""))
    if promotion.empty:
        row = {
            "decision": "missing_evidence",
            "can_promote": False,
            "can_paper_trade": False,
            "current_strategy_name": current_strategy,
            "candidate_strategy_name": "",
            "candidate_output_dir": "",
            "candidate_id": "",
            "current_candidate_id": "",
            "current_forward_days": "",
            "candidate_evidence_is_separate": False,
            "selection_basis": "missing_evidence",
            "promotion_status": "missing",
            "anti_overfit_status": "missing",
            "strict_live_passed": False,
            "practical_passed": False,
            "reason": "缺少 strategy_promotion.csv 或文件为空，当前没有候选策略可以晋级。",
            "required_action": "python -m ashare_quant observe-candidates --quiet",
            "forward_days": "",
            "required_forward_days": "",
            "remaining_forward_days": "",
            "forward_excess_return": "",
            "shadow_observed_signals": "",
            "shadow_required_signals": "",
            "failed_live_checks": "strategy_promotion_missing",
            "generated_at": "",
        }
        return pd.DataFrame([row], columns=DECISION_COLUMNS)

    ordered = promotion.copy()
    if "rank" in ordered.columns:
        ordered = ordered.sort_values("rank")
    selection_basis = "data_current;promotion_status;bootstrap;historical_excess"
    top = ordered.iloc[0].to_dict()
    selected_config: dict[str, Any] | None = None
    configured_primaries = primary_challenger_config_paths(config)
    if configured_primaries:
        configs_by_output: dict[Path, dict[str, Any]] = {}
        try:
            for configured_primary in configured_primaries:
                primary_config = load_config(project_path(configured_primary))
                primary_output = project_path(primary_config["paths"]["output_dir"]).resolve()
                if primary_output in configs_by_output:
                    raise ValueError("duplicate primary challenger output_dir")
                configs_by_output[primary_output] = primary_config
        except (KeyError, OSError, ValueError):
            configs_by_output = {}
        output_series = ordered.get("output_dir", pd.Series(dtype=object)).map(_resolved_path)
        registered_matches = ordered[output_series.isin(configs_by_output)]
        complete = bool(configs_by_output) and len(registered_matches) == len(configured_primaries)
        if complete:
            matched_outputs = registered_matches.get("output_dir", pd.Series(dtype=object)).map(_resolved_path)
            complete = matched_outputs.nunique() == len(configured_primaries)
        if not complete:
            return missing_primary_challenger_decision(
                current_strategy,
                ",".join(configured_primaries),
                ordered,
            )
        if "rank" in registered_matches.columns:
            registered_matches = registered_matches.sort_values("rank")
        top = registered_matches.iloc[0].to_dict()
        selected_config = configs_by_output.get(_resolved_path(top.get("output_dir", "")))
        selection_basis = (
            "configured_primary_challenger"
            if len(configured_primaries) == 1
            else "configured_primary_challenger_set"
        )
    current_matches = ordered[ordered.get("strategy_name", pd.Series(dtype=object)).astype(str) == current_strategy]
    current = current_matches.iloc[0].to_dict() if not current_matches.empty else {}
    status = str(top.get("promotion_status", ""))
    strict_passed = bool_value(top.get("strict_live_passed"))
    practical_passed = bool_value(top.get("practical_passed"))
    anti_overfit = str(top.get("anti_overfit_status", ""))
    candidate_name = str(top.get("strategy_name", ""))
    failed = str(top.get("failed_live_checks", ""))

    decision, reason, action = promotion_decision_text(
        status=status,
        strict_passed=strict_passed,
        practical_passed=practical_passed,
        anti_overfit=anti_overfit,
        failed_live_checks=failed,
        candidate_name=candidate_name,
        current_strategy=current_strategy,
    )
    quarantined_dependencies: list[str] = []
    if selected_config is not None:
        if is_candidate_quarantined(selected_config):
            quarantined_dependencies.append(candidate_name or "candidate")
        quarantined_dependencies.extend(quarantined_ensemble_members(selected_config))
    if quarantined_dependencies:
        unique_dependencies = list(dict.fromkeys(quarantined_dependencies))
        decision = "do_not_promote_quarantined_dependency"
        reason = "候选策略本身或其组合成员已有前向失败隔离记录，禁止晋级：" + ", ".join(unique_dependencies) + "。"
        action = "移除隔离成员并形成新的策略行为指纹，再从零开始冻结和前向验证。"
        failed = ";".join(filter(None, [failed, "quarantined_candidate_dependency"]))
    can_promote = decision == "allow_manual_live_review"
    can_paper_trade = decision in {"allow_manual_live_review", "paper_trade_only"} and practical_passed
    candidate_id = str(top.get("candidate_id", "") or "")
    current_candidate_id = str(current.get("candidate_id", "") or "")
    evidence_is_separate = bool(
        candidate_name != current_strategy
        and candidate_id
        and current_candidate_id
        and candidate_id != current_candidate_id
    )
    current_forward_days = current.get("forward_days", "")
    if evidence_is_separate:
        reason += f" 当前策略候选 {current_candidate_id} 已有 {current_forward_days or 0} 个前向交易日，其证据继续独立保留；新候选不得继承或重置这些样本。"
    row = {
        "decision": decision,
        "can_promote": can_promote,
        "can_paper_trade": can_paper_trade,
        "current_strategy_name": current_strategy,
        "candidate_strategy_name": candidate_name,
        "candidate_output_dir": top.get("output_dir", ""),
        "candidate_id": candidate_id,
        "current_candidate_id": current_candidate_id,
        "current_forward_days": current_forward_days,
        "candidate_evidence_is_separate": evidence_is_separate,
        "selection_basis": selection_basis,
        "promotion_status": status,
        "anti_overfit_status": anti_overfit,
        "strict_live_passed": strict_passed,
        "practical_passed": practical_passed,
        "reason": reason,
        "required_action": action,
        "forward_days": top.get("forward_days", ""),
        "required_forward_days": top.get("required_forward_days", ""),
        "remaining_forward_days": top.get("remaining_forward_days", ""),
        "forward_excess_return": top.get("forward_excess_return", ""),
        "shadow_observed_signals": top.get("shadow_observed_signals", ""),
        "shadow_required_signals": top.get("shadow_required_signals", ""),
        "failed_live_checks": failed,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return pd.DataFrame([row], columns=DECISION_COLUMNS)


def missing_primary_challenger_decision(
    current_strategy: str,
    configured_primary: str,
    ordered: pd.DataFrame,
) -> pd.DataFrame:
    current_matches = ordered[ordered.get("strategy_name", pd.Series(dtype=object)).astype(str) == current_strategy]
    current = current_matches.iloc[0].to_dict() if not current_matches.empty else {}
    row = {
        "decision": "missing_primary_challenger",
        "can_promote": False,
        "can_paper_trade": False,
        "current_strategy_name": current_strategy,
        "candidate_strategy_name": "",
        "candidate_output_dir": "",
        "candidate_id": "",
        "current_candidate_id": str(current.get("candidate_id", "") or ""),
        "current_forward_days": current.get("forward_days", ""),
        "candidate_evidence_is_separate": False,
        "selection_basis": "configured_primary_challenger_missing",
        "promotion_status": "missing",
        "anti_overfit_status": "missing",
        "strict_live_passed": False,
        "practical_passed": False,
        "reason": f"配置的主挑战者 {configured_primary} 未在当前候选证据中唯一出现；禁止用其他实验替代。",
        "required_action": "python -m ashare_quant observe-candidates --quiet",
        "forward_days": "",
        "required_forward_days": "",
        "remaining_forward_days": "",
        "forward_excess_return": "",
        "shadow_observed_signals": "",
        "shadow_required_signals": "",
        "failed_live_checks": "configured_primary_challenger_missing_or_ambiguous",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return pd.DataFrame([row], columns=DECISION_COLUMNS)


def _resolved_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return project_path(text).resolve()
    except OSError:
        return None


def promotion_decision_text(
    status: str,
    strict_passed: bool,
    practical_passed: bool,
    anti_overfit: str,
    failed_live_checks: str,
    candidate_name: str,
    current_strategy: str,
) -> tuple[str, str, str]:
    same_strategy = candidate_name == current_strategy
    if strict_passed and anti_overfit == "cleared_by_forward_validation":
        return (
            "allow_manual_live_review",
            "候选策略已通过严格实盘证据门禁；真实执行前仍必须人工复核账户、订单和执行环境。",
            "python -m ashare_quant execution-ready",
        )
    if status == "forward_underperforming":
        return (
            "do_not_promote_forward_underperforming",
            "冻结候选在前向验证中跑输基准；继续观察，禁止晋级。",
            "python -m ashare_quant observe-candidates --quiet",
        )
    if status == "collecting_evidence":
        return (
            "observe_only",
            "候选策略回测表现较好，但前向验证和 Shadow 证据不足；为防止过拟合，禁止晋级。",
            "python -m ashare_quant observe-candidates --quiet",
        )
    if status == "paper_ready" and practical_passed:
        return (
            "paper_trade_only",
            "候选策略已具备实用级证据，但严格实盘门禁尚未完成；仅允许模拟盘，禁止实盘晋级。",
            "python -m ashare_quant practical",
        )
    if same_strategy:
        return (
            "keep_current_strategy",
            "当前策略继续保持；所有严格证据门禁通过前，禁止实盘晋级。",
            "python -m ashare_quant observe-candidates --quiet",
        )
    return (
        "research_only",
        f"候选策略暂不具备模拟盘或实盘晋级资格。阻断项：{failed_live_checks or '缺少严格证据'}。",
        "python -m ashare_quant observe-candidates --quiet",
    )


def discover_output_dirs(base_output: Path) -> list[Path]:
    parent = base_output.parent
    candidates = [base_output]
    candidates.extend(
        path
        for path in parent.glob(f"{base_output.name}_*")
        if path.is_dir() and _is_candidate_output_dir(base_output, path)
    )
    unique: list[Path] = []
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _is_candidate_output_dir(base_output: Path, path: Path) -> bool:
    suffix = path.name[len(base_output.name) :].lower()
    if not suffix:
        return False
    if "_stress_" in suffix:
        return False
    return not suffix.startswith(("_live", "_execution_drill", "_browser_checks"))


def strategy_promotion_report(
    comparison: pd.DataFrame,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if comparison.empty or "output_dir" not in comparison:
        return pd.DataFrame(columns=PROMOTION_COLUMNS)
    rows = []
    for idx, row in comparison.reset_index(drop=True).iterrows():
        rows.append(promotion_row(Path(str(row["output_dir"])), int(idx) + 1, row.to_dict(), config=config))
    result = pd.DataFrame(rows, columns=PROMOTION_COLUMNS)
    if not result.empty:
        result["_priority"] = result["promotion_status"].map(_STATUS_PRIORITY).fillna(99)
        result = result.sort_values(
            ["data_current", "_priority", "out_of_sample_excess_return", "bootstrap_probability_excess_positive", "bootstrap_lower_95_excess_return", "excess_total_return", "information_ratio"],
            ascending=[False, True, False, False, False, False, False],
            na_position="last",
        ).drop(columns=["_priority"]).reset_index(drop=True)
        result["rank"] = range(1, len(result) + 1)
    return result


def promotion_row(
    output_dir: Path,
    rank: int,
    summary: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    practical = read_csv(output_dir / "practical_readiness.csv")
    live = read_csv(output_dir / "live_readiness.csv")
    forward = read_csv(output_dir / "live_candidate_forward_progress.csv")
    stability = read_csv(output_dir / "strategy_candidate_stability.csv")
    cost = read_csv(output_dir / "cost_stress.csv")
    manifest = read_csv(output_dir / "run_manifest.csv")
    manifest_values = key_values(manifest, "item", "value")

    data_current = bool_value(summary.get("data_current"))
    practical_passed = all_checks_passed(practical) and data_current
    strict_passed = all_checks_passed(live) and data_current
    stability_passed = all_checks_passed(stability)
    failed_live = failed_checks(live)
    reconciliation_required = bool(
        (config or {}).get("validation", {}).get("require_execution_model_reconciliation", False)
    )
    reconciliation_passed = bool_value(summary.get("execution_model_reconciled"))
    if reconciliation_required and not reconciliation_passed:
        practical_passed = False
        strict_passed = False
        failed_live = [*failed_live, "execution_model_reconciliation"]
    forward_row = forward.iloc[0].to_dict() if not forward.empty else {}
    live_values = check_values(live)
    cost_row = high_cost_row(cost)

    forward_excess = number(forward_row.get("forward_excess_return"))
    forward_days = int_number(forward_row.get("observed_forward_trading_days"))
    required_days = int_number(forward_row.get("required_forward_trading_days"))
    remaining_days = int_number(forward_row.get("remaining_forward_trading_days"))
    shadow_observed = int_number(live_values.get("shadow_observed_signal_count", {}).get("value"))
    shadow_required = required_shadow_count(live_values.get("shadow_observed_signal_count", {}).get("threshold"))
    shadow_avg = number(live_values.get("shadow_avg_excess_return", {}).get("value"))
    high_cost_excess = number(cost_row.get("excess_total_return")) if cost_row else None
    optimized = str(forward_row.get("optimized_on_current_cache_at_freeze", "")).strip().lower() in {"true", "1", "yes"}

    if not data_current:
        failed_live = [*failed_live, "research_source_data_current"]
    status = promotion_status(
        practical_passed,
        strict_passed,
        stability_passed,
        forward_excess,
        failed_live,
        forward_status=str(forward_row.get("status", "") or ""),
        data_current=data_current,
    )
    return {
        "rank": rank,
        "output_dir": str(output_dir),
        "strategy_name": summary.get("strategy_name") or manifest_values.get("strategy_name", output_dir.name),
        "promotion_status": status,
        "recommendation": recommendation_for(status),
        "anti_overfit_status": anti_overfit_status(strict_passed, optimized),
        "data_current": data_current,
        "practical_passed": practical_passed,
        "strict_live_passed": strict_passed,
        "candidate_id": forward_row.get("candidate_id", ""),
        "forward_status": forward_row.get("status", ""),
        "forward_days": forward_days,
        "required_forward_days": required_days,
        "remaining_forward_days": remaining_days,
        "forward_excess_return": forward_excess,
        "shadow_observed_signals": shadow_observed,
        "shadow_required_signals": shadow_required,
        "shadow_avg_excess_return": shadow_avg,
        "high_cost_excess_return": high_cost_excess,
        "total_return": number(summary.get("total_return")),
        "benchmark_total_return": number(summary.get("benchmark_total_return")),
        "excess_total_return": number(summary.get("excess_total_return")),
        "out_of_sample_excess_return": number(summary.get("out_of_sample_excess_return")),
        "information_ratio": number(summary.get("information_ratio")),
        "bootstrap_probability_excess_positive": number(summary.get("bootstrap_probability_excess_positive")),
        "bootstrap_lower_95_excess_return": number(summary.get("bootstrap_lower_95_excess_return")),
        "max_drawdown": number(summary.get("max_drawdown")),
        **{column: summary.get(column, "") for column in EXECUTION_COMPARISON_COLUMNS},
        "failed_live_checks": ";".join(failed_live),
        "generated_at": summary.get("generated_at") or manifest_values.get("generated_at", ""),
    }


def promotion_status(
    practical_passed: bool,
    strict_passed: bool,
    stability_passed: bool,
    forward_excess: float | None,
    failed_live: list[str],
    *,
    forward_status: str = "",
    data_current: bool = True,
) -> str:
    if not data_current:
        return "stale_evidence"
    if strict_passed:
        return "strict_live_ready"
    if forward_status in {"early_forward_degradation", "failed_forward_excess", "failed_forward_risk"}:
        return "forward_underperforming"
    if "candidate_early_forward_degradation" in failed_live:
        return "forward_underperforming"
    if not practical_passed or not stability_passed:
        return "research_only"
    if "candidate_forward_trading_days" in failed_live or any(
        check in failed_live for check in {"shadow_observed_signal_count", "shadow_distinct_cohort_count"}
    ):
        return "collecting_evidence"
    return "paper_ready"


def recommendation_for(status: str) -> str:
    return {
        "strict_live_ready": "manual_live_gate_review",
        "collecting_evidence": "continue_forward_and_shadow_observation",
        "paper_ready": "paper_trade_only_until_strict_gate_passes",
        "forward_underperforming": "keep_observing_do_not_promote",
        "research_only": "fix_evidence_before_paper_or_live",
        "stale_evidence": "rerun_on_current_market_data",
    }.get(status, "review_required")


def anti_overfit_status(strict_passed: bool, optimized_on_current_cache: bool) -> str:
    if strict_passed:
        return "cleared_by_forward_validation"
    if optimized_on_current_cache:
        return "not_cleared"
    return "not_marked_as_optimized"


def summary_row(output_dir: Path, current_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = read_csv(output_dir / "performance_metrics.csv")
    metric_values = dict(zip(metrics["metric"], metrics["value"])) if not metrics.empty else {}
    manifest = read_csv(output_dir / "run_manifest.csv")
    manifest_values = dict(zip(manifest["item"], manifest["value"])) if not manifest.empty else {}
    walk = read_csv(output_dir / "walk_forward_summary.csv")
    readiness = read_csv(output_dir / "live_readiness.csv")
    bootstrap = read_csv(output_dir / "excess_bootstrap.csv")
    robustness = read_csv(output_dir / "robustness_split.csv")
    readiness_passed = "" if readiness.empty else all_checks_passed(readiness)
    walk_row = walk.iloc[0].to_dict() if not walk.empty else {}
    bootstrap_row = bootstrap.iloc[0].to_dict() if not bootstrap.empty else {}
    out_of_sample = (
        robustness[robustness["sample"].astype(str) == "out_of_sample_last_30pct"]
        if "sample" in robustness
        else pd.DataFrame()
    )
    out_of_sample_row = out_of_sample.iloc[-1].to_dict() if not out_of_sample.empty else {}
    current = current_manifest or manifest_values
    fingerprint_keys = ("stocks_sha256", "prices_sha256", "fundamentals_sha256", "benchmark_sha256", "prices_last_date", "benchmark_last_date")
    data_current = bool(current) and all(
        str(manifest_values.get(key, "")) != ""
        and str(manifest_values.get(key, "")) == str(current.get(key, ""))
        for key in fingerprint_keys
    )
    return {
        "output_dir": str(output_dir),
        "strategy_name": manifest_values.get("strategy_name", output_dir.name),
        "total_return": number(metric_values.get("total_return")),
        "benchmark_total_return": number(metric_values.get("benchmark_total_return")),
        "excess_total_return": number(metric_values.get("excess_total_return")),
        "out_of_sample_excess_return": number(out_of_sample_row.get("excess_return")),
        "max_drawdown": number(metric_values.get("max_drawdown")),
        "information_ratio": number(metric_values.get("information_ratio")),
        "bootstrap_probability_excess_positive": number(bootstrap_row.get("probability_excess_positive")),
        "bootstrap_lower_95_excess_return": number(bootstrap_row.get("lower_95_excess_return")),
        "walk_forward_avg_excess_return": number(walk_row.get("avg_test_excess_return")),
        "walk_forward_positive_ratio": number(walk_row.get("positive_test_ratio")),
        "live_readiness_passed": readiness_passed,
        "data_current": data_current,
        "prices_last_date": manifest_values.get("prices_last_date", ""),
        "generated_at": manifest_values.get("generated_at", ""),
    }


def all_checks_passed(frame: pd.DataFrame) -> bool:
    if frame.empty or "passed" not in frame:
        return False
    return bool(frame["passed"].map(bool_value).all())


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def failed_checks(frame: pd.DataFrame) -> list[str]:
    if frame.empty or not {"check", "passed"}.issubset(frame.columns):
        return []
    failed = frame[~frame["passed"].map(bool_value)]
    return failed["check"].astype(str).tolist()


def check_values(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "check" not in frame:
        return {}
    return {str(row["check"]): row.to_dict() for _, row in frame.iterrows()}


def key_values(frame: pd.DataFrame, key: str, value: str) -> dict[str, Any]:
    if frame.empty or not {key, value}.issubset(frame.columns):
        return {}
    return {str(row[key]): row[value] for _, row in frame.iterrows()}


def high_cost_row(cost: pd.DataFrame) -> dict[str, Any]:
    if cost.empty:
        return {}
    if "scenario" in cost:
        selected = cost[cost["scenario"].astype(str) == "high_cost"]
        if not selected.empty:
            return selected.iloc[0].to_dict()
    return cost.iloc[-1].to_dict()


def required_shadow_count(value: Any) -> int | None:
    text = str(value or "")
    token = text.strip().split(" ", 1)[0]
    return int_number(token)


def int_number(value: Any) -> int | None:
    parsed = number(value)
    if parsed is None:
        return None
    return int(parsed)


def number(value: Any) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return float(parsed)
