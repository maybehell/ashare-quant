from pathlib import Path

import pandas as pd

from ashare_quant.experiment_decision import evaluate_registered_history_experiment


REGISTRATION = {
    "experiment_nominal_alpha": 0.0125,
    "_experiment_thresholds": {
        "min_total_excess_return": 0.02,
        "min_information_ratio": 0.30,
        "max_drawdown": 0.25,
        "min_out_of_sample_excess_return": 0.02,
        "min_walk_forward_folds": 3,
        "min_walk_forward_positive_ratio": 0.60,
    },
}


def write_supporting_evidence(
    output_dir: Path,
    *,
    oos: float = 0.03,
    folds: int = 3,
    positive_ratio: float = 2 / 3,
    observed_excess: float = 0.04,
    p_value: float = 0.01,
) -> None:
    pd.DataFrame(
        [{"sample": "out_of_sample_last_30pct", "excess_return": oos}]
    ).to_csv(output_dir / "robustness_split.csv", index=False)
    pd.DataFrame(
        [{"folds": folds, "positive_test_ratio": positive_ratio}]
    ).to_csv(output_dir / "walk_forward_summary.csv", index=False)
    pd.DataFrame(
        [{"observed_excess_return": observed_excess, "two_sided_p_value": p_value}]
    ).to_csv(output_dir / "excess_bootstrap.csv", index=False)


def test_registered_decision_passes_only_all_fixed_checks(tmp_path: Path) -> None:
    write_supporting_evidence(tmp_path)

    result = evaluate_registered_history_experiment(
        tmp_path,
        REGISTRATION,
        {"excess_total_return": 0.04, "information_ratio": 0.40, "max_drawdown": -0.20},
    )

    assert result["experiment_decision"] == "passed"
    assert result["experiment_decision_failures"] == ""


def test_registered_decision_rejects_economic_and_significance_failures(tmp_path: Path) -> None:
    write_supporting_evidence(
        tmp_path, oos=-0.01, folds=2, positive_ratio=0.5, observed_excess=-0.03, p_value=0.02
    )

    result = evaluate_registered_history_experiment(
        tmp_path,
        REGISTRATION,
        {"excess_total_return": -0.03, "information_ratio": -0.2, "max_drawdown": -0.30},
    )

    assert result["experiment_decision"] == "rejected"
    failures = str(result["experiment_decision_failures"])
    for expected in (
        "threshold:total_excess_return",
        "threshold:information_ratio",
        "threshold:max_drawdown",
        "threshold:out_of_sample_excess_return",
        "threshold:walk_forward_folds",
        "threshold:walk_forward_positive_ratio",
        "significance:observed_excess_not_positive",
        "significance:bonferroni_p_value",
    ):
        assert expected in failures


def test_registered_decision_rejects_missing_evidence(tmp_path: Path) -> None:
    result = evaluate_registered_history_experiment(tmp_path, REGISTRATION, {})

    assert result["experiment_decision"] == "rejected"
    assert "missing:out_of_sample_excess_return" in str(
        result["experiment_decision_failures"]
    )


def test_unregistered_legacy_experiment_has_no_new_decision() -> None:
    assert evaluate_registered_history_experiment(Path("unused"), {}, {}) == {}
