from datetime import date

import pandas as pd

from ashare_quant.factors import compute_factor_values
from ashare_quant.validation import validate_config


def _prices(symbol: str, closes: list[float], dates: list[date]) -> list[dict]:
    return [
        {
            "date": day,
            "symbol": symbol,
            "close": close,
            "amount": 100_000_000.0,
            "volume": 10_000_000.0,
        }
        for day, close in zip(dates, closes)
    ]


def test_near_52_week_high_ranks_current_close_against_trailing_high() -> None:
    dates = list(pd.date_range("2024-01-01", periods=260, freq="B").date)
    prices = pd.DataFrame(
        _prices("600000", [10.0] * 259 + [9.9], dates)
        + _prices("600001", [10.0] * 259 + [8.0], dates)
        + _prices("600002", [10.0] * 259 + [5.0], dates)
    )

    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        dates[-1],
        {"high_52_week": 252, "volatility": 60, "liquidity": 60},
    ).set_index("symbol")

    assert factors.loc["600000", "near_52_week_high"] > factors.loc["600001", "near_52_week_high"]
    assert factors.loc["600001", "near_52_week_high"] > factors.loc["600002", "near_52_week_high"]
    assert factors["near_52_week_high_available"].all()


def test_near_52_week_high_is_neutral_without_explicit_lookback() -> None:
    dates = list(pd.date_range("2024-01-01", periods=260, freq="B").date)
    prices = pd.DataFrame(
        _prices("600000", [10.0] * 260, dates)
        + _prices("600001", [20.0] * 260, dates)
    )

    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        dates[-1],
        {"volatility": 60, "liquidity": 60},
    )

    assert (factors["near_52_week_high"] == 0.0).all()


def test_active_factor_requires_explicit_high_52_week_lookback() -> None:
    config = {
        "strategy": {
            "weights": {"near_52_week_high": 1.0},
            "lookbacks": {"volatility": 60},
        }
    }

    assert any("high_52_week lookback" in error for error in validate_config(config))
