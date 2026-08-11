from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .audit import backtest_config_sha256, capital_sensitivity_config_sha256, effective_stocks_for_prices, frame_sha256, mapping_sha256
from .candidate import candidate_evidence_identity_sha256, build_candidate_validation, live_strategy_hash, load_candidate_manifest
from .config import project_path
from .data import DataStore, scope_market_frames_to_committed_fetch
from .industry_risk import industry_risk_ready
from .validation import filter_complete_market_data


READINESS_COLUMNS = ["check", "passed", "value", "threshold", "message"]
READINESS_ROADMAP_COLUMNS = ["priority", "area", "check", "status", "current", "required", "remaining", "next_action", "command"]


def build_live_readiness(
    config: dict[str, Any],
    metrics: dict[str, float],
    data_quality: pd.DataFrame,
    robustness: pd.DataFrame,
    walk_forward_summary: pd.DataFrame,
    candidate_validation: pd.DataFrame | None = None,
    shadow_forward_audit: pd.DataFrame | None = None,
    cost_stress: pd.DataFrame | None = None,
    run_manifest: pd.DataFrame | None = None,
    candidate_stability: pd.DataFrame | None = None,
    excess_bootstrap: pd.DataFrame | None = None,
    industry_risk_audit: pd.DataFrame | None = None,
    factor_robustness_summary: pd.DataFrame | None = None,
) -> pd.DataFrame:
    guard = config.get("live_guard", {})
    if not guard.get("enabled", True):
        return pd.DataFrame(
            [{"check": "live_guard", "passed": True, "value": "disabled", "threshold": "", "message": "实盘门禁已关闭。"}],
            columns=READINESS_COLUMNS,
        )

    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    total_excess = float(metrics.get("excess_total_return", 0.0))
    min_total = float(guard.get("min_total_excess_return", 0.0))
    add("total_excess_return", total_excess >= min_total, total_excess, min_total, "全区间超额收益必须达到门槛。")

    if "min_information_ratio" in guard:
        information_ratio = optional_float(metrics.get("information_ratio"))
        min_information_ratio = float(guard["min_information_ratio"])
        add(
            "information_ratio",
            information_ratio is not None and information_ratio >= min_information_ratio,
            "" if information_ratio is None else information_ratio,
            min_information_ratio,
            "相对基准的风险调整后主动收益必须达到严格实盘门槛。",
        )
    if "max_tracking_error" in guard:
        tracking_error = optional_float(metrics.get("tracking_error"))
        max_tracking_error = float(guard["max_tracking_error"])
        add(
            "tracking_error",
            tracking_error is not None and tracking_error <= max_tracking_error,
            "" if tracking_error is None else tracking_error,
            max_tracking_error,
            "年化跟踪误差必须受控，避免累计超额来自不可控的基准偏离。",
        )

    if bool(guard.get("require_excess_bootstrap", False)) or excess_bootstrap is not None:
        bootstrap_status = bootstrap_text(excess_bootstrap, "status")
        bootstrap_probability = bootstrap_value(excess_bootstrap, "probability_excess_positive")
        bootstrap_lower = bootstrap_value(excess_bootstrap, "lower_95_excess_return")
        min_probability = float(guard.get("min_bootstrap_probability_positive", 0.95))
        min_lower = float(guard.get("min_bootstrap_lower_95_excess_return", 0.0))
        add(
            "excess_bootstrap_status",
            bootstrap_status == "ok",
            bootstrap_status or "missing",
            "ok",
            "超额收益必须完成保留时间相关性的区块 Bootstrap 检验。",
        )
        add(
            "excess_bootstrap_probability_positive",
            bootstrap_probability is not None and bootstrap_probability >= min_probability,
            "" if bootstrap_probability is None else bootstrap_probability,
            min_probability,
            "区块 Bootstrap 中超额收益为正的概率必须达到严格实盘门槛。",
        )
        add(
            "excess_bootstrap_lower_95",
            bootstrap_lower is not None and bootstrap_lower >= min_lower,
            "" if bootstrap_lower is None else bootstrap_lower,
            min_lower,
            "超额收益 95% 区间下界必须达到严格实盘门槛，避免只依据高点估计晋级。",
        )

    drawdown = float(metrics.get("max_drawdown", 0.0))
    max_drawdown = float(guard.get("max_strategy_drawdown", 1.0))
    add(
        "max_strategy_drawdown",
        abs(drawdown) <= max_drawdown,
        drawdown,
        -max_drawdown,
        "策略最大回撤必须控制在实盘门槛内。",
    )

    turnover = float(metrics.get("annualized_turnover", 0.0))
    max_turnover = float(guard.get("max_annualized_turnover", 999.0))
    add(
        "annualized_turnover",
        turnover <= max_turnover,
        turnover,
        max_turnover,
        "年化换手率必须控制在可执行范围内，避免成本和冲击过高。",
    )

    out_excess = latest_out_of_sample_excess(robustness)
    min_out = float(guard.get("min_out_of_sample_excess_return", 0.0))
    add(
        "out_of_sample_excess_return",
        out_excess is not None and out_excess >= min_out,
        "" if out_excess is None else out_excess,
        min_out,
        "最近样本外区间超额收益必须达到门槛。",
    )

    in_excess = sample_excess(robustness, "in_sample_first_70pct")
    if bool(guard.get("require_in_sample_excess", False)) and in_excess is not None:
        min_in = float(guard.get("min_in_sample_excess_return", 0.0))
        add(
            "in_sample_excess_return",
            in_excess >= min_in,
            in_excess,
            min_in,
            "前 70% 样本超额收益必须达到门槛，避免策略只在最近行情中有效。",
        )

    wf_avg, wf_ratio = walk_forward_values(walk_forward_summary)
    min_wf = float(guard.get("min_walk_forward_avg_excess_return", 0.0))
    min_ratio = float(guard.get("min_walk_forward_positive_ratio", 0.6))
    wf_folds = walk_forward_fold_count(walk_forward_summary)
    if "min_walk_forward_folds" in guard and wf_folds is not None:
        min_folds = int(guard.get("min_walk_forward_folds", 3))
        add(
            "walk_forward_folds",
            wf_folds >= min_folds,
            wf_folds,
            min_folds,
            "走步验证折数必须足够，折数过少时不能支撑实盘级稳定性结论。",
        )
    add(
        "walk_forward_avg_excess_return",
        wf_avg is not None and wf_avg >= min_wf,
        "" if wf_avg is None else wf_avg,
        min_wf,
        "走步验证平均样本外超额必须达到门槛。",
    )
    add(
        "walk_forward_positive_ratio",
        wf_ratio is not None and wf_ratio >= min_ratio,
        "" if wf_ratio is None else wf_ratio,
        min_ratio,
        "走步验证样本外胜率必须达到门槛。",
    )

    price_symbol_count = data_quality_value(data_quality, "price_symbol_count")
    min_symbols = int(guard.get("min_price_symbol_count", 500))
    universe_scope = (
        data_quality_text(data_quality, "universe_scope")
        or str(config.get("universe", {}).get("scope", "curated"))
    ).lower()
    add(
        "universe_scope",
        universe_scope in {"curated", "full_market"},
        universe_scope or "missing",
        "curated or full_market",
        "股票池口径必须明确；curated 只证明精选子股票池，full_market 才能解释为全市场覆盖。",
    )
    add(
        "price_symbol_count",
        price_symbol_count is not None and price_symbol_count >= min_symbols,
        "" if price_symbol_count is None else price_symbol_count,
        min_symbols,
        "实盘级策略需要足够数量的可交易样本，避免小样本偶然性。",
    )

    coverage = data_quality_value(data_quality, "priced_stock_coverage")
    if universe_scope == "full_market":
        min_full_coverage = float(guard.get("min_full_market_priced_stock_coverage", 0.7))
        add(
            "priced_stock_coverage",
            coverage is not None and coverage >= min_full_coverage,
            "" if coverage is None else coverage,
            min_full_coverage,
            "全市场股票池必须有足够高的行情覆盖率；覆盖不足时不能把结果解释为全市场策略。",
        )
    else:
        add(
            "priced_stock_coverage",
            True,
            "" if coverage is None else coverage,
            "curated subset",
            "当前按精选股票池评估；低全市场覆盖率不阻断，但报告结论只适用于有行情的精选股票池。",
        )

    cache_age = data_quality_value(data_quality, "price_cache_age_days")
    max_age = int(config.get("data", {}).get("max_cache_age_days", 10))
    add(
        "price_cache_age_days",
        cache_age is not None and cache_age <= max_age,
        "" if cache_age is None else cache_age,
        max_age,
        "行情缓存必须足够新。",
    )
    add_index_membership_readiness(add, data_quality, config)

    if bool(guard.get("require_fundamental_provenance", False)):
        add_fundamental_provenance_readiness(add, data_quality)

    if guard.get("require_industry_metadata", True):
        unknown = data_quality_value(data_quality, "unknown_industry_ratio")
        max_unknown = float(guard.get("max_unknown_industry_ratio", 0.2))
        add(
            "unknown_industry_ratio",
            unknown is not None and unknown <= max_unknown,
            "" if unknown is None else unknown,
            max_unknown,
            "行业缺失比例过高时，行业暴露和行业上限风控不能视为实盘级可信。",
        )

    if guard.get("require_listing_metadata", True):
        imputed = data_quality_value(data_quality, "imputed_list_date_ratio")
        max_imputed = float(guard.get("max_imputed_list_date_ratio", 0.2))
        add(
            "imputed_list_date_ratio",
            imputed is not None and imputed <= max_imputed,
            "" if imputed is None else imputed,
            max_imputed,
            "上市日期占位比例过高时，新股、次新股和退市过滤不能视为实盘级可信。",
        )

    optimized = data_quality_bool(data_quality, "optimized_on_current_cache")
    allow_optimized = bool(guard.get("allow_optimized_on_current_cache", False))
    candidate_validation = candidate_validation if candidate_validation is not None else pd.DataFrame()
    candidate_ok = candidate_forward_validated(candidate_validation)
    if optimized and not allow_optimized:
        if candidate_validation.empty:
            add(
                "candidate_exists",
                False,
                "missing",
                "present",
                "缺少冻结候选参数清单；当前缓存调参污染无法解除。",
            )
        else:
            for row in candidate_validation.itertuples(index=False):
                check = str(row.check)
                if check.startswith("candidate_") or check == "forward_validation_status":
                    add(check, bool(row.passed), row.value, row.threshold, str(row.message))
    add(
        "optimized_on_current_cache",
        allow_optimized or optimized is False or candidate_ok,
        "candidate_validated" if candidate_ok else ("" if optimized is None else optimized),
        "false or validated_candidate",
        "实盘级使用前，当前缓存调参标记必须为 false，或已有冻结后前向验证通过的候选参数。",
    )

    if bool(guard.get("require_factor_robustness", False)):
        factor_summary = pd.DataFrame() if factor_robustness_summary is None else factor_robustness_summary
        factor_rows = (
            factor_summary[factor_summary["check"].astype(str) == "factor_robustness_status"]
            if {"check", "passed"}.issubset(factor_summary.columns)
            else pd.DataFrame()
        )
        factor_passed = len(factor_rows) == 1 and parse_bool_like(factor_rows.iloc[0].get("passed", False))
        factor_value = "missing" if factor_rows.empty else factor_rows.iloc[0].get("value", "")
        add(
            "factor_robustness_status",
            factor_passed,
            factor_value,
            "passed",
            "因子稳健性必须通过，且该证据会参与候选选择与激活门禁。",
        )

        def factor_summary_value(check: str) -> object:
            selected = factor_summary[factor_summary["check"].astype(str) == check] if "check" in factor_summary else pd.DataFrame()
            return "" if len(selected) != 1 else selected.iloc[0].get("value", "")

        manifest = load_candidate_manifest(config) or {}
        expected_candidate_identity = candidate_evidence_identity_sha256(manifest)
        factor_candidate_identity = str(factor_summary_value("factor_robustness_candidate_identity_sha256") or "")
        candidate_identity_current = (
            factor_candidate_identity == expected_candidate_identity
            if expected_candidate_identity
            else factor_candidate_identity in {"", "not_applicable"}
        )
        add(
            "factor_robustness_candidate_identity_current",
            candidate_identity_current,
            factor_candidate_identity or "missing",
            expected_candidate_identity or "not_applicable",
            "因子稳健性必须绑定当前候选 ID、冻结边界和冻结输入哈希，重新登记后不得复用旧证据。",
        )
        factor_config_hash = str(factor_summary_value("factor_robustness_config_sha256") or "")
        expected_factor_config_hash = backtest_config_sha256(config)
        add(
            "factor_robustness_config_current",
            bool(factor_config_hash) and factor_config_hash == expected_factor_config_hash,
            factor_config_hash or "missing",
            expected_factor_config_hash,
            "因子稳健性必须绑定当前策略和执行配置，旧配置结果不得用于候选激活。",
        )
        factor_data_hash = str(factor_summary_value("factor_robustness_data_sha256") or "")
        add(
            "factor_robustness_data_bound",
            len(factor_data_hash) == 64,
            factor_data_hash or "missing",
            "64-character market-data SHA-256",
            "因子稳健性必须记录实际测试行情的哈希身份。",
        )
        isolation_rows = (
            factor_summary[factor_summary["check"].astype(str) == "factor_robustness_forward_isolation"]
            if "check" in factor_summary
            else pd.DataFrame()
        )
        isolation_ok = len(isolation_rows) == 1 and parse_bool_like(isolation_rows.iloc[0].get("passed", False))
        add(
            "factor_robustness_forward_isolation",
            isolation_ok,
            "missing" if isolation_rows.empty else isolation_rows.iloc[0].get("value", ""),
            "sample_end <= freeze_cutoff for forward-only candidates",
            "预注册候选的因子测试不得使用冻结后的前向行情。",
        )
    if guard.get("require_candidate_stability", False):
        stability = candidate_stability if candidate_stability is not None else pd.DataFrame()
        stability_exists = not stability.empty and {"item", "passed"}.issubset(stability.columns)
        passed_mask = (
            stability["passed"].map(parse_bool_like)
            if stability_exists
            else pd.Series(dtype=bool)
        )
        failed_items = (
            stability.loc[~passed_mask, "item"].astype(str).tolist()
            if stability_exists
            else []
        )
        stability_ok = stability_exists and bool(passed_mask.all())
        add(
            "candidate_stability_evidence",
            stability_exists,
            len(stability),
            "non-empty stability and provenance checks",
            "严格实盘要求当前候选具有参数邻域稳定性报告及研究数据、产物指纹。",
        )
        add(
            "candidate_stability_passed",
            stability_ok,
            "all passed" if stability_ok else (";".join(failed_items) or "missing"),
            "all candidate stability checks passed",
            "候选数量、样本外横截面稳定性、当前策略排名和研究来源指纹必须全部通过。",
        )

    if guard.get("require_cost_stress", False):
        require_current_stress = bool(guard.get("require_current_cost_stress", True))
        stress = cost_stress_values(
            cost_stress,
            str(guard.get("cost_stress_scenario", "high_cost")),
            config_sha256=backtest_config_sha256(config),
            require_current=require_current_stress,
            run_manifest=run_manifest,
        )
        min_stress_excess = float(guard.get("min_cost_stress_excess_return", 0.0))
        max_stress_drawdown = float(guard.get("max_cost_stress_drawdown", guard.get("max_strategy_drawdown", 1.0)))
        add(
            "cost_stress_config_hash",
            not require_current_stress or stress["config_hash_match"] is True,
            stress["config_sha256"] or "missing",
            backtest_config_sha256(config)[:16],
            "成本压力测试必须由当前配置生成，旧配置压力测试不能用于实盘门禁。",
        )
        add(
            "cost_stress_data_hash",
            not require_current_stress or stress["data_hash_match"] is True,
            stress["data_hash_status"] or "missing",
            "current run_manifest data hashes",
            "成本压力测试必须使用当前股票、行情、基本面和基准数据生成。",
        )
        add(
            "cost_stress_excess_return",
            stress["excess_total_return"] is not None and stress["excess_total_return"] >= min_stress_excess,
            "" if stress["excess_total_return"] is None else stress["excess_total_return"],
            min_stress_excess,
            "高成本压力场景下超额收益仍必须达到门槛。",
        )
        add(
            "cost_stress_max_drawdown",
            stress["max_drawdown"] is not None and abs(stress["max_drawdown"]) <= max_stress_drawdown,
            "" if stress["max_drawdown"] is None else stress["max_drawdown"],
            -max_stress_drawdown,
            "高成本压力场景下最大回撤必须控制在门槛内。",
        )

    if guard.get("require_shadow_observation", False):
        candidate = load_candidate_manifest(config) if guard.get("require_current_candidate_shadow", True) else None
        shadow_stats = shadow_observation_values(
            shadow_forward_audit,
            candidate_id=str(candidate.get("candidate_id", "")) if candidate else "",
            strategy_hash=live_strategy_hash(config),
            require_current=bool(guard.get("require_current_candidate_shadow", True)),
            min_observed_days=int(guard.get("min_shadow_observed_days_per_signal", 1)),
            min_distinct_gap_days=int(guard.get("min_shadow_distinct_cohort_gap_days", 0)),
        )
        min_observed = int(guard.get("min_shadow_observed_signals", 20))
        min_distinct_cohorts = int(guard.get("min_shadow_distinct_cohorts", 0))
        distinct_gap_days = int(guard.get("min_shadow_distinct_cohort_gap_days", 0))
        min_days = int(guard.get("min_shadow_observed_days_per_signal", 1))
        min_avg_excess = float(guard.get("min_shadow_avg_excess_return", 0.0))
        min_positive_ratio = float(guard.get("min_shadow_positive_ratio", 0.55))
        min_covered_weight = float(guard.get("min_shadow_covered_weight", 0.95))
        add(
            "shadow_observed_signal_count",
            shadow_stats["observed_signal_count"] >= min_observed,
            shadow_stats["observed_signal_count"],
            f"{min_observed} signals with >= {min_days} days",
            "当前冻结候选参数下、达到最小观察天数的 Shadow 前向可评估信号数量必须达到门槛。",
        )
        if min_distinct_cohorts > 0:
            add(
                "shadow_distinct_cohort_count",
                shadow_stats["distinct_cohort_count"] >= min_distinct_cohorts,
                shadow_stats["distinct_cohort_count"],
                f">= {min_distinct_cohorts} cohorts separated by >= {distinct_gap_days} calendar days",
                "Shadow 严格实盘证据必须来自时间上分离的信号批次，不能用同一调仓期的重复运行堆高样本数。",
            )
        add(
            "shadow_avg_excess_return",
            shadow_stats["avg_excess_return"] is not None and shadow_stats["avg_excess_return"] >= min_avg_excess,
            "" if shadow_stats["avg_excess_return"] is None else shadow_stats["avg_excess_return"],
            min_avg_excess,
            "Shadow 前向观察平均超额收益必须达到门槛。",
        )
        add(
            "shadow_positive_excess_ratio",
            shadow_stats["positive_excess_ratio"] is not None and shadow_stats["positive_excess_ratio"] >= min_positive_ratio,
            "" if shadow_stats["positive_excess_ratio"] is None else shadow_stats["positive_excess_ratio"],
            min_positive_ratio,
            "Shadow 前向观察正超额比例必须达到门槛。",
        )
        add(
            "shadow_min_covered_weight",
            shadow_stats["min_covered_weight"] is not None and shadow_stats["min_covered_weight"] >= min_covered_weight,
            "" if shadow_stats["min_covered_weight"] is None else shadow_stats["min_covered_weight"],
            min_covered_weight,
            "Shadow 前向观察的信号行情覆盖率必须达到门槛。",
        )

    risk_passed, risk_value, risk_threshold = industry_risk_ready(industry_risk_audit, config)
    add(
        "post_trade_industry_risk",
        risk_passed,
        risk_value,
        risk_threshold,
        "成交后行业暴露必须在配置的目标上限与漂移容忍范围内。",
    )

    return pd.DataFrame(rows, columns=READINESS_COLUMNS)


def latest_out_of_sample_excess(robustness: pd.DataFrame) -> float | None:
    return sample_excess(robustness, "out_of_sample_last_30pct")


def sample_excess(robustness: pd.DataFrame, sample: str) -> float | None:
    if robustness.empty or not {"sample", "excess_return"}.issubset(robustness.columns):
        return None
    out = robustness[robustness["sample"].astype(str) == sample]
    if out.empty:
        return None
    value = pd.to_numeric(out["excess_return"].iloc[0], errors="coerce")
    return None if pd.isna(value) else float(value)


def walk_forward_values(summary: pd.DataFrame) -> tuple[float | None, float | None]:
    if summary.empty:
        return None, None
    row = summary.iloc[0]
    avg = pd.to_numeric(row.get("avg_test_excess_return", None), errors="coerce")
    ratio = pd.to_numeric(row.get("positive_test_ratio", None), errors="coerce")
    return (None if pd.isna(avg) else float(avg), None if pd.isna(ratio) else float(ratio))


def walk_forward_fold_count(summary: pd.DataFrame) -> int | None:
    if summary.empty or "folds" not in summary:
        return None
    value = pd.to_numeric(summary["folds"].iloc[0], errors="coerce")
    return None if pd.isna(value) else int(value)


def data_quality_value(data_quality: pd.DataFrame, item: str) -> float | None:
    if data_quality.empty or "item" not in data_quality:
        return None
    row = data_quality[data_quality["item"] == item]
    if row.empty:
        return None
    value = pd.to_numeric(row["value"].iloc[0], errors="coerce")
    return None if pd.isna(value) else float(value)


def data_quality_bool(data_quality: pd.DataFrame, item: str) -> bool | None:
    if data_quality.empty or "item" not in data_quality:
        return None
    row = data_quality[data_quality["item"] == item]
    if row.empty:
        return None
    value = str(row["value"].iloc[0]).strip().lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    return None


def data_quality_text(data_quality: pd.DataFrame, item: str) -> str:
    if data_quality.empty or "item" not in data_quality:
        return ""
    row = data_quality[data_quality["item"] == item]
    if row.empty:
        return ""
    value = row["value"].iloc[0]
    return "" if pd.isna(value) else str(value).strip()


def add_index_membership_readiness(
    add,
    data_quality: pd.DataFrame,
    config: dict[str, Any],
) -> None:
    policy = config.get("strategy", {}).get("benchmark_membership", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return
    ready = data_quality_bool(data_quality, "point_in_time_membership_ready")
    coverage = data_quality_text(
        data_quality,
        "index_membership_historical_member_price_coverage",
    )
    minimum = data_quality_text(
        data_quality,
        "index_membership_minimum_snapshot_price_coverage",
    )
    add(
        "point_in_time_membership_ready",
        ready is True,
        f"ready={ready};historical={coverage or 'missing'};minimum={minimum or 'missing'}",
        "all point-in-time membership checks passed",
        "启用历史指数成分后，成分快照和退出指数老成员行情必须完整；缺失时同时阻断实用与严格就绪。",
    )


def add_fundamental_provenance_readiness(add, data_quality: pd.DataFrame) -> None:
    status = data_quality_text(data_quality, "fundamental_factor_status").lower()
    source = data_quality_text(data_quality, "fundamental_source") or "missing"
    active = data_quality_text(data_quality, "fundamental_active_factors") or "missing"
    passed = status in {"inactive", "point_in_time_usable"}
    add(
        "fundamental_factor_provenance",
        passed,
        f"status={status or 'missing'};source={source};active={active}",
        "inactive or point_in_time_usable",
        (
            "\u57fa\u672c\u9762\u56e0\u5b50\u53ea\u80fd\u4f7f\u7528\u53ef\u5ba1\u8ba1\u7684\u65f6\u70b9\u6570\u636e\uff1b"
            "\u4ef7\u683c\u4ee3\u7406\u3001\u7f3a\u5931\u6216\u672a\u58f0\u660e\u6765\u6e90\u4f1a\u963b\u65ad\u5b9e\u7528\u548c\u5b9e\u76d8\u51c6\u5907\u5ea6\u3002"
        ),
    )


def candidate_forward_validated(candidate_validation: pd.DataFrame) -> bool:
    if candidate_validation.empty or "check" not in candidate_validation:
        return False
    row = candidate_validation[candidate_validation["check"] == "forward_validation_status"]
    if row.empty:
        return False
    return bool(row["passed"].astype(bool).iloc[0])


def cost_stress_values(
    cost_stress: pd.DataFrame | None,
    scenario: str,
    config_sha256: str = "",
    require_current: bool = False,
    run_manifest: pd.DataFrame | None = None,
) -> dict[str, float | str | bool | None]:
    if cost_stress is None or cost_stress.empty or "scenario" not in cost_stress:
        return {
            "excess_total_return": None,
            "max_drawdown": None,
            "config_sha256": "",
            "config_hash_match": False,
            "data_hash_match": False,
            "data_hash_status": "",
        }
    frame = cost_stress.copy()
    row = frame[frame["scenario"].astype(str) == str(scenario)]
    if row.empty:
        return {
            "excess_total_return": None,
            "max_drawdown": None,
            "config_sha256": "",
            "config_hash_match": False,
            "data_hash_match": False,
            "data_hash_status": "",
        }
    selected = row.iloc[0]
    excess = pd.to_numeric(selected.get("excess_total_return", None), errors="coerce")
    drawdown = pd.to_numeric(selected.get("max_drawdown", None), errors="coerce")
    stress_hash = "" if pd.isna(selected.get("config_sha256", "")) else str(selected.get("config_sha256", ""))
    return {
        "excess_total_return": None if pd.isna(excess) else float(excess),
        "max_drawdown": None if pd.isna(drawdown) else float(drawdown),
        "config_sha256": stress_hash[:16],
        "config_hash_match": (not require_current) or bool(config_sha256 and stress_hash == config_sha256),
        "data_hash_match": (not require_current) or cost_stress_data_hash_matches(selected, run_manifest),
        "data_hash_status": cost_stress_data_hash_status(selected, run_manifest),
    }


def cost_stress_data_hash_matches(row: pd.Series, run_manifest: pd.DataFrame | None) -> bool:
    if run_manifest is None or run_manifest.empty:
        return False
    for name in ["stocks", "prices", "fundamentals", "benchmark"]:
        stress_hash = "" if pd.isna(row.get(f"{name}_sha256", "")) else str(row.get(f"{name}_sha256", ""))
        manifest_hash = manifest_value(run_manifest, f"{name}_sha256")
        if not stress_hash or stress_hash != manifest_hash:
            return False
    return True


def cost_stress_data_hash_status(row: pd.Series, run_manifest: pd.DataFrame | None) -> str:
    if run_manifest is None or run_manifest.empty:
        return "missing_run_manifest"
    mismatched = []
    for name in ["stocks", "prices", "fundamentals", "benchmark"]:
        stress_hash = "" if pd.isna(row.get(f"{name}_sha256", "")) else str(row.get(f"{name}_sha256", ""))
        manifest_hash = manifest_value(run_manifest, f"{name}_sha256")
        if not stress_hash:
            mismatched.append(f"{name}:missing")
        elif stress_hash != manifest_hash:
            mismatched.append(f"{name}:mismatch")
    return "match" if not mismatched else ",".join(mismatched)


def shadow_observation_values(
    shadow_forward_audit: pd.DataFrame | None,
    candidate_id: str = "",
    strategy_hash: str = "",
    require_current: bool = False,
    min_observed_days: int = 1,
    min_distinct_gap_days: int = 0,
) -> dict[str, float | int | None]:
    if shadow_forward_audit is None or shadow_forward_audit.empty:
        return {
            "observed_signal_count": 0,
            "distinct_cohort_count": 0,
            "avg_excess_return": None,
            "positive_excess_ratio": None,
            "min_covered_weight": None,
        }
    audit = shadow_forward_audit.copy()
    if require_current:
        if not candidate_id or "candidate_id" not in audit.columns:
            audit = audit.iloc[0:0]
        else:
            audit = audit[audit["candidate_id"].astype(str) == str(candidate_id)]
        if not audit.empty:
            if not strategy_hash or "strategy_hash" not in audit.columns:
                audit = audit.iloc[0:0]
            else:
                audit = audit[audit["strategy_hash"].astype(str) == str(strategy_hash)]
    if "signal_hash" in audit.columns:
        audit = audit.drop_duplicates(subset=["signal_hash"], keep="last")
    elif "signal_date" in audit.columns:
        audit = audit.drop_duplicates(subset=["signal_date"], keep="last")
    if audit.empty:
        return {
            "observed_signal_count": 0,
            "distinct_cohort_count": 0,
            "avg_excess_return": None,
            "positive_excess_ratio": None,
            "min_covered_weight": None,
        }
    status = audit.get("status", pd.Series("", index=audit.index)).astype(str).str.lower()
    days = pd.to_numeric(audit.get("observed_trading_days", pd.Series(0, index=audit.index)), errors="coerce").fillna(0)
    excess = pd.to_numeric(audit.get("excess_return", pd.Series(index=audit.index, dtype="float64")), errors="coerce")
    coverage = pd.to_numeric(audit.get("covered_weight", pd.Series(index=audit.index, dtype="float64")), errors="coerce")
    observed_mask = status.isin({"ok", "partial_coverage"}) & (days >= max(1, int(min_observed_days))) & excess.notna()
    if not bool(observed_mask.any()):
        return {
            "observed_signal_count": 0,
            "distinct_cohort_count": 0,
            "avg_excess_return": None,
            "positive_excess_ratio": None,
            "min_covered_weight": None,
        }
    observed = audit.loc[observed_mask].copy()
    cohorts = distinct_shadow_cohorts(observed, min_distinct_gap_days)
    cohort_excess = pd.to_numeric(cohorts["excess_return"], errors="coerce").dropna()
    cohort_coverage = pd.to_numeric(cohorts.get("covered_weight", pd.Series(dtype=float)), errors="coerce").dropna()
    return {
        "observed_signal_count": int(len(observed)),
        "distinct_cohort_count": int(len(cohorts)),
        "avg_excess_return": None if cohort_excess.empty else float(cohort_excess.mean()),
        "positive_excess_ratio": None if cohort_excess.empty else float((cohort_excess > 0).mean()),
        "min_covered_weight": None if cohort_coverage.empty else float(cohort_coverage.min()),
    }


def distinct_shadow_cohorts(frame: pd.DataFrame, min_gap_days: int) -> pd.DataFrame:
    if frame.empty or min_gap_days <= 0 or "execution_date" not in frame.columns:
        return frame.copy()
    dated = frame.copy()
    dated["_execution_date"] = pd.to_datetime(dated["execution_date"], errors="coerce")
    dated = dated.dropna(subset=["_execution_date"]).sort_values(["_execution_date"])
    if dated.empty:
        return dated.drop(columns=["_execution_date"], errors="ignore")
    selected: list[object] = []
    last_date: pd.Timestamp | None = None
    gap = pd.Timedelta(days=max(0, int(min_gap_days)))
    for index, row in dated.iterrows():
        current = row["_execution_date"]
        if last_date is None or current - last_date >= gap:
            selected.append(index)
            last_date = current
    return dated.loc[selected].drop(columns=["_execution_date"], errors="ignore")


def readiness_passed(readiness: pd.DataFrame) -> bool:
    return not readiness.empty and bool(readiness["passed"].astype(bool).all())


def build_live_readiness_roadmap(readiness: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    if readiness.empty or "passed" not in readiness:
        return pd.DataFrame(
            [
                roadmap_row(
                    1,
                    "证据生成",
                    "live_readiness",
                    "action_required",
                    "missing",
                    "present",
                    "",
                    "先运行 backtest 生成实盘门禁证据。",
                    r".\scripts\aq.ps1 backtest",
                )
            ],
            columns=READINESS_ROADMAP_COLUMNS,
        )
    failed = readiness[~readiness["passed"].map(parse_bool_like)].copy()
    if failed.empty:
        return pd.DataFrame(
            [
                roadmap_row(
                    1,
                    "实盘门禁",
                    "all_checks",
                    "passed",
                    "passed",
                    "all checks passed",
                    0,
                    "门禁全部通过；仍需人工复核订单、账户和券商端输入。",
                    r".\scripts\aq.ps1 paper",
                )
            ],
            columns=READINESS_ROADMAP_COLUMNS,
        )
    rows = [roadmap_item(row, config or {}) for row in failed.itertuples(index=False)]
    return pd.DataFrame(rows, columns=READINESS_ROADMAP_COLUMNS).sort_values(["priority", "check"]).reset_index(drop=True)


def roadmap_item(row: Any, config: dict[str, Any]) -> dict[str, object]:
    check = str(row.check)
    value = "" if pd.isna(row.value) else row.value
    threshold = "" if pd.isna(row.threshold) else row.threshold
    remaining = roadmap_remaining(value, threshold)
    if check == "candidate_forward_trading_days":
        return roadmap_row(
            1,
            "冻结参数前向验证",
            check,
            "collecting_forward_data",
            value,
            threshold,
            remaining,
            "继续积累冻结后完整交易日；不要改动策略、交易、股票池和风控参数。",
            r".\scripts\aq.ps1 observe --quiet",
        )
    if check in {"forward_validation_status", "optimized_on_current_cache"}:
        return roadmap_row(
            2,
            "冻结参数前向验证",
            check,
            "waiting_for_candidate_validation",
            value,
            threshold,
            remaining,
            "等待冻结候选参数达到前向交易日门槛，并确认冻结后超额收益仍通过。",
            r".\scripts\aq.ps1 candidate-audit",
        )
    if check == "shadow_observed_signal_count":
        return roadmap_row(
            1,
            "Shadow 前向观察",
            check,
            "collecting_shadow_signals",
            value,
            threshold,
            remaining,
            "每天盘后记录 shadow 信号，并用后续真实行情审计；每条信号需达到最小观察天数。",
            r".\scripts\aq.ps1 observe --quiet",
        )
    if check.startswith("shadow_"):
        return roadmap_row(
            2,
            "Shadow 前向观察",
            check,
            "waiting_for_observable_shadow_results",
            value,
            threshold,
            remaining,
            "当前可评估 shadow 样本不足；继续观察，等样本足够后再判断平均超额、胜率和覆盖率。",
            r".\scripts\aq.ps1 shadow-audit",
        )
    if check in {"price_cache_age_days", "price_symbol_count", "priced_stock_coverage"}:
        return roadmap_row(
            1,
            "数据新鲜度/覆盖",
            check,
            "action_required",
            value,
            threshold,
            remaining,
            "刷新行情池并重新生成回测证据。",
            r".\scripts\aq.ps1 fetch --cached-universe --max-symbols 800",
        )
    if check in {"unknown_industry_ratio", "imputed_list_date_ratio"}:
        return roadmap_row(
            1,
            "股票元数据",
            check,
            "action_required",
            value,
            threshold,
            remaining,
            "刷新行业和上市日期元数据，然后重新运行 backtest。",
            r".\scripts\aq.ps1 metadata --max-symbols 800 --workers 3",
        )
    if check.startswith("cost_stress_"):
        return roadmap_row(
            1,
            "成本压力测试",
            check,
            "action_required",
            value,
            threshold,
            remaining,
            "重新运行成本压力测试并刷新报告，确保压力测试匹配当前配置和数据。",
            r".\scripts\aq.ps1 stress",
        )
    if check in {"in_sample_excess_return", "walk_forward_folds", "walk_forward_avg_excess_return", "walk_forward_positive_ratio"}:
        return roadmap_row(
            2,
            "策略稳定性验证",
            check,
            "review_required",
            value,
            threshold,
            remaining,
            "重新运行 workflow 刷新研究、回测、走步验证和压力测试；如果仍不达标，应重新设计策略而不是继续调参追历史。",
            r".\scripts\aq.ps1 workflow",
        )
    if check.startswith("candidate_stability_"):
        return roadmap_row(
            1,
            "候选参数稳定性",
            check,
            "action_required",
            value,
            threshold,
            remaining,
            "重新运行候选研究和报告，确认参数邻域、样本外横截面及研究指纹全部通过；不要用单一最优参数替代稳定参数族。",
            r".\scripts\aq.ps1 workflow",
        )
    if check in {"candidate_exists", "candidate_hash_match"}:
        return roadmap_row(
            1,
            "候选参数冻结",
            check,
            "action_required",
            value,
            threshold,
            remaining,
            "冻结当前候选参数；若已冻结但配置不一致，需明确重新冻结并重新开始前向验证。",
            r".\scripts\aq.ps1 freeze-candidate",
        )
    return roadmap_row(
        3,
        "其他门禁",
        check,
        "review_required",
        value,
        threshold,
        remaining,
        str(row.message),
        r".\scripts\aq.ps1 doctor",
    )


def roadmap_row(
    priority: int,
    area: str,
    check: str,
    status: str,
    current: object,
    required: object,
    remaining: object,
    next_action: str,
    command: str,
) -> dict[str, object]:
    return {
        "priority": priority,
        "area": area,
        "check": check,
        "status": status,
        "current": current,
        "required": required,
        "remaining": remaining,
        "next_action": next_action,
        "command": command,
    }


def roadmap_remaining(value: object, threshold: object) -> object:
    current = pd.to_numeric(value, errors="coerce")
    required = pd.to_numeric(threshold, errors="coerce")
    if pd.notna(current) and pd.notna(required):
        return max(0.0, float(required) - float(current))
    current_int = first_int(value)
    required_int = first_int(threshold)
    if current_int is not None and required_int is not None:
        return max(0, required_int - current_int)
    return ""


def first_int(value: object) -> int | None:
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def readiness_level(readiness: pd.DataFrame) -> str:
    if readiness_passed(readiness):
        return "实盘级"
    if readiness.empty:
        return "未评估"
    failed = set(readiness.loc[~readiness["passed"].astype(bool), "check"].astype(str))
    strategy_checks = {
        "total_excess_return",
        "max_strategy_drawdown",
        "annualized_turnover",
        "in_sample_excess_return",
        "walk_forward_folds",
        "out_of_sample_excess_return",
        "walk_forward_avg_excess_return",
        "walk_forward_positive_ratio",
        "cost_stress_config_hash",
        "cost_stress_data_hash",
        "cost_stress_excess_return",
        "cost_stress_max_drawdown",
    }
    evidence_checks = {
        "universe_scope",
        "priced_stock_coverage",
        "unknown_industry_ratio",
        "imputed_list_date_ratio",
        "optimized_on_current_cache",
        "candidate_exists",
        "candidate_hash_match",
        "candidate_forward_trading_days",
        "candidate_forward_excess_return",
        "forward_validation_status",
        "shadow_observed_signal_count",
        "shadow_avg_excess_return",
        "shadow_positive_excess_ratio",
        "shadow_min_covered_weight",
    }
    if failed and failed.issubset(evidence_checks):
        return "模拟盘级"
    if failed.isdisjoint(strategy_checks):
        return "研究级"
    return "不通过"


def practical_readiness_level(readiness: pd.DataFrame) -> str:
    if readiness_passed(readiness):
        return "实用可运行级"
    if readiness.empty:
        return "未评估"
    failed = set(readiness.loc[~readiness["passed"].astype(bool), "check"].astype(str))
    strategy_checks = {
        "total_excess_return",
        "max_strategy_drawdown",
        "annualized_turnover",
        "in_sample_excess_return",
        "walk_forward_folds",
        "out_of_sample_excess_return",
        "walk_forward_avg_excess_return",
        "walk_forward_positive_ratio",
        "cost_stress_excess_return",
        "cost_stress_max_drawdown",
    }
    if failed.isdisjoint(strategy_checks):
        return "需刷新或补数据"
    return "未通过"


def load_live_readiness(config: dict[str, Any]) -> pd.DataFrame:
    path = project_path(config["paths"]["output_dir"]) / "live_readiness.csv"
    if not path.exists():
        return pd.DataFrame(columns=READINESS_COLUMNS)
    return pd.read_csv(path)


def data_quality_with_current_cache_age(
    data_quality: pd.DataFrame,
    run_manifest: pd.DataFrame | None,
    config: dict[str, Any],
    today: date | None = None,
) -> pd.DataFrame:
    if data_quality.empty or "item" not in data_quality or "value" not in data_quality:
        return data_quality
    out = data_quality.copy()
    latest = (
        data_quality_text(out, "backtest_price_end")
        or data_quality_text(out, "complete_price_end")
        or data_quality_text(out, "price_end")
        or manifest_value(run_manifest if run_manifest is not None else pd.DataFrame(), "prices_last_date")
    )
    age = cache_age_from_date_text(latest, today=today)
    if age is None:
        return out
    max_age = int(config.get("data", {}).get("max_cache_age_days", 10))
    note = f"行情缓存距今天的自然日天数；超过 {max_age} 天时不应生成实盘辅助订单。"
    mask = out["item"].astype(str) == "price_cache_age_days"
    if mask.any():
        out.loc[mask, "value"] = str(age)
        if "severity" in out.columns:
            out.loc[mask, "severity"] = "warning" if age > max_age else "ok"
        if "note" in out.columns:
            out.loc[mask, "note"] = note
    else:
        row = {column: "" for column in out.columns}
        row.update({"item": "price_cache_age_days", "value": str(age)})
        if "severity" in out.columns:
            row["severity"] = "warning" if age > max_age else "ok"
        if "note" in out.columns:
            row["note"] = note
        out = pd.concat([out, pd.DataFrame([row])], ignore_index=True)
    return out


def cache_age_from_date_text(value: object, today: date | None = None) -> int | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    reference = today or date.today()
    return max(0, (reference - parsed.date()).days)


def bootstrap_value(frame: pd.DataFrame | None, column: str) -> float | None:
    if frame is None or frame.empty or column not in frame.columns:
        return None
    value = pd.to_numeric(frame[column].iloc[0], errors="coerce")
    return None if pd.isna(value) else float(value)


def bootstrap_text(frame: pd.DataFrame | None, column: str) -> str:
    if frame is None or frame.empty or column not in frame.columns:
        return ""
    return str(frame[column].iloc[0]).strip().lower()


def build_live_readiness_from_outputs(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    comparison = read_output_csv(output_dir / "benchmark_comparison.csv")
    metrics = metrics_from_output(read_output_csv(output_dir / "performance_metrics.csv"))
    metrics["excess_total_return"] = comparison_total_excess_return(comparison)
    run_manifest = read_output_csv(output_dir / "run_manifest.csv")
    data_quality = data_quality_with_current_cache_age(read_output_csv(output_dir / "data_quality.csv"), run_manifest, config)
    return build_live_readiness(
        config,
        metrics,
        data_quality,
        read_output_csv(output_dir / "robustness_split.csv"),
        read_output_csv(output_dir / "walk_forward_summary.csv"),
        build_candidate_validation(
            config,
            comparison,
            read_output_csv(output_dir / "shadow_forward_audit.csv"),
        ),
        read_output_csv(output_dir / "shadow_forward_audit.csv"),
        read_output_csv(output_dir / "cost_stress.csv"),
        run_manifest,
        candidate_stability=read_output_csv(output_dir / "strategy_candidate_stability.csv"),
        excess_bootstrap=read_output_csv(output_dir / "excess_bootstrap.csv"),
        industry_risk_audit=read_output_csv(output_dir / "industry_risk_audit.csv"),
        factor_robustness_summary=read_output_csv(output_dir / "factor_robustness_summary.csv"),
    )

def build_practical_readiness(
    config: dict[str, Any],
    metrics: dict[str, float],
    data_quality: pd.DataFrame,
    robustness: pd.DataFrame,
    walk_forward_summary: pd.DataFrame,
    cost_stress: pd.DataFrame | None = None,
    run_manifest: pd.DataFrame | None = None,
    evidence_issues: list[str] | None = None,
    capital_sensitivity_audit: pd.DataFrame | None = None,
    industry_risk_audit: pd.DataFrame | None = None,
    factor_robustness_summary: pd.DataFrame | None = None,
) -> pd.DataFrame:
    guard = config.get("practical_guard", {})
    if not guard.get("enabled", True):
        return pd.DataFrame(
            [{"check": "practical_guard", "passed": True, "value": "disabled", "threshold": "", "message": "实用门禁已关闭。"}],
            columns=READINESS_COLUMNS,
        )

    rows: list[dict[str, object]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    if bool(guard.get("require_current_evidence", True)):
        issues = evidence_issues or []
        add(
            "evidence_current",
            not issues,
            "current" if not issues else "; ".join(issues),
            "current backtest config and market-data manifest",
            "回测相关配置与缓存数据必须一致；调度、界面等运维配置单独审计，不应污染策略证据。",
        )

    if bool(guard.get("require_capital_sensitivity", False)):
        capital_audit = pd.DataFrame() if capital_sensitivity_audit is None else capital_sensitivity_audit

        def capital_row(check: str) -> pd.Series:
            if capital_audit.empty or not {"check", "passed", "value"}.issubset(capital_audit.columns):
                return pd.Series(dtype=object)
            selected = capital_audit[capital_audit["check"].astype(str) == check]
            return selected.iloc[-1] if not selected.empty else pd.Series(dtype=object)

        config_row = capital_row("capital_sensitivity_strategy_config_sha256")
        actual_hash = "" if config_row.empty else str(config_row.get("value", ""))
        expected_hash = capital_sensitivity_config_sha256(config)
        add(
            "capital_sensitivity_config_current",
            bool(actual_hash) and actual_hash == expected_hash,
            actual_hash or "missing",
            expected_hash,
            "Capital-sensitivity evidence must match the current strategy, universe, rules and execution assumptions.",
        )
        reference_row = capital_row("capital_sensitivity_reference_market_data_sha256")
        actual_reference_hash = "" if reference_row.empty else str(reference_row.get("value", ""))
        expected_reference_hash = manifest_market_data_fingerprint(run_manifest)
        add(
            "capital_sensitivity_data_current",
            (
                not reference_row.empty
                and parse_bool_like(reference_row.get("passed", False))
                and bool(expected_reference_hash)
                and actual_reference_hash == expected_reference_hash
            ),
            actual_reference_hash or "missing",
            expected_reference_hash or "current main run_manifest market-data fingerprint",
            "Capital-sensitivity evidence must use the exact market data from the current main backtest.",
        )
        ready_row = capital_row("capital_sensitivity_ready")
        capital_ready = not ready_row.empty and parse_bool_like(ready_row.get("passed", False))
        add(
            "capital_sensitivity_ready",
            capital_ready,
            "missing" if ready_row.empty else ready_row.get("value", ""),
            "audited capital level passes with identical market data",
            "Board-lot capital sensitivity must pass before practical execution is allowed.",
        )

    total_excess = float(metrics.get("excess_total_return", 0.0))
    min_total = float(guard.get("min_total_excess_return", 0.0))
    add("total_excess_return", total_excess >= min_total, total_excess, min_total, "过去回测区间必须跑赢基准。")

    if "min_information_ratio" in guard:
        information_ratio = optional_float(metrics.get("information_ratio"))
        min_information_ratio = float(guard["min_information_ratio"])
        add(
            "information_ratio",
            information_ratio is not None and information_ratio >= min_information_ratio,
            "" if information_ratio is None else information_ratio,
            min_information_ratio,
            "实用观察也要求主动收益具有基本的风险调整后效率。",
        )
    if "max_tracking_error" in guard:
        tracking_error = optional_float(metrics.get("tracking_error"))
        max_tracking_error = float(guard["max_tracking_error"])
        add(
            "tracking_error",
            tracking_error is not None and tracking_error <= max_tracking_error,
            "" if tracking_error is None else tracking_error,
            max_tracking_error,
            "实用观察组合相对基准的波动偏离必须保持在可解释范围内。",
        )

    drawdown = float(metrics.get("max_drawdown", 0.0))
    max_drawdown = float(guard.get("max_strategy_drawdown", 0.35))
    add("max_strategy_drawdown", abs(drawdown) <= max_drawdown, drawdown, -max_drawdown, "策略最大回撤必须控制在实用阈值内。")

    turnover = float(metrics.get("annualized_turnover", 0.0))
    max_turnover = float(guard.get("max_annualized_turnover", 10.0))
    add("annualized_turnover", turnover <= max_turnover, turnover, max_turnover, "换手过高会放大滑点、佣金和人工执行误差。")

    out_excess = latest_out_of_sample_excess(robustness)
    min_out = float(guard.get("min_out_of_sample_excess_return", 0.0))
    if bool(guard.get("require_out_of_sample", True)):
        add(
            "out_of_sample_excess_return",
            out_excess is not None and out_excess >= min_out,
            "" if out_excess is None else out_excess,
            min_out,
            "最近样本外区间不能明显弱于基准。",
        )
    elif out_excess is not None:
        add("out_of_sample_excess_return", True, out_excess, "advisory", "样本外结果仅作提示。")

    wf_avg, wf_ratio = walk_forward_values(walk_forward_summary)
    if bool(guard.get("require_walk_forward", False)):
        min_wf = float(guard.get("min_walk_forward_avg_excess_return", 0.0))
        min_ratio = float(guard.get("min_walk_forward_positive_ratio", 0.5))
        wf_folds = walk_forward_fold_count(walk_forward_summary)
        if "min_walk_forward_folds" in guard and wf_folds is not None:
            min_folds = int(guard.get("min_walk_forward_folds", 2))
            add(
                "walk_forward_folds",
                wf_folds >= min_folds,
                wf_folds,
                min_folds,
                "实用模式也需要足够走步折数，否则样本外稳定性证据过少。",
            )
        add(
            "walk_forward_avg_excess_return",
            wf_avg is not None and wf_avg >= min_wf,
            "" if wf_avg is None else wf_avg,
            min_wf,
            "滚动验证平均超额收益必须达到实用阈值。",
        )
        add(
            "walk_forward_positive_ratio",
            wf_ratio is not None and wf_ratio >= min_ratio,
            "" if wf_ratio is None else wf_ratio,
            min_ratio,
            "滚动验证胜率必须达到实用阈值。",
        )
    elif wf_avg is not None or wf_ratio is not None:
        add("walk_forward_summary", True, f"avg={wf_avg}, positive_ratio={wf_ratio}", "advisory", "滚动验证仅作辅助提示。")

    universe_scope = (
        data_quality_text(data_quality, "universe_scope")
        or str(config.get("universe", {}).get("scope", "curated"))
    ).lower()
    add(
        "universe_scope",
        universe_scope in {"curated", "full_market"},
        universe_scope or "missing",
        "curated or full_market",
        "股票池范围必须明确；精选池结果不能解释为全市场覆盖。",
    )

    price_symbol_count = data_quality_value(data_quality, "price_symbol_count")
    min_symbols = int(guard.get("min_price_symbol_count", 80))
    add(
        "price_symbol_count",
        price_symbol_count is not None and price_symbol_count >= min_symbols,
        "" if price_symbol_count is None else price_symbol_count,
        min_symbols,
        "可交易样本数量过低时，回测容易被少数股票偶然表现主导。",
    )

    coverage = data_quality_value(data_quality, "priced_stock_coverage")
    if universe_scope == "full_market":
        min_full = float(guard.get("min_full_market_priced_stock_coverage", 0.7))
        add(
            "priced_stock_coverage",
            coverage is not None and coverage >= min_full,
            "" if coverage is None else coverage,
            min_full,
            "全市场模式下，行情覆盖率过低会使结论失真。",
        )
    else:
        add(
            "priced_stock_coverage",
            True,
            "" if coverage is None else coverage,
            "curated subset",
            "当前按精选股票池评估；低全市场覆盖率不阻断，但报告结论只适用于已有行情的股票池。",
        )

    cache_age = data_quality_value(data_quality, "price_cache_age_days")
    max_age = int(guard.get("max_cache_age_days", config.get("data", {}).get("max_cache_age_days", 10)))
    add(
        "price_cache_age_days",
        cache_age is not None and cache_age <= max_age,
        "" if cache_age is None else cache_age,
        max_age,
        "行情缓存必须足够新。",
    )
    add_index_membership_readiness(add, data_quality, config)

    if bool(guard.get("require_fundamental_provenance", False)):
        add_fundamental_provenance_readiness(add, data_quality)

    require_metadata = bool(guard.get("require_metadata", False))
    unknown = data_quality_value(data_quality, "unknown_industry_ratio")
    max_unknown = float(guard.get("max_unknown_industry_ratio", 0.5))
    add(
        "unknown_industry_ratio",
        (unknown is not None and unknown <= max_unknown) if require_metadata else True,
        "" if unknown is None else unknown,
        max_unknown if require_metadata else "advisory",
        "行业缺失会降低行业暴露控制可信度；实用门禁默认仅提示，不阻断。",
    )

    imputed = data_quality_value(data_quality, "imputed_list_date_ratio")
    max_imputed = float(guard.get("max_imputed_list_date_ratio", 0.5))
    add(
        "imputed_list_date_ratio",
        (imputed is not None and imputed <= max_imputed) if require_metadata else True,
        "" if imputed is None else imputed,
        max_imputed if require_metadata else "advisory",
        "上市日期占位会影响新股过滤；实用门禁默认仅提示，不阻断。",
    )

    optimized = data_quality_bool(data_quality, "optimized_on_current_cache")
    allow_optimized = bool(guard.get("allow_optimized_on_current_cache", True))
    add(
        "optimized_on_current_cache",
        allow_optimized or optimized is False,
        "" if optimized is None else optimized,
        "allowed in practical mode" if allow_optimized else False,
        "当前参数参考过本缓存回测结果；实用门禁可允许，但报告不会把它解释为严格样本外证据。",
    )

    if bool(guard.get("require_cost_stress", True)):
        require_current_stress = bool(guard.get("require_current_cost_stress", True))
        stress = cost_stress_values(
            cost_stress,
            str(guard.get("cost_stress_scenario", "high_cost")),
            config_sha256=backtest_config_sha256(config),
            require_current=require_current_stress,
            run_manifest=run_manifest,
        )
        min_stress_excess = float(guard.get("min_cost_stress_excess_return", 0.0))
        max_stress_drawdown = float(guard.get("max_cost_stress_drawdown", max_drawdown))
        add(
            "cost_stress_config_hash",
            not require_current_stress or stress["config_hash_match"] is True,
            stress["config_sha256"] or "missing",
            backtest_config_sha256(config)[:16],
            "成本压力测试必须由当前配置生成。",
        )
        add(
            "cost_stress_data_hash",
            not require_current_stress or stress["data_hash_match"] is True,
            stress["data_hash_status"] or "missing",
            "current run_manifest data hashes",
            "成本压力测试必须使用当前缓存数据。",
        )
        add(
            "cost_stress_excess_return",
            stress["excess_total_return"] is not None and stress["excess_total_return"] >= min_stress_excess,
            "" if stress["excess_total_return"] is None else stress["excess_total_return"],
            min_stress_excess,
            "高成本压力场景下仍需不弱于基准。",
        )
        add(
            "cost_stress_max_drawdown",
            stress["max_drawdown"] is not None and abs(stress["max_drawdown"]) <= max_stress_drawdown,
            "" if stress["max_drawdown"] is None else stress["max_drawdown"],
            -max_stress_drawdown,
            "高成本压力场景下最大回撤必须在阈值内。",
        )

    risk_passed, risk_value, risk_threshold = industry_risk_ready(industry_risk_audit, config)
    add(
        "post_trade_industry_risk",
        risk_passed,
        risk_value,
        risk_threshold,
        "成交后行业暴露必须在配置的目标上限与漂移容忍范围内。",
    )

    return pd.DataFrame(rows, columns=READINESS_COLUMNS)


def build_practical_readiness_from_outputs(config: dict[str, Any]) -> pd.DataFrame:
    output_dir = project_path(config["paths"]["output_dir"])
    comparison = read_output_csv(output_dir / "benchmark_comparison.csv")
    metrics = metrics_from_output(read_output_csv(output_dir / "performance_metrics.csv"))
    metrics["excess_total_return"] = comparison_total_excess_return(comparison)
    guard = config.get("practical_guard", {})
    evidence_issues = readiness_evidence_issues(config) if bool(guard.get("require_current_evidence", True)) else []
    run_manifest = read_output_csv(output_dir / "run_manifest.csv")
    data_quality = data_quality_with_current_cache_age(read_output_csv(output_dir / "data_quality.csv"), run_manifest, config)
    return build_practical_readiness(
        config,
        metrics,
        data_quality,
        read_output_csv(output_dir / "robustness_split.csv"),
        read_output_csv(output_dir / "walk_forward_summary.csv"),
        read_output_csv(output_dir / "cost_stress.csv"),
        run_manifest,
        evidence_issues,
        read_output_csv(output_dir / "capital_sensitivity_audit.csv"),
        industry_risk_audit=read_output_csv(output_dir / "industry_risk_audit.csv"),
        factor_robustness_summary=read_output_csv(output_dir / "factor_robustness_summary.csv"),
    )

def read_output_csv(path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def comparison_total_excess_return(comparison: pd.DataFrame) -> float:
    if comparison.empty or not {"strategy_nav", "benchmark_nav"}.issubset(comparison.columns):
        return 0.0
    strategy_nav = pd.to_numeric(comparison["strategy_nav"], errors="coerce").dropna()
    benchmark_nav = pd.to_numeric(comparison["benchmark_nav"], errors="coerce").dropna()
    if strategy_nav.empty or benchmark_nav.empty:
        return 0.0
    return float(strategy_nav.iloc[-1] - benchmark_nav.iloc[-1])


def optional_float(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def metrics_from_output(metrics_frame: pd.DataFrame) -> dict[str, float]:
    if metrics_frame.empty or not {"metric", "value"}.issubset(metrics_frame.columns):
        return {}
    out: dict[str, float] = {}
    for row in metrics_frame.itertuples(index=False):
        value = pd.to_numeric(row.value, errors="coerce")
        if not pd.isna(value):
            out[str(row.metric)] = float(value)
    return out


def readiness_evidence_issues(config: dict[str, Any]) -> list[str]:
    manifest_path = project_path(config["paths"]["output_dir"]) / "run_manifest.csv"
    if not manifest_path.exists():
        return ["缺少 run_manifest.csv；请先运行 backtest 生成当前门禁证据。"]
    try:
        manifest = pd.read_csv(manifest_path)
    except Exception as exc:
        return [f"run_manifest.csv 无法读取：{exc}"]
    if manifest.empty or not {"item", "value"}.issubset(manifest.columns):
        return ["run_manifest.csv 格式无效；请重新运行 backtest。"]

    issues: list[str] = []
    expected_backtest_hash = backtest_config_sha256(config)
    actual_backtest_hash = manifest_value(manifest, "backtest_config_sha256")
    if actual_backtest_hash:
        if actual_backtest_hash != expected_backtest_hash:
            issues.append("backtest_config_sha256 不一致")
    else:
        expected_config_hash = mapping_sha256(config)
        actual_config_hash = manifest_value(manifest, "config_sha256")
        if actual_config_hash != expected_config_hash:
            issues.append("config_sha256 不一致")

    try:
        frames = current_manifest_frames(config)
    except Exception as exc:
        return [f"无法读取当前缓存数据用于门禁证据校验：{exc}"]

    for name, frame in frames.items():
        expected_rows = str(len(frame))
        actual_rows = manifest_value(manifest, f"{name}_rows")
        if actual_rows != expected_rows:
            issues.append(f"{name}_rows 不一致")
        expected_hash = frame_sha256(frame) if not frame.empty else ""
        actual_hash = manifest_value(manifest, f"{name}_sha256")
        if actual_hash != expected_hash:
            issues.append(f"{name}_sha256 不一致")
    return issues


def current_manifest_frames(config: dict[str, Any]) -> dict[str, pd.DataFrame]:
    store = DataStore.from_config(config)
    stocks, prices, fundamentals = store.load_market_data()
    benchmark = store.load_benchmark(required=False)
    stocks, prices, fundamentals, _scope_passed, _scope_value = (
        scope_market_frames_to_committed_fetch(store, stocks, prices, fundamentals)
    )
    start = pd.to_datetime(config["backtest"]["start"]).date()
    end_value = config["backtest"].get("end")
    end = pd.to_datetime(end_value).date() if end_value else max(prices["date"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()
    if not benchmark.empty:
        benchmark = benchmark[(benchmark["date"] >= start) & (benchmark["date"] <= end)].copy()
    prices, benchmark = filter_complete_market_data(prices, benchmark, config)
    return {
        "stocks": effective_stocks_for_prices(stocks, prices),
        "prices": prices,
        "fundamentals": fundamentals,
        "benchmark": benchmark,
    }


def manifest_market_data_fingerprint(manifest: pd.DataFrame | None) -> str:
    if manifest is None or manifest.empty or not {"item", "value"}.issubset(manifest.columns):
        return ""
    values = {
        key: manifest_value(manifest, key)
        for key in [
            "stocks_sha256",
            "prices_sha256",
            "fundamentals_sha256",
            "benchmark_sha256",
        ]
    }
    if any(not value for value in values.values()):
        return ""
    return mapping_sha256(values)


def manifest_value(manifest: pd.DataFrame, item: str) -> str:
    row = manifest[manifest["item"] == item]
    if row.empty:
        return ""
    value = row["value"].iloc[0]
    return "" if pd.isna(value) else str(value)


def ensure_live_readiness(config: dict[str, Any], allow_weak_strategy: bool = False) -> None:
    if allow_weak_strategy or not config.get("live_guard", {}).get("enabled", True):
        return
    evidence_issues = readiness_evidence_issues(config)
    if evidence_issues:
        reasons = "; ".join(evidence_issues)
        raise RuntimeError(f"实盘门禁证据已过期或不完整，已阻止生成订单。请先运行 backtest 刷新报告和门禁证据。{reasons}")
    readiness = build_live_readiness_from_outputs(config)
    if readiness.empty:
        raise RuntimeError("缺少实盘就绪检查结果。请先运行 backtest/report，或使用 --allow-weak-strategy 做历史演练。")
    failed = readiness[~readiness["passed"].astype(bool)]
    if not failed.empty:
        reasons = "; ".join(f"{row.check}: {row.message} (value={row.value}, threshold={row.threshold})" for row in failed.itertuples(index=False))
        raise RuntimeError(f"实盘门禁未通过，已阻止生成订单。{reasons}")


def ensure_practical_readiness(config: dict[str, Any], allow_weak_strategy: bool = False) -> None:
    if allow_weak_strategy or not config.get("practical_guard", {}).get("enabled", True):
        return
    readiness = build_practical_readiness_from_outputs(config)
    if readiness.empty:
        raise RuntimeError("缺少实用门禁检查结果；请先运行 backtest/report，或使用 --allow-weak-strategy 做历史演练。")
    failed = readiness[~readiness["passed"].astype(bool)]
    if not failed.empty:
        reasons = "; ".join(f"{row.check}: {row.message} (value={row.value}, threshold={row.threshold})" for row in failed.itertuples(index=False))
        raise RuntimeError(f"实用门禁未通过，已阻止生成订单。{reasons}")
