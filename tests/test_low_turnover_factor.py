from datetime import date

import pandas as pd

from ashare_quant.baostock_source import fetch_history_frame_baostock
from ashare_quant.data import (
    history_from_akshare,
    merge_fetch_history_provenance,
    normalize_prices,
)
from ashare_quant.factors import compute_factor_values, compute_scores
from ashare_quant.validation import validate_config

from test_baostock_source import FakeResult


def _price_rows(symbol: str, dates: list[date], turnover: float | None) -> list[dict]:
    rows = []
    for index, day in enumerate(dates):
        row = {
            "date": day,
            "symbol": symbol,
            "open": 10.0,
            "high": 10.1,
            "low": 9.9,
            "close": 10.0 + index * 0.001,
            "volume": 1_000_000.0,
            "amount": 10_000_000.0,
        }
        if turnover is not None:
            row["turnover"] = turnover
        rows.append(row)
    return rows


def test_normalize_prices_preserves_optional_turnover_without_requiring_it() -> None:
    base = pd.DataFrame(_price_rows("600000", [date(2025, 1, 2)], None))
    legacy = normalize_prices(base)
    enriched = normalize_prices(base.assign(turnover=["1.25"]))

    assert "turnover" not in legacy.columns
    assert enriched.loc[0, "turnover"] == 1.25


def test_akshare_history_preserves_reported_turnover_percent() -> None:
    raw = pd.DataFrame(
        [{
            "日期": "2025-01-02",
            "开盘": 10.0,
            "最高": 10.2,
            "最低": 9.8,
            "收盘": 10.1,
            "成交量": 1_000_000,
            "成交额": 10_100_000,
            "换手率": 1.75,
        }]
    )

    result = history_from_akshare(raw, "600000")

    assert result.loc[0, "turnover"] == 1.75


class _BaoStockTurnoverClient:
    def __init__(self):
        self.fields = ""

    def query_history_k_data_plus(self, code, fields, **kwargs):
        self.fields = fields
        return FakeResult(
            [
                "date", "code", "open", "high", "low", "close",
                "volume", "amount", "turn", "tradestatus",
            ],
            [[
                "2025-01-02", code, "10", "10.2", "9.8", "10.1",
                "1000000", "10100000", "2.5", "1",
            ]],
        )


def test_baostock_history_preserves_turnover_and_provenance() -> None:
    client = _BaoStockTurnoverClient()
    frame, provenance = fetch_history_frame_baostock(
        client,
        "600000",
        date(2025, 1, 1),
        date(2025, 1, 31),
    )

    assert frame.loc[0, "turnover"] == 2.5
    assert provenance["turnover_quality"] == "reported_percent"
    assert "turn" in client.fields.split(",")


def test_legacy_provenance_marks_turnover_unavailable() -> None:
    class _Store:
        def read_frame(self, name, required=False):
            return pd.DataFrame(
                [{
                    "symbol": "600000",
                    "source": "legacy",
                    "amount_quality": "reported",
                }]
            )

    merged = merge_fetch_history_provenance(_Store(), [], ["600000"])

    assert merged.loc[0, "turnover_quality"] == "unavailable"


def test_low_turnover_ranks_lower_trailing_average_higher() -> None:
    dates = list(pd.date_range("2024-01-01", periods=260, freq="B").date)
    prices = pd.DataFrame(
        _price_rows("600000", dates, 0.8)
        + _price_rows("600001", dates, 2.0)
        + _price_rows("600002", dates, 5.0)
    )

    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        dates[-1],
        {
            "turnover": 252,
            "turnover_min_observations": 200,
            "volatility": 60,
            "liquidity": 60,
        },
    ).set_index("symbol")

    assert factors.loc["600000", "low_turnover"] > factors.loc["600001", "low_turnover"]
    assert factors.loc["600001", "low_turnover"] > factors.loc["600002", "low_turnover"]
    assert factors["low_turnover_available"].all()


def test_required_low_turnover_excludes_incomplete_source_rows() -> None:
    dates = list(pd.date_range("2024-01-01", periods=260, freq="B").date)
    complete = _price_rows("600000", dates, 1.0)
    incomplete = _price_rows("600001", dates, None)
    for row in incomplete[-40:]:
        row["turnover"] = 2.0
    prices = pd.DataFrame(complete + incomplete)
    stocks = pd.DataFrame(
        [
            {"symbol": "600000", "industry": "bank", "board": "main"},
            {"symbol": "600001", "industry": "tech", "board": "main"},
        ]
    )
    config = {
        "strategy": {
            "weights": {"low_turnover": 1.0},
            "lookbacks": {
                "turnover": 252,
                "turnover_min_observations": 200,
                "volatility": 60,
                "liquidity": 60,
            },
            "required_factors": ["low_turnover"],
        }
    }

    scores = compute_scores(prices, stocks, pd.DataFrame(), dates[-1], config)

    assert scores["symbol"].tolist() == ["600000"]


def test_low_turnover_is_neutral_without_explicit_lookback() -> None:
    dates = list(pd.date_range("2025-01-01", periods=60, freq="B").date)
    prices = pd.DataFrame(
        _price_rows("600000", dates, 1.0)
        + _price_rows("600001", dates, 4.0)
    )

    factors = compute_factor_values(
        prices,
        pd.DataFrame(),
        dates[-1],
        {"volatility": 20, "liquidity": 20},
    )

    assert (factors["low_turnover"] == 0.0).all()
    assert not factors["low_turnover_available"].any()


def test_active_low_turnover_requires_explicit_valid_windows() -> None:
    missing = {
        "strategy": {
            "weights": {"low_turnover": 1.0},
            "lookbacks": {"volatility": 60},
        }
    }
    invalid = {
        "strategy": {
            "weights": {"low_turnover": 1.0},
            "lookbacks": {
                "turnover": 10,
                "turnover_min_observations": 30,
            },
        }
    }

    assert any("requires turnover" in error for error in validate_config(missing))
    errors = validate_config(invalid)
    assert "strategy.lookbacks.turnover must be at least 20" in errors
    assert (
        "strategy.lookbacks.turnover_min_observations must not exceed turnover"
        in errors
    )
