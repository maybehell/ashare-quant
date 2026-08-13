import argparse
from unittest.mock import patch

import pandas as pd

from ashare_quant.cli import (
    preflight_forward_candidate_configs,
    run_forward_daily,
    run_forward_only_candidate_observations,
)


def _candidate_config(output_dir="unused") -> dict:
    return {
        "paths": {"output_dir": str(output_dir)},
        "strategy": {"name": "rejected_candidate"},
        "validation": {
            "research_only": True,
            "experiment_status": "preregistered_forward_only",
        },
    }


def _manifest() -> dict:
    return {
        "freeze_mode": "forward_only_preregistration",
        "freeze_boundary_schema_version": 1,
        "freeze_known_data_end": "2026-08-01",
        "freeze_price_end": "2026-08-01",
    }


def _rejection() -> dict:
    return {
        "status": "authoritative_history_rejected",
        "decision_sha256": "rejection-decision",
    }


def test_preflight_skips_rejected_candidate_before_freeze_validation():
    candidate = _candidate_config()
    with (
        patch("ashare_quant.cli.load_config", return_value=candidate),
        patch("ashare_quant.cli.load_candidate_manifest", return_value=_manifest()),
        patch("ashare_quant.cli.matching_candidate_id", return_value="candidate-id"),
        patch("ashare_quant.cli.candidate_quarantine_record", return_value=_rejection()),
        patch("ashare_quant.cli.candidate_freeze_boundary_evidence") as boundary,
    ):
        result = preflight_forward_candidate_configs(["configs/rejected.yaml"])

    assert result == []
    boundary.assert_not_called()


def test_observation_records_rejection_without_market_access(tmp_path):
    config = {
        "paths": {"output_dir": str(tmp_path)},
        "validation": {"forward_only_candidate_configs": ["configs/rejected.yaml"]},
    }
    candidate = _candidate_config(tmp_path / "candidate")
    with (
        patch("ashare_quant.cli.load_config", return_value=candidate),
        patch("ashare_quant.cli.load_candidate_manifest", return_value=_manifest()),
        patch("ashare_quant.cli.matching_candidate_id", return_value="candidate-id"),
        patch("ashare_quant.cli.candidate_quarantine_record", return_value=_rejection()),
        patch("ashare_quant.cli.candidate_market_freshness") as freshness,
        patch("ashare_quant.cli.run_shadow_signals") as shadow,
        patch("ashare_quant.cli.run_shadow_audit") as audit,
    ):
        paths = run_forward_only_candidate_observations(config)

    summary = pd.read_csv(paths["forward_only_candidate_observation.csv"])
    assert summary.loc[0, "status"] == "permanently_rejected"
    assert summary.loc[0, "forward_observation_status"] == "permanently_rejected"
    assert "rejection-decision" in summary.loc[0, "message"]
    freshness.assert_not_called()
    shadow.assert_not_called()
    audit.assert_not_called()


def test_forward_daily_does_not_fetch_rejected_candidate(tmp_path):
    config = {
        "paths": {"output_dir": str(tmp_path), "cache_dir": str(tmp_path / "base-cache")},
        "validation": {"forward_only_candidate_configs": ["configs/rejected.yaml"]},
    }
    args = argparse.Namespace(refresh=True, allow_stale=False, quiet=True)
    summary_path = tmp_path / "forward_only_candidate_observation.csv"

    def observe(*args, **kwargs):
        pd.DataFrame(
            [{"status": "permanently_rejected", "message": "successor research is required"}]
        ).to_csv(summary_path, index=False)
        return {"forward_only_candidate_observation.csv": summary_path}

    with (
        patch("ashare_quant.cli.forward_candidate_cache_isolation", return_value=(True, "isolated")),
        patch("ashare_quant.cli.preflight_forward_candidate_configs", return_value=[]),
        patch("ashare_quant.cli.fetch_akshare") as fetch,
        patch("ashare_quant.cli.DataStore.from_config") as store,
        patch("ashare_quant.cli.run_forward_only_candidate_observations", side_effect=observe),
        patch("ashare_quant.cli.refresh_selected_candidate_evidence", return_value={}),
        patch("ashare_quant.cli.load_config", return_value={}),
        patch("ashare_quant.cli.write_deployment_gate", return_value={}),
        patch("ashare_quant.cli.regenerate_report", return_value=tmp_path / "report.html"),
        patch("ashare_quant.cli.generate_control_panel", return_value=tmp_path / "control_panel.html"),
    ):
        paths = run_forward_daily(config, args)

    fetch.assert_not_called()
    store.assert_not_called()
    assert "forward-observation:forward_only_candidate_observation.csv" in paths
