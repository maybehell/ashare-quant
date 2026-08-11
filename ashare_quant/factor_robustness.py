from __future__ import annotations

import json
from itertools import combinations
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import backtest_config_sha256, effective_stocks_for_prices, frame_sha256, mapping_sha256
from .candidate import candidate_evidence_identity_sha256, load_candidate_manifest
from .config import project_path
from .data import DataStore
from .factors import build_target_weights, strategy_member_configs
from .io_utils import atomic_to_csv
from .research import ResearchSimulationCache, evaluate_candidate, load_research_market_data
from .rules import monthly_rebalance_dates


FACTOR_ROBUSTNESS_COLUMNS = [
    "scenario",
    "target_type",
    "target_name",
    "factor",
    "perturbation",
    "passed",
    "status",
    "message",
    "score",
    "strategy_return",
    "benchmark_return",
    "excess_return",
    "max_drawdown",
    "in_sample_excess_return",
    "out_of_sample_excess_return",
    "excess_delta_vs_base",
    "out_of_sample_delta_vs_base",
    "max_drawdown_delta_vs_base",
    "anti_overfit_passed",
    "overfit_penalty",
    "stability_score",
    "top_n",
    "max_stock_weight",
    "weighting",
    "weights",
    "ensemble_members",
]

FACTOR_ROBUSTNESS_SUMMARY_COLUMNS = ["check", "passed", "value", "threshold", "message"]


def run_factor_robustness(config: dict[str, Any]) -> dict[str, Path]:
    result, summary = build_factor_robustness(config)
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "factor_robustness.csv"
    summary_path = output_dir / "factor_robustness_summary.csv"
    atomic_to_csv(result, result_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(summary, summary_path, index=False, encoding="utf-8-sig")
    return {"factor_robustness.csv": result_path, "factor_robustness_summary.csv": summary_path}


def build_factor_robustness(config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    store = DataStore.from_config(config)
    stocks, prices, fundamentals, benchmark, freeze_cutoff = factor_robustness_market_data(config, store)
    cache = ResearchSimulationCache(prices, stocks, fundamentals, benchmark)
    candidates = factor_robustness_candidates(config)
    rows = []
    base_metrics: dict[str, Any] | None = None
    for candidate in candidates:
        metrics = evaluate_candidate(prices, stocks, fundamentals, benchmark, config, candidate, simulation_cache=cache)
        if candidate["scenario"] == "current":
            base_metrics = metrics
        if base_metrics is None:
            base_metrics = metrics
        row = robustness_row(config, candidate, metrics, base_metrics)
        rows.append(row)
    result = pd.DataFrame(rows, columns=FACTOR_ROBUSTNESS_COLUMNS)
    summary = factor_robustness_summary(config, result)
    diversity = ensemble_diversity_summary(config, cache)
    summary = pd.concat([summary, diversity], ignore_index=True)
    diversity_status = diversity[diversity["check"].astype(str) == "ensemble_diversity_status"]
    if not diversity_status.empty and not parse_bool_like(diversity_status.iloc[0]["passed"]):
        mask = summary["check"].astype(str) == "factor_robustness_status"
        summary.loc[mask, "passed"] = False
        summary.loc[mask, "value"] = "review_required"
        summary.loc[mask, "message"] = "Overall robustness review failed, including ensemble target-portfolio diversity."
    provenance = factor_robustness_provenance(config, stocks, prices, fundamentals, benchmark, freeze_cutoff)
    summary = pd.concat([summary, provenance], ignore_index=True)
    isolation = provenance[provenance["check"].astype(str) == "factor_robustness_forward_isolation"]
    if isolation.empty or not parse_bool_like(isolation.iloc[0]["passed"]):
        mask = summary["check"].astype(str) == "factor_robustness_status"
        summary.loc[mask, "passed"] = False
        summary.loc[mask, "value"] = "forward_contaminated"
    return result, summary


def factor_robustness_market_data(
    config: dict[str, Any],
    store: DataStore,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    stocks, prices, fundamentals, benchmark = load_research_market_data(config, store)
    manifest = load_candidate_manifest(config) or {}
    forward_only = str(manifest.get("freeze_mode", "") or "") == "forward_only_preregistration"
    cutoff_value = manifest.get("freeze_known_data_end") or manifest.get("freeze_price_end")
    cutoff = pd.to_datetime(cutoff_value, errors="coerce") if forward_only else pd.NaT
    freeze_cutoff = ""
    if forward_only:
        if pd.isna(cutoff):
            raise RuntimeError("Forward-only factor robustness requires a valid candidate freeze boundary.")
        freeze_date = cutoff.date()
        freeze_cutoff = freeze_date.isoformat()
        prices = prices[pd.to_datetime(prices["date"], errors="coerce").dt.date <= freeze_date].copy()
        benchmark = benchmark[pd.to_datetime(benchmark["date"], errors="coerce").dt.date <= freeze_date].copy()
        if "date" in fundamentals:
            fundamentals = fundamentals[
                pd.to_datetime(fundamentals["date"], errors="coerce").dt.date <= freeze_date
            ].copy()
        if prices.empty or benchmark.empty:
            raise RuntimeError("Candidate freeze boundary leaves no factor-robustness market data.")
    return stocks, prices, fundamentals, benchmark, freeze_cutoff


def factor_robustness_data_sha256(
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> str:
    frames = {
        "stocks": effective_stocks_for_prices(stocks, prices),
        "prices": prices,
        "fundamentals": fundamentals,
        "benchmark": benchmark,
    }
    return mapping_sha256({name: frame_sha256(frame) if not frame.empty else "" for name, frame in frames.items()})


def current_factor_robustness_data_sha256(config: dict[str, Any]) -> str:
    store = DataStore.from_config(config)
    stocks, prices, fundamentals, benchmark, _freeze_cutoff = factor_robustness_market_data(
        config, store
    )
    return factor_robustness_data_sha256(stocks, prices, fundamentals, benchmark)


def factor_robustness_provenance(
    config: dict[str, Any],
    stocks: pd.DataFrame,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    freeze_cutoff: str,
) -> pd.DataFrame:
    sample_end = max(pd.to_datetime(prices["date"], errors="coerce").dropna()).date().isoformat()
    isolation_ok = not freeze_cutoff or sample_end <= freeze_cutoff
    manifest = load_candidate_manifest(config) or {}
    candidate_identity = candidate_evidence_identity_sha256(manifest)
    rows = [
        {"check": "factor_robustness_candidate_identity_sha256", "passed": bool(candidate_identity) or not freeze_cutoff, "value": candidate_identity or "not_applicable", "threshold": "current frozen candidate identity", "message": "Factor robustness is bound to the candidate ID, freeze boundary, and frozen input hashes."},
        {"check": "factor_robustness_config_sha256", "passed": True, "value": backtest_config_sha256(config), "threshold": "current backtest config", "message": "Factor robustness is bound to the strategy and execution configuration."},
        {"check": "factor_robustness_data_sha256", "passed": True, "value": factor_robustness_data_sha256(stocks, prices, fundamentals, benchmark), "threshold": "current run-manifest market data", "message": "Factor robustness is bound to the exact market-data frames used by the test."},
        {"check": "factor_robustness_sample_end", "passed": True, "value": sample_end, "threshold": freeze_cutoff or "current configured backtest end", "message": "Latest market date included in factor robustness."},
        {"check": "factor_robustness_forward_isolation", "passed": isolation_ok, "value": f"sample_end={sample_end};freeze_cutoff={freeze_cutoff or 'not_applicable'}", "threshold": "sample_end <= freeze_cutoff for forward-only candidates", "message": "Forward-only candidates must not use post-freeze outcomes in factor robustness."},
    ]
    return pd.DataFrame(rows, columns=FACTOR_ROBUSTNESS_SUMMARY_COLUMNS)

def factor_robustness_candidates(config: dict[str, Any]) -> list[dict[str, Any]]:
    strategy = deepcopy(config.get("strategy", {}))
    ensemble = strategy.get("ensemble_members", [])
    if isinstance(ensemble, list) and ensemble:
        active_factors = sorted(
            {
                factor
                for member in ensemble
                if isinstance(member, dict)
                for factor, weight in numeric_weights(member.get("weights", {})).items()
                if abs(weight) > 1e-12
            }
        )
        current = deepcopy(strategy)
        current.update({"scenario": "current", "factor": "", "perturbation": "base"})
        candidates = [current]
        shock = float(config.get("factor_robustness", {}).get("shock", 0.25))
        for factor in active_factors:
            for scenario, multiplier, label in (
                ("drop_factor", 0.0, "-100%"),
                ("factor_up", 1.0 + shock, f"+{int(round(shock * 100))}%"),
                ("factor_down", max(0.0, 1.0 - shock), f"-{int(round(shock * 100))}%"),
            ):
                candidate = perturb_ensemble_factor(strategy, factor, multiplier)
                candidate["name"] = f"{strategy.get('name', 'strategy')}_{scenario}_{factor}"
                candidate["scenario"] = scenario
                candidate["factor"] = factor
                candidate["perturbation"] = label
                candidates.append(candidate)

        member_shock = float(config.get("factor_robustness", {}).get("member_shock", shock))
        if len([member for member in ensemble if isinstance(member, dict)]) > 1:
            for index, member in enumerate(ensemble):
                if not isinstance(member, dict):
                    continue
                member_name = str(member.get("name") or f"member_{index + 1}")
                for scenario, multiplier, label in (
                    ("drop_member", 0.0, "-100%"),
                    ("member_up", 1.0 + member_shock, f"+{int(round(member_shock * 100))}%"),
                    ("member_down", max(0.0, 1.0 - member_shock), f"-{int(round(member_shock * 100))}%"),
                ):
                    candidate = perturb_ensemble_member(strategy, index, multiplier)
                    candidate["name"] = f"{strategy.get('name', 'strategy')}_{scenario}_{member_name}"
                    candidate["scenario"] = scenario
                    candidate["member"] = member_name
                    candidate["factor"] = ""
                    candidate["perturbation"] = label
                    candidates.append(candidate)
        return candidates

    weights = numeric_weights(strategy.get("weights", {}))
    active_factors = [factor for factor, weight in weights.items() if abs(weight) > 1e-12]
    total = sum(weights[factor] for factor in active_factors)
    if not active_factors or total <= 0:
        current = deepcopy(strategy)
        current["scenario"] = "current"
        current["factor"] = ""
        current["perturbation"] = "base"
        return [current]

    shock = float(config.get("factor_robustness", {}).get("shock", 0.25))
    candidates = []
    current = deepcopy(strategy)
    current["scenario"] = "current"
    current["factor"] = ""
    current["perturbation"] = "base"
    candidates.append(current)

    for factor in active_factors:
        dropped = deepcopy(strategy)
        dropped["name"] = f"{strategy.get('name', 'strategy')}_drop_{factor}"
        dropped["weights"] = renormalize_weights({name: (0.0 if name == factor else value) for name, value in weights.items()}, total)
        dropped["scenario"] = "drop_factor"
        dropped["factor"] = factor
        dropped["perturbation"] = "-100%"
        candidates.append(dropped)

        up = deepcopy(strategy)
        up["name"] = f"{strategy.get('name', 'strategy')}_{factor}_up"
        up_weights = deepcopy(weights)
        up_weights[factor] = up_weights[factor] * (1.0 + shock)
        up["weights"] = renormalize_weights(up_weights, total)
        up["scenario"] = "factor_up"
        up["factor"] = factor
        up["perturbation"] = f"+{int(round(shock * 100))}%"
        candidates.append(up)

        down = deepcopy(strategy)
        down["name"] = f"{strategy.get('name', 'strategy')}_{factor}_down"
        down_weights = deepcopy(weights)
        down_weights[factor] = max(0.0, down_weights[factor] * (1.0 - shock))
        down["weights"] = renormalize_weights(down_weights, total)
        down["scenario"] = "factor_down"
        down["factor"] = factor
        down["perturbation"] = f"-{int(round(shock * 100))}%"
        candidates.append(down)
    return candidates


def robustness_row(
    config: dict[str, Any],
    candidate: dict[str, Any],
    metrics: dict[str, Any],
    base_metrics: dict[str, Any],
) -> dict[str, Any]:
    excess_delta = float(metrics["excess_return"]) - float(base_metrics["excess_return"])
    oos_delta = float(metrics["out_of_sample_excess_return"]) - float(base_metrics["out_of_sample_excess_return"])
    drawdown_delta = float(metrics["max_drawdown"]) - float(base_metrics["max_drawdown"])
    passed, status, message = robustness_status(config, candidate, metrics, excess_delta, oos_delta, drawdown_delta)
    scenario = str(candidate.get("scenario", ""))
    target_type = "member" if "member" in scenario else ("factor" if "factor" in scenario else "strategy")
    target_name = candidate.get("member", "") if target_type == "member" else candidate.get("factor", "")
    return {
        "scenario": scenario,
        "target_type": target_type,
        "target_name": target_name,
        "factor": candidate.get("factor", ""),
        "perturbation": candidate.get("perturbation", ""),
        "passed": bool(passed),
        "status": status,
        "message": message,
        "score": metrics["score"],
        "strategy_return": metrics["strategy_return"],
        "benchmark_return": metrics["benchmark_return"],
        "excess_return": metrics["excess_return"],
        "max_drawdown": metrics["max_drawdown"],
        "in_sample_excess_return": metrics["in_sample_excess_return"],
        "out_of_sample_excess_return": metrics["out_of_sample_excess_return"],
        "excess_delta_vs_base": excess_delta,
        "out_of_sample_delta_vs_base": oos_delta,
        "max_drawdown_delta_vs_base": drawdown_delta,
        "anti_overfit_passed": bool(metrics["anti_overfit_passed"]),
        "overfit_penalty": metrics["overfit_penalty"],
        "stability_score": metrics["stability_score"],
        "top_n": metrics["top_n"],
        "max_stock_weight": metrics["max_stock_weight"],
        "weighting": metrics["weighting"],
        "weights": json.dumps(candidate.get("weights", {}), ensure_ascii=False, sort_keys=True),
        "ensemble_members": json.dumps(candidate.get("ensemble_members", []), ensure_ascii=False, sort_keys=True),
    }


def robustness_status(
    config: dict[str, Any],
    candidate: dict[str, Any],
    metrics: dict[str, Any],
    excess_delta: float,
    oos_delta: float,
    drawdown_delta: float,
) -> tuple[bool, str, str]:
    if candidate.get("scenario") == "current":
        passed = bool(metrics.get("anti_overfit_passed", False))
        return passed, "baseline" if passed else "review", "current_strategy_baseline"
    cfg = config.get("factor_robustness", {})
    max_total_loss = float(cfg.get("max_total_excess_loss", 0.10))
    max_oos_loss = float(cfg.get("max_oos_excess_loss", 0.08))
    max_drawdown_worsening = float(cfg.get("max_drawdown_worsening", 0.08))
    messages = []
    if excess_delta < -max_total_loss:
        messages.append("total_excess_sensitive")
    if oos_delta < -max_oos_loss:
        messages.append("out_of_sample_sensitive")
    if drawdown_delta < -max_drawdown_worsening:
        messages.append("drawdown_worse")
    if messages:
        return False, "review", ";".join(messages)
    return True, "ok", "factor_robustness_ok"


def factor_robustness_summary(config: dict[str, Any], result: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    if result.empty:
        add("factor_robustness_status", False, "missing", "non-empty", "Factor robustness result is empty.")
        return pd.DataFrame(rows, columns=FACTOR_ROBUSTNESS_SUMMARY_COLUMNS)

    stress = result[result["scenario"].astype(str) != "current"].copy()
    passed_series = stress["passed"].map(parse_bool_like) if "passed" in stress else pd.Series(dtype=bool)
    passed_count = int(passed_series.sum()) if not passed_series.empty else 0
    stress_count = int(len(stress))
    pass_ratio = float(passed_count / stress_count) if stress_count else 0.0
    cfg = config.get("factor_robustness", {})
    min_pass_ratio = float(cfg.get("min_pass_ratio", 0.60))
    max_fragile_ratio = float(cfg.get("max_fragile_scenario_ratio", max(0.0, 1.0 - min_pass_ratio)))
    max_fragile_factor_ratio = float(cfg.get("max_fragile_factor_ratio", max_fragile_ratio))
    require_zero_fragile_members = bool(cfg.get("require_zero_fragile_member_scenarios", True))
    failed = stress[~passed_series] if not stress.empty and not passed_series.empty else stress
    fragile_ratio = float(len(failed) / stress_count) if stress_count else 1.0
    worst_oos_delta = min_numeric(stress, "out_of_sample_delta_vs_base")
    worst_excess_delta = min_numeric(stress, "excess_delta_vs_base")
    worst_drawdown_delta = min_numeric(stress, "max_drawdown_delta_vs_base")
    factor_stress = stress[stress["scenario"].astype(str).isin({"drop_factor", "factor_up", "factor_down"})]
    member_stress = stress[stress["scenario"].astype(str).isin({"drop_member", "member_up", "member_down"})]
    factor_passed = factor_stress["passed"].map(parse_bool_like) if not factor_stress.empty else pd.Series(dtype=bool)
    factor_failed = factor_stress[~factor_passed] if not factor_stress.empty else factor_stress
    fragile_factor_ratio = float(len(factor_failed) / len(factor_stress)) if len(factor_stress) else 0.0
    active_factor_count = int(factor_stress["factor"].astype(str).replace("", pd.NA).dropna().nunique()) if "factor" in factor_stress else 0
    member_passed = member_stress["passed"].map(parse_bool_like) if not member_stress.empty else pd.Series(dtype=bool)
    member_failed = member_stress[~member_passed] if not member_stress.empty else member_stress
    member_pass_ratio = float(member_passed.mean()) if not member_passed.empty else 0.0
    ensemble_member_count = ensemble_member_count_from_result(result)
    baseline = result[result["scenario"].astype(str) == "current"]
    baseline_passed = len(baseline) == 1 and parse_bool_like(baseline.iloc[0].get("passed", False))
    member_resilience_passed = ensemble_member_count == 0 or (
        len(member_failed) == 0 if require_zero_fragile_members else member_pass_ratio >= min_pass_ratio
    )
    overall_passed = (
        baseline_passed
        and stress_count > 0
        and pass_ratio >= min_pass_ratio
        and fragile_ratio <= max_fragile_ratio
        and fragile_factor_ratio <= max_fragile_factor_ratio
        and member_resilience_passed
    )

    add("baseline_anti_overfit_passed", baseline_passed, baseline.iloc[0].get("status", "missing") if len(baseline) == 1 else "missing_or_duplicate", "passed baseline", "Base strategy must pass anti-overfit checks before perturbation tolerance can count.")
    add("active_factor_count", active_factor_count > 1, active_factor_count, "> 1", "A single active factor increases model concentration risk.")
    add("ensemble_member_count", ensemble_member_count == 0 or ensemble_member_count > 1, ensemble_member_count, "0 (not ensemble) or > 1", "Number of independently configured target-portfolio members.")
    add("member_stress_scenario_count", ensemble_member_count == 0 or len(member_stress) > 0, int(len(member_stress)), "> 0 when ensemble", "Number of ensemble-member dropout and blend-weight perturbation scenarios.")
    add("member_stress_pass_ratio", ensemble_member_count == 0 or member_pass_ratio >= min_pass_ratio, "not_applicable" if ensemble_member_count == 0 else round(member_pass_ratio, 4), f">= {min_pass_ratio}", "Share of member perturbations within configured robustness limits.")
    add("fragile_member_scenario_count", member_resilience_passed, int(len(member_failed)), 0 if require_zero_fragile_members else f"pass ratio >= {min_pass_ratio}", "Ensemble members are hard dependencies by default; any fragile member scenario requires review.")
    add("stress_scenario_count", stress_count > 0, stress_count, "> 0", "Number of factor and ensemble-member perturbation scenarios tested.")
    add("stress_pass_ratio", pass_ratio >= min_pass_ratio, round(pass_ratio, 4), f">= {min_pass_ratio}", "Share of perturbations within configured robustness limits.")
    add("fragile_scenario_count", fragile_ratio <= max_fragile_ratio, int(len(failed)), f"ratio <= {max_fragile_ratio}", "A bounded minority of factor shocks may fail; the configured ratio prevents a single unrealistic deletion test from vetoing an otherwise redundant strategy.")
    add("fragile_scenario_ratio", fragile_ratio <= max_fragile_ratio, round(fragile_ratio, 4), f"<= {max_fragile_ratio}", "Overall fragile perturbation ratio.")
    add("fragile_factor_scenario_ratio", fragile_factor_ratio <= max_fragile_factor_ratio, round(fragile_factor_ratio, 4), f"<= {max_fragile_factor_ratio}", "Factor-only fragility remains capped separately from ensemble-member resilience.")
    add("worst_oos_delta_vs_base", True, round(worst_oos_delta, 6), "informational", "Worst out-of-sample excess return loss versus current strategy.")
    add("worst_total_delta_vs_base", True, round(worst_excess_delta, 6), "informational", "Worst full-period excess return loss versus current strategy.")
    add("worst_drawdown_delta_vs_base", True, round(worst_drawdown_delta, 6), "informational", "Most negative max-drawdown delta versus current strategy.")
    add("factor_robustness_status", overall_passed, "passed" if overall_passed else "review_required", "passed", "Overall status requires a valid baseline, sufficient perturbation pass ratio, capped factor fragility, and resilient ensemble members.")
    return pd.DataFrame(rows, columns=FACTOR_ROBUSTNESS_SUMMARY_COLUMNS)


def ensemble_diversity_summary(config: dict[str, Any], cache: ResearchSimulationCache) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(check: str, passed: bool, value: object, threshold: object, message: str) -> None:
        rows.append({"check": check, "passed": bool(passed), "value": value, "threshold": threshold, "message": message})

    members = strategy_member_configs(config)
    if len(members) <= 1:
        add("ensemble_diversity_status", True, "not_applicable", "passed or not_applicable", "Current strategy is not a target-portfolio ensemble.")
        return pd.DataFrame(rows, columns=FACTOR_ROBUSTNESS_SUMMARY_COLUMNS)

    cfg = config.get("ensemble_diversity", {})
    lookback = max(int(cfg.get("lookback_rebalances", 12)), 1)
    min_samples = max(int(cfg.get("min_rebalances", 6)), 1)
    max_mean_overlap = float(cfg.get("max_mean_weight_overlap", 0.75))
    max_single_overlap = float(cfg.get("max_single_weight_overlap", 0.90))
    max_mean_jaccard = float(cfg.get("max_mean_holding_jaccard", 0.70))
    min_effective = float(cfg.get("min_effective_holdings", 30.0))

    raw_dates = pd.to_datetime(cache.prices.get("date", pd.Series(dtype=object)), errors="coerce").dt.date.dropna().unique().tolist()
    sample_dates = sorted(monthly_rebalance_dates(sorted(raw_dates)))[-lookback:]
    metrics: list[dict[str, float]] = []
    for asof in sample_dates:
        member_weights: list[pd.Series] = []
        for _, member_config in members:
            weights = build_target_weights(cache.scores(asof, member_config), member_config)
            total = float(weights.sum())
            member_weights.append(weights / total if total > 0 else weights)
        for left, right in combinations(member_weights, 2):
            index = left.index.union(right.index)
            left_aligned = left.reindex(index, fill_value=0.0)
            right_aligned = right.reindex(index, fill_value=0.0)
            left_symbols = set(left_aligned[left_aligned > 0].index)
            right_symbols = set(right_aligned[right_aligned > 0].index)
            union = left_symbols | right_symbols
            combined = left_aligned.add(right_aligned, fill_value=0.0) / 2.0
            denominator = float((combined**2).sum())
            metrics.append(
                {
                    "weighted_overlap": float(pd.concat([left_aligned, right_aligned], axis=1).min(axis=1).sum()),
                    "holding_jaccard": float(len(left_symbols & right_symbols) / len(union)) if union else 1.0,
                    "effective_holdings": float(1.0 / denominator) if denominator > 0 else 0.0,
                }
            )

    frame = pd.DataFrame(metrics)
    sample_count = len(sample_dates) if not frame.empty else 0
    mean_overlap = float(frame["weighted_overlap"].mean()) if not frame.empty else 1.0
    peak_overlap = float(frame["weighted_overlap"].max()) if not frame.empty else 1.0
    mean_jaccard = float(frame["holding_jaccard"].mean()) if not frame.empty else 1.0
    minimum_effective = float(frame["effective_holdings"].min()) if not frame.empty else 0.0
    checks = [
        sample_count >= min_samples,
        mean_overlap <= max_mean_overlap,
        peak_overlap <= max_single_overlap,
        mean_jaccard <= max_mean_jaccard,
        minimum_effective >= min_effective,
    ]
    add("ensemble_diversity_sample_count", checks[0], sample_count, f">= {min_samples}", "Monthly rebalance dates sampled for member diversity.")
    add("mean_member_weight_overlap", checks[1], round(mean_overlap, 6), f"<= {max_mean_overlap}", "Mean pairwise overlap measured as the shared target-weight mass.")
    add("max_member_weight_overlap", checks[2], round(peak_overlap, 6), f"<= {max_single_overlap}", "Worst sampled pairwise target-weight overlap.")
    add("mean_member_holding_jaccard", checks[3], round(mean_jaccard, 6), f"<= {max_mean_jaccard}", "Mean pairwise holding-set Jaccard similarity.")
    add("min_combined_effective_holdings", checks[4], round(minimum_effective, 4), f">= {min_effective}", "Lowest inverse-Herfindahl effective holding count after equal member blending.")
    add("ensemble_diversity_status", all(checks), "passed" if all(checks) else "review_required", "passed or not_applicable", "Target-portfolio members must remain meaningfully distinct across recent rebalances.")
    return pd.DataFrame(rows, columns=FACTOR_ROBUSTNESS_SUMMARY_COLUMNS)


def perturb_ensemble_factor(strategy: dict[str, Any], factor: str, multiplier: float) -> dict[str, Any]:
    candidate = deepcopy(strategy)
    members = candidate.get("ensemble_members", [])
    for member in members:
        if not isinstance(member, dict):
            continue
        weights = numeric_weights(member.get("weights", {}))
        if factor not in weights or abs(weights[factor]) <= 1e-12:
            continue
        target_sum = sum(value for value in weights.values() if value > 0)
        weights[factor] = max(0.0, weights[factor] * multiplier)
        member["weights"] = renormalize_weights(weights, target_sum)
    return candidate


def perturb_ensemble_member(strategy: dict[str, Any], member_index: int, multiplier: float) -> dict[str, Any]:
    candidate = deepcopy(strategy)
    members = candidate.get("ensemble_members", [])
    if not isinstance(members, list) or member_index < 0 or member_index >= len(members):
        return candidate
    member = members[member_index]
    if isinstance(member, dict):
        current = max(float(member.get("blend_weight", 0.0)), 0.0)
        member["blend_weight"] = max(0.0, current * multiplier)
    return candidate


def ensemble_member_count_from_result(result: pd.DataFrame) -> int:
    if result.empty or "ensemble_members" not in result:
        return 0
    current = result[result["scenario"].astype(str) == "current"]
    if current.empty:
        return 0
    try:
        members = json.loads(str(current["ensemble_members"].iloc[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    return len([member for member in members if isinstance(member, dict) and float(member.get("blend_weight", 0.0)) > 0]) if isinstance(members, list) else 0


def numeric_weights(weights: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in weights.items():
        parsed = pd.to_numeric(value, errors="coerce")
        out[str(name)] = 0.0 if pd.isna(parsed) else float(parsed)
    return out


def renormalize_weights(weights: dict[str, float], target_sum: float) -> dict[str, float]:
    positive = {name: max(0.0, float(value)) for name, value in weights.items()}
    current_sum = sum(positive.values())
    if current_sum <= 0:
        return positive
    scale = target_sum / current_sum
    return {name: round(value * scale, 10) for name, value in positive.items()}


def min_numeric(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.min()) if not values.empty else 0.0


def parse_bool_like(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}
