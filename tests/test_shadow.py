import unittest
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import pandas as pd

from ashare_quant.audit import signal_identity_frames_equal

from ashare_quant.shadow import archive_shadow_outputs, build_continuous_shadow_portfolios, build_shadow_component_attribution, build_shadow_forward_audit, exclude_pre_freeze_candidate_evidence, write_shadow_component_outputs


class ShadowAuditTests(unittest.TestCase):
    def test_missing_candidate_manifest_leaves_research_audit_unchanged(self):
        audit = pd.DataFrame(
            [{"execution_date": "2026-01-02", "status": "ok", "excess_return": 0.01}]
        )

        result = exclude_pre_freeze_candidate_evidence(audit, None)

        pd.testing.assert_frame_equal(result, audit)

    def test_pre_freeze_candidate_audit_rows_are_explicitly_non_evidence(self):
        audit = pd.DataFrame(
            [
                {
                    "candidate_id": "candidate",
                    "execution_date": "2026-01-02",
                    "observed_trading_days": 2,
                    "portfolio_return": 0.02,
                    "benchmark_return": 0.01,
                    "excess_return": 0.01,
                    "status": "ok",
                },
                {
                    "candidate_id": "candidate",
                    "execution_date": "2026-01-06",
                    "observed_trading_days": 1,
                    "portfolio_return": 0.01,
                    "benchmark_return": 0.0,
                    "excess_return": 0.01,
                    "status": "ok",
                },
            ]
        )
        manifest = {
            "candidate_id": "candidate",
            "freeze_mode": "forward_only_preregistration",
            "freeze_boundary_schema_version": 2,
            "freeze_price_end": "2026-01-05",
            "freeze_known_data_end": "2026-01-05",
        }

        result = exclude_pre_freeze_candidate_evidence(audit, manifest)

        self.assertEqual(result.loc[0, "status"], "pre_freeze_not_evidence")
        self.assertEqual(int(result.loc[0, "observed_trading_days"]), 0)
        self.assertTrue(pd.isna(result.loc[0, "excess_return"]))
        self.assertEqual(result.loc[1, "status"], "ok")
        self.assertEqual(int(result.loc[1, "observed_trading_days"]), 1)

    def test_shadow_forward_audit_computes_excess_return(self):
        archive = Path.cwd() / "test_outputs" / "shadow_audit" / "shadow_archive"
        archive.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {"signal_date": "2026-01-01", "execution_date": "2026-01-02", "rank": 1, "symbol": "600000", "score": 1.0, "target_weight": 0.6, "industry": "bank", "board": "main"},
                {"signal_date": "2026-01-01", "execution_date": "2026-01-02", "rank": 2, "symbol": "000001", "score": 0.8, "target_weight": 0.4, "industry": "bank", "board": "main"},
            ]
        ).to_csv(archive / "20260101_signal.csv", index=False)
        pd.DataFrame(
            [
                {"item": "signal_date", "value": "2026-01-01"},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "signal_hash", "value": "abc"},
                {"item": "strategy_hash", "value": "strategy"},
                {"item": "candidate_id", "value": "candidate"},
            ]
        ).to_csv(archive / "20260101_provenance.csv", index=False)
        prices = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "600000", "close": 10.0},
                {"date": "2026-01-03", "symbol": "600000", "close": 11.0},
                {"date": "2026-01-02", "symbol": "000001", "close": 20.0},
                {"date": "2026-01-03", "symbol": "000001", "close": 21.0},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "000300", "name": "娌繁300", "close": 100.0},
                {"date": "2026-01-03", "symbol": "000300", "name": "娌繁300", "close": 102.0},
            ]
        )

        audit = build_shadow_forward_audit(archive, prices, benchmark)

        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.loc[0, "status"], "ok")
        self.assertEqual(audit.loc[0, "strategy_hash"], "strategy")
        self.assertAlmostEqual(float(audit.loc[0, "portfolio_return"]), 0.08)
        self.assertAlmostEqual(float(audit.loc[0, "benchmark_return"]), 0.02)
        self.assertAlmostEqual(float(audit.loc[0, "excess_return"]), 0.06)

    def test_shadow_forward_audit_preserves_cash_exposure_and_entry_costs(self):
        archive = Path.cwd() / "test_outputs" / "shadow_total_portfolio" / uuid4().hex
        archive.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {"signal_date": "2026-01-01", "execution_date": "2026-01-02", "symbol": "600000", "target_weight": 0.24},
                {"signal_date": "2026-01-01", "execution_date": "2026-01-02", "symbol": "000001", "target_weight": 0.16},
            ]
        ).to_csv(archive / "20260101_signal.csv", index=False)
        pd.DataFrame(
            [
                {"item": "signal_date", "value": "2026-01-01"},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "candidate_id", "value": "candidate"},
            ]
        ).to_csv(archive / "20260101_provenance.csv", index=False)
        prices = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "600000", "close": 10.0},
                {"date": "2026-01-03", "symbol": "600000", "close": 11.0},
                {"date": "2026-01-02", "symbol": "000001", "close": 20.0},
                {"date": "2026-01-03", "symbol": "000001", "close": 21.0},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"date": "2026-01-02", "close": 100.0},
                {"date": "2026-01-03", "close": 102.0},
            ]
        )
        config = {
            "trading": {
                "slippage_bps": 5,
                "commission_rate": 0.0003,
                "transfer_fee_rate": 0.00001,
            }
        }

        audit = build_shadow_forward_audit(
            archive, prices, benchmark, config=config
        )

        entry_cost = 0.00081
        expected = 0.24 * (11.0 / (10.0 * (1 + entry_cost)) - 1)
        expected += 0.16 * (21.0 / (20.0 * (1 + entry_cost)) - 1)
        self.assertEqual(audit.loc[0, "status"], "ok")
        self.assertAlmostEqual(float(audit.loc[0, "target_weight"]), 0.4)
        self.assertAlmostEqual(float(audit.loc[0, "coverage_ratio"]), 1.0)
        self.assertAlmostEqual(float(audit.loc[0, "portfolio_return"]), expected)
        self.assertEqual(audit.loc[0, "return_basis"], "total_portfolio_including_cash")
    def test_shadow_forward_audit_rejects_legacy_weight_cap_violation(self):
        archive = Path.cwd() / "test_outputs" / "shadow_weight_cap" / uuid4().hex
        archive.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-01",
                    "execution_date": "2026-01-02",
                    "symbol": "600000",
                    "target_weight": 0.06,
                },
                {
                    "signal_date": "2026-01-01",
                    "execution_date": "2026-01-02",
                    "symbol": "000001",
                    "target_weight": 0.04,
                },
            ]
        ).to_csv(archive / "20260101_signal.csv", index=False)
        pd.DataFrame(
            [
                {"item": "signal_date", "value": "2026-01-01"},
                {"item": "execution_date", "value": "2026-01-02"},
            ]
        ).to_csv(archive / "20260101_provenance.csv", index=False)
        prices = pd.DataFrame(
            [
                {"date": date, "symbol": symbol, "close": close}
                for date, close in (("2026-01-02", 10.0), ("2026-01-03", 11.0))
                for symbol in ("600000", "000001")
            ]
        )
        audit = build_shadow_forward_audit(
            archive, prices, pd.DataFrame(), max_stock_weight=0.05
        )

        self.assertEqual(audit.loc[0, "status"], "weight_cap_violation")
        self.assertFalse(bool(audit.loc[0, "weight_cap_passed"]))
        self.assertAlmostEqual(float(audit.loc[0, "max_target_weight"]), 0.06)

    def test_component_attribution_reconciles_to_total_excess_return(self):
        archive = Path.cwd() / "test_outputs" / "shadow_component_attribution" / "shadow_component_archive"
        archive.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-01",
                    "execution_date": "2026-01-02",
                    "symbol": "600000",
                    "target_weight": 0.6,
                    "anchor_weight": 0.4,
                    "satellite_weight": 0.2,
                    "signal_hash": "signal",
                    "strategy_hash": "strategy",
                    "candidate_id": "candidate",
                },
                {
                    "signal_date": "2026-01-01",
                    "execution_date": "2026-01-02",
                    "symbol": "000001",
                    "target_weight": 0.4,
                    "anchor_weight": 0.1,
                    "satellite_weight": 0.3,
                    "signal_hash": "signal",
                    "strategy_hash": "strategy",
                    "candidate_id": "candidate",
                },
            ]
        ).to_csv(archive / "20260101_candidate_components.csv", index=False)
        prices = pd.DataFrame(
            [
                {"date": "2026-01-02", "symbol": "600000", "close": 10.0},
                {"date": "2026-01-03", "symbol": "600000", "close": 11.0},
                {"date": "2026-01-02", "symbol": "000001", "close": 20.0},
                {"date": "2026-01-03", "symbol": "000001", "close": 21.0},
            ]
        )
        benchmark = pd.DataFrame(
            [
                {"date": "2026-01-02", "close": 100.0},
                {"date": "2026-01-03", "close": 102.0},
            ]
        )

        attribution = build_shadow_component_attribution(archive, prices, benchmark)

        self.assertEqual(attribution.loc[0, "status"], "ok")
        self.assertAlmostEqual(float(attribution.loc[0, "anchor_return"]), 0.09)
        self.assertAlmostEqual(float(attribution.loc[0, "satellite_return"]), 0.07)
        self.assertAlmostEqual(float(attribution.loc[0, "portfolio_return"]), 0.08)
        self.assertAlmostEqual(float(attribution.loc[0, "excess_return"]), 0.06)
        contribution = (
            float(attribution.loc[0, "anchor_excess_contribution"])
            + float(attribution.loc[0, "satellite_excess_contribution"])
        )
        self.assertAlmostEqual(contribution, 0.06)

    def test_component_archive_is_immutable(self):
        output = Path.cwd() / "test_outputs" / "shadow_component_archive"
        config = {"paths": {"output_dir": str(output)}}
        components = pd.DataFrame(
            [
                {
                    "signal_date": "2026-01-01",
                    "execution_date": "2026-01-02",
                    "symbol": "600000",
                    "target_weight": 1.0,
                    "anchor_weight": 0.7,
                    "satellite_weight": 0.3,
                    "signal_hash": "signal",
                    "strategy_hash": "strategy",
                    "candidate_id": "candidate",
                }
            ]
        )

        paths = write_shadow_component_outputs(config, components)
        write_shadow_component_outputs(config, components.copy())
        changed = components.copy()
        changed.loc[0, "anchor_weight"] = 0.6
        changed.loc[0, "satellite_weight"] = 0.4

        self.assertIn("shadow_component_archive/20260101_candidate_components.csv", paths)
        equivalent = components.copy()
        equivalent.loc[0, "signal_hash"] = "actionable-v2"
        equivalent.loc[0, "anchor_weight"] += 1e-14
        equivalent.loc[0, "satellite_weight"] -= 1e-14
        write_shadow_component_outputs(config, equivalent)
        with self.assertRaisesRegex(RuntimeError, "Immutable shadow component evidence conflict"):
            write_shadow_component_outputs(config, changed)


    def test_candidate_archives_are_immutable_and_candidate_scoped(self):
        temp_root = Path.cwd() / "test_outputs" / "temporary"
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_path = temp_root / uuid4().hex
        temp_path.mkdir(parents=True, exist_ok=True)
        with nullcontext(temp_path) as temp:
            archive = Path(temp)
            orders = pd.DataFrame(
                [{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}]
            )
            signal = pd.DataFrame(
                [
                    {
                        "signal_date": "2026-01-01",
                        "execution_date": "2026-01-02",
                        "symbol": "600000",
                        "target_weight": 1.0,
                    }
                ]
            )
            provenance = pd.DataFrame(
                [
                    {"item": "generated_at", "value": "2026-01-01T16:00:00"},
                    {"item": "signal_date", "value": "2026-01-01"},
                    {"item": "execution_date", "value": "2026-01-02"},
                    {"item": "signal_hash", "value": "signal-a"},
                    {"item": "strategy_hash", "value": "strategy-a"},
                    {"item": "candidate_id", "value": "candidate-a"},
                ]
            )

            paths = archive_shadow_outputs(
                archive,
                "2026-01-01",
                orders,
                signal,
                provenance,
            )
            provenance_path = paths[
                "shadow_archive/20260101_candidate-a_provenance.csv"
            ]
            original = provenance_path.read_text(encoding="utf-8-sig")

            rerun = provenance.copy()
            rerun.loc[rerun["item"] == "generated_at", "value"] = "2026-01-01T17:00:00"
            archive_shadow_outputs(archive, "2026-01-01", orders, signal, rerun)
            self.assertEqual(provenance_path.read_text(encoding="utf-8-sig"), original)

            migrated = provenance.copy()
            migrated.loc[migrated["item"] == "signal_hash", "value"] = "actionable-v2"
            archive_shadow_outputs(archive, "2026-01-01", orders, signal, migrated)

            changed_signal = signal.copy()
            changed_signal.loc[0, "target_weight"] = 0.9
            with self.assertRaisesRegex(RuntimeError, "signal_identity"):
                archive_shadow_outputs(archive, "2026-01-01", orders, changed_signal, provenance)

            second_candidate = provenance.copy()
            second_candidate.loc[
                second_candidate["item"] == "candidate_id",
                "value",
            ] = "candidate-b"
            second_paths = archive_shadow_outputs(
                archive,
                "2026-01-01",
                orders,
                signal,
                second_candidate,
            )
            self.assertIn(
                "shadow_archive/20260101_candidate-b_signal.csv",
                second_paths,
            )

    def test_empty_signal_identity_is_stable_across_csv_dtype_inference(self):
        columns = ["signal_date", "execution_date", "symbol", "target_weight"]
        generated = pd.DataFrame({
            "signal_date": pd.Series(dtype=object),
            "execution_date": pd.Series(dtype=object),
            "symbol": pd.Series(dtype=object),
            "target_weight": pd.Series(dtype=float),
        })
        csv_loaded = pd.DataFrame({
            "signal_date": pd.Series(dtype=object),
            "execution_date": pd.Series(dtype=object),
            "symbol": pd.Series(dtype=str),
            "target_weight": pd.Series(dtype=object),
        })

        self.assertEqual(list(generated.columns), columns)
        self.assertEqual(list(csv_loaded.columns), columns)
        self.assertTrue(signal_identity_frames_equal(generated, csv_loaded))

    def test_shadow_audit_deduplicates_same_evidence_identity(self):
        temp_root = Path.cwd() / "test_outputs" / "temporary"
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_path = temp_root / uuid4().hex
        temp_path.mkdir(parents=True, exist_ok=True)
        with nullcontext(temp_path) as temp:
            archive = Path(temp)
            signal = pd.DataFrame(
                [
                    {
                        "signal_date": "2026-01-01",
                        "execution_date": "2026-01-02",
                        "symbol": "600000",
                        "target_weight": 1.0,
                    }
                ]
            )
            provenance = pd.DataFrame(
                [
                    {"item": "signal_date", "value": "2026-01-01"},
                    {"item": "execution_date", "value": "2026-01-02"},
                    {"item": "signal_hash", "value": "same-signal"},
                    {"item": "strategy_hash", "value": "same-strategy"},
                    {"item": "candidate_id", "value": "same-candidate"},
                ]
            )
            for stem in ("20260101", "20260101_same-candidate"):
                signal.to_csv(archive / f"{stem}_signal.csv", index=False)
    def test_continuous_shadow_rebalances_once_per_month_and_preserves_cash(self):
        archive = Path.cwd() / "test_outputs" / "shadow_continuous" / uuid4().hex
        archive.mkdir(parents=True, exist_ok=True)
        signals = [
            ("2026-01-01", "2026-01-02", "600000", 0.4, "first"),
            ("2026-01-02", "2026-01-03", "000001", 1.0, "ignored_same_month"),
            ("2026-02-01", "2026-02-02", "000001", 0.4, "february"),
        ]
        for signal_date, execution_date, symbol, weight, signal_hash in signals:
            stem = signal_date.replace("-", "") + "_candidate"
            pd.DataFrame([{
                "signal_date": signal_date,
                "execution_date": execution_date,
                "symbol": symbol,
                "target_weight": weight,
            }]).to_csv(archive / f"{stem}_signal.csv", index=False)
            pd.DataFrame([
                {"item": "signal_date", "value": signal_date},
                {"item": "execution_date", "value": execution_date},
                {"item": "signal_hash", "value": signal_hash},
                {"item": "strategy_hash", "value": "strategy"},
                {"item": "candidate_id", "value": "candidate"},
            ]).to_csv(archive / f"{stem}_provenance.csv", index=False)
        prices = pd.DataFrame([
            {"date": date, "symbol": symbol, "open": close, "close": close}
            for date, a_close, b_close in [
                ("2026-01-01", 10.0, 20.0),
                ("2026-01-02", 10.0, 20.0),
                ("2026-01-03", 11.0, 20.0),
                ("2026-02-02", 12.0, 20.0),
                ("2026-02-03", 12.0, 22.0),
            ]
            for symbol, close in (("600000", a_close), ("000001", b_close))
        ])
        benchmark = pd.DataFrame([
            {"date": date, "close": close}
            for date, close in [
                ("2026-01-01", 99.0),
                ("2026-01-02", 100.0), ("2026-01-03", 101.0),
                ("2026-02-02", 102.0), ("2026-02-03", 103.0),
            ]
        ])
        config = {"strategy": {"max_stock_weight": 1.0}, "trading": {}}

        summary, curve = build_continuous_shadow_portfolios(
            archive, prices, benchmark, config
        )

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary.loc[0, "status"], "continuous_ok")
        self.assertEqual(int(summary.loc[0, "rebalance_count"]), 2)
        self.assertEqual(int(summary.loc[0, "observed_trading_days"]), 3)
        self.assertAlmostEqual(float(summary.loc[0, "portfolio_return"]), 0.1232)
        self.assertAlmostEqual(float(summary.loc[0, "benchmark_return"]), 103.0 / 99.0 - 1.0)
        self.assertEqual(int(curve["rebalance"].sum()), 2)
        self.assertAlmostEqual(float(curve.iloc[-1]["cash_weight"]), 0.648 / 1.1232)
    def test_continuous_shadow_uses_signal_close_benchmark_and_execution_open(self):
        archive = Path.cwd() / "test_outputs" / "shadow_open_execution" / uuid4().hex
        archive.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{
            "signal_date": "2026-01-01", "execution_date": "2026-01-02",
            "symbol": "600000", "target_weight": 0.5,
        }]).to_csv(archive / "20260101_candidate_signal.csv", index=False)
        pd.DataFrame([
            {"item": "signal_date", "value": "2026-01-01"},
            {"item": "execution_date", "value": "2026-01-02"},
            {"item": "signal_hash", "value": "signal"},
            {"item": "strategy_hash", "value": "strategy"},
            {"item": "candidate_id", "value": "candidate"},
        ]).to_csv(archive / "20260101_candidate_provenance.csv", index=False)
        prices = pd.DataFrame([
            {"date": "2026-01-01", "symbol": "600000", "open": 9.5, "close": 10.0},
            {"date": "2026-01-02", "symbol": "600000", "open": 10.0, "close": 11.0},
            {"date": "2026-01-03", "symbol": "600000", "open": 11.0, "close": 12.0},
        ])
        benchmark = pd.DataFrame([
            {"date": "2026-01-01", "close": 100.0},
            {"date": "2026-01-02", "close": 102.0},
            {"date": "2026-01-03", "close": 103.0},
        ])

        summary, curve = build_continuous_shadow_portfolios(
            archive, prices, benchmark,
            {"strategy": {"max_stock_weight": 1.0}, "trading": {}},
        )

        self.assertEqual(summary.loc[0, "status"], "continuous_ok")
        self.assertEqual(int(summary.loc[0, "observed_trading_days"]), 1)
        self.assertAlmostEqual(float(summary.loc[0, "portfolio_return"]), 0.10)
        self.assertAlmostEqual(float(summary.loc[0, "benchmark_return"]), 0.03)
        self.assertEqual(summary.loc[0, "entry_price_basis"], "next_session_open_with_side_specific_costs")
        self.assertEqual(curve.loc[curve["date"] == "2026-01-02", "rebalance"].iloc[0], True)
        self.assertAlmostEqual(float(summary.loc[0, "active_max_drawdown"]), 0.0)
        self.assertGreater(float(summary.loc[0, "tracking_error"]), 0.0)
        self.assertGreater(float(summary.loc[0, "information_ratio"]), 0.0)
        self.assertAlmostEqual(float(curve.iloc[-1]["active_nav"]), 1.10 / 1.03)
if __name__ == "__main__":
    unittest.main()
