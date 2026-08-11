from pathlib import Path

import pandas as pd

from ashare_quant.data import (
    DataStore,
    benchmark_membership_fetch_symbols,
    ensure_benchmark_membership_fetch_prerequisites,
)
from ashare_quant.index_membership import (
    build_index_membership_quality,
    current_index_membership_quality,
    index_membership_data_quality_rows,
)
from ashare_quant.readiness import add_index_membership_readiness
from ashare_quant.report import write_html_report


def quality_config() -> dict:
    return {
        "benchmark": {"symbol": "000300"},
        "backtest": {"start": "2024-01-02", "end": "2024-02-02"},
        "strategy": {
            "benchmark_membership": {
                "enabled": True,
                "index_symbol": "000300",
                "expected_constituent_count": 2,
                "max_staleness_days": 40,
            }
        },
    }


def membership_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "snapshot_date": snapshot,
                "update_date": snapshot,
                "index_symbol": "000300",
                "symbol": symbol,
                "source": "baostock.query_hs300_stocks",
            }
            for snapshot, symbols in (
                ("2024-01-02", ("600001", "600002")),
                ("2024-02-02", ("600001", "600003")),
            )
            for symbol in symbols
        ]
    )


def market_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stocks = pd.DataFrame(
        [{"symbol": symbol} for symbol in ("600001", "600002", "600003")]
    )
    prices = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": symbol, "close": 10.0}
            for symbol in ("600001", "600002", "600003")
        ]
        + [
            {"date": "2024-02-02", "symbol": symbol, "close": 11.0}
            for symbol in ("600001", "600002", "600003")
        ]
    )
    benchmark = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "000300", "name": "沪深300", "close": 100.0},
            {"date": "2024-02-02", "symbol": "000300", "name": "沪深300", "close": 101.0},
        ]
    )
    return stocks, prices, benchmark


def audit_by_check(audit: pd.DataFrame) -> dict[str, bool]:
    return {
        str(row.check): bool(row.passed)
        for row in audit.itertuples(index=False)
    }


def test_membership_only_fetch_uses_all_research_period_members():
    root = Path.cwd() / "test_outputs" / "membership_history_union"
    config = quality_config()
    config["paths"] = {
        "cache_dir": str(root / "cache"),
        "state_db": str(root / "state.sqlite3"),
    }
    config["data"] = {"fetch_benchmark_membership_only": True}
    store = DataStore.from_config(config)
    store.write_frame("index_membership", membership_frame())

    assert benchmark_membership_fetch_symbols(store, config) == [
        "600001",
        "600002",
        "600003",
    ]


def test_membership_quality_cold_start_is_blocked_instead_of_crashing():
    root = Path.cwd() / "test_outputs" / "membership_quality_cold_start"
    config = quality_config()
    config["paths"] = {
        "cache_dir": str(root / "cache"),
        "output_dir": str(root / "outputs"),
        "state_db": str(root / "state.sqlite3"),
    }
    store = DataStore.from_config(config)
    store.write_frame("index_membership", membership_frame())
    store.write_frame("benchmark", market_frames()[2])

    audit, snapshots = current_index_membership_quality(config)

    assert not audit_by_check(audit)["point_in_time_membership_ready"]
    assert not snapshots["passed"].any()


def test_fetch_cold_start_prepares_benchmark_and_membership(monkeypatch, tmp_path):
    root = tmp_path / "membership_fetch_cold_start"
    config = quality_config()
    config["paths"] = {
        "cache_dir": str(root / "cache"),
        "output_dir": str(root / "outputs"),
        "state_db": str(root / "state.sqlite3"),
    }
    config["data"] = {
        "fetch_benchmark_membership_only": True,
        "benchmark_fallback_to_baostock": False,
    }
    store = DataStore.from_config(config)
    benchmark = market_frames()[2]
    monkeypatch.setattr(
        "ashare_quant.data.fetch_benchmark_akshare",
        lambda _ak, _config: benchmark,
    )

    def refresh(_config):
        return {
            "index_membership": store.write_frame(
                "index_membership", membership_frame()
            )
        }

    monkeypatch.setattr(
        "ashare_quant.index_membership.refresh_benchmark_membership", refresh
    )

    paths = ensure_benchmark_membership_fetch_prerequisites(store, config, object())

    assert len(paths) == 2
    assert not store.load_benchmark(required=True).empty
    assert benchmark_membership_fetch_symbols(store, config) == [
        "600001",
        "600002",
        "600003",
    ]


def test_complete_point_in_time_membership_passes_quality_gate():
    stocks, prices, benchmark = market_frames()

    audit, snapshots = build_index_membership_quality(
        quality_config(), membership_frame(), stocks, prices, benchmark
    )
    checks = audit_by_check(audit)

    assert checks["index_membership_historical_member_price_coverage"]
    assert checks["index_membership_minimum_snapshot_price_coverage"]
    assert checks["point_in_time_membership_ready"]
    assert snapshots["passed"].all()


def test_missing_exited_member_price_fails_closed_at_full_coverage():
    stocks, prices, benchmark = market_frames()
    prices = prices[prices["symbol"] != "600002"]

    audit, snapshots = build_index_membership_quality(
        quality_config(), membership_frame(), stocks, prices, benchmark
    )
    checks = audit_by_check(audit)
    quality = index_membership_data_quality_rows(audit)
    captured = []

    def add(check, passed, value, threshold, message):
        captured.append((check, passed, value, threshold, message))

    add_index_membership_readiness(add, quality, quality_config())

    assert not checks["index_membership_historical_member_price_coverage"]
    assert not checks["index_membership_minimum_snapshot_price_coverage"]
    assert not checks["point_in_time_membership_ready"]
    assert snapshots["price_history_coverage_ratio"].min() == 0.5
    assert captured[0][0] == "point_in_time_membership_ready"
    assert captured[0][1] is False


def test_future_membership_update_date_fails_quality_gate():
    stocks, prices, benchmark = market_frames()
    membership = membership_frame()
    membership.loc[0, "update_date"] = "2024-01-03"

    audit, _ = build_index_membership_quality(
        quality_config(), membership, stocks, prices, benchmark
    )
    checks = audit_by_check(audit)

    assert not checks["index_membership_no_future_update"]
    assert not checks["point_in_time_membership_ready"]


def test_report_renders_membership_quality_gate_in_chinese():
    stocks, prices, benchmark = market_frames()
    prices = prices[prices["symbol"] != "600002"]
    audit, snapshots = build_index_membership_quality(
        quality_config(), membership_frame(), stocks, prices, benchmark
    )
    path = Path("test_outputs") / "membership_quality_report.html"

    write_html_report(
        path,
        index_membership_quality=audit,
        index_membership_snapshots=snapshots,
    )

    html = path.read_text(encoding="utf-8")
    assert "时点指数成分质量门禁" in html
    assert "时点指数成分快照覆盖" in html
    assert "缺失退出指数的老成员会重新引入幸存者偏差" in html



