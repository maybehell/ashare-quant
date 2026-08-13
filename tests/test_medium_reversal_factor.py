from datetime import date

import pandas as pd

from ashare_quant.factors import compute_factor_values


def prices_for_path(symbol: str, closes: list[float], dates: list[date]) -> list[dict]:
    return [
        {"date": day, "symbol": symbol, "close": close, "amount": 100_000_000.0}
        for day, close in zip(dates, closes)
    ]


def test_medium_reversal_uses_60_day_return_skipping_latest_5_days() -> None:
    dates = list(pd.date_range("2025-01-01", periods=70, freq="B").date)
    up_then_recent_drop = [10.0 + 0.10 * index for index in range(65)] + [8.0] * 5
    down_then_recent_rally = [20.0 - 0.10 * index for index in range(65)] + [30.0] * 5
    flat = [15.0] * 70
    prices = pd.DataFrame(
        prices_for_path("600000", up_then_recent_drop, dates)
        + prices_for_path("600001", down_then_recent_rally, dates)
        + prices_for_path("600002", flat, dates)
    )

    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        dates[-1],
        {
            "momentum": 20,
            "volatility": 20,
            "liquidity": 20,
            "medium_reversal": 60,
            "medium_reversal_skip": 5,
        },
    ).set_index("symbol")

    assert factors.loc["600001", "medium_reversal"] > factors.loc["600002", "medium_reversal"]
    assert factors.loc["600002", "medium_reversal"] > factors.loc["600000", "medium_reversal"]


def test_medium_reversal_is_neutral_unless_explicitly_configured() -> None:
    dates = list(pd.date_range("2025-01-01", periods=70, freq="B").date)
    prices = pd.DataFrame(
        prices_for_path("600000", [10.0 + index * 0.1 for index in range(70)], dates)
        + prices_for_path("600001", [20.0 - index * 0.1 for index in range(70)], dates)
    )

    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        dates[-1],
        {"momentum": 20, "volatility": 20, "liquidity": 20},
    )

    assert (factors["medium_reversal"] == 0.0).all()
