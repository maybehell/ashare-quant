import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ashare_quant.data import (
    DataStore,
    committed_fetch_market_scope,
    scope_market_frames_to_committed_fetch,
    commit_formal_fetch_then_progress,

    akshare_network_context,
    build_fetch_summary,
    build_history_backfill_progress,
    classify_history_batch_source_status,
    apply_stock_info,
    apply_stock_profile_cninfo,
    cached_universe_symbols,
    benchmark_membership_fetch_symbols,
    enrich_stock_metadata_checkpointed,
    expected_history_start_dates,
    fetch_start_for_symbol,
    finalized_market_data_end,
    fetch_symbol_history,
    fetch_baostock_history_batch,
    fetch_benchmark_akshare,
    fetch_stock_spot,
    normalize_benchmark,
    normalize_industry,
    normalize_trade_calendar,
    normalize_stocks,
    history_from_tx,
    history_overlap_quality,
    historical_backfill_required,
    history_backfill_completion_status,
    history_staging_frame_name,
    infer_board,
    ensure_required_fetch_symbols,
    immutable_fetch_start,
    immutable_candidate_freeze_date,
    merge_benchmark_cache,
    merge_fetched_cache,
    metadata_refresh_symbols,
    price_frame_issues,
    repair_tx_history_with_hfq,
    reusable_metadata_cache,
    recover_partial_prices,
    resolve_finalized_trading_date,
    resumable_partial_history,
    select_fetch_symbols,
    stocks_from_spot,
    tx_symbol,
    unfinished_history_symbols,
    write_fetch_outputs,
)


class CommittedFetchMarketScopeTests(unittest.TestCase):
    def test_filters_retained_history_to_latest_committed_fetch_symbols(self):
        class Store:
            def read_frame(self, name):
                if name == "fetch_summary":
                    return pd.DataFrame([{
                        "requested_symbols": 2,
                        "fetched_symbols": 2,
                        "failed_items": 0,
                        "stage": "complete",
                    }])
                return pd.DataFrame([{"symbol": "600000"}, {"symbol": "000001"}])

        prices = pd.DataFrame([
            {"symbol": "600000", "date": "2026-07-31", "close": 10},
            {"symbol": "000001.SZ", "date": "2026-07-31", "close": 11},
            {"symbol": "300001", "date": "2026-07-21", "close": 12},
        ])
        scoped, passed, value = committed_fetch_market_scope(Store(), prices)
        self.assertTrue(passed)
        self.assertEqual(set(scoped["symbol"]), {"600000", "000001"})
        self.assertIn("bound_symbols=2", value)


    def test_scopes_all_symbol_frames_to_one_committed_fetch_identity(self):
        class Store:
            def read_frame(self, name):
                if name == "fetch_summary":
                    return pd.DataFrame([{
                        "requested_symbols": 2,
                        "fetched_symbols": 2,
                        "failed_items": 0,
                        "stage": "complete",
                    }])
                return pd.DataFrame([{"symbol": "600000"}, {"symbol": "000001"}])

        stocks = pd.DataFrame(
            [{"symbol": "600000"}, {"symbol": "000001.SZ"}, {"symbol": "300001"}]
        )
        prices = pd.DataFrame(
            [
                {"symbol": "600000", "date": "2026-07-31", "close": 10},
                {"symbol": "000001.SZ", "date": "2026-07-31", "close": 11},
                {"symbol": "300001", "date": "2026-07-31", "close": 12},
            ]
        )
        fundamentals = pd.DataFrame(
            [{"symbol": "600000"}, {"symbol": "000001"}, {"symbol": "300001"}]
        )

        scoped_stocks, scoped_prices, scoped_fundamentals, passed, _ = (
            scope_market_frames_to_committed_fetch(
                Store(), stocks, prices, fundamentals
            )
        )

        self.assertTrue(passed)
        self.assertEqual(set(scoped_prices["symbol"]), {"600000", "000001"})
        self.assertEqual(len(scoped_stocks), 2)
        self.assertEqual(len(scoped_fundamentals), 2)

    def test_invalid_manifest_does_not_filter_prices(self):
        class Store:
            def read_frame(self, name):
                if name == "fetch_summary":
                    return pd.DataFrame([{
                        "requested_symbols": 2,
                        "fetched_symbols": 2,
                        "failed_items": 0,
                        "stage": "complete",
                    }])
                return pd.DataFrame([{"symbol": "600000"}])

        prices = pd.DataFrame([{"symbol": "600000", "date": "2026-07-31", "close": 10}])
        scoped, passed, value = committed_fetch_market_scope(Store(), prices)
        self.assertFalse(passed)
        self.assertEqual(len(scoped), len(prices))
        self.assertIn("bound_symbols=1", value)


class StockMetadataNormalizationTests(unittest.TestCase):
    def test_repairs_gb18030_mojibake_and_removes_industry_code_prefix(self):
        self.assertEqual(normalize_industry("Ò½Ò©ÖÆÔìÒµ"), "医药制造业")
        self.assertEqual(
            normalize_industry("C39¼ÆËã»ú¡¢Í¨ÐÅºÍÆäËûµç×ÓÉè±¸ÖÆÔìÒµ"),
            "计算机、通信和其他电子设备制造业",
        )
        self.assertEqual(
            normalize_industry("计算机、通信和其他电子设备制造业"),
            "计算机、通信和其他电子设备制造业",
        )

    def test_normalizes_missing_industry_tokens(self):
        for value in (None, "", "unknown", "未知", "未分类", "nan", "--"):
            self.assertEqual(normalize_industry(value), "unknown")

    def test_stock_normalization_merges_encoded_industry_aliases(self):
        stocks = pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "board": "main",
                    "industry": "C27Ò½Ò©ÖÆÔìÒµ",
                    "list_date": "1999-11-10",
                    "is_st": False,
                },
                {
                    "symbol": "600001",
                    "name": "B",
                    "board": "main",
                    "industry": "医药制造业",
                    "list_date": "2000-01-01",
                    "is_st": False,
                },
            ]
        )

        normalized = normalize_stocks(stocks)

        self.assertEqual(set(normalized["industry"]), {"医药制造业"})

class DataFetchTests(unittest.TestCase):
    def test_incremental_fetch_start_uses_cached_latest_with_overlap(self):
        start = fetch_start_for_symbol(
            "000001",
            pd.Timestamp("2026-01-01").date(),
            {"000001": pd.Timestamp("2026-06-20").date()},
            {"data": {"incremental_overlap_days": 10}},
        )
        self.assertEqual(str(start), "2026-06-10")

        clamped = fetch_start_for_symbol(
            "000001",
            pd.Timestamp("2026-01-01").date(),
            {"000001": pd.Timestamp("2026-01-05").date()},
            {"data": {"incremental_overlap_days": 20}},
        )
        self.assertEqual(str(clamped), "2026-01-01")

        missing = fetch_start_for_symbol(
            "000002",
            pd.Timestamp("2026-01-01").date(),
            {"000001": pd.Timestamp("2026-06-20").date()},
            {"data": {"incremental_overlap_days": 10}},
        )
        self.assertEqual(str(missing), "2026-01-01")

    def test_incremental_fetch_start_backfills_a_real_historical_gap(self):
        expected_start = date(2018, 1, 1)
        cached_earliest = {"000001": date(2023, 1, 3)}
        start = fetch_start_for_symbol(
            "000001",
            expected_start,
            {"000001": date(2026, 8, 7)},
            {"data": {"incremental_overlap_days": 20, "history_backfill_tolerance_days": 31}},
            cached_earliest=cached_earliest,
        )

        self.assertEqual(start, expected_start)
        self.assertTrue(
            historical_backfill_required(
                "000001",
                expected_start,
                cached_earliest,
                {"data": {"history_backfill_tolerance_days": 31}},
            )
        )

    def test_listing_date_prevents_false_history_gap_for_new_stock(self):
        starts = expected_history_start_dates(
            pd.DataFrame(
                [
                    {"symbol": "000001", "list_date": "1991-04-03"},
                    {"symbol": "301001", "list_date": "2024-06-20"},
                ]
            ),
            ["000001", "301001"],
            date(2018, 1, 1),
        )

        self.assertEqual(starts["000001"], date(2018, 1, 1))
        self.assertEqual(starts["301001"], date(2024, 6, 20))
        self.assertFalse(
            historical_backfill_required(
                "301001",
                starts["301001"],
                {"301001": date(2024, 7, 1)},
                {"data": {"history_backfill_tolerance_days": 31}},
            )
        )
    def test_history_staging_namespace_is_validated(self):
        self.assertEqual(
            history_staging_frame_name(
                {"data": {"history_staging_namespace": "extended_history"}},
                "prices_partial",
            ),
            "prices_partial_extended_history",
        )
        with self.assertRaises(ValueError):
            history_staging_frame_name(
                {"data": {"history_staging_namespace": "../unsafe"}},
                "prices_partial",
            )

    def test_partial_history_resume_requires_full_requested_coverage(self):
        root = Path.cwd() / "test_outputs" / "partial_history_resume"
        store = DataStore(cache_dir=root / "cache", state_db=root / "state.sqlite3")
        store.write_frame(
            "prices_partial",
            pd.DataFrame(
                [
                    {"date": "2018-01-02", "symbol": "000001", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100, "amount": 1000},
                    {"date": "2026-08-07", "symbol": "000001", "open": 11, "high": 11, "low": 11, "close": 11, "volume": 100, "amount": 1100},
                    {"date": "2023-01-03", "symbol": "000002", "open": 20, "high": 20, "low": 20, "close": 20, "volume": 100, "amount": 2000},
                    {"date": "2026-08-07", "symbol": "000002", "open": 21, "high": 21, "low": 21, "close": 21, "volume": 100, "amount": 2100},
                ]
            ),
        )

        store.write_frame(
            "fetch_history_partial_provenance",
            pd.DataFrame(
                [
                    {
                        "symbol": "000001",
                        "source": "baostock",
                        "amount_quality": "derived_turnover",
                    }
                ]
            ),
        )
        frames, provenance, resumed = resumable_partial_history(
            store,
            ["000001", "000002"],
            {"000001": date(2018, 1, 1), "000002": date(2018, 1, 1)},
            date(2026, 8, 7),
            {"data": {"resume_partial_history": True, "history_backfill_tolerance_days": 31}},
        )

        self.assertEqual(resumed, {"000001"})
        self.assertEqual(len(frames), 1)
        self.assertEqual(provenance[0]["source"], "baostock")
        self.assertEqual(provenance[0]["amount_quality"], "derived_turnover")
        self.assertEqual(provenance[0]["rows"], 2)
    def test_partial_history_strict_resume_rejects_unknown_or_estimated_amount(self):
        root = Path.cwd() / "test_outputs" / "partial_history_strict_provenance"
        store = DataStore(cache_dir=root / "cache", state_db=root / "state.sqlite3")
        rows = []
        for symbol in ["000001", "000002", "000003"]:
            rows.extend(
                [
                    {"date": "2018-01-02", "symbol": symbol, "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100, "amount": 1000},
                    {"date": "2026-08-07", "symbol": symbol, "open": 11, "high": 11, "low": 11, "close": 11, "volume": 100, "amount": 1100},
                ]
            )
        store.write_frame("prices_partial", pd.DataFrame(rows))
        store.write_frame(
            "fetch_history_partial_provenance",
            pd.DataFrame(
                [
                    {"symbol": "000001", "source": "stock_zh_a_daily", "amount_quality": "reported"},
                    {"symbol": "000002", "source": "stock_zh_a_hist_tx", "amount_quality": "estimated_close_x_volume"},
                ]
            ),
        )

        frames, provenance, resumed = resumable_partial_history(
            store,
            ["000001", "000002", "000003"],
            {symbol: date(2018, 1, 1) for symbol in ["000001", "000002", "000003"]},
            date(2026, 8, 7),
            {
                "data": {
                    "resume_partial_history": True,
                    "history_backfill_tolerance_days": 31,
                    "history_resume_require_trusted_provenance": True,
                }
            },
        )

        self.assertEqual(resumed, {"000001"})
        self.assertEqual(len(frames), 1)
        self.assertEqual(provenance[0]["amount_quality"], "reported")
    def test_merge_fetched_cache_preserves_failed_symbol_history_and_cached_benchmark(self):
        root = Path.cwd() / "test_outputs" / "merge_fetched_cache"
        config = {
            "paths": {"cache_dir": str(root / "cache"), "state_db": str(root / "state.sqlite3")},
            "data": {"preserve_cached_history_on_fetch": True},
        }
        store = DataStore.from_config(config)
        store.write_frame(
            "stocks",
            pd.DataFrame(
                [
                    {"symbol": "000001", "name": "Old A", "board": "main", "industry": "old", "list_date": "2020-01-01", "is_st": False},
                    {"symbol": "000002", "name": "Old B", "board": "main", "industry": "old", "list_date": "2020-01-01", "is_st": False},
                ]
            ),
        )
        store.write_frame(
            "prices",
            pd.DataFrame(
                [
                    {"date": "2026-01-01", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 10},
                    {"date": "2026-01-01", "symbol": "000002", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1, "amount": 20},
                ]
            ),
        )
        store.write_frame("benchmark", pd.DataFrame([{"date": "2026-01-01", "symbol": "000300", "name": "沪深300", "close": 100}]))
        fetched_stocks = pd.DataFrame(
            [
                {"symbol": "000001", "name": "New A", "board": "main", "industry": "new", "list_date": "2020-01-01", "is_st": False},
                {"symbol": "000003", "name": "New C", "board": "main", "industry": "new", "list_date": "2020-01-01", "is_st": False},
            ]
        )
        fetched_prices = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "000001", "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1, "volume": 1, "amount": 11},
                {"date": "2026-01-02", "symbol": "000003", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 1, "amount": 30},
            ]
        )

        stocks, prices, benchmark = merge_fetched_cache(store, fetched_stocks, fetched_prices, pd.DataFrame(), config)

        self.assertIn("000002", prices["symbol"].astype(str).tolist())
        self.assertIn("000003", prices["symbol"].astype(str).tolist())
        self.assertEqual(stocks.loc[stocks["symbol"] == "000001", "name"].iloc[0], "New A")
        self.assertFalse(benchmark.empty)
        self.assertEqual(str(max(benchmark["date"])), "2026-01-01")

    def test_membership_fetch_symbols_use_latest_snapshot(self):
        self.assertEqual(infer_board("302132"), "gem")
        self.assertEqual(immutable_fetch_start({"data": {}}, date(2026, 1, 2)), date(2026, 1, 2))

        root = Path.cwd() / "test_outputs" / "membership_fetch_symbols"
        config = {
            "paths": {
                "cache_dir": str(root / "cache"),
                "state_db": str(root / "state.sqlite3"),
            },
            "strategy": {
                "benchmark_membership": {
                    "enabled": True,
                    "index_symbol": "000300",
                }
            },
            "data": {"include_account_positions_on_fetch": False},
        }
        store = DataStore.from_config(config)
        store.write_frame(
            "index_membership",
            pd.DataFrame(
                [
                    {"snapshot_date": "2026-01-01", "index_symbol": "000300", "symbol": "600000"},
                    {"snapshot_date": "2026-02-01", "index_symbol": "000300", "symbol": "000001"},
                    {"snapshot_date": "2026-02-01", "index_symbol": "000300", "symbol": "600001"},
                    {"snapshot_date": "2026-02-01", "index_symbol": "000905", "symbol": "000002"},
                ]
            ),
        )

        self.assertEqual(
            benchmark_membership_fetch_symbols(store, config),
            ["000001", "600001"],
        )
        self.assertEqual(
            ensure_required_fetch_symbols([], store, config),
            ["000001", "600001"],
        )

    def test_candidate_cache_keeps_freeze_date_and_earlier_immutable(self):
        root = Path.cwd() / "test_outputs" / "immutable_candidate_cache"
        candidate_path = root / "candidate.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            '{"freeze_price_end": "2026-01-02"}',
            encoding="utf-8",
        )
        config = {
            "paths": {
                "cache_dir": str(root / "cache"),
                "state_db": str(root / "state.sqlite3"),
            },
            "validation": {"candidate_path": str(candidate_path)},
            "data": {
                "preserve_cached_history_on_fetch": True,
                "immutable_before_candidate_freeze": True,
            },
        }
        store = DataStore.from_config(config)
        stocks = pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "name": "A",
                    "board": "main",
                    "industry": "bank",
                    "list_date": "2020-01-01",
                    "is_st": False,
                }
            ]
        )
        cached_prices = pd.DataFrame(
            [
                {"date": "2026-01-01", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1},
                {"date": "2026-01-02", "symbol": "000001", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2, "amount": 4},
            ]
        )
        store.write_frame("stocks", stocks)
        store.write_frame("prices", cached_prices)
        store.write_frame(
            "benchmark",
            pd.DataFrame(
                [
                    {"date": "2026-01-02", "symbol": "000300", "name": "CSI300", "close": 100},
                ]
            ),
        )
        fresh_prices = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "000001", "open": 20, "high": 20, "low": 20, "close": 20, "volume": 20, "amount": 400},
                {"date": "2026-01-03", "symbol": "000001", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 3, "amount": 9},
            ]
        )
        fresh_benchmark = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "000300", "name": "CSI300", "close": 999},
                {"date": "2026-01-03", "symbol": "000300", "name": "CSI300", "close": 101},
            ]
        )

        _, prices, benchmark = merge_fetched_cache(
            store,
            stocks,
            fresh_prices,
            fresh_benchmark,
            config,
        )

        self.assertEqual(immutable_candidate_freeze_date(config), date(2026, 1, 2))
        self.assertEqual(immutable_fetch_start(config, date(2024, 1, 1)), date(2026, 1, 3))
        merged_benchmark = merge_benchmark_cache(store, pd.DataFrame([
            {"date": "2026-01-02", "symbol": "000300", "name": "new", "close": 999.0},
            {"date": "2026-01-03", "symbol": "000300", "name": "new", "close": 101.0},
        ]), config)
        self.assertEqual(float(merged_benchmark.loc[merged_benchmark["date"] == date(2026, 1, 2), "close"].iloc[0]), 100.0)
        self.assertEqual(float(merged_benchmark.loc[merged_benchmark["date"] == date(2026, 1, 3), "close"].iloc[0]), 101.0)
        self.assertEqual(
            float(prices.loc[prices["date"] == date(2026, 1, 2), "close"].iloc[0]),
            2.0,
        )
        self.assertEqual(
            float(prices.loc[prices["date"] == date(2026, 1, 3), "close"].iloc[0]),
            3.0,
        )
        self.assertEqual(
            float(benchmark.loc[benchmark["date"] == date(2026, 1, 2), "close"].iloc[0]),
            100.0,
        )
        self.assertEqual(
            float(benchmark.loc[benchmark["date"] == date(2026, 1, 3), "close"].iloc[0]),
            101.0,
        )

    def test_history_batch_source_status_distinguishes_outage_from_symbol_failures(self):
        symbols = ["600000", "000001"]
        self.assertEqual(
            classify_history_batch_source_status(
                symbols,
                set(),
                {
                    "600000": "baostock session attempt 2/2: timed out",
                    "000001": "baostock session attempt 2/2: timed out",
                },
            ),
            "source_unavailable",
        )
        self.assertEqual(
            classify_history_batch_source_status(
                symbols,
                set(),
                {"600000": "empty history", "000001": "invalid rows"},
            ),
            "failed",
        )
        self.assertEqual(
            classify_history_batch_source_status(
                symbols,
                {"600000"},
                {"000001": "temporary failure"},
            ),
            "degraded",
        )
        self.assertEqual(
            classify_history_batch_source_status(symbols, set(symbols), {}),
            "ok",
        )

    def test_history_backfill_progress_distinguishes_progress_from_completion(self):
        frame = pd.DataFrame(
            [{"symbol": "000001", "date": "2018-01-02", "close": 10}]
        )
        progress = build_history_backfill_progress(
            ["000001", "000002", "000003"],
            [frame],
            [],
            {"000001"},
            ["000003"],
            date(2018, 1, 1),
            date(2026, 8, 7),
            1,
            status="in_progress",
            source_status="source_unavailable",
            attempted_symbols=1,
            successful_symbols=0,
        ).iloc[0]

        self.assertEqual(progress["status"], "in_progress")
        self.assertEqual(int(progress["completed_symbols"]), 1)
        self.assertEqual(int(progress["remaining_symbols"]), 2)
        self.assertEqual(int(progress["deferred_symbols"]), 1)
        self.assertEqual(int(progress["request_budget"]), 1)
        self.assertEqual(progress["source_status"], "source_unavailable")
        self.assertEqual(int(progress["attempted_symbols"]), 1)
        self.assertEqual(int(progress["successful_symbols"]), 0)
    def test_history_completion_requires_zero_final_fetch_failures(self):
        self.assertEqual(history_backfill_completion_status([]), "complete")
        self.assertEqual(
            history_backfill_completion_status(
                [{"symbol": "000300", "stage": "benchmark", "error": "timeout"}]
            ),
            "in_progress",
        )

    def test_budgeted_history_remains_incomplete_until_every_symbol_finishes(self):
        frames = [
            pd.DataFrame([{"symbol": "000001", "date": "2026-08-07"}]),
            pd.DataFrame([{"symbol": "000003", "date": "2026-08-07"}]),
        ]

        self.assertEqual(
            unfinished_history_symbols(["000001", "000002", "000003"], frames),
            ["000002"],
        )

    def test_history_completion_is_written_after_formal_outputs(self):
        events = []

        class Store:
            def write_frame(self, name, frame):
                events.append(("progress", name, frame.iloc[0]["status"]))
                return Path(f"{name}.parquet")

        def formal_writer():
            events.append(("formal", "prices", "complete"))
            return [Path("prices.parquet")]

        paths = commit_formal_fetch_then_progress(
            formal_writer,
            Store(),
            "history_progress_isolated",
            pd.DataFrame([{"status": "complete"}]),
        )

        self.assertEqual(events[0][0], "formal")
        self.assertEqual(events[1], ("progress", "history_progress_isolated", "complete"))
        self.assertEqual(paths[-1], Path("history_progress_isolated.parquet"))

    def test_history_completion_is_absent_when_formal_commit_fails(self):
        writes = []

        class Store:
            def write_frame(self, name, frame):
                writes.append(name)
                return Path(f"{name}.parquet")

        def failing_writer():
            raise RuntimeError("formal commit failed")

        with self.assertRaisesRegex(RuntimeError, "formal commit failed"):
            commit_formal_fetch_then_progress(
                failing_writer,
                Store(),
                "history_progress_isolated",
                pd.DataFrame([{"status": "complete"}]),
            )

        self.assertEqual(writes, [])

    def test_fetch_summary_reports_preserved_symbols(self):
        price_frames = [pd.DataFrame([{"symbol": "000001", "date": "2026-01-02"}])]
        summary = build_fetch_summary(
            ["000001", "000002"],
            price_frames,
            [{"symbol": "000002", "stage": "history", "error": "timeout"}],
            pd.DataFrame([{"date": "2026-01-02"}]),
            0,
            historical_backfill_symbols=["000001"],
        )

        row = summary.iloc[0]
        self.assertEqual(int(row["requested_symbols"]), 2)
        self.assertEqual(int(row["fetched_symbols"]), 1)
        self.assertEqual(int(row["preserved_failed_symbols"]), 1)
        self.assertEqual(int(row["failed_items"]), 1)
        self.assertEqual(int(row["historical_backfill_symbols"]), 1)
    def test_recover_partial_prices_merges_partial_and_preserves_benchmark(self):
        root = Path.cwd() / "test_outputs" / "recover_partial_prices"
        config = {
            "paths": {"cache_dir": str(root / "cache"), "state_db": str(root / "state.sqlite3")},
            "data": {"preserve_cached_history_on_fetch": True, "min_fetch_success_symbols": 1, "max_symbols": 2},
            "universe": {"boards": ["main"], "liquidity_lookback": 20},
        }
        store = DataStore.from_config(config)
        stocks = pd.DataFrame(
            [
                {"symbol": "000001", "name": "A", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False},
                {"symbol": "000002", "name": "B", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False},
            ]
        )
        store.write_frame("stocks", stocks)
        store.write_frame(
            "prices",
            pd.DataFrame(
                [
                    {"date": "2026-01-01", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 10},
                    {"date": "2026-01-01", "symbol": "000002", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1, "amount": 20},
                ]
            ),
        )
        store.write_frame("fundamentals", pd.DataFrame(columns=["date", "symbol", "pe", "pb", "roe"]))
        store.write_frame("benchmark", pd.DataFrame([{"date": "2026-01-01", "symbol": "000300", "name": "沪深300", "close": 100}]))
        store.write_frame(
            "prices_partial",
            pd.DataFrame(
                [{"date": "2026-01-02", "symbol": "000001", "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1, "volume": 1, "amount": 11}]
            ),
        )

        recover_partial_prices(store, config)

        prices = store.read_frame("prices")
        summary = store.read_frame("fetch_summary")
        benchmark = store.read_frame("benchmark")
        self.assertIn("2026-01-02", set(pd.to_datetime(prices["date"]).dt.strftime("%Y-%m-%d")))
        self.assertEqual(summary.iloc[0]["stage"], "partial_recovery")
        self.assertEqual(str(pd.to_datetime(benchmark["date"]).dt.date.max()), "2026-01-01")

    def test_required_fetch_symbols_include_sqlite_positions_beyond_max_symbols(self):
        root = Path.cwd() / "test_outputs" / "fetch_required_positions"
        config = {"paths": {"cache_dir": str(root / "cache"), "state_db": str(root / "state.sqlite3")}, "data": {"max_symbols": 1}}
        store = DataStore.from_config(config)
        store.init_db()
        import sqlite3
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert or replace into positions(symbol, quantity, cost, locked_quantity) values ('000002', 100, 10, 0)")
            conn.execute("insert or replace into positions(symbol, quantity, cost, locked_quantity) values ('920000', 100, 10, 0)")

        symbols = ensure_required_fetch_symbols(["000001"], store, config)

        self.assertEqual(symbols, ["000001", "000002"])

    def test_required_fetch_symbols_can_be_disabled(self):
        root = Path.cwd() / "test_outputs" / "fetch_required_positions_disabled"
        config = {
            "paths": {"cache_dir": str(root / "cache"), "state_db": str(root / "state.sqlite3")},
            "data": {"include_account_positions_on_fetch": False},
        }
        store = DataStore.from_config(config)
        store.init_db()
        import sqlite3
        with sqlite3.connect(store.state_db) as conn:
            conn.execute("insert or replace into positions(symbol, quantity, cost, locked_quantity) values ('000002', 100, 10, 0)")

        symbols = ensure_required_fetch_symbols(["000001"], store, config)

        self.assertEqual(symbols, ["000001"])

    def test_fetch_summary_records_checkpoint_stage(self):
        summary = build_fetch_summary(
            ["000001"],
            [pd.DataFrame([{"symbol": "000001", "date": "2026-01-02"}])],
            [],
            pd.DataFrame([{"date": "2026-01-02"}]),
            0,
            stage="price_checkpoint",
        )

        self.assertEqual(summary.iloc[0]["stage"], "price_checkpoint")

    def test_write_fetch_outputs_persists_price_checkpoint(self):
        root = Path.cwd() / "test_outputs" / "write_fetch_outputs_checkpoint"
        config = {"paths": {"cache_dir": str(root / "cache"), "state_db": str(root / "state.sqlite3")}}
        store = DataStore.from_config(config)
        stocks = pd.DataFrame(
            [{"symbol": "000001", "name": "A", "board": "main", "industry": "bank", "list_date": "2020-01-01", "is_st": False}]
        )
        prices = pd.DataFrame(
            [{"date": "2026-01-02", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 10}]
        )
        fundamentals = pd.DataFrame([{"date": "2026-01-02", "symbol": "000001", "pe": 10, "pb": 1, "roe": 0.1}])
        benchmark = pd.DataFrame([{"date": "2026-01-02", "symbol": "000300", "name": "沪深300", "close": 100}])

        write_fetch_outputs(
            store,
            stocks,
            prices,
            fundamentals,
            benchmark,
            ["000001"],
            [prices],
            [],
            0,
            {"000001": pd.Timestamp("2026-01-01").date()},
            True,
            "price_checkpoint",
            history_provenance=[{"symbol": "000001", "source": "stock_zh_a_hist_tx", "first_date": "2026-01-02", "last_date": "2026-01-02", "rows": 1, "amount_quality": "estimated_close_x_volume", "overlap_status": "passed", "overlap_rows": 20, "overlap_close_median_relative_error": 0.001}],
        )

        self.assertEqual(len(store.read_frame("prices")), 1)
        self.assertEqual(store.read_frame("fetch_summary").iloc[0]["stage"], "price_checkpoint")
        provenance = store.read_frame("fetch_history_provenance")
        self.assertEqual(provenance.iloc[0]["amount_quality"], "estimated_close_x_volume")
        self.assertEqual(provenance.iloc[0]["overlap_status"], "passed")
        self.assertEqual(int(provenance.iloc[0]["overlap_rows"]), 20)

    def test_normalize_benchmark_canonicalizes_provider_symbol_alias(self):
        frame = normalize_benchmark(
            pd.DataFrame(
                [{
                    "date": "2026-01-02",
                    "symbol": "sh.000300",
                    "name": "CSI 300",
                    "close": 4000,
                }]
            )
        )

        self.assertEqual(frame.loc[0, "symbol"], "000300")

    def test_fetch_benchmark_akshare_canonicalizes_alias_before_provider_call(self):
        class FakeAk:
            def __init__(self):
                self.calls = []

            def index_zh_a_hist(self, **kwargs):
                self.calls.append(kwargs)
                return pd.DataFrame([{"日期": "2026-01-02", "收盘": 4000.0}])

        source = FakeAk()
        config = {
            "benchmark": {"symbol": "sh.000300", "name": "沪深300"},
            "backtest": {"start": "2026-01-01", "end": "2026-01-31"},
            "data": {},
        }
        frame = fetch_benchmark_akshare(source, config)

        self.assertEqual(source.calls[0]["symbol"], "000300")
        self.assertEqual(frame.loc[0, "symbol"], "000300")

    def test_akshare_network_context_sets_default_timeout(self):
        import socket
        import requests

        seen = {}
        original = requests.sessions.Session.request

        def fake_request(self, method, url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            seen["proxies"] = kwargs.get("proxies")

            class Response:
                status_code = 200
                text = "ok"

                def raise_for_status(self):
                    return None

            return Response()

        requests.sessions.Session.request = fake_request
        original_socket_timeout = socket.getdefaulttimeout()
        try:
            with akshare_network_context({"data": {"disable_requests_proxy": True, "request_timeout_seconds": 7}}):
                self.assertEqual(socket.getdefaulttimeout(), 7)
                requests.Session().get("http://example.test")
        finally:
            requests.sessions.Session.request = original

        self.assertEqual(seen["timeout"], 7)
        self.assertEqual(seen["proxies"], {})
        self.assertEqual(socket.getdefaulttimeout(), original_socket_timeout)
    def test_baostock_batch_reconnects_after_session_failure(self):
        session_attempts = []
        checkpoints = []

        @contextmanager
        def session_factory():
            session_attempts.append(len(session_attempts) + 1)
            if len(session_attempts) == 1:
                raise RuntimeError("temporary login failure")
            yield object()

        def fetcher(_client, symbol, start, _end):
            frame = pd.DataFrame(
                [{
                    "date": start,
                    "symbol": symbol,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 100.0,
                    "amount": 1000.0,
                }]
            )
            return frame, {"symbol": symbol, "source": "baostock"}

        frames, _provenance, errors = fetch_baostock_history_batch(
            ["600000"],
            {"600000": date(2026, 1, 2)},
            date(2026, 1, 3),
            {"data": {"history_session_retries": 2, "history_session_retry_delay_seconds": 0}},
            session_factory=session_factory,
            fetcher=fetcher,
            on_progress=lambda count, frames, provenance, errors: checkpoints.append(
                (count, len(frames), len(provenance), errors)
            ),
        )

        self.assertEqual(session_attempts, [1, 2])
        self.assertEqual(len(frames), 1)
        self.assertEqual(errors, {})
        self.assertEqual(checkpoints[-1], (1, 1, 1, 0))

    def test_baostock_batch_retries_only_unfinished_symbols(self):
        calls = {"600000": 0, "000001": 0}

        @contextmanager
        def session_factory():
            yield object()

        def fetcher(_client, symbol, start, _end):
            calls[symbol] += 1
            if symbol == "000001" and calls[symbol] == 1:
                raise RuntimeError("temporary symbol failure")
            frame = pd.DataFrame(
                [{
                    "date": start,
                    "symbol": symbol,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 100.0,
                    "amount": 1000.0,
                }]
            )
            return frame, {"symbol": symbol, "source": "baostock"}

        frames, _provenance, errors = fetch_baostock_history_batch(
            ["600000", "000001"],
            {"600000": date(2026, 1, 2), "000001": date(2026, 1, 2)},
            date(2026, 1, 3),
            {"data": {"history_session_retries": 2, "history_session_retry_delay_seconds": 0}},
            session_factory=session_factory,
            fetcher=fetcher,
        )

        self.assertEqual(calls, {"600000": 1, "000001": 2})
        self.assertEqual(len(frames), 2)
        self.assertEqual(errors, {})

    def test_cached_trade_calendar_resolves_weekday_holiday(self):
        root = Path.cwd() / "test_outputs" / "trade_calendar_resolve"
        store = DataStore(cache_dir=root / "cache", state_db=root / "state.sqlite3")
        store.write_frame(
            "benchmark",
            pd.DataFrame(
                [{"date": "2026-07-24", "symbol": "000300", "name": "CSI 300", "close": 100.0}]
            ),
        )
        store.write_frame(
            "trade_calendar",
            pd.DataFrame(
                [
                    {"date": "2026-07-24", "is_trading_day": True, "source": "test", "retrieved_at": "now"},
                    {"date": "2026-07-25", "is_trading_day": False, "source": "test", "retrieved_at": "now"},
                    {"date": "2026-07-26", "is_trading_day": False, "source": "test", "retrieved_at": "now"},
                    {"date": "2026-07-27", "is_trading_day": False, "source": "test", "retrieved_at": "now"},
                    {"date": "2026-07-28", "is_trading_day": True, "source": "test", "retrieved_at": "now"},
                ]
            ),
        )
        config = {"data": {"trade_calendar_preflight": True}}

        self.assertEqual(resolve_finalized_trading_date(store, config, date(2026, 7, 27)), date(2026, 7, 24))
        self.assertEqual(resolve_finalized_trading_date(store, config, date(2026, 7, 28)), date(2026, 7, 28))
        normalized = normalize_trade_calendar(store.read_frame("trade_calendar"))
        self.assertEqual(normalized["is_trading_day"].tolist(), [True, False, False, False, True])

    def test_fetch_end_uses_previous_day_before_close_cutoff(self):

        config = {"data": {"same_day_close_available_after": "16:00"}}
        self.assertEqual(finalized_market_data_end(config, datetime(2026, 7, 16, 13, 45)), date(2026, 7, 15))
        self.assertEqual(finalized_market_data_end(config, datetime(2026, 7, 16, 16, 1)), date(2026, 7, 16))
        self.assertEqual(finalized_market_data_end(config, datetime(2026, 7, 26, 18, 0)), date(2026, 7, 24))

    def test_spot_fallback_is_used_when_primary_fails(self):
        class FakeAk:
            def stock_zh_a_spot_em(self):
                raise RuntimeError("primary down")

            def stock_zh_a_spot(self):
                return pd.DataFrame(
                    [
                        {"代码": "sh600000", "名称": "浦发银行", "成交额": 1000000},
                        {"代码": "bj920000", "名称": "北交样例", "成交额": 1000000},
                    ]
                )

        spot = fetch_stock_spot(FakeAk())
        stocks = stocks_from_spot(spot)
        self.assertIn("600000", stocks["symbol"].tolist())

    def test_legacy_spot_can_select_a_share_symbols(self):
        spot = pd.DataFrame(
            [
                {"代码": "sh600000", "名称": "浦发银行", "成交额": 2000000},
                {"代码": "sz300001", "名称": "创业样例", "成交额": 1000000},
                {"代码": "bj920000", "名称": "北交样例", "成交额": 3000000},
                {"代码": "sh510300", "名称": "ETF样例", "成交额": 9000000},
            ]
        )
        config = {"universe": {"boards": ["main", "gem", "star"]}}
        self.assertEqual(select_fetch_symbols(spot, config).tolist(), ["600000", "300001"])

    def test_stock_names_and_amount_survive_standard_chinese_columns(self):
        spot = pd.DataFrame([{"代码": "sh600000", "名称": "浦发银行", "成交额": 2000000}])
        stocks = stocks_from_spot(spot)
        self.assertEqual(stocks.loc[0, "symbol"], "600000")
        self.assertEqual(stocks.loc[0, "name"], "浦发银行")

    def test_cached_universe_ranks_by_cached_liquidity(self):
        class FakeStore:
            def load_market_data(self):
                stocks = pd.DataFrame(
                    [
                        {"symbol": "600000", "name": "A", "board": "main", "industry": "x", "list_date": "2020-01-01", "is_st": False},
                        {"symbol": "300001", "name": "B", "board": "gem", "industry": "x", "list_date": "2020-01-01", "is_st": False},
                        {"symbol": "920000", "name": "C", "board": "bse", "industry": "x", "list_date": "2020-01-01", "is_st": False},
                    ]
                )
                prices = pd.DataFrame(
                    [
                        {"date": "2026-01-01", "symbol": "600000", "amount": 1},
                        {"date": "2026-01-01", "symbol": "300001", "amount": 10},
                    ]
                )
                return stocks, prices, pd.DataFrame()

        config = {"data": {"max_symbols": 2}, "universe": {"boards": ["main", "gem", "star"], "liquidity_lookback": 20}}
        _, symbols = cached_universe_symbols(FakeStore(), config)
        self.assertEqual(symbols, ["300001", "600000"])

    def test_metadata_refresh_symbols_rank_by_liquidity(self):
        stocks = pd.DataFrame(
            [
                {"symbol": "600000", "name": "A", "board": "main", "industry": "unknown", "list_date": "2000-01-01", "is_st": False},
                {"symbol": "300001", "name": "B", "board": "gem", "industry": "unknown", "list_date": "2000-01-01", "is_st": False},
                {"symbol": "000001", "name": "D", "board": "main", "industry": "bank", "list_date": "2010-01-01", "is_st": False},
                {"symbol": "920000", "name": "C", "board": "bse", "industry": "unknown", "list_date": "2000-01-01", "is_st": False},
            ]
        )
        prices = pd.DataFrame(
            [
                {"date": "2026-01-01", "symbol": "600000", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 10},
                {"date": "2026-01-01", "symbol": "300001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 100},
                {"date": "2026-01-01", "symbol": "000001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1000},
            ]
        )
        config = {"universe": {"boards": ["main", "gem", "star"], "liquidity_lookback": 20}}
        self.assertEqual(metadata_refresh_symbols(stocks, prices, config, max_symbols=3), ["300001", "600000", "000001"])

    def test_reusable_metadata_cache_requires_fresh_complete_coverage(self):
        class FakeStore:
            def __init__(self, retrieved_at: str):
                self.retrieved_at = retrieved_at

            def read_frame(self, name, required=False):
                self.assert_name = name
                return pd.DataFrame([
                    {"symbol": "600000", "retrieved_at": self.retrieved_at},
                    {"symbol": "000001", "retrieved_at": self.retrieved_at},
                ])

        stocks = pd.DataFrame([
            {"symbol": "600000", "name": "A", "board": "main", "industry": "bank", "list_date": "1999-11-10", "is_st": False},
            {"symbol": "000001", "name": "B", "board": "main", "industry": "finance", "list_date": "1991-04-03", "is_st": False},
        ])
        config = {"data": {"metadata_cache_max_age_days": 30, "metadata_cache_min_coverage": 1.0}}
        now = pd.Timestamp("2026-08-09T12:00:00Z")

        fresh, fresh_value = reusable_metadata_cache(
            FakeStore("2026-08-01T12:00:00Z"), stocks, ["600000", "000001"], config, now=now
        )
        stale, _ = reusable_metadata_cache(
            FakeStore("2026-06-01T12:00:00Z"), stocks, ["600000", "000001"], config, now=now
        )
        incomplete_stocks = stocks.copy()
        incomplete_stocks.loc[incomplete_stocks["symbol"] == "000001", "industry"] = "unknown"
        incomplete, incomplete_value = reusable_metadata_cache(
            FakeStore("2026-08-01T12:00:00Z"), incomplete_stocks, ["600000", "000001"], config, now=now
        )

        self.assertTrue(fresh)
        self.assertIn("coverage=1.0000", fresh_value)
        self.assertFalse(stale)
        self.assertFalse(incomplete)
        self.assertIn("coverage=0.5000", incomplete_value)
    def test_apply_stock_info_updates_metadata(self):
        stocks = pd.DataFrame(
            [{"symbol": "600000", "name": "A", "board": "main", "industry": "unknown", "list_date": "2000-01-01", "is_st": False}]
        ).set_index("symbol", drop=False)
        info = pd.DataFrame([{"item": "行业", "value": "银行"}, {"item": "上市时间", "value": "19991110"}])
        apply_stock_info(stocks, "600000", info)
        self.assertEqual(stocks.loc["600000", "industry"], "银行")
        self.assertEqual(stocks.loc["600000", "list_date"], "1999-11-10")

    def test_apply_cninfo_profile_updates_metadata(self):
        stocks = pd.DataFrame(
            [{"symbol": "600000", "name": "A", "board": "main", "industry": "unknown", "list_date": "2000-01-01", "is_st": False}]
        ).set_index("symbol", drop=False)
        profile = pd.DataFrame([{"所属行业": "货币金融服务", "上市日期": "1999-11-10"}])
        changed = apply_stock_profile_cninfo(stocks, "600000", profile)
        self.assertTrue(changed)
        self.assertEqual(stocks.loc["600000", "industry"], "货币金融服务")
        self.assertEqual(stocks.loc["600000", "list_date"], "1999-11-10")

    def test_metadata_checkpoint_uses_cninfo_fallback(self):
        class FakeAk:
            def stock_individual_info_em(self, symbol):
                raise RuntimeError("primary down")

            def stock_profile_cninfo(self, symbol):
                return pd.DataFrame([{"所属行业": "银行", "上市日期": "1999-11-10"}])

        class FakeStore:
            def __init__(self):
                self.writes = []

            def write_frame(self, name, frame):
                self.writes.append((name, frame.copy()))
                return name

        stocks = pd.DataFrame(
            [{"symbol": "600000", "name": "A", "board": "main", "industry": "unknown", "list_date": "2000-01-01", "is_st": False}]
        )
        enriched, failures = enrich_stock_metadata_checkpointed(
            FakeAk(),
            FakeStore(),
            stocks,
            ["600000"],
            {"data": {"metadata_checkpoint_every": 1}},
        )
        self.assertFalse(failures)
        self.assertEqual(enriched.loc[0, "industry"], "银行")
        self.assertEqual(str(enriched.loc[0, "list_date"]), "1999-11-10")

    def test_tx_history_normalization(self):
        hist = pd.DataFrame([{"date": "2026-06-12", "open": 10, "close": 11, "high": 12, "low": 9, "amount": 100}])
        out = history_from_tx(hist, "600000")
        self.assertEqual(out["volume"].iloc[0], 10000)
        self.assertEqual(out["amount"].iloc[0], 110000)
        self.assertEqual(tx_symbol("600000"), "sh600000")
        self.assertEqual(tx_symbol("000001"), "sz000001")
    def test_history_overlap_quality_passes_recent_reported_series(self):
        dates = pd.bdate_range("2026-01-01", periods=25).date
        reference = pd.DataFrame(
            [{
                "date": value,
                "symbol": "600000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 10000,
                "amount": 100000,
            } for value in dates]
        )
        candidate = reference.copy()
        candidate["close"] = 10.05
        candidate["amount"] = 105000

        metrics, issues = history_overlap_quality(candidate, reference, {"data": {}})

        self.assertFalse(issues)
        self.assertEqual(metrics["overlap_status"], "passed")
        self.assertEqual(metrics["overlap_rows"], 25)
        self.assertLess(metrics["overlap_close_median_relative_error"], 0.01)
        self.assertLess(metrics["overlap_amount_median_relative_error"], 0.10)

    def test_history_overlap_quality_rejects_material_amount_divergence(self):
        dates = pd.bdate_range("2026-01-01", periods=25).date
        reference = pd.DataFrame(
            [{
                "date": value,
                "symbol": "600000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 10000,
                "amount": 100000,
            } for value in dates]
        )
        candidate = reference.copy()
        candidate["amount"] = 150000

        metrics, issues = history_overlap_quality(candidate, reference, {"data": {}})

        self.assertEqual(metrics["overlap_status"], "failed")
        self.assertTrue(any("amount median relative error" in issue for issue in issues))
    def test_price_frame_issues_rejects_nonpositive_adjusted_prices(self):
        frame = pd.DataFrame(
            [
                {"date": "2018-01-02", "symbol": "600000", "open": -1, "high": 1, "low": -2, "close": 0, "volume": 100, "amount": 0},
            ]
        )
        self.assertIn("nonpositive OHLC rows=1", price_frame_issues(frame))

    def test_repair_tx_history_rebases_positive_hfq_to_latest_qfq_close(self):
        class FakeAk:
            @staticmethod
            def stock_zh_a_hist_tx(**kwargs):
                self_kwargs.append(kwargs)
                return pd.DataFrame(
                    [
                        {"date": "2018-01-02", "open": 20, "close": 20, "high": 21, "low": 19, "amount": 100},
                        {"date": "2026-01-02", "open": 40, "close": 40, "high": 41, "low": 39, "amount": 100},
                    ]
                )

        self_kwargs = []
        qfq = pd.DataFrame(
            [
                {"date": "2018-01-02", "symbol": "600000", "open": -1, "high": 0, "low": -2, "close": -1, "volume": 100, "amount": -100},
                {"date": "2026-01-02", "symbol": "600000", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "amount": 1000},
            ]
        )
        repaired = repair_tx_history_with_hfq(
            FakeAk(),
            "600000",
            pd.Timestamp("2018-01-01").date(),
            pd.Timestamp("2026-01-02").date(),
            qfq,
        )

        self.assertEqual(self_kwargs[0]["adjust"], "hfq")
        self.assertAlmostEqual(float(repaired.iloc[-1]["close"]), 10.0)
        self.assertFalse(price_frame_issues(repaired))
        self.assertTrue((repaired[["open", "high", "low", "close"]] > 0).all().all())


    def test_fetch_symbol_history_uses_sina_reported_amount_before_tencent(self):
        class FakeAk:
            tx_called = False

            def stock_zh_a_hist(self, **kwargs):
                raise ConnectionError("eastmoney down")

            def stock_zh_a_daily(self, **kwargs):
                self.daily_kwargs = kwargs
                return pd.DataFrame(
                    [{
                        "date": "2026-01-02",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10,
                        "volume": 12300,
                        "amount": 125000,
                    }]
                )

            def stock_zh_a_hist_tx(self, **kwargs):
                self.tx_called = True
                raise AssertionError("Tencent should not run after Sina succeeds")

        client = FakeAk()
        frame, provenance = fetch_symbol_history(
            client,
            "600000",
            pd.Timestamp("2026-01-01").date(),
            pd.Timestamp("2026-01-03").date(),
            retries=1,
        )

        self.assertFalse(client.tx_called)
        self.assertEqual(client.daily_kwargs["symbol"], "sh600000")
        self.assertEqual(float(frame.iloc[0]["volume"]), 12300.0)
        self.assertEqual(float(frame.iloc[0]["amount"]), 125000.0)
        self.assertEqual(provenance["source"], "stock_zh_a_daily")
        self.assertEqual(provenance["amount_quality"], "reported")

    def test_fetch_symbol_history_retries_and_returns_provenance(self):
        class FakeAk:
            calls = 0

            def stock_zh_a_hist(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("temporary")
                return pd.DataFrame(
                    [{"日期": "2026-01-02", "开盘": 10, "最高": 11, "最低": 9, "收盘": 10, "成交量": 100, "成交额": 1000}]
                )

            def stock_zh_a_hist_tx(self, **kwargs):
                return pd.DataFrame()

        frame, provenance = fetch_symbol_history(
            FakeAk(),
            "600000",
            pd.Timestamp("2026-01-01").date(),
            pd.Timestamp("2026-01-03").date(),
            retries=2,
        )

        self.assertEqual(len(frame), 1)
        self.assertEqual(provenance["source"], "stock_zh_a_hist")
        self.assertEqual(provenance["amount_quality"], "reported")

    def test_fetch_symbol_history_reports_all_retry_errors(self):
        class FakeAk:
            def stock_zh_a_hist(self, **kwargs):
                raise ConnectionError("primary down")

            def stock_zh_a_hist_tx(self, **kwargs):
                raise ConnectionError("fallback down")

        with self.assertRaisesRegex(RuntimeError, "attempt 1.*attempt 2"):
            fetch_symbol_history(
                FakeAk(), "600000", pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-01-03").date(), retries=2
            )

if __name__ == "__main__":
    unittest.main()
