from datetime import date

import numpy as np
import pandas as pd

from ashare_quant.config import load_config
from ashare_quant.factors import compute_factor_values, compute_scores
from ashare_quant.validation import validate_config


def residual_fixture() -> tuple[pd.DataFrame, pd.DataFrame, list[date]]:
    dates = list(pd.date_range("2024-01-02", periods=300, freq="B").date)
    phase = np.arange(len(dates), dtype=float)
    market_returns = np.zeros(len(dates), dtype=float)
    market_returns[1:] = 0.0004 + 0.006 * np.sin(phase[1:] / 11.0)

    residuals = {
        "600000": np.zeros(len(dates), dtype=float),
        "600001": np.zeros(len(dates), dtype=float),
        "600002": np.where((phase.astype(int) % 2) == 0, 0.015, -0.015),
    }
    residuals["600001"][-160:-20] = 0.003
    betas = {"600000": 1.0, "600001": 1.4, "600002": 0.8}

    rows: list[dict[str, object]] = []
    for symbol, residual in residuals.items():
        stock_returns = betas[symbol] * market_returns + residual
        stock_returns[0] = 0.0
        closes = 10.0 * np.cumprod(1.0 + stock_returns)
        for dt, close in zip(dates, closes, strict=True):
            rows.append(
                {
                    "date": dt,
                    "symbol": symbol,
                    "close": float(close),
                    "amount": 100_000_000.0,
                }
            )

    benchmark = pd.DataFrame(
        {
            "date": dates,
            "close": 100.0 * np.cumprod(1.0 + market_returns),
        }
    )
    return pd.DataFrame(rows), benchmark, dates


def residual_lookbacks() -> dict[str, int]:
    return {
        "momentum": 60,
        "volatility": 60,
        "drawdown": 60,
        "liquidity": 60,
        "residual_beta": 252,
        "residual_momentum": 120,
        "residual_momentum_skip": 20,
        "idiosyncratic_volatility": 60,
    }


def test_residual_momentum_and_idiosyncratic_volatility_rank_independently() -> None:
    prices, benchmark, dates = residual_fixture()
    factors = compute_factor_values(
        prices, pd.DataFrame(), dates[-1], residual_lookbacks(), benchmark
    ).set_index("symbol")

    assert factors.loc["600001", "residual_momentum"] > factors.loc["600000", "residual_momentum"]
    assert factors.loc["600000", "low_idiosyncratic_volatility"] > factors.loc["600002", "low_idiosyncratic_volatility"]
    assert factors["residual_momentum_available"].all()
    assert factors["low_idiosyncratic_volatility_available"].all()


def test_residual_factors_do_not_use_prices_after_signal_date() -> None:
    prices, benchmark, dates = residual_fixture()
    asof = dates[-41]
    before = compute_factor_values(
        prices[prices["date"] <= asof],
        pd.DataFrame(),
        asof,
        residual_lookbacks(),
        benchmark[benchmark["date"] <= asof],
    ).set_index("symbol")
    after = compute_factor_values(
        prices,
        pd.DataFrame(),
        asof,
        residual_lookbacks(),
        benchmark,
    ).set_index("symbol")

    pd.testing.assert_series_equal(before["residual_momentum"], after["residual_momentum"])
    pd.testing.assert_series_equal(
        before["low_idiosyncratic_volatility"],
        after["low_idiosyncratic_volatility"],
    )


def test_required_residual_factors_fail_closed_without_benchmark() -> None:
    prices, _, dates = residual_fixture()
    stocks = pd.DataFrame(
        {
            "symbol": ["600000", "600001", "600002"],
            "industry": ["bank", "industry", "technology"],
            "board": ["main", "main", "main"],
        }
    )
    config = load_config("configs/residual_momentum_idvol.yaml")

    scores = compute_scores(prices, stocks, pd.DataFrame(), dates[-1], config)

    assert scores.empty


def test_residual_momentum_candidate_config_is_valid() -> None:
    config = load_config("configs/residual_momentum_idvol.yaml")

    assert validate_config(config) == []
