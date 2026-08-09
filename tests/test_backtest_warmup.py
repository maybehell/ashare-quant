from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ashare_quant.backtest import run_backtest
from ashare_quant.config import load_config
from ashare_quant.data import DataStore, make_sample_data
from ashare_quant.validation import max_lookback_days


def test_backtest_equity_and_benchmark_period_start_after_factor_warmup() -> None:
    root = Path.cwd() / "test_outputs" / "backtest_warmup"
    config = load_config("configs/default.yaml")
    config["paths"] = {
        "cache_dir": str(root / "cache"),
        "output_dir": str(root / "outputs"),
        "state_db": str(root / "state.sqlite3"),
    }
    config["backtest"]["start"] = "2023-01-01"
    config["backtest"]["end"] = None
    config["strategy"]["lookbacks"] = {
        "momentum": 20,
        "long_momentum": 40,
        "volatility": 20,
        "drawdown": 20,
        "liquidity": 20,
    }
    config["strategy"]["top_n"] = 4
    config["strategy"]["max_stock_weight"] = 0.25
    config["strategy"]["max_industry_weight"] = 0.60
    config["risk_overlay"]["enabled"] = False
    config.setdefault("breadth_overlay", {})["enabled"] = False

    store = DataStore.from_config(config)
    make_sample_data(store)
    prices = store.read_frame("prices")
    expected_start = sorted(prices["date"].unique())[max_lookback_days(config)]
    captured: dict[str, pd.DataFrame] = {}

    def capture_outputs(_config, *, equity, orders, **_kwargs):
        captured["equity"] = equity.copy()
        captured["orders"] = orders.copy()
        captured["order_output_name"] = _kwargs.get("order_output_name")
        return {"equity_curve.csv": root / "equity_curve.csv"}

    with patch("ashare_quant.backtest.write_outputs", side_effect=capture_outputs):
        run_backtest(config)

    assert captured["order_output_name"] == "backtest_orders.csv"
    orders = captured["orders"]
    dates = sorted(prices["date"].unique())
    assert not orders.empty
    assert pd.to_datetime(orders["date"]).dt.date.iloc[0] == dates[max_lookback_days(config) + 1]
    equity = captured["equity"]
    assert not equity.empty
    assert pd.to_datetime(equity["date"]).dt.date.iloc[0] == expected_start
    assert len(equity) == len(prices["date"].unique()) - max_lookback_days(config)
