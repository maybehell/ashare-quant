from datetime import date

import pandas as pd

from ashare_quant.factors import compute_factor_values, compute_scores


def prices_for_path(symbol: str, closes: list[float], dates: list[date]) -> list[dict]:
    return [
        {"date": day, "symbol": symbol, "close": close, "amount": 100_000_000.0}
        for day, close in zip(dates, closes)
    ]


def test_medium_reversal_availability_is_explicit() -> None:
    dates = list(pd.date_range("2025-01-01", periods=70, freq="B").date)
    prices = pd.DataFrame(
        prices_for_path("600000", [10.0 + index * 0.1 for index in range(70)], dates)
    )

    configured = compute_factor_values(
        prices,
        pd.DataFrame(),
        dates[-1],
        {"medium_reversal": 60, "medium_reversal_skip": 5},
    )
    unconfigured = compute_factor_values(prices, pd.DataFrame(), dates[-1], {})

    assert configured["medium_reversal_available"].all()
    assert not unconfigured["medium_reversal_available"].any()


def test_required_medium_reversal_excludes_insufficient_history() -> None:
    dates = list(pd.date_range("2025-01-01", periods=70, freq="B").date)
    prices = pd.DataFrame(
        prices_for_path("600000", [10.0 + index * 0.1 for index in range(70)], dates)
        + prices_for_path("600001", [20.0 - index * 0.1 for index in range(30)], dates[-30:])
    )
    stocks = pd.DataFrame(
        [
            {"symbol": "600000", "industry": "test", "board": "main"},
            {"symbol": "600001", "industry": "test", "board": "main"},
        ]
    )

    scores = compute_scores(
        prices,
        stocks,
        pd.DataFrame(),
        dates[-1],
        {
            "strategy": {
                "lookbacks": {"medium_reversal": 60, "medium_reversal_skip": 5},
                "weights": {"medium_reversal": 1.0},
                "required_factors": ["medium_reversal"],
            }
        },
    )

    assert scores["symbol"].tolist() == ["600000"]
