from __future__ import annotations

from itertools import combinations
import json
from typing import Any

import numpy as np
import pandas as pd


AUDIT_COLUMNS = ["item", "value", "threshold", "passed", "severity", "note"]
FAMILY_COLUMNS = [
    "candidate",
    "rank",
    "cluster_id",
    "cluster_representative",
    "cluster_size",
    "mean_within_similarity",
    "min_within_similarity",
    "fold_count",
]
SIMILARITY_COLUMNS = [
    "candidate_a",
    "candidate_b",
    "similarity",
    "above_threshold",
    "same_cluster",
]


def build_candidate_family_redundancy(
    research: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Audit distinct candidate behaviours using contiguous-fold excess returns."""
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    similarity_threshold = min(max(float(validation.get("candidate_similarity_threshold", 0.95)), -1.0), 1.0)
    min_clusters = max(int(validation.get("min_candidate_family_clusters", 3)), 1)
    min_effective = max(float(validation.get("min_effective_candidate_count", 2.0)), 1.0)
    max_largest_share = min(max(float(validation.get("max_largest_candidate_cluster_share", 0.75)), 0.0), 1.0)

    names, ranks, matrix = parse_fold_matrix(research)
    candidate_count = len(names)
    fold_count = len(matrix[0]) if matrix else 0
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

    if candidate_count < 2 or fold_count < 2:
        add(
            "candidate_family_cluster_count",
            0,
            f">= {min_clusters}",
            False,
            "\u5019\u9009\u6298\u6536\u76ca\u8bc1\u636e\u4e0d\u8db3\uff0c\u65e0\u6cd5\u8bc6\u522b\u72ec\u7acb\u7b56\u7565\u65cf\u3002",
        )
        add(
            "candidate_family_effective_count",
            0.0,
            f">= {min_effective:.2f}",
            False,
            "\u6709\u6548\u5019\u9009\u6570\u4f7f\u7528\u6298\u6536\u76ca\u76f8\u5173\u77e9\u9635\u7684\u6709\u6548\u79e9\u4f30\u7b97\uff0c\u8bc1\u636e\u7f3a\u5931\u65f6\u5931\u8d25\u5173\u95ed\u3002",
        )
        add(
            "candidate_family_largest_cluster_share",
            1.0,
            f"<= {max_largest_share:.2f}",
            False,
            "\u6700\u5927\u540c\u8d28\u5019\u9009\u65cf\u4e0d\u5f97\u5360\u636e\u5927\u90e8\u5206\u53c2\u6570\u641c\u7d22\u7a7a\u95f4\u3002",
        )
        add(
            "candidate_family_redundancy_ready",
            "review_required",
            "all required checks passed",
            False,
            "\u5019\u9009\u65cf\u72ec\u7acb\u6027\u672a\u5f97\u5230\u8bc1\u660e\uff0c\u4e0d\u5f97\u5c06\u8868\u9762\u5019\u9009\u6570\u89c6\u4e3a\u72ec\u7acb\u8bc1\u636e\u6570\u3002",
        )
        return (
            pd.DataFrame(rows, columns=AUDIT_COLUMNS),
            pd.DataFrame(columns=FAMILY_COLUMNS),
            pd.DataFrame(columns=SIMILARITY_COLUMNS),
        )

    similarity = fold_similarity_matrix(matrix)
    order = sorted(range(candidate_count), key=lambda index: (ranks[index], names[index], index))
    clusters: list[list[int]] = []
    for index in order:
        target = next(
            (
                cluster
                for cluster in clusters
                if all(similarity[index, member] >= similarity_threshold - 1e-12 for member in cluster)
            ),
            None,
        )
        if target is None:
            clusters.append([index])
        else:
            target.append(index)

    cluster_by_index = {
        member: cluster_id
        for cluster_id, cluster in enumerate(clusters, start=1)
        for member in cluster
    }
    family_rows: list[dict[str, object]] = []
    for index in order:
        cluster_id = cluster_by_index[index]
        members = clusters[cluster_id - 1]
        peers = [float(similarity[index, member]) for member in members if member != index]
        family_rows.append({
            "candidate": names[index],
            "rank": ranks[index],
            "cluster_id": cluster_id,
            "cluster_representative": names[members[0]],
            "cluster_size": len(members),
            "mean_within_similarity": 1.0 if not peers else float(np.mean(peers)),
            "min_within_similarity": 1.0 if not peers else float(np.min(peers)),
            "fold_count": fold_count,
        })

    pair_rows: list[dict[str, object]] = []
    for left, right in combinations(range(candidate_count), 2):
        value = float(similarity[left, right])
        pair_rows.append({
            "candidate_a": names[left],
            "candidate_b": names[right],
            "similarity": value,
            "above_threshold": value >= similarity_threshold - 1e-12,
            "same_cluster": cluster_by_index[left] == cluster_by_index[right],
        })

    eigenvalues = np.clip(np.linalg.eigvalsh(similarity), 0.0, None)
    eigenvalue_square_sum = float(np.square(eigenvalues).sum())
    effective_count = (
        0.0
        if eigenvalue_square_sum <= 1e-15
        else float(eigenvalues.sum() ** 2 / eigenvalue_square_sum)
    )
    cluster_count = len(clusters)
    largest_share = max(len(cluster) for cluster in clusters) / candidate_count
    high_similarity_ratio = (
        float(np.mean([row["above_threshold"] for row in pair_rows]))
        if pair_rows
        else 0.0
    )

    cluster_ok = cluster_count >= min_clusters
    effective_ok = effective_count >= min_effective - 1e-12
    largest_ok = largest_share <= max_largest_share + 1e-12
    add(
        "candidate_family_cluster_count",
        cluster_count,
        f">= {min_clusters}",
        cluster_ok,
        "\u6309\u6298\u95f4\u8d85\u989d\u6536\u76ca\u76f8\u4f3c\u5ea6\u5b8c\u5168\u8fde\u63a5\u805a\u7c7b\u540e\u7684\u72ec\u7acb\u5019\u9009\u65cf\u6570\u3002",
    )
    add(
        "candidate_family_effective_count",
        round(effective_count, 6),
        f">= {min_effective:.2f}",
        effective_ok,
        "\u76f8\u5173\u77e9\u9635\u7279\u5f81\u503c\u7684 participation ratio\uff1b\u8d8a\u63a5\u8fd1\u8868\u9762\u5019\u9009\u6570\uff0c\u72ec\u7acb\u6027\u8d8a\u5f3a\u3002",
    )
    add(
        "candidate_family_largest_cluster_share",
        round(largest_share, 6),
        f"<= {max_largest_share:.2f}",
        largest_ok,
        "\u6700\u5927\u540c\u8d28\u65cf\u5360\u5168\u90e8\u5019\u9009\u7684\u6bd4\u4f8b\uff1b\u8fc7\u9ad8\u8868\u793a\u53c2\u6570\u641c\u7d22\u96c6\u4e2d\u5728\u540c\u4e00\u884c\u4e3a\u3002",
    )
    add(
        "candidate_family_high_similarity_pair_ratio",
        round(high_similarity_ratio, 6),
        f"similarity >= {similarity_threshold:.2f}",
        True,
        "\u8bb0\u5f55\u8fbe\u5230\u805a\u7c7b\u9608\u503c\u7684\u5019\u9009\u5bf9\u6bd4\u4f8b\uff1b\u4ec5\u7528\u4e8e\u89e3\u91ca\uff0c\u4e0d\u5355\u72ec\u653e\u884c\u6216\u963b\u65ad\u3002",
        "info",
    )
    ready = cluster_ok and effective_ok and largest_ok
    add(
        "candidate_family_redundancy_ready",
        "ready" if ready else "review_required",
        "all required checks passed",
        ready,
        "\u8be5\u95e8\u69db\u9632\u6b62\u9ad8\u5ea6\u76f8\u5173\u7684\u53c2\u6570\u53d8\u4f53\u865a\u589e\u5019\u9009\u8bc1\u636e\u91cf\uff0c\u4e0d\u8868\u793a\u7b56\u7565\u5177\u6709\u672a\u6765\u6536\u76ca\u3002",
    )
    return (
        pd.DataFrame(rows, columns=AUDIT_COLUMNS),
        pd.DataFrame(family_rows, columns=FAMILY_COLUMNS),
        pd.DataFrame(pair_rows, columns=SIMILARITY_COLUMNS),
    )


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


def fold_similarity_matrix(matrix: list[list[float]]) -> np.ndarray:
    values = np.asarray(matrix, dtype=float)
    centered = values - values.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(centered, axis=1)
    normalized = np.divide(
        centered,
        norms[:, None],
        out=np.zeros_like(centered),
        where=norms[:, None] > 1e-12,
    )
    similarity = np.clip(normalized @ normalized.T, -1.0, 1.0)
    constant = norms <= 1e-12
    for left in np.flatnonzero(constant):
        for right in np.flatnonzero(constant):
            similarity[left, right] = (
                1.0
                if np.allclose(values[left], values[right], atol=1e-12, rtol=0.0)
                else 0.0
            )
    np.fill_diagonal(similarity, 1.0)
    return similarity


FAMILY_SELECTION_SPLIT_COLUMNS = [
    "split_id",
    "train_folds",
    "test_folds",
    "selected_cluster",
    "selected_representative",
    "selected_train_excess",
    "selected_test_excess",
    "selected_test_percentile",
    "selected_test_below_median",
    "selected_test_positive",
    "cluster_count",
    "candidate_count",
    "fold_count",
]


def build_candidate_family_selection_bias(
    research: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate complementary holdout selection after collapsing redundant candidates."""
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    min_clusters = max(int(validation.get("min_family_selection_clusters", 3)), 2)
    min_folds = max(int(validation.get("min_family_selection_folds", 4)), 2)
    min_splits = max(int(validation.get("min_family_selection_splits", 6)), 1)
    max_splits = max(int(validation.get("max_selection_bias_splits", 1000)), min_splits)
    max_probability = min(max(float(validation.get("max_family_selection_bias_probability", 0.50)), 0.0), 1.0)
    min_median_test = float(validation.get("min_family_selected_test_median_excess_return", 0.0))
    min_positive_ratio = min(max(float(validation.get("min_family_selected_test_positive_ratio", 0.50)), 0.0), 1.0)
    min_consensus_ratio = min(max(float(validation.get("min_family_selection_consensus_ratio", 0.50)), 0.0), 1.0)

    names, _, matrix = parse_fold_matrix(research)
    _, families, _ = build_candidate_family_redundancy(research, config)
    candidate_count = len(names)
    fold_count = len(matrix[0]) if matrix else 0
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

    clusters: dict[int, list[int]] = {}
    representatives: dict[int, str] = {}
    if not families.empty:
        cluster_lookup = dict(zip(families["candidate"].astype(str), families["cluster_id"]))
        representative_lookup = dict(zip(families["candidate"].astype(str), families["cluster_representative"].astype(str)))
        for index, name in enumerate(names):
            if name not in cluster_lookup:
                continue
            cluster_id = int(cluster_lookup[name])
            clusters.setdefault(cluster_id, []).append(index)
            representatives.setdefault(cluster_id, representative_lookup[name])

    cluster_count = len(clusters)
    cluster_ok = cluster_count >= min_clusters
    fold_ok = fold_count >= min_folds
    add(
        "family_selection_cluster_count",
        cluster_count,
        f">= {min_clusters}",
        cluster_ok,
        "\u5019\u9009\u53bb\u91cd\u540e\u5fc5\u987b\u4fdd\u7559\u8db3\u591f\u7684\u72ec\u7acb\u7b56\u7565\u65cf\uff0c\u624d\u80fd\u8fdb\u884c\u65cf\u7ea7\u9009\u62e9\u504f\u5dee\u8bc4\u4f30\u3002",
    )
    add(
        "family_selection_fold_count",
        fold_count,
        f">= {min_folds}",
        fold_ok,
        "\u65cf\u7ea7\u4e92\u8865\u7559\u51fa\u9700\u8981\u8db3\u591f\u7684\u8fde\u7eed\u65f6\u95f4\u6298\u3002",
    )

    split_rows: list[dict[str, object]] = []
    if cluster_ok and fold_ok:
        train_size = max(fold_count // 2, 1)
        split_sets = list(combinations(range(fold_count), train_size))
        if len(split_sets) > max_splits:
            indices = [round(index * (len(split_sets) - 1) / (max_splits - 1)) for index in range(max_splits)] if max_splits > 1 else [0]
            split_sets = [split_sets[index] for index in indices]
        for split_id, train_folds in enumerate(split_sets, start=1):
            test_folds = tuple(index for index in range(fold_count) if index not in train_folds)
            train_scores = {
                cluster_id: float(np.mean([
                    np.mean([matrix[candidate][fold] for fold in train_folds])
                    for candidate in members
                ]))
                for cluster_id, members in clusters.items()
            }
            test_scores = {
                cluster_id: float(np.mean([
                    np.mean([matrix[candidate][fold] for fold in test_folds])
                    for candidate in members
                ]))
                for cluster_id, members in clusters.items()
            }
            selected = max(train_scores, key=lambda cluster_id: (train_scores[cluster_id], -cluster_id))
            selected_test = test_scores[selected]
            test_values = list(test_scores.values())
            lower = sum(value < selected_test - 1e-12 for value in test_values)
            equal = sum(abs(value - selected_test) <= 1e-12 for value in test_values)
            percentile = float((lower + 0.5 * equal) / len(test_values))
            split_rows.append({
                "split_id": split_id,
                "train_folds": ",".join(str(index + 1) for index in train_folds),
                "test_folds": ",".join(str(index + 1) for index in test_folds),
                "selected_cluster": selected,
                "selected_representative": representatives.get(selected, "missing"),
                "selected_train_excess": train_scores[selected],
                "selected_test_excess": selected_test,
                "selected_test_percentile": percentile,
                "selected_test_below_median": percentile <= 0.50,
                "selected_test_positive": selected_test > 0,
                "cluster_count": cluster_count,
                "candidate_count": candidate_count,
                "fold_count": fold_count,
            })

    details = pd.DataFrame(split_rows, columns=FAMILY_SELECTION_SPLIT_COLUMNS)
    split_count = len(details)
    splits_ok = split_count >= min_splits
    add(
        "family_selection_split_count",
        split_count,
        f">= {min_splits}",
        splits_ok,
        "\u7b56\u7565\u65cf\u8bad\u7ec3\u4e0e\u4e92\u8865\u7559\u51fa\u7ec4\u5408\u6570\u5fc5\u987b\u8fbe\u6807\u3002",
    )

    if details.empty:
        bias_probability = 1.0
        median_test = float("nan")
        positive_ratio = 0.0
        consensus_cluster = 0
        consensus_representative = "missing"
        consensus_ratio = 0.0
    else:
        bias_probability = float(details["selected_test_below_median"].map(bool).mean())
        median_test = float(pd.to_numeric(details["selected_test_excess"], errors="coerce").median())
        positive_ratio = float(details["selected_test_positive"].map(bool).mean())
        counts = details["selected_cluster"].value_counts()
        consensus_cluster = int(counts.index[0])
        consensus_ratio = float(counts.iloc[0] / split_count)
        consensus_representative = representatives.get(consensus_cluster, "missing")

    probability_ok = splits_ok and bias_probability <= max_probability + 1e-12
    median_ok = splits_ok and not pd.isna(median_test) and median_test >= min_median_test
    positive_ok = splits_ok and positive_ratio >= min_positive_ratio
    consensus_ok = splits_ok and consensus_ratio >= min_consensus_ratio
    add(
        "family_selection_bias_probability",
        round(bias_probability, 6),
        f"<= {max_probability:.2f}",
        probability_ok,
        "\u8bad\u7ec3\u6298\u9009\u4e2d\u7684\u7b56\u7565\u65cf\u5728\u4e92\u8865\u7559\u51fa\u6298\u843d\u5230\u65cf\u4e2d\u4f4d\u6570\u53ca\u4ee5\u4e0b\u7684\u6bd4\u4f8b\u3002",
    )
    add(
        "family_selected_median_test_excess",
        "missing" if pd.isna(median_test) else round(median_test, 6),
        f">= {min_median_test:.4f}",
        median_ok,
        "\u6309\u65cf\u5e73\u5747\u9009\u4e2d\u540e\uff0c\u5bf9\u5e94\u7559\u51fa\u6298\u8d85\u989d\u6536\u76ca\u4e2d\u4f4d\u6570\u5fc5\u987b\u4e0d\u4e3a\u8d1f\u3002",
    )
    add(
        "family_selected_positive_test_ratio",
        round(positive_ratio, 6),
        f">= {min_positive_ratio:.2f}",
        positive_ok,
        "\u6309\u65cf\u5e73\u5747\u9009\u4e2d\u540e\uff0c\u7559\u51fa\u6298\u4fdd\u6301\u6b63\u8d85\u989d\u7684\u6bd4\u4f8b\u3002",
    )
    add(
        "family_selection_consensus_ratio",
        f"cluster={consensus_cluster};representative={consensus_representative};share={consensus_ratio:.3f}",
        f">= {min_consensus_ratio:.2f}",
        consensus_ok,
        "\u4e0d\u540c\u8bad\u7ec3\u6298\u5bf9\u540c\u4e00\u7b56\u7565\u65cf\u8fbe\u6210\u5171\u8bc6\u7684\u6bd4\u4f8b\uff1b\u8fc7\u4f4e\u8868\u793a\u65cf\u7ea7\u83b7\u80dc\u8005\u4e0d\u7a33\u5b9a\u3002",
    )
    ready = cluster_ok and fold_ok and splits_ok and probability_ok and median_ok and positive_ok and consensus_ok
    add(
        "strategy_family_selection_ready",
        "ready" if ready else "review_required",
        "all required checks passed",
        ready,
        "\u5148\u5bf9\u540c\u8d28\u5019\u9009\u53bb\u91cd\u518d\u505a\u4e92\u8865\u7559\u51fa\uff1b\u8be5\u7ed3\u8bba\u4ec5\u7528\u4e8e\u7814\u7a76\u6cbb\u7406\uff0c\u4e0d\u81ea\u52a8\u4fee\u6539\u6216\u664b\u7ea7\u7b56\u7565\u3002",
    )
    return pd.DataFrame(rows, columns=AUDIT_COLUMNS), details
