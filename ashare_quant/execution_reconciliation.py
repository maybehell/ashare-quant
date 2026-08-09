from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from .audit import frame_sha256
from .config import project_path


DETAIL_COLUMNS = [
    "strategy_name",
    "formal_rank",
    "research_rank",
    "research_only",
    "included_in_global_reconciliation",
    "formal_total_return",
    "research_strategy_return",
    "return_gap",
    "abs_return_gap",
    "formal_excess_return",
    "research_excess_return",
    "excess_gap",
    "abs_excess_gap",
    "excess_sign_agreement",
    "formal_data_current",
    "research_data_current",
    "within_gap_tolerance",
    "reconciled",
    "status",
    "note",
]

SUMMARY_COLUMNS = ["item", "value", "threshold", "passed", "severity", "note"]

COMPARISON_COLUMNS = [
    "research_rank",
    "research_strategy_return",
    "research_excess_return",
    "execution_return_gap",
    "execution_excess_gap",
    "execution_excess_sign_agreement",
    "execution_model_reconciled",
    "execution_reconciliation_status",
]

_MARKET_HASH_ITEMS = (
    "stocks_sha256",
    "prices_sha256",
    "fundamentals_sha256",
    "benchmark_sha256",
)


def write_execution_model_reconciliation(
    config: dict[str, Any],
    comparison: pd.DataFrame,
) -> tuple[pd.DataFrame, Path, Path]:
    output_dir = project_path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    detail, summary, enriched = build_execution_model_reconciliation(config, comparison)
    detail_path = output_dir / "execution_model_reconciliation.csv"
    summary_path = output_dir / "execution_model_reconciliation_summary.csv"
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    return enriched, detail_path, summary_path


def build_execution_model_reconciliation(
    config: dict[str, Any],
    comparison: pd.DataFrame,
    *,
    research: pd.DataFrame | None = None,
    research_manifest: pd.DataFrame | None = None,
    current_manifest: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    output_dir = project_path(config["paths"]["output_dir"])
    validation = config.get("validation", {})
    required = bool(validation.get("require_execution_model_reconciliation", False))
    min_matched = max(int(validation.get("min_execution_reconciliation_candidates", 3)), 1)
    max_gap = max(float(validation.get("max_execution_excess_gap", 0.10)), 0.0)
    min_rank_correlation = float(validation.get("min_execution_rank_correlation", 0.50))
    min_sign_agreement = float(validation.get("min_execution_sign_agreement_ratio", 0.80))

    research_path = _research_path(config, output_dir)
    research = _read_csv(research_path) if research is None else research.copy()
    research_manifest = (
        _read_csv(research_path.parent / "strategy_research_manifest.csv")
        if research_manifest is None
        else research_manifest.copy()
    )
    current_manifest = (
        _read_csv(output_dir / "run_manifest.csv")
        if current_manifest is None
        else current_manifest.copy()
    )
    research_current = _research_evidence_current(research, research_manifest, current_manifest)

    formal = comparison.copy().reset_index(drop=True)
    if formal.empty:
        formal = pd.DataFrame(columns=["strategy_name"])
    formal["formal_rank"] = range(1, len(formal) + 1)

    required_research_columns = {"name", "rank", "strategy_return", "excess_return"}
    if required_research_columns.issubset(research.columns):
        research_view = research[["name", "rank", "strategy_return", "excess_return"]].copy()
        research_view["research_only"] = (
            research["research_only"].map(_bool_value)
            if "research_only" in research
            else False
        )
        research_view = research_view.drop_duplicates("name", keep=False).rename(
            columns={
                "name": "strategy_name",
                "rank": "research_rank",
                "strategy_return": "research_strategy_return",
                "excess_return": "research_excess_return",
            }
        )
    else:
        research_view = pd.DataFrame(
            columns=[
                "strategy_name", "research_rank", "research_strategy_return",
                "research_excess_return", "research_only",
            ]
        )

    joined = formal.merge(research_view, on="strategy_name", how="left")
    numeric_columns = [
        "formal_rank",
        "research_rank",
        "total_return",
        "research_strategy_return",
        "excess_total_return",
        "research_excess_return",
    ]
    for column in numeric_columns:
        if column not in joined:
            joined[column] = pd.NA
        joined[column] = pd.to_numeric(joined[column], errors="coerce")

    joined["formal_total_return"] = joined["total_return"]
    joined["formal_excess_return"] = joined["excess_total_return"]
    joined["return_gap"] = joined["formal_total_return"] - joined["research_strategy_return"]
    joined["abs_return_gap"] = joined["return_gap"].abs()
    joined["excess_gap"] = joined["formal_excess_return"] - joined["research_excess_return"]
    joined["abs_excess_gap"] = joined["excess_gap"].abs()
    matched = joined["research_rank"].notna()
    joined["excess_sign_agreement"] = matched & (
        (joined["formal_excess_return"] >= 0) == (joined["research_excess_return"] >= 0)
    )
    joined["research_only"] = joined["research_only"].map(_bool_value)
    formal_current = (
        joined["data_current"]
        if "data_current" in joined
        else pd.Series(False, index=joined.index, dtype=bool)
    )
    joined["formal_data_current"] = formal_current.map(_bool_value)
    joined["research_data_current"] = research_current
    joined["within_gap_tolerance"] = matched & (joined["abs_excess_gap"] <= max_gap)

    included = (
        matched
        & ~joined["research_only"]
        & joined["formal_data_current"]
        & joined["research_data_current"]
    )
    joined["included_in_global_reconciliation"] = included
    matched_frame = joined[included].copy()
    matched_count = len(matched_frame)
    rank_correlation = _rank_correlation(matched_frame)
    sign_agreement_ratio = (
        float(matched_frame["excess_sign_agreement"].mean()) if matched_count else 0.0
    )
    max_observed_gap = (
        float(matched_frame["abs_excess_gap"].max()) if matched_count else float("nan")
    )
    checks = [
        _summary_row(
            "execution_research_evidence_current",
            research_current,
            True,
            research_current,
            "研究结果、清单哈希和当前市场数据指纹必须一致。",
        ),
        _summary_row(
            "execution_matched_candidate_count",
            matched_count,
            f">= {min_matched}",
            matched_count >= min_matched,
            "当前数据且未归档的正式回测与快速研究必须有足够多的同名候选可对账。",
        ),
        _summary_row(
            "execution_excess_rank_correlation",
            rank_correlation,
            f">= {min_rank_correlation:.2f}",
            rank_correlation is not None and rank_correlation >= min_rank_correlation,
            "按超额收益计算的 Spearman 排序相关性；过低说明快速研究不能可靠筛选正式候选。",
        ),
        _summary_row(
            "execution_excess_sign_agreement_ratio",
            sign_agreement_ratio,
            f">= {min_sign_agreement:.2f}",
            sign_agreement_ratio >= min_sign_agreement,
            "研究估算与正式撮合的超额收益方向一致比例。",
        ),
        _summary_row(
            "execution_max_abs_excess_gap",
            max_observed_gap,
            f"<= {max_gap:.4f}",
            matched_count > 0 and max_observed_gap <= max_gap,
            "匹配候选中研究估算与正式撮合的最大绝对超额收益差。",
        ),
    ]
    global_ready = all(bool(row["passed"]) for row in checks)
    checks.append(
        _summary_row(
            "execution_model_reconciliation_ready",
            "ready" if global_ready else "review_required",
            "all required checks passed",
            global_ready,
            "该门槛用于验证快速候选研究能否代表正式次日开盘、整手和交易约束撮合。",
        )
    )
    summary = pd.DataFrame(checks, columns=SUMMARY_COLUMNS)

    per_row_ready = (
        included & joined["within_gap_tolerance"]
        & joined["excess_sign_agreement"]
        & global_ready
    )
    joined["reconciled"] = per_row_ready
    joined["status"] = joined.apply(
        lambda row: _detail_status(row, required=required, global_ready=global_ready), axis=1
    )
    joined["note"] = joined.apply(_detail_note, axis=1)

    detail = joined.reindex(columns=DETAIL_COLUMNS)
    enriched = comparison.copy()
    merge_columns = joined[
        [
            "strategy_name",
            "research_rank",
            "research_strategy_return",
            "research_excess_return",
            "return_gap",
            "excess_gap",
            "excess_sign_agreement",
            "reconciled",
            "status",
        ]
    ].rename(
        columns={
            "return_gap": "execution_return_gap",
            "excess_gap": "execution_excess_gap",
            "excess_sign_agreement": "execution_excess_sign_agreement",
            "reconciled": "execution_model_reconciled",
            "status": "execution_reconciliation_status",
        }
    )
    enriched = enriched.merge(merge_columns, on="strategy_name", how="left")
    for column in COMPARISON_COLUMNS:
        if column not in enriched:
            enriched[column] = pd.NA
    return detail, summary, enriched


def _research_path(config: dict[str, Any], output_dir: Path) -> Path:
    configured = str(config.get("validation", {}).get("strategy_research_path", "")).strip()
    return project_path(configured) if configured else output_dir / "strategy_research.csv"


def _research_evidence_current(
    research: pd.DataFrame,
    research_manifest: pd.DataFrame,
    current_manifest: pd.DataFrame,
) -> bool:
    if research.empty or research_manifest.empty or current_manifest.empty:
        return False
    expected_hash = _manifest_value(research_manifest, "strategy_research_sha256")
    if not expected_hash or frame_sha256(research) != expected_hash:
        return False
    return all(
        _manifest_value(research_manifest, item)
        and _manifest_value(research_manifest, item) == _manifest_value(current_manifest, item)
        for item in _MARKET_HASH_ITEMS
    )


def _rank_correlation(frame: pd.DataFrame) -> float | None:
    if len(frame) < 2:
        return None
    formal_rank = frame["formal_excess_return"].rank(method="average")
    research_rank = frame["research_excess_return"].rank(method="average")
    correlation = formal_rank.corr(research_rank)
    return None if pd.isna(correlation) else float(correlation)


def _summary_row(
    item: str,
    value: object,
    threshold: object,
    passed: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "item": item,
        "value": value,
        "threshold": threshold,
        "passed": bool(passed),
        "severity": "ok" if passed else "warning",
        "note": note,
    }


def _detail_status(row: pd.Series, *, required: bool, global_ready: bool) -> str:
    if not required:
        return "not_required"
    if pd.isna(row.get("research_rank")):
        return "research_candidate_missing"
    if _bool_value(row.get("research_only")):
        return "excluded_research_only"
    if not _bool_value(row.get("formal_data_current")):
        return "formal_evidence_stale"
    if not _bool_value(row.get("research_data_current")):
        return "research_evidence_stale"
    if not global_ready:
        return "global_reconciliation_failed"
    if not _bool_value(row.get("excess_sign_agreement")):
        return "excess_direction_mismatch"
    if not _bool_value(row.get("within_gap_tolerance")):
        return "excess_gap_exceeded"
    return "reconciled"


def _detail_note(row: pd.Series) -> str:
    status = str(row.get("status", ""))
    messages = {
        "not_required": "当前配置未把执行模型对账设为晋级硬门槛。",
        "research_candidate_missing": "正式回测策略在当前候选研究表中没有唯一同名记录。",
        "excluded_research_only": "该实验已归档为 research_only；保留差异明细，但不参与现役候选全局统计。",
        "formal_evidence_stale": "正式回测市场数据指纹不是当前版本。",
        "research_evidence_stale": "候选研究文件、清单哈希或市场数据指纹不一致。",
        "global_reconciliation_failed": "匹配数量、排序相关性、方向一致率或总体差异门槛未通过。",
        "excess_direction_mismatch": "研究估算与正式撮合的超额收益方向相反。",
        "excess_gap_exceeded": "研究估算与正式撮合的超额收益差超过配置阈值。",
        "reconciled": "研究成本估算与正式次日开盘撮合在配置容差内一致。",
    }
    return messages.get(status, "需要复核执行模型对账证据。")


def _manifest_value(frame: pd.DataFrame, item: str) -> str:
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return ""
    selected = frame[frame["item"].astype(str) == item]
    if selected.empty:
        return ""
    value = selected["value"].iloc[0]
    return "" if pd.isna(value) else str(value)


def _bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()
