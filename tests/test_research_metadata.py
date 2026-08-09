from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.config import load_config
from ashare_quant.research import candidate_list, refresh_strategy_research_metadata


def test_research_metadata_refreshes_rejection_without_recomputing_metrics() -> None:
    root = Path.cwd() / "test_outputs" / "research_metadata"
    root.mkdir(parents=True, exist_ok=True)
    config = load_config("configs/default.yaml")
    config["paths"]["output_dir"] = str(root)
    research_path = root / "strategy_research.csv"
    pd.DataFrame(
        [
            {
                "rank": 1,
                "name": "residual_momentum_idvol80_equal",
                "score": -1.193582,
                "experiment_status": "evaluating",
                "experiment_reason": "old",
                "research_origin": "archive_config",
                "source_config": "configs/residual_momentum_idvol.yaml",
                "research_only": True,
                "config_overrides": "{}",
            }
        ]
    ).to_csv(research_path, index=False, encoding="utf-8-sig")

    with patch("ashare_quant.research.write_strategy_selection_bias") as selection, patch(
        "ashare_quant.research.write_strategy_research_manifest"
    ) as manifest, patch("ashare_quant.research.write_research_gate") as gate:
        result = refresh_strategy_research_metadata(config)

    refreshed = pd.read_csv(result, encoding="utf-8-sig").iloc[0]
    expected = load_config("configs/residual_momentum_idvol.yaml")["validation"]
    assert refreshed["score"] == -1.193582
    assert refreshed["experiment_status"] == "rejected"
    assert refreshed["experiment_reason"] == expected["experiment_reason"]
    selection.assert_not_called()
    manifest.assert_not_called()
    gate.assert_called_once()


def test_research_metadata_does_not_rewrite_canonical_file() -> None:
    root = Path.cwd() / "test_outputs" / "research_metadata_noop"
    root.mkdir(parents=True, exist_ok=True)
    config = load_config("configs/default.yaml")
    config["paths"]["output_dir"] = str(root)
    candidate = next(
        item for item in candidate_list(config)
        if item["name"] == config["strategy"]["name"]
    )
    pd.DataFrame([{
        "name": candidate["name"],
        "research_origin": candidate["research_origin"],
        "source_config": candidate["source_config"],
        "research_only": candidate["research_only"],
        "experiment_status": candidate["experiment_status"],
        "experiment_reason": candidate.get("experiment_reason", ""),
        "config_overrides": "{}",
    }]).to_csv(root / "strategy_research.csv", index=False, encoding="utf-8-sig")

    with patch("ashare_quant.research.pd.DataFrame.to_csv") as write, patch(
        "ashare_quant.research.write_research_gate"
    ):
        refresh_strategy_research_metadata(config)
    write.assert_not_called()
