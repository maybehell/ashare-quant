from datetime import date, datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256
from ashare_quant.canary_capital import write_broker_canary_capital_feasibility


class CanaryCapitalTests(unittest.TestCase):
    def fixture(self, name: str, *, cash: float, available_cash: float, environment: str = "live") -> tuple[dict, Path, Path]:
        root = Path.cwd() / "test_outputs" / name
        output = root / "outputs_live"
        output.mkdir(parents=True, exist_ok=True)
        state_db = root / "live_state.sqlite3"
        config = {
            "paths": {
                "output_dir": str(output),
                "state_db": str(state_db),
                "cache_dir": str(root / "cache"),
            },
            "data": {"max_cache_age_days": 10},
            "universe": {
                "boards": ["main", "gem", "star"],
                "exclude_st": True,
                "min_listing_days": 60,
                "min_avg_amount": 1,
                "liquidity_lookback": 20,
            },
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "transfer_fee_rate": 0.00001,
                "stamp_tax_rate": 0.0005,
            },
            "auto_live": {
                "enabled": True,
                "allow_simulated_account_snapshot": False,
                "account_snapshot_max_age_seconds": 300,
                "rollout": {
                    "stage": "canary",
                    "stages": {
                        "canary": {
                            "max_orders": 1,
                            "max_single_order_amount": 1000,
                            "max_buy_amount": 1000,
                            "max_gross_amount": 1000,
                        }
                    },
                },
            },
        }
        fingerprint = auto_live_config_sha256(config)
        pd.DataFrame([{
            "snapshot_run_id": "canary-capital-1",
            "broker": "huatai",
            "account_id": "masked-account",
            "environment": environment,
            "cash": cash,
            "available_cash": available_cash,
            "market_value": 0.0,
            "total_asset": cash,
            "positions_count": 0,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "auto_live_config_sha256": fingerprint,
        }]).to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        pd.DataFrame([
            {"check": "capital_minimum_equity_for_integer_tracking", "passed": True, "value": 600000.0},
            {"check": "broker_capital_feasibility_ready", "passed": False, "value": "blocked"},
        ]).to_csv(output / "broker_capital_feasibility_audit.csv", index=False)
        return config, output, state_db

    @staticmethod
    def store(
        price: float,
        benchmark_date: date = date(2026, 7, 16),
        manifest_fetched: int = 1,
    ):
        class Store:
            def load_market_data(self):
                stocks = pd.DataFrame([{
                    "symbol": "600000",
                    "name": "Test Bank",
                    "board": "main",
                    "industry": "bank",
                    "list_date": date(2000, 1, 1),
                    "is_st": False,
                }])
                prices = pd.DataFrame([{
                    "date": date(2026, 7, 16),
                    "symbol": "600000",
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1000000,
                    "amount": price * 1000000,
                }])
                return stocks, prices, pd.DataFrame()

            def load_benchmark(self, required=False):
                return pd.DataFrame([{"date": benchmark_date, "close": 4000.0}])

            def read_frame(self, name, required=True):
                if name == "fetch_summary":
                    return pd.DataFrame([{
                        "requested_symbols": manifest_fetched,
                        "fetched_symbols": manifest_fetched,
                        "failed_items": 0,
                        "stage": "complete",
                    }])
                if name == "fetch_history_provenance":
                    return pd.DataFrame([{"symbol": "600000"}])
                raise FileNotFoundError(name)

        return Store()

    def run_audit(
        self,
        config: dict,
        price: float,
        benchmark_date: date = date(2026, 7, 16),
        manifest_fetched: int = 1,
    ):
        universe = pd.DataFrame([{
            "symbol": "600000",
            "name": "Test Bank",
            "board": "main",
            "avg_amount": price * 1000000,
        }])
        with (
            patch(
                "ashare_quant.canary_capital.DataStore.from_config",
                return_value=self.store(price, benchmark_date, manifest_fetched),
            ),
            patch("ashare_quant.canary_capital.validate_market_data", return_value=[]),
            patch("ashare_quant.canary_capital.complete_market_dates", return_value=[date(2026, 7, 16)]),
            patch("ashare_quant.canary_capital.eligible_universe", return_value=universe),
        ):
            return write_broker_canary_capital_feasibility(config)

    def test_small_account_can_pass_execution_canary_without_passing_strategy_capital(self):
        config, output, state_db = self.fixture("canary_capital_small_affordable", cash=900.0, available_cash=900.0)
        paths = self.run_audit(config, price=8.0)

        audit = pd.read_csv(paths["broker_canary_capital_audit.csv"]).set_index("check")
        candidates = pd.read_csv(paths["broker_canary_capital_candidates.csv"], dtype={"symbol": str})
        strategy = pd.read_csv(output / "broker_capital_feasibility_audit.csv").set_index("check")
        self.assertTrue(bool(audit.loc["broker_canary_capital_ready", "passed"]))
        self.assertIn("strategy_portfolio_ready=False", audit.loc["broker_canary_capital_ready", "value"])
        self.assertFalse(bool(strategy.loc["broker_capital_feasibility_ready", "passed"]))
        self.assertTrue(bool(candidates.loc[0, "affordable"]))
        self.assertFalse(bool(candidates.loc[0, "executable"]))
        self.assertEqual(candidates.loc[0, "scope"], "execution_probe_only")
        self.assertFalse((output / "orders.csv").exists())
        self.assertFalse((output / "rpa_orders.csv").exists())
        self.assertFalse((output / "auto_live_submission_intent.csv").exists())
        self.assertFalse((output / "huatai_desktop_canary_certification.json").exists())
        transition = pd.read_csv(paths["broker_capital_transition.csv"]).iloc[0]
        self.assertEqual(transition["allowed_mode"], "execution_canary_review_only")
        self.assertFalse(bool(transition["full_strategy_ready"]))
        self.assertTrue(bool(transition["canary_ready"]))
        self.assertTrue(bool(transition["strategy_identity_preserved"]))
        self.assertFalse(state_db.exists())

    def test_no_affordable_board_lot_fails_canary_capital(self):
        config, _, _ = self.fixture("canary_capital_no_lot", cash=900.0, available_cash=900.0)
        paths = self.run_audit(config, price=12.0)
        audit = pd.read_csv(paths["broker_canary_capital_audit.csv"]).set_index("check")
        candidates = pd.read_csv(paths["broker_canary_capital_candidates.csv"])
        self.assertFalse(bool(audit.loc["canary_one_lot_candidate_affordable", "passed"]))
        self.assertFalse(bool(audit.loc["broker_canary_capital_ready", "passed"]))
        self.assertFalse(bool(candidates.loc[0, "limit_affordable"]))
        transition = pd.read_csv(paths["broker_capital_transition.csv"]).iloc[0]
        self.assertEqual(transition["allowed_mode"], "blocked")

    def test_stock_universe_date_behind_benchmark_fails_canary_capital(self):
        config, _, _ = self.fixture(
            "canary_capital_stale_market", cash=900.0, available_cash=900.0
        )
        paths = self.run_audit(config, price=8.0, benchmark_date=date(2026, 7, 17))
        audit = pd.read_csv(paths["broker_canary_capital_audit.csv"]).set_index("check")
        self.assertTrue(bool(audit.loc["canary_market_evidence_ready", "passed"]))
        self.assertFalse(bool(audit.loc["canary_market_evidence_current", "passed"]))
        self.assertFalse(bool(audit.loc["canary_one_lot_candidate_affordable", "passed"]))
        self.assertFalse(bool(audit.loc["broker_canary_capital_ready", "passed"]))
        transition = pd.read_csv(paths["broker_capital_transition.csv"]).iloc[0]
        self.assertEqual(transition["status"], "market_evidence_stale")
        self.assertEqual(transition["allowed_mode"], "blocked")

    def test_committed_fetch_manifest_mismatch_fails_canary_capital(self):
        config, _, _ = self.fixture(
            "canary_capital_manifest_mismatch", cash=900.0, available_cash=900.0
        )
        paths = self.run_audit(config, price=8.0, manifest_fetched=2)
        audit = pd.read_csv(paths["broker_canary_capital_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["canary_market_universe_bound", "passed"]))
        self.assertIn("bound_symbols=1", audit.loc["canary_market_universe_bound", "value"])
        self.assertFalse(bool(audit.loc["broker_canary_capital_ready", "passed"]))

    def test_total_cash_never_replaces_zero_available_cash(self):
        config, _, _ = self.fixture("canary_capital_zero_available", cash=2563.17, available_cash=0.0)
        paths = self.run_audit(config, price=8.0)
        audit = pd.read_csv(paths["broker_canary_capital_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["canary_available_cash_positive", "passed"]))
        self.assertFalse(bool(audit.loc["broker_canary_capital_ready", "passed"]))

    def test_stale_or_simulated_snapshot_fails_closed(self):
        config, output, _ = self.fixture("canary_capital_stale_simulated", cash=900.0, available_cash=900.0, environment="simulation")
        snapshot_path = output / "auto_live_broker_account_snapshot.csv"
        snapshot = pd.read_csv(snapshot_path)
        snapshot.loc[0, "generated_at"] = (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")
        snapshot.to_csv(snapshot_path, index=False)
        paths = self.run_audit(config, price=8.0)
        audit = pd.read_csv(paths["broker_canary_capital_audit.csv"]).set_index("check")
        self.assertFalse(bool(audit.loc["canary_snapshot_environment_live", "passed"]))
        self.assertFalse(bool(audit.loc["canary_snapshot_fresh", "passed"]))
        self.assertFalse(bool(audit.loc["broker_canary_capital_ready", "passed"]))


if __name__ == "__main__":
    unittest.main()
