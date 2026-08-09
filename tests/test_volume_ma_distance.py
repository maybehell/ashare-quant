from copy import deepcopy
from datetime import date

import pandas as pd

from ashare_quant.config import load_config
from ashare_quant.factors import compute_factor_values, score_factor_values
from ashare_quant.validation import validate_config


def price_frame(periods: int = 220) -> pd.DataFrame:
    dates = pd.date_range("2025-01-02", periods=periods, freq="B").date
    rows = []
    for index, current in enumerate(dates):
        for symbol in ("600000", "600001"):
            volume = 100.0
            if index >= periods - 21:
                volume = 200.0 if symbol == "600000" else 50.0
            rows.append(
                {
                    "date": current,
                    "symbol": symbol,
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                    "volume": volume,
                    "amount": 10_000_000.0,
                }
            )
    return pd.DataFrame(rows)


def test_low_volume_ma_distance_prefers_falling_recent_volume() -> None:
    prices = price_frame()
    asof = max(prices["date"])

    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        asof,
        {"volume_ma_short": 21, "volume_ma_long": 200},
    ).set_index("symbol")

    assert factors.loc["600001", "low_volume_ma_distance"] > factors.loc["600000", "low_volume_ma_distance"]
    assert bool(factors["low_volume_ma_distance_available"].all())


def test_volume_ma_distance_is_invariant_to_future_rows() -> None:
    prices = price_frame()
    asof = max(prices["date"])
    future_dates = pd.date_range(pd.Timestamp(asof) + pd.Timedelta(days=1), periods=5, freq="B").date
    future = []
    for current in future_dates:
        for symbol, volume in (("600000", 1.0), ("600001", 10_000.0)):
            future.append(
                {
                    "date": current,
                    "symbol": symbol,
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                    "volume": volume,
                    "amount": 10_000_000.0,
                }
            )

    before = compute_factor_values(
        prices, pd.DataFrame(), asof, {"volume_ma_short": 21, "volume_ma_long": 200}
    ).set_index("symbol")["low_volume_ma_distance"]
    after = compute_factor_values(
        pd.concat([prices, pd.DataFrame(future)], ignore_index=True),
        pd.DataFrame(),
        asof,
        {"volume_ma_short": 21, "volume_ma_long": 200},
    ).set_index("symbol")["low_volume_ma_distance"]

    pd.testing.assert_series_equal(before, after)


def test_required_volume_ma_distance_fails_closed_without_history() -> None:
    prices = price_frame(periods=100)
    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        max(prices["date"]),
        {"volume_ma_short": 21, "volume_ma_long": 200},
    )
    stocks = pd.DataFrame(
        [
            {"symbol": "600000", "industry": "bank", "board": "main"},
            {"symbol": "600001", "industry": "bank", "board": "main"},
        ]
    )
    config = {
        "strategy": {
            "required_factors": ["low_volume_ma_distance"],
        }
    }

    scored = score_factor_values(
        factors, stocks, config, {"low_volume_ma_distance": 1.0}
    )

    assert scored.empty


def test_volume_ma_distance_config_requires_ordered_lookbacks() -> None:
    config = load_config("configs/default.yaml")
    config = deepcopy(config)
    config["strategy"]["weights"] = {"low_volume_ma_distance": 1.0}
    config["strategy"]["required_factors"] = ["low_volume_ma_distance"]
    config["strategy"]["lookbacks"] = {
        "volume_ma_short": 21,
        "volume_ma_long": 200,
    }
    assert not validate_config(config)

    config["strategy"]["lookbacks"]["volume_ma_short"] = 200
    errors = validate_config(config)
    assert "strategy.lookbacks.volume_ma_short must be less than volume_ma_long" in errors
