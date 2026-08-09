from contextlib import nullcontext
from datetime import date
from pathlib import Path
from uuid import uuid4
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from ashare_quant.audit import orders_frame_sha256, signal_frame_sha256
from ashare_quant.candidate import live_strategy_hash
from ashare_quant.models import Order
from ashare_quant.paper import append_signal_log, build_signal_snapshot, filter_due_orders_for_health, resolve_order_date, run_shadow_signals, write_paper_outputs, write_shadow_outputs
from ashare_quant.portfolio import Portfolio


class PaperTests(unittest.TestCase):
    def test_forward_candidate_waits_without_generating_pre_freeze_shadow_orders(self):
        output_dir = Path.cwd() / "test_outputs" / "shadow_waiting_boundary"
        output_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output_dir)},
            "strategy": {"name": "forward-test", "top_n": 10},
            "benchmark": {"symbol": "000300"},
            "rules": {"effective_date": "2026-07-06"},
        }
        manifest = {
            "candidate_id": "forward-candidate",
            "strategy_hash": live_strategy_hash(config),
            "freeze_mode": "forward_only_preregistration",
            "freeze_boundary_schema_version": 2,
            "freeze_price_end": "2026-01-02",
            "freeze_known_data_end": "2026-01-02",
        }
        store = Mock()
        store.load_market_data.return_value = (
            pd.DataFrame([{"symbol": "600000"}]),
            pd.DataFrame([{"date": date(2026, 1, 1), "symbol": "600000"}]),
            pd.DataFrame(),
        )
        store.load_benchmark.return_value = pd.DataFrame()

        with (
            patch("ashare_quant.paper.DataStore.from_config", return_value=store),
            patch("ashare_quant.paper.validate_config", return_value=[]),
            patch("ashare_quant.paper.validate_market_data", return_value=[]),
            patch(
                "ashare_quant.paper.complete_market_dates",
                return_value=[date(2026, 1, 1), date(2026, 1, 2)],
            ),
            patch("ashare_quant.paper.load_candidate_manifest", return_value=manifest),
        ):
            paths = run_shadow_signals(config, run_date="2026-01-01")

        status = pd.read_csv(paths["shadow_observation_status.csv"])
        orders = pd.read_csv(paths["shadow_orders.csv"])
        signals = pd.read_csv(paths["shadow_signal_snapshot.csv"])
        self.assertEqual(status.loc[0, "status"], "waiting_for_post_freeze_market_data")
        self.assertTrue(orders.empty)
        self.assertTrue(signals.empty)
        self.assertFalse((output_dir / "shadow_archive").exists())
        store.log_run.assert_called_once()

    def test_shadow_uses_latest_committed_fetch_scope_for_complete_dates(self):
        output_dir = Path.cwd() / "test_outputs" / "shadow_committed_scope"
        output_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output_dir)},
            "strategy": {"name": "scope-test", "top_n": 10},
            "benchmark": {"symbol": "000300"},
            "data": {
                "min_daily_price_coverage_ratio": 0.9,
                "require_benchmark_on_signal_date": False,
            },
            "rules": {"effective_date": "2026-01-01"},
        }
        manifest = {
            "candidate_id": "scope-candidate",
            "strategy_hash": live_strategy_hash(config),
            "freeze_mode": "forward_only_preregistration",
            "freeze_boundary_schema_version": 2,
            "freeze_price_end": "2026-01-10",
            "freeze_known_data_end": "2026-01-10",
        }
        stocks = pd.DataFrame(
            [{"symbol": "600000"}, {"symbol": "000001"}, {"symbol": "300001"}]
        )
        prices = pd.DataFrame(
            [
                {"date": date(2026, 1, 1), "symbol": symbol}
                for symbol in ["600000", "000001", "300001"]
            ]
            + [
                {"date": date(2026, 1, 2), "symbol": symbol}
                for symbol in ["600000", "000001"]
            ]
        )
        summary = pd.DataFrame(
            [{
                "requested_symbols": 2,
                "fetched_symbols": 2,
                "failed_items": 0,
                "stage": "complete",
            }]
        )
        provenance = pd.DataFrame([{"symbol": "600000"}, {"symbol": "000001"}])
        store = Mock()
        store.load_market_data.return_value = (stocks, prices, pd.DataFrame())
        store.load_benchmark.return_value = pd.DataFrame()
        store.read_frame.side_effect = lambda name: {
            "fetch_summary": summary,
            "fetch_history_provenance": provenance,
        }[name]

        with (
            patch("ashare_quant.paper.DataStore.from_config", return_value=store),
            patch("ashare_quant.paper.validate_config", return_value=[]),
            patch("ashare_quant.paper.validate_market_data", return_value=[]),
            patch("ashare_quant.paper.load_candidate_manifest", return_value=manifest),
        ):
            paths = run_shadow_signals(config, run_date="2026-01-02")

        status = pd.read_csv(paths["shadow_observation_status.csv"])
        self.assertEqual(status.loc[0, "latest_complete_signal_date"], "2026-01-02")
        self.assertEqual(status.loc[0, "status"], "waiting_for_post_freeze_market_data")
        self.assertEqual(store.read_frame.call_count, 2)
    def test_resolve_order_date_uses_cached_next_date(self):
        dates = [date(2026, 4, 27), date(2026, 4, 28)]
        self.assertEqual(resolve_order_date(dates, date(2026, 4, 27)), date(2026, 4, 28))

    def test_resolve_order_date_uses_next_business_day_at_cache_end(self):
        dates = [date(2026, 4, 27)]
        self.assertEqual(resolve_order_date(dates, date(2026, 4, 27)), date(2026, 4, 28))

    def test_resolve_order_date_rolls_forward_for_practical_mode(self):
        dates = [date(2026, 6, 16)]
        self.assertEqual(
            resolve_order_date(dates, date(2026, 6, 16), current_date=date(2026, 6, 18), roll_forward=True),
            date(2026, 6, 19),
        )
        self.assertEqual(
            resolve_order_date(dates, date(2026, 6, 16), current_date=date(2026, 6, 18), roll_forward=False),
            date(2026, 6, 17),
        )

    def test_paused_health_rejects_due_buys_but_allows_risk_reducing_sells(self):
        portfolio = Portfolio(cash=100000.0)
        orders = [
            Order(date=date(2026, 7, 16), symbol="600000", side="buy", quantity=100),
            Order(date=date(2026, 7, 16), symbol="000001", side="sell", quantity=100),
        ]
        allowed = filter_due_orders_for_health(
            orders,
            portfolio,
            date(2026, 7, 16),
            {"exposure": 0.0, "reason": "strategy_health_paused"},
        )
        self.assertEqual([order.side for order in allowed], ["sell"])
        self.assertEqual(portfolio.rejected_orders[0]["reason"], "strategy_health_paused_buy")

    def test_paper_outputs_do_not_overwrite_live_readiness(self):
        output_dir = Path.cwd() / "test_outputs" / "paper_contract"
        output_dir.mkdir(parents=True, exist_ok=True)
        readiness_path = output_dir / "live_readiness.csv"
        readiness_path.write_text("check,passed,value,threshold,message\nsentinel,True,x,y,z\n", encoding="utf-8-sig")
        report_path = output_dir / "report.html"
        report_path.write_text("sentinel report", encoding="utf-8")

        paths = write_paper_outputs(
            {"paths": {"output_dir": str(output_dir)}},
            equity=pd.DataFrame([{"date": "2026-06-12", "cash": 1, "market_value": 0, "equity": 1, "fills": 0, "turnover": 0}]),
            orders=pd.DataFrame([{"date": "2026-06-15", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}]),
            positions=pd.DataFrame(),
            trades=pd.DataFrame(),
            provenance={"source": "paper", "signal_date": "2026-06-12", "execution_date": "2026-06-15", "order_count": 1},
        )

        self.assertIn("orders.csv", paths)
        self.assertIn("orders_provenance.csv", paths)
        self.assertIn("account_reconciliation.csv", paths)
        self.assertEqual(readiness_path.read_text(encoding="utf-8-sig").splitlines()[1], "sentinel,True,x,y,z")
        self.assertEqual(report_path.read_text(encoding="utf-8"), "sentinel report")
        provenance = pd.read_csv(paths["orders_provenance.csv"])
        self.assertEqual(provenance.loc[provenance["item"] == "source", "value"].iloc[0], "paper")
        self.assertEqual(
            provenance.loc[provenance["item"] == "orders_sha256", "value"].iloc[0],
            orders_frame_sha256(pd.DataFrame([{"date": "2026-06-15", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}])),
        )

    def test_signal_snapshot_and_log_are_written(self):
        output_dir = Path.cwd() / "test_outputs" / "paper_signal"
        output_dir.mkdir(parents=True, exist_ok=True)
        signal = build_signal_snapshot(
            pd.DataFrame(
                [
                    {"symbol": "600000", "score": 1.2, "industry": "bank", "board": "main"},
                    {"symbol": "000001", "score": 0.8, "industry": "bank", "board": "main"},
                ]
            ),
            pd.Series({"600000": 0.6, "000001": 0.4}),
            date(2026, 6, 12),
            date(2026, 6, 15),
        )
        paths = write_paper_outputs(
            {"paths": {"output_dir": str(output_dir)}},
            equity=pd.DataFrame(),
            orders=pd.DataFrame(),
            positions=pd.DataFrame(),
            trades=pd.DataFrame(),
            provenance={
                "source": "paper",
                "generated_at": "2026-06-12T15:30:00",
                "signal_date": "2026-06-12",
                "execution_date": "2026-06-15",
                "order_count": 0,
                "signal_hash": "abc",
                "config_sha256": "cfg",
                "strategy_name": "test_strategy",
                "candidate_id": "candidate",
                "allow_weak_strategy": False,
            },
            signal=signal,
        )

        snapshot = pd.read_csv(paths["paper_signal_snapshot.csv"], dtype={"symbol": str})
        self.assertEqual(snapshot["symbol"].tolist(), ["600000", "000001"])
        log = pd.read_csv(paths["signal_log.csv"])
        self.assertEqual(log["signal_hash"].iloc[-1], "abc")
        self.assertEqual(log["strategy_name"].iloc[-1], "test_strategy")

    def test_signal_hash_tracks_actionable_target_not_diagnostic_score_or_rank(self):
        signal = build_signal_snapshot(
            pd.DataFrame([
                {"symbol": "600000", "score": 1.2, "industry": "bank", "board": "main"},
                {"symbol": "000001", "score": 0.8, "industry": "bank", "board": "main"},
            ]),
            pd.Series({"600000": 0.6, "000001": 0.4}),
            date(2026, 6, 12),
            date(2026, 6, 15),
        )
        diagnostic_drift = signal.iloc[::-1].reset_index(drop=True)
        diagnostic_drift["score"] += 0.01
        diagnostic_drift["rank"] = [9, 8]
        diagnostic_drift["industry"] = "updated-label"
        changed_target = signal.copy()
        changed_target.loc[changed_target["symbol"] == "600000", "target_weight"] = 0.59

        self.assertEqual(signal_frame_sha256(signal), signal_frame_sha256(diagnostic_drift))
        self.assertNotEqual(signal_frame_sha256(signal), signal_frame_sha256(changed_target))

    def test_shadow_outputs_are_separate_from_executable_orders(self):
        output_dir = Path.cwd() / "test_outputs" / "shadow_signal"
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = write_shadow_outputs(
            {"paths": {"output_dir": str(output_dir)}},
            orders=pd.DataFrame([{"date": "2026-06-15", "symbol": "600000", "side": "buy", "quantity": 100, "reason": "rebalance"}]),
            signal=pd.DataFrame(
                [{"signal_date": "2026-06-12", "execution_date": "2026-06-15", "rank": 1, "symbol": "600000", "score": 1.0, "target_weight": 0.1, "industry": "bank", "board": "main"}]
            ),
            provenance={"source": "shadow", "signal_date": "2026-06-12", "execution_date": "2026-06-15", "order_count": 1, "signal_hash": "abc"},
        )
        self.assertIn("shadow_orders.csv", paths)
        self.assertIn("shadow_signal_log.csv", paths)
        self.assertIn("shadow_archive/20260612_signal.csv", paths)
        self.assertFalse((output_dir / "orders_provenance.csv").exists())


    def test_signal_log_keeps_distinct_candidates_on_same_date(self):
        temp_root = Path.cwd() / "test_outputs" / "temporary"
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_path = temp_root / uuid4().hex
        temp_path.mkdir(parents=True, exist_ok=True)
        with nullcontext(temp_path) as temp:
            path = Path(temp) / "shadow_signal_log.csv"
            base = {
                "generated_at": "2026-01-01T16:00:00",
                "signal_date": "2026-01-01",
                "execution_date": "2026-01-02",
                "signal_hash": "signal-a",
                "strategy_hash": "strategy-a",
                "strategy_name": "strategy",
                "candidate_id": "candidate-a",
            }
            append_signal_log(path, base)
            append_signal_log(
                path,
                {
                    **base,
                    "generated_at": "2026-01-01T17:00:00",
                    "signal_hash": "signal-b",
                    "candidate_id": "candidate-b",
                },
            )
            append_signal_log(path, {**base, "generated_at": "2026-01-01T18:00:00"})

            log = pd.read_csv(path)
            self.assertEqual(len(log), 2)
            self.assertEqual(set(log["candidate_id"]), {"candidate-a", "candidate-b"})
            candidate_a = log[log["candidate_id"] == "candidate-a"].iloc[0]
            self.assertEqual(candidate_a["generated_at"], "2026-01-01T18:00:00")

if __name__ == "__main__":
    unittest.main()
