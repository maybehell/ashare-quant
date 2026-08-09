from __future__ import annotations

from collections import Counter
from itertools import combinations
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import frame_sha256
from .candidate_family import build_candidate_family_redundancy, build_candidate_family_selection_bias
from .config import project_path
from .io_utils import atomic_to_csv


SELECTION_BIAS_AUDIT_COLUMNS = ["item", "value", "threshold", "passed", "severity", "note"]
SELECTION_BIAS_SPLIT_COLUMNS = [
    "split_id",
    "train_folds",
    "test_folds",
    "selected_candidate",
    "selected_train_excess",
    "selected_test_excess",
    "selected_test_percentile",
    "selected_test_below_median",
    "selected_test_positive",
    "candidate_count",
    "fold_count",
]


def build_strategy_selection_bias(
    research: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Estimate fold-level selection bias with complementary train/test fold splits.

    This is a CSCV-style diagnostic over already-computed contiguous time-fold excess
    returns. It is intentionally labelled as a diagnostic rather than a formal PBO
    estimate because the inputs are fold aggregates, not raw bar-level returns.
    """
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    min_candidates = max(int(validation.get("min_selection_bias_candidates", 5)), 2)
    min_folds = max(int(validation.get("min_selection_bias_folds", 4)), 2)
    min_splits = max(int(validation.get("min_selection_bias_splits", 6)), 1)
    max_probability = min(max(float(validation.get("max_selection_bias_probability", 0.50)), 0.0), 1.0)
    min_median_test = float(validation.get("min_selected_test_median_excess_return", 0.0))
    min_positive_ratio = min(max(float(validation.get("min_selected_test_positive_ratio", 0.50)), 0.0), 1.0)
    max_splits = max(int(validation.get("max_selection_bias_splits", 1000)), min_splits)

    rows: list[dict[str, object]] = []

    def add(item: str, value: object, threshold: object, passed: bool, note: str, severity: str | None = None) -> None:
        rows.append({
            "item": item,
            "value": value,
            "threshold": threshold,
            "passed": bool(passed),
            "severity": severity or ("ok" if passed else "warning"),
            "note": note,
        })

    family_audit, _, _ = build_candidate_family_redundancy(research, config)
    rows.extend(family_audit.to_dict(orient="records"))
    family_ready_rows = family_audit[family_audit["item"] == "candidate_family_redundancy_ready"]
    family_ready = (
        not family_ready_rows.empty and bool(family_ready_rows["passed"].iloc[0])
    )
    family_selection_audit, _ = build_candidate_family_selection_bias(research, config)
    rows.extend(family_selection_audit.to_dict(orient="records"))
    family_selection_ready_rows = family_selection_audit[
        family_selection_audit["item"] == "strategy_family_selection_ready"
    ]
    family_selection_ready = not family_selection_ready_rows.empty and bool(family_selection_ready_rows["passed"].iloc[0])
    names, ranks, matrix = parse_fold_matrix(research)
    candidate_count = len(matrix)
    fold_count = len(matrix[0]) if matrix else 0
    candidate_ok = candidate_count >= min_candidates
    fold_ok = fold_count >= min_folds
    add(
        "selection_bias_candidate_count",
        candidate_count,
        f">= {min_candidates}",
        candidate_ok,
        "候选数量必须足以评估反复择优造成的家族级选择偏差。",
    )
    add(
        "selection_bias_fold_count",
        fold_count,
        f">= {min_folds}",
        fold_ok,
        "连续时间折过少时，训练折获胜者在互补留出折上的排名反转没有统计解释力。",
    )

    split_rows: list[dict[str, object]] = []
    if candidate_ok and fold_ok:
        train_size = max(fold_count // 2, 1)
        split_sets = list(combinations(range(fold_count), train_size))
        if len(split_sets) > max_splits:
            split_sets = evenly_spaced_combinations(split_sets, max_splits)
        for split_id, train_folds in enumerate(split_sets, start=1):
            test_folds = tuple(index for index in range(fold_count) if index not in train_folds)
            train_scores = [mean_at(values, train_folds) for values in matrix]
            test_scores = [mean_at(values, test_folds) for values in matrix]
            selected = deterministic_argmax(train_scores, ranks, names)
            selected_test = test_scores[selected]
            percentile = midpoint_percentile(selected_test, test_scores)
            split_rows.append({
                "split_id": split_id,
                "train_folds": ",".join(str(index + 1) for index in train_folds),
                "test_folds": ",".join(str(index + 1) for index in test_folds),
                "selected_candidate": names[selected],
                "selected_train_excess": train_scores[selected],
                "selected_test_excess": selected_test,
                "selected_test_percentile": percentile,
                "selected_test_below_median": percentile <= 0.50,
                "selected_test_positive": selected_test > 0,
                "candidate_count": candidate_count,
                "fold_count": fold_count,
            })

    details = pd.DataFrame(split_rows, columns=SELECTION_BIAS_SPLIT_COLUMNS)
    split_count = len(details)
    splits_ok = split_count >= min_splits
    add(
        "selection_bias_split_count",
        split_count,
        f">= {min_splits}",
        splits_ok,
        "互补训练/留出组合数量必须足够，避免单次切分碰巧支持某个候选。",
    )

    if details.empty:
        bias_probability = 1.0
        median_test = float("nan")
        positive_ratio = 0.0
        top_name = "missing"
        top_share = 1.0
    else:
        bias_probability = float(details["selected_test_below_median"].map(bool).mean())
        median_test = float(pd.to_numeric(details["selected_test_excess"], errors="coerce").median())
        positive_ratio = float(details["selected_test_positive"].map(bool).mean())
        counts = Counter(details["selected_candidate"].astype(str))
        top_name, top_count = counts.most_common(1)[0]
        top_share = float(top_count / split_count)

    probability_ok = splits_ok and bias_probability <= max_probability + 1e-12
    median_ok = splits_ok and not pd.isna(median_test) and median_test >= min_median_test
    positive_ok = splits_ok and positive_ratio >= min_positive_ratio
    add(
        "fold_selection_bias_probability",
        round(bias_probability, 6),
        f"<= {max_probability:.2f}",
        probability_ok,
        "训练折最优候选在互补留出折跌到候选中位数及以下的比例；越低越好。",
    )
    add(
        "selected_candidate_median_test_excess",
        "missing" if pd.isna(median_test) else round(median_test, 6),
        f">= {min_median_test:.4f}",
        median_ok,
        "每个训练折获胜者在对应留出折上的超额收益中位数必须不为负。",
    )
    add(
        "selected_candidate_positive_test_ratio",
        round(positive_ratio, 6),
        f">= {min_positive_ratio:.2f}",
        positive_ok,
        "训练折获胜者在互补留出折保持正超额的比例。",
    )
    add(
        "selection_frequency_concentration",
        f"candidate={top_name};share={top_share:.3f}",
        "informational",
        True,
        "记录不同训练折是否总由同一候选获胜；该项仅用于解释，不单独放行或阻断。",
        "info",
    )
    ready = family_ready and family_selection_ready and candidate_ok and fold_ok and splits_ok and probability_ok and median_ok and positive_ok
    add(
        "strategy_selection_bias_ready",
        "ready" if ready else "review_required",
        "all required checks passed",
        ready,
        "该结论只约束候选研究可信度，不替代冻结后前向验证、Shadow 观察或实盘门禁。",
    )
    return pd.DataFrame(rows, columns=SELECTION_BIAS_AUDIT_COLUMNS), details


def parse_fold_matrix(research: pd.DataFrame) -> tuple[list[str], list[float], list[list[float]]]:
    if research.empty or "time_fold_excess_returns" not in research.columns:
        return [], [], []
    parsed_rows: list[tuple[str, float, list[float]]] = []
    for position, row in enumerate(research.to_dict(orient="records")):
        raw = row.get("time_fold_excess_returns", "")
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(values, list) or len(values) < 2:
            continue
        numeric = pd.to_numeric(pd.Series(values), errors="coerce")
        if numeric.isna().any():
            continue
        name = str(row.get("name", "") or f"candidate_{position + 1}")
        rank_value = pd.to_numeric(row.get("rank", position + 1), errors="coerce")
        rank = float(position + 1) if pd.isna(rank_value) else float(rank_value)
        parsed_rows.append((name, rank, numeric.astype(float).tolist()))
    if not parsed_rows:
        return [], [], []
    fold_count = min(len(values) for _, _, values in parsed_rows)
    if fold_count < 2:
        return [], [], []
    return (
        [name for name, _, _ in parsed_rows],
        [rank for _, rank, _ in parsed_rows],
        [values[:fold_count] for _, _, values in parsed_rows],
    )


def mean_at(values: list[float], indices: tuple[int, ...]) -> float:
    if not indices:
        return 0.0
    return float(sum(values[index] for index in indices) / len(indices))


def deterministic_argmax(scores: list[float], ranks: list[float], names: list[str]) -> int:
    if not scores:
        raise ValueError("candidate scores are empty")
    best = max(scores)
    candidates = [index for index, score in enumerate(scores) if abs(score - best) <= 1e-12]
    return min(candidates, key=lambda index: (ranks[index], names[index], index))


def midpoint_percentile(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    lower = sum(candidate < value - 1e-12 for candidate in values)
    equal = sum(abs(candidate - value) <= 1e-12 for candidate in values)
    return float((lower + 0.5 * equal) / len(values))


def evenly_spaced_combinations(values: list[tuple[int, ...]], limit: int) -> list[tuple[int, ...]]:
    if len(values) <= limit:
        return values
    if limit <= 1:
        return [values[0]]
    indices = [round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)]
    return [values[index] for index in indices]


def write_strategy_selection_bias(
    config: dict[str, Any],
    research_path: str | Path | None = None,
) -> dict[str, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    source = project_path(research_path) if research_path else output_dir / "strategy_research.csv"
    try:
        research = pd.read_csv(source, encoding="utf-8-sig")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        research = pd.DataFrame()
    audit, details = build_strategy_selection_bias(research, config)
    _, families, similarities = build_candidate_family_redundancy(research, config)
    family_selection_audit, family_selection_details = build_candidate_family_selection_bias(research, config)
    audit_path = output_dir / "strategy_selection_bias.csv"
    detail_path = output_dir / "strategy_selection_bias_splits.csv"
    family_path = output_dir / "strategy_candidate_family.csv"
    similarity_path = output_dir / "strategy_candidate_similarity.csv"
    family_selection_path = output_dir / "strategy_family_selection.csv"
    family_selection_detail_path = output_dir / "strategy_family_selection_splits.csv"
    atomic_to_csv(audit, audit_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(details, detail_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(families, family_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(similarities, similarity_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(family_selection_audit, family_selection_path, index=False, encoding="utf-8-sig")
    atomic_to_csv(family_selection_details, family_selection_detail_path, index=False, encoding="utf-8-sig")
    return {
        "strategy_selection_bias.csv": audit_path,
        "strategy_selection_bias_splits.csv": detail_path,
        "strategy_candidate_family.csv": family_path,
        "strategy_candidate_similarity.csv": similarity_path,
        "strategy_family_selection.csv": family_selection_path,
        "strategy_family_selection_splits.csv": family_selection_detail_path,
    }


def selection_bias_manifest_rows(output_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for filename in (
        "strategy_selection_bias.csv",
        "strategy_selection_bias_splits.csv",
        "strategy_candidate_family.csv",
        "strategy_candidate_similarity.csv",
        "strategy_family_selection.csv",
        "strategy_family_selection_splits.csv",
    ):
        path = output_dir / filename
        try:
            frame = pd.read_csv(path, encoding="utf-8-sig")
        except (FileNotFoundError, pd.errors.EmptyDataError):
            frame = pd.DataFrame()
        stem = filename.removesuffix(".csv")
        rows.extend([
            {"item": f"{stem}_rows", "value": str(len(frame))},
            {"item": f"{stem}_sha256", "value": frame_sha256(frame)},
        ])
    return rows
