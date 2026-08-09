from datetime import date
from types import SimpleNamespace

import pandas as pd

from ashare_quant.candidate import (
    INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION,
    candidate_benchmark_data_identity,
    candidate_benchmark_mapping_evidence,
    candidate_effective_index_membership_fields,
    candidate_index_membership_sha256,
)
from ashare_quant.index_symbols import (
    benchmark_index_identity,
    canonical_index_symbol,
    provider_index_symbol,
)
from ashare_quant.audit import build_run_manifest
import ashare_quant.index_membership as membership_module
from ashare_quant.index_membership import (
    clear_membership_cache,
    fetch_hs300_membership_baostock,
    normalize_index_membership,
)
from ashare_quant.universe import eligible_universe


class FakeResult:
    def __init__(self, fields, rows):
        self.fields = fields
        self.rows = rows
        self.error_code = "0"
        self.error_msg = "success"
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class FakeBaoStock:
    def login(self):
        return SimpleNamespace(error_code="0", error_msg="success")

    def logout(self):
        return SimpleNamespace(error_code="0", error_msg="success")

    def query_hs300_stocks(self, date=""):
        rows = (
            [["2024-02-01", "sh.600001", "A"], ["2024-02-01", "sz.000002", "B"]]
            if date >= "2024-02-01"
            else [["2024-01-01", "sh.600001", "A"], ["2024-01-01", "sh.600003", "C"]]
        )
        return FakeResult(["updateDate", "code", "code_name"], rows)


def test_fetch_hs300_membership_keeps_point_in_time_snapshots():
    frame = fetch_hs300_membership_baostock(
        [date(2024, 1, 2), date(2024, 2, 2)],
        module=FakeBaoStock(),
    )

    assert len(frame) == 4
    assert frame["snapshot_date"].nunique() == 2
    february = frame[frame["snapshot_date"] == date(2024, 2, 2)]
    assert set(february["symbol"]) == {"600001", "000002"}


def test_eligible_universe_uses_latest_membership_snapshot(monkeypatch):
    membership = normalize_index_membership(
        pd.DataFrame(
            [
                {"snapshot_date": "2024-01-02", "update_date": "2024-01-01", "index_symbol": "000300", "symbol": "600001"},
                {"snapshot_date": "2024-01-02", "update_date": "2024-01-01", "index_symbol": "000300", "symbol": "600003"},
                {"snapshot_date": "2024-02-02", "update_date": "2024-02-01", "index_symbol": "000300", "symbol": "600002"},
            ]
        )
    )
    monkeypatch.setattr(membership_module, "load_membership_from_config", lambda _config: membership)
    stocks = pd.DataFrame(
        [
            {"symbol": symbol, "name": symbol, "board": "main", "industry": "x", "list_date": date(2020, 1, 1), "is_st": False}
            for symbol in ("600001", "600002", "600003")
        ]
    )
    prices = pd.DataFrame(
        [
            {"date": date(2024, 2, 5), "symbol": symbol, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100, "amount": 100}
            for symbol in ("600001", "600002", "600003")
        ]
    )
    config = {
        "paths": {"cache_dir": "unused"},
        "benchmark": {"symbol": "000300"},
        "universe": {
            "boards": ["main"],
            "exclude_st": True,
            "min_listing_days": 60,
            "min_avg_amount": 1,
            "liquidity_lookback": 1,
        },
        "strategy": {
            "benchmark_membership": {
                "enabled": True,
                "index_symbol": "000300",
                "max_staleness_days": 120,
            }
        },
    }

    eligible = eligible_universe(stocks, prices, date(2024, 2, 5), config)

    assert eligible["symbol"].tolist() == ["600002"]


def test_manifest_signs_membership_used_by_strategy(monkeypatch):
    config = {
        "paths": {"cache_dir": "unused"},
        "backtest": {"start": "2024-01-01", "end": "2024-12-31"},
        "strategy": {"name": "index-aware", "benchmark_membership": {"enabled": True}},
    }
    first = normalize_index_membership(
        pd.DataFrame(
            [{"snapshot_date": "2024-01-02", "index_symbol": "000300", "symbol": "600001"}]
        )
    )
    monkeypatch.setattr(membership_module, "load_membership_from_config", lambda _config: first)
    before_manifest = build_run_manifest(config)
    before = dict(zip(before_manifest["item"], before_manifest["value"]))

    second = normalize_index_membership(
        pd.DataFrame(
            [{"snapshot_date": "2024-01-02", "index_symbol": "000300", "symbol": "600002"}]
        )
    )
    monkeypatch.setattr(membership_module, "load_membership_from_config", lambda _config: second)
    after_manifest = build_run_manifest(config)
    after = dict(zip(after_manifest["item"], after_manifest["value"]))

    assert before["index_membership_rows"] == "1"
    assert before["index_membership_sha256"] != after["index_membership_sha256"]


def test_index_symbol_aliases_map_to_provider_neutral_code():
    aliases = ["000300", 300, 300.0, "sh000300", "sh.000300", "000300.SH", "CSI300", "HS300", "沪深300"]

    assert {canonical_index_symbol(value) for value in aliases} == {"000300"}
    assert canonical_index_symbol(None) == ""
    assert canonical_index_symbol(pd.NA) == ""


def test_index_provider_mapping_repairs_exchange_prefixes():
    assert provider_index_symbol("sz.000300", "baostock") == "sh.000300"
    assert provider_index_symbol("000300.SH", "sina") == "sh000300"
    assert provider_index_symbol("399006", "baostock") == "sz.399006"
    identity = benchmark_index_identity("HS300")
    assert identity["benchmark_symbol_canonical"] == "000300"
    assert identity["benchmark_symbol_akshare"] == "000300"
    assert identity["benchmark_index_mapping_schema_version"] == 2


def test_membership_normalization_canonicalizes_provider_index_symbols():
    membership = normalize_index_membership(
        pd.DataFrame(
            [
                {"snapshot_date": "2024-01-02", "index_symbol": "sh.000300", "symbol": "sh.600001"},
                {"snapshot_date": "2024-01-02", "index_symbol": "CSI300", "symbol": "sz.000002"},
            ]
        )
    )

    assert membership["index_symbol"].tolist() == ["000300", "000300"]
    assert membership["symbol"].tolist() == ["000002", "600001"]


def test_semantic_membership_hash_ignores_collection_metadata_and_column_order():
    first = normalize_index_membership(
        pd.DataFrame(
            [
                {
                    "snapshot_date": "2024-01-02",
                    "update_date": "2024-01-01",
                    "index_symbol": "000300",
                    "symbol": "600001",
                    "name": "A",
                    "source": "first",
                    "retrieved_at": "2024-01-02T18:00:00",
                }
            ]
        )
    )
    second = first.copy()
    second["name"] = "renamed"
    second["source"] = "refreshed"
    second["retrieved_at"] = "2024-01-03T18:00:00"
    second = second[list(reversed(second.columns))]

    first_hash = candidate_index_membership_sha256(first, INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION)
    second_hash = candidate_index_membership_sha256(second, INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION)
    changed = second.copy()
    changed["symbol"] = "600002"

    assert first_hash == second_hash
    assert candidate_index_membership_sha256(changed, INDEX_MEMBERSHIP_HASH_SCHEMA_VERSION) != first_hash

def test_effective_membership_hash_binds_latest_snapshot_only():
    membership = normalize_index_membership(
        pd.DataFrame(
            [
                {"snapshot_date": "2024-01-02", "index_symbol": "000300", "symbol": "600001"},
                {"snapshot_date": "2024-02-01", "index_symbol": "000300", "symbol": "600002"},
            ]
        )
    )

    fields = candidate_effective_index_membership_fields(membership)
    changed = membership.copy()
    changed.loc[changed["snapshot_date"] == date(2024, 2, 1), "symbol"] = "600003"
    changed_fields = candidate_effective_index_membership_fields(changed)

    assert fields["index_membership_effective_date_at_freeze"] == "2024-02-01"
    assert fields["index_membership_effective_rows_at_freeze"] == 1
    assert fields["index_membership_effective_sha256_at_freeze"] != changed_fields["index_membership_effective_sha256_at_freeze"]


def test_benchmark_mapping_evidence_rejects_wrong_cached_index_symbol():
    config = {"benchmark": {"symbol": "000300"}}
    manifest = {
        **benchmark_index_identity("000300"),
        **candidate_benchmark_data_identity(
            pd.DataFrame([{"date": "2024-01-02", "symbol": "399006"}]),
            "2024-01-02",
        ),
    }

    passed, current, frozen = candidate_benchmark_mapping_evidence(config, manifest)

    assert not passed
    assert current["benchmark_data_symbol_canonical_at_freeze"] == "000300"
    assert frozen["benchmark_data_symbol_canonical_at_freeze"] == "399006"
