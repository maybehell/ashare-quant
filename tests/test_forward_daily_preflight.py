import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from ashare_quant.cli import preflight_forward_candidate_configs, run_forward_daily


def candidate_config() -> dict:
    return {
        "paths": {"output_dir": "unused"},
        "validation": {
            "research_only": True,
            "experiment_status": "preregistered_forward_only",
        },
    }


def valid_manifest() -> dict:
    return {
        "freeze_mode": "forward_only_preregistration",
        "freeze_boundary_schema_version": 1,
        "freeze_known_data_end": "2026-08-01",
        "freeze_price_end": "2026-08-01",
    }


def test_forward_candidate_preflight_accepts_matching_safe_manifest():
    config = candidate_config()
    with (
        patch("ashare_quant.cli.load_config", return_value=config),
        patch("ashare_quant.cli.load_candidate_manifest", return_value=valid_manifest()),
        patch("ashare_quant.cli.matching_candidate_id", return_value="candidate-id"),
        patch("ashare_quant.cli.candidate_freeze_boundary_evidence", return_value=(True, "2026-08-01", "ok")),
    ):
        result = preflight_forward_candidate_configs(["configs/candidate.yaml"])

    assert result == [("configs/candidate.yaml", config)]


def test_forward_daily_rejects_stale_identity_before_data_or_network_access(tmp_path):
    config = {
        "paths": {"output_dir": str(tmp_path)},
        "validation": {"forward_only_candidate_configs": ["configs/stale.yaml"]},
    }
    args = argparse.Namespace(refresh=True, allow_stale=False, quiet=True)
    with (
        patch("ashare_quant.cli.load_config", return_value=candidate_config()),
        patch("ashare_quant.cli.load_candidate_manifest", return_value=valid_manifest()),
        patch("ashare_quant.cli.matching_candidate_id", return_value=""),
        patch("ashare_quant.cli.fetch_akshare") as fetch,
        patch("ashare_quant.cli.DataStore.from_config") as store,
        patch(
            "ashare_quant.cli.forward_candidate_cache_isolation",
            return_value=(True, "isolated"),
        ) as isolation,
    ):
        with pytest.raises(RuntimeError, match="preflight failed before market refresh"):
            run_forward_daily(config, args)

    isolation.assert_called_once()
    fetch.assert_not_called()
    store.assert_not_called()


def test_forward_candidate_preflight_rejects_missing_manifest():
    with (
        patch("ashare_quant.cli.load_config", return_value=candidate_config()),
        patch("ashare_quant.cli.load_candidate_manifest", return_value={}),
    ):
        with pytest.raises(RuntimeError, match="no valid preregistration manifest"):
            preflight_forward_candidate_configs([Path("configs/missing.yaml")])
