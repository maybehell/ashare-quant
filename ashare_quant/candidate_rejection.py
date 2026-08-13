from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import mapping_sha256
from .candidate import (
    CANDIDATE_FAILURE_COLUMNS,
    candidate_failure_history_path,
    live_strategy_behavior_hash,
    live_strategy_hash,
    load_candidate_failure_history,
    load_candidate_manifest,
    mirror_candidate_failure_history,
)
from .candidate_history import build_candidate_history_decision
from .io_utils import atomic_to_csv


HISTORY_REJECTION_STATUS = "authoritative_history_rejected"


def record_candidate_history_rejection(
    config: dict[str, Any], *, decision_sha256: str, confirm: bool = False
) -> Path:
    if not confirm:
        raise RuntimeError("Candidate history rejection requires explicit --confirm.")
    expected = str(decision_sha256 or "").strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise RuntimeError("Candidate history rejection requires a valid decision SHA-256.")

    decision = build_candidate_history_decision(config).iloc[-1]
    current = str(decision.get("decision_sha256", "") or "").strip().lower()
    if current != expected:
        raise RuntimeError("Candidate history decision SHA-256 does not match current evidence.")
    if str(decision.get("decision", "") or "") != "rejected":
        raise RuntimeError("Candidate history rejection requires a current rejected decision.")

    manifest = load_candidate_manifest(config) or {}
    candidate_id = str(manifest.get("candidate_id", "") or "")
    strategy_hash = str(manifest.get("strategy_hash", "") or "")
    if not candidate_id or strategy_hash != live_strategy_hash(config):
        raise RuntimeError("Candidate manifest identity does not match the current strategy.")

    history = load_candidate_failure_history(config)
    same_candidate = history[
        history["candidate_id"].fillna("").astype(str).eq(candidate_id)
        & history["status"].fillna("").astype(str).eq(HISTORY_REJECTION_STATUS)
    ]
    if not same_candidate.empty:
        matching = same_candidate[
            same_candidate["decision_sha256"].fillna("").astype(str).str.lower().eq(expected)
        ]
        if len(matching) == 1 and len(same_candidate) == 1:
            mirror_candidate_failure_history(config, history)
            return candidate_failure_history_path(config)
        raise RuntimeError("Candidate already has a different authoritative history rejection record.")

    evidence_identity = {
        "candidate_id": candidate_id,
        "strategy_sha256": strategy_hash,
        "decision_sha256": current,
        "history_status_sha256": str(decision.get("history_status_sha256", "") or ""),
        "formal_prices_sha256": str(decision.get("formal_prices_sha256", "") or ""),
        "history_input_binding_sha256": str(decision.get("history_input_binding_sha256", "") or ""),
        "failed_checks": str(decision.get("failed_checks", "") or ""),
    }
    failure = {
        "candidate_id": candidate_id,
        "strategy_hash": strategy_hash,
        "behavior_hash": live_strategy_behavior_hash(config),
        "strategy_name": str(manifest.get("strategy_name", config.get("strategy", {}).get("name", ""))),
        "status": HISTORY_REJECTION_STATUS,
        "freeze_price_end": str(manifest.get("freeze_price_end", "") or ""),
        "observed_forward_trading_days": "",
        "forward_excess_return": "",
        "evidence_source": "candidate_history_decision",
        "decision_sha256": current,
        "evidence_sha256": mapping_sha256(evidence_identity),
        "failed_checks": str(decision.get("failed_checks", "") or ""),
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    result = pd.concat([history, pd.DataFrame([failure])], ignore_index=True)
    path = candidate_failure_history_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_to_csv(result[CANDIDATE_FAILURE_COLUMNS], path, index=False, encoding="utf-8-sig")
    mirror_candidate_failure_history(config, result)
    return path
