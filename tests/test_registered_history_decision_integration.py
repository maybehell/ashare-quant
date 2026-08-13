import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.candidate import live_strategy_hash
from ashare_quant.data import DataStore
from ashare_quant.history_validation import run_extended_history_validation


def test_registered_history_run_persists_rejected_decision_without_orders() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = {
            "paths": {
                "cache_dir": str(root / "cache"),
                "state_db": str(root / "research.sqlite3"),
                "output_dir": str(root / "outputs"),
            },
            "data": {"history_staging_namespace": "registered_test"},
            "backtest": {"start": "2018-01-01", "end": "2026-08-07"},
            "validation": {"research_only": True, "apply_active_execution": False},
            "auto_live": {"allow_broker_submit": False, "emergency_stop": True},
        }
        config["validation"]["fixed_strategy_sha256"] = live_strategy_hash(config)
        store = DataStore.from_config(config)
        store.write_frame(
            "prices",
            pd.DataFrame(
                [{
                    "date": "2026-08-07", "symbol": "000001", "open": 10,
                    "high": 10, "low": 10, "close": 10, "volume": 100,
                    "amount": 1000,
                }]
            ),
        )
        store.write_frame(
            "history_backfill_progress_registered_test",
            pd.DataFrame([{
                "status": "complete", "requested_symbols": 1,
                "completed_symbols": 1, "remaining_symbols": 0,
                "failed_items": 0, "requested_start": "2018-01-01",
                "requested_end": "2026-08-07",
                "generated_at": "2026-08-13T00:00:00",
            }]),
        )
        output_dir = root / "outputs"
        registration = {
            "experiment_id": "registered",
            "experiment_sequence": 4,
            "experiment_family_id": "family",
            "experiment_nominal_alpha": 0.0125,
            "experiment_preregistration_sha256": "registration",
            "_experiment_thresholds": {
                "min_total_excess_return": 0.02,
                "min_information_ratio": 0.30,
                "max_drawdown": 0.25,
                "min_out_of_sample_excess_return": 0.02,
                "min_walk_forward_folds": 3,
                "min_walk_forward_positive_ratio": 0.60,
            },
        }

        def fake_backtest(_config):
            output_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([
                {"metric": "total_return", "value": 0.01},
                {"metric": "benchmark_total_return", "value": 0.02},
                {"metric": "excess_total_return", "value": -0.01},
                {"metric": "max_drawdown", "value": -0.10},
                {"metric": "information_ratio", "value": -0.20},
            ]).to_csv(output_dir / "performance_metrics.csv", index=False)
            pd.DataFrame([{"date": "2026-08-07", "equity": 99000}]).to_csv(
                output_dir / "equity_curve.csv", index=False
            )
            pd.DataFrame([{
                "sample": "out_of_sample_last_30pct", "excess_return": -0.02
            }]).to_csv(output_dir / "robustness_split.csv", index=False)
            pd.DataFrame([{
                "folds": 3, "positive_test_ratio": 1 / 3
            }]).to_csv(output_dir / "walk_forward_summary.csv", index=False)
            pd.DataFrame([{
                "observed_excess_return": -0.01, "two_sided_p_value": 0.50
            }]).to_csv(output_dir / "excess_bootstrap.csv", index=False)
            return {"performance_metrics.csv": output_dir / "performance_metrics.csv"}

        def fake_report(_config):
            target = output_dir / "report.html"
            target.write_text("<html></html>", encoding="utf-8")
            return target

        with (
            patch(
                "ashare_quant.history_validation.require_history_experiment_registration",
                return_value=registration,
            ),
            patch("ashare_quant.history_validation.run_backtest", side_effect=fake_backtest),
            patch("ashare_quant.history_validation.regenerate_report", side_effect=fake_report),
        ):
            run_extended_history_validation(config)

        status = pd.read_csv(output_dir / "history_validation_status.csv").iloc[-1]
        assert status["status"] == "completed"
        assert status["experiment_decision"] == "rejected"
        assert "significance:bonferroni_p_value" in status["experiment_decision_failures"]
        assert not (output_dir / "orders.csv").exists()
        assert not (output_dir / "rpa_orders.csv").exists()
