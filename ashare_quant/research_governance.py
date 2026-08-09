from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .candidate import live_strategy_hash
from .config import project_path
from .data import DataStore
from .validation import complete_market_dates
from .walkforward import apply_candidate


GATE_COLUMNS = ["item", "value", "threshold", "passed", "severity", "note"]


def candidate_hashes(config: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(candidate.get("name", "candidate")): live_strategy_hash(apply_candidate(config, candidate))
        for candidate in candidates
    }


def governance_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    state_value = str(validation.get("research_governance_state_path", "") or "").strip()
    if state_value:
        state_path = project_path(state_value)
    else:
        state_db = project_path(config.get("paths", {}).get("state_db", "data/state.sqlite3"))
        state_path = state_db.parent / "research_governance.json"
    output_dir = project_path(config["paths"]["output_dir"])
    return state_path, output_dir / "strategy_research_gate.csv"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def manifest_value(path: Path, item: str) -> str:
    frame = read_csv(path)
    if frame.empty or not {"item", "value"}.issubset(frame.columns):
        return ""
    rows = frame[frame["item"].astype(str) == item]
    return "" if rows.empty else str(rows.iloc[-1]["value"])


def approved_hashes_from_research(path: Path) -> dict[str, str]:
    frame = read_csv(path)
    if frame.empty or not {"name", "strategy_hash"}.issubset(frame.columns):
        return {}
    return {
        str(row["name"]): str(row["strategy_hash"])
        for _, row in frame[["name", "strategy_hash"]].dropna().iterrows()
    }


def latest_market_date(store: DataStore, config: dict[str, Any]) -> tuple[str, list[pd.Timestamp]]:
    prices = store.read_frame("prices", required=False)
    if prices.empty or "date" not in prices.columns:
        return "", []
    try:
        benchmark = store.load_benchmark(required=False)
    except (AttributeError, FileNotFoundError):
        benchmark = pd.DataFrame()
    complete = complete_market_dates(prices, benchmark, config)
    dates = [pd.Timestamp(value) for value in complete]
    return (dates[-1].date().isoformat(), dates) if dates else ("", [])


def count_new_trading_days(dates: list[pd.Timestamp], anchor: str) -> int:
    parsed = pd.to_datetime(anchor, errors="coerce")
    if pd.isna(parsed):
        return 0
    return sum(value > parsed.normalize() for value in dates)


def gate_snapshot(config: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    state_path, _ = governance_paths(config)
    output_dir = project_path(config["paths"]["output_dir"])
    research_path = output_dir / "strategy_research.csv"
    manifest_path = output_dir / "strategy_research_manifest.csv"
    family_path = output_dir / "strategy_family_selection.csv"
    state = read_json(state_path)
    approved = state.get("approved_strategy_hashes") if isinstance(state.get("approved_strategy_hashes"), dict) else {}
    if not approved:
        approved = approved_hashes_from_research(research_path)
    anchor = str(state.get("last_research_market_date", "") or "")
    if not anchor:
        anchor = manifest_value(manifest_path, "prices_last_date")

    latest, dates = latest_market_date(DataStore.from_config(config), config)
    new_days = count_new_trading_days(dates, anchor)
    current = candidate_hashes(config, candidates)
    approved_values = {str(value) for value in approved.values()}
    new_candidates = sorted(name for name, value in current.items() if value not in approved_values)

    research = read_csv(research_path)
    anti_count = 0
    if not research.empty and "anti_overfit_passed" in research.columns:
        anti_count = int(research["anti_overfit_passed"].astype(str).str.lower().isin({"true", "1", "yes"}).sum())
    family = read_csv(family_path)
    family_ready = False
    if not family.empty and {"item", "passed"}.issubset(family.columns):
        row = family[family["item"].astype(str) == "strategy_family_selection_ready"]
        family_ready = not row.empty and str(row.iloc[-1]["passed"]).strip().lower() in {"true", "1", "yes"}

    validation = config.get("validation", {}) if isinstance(config.get("validation"), dict) else {}
    min_new_days = int(validation.get("research_min_new_trading_days", 60))
    min_anti = int(validation.get("min_anti_overfit_candidates", 2))
    has_evidence = research_path.exists() and not research.empty
    weak_evidence = has_evidence and (anti_count < min_anti or not family_ready)
    unlocked_by_new_sample = bool(anchor and new_days >= min_new_days)
    status = "stop_new_candidates" if weak_evidence and not unlocked_by_new_sample else "open"
    return {
        "status": status,
        "anchor_market_date": anchor,
        "latest_market_date": latest,
        "new_trading_days": new_days,
        "required_new_trading_days": min_new_days,
        "anti_overfit_candidates": anti_count,
        "required_anti_overfit_candidates": min_anti,
        "family_selection_ready": family_ready,
        "new_candidates": new_candidates,
        "current_strategy_hashes": current,
        "approved_strategy_hashes": approved,
        "has_research_evidence": has_evidence,
        "unlocked_by_new_sample": unlocked_by_new_sample,
    }


def gate_frame(snapshot: dict[str, Any]) -> pd.DataFrame:
    stopped = snapshot["status"] == "stop_new_candidates"
    rows = [
        {
            "item": "research_candidate_gate",
            "value": snapshot["status"],
            "threshold": "open only with credible evidence or a fresh independent sample",
            "passed": not stopped,
            "severity": "warning" if stopped else "ok",
            "note": "门禁仅阻止继续增加候选；已登记候选的回测、报告和只读观察仍可刷新。",
        },
        {
            "item": "new_independent_trading_days",
            "value": snapshot["new_trading_days"],
            "threshold": f'>= {snapshot["required_new_trading_days"]}',
            "passed": snapshot["unlocked_by_new_sample"],
            "severity": "warning" if not snapshot["unlocked_by_new_sample"] else "ok",
            "note": f'上次研究样本截止 {snapshot["anchor_market_date"] or "未知"}；当前行情截止 {snapshot["latest_market_date"] or "未知"}。',
        },
        {
            "item": "anti_overfit_candidate_count",
            "value": snapshot["anti_overfit_candidates"],
            "threshold": f'>= {snapshot["required_anti_overfit_candidates"]}',
            "passed": snapshot["anti_overfit_candidates"] >= snapshot["required_anti_overfit_candidates"],
            "severity": "warning",
            "note": "在当前缓存上继续试参数不能替代新的独立样本。",
        },
        {
            "item": "family_selection_ready",
            "value": snapshot["family_selection_ready"],
            "threshold": "True",
            "passed": snapshot["family_selection_ready"],
            "severity": "warning" if not snapshot["family_selection_ready"] else "ok",
            "note": "候选族互补留出需要通过，避免从相似策略中挑出偶然赢家。",
        },
        {
            "item": "unregistered_candidate_count",
            "value": len(snapshot["new_candidates"]),
            "threshold": "0 while gate is stopped",
            "passed": not stopped or not snapshot["new_candidates"],
            "severity": "error" if stopped and snapshot["new_candidates"] else "ok",
            "note": ", ".join(snapshot["new_candidates"]) or "当前候选均已登记。",
        },
    ]
    return pd.DataFrame(rows, columns=GATE_COLUMNS)


def write_research_gate(config: dict[str, Any], candidates: list[dict[str, Any]], commit: bool = False) -> Path:
    state_path, output_path = governance_paths(config)
    snapshot = gate_snapshot(config, candidates)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gate_frame(snapshot).to_csv(output_path, index=False, encoding="utf-8-sig")
    if commit or not state_path.exists():
        previous = read_json(state_path)
        previous_hashes = (
            previous.get("approved_strategy_hashes")
            if isinstance(previous.get("approved_strategy_hashes"), dict)
            else {}
        )
        expands_candidate_set = bool(snapshot["new_candidates"])
        preserve_anchor = bool(previous) and commit and not expands_candidate_set
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_research_market_date": (
                str(previous.get("last_research_market_date", "") or snapshot["anchor_market_date"])
                if preserve_anchor
                else snapshot["latest_market_date"] or snapshot["anchor_market_date"]
            ),
            "approved_strategy_hashes": (
                snapshot["current_strategy_hashes"]
                if commit and expands_candidate_set
                else previous_hashes or snapshot["approved_strategy_hashes"]
            ),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def assert_research_candidates_allowed(config: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    snapshot = gate_snapshot(config, candidates)
    write_research_gate(config, candidates, commit=False)
    if snapshot["status"] == "stop_new_candidates" and snapshot["new_candidates"]:
        names = ", ".join(snapshot["new_candidates"])
        raise RuntimeError(
            f"研究候选门禁已暂停新增策略（{names}）。当前独立新样本 "
            f'{snapshot["new_trading_days"]}/{snapshot["required_new_trading_days"]} 个交易日；'
            "请继续积累前向数据或完成独立历史扩展。已登记候选仍可正常刷新。"
        )
