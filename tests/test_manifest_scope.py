import pandas as pd

from ashare_quant.audit import backtest_config_sha256, build_run_manifest
from ashare_quant.data import metadata_refresh_symbols


def manifest_values(frame):
    return dict(zip(frame["item"], frame["value"]))


def test_unpriced_catalog_metadata_does_not_change_effective_stock_hash():
    stocks = pd.DataFrame(
        [
            {"symbol": "600000", "industry": "bank", "list_date": "1999-11-10"},
            {"symbol": "300001", "industry": "unknown", "list_date": "2000-01-01"},
        ]
    )
    prices = pd.DataFrame([{"date": "2026-07-21", "symbol": "600000", "close": 10.0}])
    changed = stocks.copy()
    changed.loc[changed["symbol"] == "300001", "industry"] = "machinery"

    before = manifest_values(build_run_manifest({}, stocks, prices))
    after = manifest_values(build_run_manifest({}, changed, prices))

    assert before["stocks_rows"] == "1"
    assert before["stocks_sha256"] == after["stocks_sha256"]
    assert before["stock_catalog_sha256"] != after["stock_catalog_sha256"]


def test_metadata_refresh_defaults_to_priced_curated_symbols():
    stocks = pd.DataFrame(
        [
            {"symbol": "600000", "name": "A", "board": "main", "industry": "bank", "list_date": "1999-11-10", "is_st": False},
            {"symbol": "300001", "name": "B", "board": "gem", "industry": "unknown", "list_date": "2000-01-01", "is_st": False},
        ]
    )
    prices = pd.DataFrame(
        [{"date": "2026-07-21", "symbol": "600000", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "amount": 1000}]
    )
    config = {
        "data": {"metadata_priced_only": True},
        "universe": {"boards": ["main", "gem", "star"], "liquidity_lookback": 20},
    }

    assert metadata_refresh_symbols(stocks, prices, config, max_symbols=10) == ["600000"]


def test_fetch_operations_do_not_change_strategy_hash_but_coverage_rules_do():
    base = {
        "data": {
            "metadata_source": "baostock",
            "history_workers": 3,
            "min_daily_price_coverage_ratio": 0.9,
        }
    }
    operational = {"data": {**base["data"], "metadata_source": "akshare", "history_workers": 1}}
    behavioral = {"data": {**base["data"], "min_daily_price_coverage_ratio": 0.8}}

    assert backtest_config_sha256(base) == backtest_config_sha256(operational)
    assert backtest_config_sha256(base) != backtest_config_sha256(behavioral)
