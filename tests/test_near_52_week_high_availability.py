import pandas as pd

from ashare_quant.factors import score_factor_values


def test_required_factor_excludes_symbols_without_full_history() -> None:
    factors = pd.DataFrame(
        [
            {
                "symbol": "600000",
                "near_52_week_high": 1.0,
                "near_52_week_high_available": True,
            },
            {
                "symbol": "600001",
                "near_52_week_high": 2.0,
                "near_52_week_high_available": False,
            },
        ]
    )
    stocks = pd.DataFrame(
        [
            {"symbol": "600000", "industry": "A"},
            {"symbol": "600001", "industry": "A"},
        ]
    )
    config = {
        "strategy": {
            "required_factors": ["near_52_week_high"],
            "industry_neutralization": {"enabled": False},
        }
    }

    scored = score_factor_values(
        factors,
        stocks,
        config,
        {"near_52_week_high": 1.0},
    )

    assert scored["symbol"].tolist() == ["600000"]
