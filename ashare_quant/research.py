from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import build_run_manifest, frame_sha256
from .candidate import QUARANTINE_STATUSES, live_strategy_hash, load_candidate_failure_history
from .config import load_config, project_path
from .data import DataStore, make_proxy_benchmark
from .factors import build_target_weights, compute_factor_values, effective_factor_weights, score_factor_values, strategy_member_configs
from .fundamental_policy import assert_fundamental_factor_policy
from .risk import (
    benchmark_exposure_multiplier,
    combine_exposure_multipliers,
    market_breadth_exposure_multiplier,
)
from .rules import configured_rebalance_dates
from .universe import eligible_universe
from .validation import filter_complete_market_data, max_lookback_days, validate_config, validate_market_data
from .walkforward import apply_candidate, apply_portfolio_returns, estimate_rebalance_cost, quantize_rebalance_target, session_return_matrices, simulate_period
from .research_governance import (
    assert_research_candidates_allowed,
    gate_snapshot,
    write_research_gate,
)
from .selection_bias import selection_bias_manifest_rows, write_strategy_selection_bias


RESEARCH_COLUMNS = [
    "rank",
    "name",
    "research_origin",
    "source_config",
    "research_only",
    "experiment_status",
    "experiment_reason",
    "config_overrides",
    "strategy_hash",
    "score",
    "strategy_return",
    "gross_strategy_return",
    "transaction_cost_drag",
    "estimated_trading_cost",
    "annualized_turnover",
    "rebalance_count",
    "benchmark_return",
    "excess_return",
    "max_drawdown",
    "in_sample_excess_return",
    "out_of_sample_excess_return",
    "recent_excess_concentration",
    "annual_positive_ratio",
    "time_fold_count",
    "time_fold_positive_ratio",
    "time_fold_worst_excess_return",
    "time_fold_median_excess_return",
    "time_fold_rank_mean",
    "time_fold_rank_std",
    "time_fold_top_half_ratio",
    "time_fold_stability_passed",
    "time_fold_excess_returns",
    "anti_overfit_passed",
    "overfit_penalty",
    "stability_score",
    "out_of_sample_strategy_return",
    "out_of_sample_benchmark_return",
    "top_n",
    "max_stock_weight",
    "weighting",
    "weights",
    "adaptive_weights",
    "industry_neutralization",
    "cross_sectional_neutralization",
    "industry_relative_cap",
    "ensemble_members",
]

BASELINE_COLUMNS = [
    "rank",
    "baseline_type",
    "name",
    "strategy_hash",
    "description",
    "score",
    "strategy_return",
    "gross_strategy_return",
    "transaction_cost_drag",
    "estimated_trading_cost",
    "annualized_turnover",
    "rebalance_count",
    "benchmark_return",
    "excess_return",
    "max_drawdown",
    "in_sample_excess_return",
    "out_of_sample_excess_return",
    "recent_excess_concentration",
    "annual_positive_ratio",
    "time_fold_count",
    "time_fold_positive_ratio",
    "time_fold_worst_excess_return",
    "time_fold_median_excess_return",
    "time_fold_rank_mean",
    "time_fold_rank_std",
    "time_fold_top_half_ratio",
    "time_fold_stability_passed",
    "time_fold_excess_returns",
    "anti_overfit_passed",
    "overfit_penalty",
    "stability_score",
    "out_of_sample_strategy_return",
    "out_of_sample_benchmark_return",
    "top_n",
    "max_stock_weight",
    "weighting",
    "weights",
    "adaptive_weights",
    "industry_neutralization",
    "cross_sectional_neutralization",
    "industry_relative_cap",
    "ensemble_members",
]


def run_strategy_research(config: dict[str, Any]) -> Path:
    configured_candidates = candidate_list(config)
    snapshot = gate_snapshot(config, configured_candidates)
    blocked_names = (
        set(snapshot["new_candidates"])
        if snapshot["status"] == "stop_new_candidates"
        else set()
    )
    candidates = [
        candidate
        for candidate in configured_candidates
        if str(candidate.get("name", "candidate")) not in blocked_names
    ]
    if not candidates:
        assert_research_candidates_allowed(config, configured_candidates)
    else:
        assert_research_candidates_allowed(config, candidates)
    result = evaluate_candidates(config, candidates)
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "strategy_research.csv"
    write_ranked_result(result, path, RESEARCH_COLUMNS)
    refresh_strategy_research_metadata(config, path)
    write_strategy_selection_bias(config, path)
    store = DataStore.from_config(config)
    write_strategy_research_manifest(config, store, path)
    write_research_gate(config, candidates, commit=True)
    if blocked_names:
        write_research_gate(config, configured_candidates, commit=False)
    store.log_run(
        "research",
        "ok",
        f"evaluated {len(result)} candidates; blocked_new={len(blocked_names)}",
    )
    return path

def refresh_strategy_research_metadata(config: dict[str, Any], research_path: Path | None = None) -> Path | None:
    if research_path is None:
        validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
        configured = str(validation.get("strategy_research_path", "") or "").strip()
        output_dir = project_path(config["paths"]["output_dir"])
        research_path = project_path(configured) if configured else output_dir / "strategy_research.csv"
    research_path = Path(research_path)
    if not research_path.exists():
        return None

    research = pd.read_csv(research_path, encoding="utf-8-sig")
    if research.empty or "name" not in research.columns:
        return research_path
    candidates = {str(candidate.get("name", "")): candidate for candidate in candidate_list(config)}
    metadata_fields = (
        "research_origin",
        "source_config",
        "research_only",
        "experiment_status",
        "experiment_reason",
    )
    for field in metadata_fields:
        if field in research.columns:
            research[field] = research[field].astype(object)
    changed = False
    for index, row in research.iterrows():
        candidate = candidates.get(str(row.get("name", "")))
        if candidate is None:
            continue
        for field in metadata_fields:
            if field in research.columns:
                current = research.at[index, field]
                desired = candidate.get(field, current)
                if field == "research_only":
                    current_text = str(current).strip().lower()
                    same = current_text in {"true", "false"} and (current_text == str(bool(desired)).lower())
                else:
                    same = (pd.isna(current) and pd.isna(desired)) or str(current) == str(desired)
                if not same:
                    research.at[index, field] = desired
                    changed = True
        if "config_overrides" in research.columns:
            desired_overrides = json.dumps(
                candidate.get("config_overrides", {}), ensure_ascii=False, sort_keys=True
            )
            if str(research.at[index, "config_overrides"]) != desired_overrides:
                research.at[index, "config_overrides"] = desired_overrides
                changed = True

    failures = load_candidate_failure_history(config)
    if not failures.empty and "strategy_hash" in research.columns:
        quarantined = failures[
            failures["status"].fillna("").astype(str).isin(QUARANTINE_STATUSES)
        ].copy()
        quarantined = quarantined.drop_duplicates(subset=["strategy_hash"], keep="last")
        failure_by_hash = {
            str(row.get("strategy_hash", "") or ""): row
            for row in quarantined.to_dict(orient="records")
            if str(row.get("strategy_hash", "") or "")
        }
        for index, row in research.iterrows():
            failure = failure_by_hash.get(str(row.get("strategy_hash", "") or ""))
            if failure is None:
                continue
            status = str(failure.get("status", "") or "forward_failure")
            days = str(failure.get("observed_forward_trading_days", "") or "unknown")
            excess = str(failure.get("forward_excess_return", "") or "unknown")
            reason = f"Forward quarantine: status={status}; observed_days={days}; excess_return={excess}"
            if "experiment_status" in research.columns and str(research.at[index, "experiment_status"]) != "quarantined_forward_failure":
                research.at[index, "experiment_status"] = "quarantined_forward_failure"
                changed = True
            if "experiment_reason" in research.columns and str(research.at[index, "experiment_reason"]) != reason:
                research.at[index, "experiment_reason"] = reason
                changed = True

    if changed:
        research.to_csv(research_path, index=False, encoding="utf-8-sig")
    # Metadata-only refreshes must not re-sign stale numerical evidence against
    # the current cache. A full research run owns selection-bias and manifest
    # regeneration; until then the artifact hash mismatch remains visible.
    write_research_gate(config, candidate_list(config), commit=False)
    return research_path


def write_strategy_research_manifest(config: dict[str, Any], store: DataStore, research_path: Path) -> Path:
    stocks, prices, fundamentals, benchmark = load_research_market_data(config, store)
    manifest = build_run_manifest(config, stocks, prices, fundamentals, benchmark)
    research = pd.read_csv(research_path, encoding="utf-8-sig")
    artifact_rows = pd.DataFrame(
        [
            {"item": "strategy_research_rows", "value": str(len(research))},
            {"item": "strategy_research_sha256", "value": frame_sha256(research)},
        ]
    )
    selection_rows = pd.DataFrame(selection_bias_manifest_rows(research_path.parent))
    manifest = pd.concat([manifest, artifact_rows, selection_rows], ignore_index=True)
    path = research_path.parent / "strategy_research_manifest.csv"
    manifest.to_csv(path, index=False, encoding="utf-8-sig")
    return path

def run_strategy_baselines(config: dict[str, Any]) -> Path:
    result = evaluate_candidates(config, baseline_candidate_list(config))
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "strategy_baselines.csv"
    write_ranked_result(result, path, BASELINE_COLUMNS)
    DataStore.from_config(config).log_run("baselines", "ok", f"evaluated {len(result)} reference baselines")
    return path


def load_research_market_data(
    config: dict[str, Any],
    store: DataStore,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stocks, prices, fundamentals = store.load_market_data()
    data_errors = validate_market_data(stocks, prices, fundamentals)
    if data_errors:
        raise ValueError("; ".join(data_errors))

    benchmark = store.load_benchmark(required=False)
    start = pd.to_datetime(config["backtest"]["start"]).date()
    end_value = config["backtest"].get("end")
    end = pd.to_datetime(end_value).date() if end_value else max(prices["date"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()
    if benchmark.empty:
        benchmark = make_proxy_benchmark(prices, config)
    benchmark = benchmark[(benchmark["date"] >= start) & (benchmark["date"] <= end)].copy()
    prices, benchmark = filter_complete_market_data(prices, benchmark, config)
    return stocks, prices, fundamentals, benchmark

def evaluate_candidates(config: dict[str, Any], candidates: list[dict[str, Any]]) -> pd.DataFrame:
    errors = validate_config(config)
    if errors:
        raise ValueError("; ".join(errors))

    store = DataStore.from_config(config)
    stocks, prices, fundamentals, benchmark = load_research_market_data(config, store)
    simulation_cache = ResearchSimulationCache(prices, stocks, fundamentals, benchmark)
    rows = []
    for candidate in candidates:
        row = evaluate_candidate(prices, stocks, fundamentals, benchmark, config, candidate, simulation_cache=simulation_cache)
        row["baseline_type"] = str(candidate.get("baseline_type", "candidate"))
        row["description"] = str(candidate.get("description", ""))
        row["research_origin"] = str(candidate.get("research_origin", "configured"))
        row["source_config"] = str(candidate.get("source_config", ""))
        row["research_only"] = bool(candidate.get("research_only", False))
        row["experiment_status"] = str(candidate.get("experiment_status", "evaluating"))
        row["experiment_reason"] = str(candidate.get("experiment_reason", ""))
        row["config_overrides"] = json.dumps(candidate.get("config_overrides", {}), ensure_ascii=False, sort_keys=True)
        rows.append(row)
        print(
            f"{row['name']}: score={row['score']:.4f}, excess={row['excess_return']:.2%}, "
            f"in={row['in_sample_excess_return']:.2%}, oos={row['out_of_sample_excess_return']:.2%}, "
            f"stable={row['anti_overfit_passed']}, maxDD={row['max_drawdown']:.2%}"
        )

    return add_temporal_rank_stability(pd.DataFrame(rows), config)


def write_ranked_result(result: pd.DataFrame, path: Path, columns: list[str]) -> None:
    if result.empty:
        result = pd.DataFrame(columns=columns)
    else:
        result = result.sort_values(
            ["time_fold_stability_passed", "anti_overfit_passed", "score", "stability_score", "out_of_sample_excess_return", "excess_return"],
            ascending=[False, False, False, False, False, False],
        ).reset_index(drop=True)
        result.insert(0, "rank", result.index + 1)
        for column in columns:
            if column not in result:
                result[column] = ""
        result = result[columns]
    result.to_csv(path, index=False, encoding="utf-8-sig")


def candidate_list(config: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    current = deepcopy(config.get("strategy", {}))
    current["name"] = current.get("name", "current_strategy")
    current["research_origin"] = "formal"
    current["source_config"] = "active_config"
    current["research_only"] = False
    current["experiment_status"] = "formal"
    candidates.append(current)

    for configured in deepcopy(config.get("walk_forward", {}).get("candidates", [])):
        configured.setdefault("research_origin", "walk_forward")
        configured.setdefault("source_config", "configs/default.yaml")
        configured.setdefault("research_only", False)
        configured.setdefault("experiment_status", "evaluating")
        candidates.append(configured)
    candidates.extend(archived_candidate_list(config))

    deduped: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    metadata_fields = (
        "source_config",
        "research_only",
        "experiment_status",
        "experiment_reason",
        "config_overrides",
    )
    for candidate in candidates:
        name = str(candidate.get("name", "candidate"))
        existing = by_name.get(name)
        if existing is not None:
            if candidate.get("research_origin") == "archive_config":
                for field in metadata_fields:
                    value = candidate.get(field)
                    if value is not None and value != "":
                        existing[field] = deepcopy(value)
                existing["research_origin"] = f"{existing.get('research_origin', 'configured')}+archive"
            continue
        by_name[name] = candidate
        deduped.append(candidate)
    return deduped


def archived_candidate_list(config: dict[str, Any]) -> list[dict[str, Any]]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    configured_paths = validation.get("research_archive_configs", [])
    if not isinstance(configured_paths, list):
        raise ValueError("validation.research_archive_configs must be a list")

    archived: list[dict[str, Any]] = []
    for configured in configured_paths:
        source = str(configured)
        candidate_config = load_config(source)
        strategy = deepcopy(candidate_config.get("strategy", {}))
        if not strategy:
            raise ValueError(f"archived research config has no strategy: {source}")
        candidate_validation = (
            candidate_config.get("validation", {})
            if isinstance(candidate_config.get("validation"), dict)
            else {}
        )
        research_only = bool(candidate_validation.get("research_only", False))
        strategy["research_origin"] = "archive_config"
        strategy["source_config"] = source
        strategy["research_only"] = research_only
        strategy["experiment_status"] = str(
            candidate_validation.get("experiment_status", "research_only" if research_only else "evaluating")
        )
        strategy["experiment_reason"] = str(
            candidate_validation.get("rejection_reason")
            or candidate_validation.get("experiment_reason")
            or candidate_validation.get("research_rationale")
            or ""
        )
        overrides = {
            section: deepcopy(candidate_config.get(section) or {})
            for section in ("risk_overlay", "breadth_overlay")
            if candidate_config.get(section) != config.get(section)
        }
        candidate_rebalance = str(candidate_config.get("backtest", {}).get("rebalance", "monthly"))
        base_rebalance = str(config.get("backtest", {}).get("rebalance", "monthly"))
        if candidate_rebalance != base_rebalance:
            overrides["backtest"] = {"rebalance": candidate_rebalance}
        if overrides:
            strategy["config_overrides"] = overrides
        archived.append(strategy)
    return archived


def baseline_candidate_list(config: dict[str, Any]) -> list[dict[str, Any]]:
    base = deepcopy(config.get("strategy", {}))
    base_name = str(base.get("name", "current_strategy"))
    top_n = int(base.get("top_n", 100))
    max_weight = float(base.get("max_stock_weight", 0.05))
    max_industry = float(base.get("max_industry_weight", 0.30))
    lookbacks = deepcopy(base.get("lookbacks", {}))

    def make(
        name: str,
        baseline_type: str,
        description: str,
        weights: dict[str, float],
        weighting: str = "liquidity_sqrt",
    ) -> dict[str, Any]:
        return {
            "name": name,
            "baseline_type": baseline_type,
            "description": description,
            "top_n": top_n,
            "max_stock_weight": max_weight,
            "max_industry_weight": max_industry,
            "weighting": weighting,
            "lookbacks": deepcopy(lookbacks),
            "weights": weights,
        }

    current = deepcopy(base)
    current["name"] = base_name
    current["baseline_type"] = "formal"
    current["description"] = "当前正式策略。"

    return [
        current,
        make("baseline_liquidity_equal", "common_factor", "高流动性股票等权。", {"liquidity": 1.0}, "equal"),
        make("baseline_liquidity_sqrt", "common_factor", "高流动性股票按流动性平方根加权。", {"liquidity": 1.0}),
        make("baseline_low_volatility", "common_factor", "低波动 Smart Beta 近似。", {"low_volatility": 1.0}),
        make("baseline_momentum_60", "common_factor", "60 日动量排序。", {"momentum": 1.0}, "equal"),
        make("baseline_long_momentum_120", "common_factor", "120 日动量排序。", {"long_momentum": 1.0}, "equal"),
        make("baseline_low_drawdown", "common_factor", "低回撤防御型排序。", {"low_drawdown": 1.0}),
        make(
            "baseline_defensive_liquidity",
            "common_blend",
            "低波动、低回撤和流动性混合。",
            {"low_volatility": 0.50, "low_drawdown": 0.20, "liquidity": 0.30},
        ),
        make(
            "baseline_conservative_proxy",
            "common_blend",
            "保守公式近似：动量、低波动和流动性混合；因缺少分红回购数据，未包含股东收益率。",
            {"momentum": 0.25, "low_volatility": 0.45, "liquidity": 0.30},
        ),
        make(
            "baseline_balanced_mlv",
            "common_blend",
            "动量、低波动和流动性均衡混合。",
            {"momentum": 0.33, "low_volatility": 0.34, "liquidity": 0.33},
        ),
    ]


def evaluate_candidate(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    base_config: dict[str, Any],
    candidate: dict[str, Any],
    simulation_cache: "ResearchSimulationCache | None" = None,
) -> dict[str, Any]:
    candidate_config = apply_candidate(base_config, candidate)
    dates = sorted(prices["date"].unique())
    warmup = max_lookback_days(candidate_config)
    usable_dates = dates[warmup:] if len(dates) > warmup else dates
    split_ratio = float(base_config.get("validation", {}).get("holdout_split_ratio", 0.7))
    split_idx = max(1, min(len(usable_dates) - 1, int(len(usable_dates) * split_ratio))) if len(usable_dates) > 1 else 1
    train_dates = usable_dates[:split_idx]
    test_dates = usable_dates[split_idx:]

    simulate = simulation_cache.simulate if simulation_cache is not None else None
    full = simulate(candidate_config, usable_dates) if simulate is not None else simulate_period(prices, stocks, fundamentals, benchmark, candidate_config, usable_dates)
    train = simulate(candidate_config, train_dates) if simulate is not None and train_dates else simulate_period(prices, stocks, fundamentals, benchmark, candidate_config, train_dates) if train_dates else zero_result()
    test = simulate(candidate_config, test_dates) if simulate is not None and test_dates else simulate_period(prices, stocks, fundamentals, benchmark, candidate_config, test_dates) if test_dates else zero_result()
    annual_positive = annual_positive_ratio(prices, stocks, fundamentals, benchmark, candidate_config, usable_dates, simulation_cache=simulation_cache)
    fold_excess = temporal_fold_excess_returns(
        prices,
        stocks,
        fundamentals,
        benchmark,
        candidate_config,
        usable_dates,
        base_config,
        simulation_cache=simulation_cache,
    )
    concentration = recent_excess_concentration(full["excess_return"], test["excess_return"])
    overfit = overfit_penalty(base_config, train, test, full, concentration, annual_positive)
    stability = stability_score(full, train, test, annual_positive, concentration)
    score = composite_score(full, train, test, stability, overfit)
    strategy = candidate_config["strategy"]
    return {
        "name": str(strategy.get("name", "candidate")),
        "strategy_hash": live_strategy_hash(candidate_config),
        "score": score,
        "strategy_return": full["strategy_return"],
        "gross_strategy_return": full["gross_strategy_return"],
        "transaction_cost_drag": full["transaction_cost_drag"],
        "estimated_trading_cost": full["estimated_trading_cost"],
        "annualized_turnover": full["annualized_turnover"],
        "rebalance_count": full["rebalance_count"],
        "benchmark_return": full["benchmark_return"],
        "excess_return": full["excess_return"],
        "max_drawdown": full["max_drawdown"],
        "in_sample_excess_return": train["excess_return"],
        "out_of_sample_excess_return": test["excess_return"],
        "recent_excess_concentration": concentration,
        "annual_positive_ratio": annual_positive,
        "_time_fold_excess": fold_excess,
        "time_fold_count": len(fold_excess),
        "time_fold_excess_returns": json.dumps(fold_excess),
        "anti_overfit_passed": overfit <= 0.0,
        "overfit_penalty": overfit,
        "stability_score": stability,
        "out_of_sample_strategy_return": test["strategy_return"],
        "out_of_sample_benchmark_return": test["benchmark_return"],
        "top_n": int(strategy.get("top_n", 0)),
        "max_stock_weight": float(strategy.get("max_stock_weight", 0.0)),
        "weighting": str(strategy.get("weighting", "equal")),
        "weights": json.dumps(strategy.get("weights", {}), ensure_ascii=False, sort_keys=True),
        "industry_neutralization": json.dumps(strategy.get("industry_neutralization", {}), ensure_ascii=False, sort_keys=True),
        "cross_sectional_neutralization": json.dumps(strategy.get("cross_sectional_neutralization", {}), ensure_ascii=False, sort_keys=True),
        "industry_relative_cap": json.dumps(strategy.get("industry_relative_cap", {}), ensure_ascii=False, sort_keys=True),
        "adaptive_weights": json.dumps(strategy.get("adaptive_weights", {}), ensure_ascii=False, sort_keys=True),
        "ensemble_members": json.dumps(strategy.get("ensemble_members", []), ensure_ascii=False, sort_keys=True),
    }


def temporal_fold_excess_returns(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    candidate_config: dict[str, Any],
    dates: list,
    research_config: dict[str, Any],
    *,
    simulation_cache: "ResearchSimulationCache | None" = None,
) -> list[float]:
    validation = research_config.get("validation", {})
    requested = max(int(validation.get("research_time_folds", 4)), 2)
    min_days = max(int(validation.get("research_min_fold_trading_days", 60)), 20)
    available = len(dates) // min_days
    fold_count = min(requested, available)
    if fold_count < 2:
        return []
    base_size, remainder = divmod(len(dates), fold_count)
    folds: list[list] = []
    offset = 0
    for index in range(fold_count):
        size = base_size + (1 if index < remainder else 0)
        folds.append(dates[offset : offset + size])
        offset += size
    values: list[float] = []
    for fold_dates in folds:
        if simulation_cache is not None:
            result = simulation_cache.simulate(candidate_config, fold_dates)
        else:
            result = simulate_period(prices, stocks, fundamentals, benchmark, candidate_config, fold_dates)
        values.append(float(result["excess_return"]))
    return values


def add_temporal_rank_stability(result: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    if result.empty:
        return result
    parsed = [list(value) if isinstance(value, list) else [] for value in result.get("_time_fold_excess", pd.Series([[]] * len(result)))]
    fold_count = min((len(values) for values in parsed), default=0)
    validation = config.get("validation", {})
    min_positive = float(validation.get("min_time_fold_positive_ratio", 0.50))
    min_top_half = float(validation.get("min_time_fold_top_half_ratio", 0.50))
    min_worst = float(validation.get("min_time_fold_worst_excess_return", -0.15))
    if fold_count < 2:
        result["time_fold_count"] = [len(values) for values in parsed]
        result["time_fold_positive_ratio"] = 0.0
        result["time_fold_worst_excess_return"] = 0.0
        result["time_fold_median_excess_return"] = 0.0
        result["time_fold_rank_mean"] = float(len(result))
        result["time_fold_rank_std"] = 0.0
        result["time_fold_top_half_ratio"] = 0.0
        result["time_fold_stability_passed"] = False
        result["anti_overfit_passed"] = False
        return result

    matrix = pd.DataFrame([values[:fold_count] for values in parsed], index=result.index)
    ranks = matrix.rank(axis=0, method="average", ascending=False)
    top_half_cutoff = max((len(result) + 1) // 2, 1)
    positive_ratio = (matrix > 0).mean(axis=1)
    worst = matrix.min(axis=1)
    median = matrix.median(axis=1)
    rank_mean = ranks.mean(axis=1)
    rank_std = ranks.std(axis=1, ddof=0)
    top_half = (ranks <= top_half_cutoff).mean(axis=1)
    stable = (positive_ratio >= min_positive) & (top_half >= min_top_half) & (worst >= min_worst)
    result["time_fold_count"] = fold_count
    result["time_fold_positive_ratio"] = positive_ratio
    result["time_fold_worst_excess_return"] = worst
    result["time_fold_median_excess_return"] = median
    result["time_fold_rank_mean"] = rank_mean
    result["time_fold_rank_std"] = rank_std
    result["time_fold_top_half_ratio"] = top_half
    result["time_fold_stability_passed"] = stable.astype(bool)
    result["time_fold_excess_returns"] = [json.dumps(values[:fold_count]) for values in parsed]
    original = result["anti_overfit_passed"].map(lambda value: str(value).strip().lower() in {"true", "1", "yes"})
    result["anti_overfit_passed"] = original & stable
    return result


def composite_score(
    full: dict[str, float],
    train: dict[str, float],
    test: dict[str, float],
    stability: float,
    penalty: float,
) -> float:
    # Ranking score only. Keep units close to return space and penalize unstable evidence.
    return float(
        0.60 * full["excess_return"]
        + 0.45 * test["excess_return"]
        + 0.35 * train["excess_return"]
        + stability
        + 0.25 * full["max_drawdown"]
        - penalty
    )


def stability_score(
    full: dict[str, float],
    train: dict[str, float],
    test: dict[str, float],
    annual_positive: float,
    concentration: float,
) -> float:
    balance = min(train["excess_return"], test["excess_return"], full["excess_return"])
    concentration_bonus = 0.0 if concentration > 1.0 else 0.02 * (1.0 - concentration)
    return float(0.40 * balance + 0.05 * annual_positive + concentration_bonus)


def overfit_penalty(
    config: dict[str, Any],
    train: dict[str, float],
    test: dict[str, float],
    full: dict[str, float],
    concentration: float,
    annual_positive: float,
) -> float:
    validation = config.get("validation", {})
    min_in = float(validation.get("min_in_sample_excess_return", 0.0))
    min_out = float(validation.get("min_out_of_sample_excess_return", 0.0))
    max_concentration = float(validation.get("max_recent_excess_concentration", 0.85))
    min_annual = float(validation.get("min_annual_positive_ratio", 0.5))
    penalty = 0.0
    penalty += max(0.0, min_in - train["excess_return"])
    penalty += max(0.0, min_out - test["excess_return"])
    penalty += max(0.0, -full["excess_return"])
    penalty += max(0.0, concentration - max_concentration) * 0.10
    penalty += max(0.0, min_annual - annual_positive) * 0.05
    return float(penalty)


def recent_excess_concentration(full_excess: float, test_excess: float) -> float:
    if full_excess <= 0 or test_excess <= 0:
        return 0.0
    return float(test_excess / full_excess)


def annual_positive_ratio(
    prices: pd.DataFrame,
    stocks: pd.DataFrame,
    fundamentals: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
    dates: list,
    simulation_cache: "ResearchSimulationCache | None" = None,
) -> float:
    if not dates:
        return 0.0
    by_year: dict[int, list] = {}
    for dt in dates:
        year = pd.to_datetime(dt).year
        by_year.setdefault(year, []).append(dt)
    results = []
    for year_dates in by_year.values():
        if len(year_dates) < 20:
            continue
        if simulation_cache is not None:
            result = simulation_cache.simulate(config, year_dates)
        else:
            result = simulate_period(prices, stocks, fundamentals, benchmark, config, year_dates)
        results.append(result["excess_return"])
    if not results:
        return 0.0
    return float(sum(1 for value in results if value > 0) / len(results))


def zero_result() -> dict[str, float]:
    return {
        "strategy_return": 0.0,
        "gross_strategy_return": 0.0,
        "benchmark_return": 0.0,
        "excess_return": 0.0,
        "max_drawdown": 0.0,
        "transaction_cost_drag": 0.0,
        "estimated_trading_cost": 0.0,
        "annualized_turnover": 0.0,
        "rebalance_count": 0.0,
    }


class ResearchSimulationCache:
    def __init__(
        self,
        prices: pd.DataFrame,
        stocks: pd.DataFrame,
        fundamentals: pd.DataFrame,
        benchmark: pd.DataFrame,
    ) -> None:
        self.prices = prices
        self.stocks = stocks
        self.fundamentals = fundamentals
        self.benchmark = benchmark
        self.overnight_returns, self.intraday_returns = session_return_matrices(prices)
        self.close_prices = prices.pivot(index="date", columns="symbol", values="close").sort_index()
        self.factor_cache: dict[tuple[object, str], pd.DataFrame] = {}
        self.universe_cache: dict[tuple[object, str], pd.DataFrame] = {}

    def simulate(self, config: dict[str, Any], dates: list) -> dict[str, float]:
        if not dates:
            return zero_result()
        period_prices = self.prices[self.prices["date"].isin(dates)]
        if period_prices.empty:
            return zero_result()
        rebalance_dates = configured_rebalance_dates(dates, config.get("backtest", {}).get("rebalance", "monthly"))
        pending_targets: dict[object, tuple[pd.Series, float, float]] = {}
        current_weights = pd.Series(dtype=float)
        nav = []
        value = 1.0
        gross_value = 1.0
        cumulative_cost = 0.0
        total_turnover = 0.0
        rebalance_count = 0
        for idx, dt in enumerate(dates):
            if idx > 0 and not current_weights.empty and dt in self.overnight_returns.index:
                period_return, current_weights = apply_portfolio_returns(current_weights, self.overnight_returns.loc[dt])
                value *= 1 + period_return
                gross_value *= 1 + period_return
            if dt in pending_targets:
                weights, cost_fraction, turnover = pending_targets.pop(dt)
                value *= 1.0 - cost_fraction
                cumulative_cost += cost_fraction * value / max(1.0 - cost_fraction, 1e-12)
                total_turnover += turnover
                rebalance_count += 1
                current_weights = weights
            if not current_weights.empty and dt in self.intraday_returns.index:
                period_return, current_weights = apply_portfolio_returns(current_weights, self.intraday_returns.loc[dt])
                value *= 1 + period_return
                gross_value *= 1 + period_return
            nav.append({"date": dt, "strategy_nav": value, "gross_strategy_nav": gross_value})
            if dt in rebalance_dates and idx < len(dates) - 1:
                weights = self.target_weights(dt, config)
                exposure = combine_exposure_multipliers(
                    benchmark_exposure_multiplier(self.benchmark, dt, config),
                    market_breadth_exposure_multiplier(self.prices, dt, config),
                    config,
                )
                weights = weights * exposure
                reserve = max(0.0, min(1.0, float(config.get("trading", {}).get("reserve_cash_ratio", 0.0))))
                weights = weights * (1.0 - reserve)
                signal_prices = (
                    self.close_prices.loc[dt]
                    if dt in self.close_prices.index
                    else pd.Series(dtype=float)
                )
                weights = quantize_rebalance_target(
                    current_weights, weights, value, signal_prices, config
                )
                planned_cost, planned_turnover = estimate_rebalance_cost(current_weights, weights, value, config)
                pending_targets[dates[idx + 1]] = (weights, planned_cost, planned_turnover)

        strategy = pd.DataFrame(nav)
        benchmark_slice = self.benchmark[self.benchmark["date"].isin(dates)].sort_values("date")
        merged = strategy.merge(benchmark_slice[["date", "close"]], how="inner", on="date")
        if merged.empty:
            return zero_result()
        merged["strategy_nav"] = merged["strategy_nav"] / merged["strategy_nav"].iloc[0]
        merged["gross_strategy_nav"] = merged["gross_strategy_nav"] / merged["gross_strategy_nav"].iloc[0]
        merged["benchmark_nav"] = merged["close"] / merged["close"].iloc[0]
        drawdown = merged["strategy_nav"] / merged["strategy_nav"].cummax() - 1
        strategy_return = float(merged["strategy_nav"].iloc[-1] - 1)
        gross_return = float(merged["gross_strategy_nav"].iloc[-1] - 1)
        benchmark_return = float(merged["benchmark_nav"].iloc[-1] - 1)
        return {
            "strategy_return": strategy_return,
            "gross_strategy_return": gross_return,
            "benchmark_return": benchmark_return,
            "excess_return": strategy_return - benchmark_return,
            "max_drawdown": float(drawdown.min()),
            "transaction_cost_drag": gross_return - strategy_return,
            "estimated_trading_cost": cumulative_cost,
            "annualized_turnover": total_turnover * 252.0 / max(len(dates) - 1, 1),
            "rebalance_count": float(rebalance_count),
        }


    def target_weights(self, asof: object, config: dict[str, Any]) -> pd.Series:
        combined = pd.Series(dtype=float)
        for blend_weight, member_config in strategy_member_configs(config):
            scores = self.scores(asof, member_config)
            weights = build_target_weights(scores, member_config)
            combined = combined.add(weights * blend_weight, fill_value=0.0)
        max_weight = float(config.get("strategy", {}).get("max_stock_weight", 1.0))
        from .factors import cap_and_normalize

        return cap_and_normalize(combined, max_weight)

    def scores(self, asof: object, config: dict[str, Any]) -> pd.DataFrame:
        universe = self.universe(asof, config)
        factors = self.factors(asof, config)
        if universe.empty or factors.empty:
            return pd.DataFrame(columns=["symbol", "score"])
        weights = effective_factor_weights(config, self.benchmark, asof)
        assert_fundamental_factor_policy(config, weights, data_available=not self.fundamentals.empty)
        return score_factor_values(factors, universe, config, weights)

    def factors(self, asof: object, config: dict[str, Any]) -> pd.DataFrame:
        lookbacks = config["strategy"].get("lookbacks", {})
        key = (asof, stable_json(lookbacks))
        cached = self.factor_cache.get(key)
        if cached is not None:
            return cached
        frame = compute_factor_values(self.prices, self.fundamentals, asof, lookbacks, self.benchmark)
        self.factor_cache[key] = frame
        return frame

    def universe(self, asof: object, config: dict[str, Any]) -> pd.DataFrame:
        key = (asof, stable_json(config.get("universe", {})))
        cached = self.universe_cache.get(key)
        if cached is not None:
            return cached
        frame = eligible_universe(self.stocks, self.prices, asof, config)
        self.universe_cache[key] = frame
        return frame


def stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
