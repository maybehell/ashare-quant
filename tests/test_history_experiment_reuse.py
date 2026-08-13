from pathlib import Path

import pandas as pd

from ashare_quant.history_validation import reusable_completed_validation


def reusable(
    tmp_path: Path,
    registration: str,
    status_registration: str | None,
    decision: str = "passed",
) -> bool:
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    row = {
        "status": "completed",
        "formal_prices_sha256": "prices",
        "backtest_config_sha256": "config",
        "strategy_sha256": "strategy",
        "history_input_binding_sha256": "input",
        "survivorship_audit_sha256": "survivorship",
        "effective_trading_days": 1500,
        "progress_generated_at": "2026-08-13T00:00:00",
    }
    if status_registration is not None:
        row["experiment_preregistration_sha256"] = status_registration
        if decision:
            row["experiment_decision"] = decision
    status = tmp_path / "status.csv"
    pd.DataFrame([row]).to_csv(status, index=False)
    return reusable_completed_validation(
        status, report, "prices", "config", "strategy", "input",
        "survivorship", registration, 1500, "2026-08-13T00:00:00",
    )


def test_registry_bound_result_requires_exact_preregistration_identity(tmp_path: Path) -> None:
    assert reusable(tmp_path, "registered", "registered") is True
    assert reusable(tmp_path, "registered", "different") is False
    assert reusable(tmp_path, "registered", "registered", decision="") is False


def test_registry_bound_rejected_result_is_reusable_without_becoming_authority(tmp_path: Path) -> None:
    assert reusable(tmp_path, "registered", "registered", decision="rejected") is True


def test_legacy_unbound_result_remains_reusable_without_registry_requirement(tmp_path: Path) -> None:
    assert reusable(tmp_path, "", None) is True
