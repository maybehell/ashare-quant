import argparse
import io
import shutil
import unittest
from datetime import datetime
from pathlib import Path
import json
from unittest.mock import MagicMock, patch

import pandas as pd

from ashare_quant.audit import backtest_config_sha256, mapping_sha256, orders_frame_sha256
from ashare_quant.cli import build_candidate_market_freshness, candidate_observation_configs, cost_stress_needs_refresh, default_execution_drill_cash, ensure_cost_stress_current, execution_drill_config, fetch_account_prices, local_run_stale_cache_message, primary_challenger_backtest_issues, print_observe_summary, refresh_huatai_canary_read_only_evidence, report_render_issues, run_command, run_daily, run_execution_drill, run_forward_daily, run_forward_only_candidate_observations, run_huatai_read_only_refresh, run_local_system, refresh_live_deployment_evidence, run_observe, run_observe_candidates, run_one_click, run_candidate_audit, run_primary_challenger_observation, run_practical, run_research_candidate_observations, run_workflow, write_cost_stress, write_execution_readiness, write_one_click_summary


class CliWorkflowTests(unittest.TestCase):
    def test_calendar_status_strict_exit_codes_fail_closed_for_scheduler(self):
        args = argparse.Namespace(
            command="calendar-status",
            date="2026-10-01",
            refresh_missing=True,
            strict_exit_code=True,
        )
        with patch(
            "ashare_quant.cli.write_trading_day_status",
            return_value=(Path("calendar.csv"), "closed"),
        ):
            with self.assertRaises(SystemExit) as closed:
                run_command(args, {})
        self.assertEqual(closed.exception.code, 3)

        with patch(
            "ashare_quant.cli.write_trading_day_status",
            return_value=(Path("calendar.csv"), "unknown"),
        ):
            with self.assertRaises(SystemExit) as unknown:
                run_command(args, {})
        self.assertEqual(unknown.exception.code, 4)
    def test_candidate_market_freshness_requires_complete_prices_benchmark_and_calendar(self):
        config = {
            "data": {
                "complete_market_min_symbol_count": 2,
                "same_day_close_available_after": "16:00",
                "trade_calendar_preflight": True,
            }
        }
        prices = pd.DataFrame(
            [
                {"date": "2026-07-28", "symbol": "600000", "close": 10.0},
                {"date": "2026-07-28", "symbol": "000001", "close": 12.0},
            ]
        )
        benchmark = pd.DataFrame([{"date": "2026-07-28", "close": 4000.0}])
        calendar = pd.DataFrame(
            [
                {
                    "date": "2026-07-28",
                    "is_trading_day": True,
                    "source": "test",
                    "retrieved_at": "2026-07-28T18:00:00",
                }
            ]
        )

        current = build_candidate_market_freshness(
            prices,
            benchmark,
            calendar,
            config,
            now=datetime(2026, 7, 28, 18, 30),
        )
        stale = build_candidate_market_freshness(
            prices,
            pd.DataFrame([{"date": "2026-07-27", "close": 3990.0}]),
            calendar,
            config,
            now=datetime(2026, 7, 28, 18, 30),
        )

        self.assertTrue(bool(current["market_data_current"]))
        self.assertEqual(current["expected_market_date"], "2026-07-28")
        self.assertFalse(bool(stale["market_data_current"]))
    def test_forward_observation_summary_ignores_pre_freeze_shadow_days(self):
        output = Path.cwd() / "test_outputs" / "cli_forward_freeze_filter"
        candidate_output = output / "candidate"
        candidate_output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "validation": {"forward_only_candidate_configs": ["configs/candidate.yaml"]},
        }
        candidate_config = {
            "paths": {"output_dir": str(candidate_output)},
            "validation": {
                "research_only": True,
                "experiment_status": "preregistered_forward_only",
                "require_frozen_input_evidence": False,
            },
            "strategy": {"name": "candidate"},
        }
        manifest = {
            "candidate_id": "candidate-id",
            "strategy_hash": "strategy-hash",
            "strategy_name": "candidate",
            "freeze_mode": "forward_only_preregistration",
            "freeze_boundary_schema_version": 2,
            "freeze_price_end": "2026-07-24",
            "freeze_known_data_end": "2026-07-24",
            "promotion_eligible_at_freeze": False,
            "required_forward_trading_days": 60,
        }
        audit_path = candidate_output / "shadow_forward_audit.csv"
        pd.DataFrame(
            [
                {
                    "candidate_id": "candidate-id",
                    "signal_date": "2026-07-21",
                    "execution_date": "2026-07-22",
                    "latest_date": "2026-07-24",
                    "observed_trading_days": 2,
                    "portfolio_return": 0.01,
                    "benchmark_return": 0.0,
                    "status": "ok",
                },
                {
                    "candidate_id": "candidate-id",
                    "signal_date": "2026-07-27",
                    "execution_date": "2026-07-28",
                    "latest_date": None,
                    "observed_trading_days": 0,
                    "portfolio_return": None,
                    "benchmark_return": None,
                    "status": "not_observable",
                }
            ]
        ).to_csv(audit_path, index=False)
        attribution_path = candidate_output / "shadow_component_attribution.csv"
        pd.DataFrame().to_csv(attribution_path, index=False)

        with (
            patch("ashare_quant.cli.load_config", return_value=candidate_config),
            patch("ashare_quant.cli.load_candidate_manifest", return_value=manifest),
            patch("ashare_quant.cli.matching_candidate_id", return_value="candidate-id"),
            patch(
                "ashare_quant.cli.candidate_index_membership_evidence",
                return_value=(True, 0, "", 0, ""),
            ),
            patch(
                "ashare_quant.cli.candidate_benchmark_mapping_evidence",
                return_value=(True, {}, {}),
            ),
            patch(
                "ashare_quant.cli.shadow_evaluation_evidence",
                return_value=(True, {}, {}),
            ),
            patch(
                "ashare_quant.cli.forward_risk_evaluation_evidence",
                return_value=(True, {}, {}),
            ),
            patch(
                "ashare_quant.cli.candidate_market_freshness",
                return_value={
                    "market_data_current": True,
                    "expected_market_date": "2026-07-27",
                    "complete_market_end": "2026-07-27",
                    "benchmark_end": "2026-07-27",
                    "trade_calendar_covered": True,
                },
            ),
            patch("ashare_quant.cli.run_shadow_signals", return_value={}),
            patch("ashare_quant.cli.run_shadow_audit", return_value=audit_path),
            patch(
                "ashare_quant.cli.run_shadow_component_attribution",
                return_value=attribution_path,
            ),
            patch(
                "ashare_quant.cli.run_candidate_audit",
                return_value=candidate_output / "live_candidate_validation.csv",
            ),
        ):
            paths = run_forward_only_candidate_observations(config, allow_stale=True)

        summary = pd.read_csv(paths["forward_only_candidate_observation.csv"])
        self.assertEqual(summary.loc[0, "status"], "observed")
        self.assertEqual(int(summary.loc[0, "max_observed_trading_days"]), 0)
        self.assertEqual(int(summary.loc[0, "remaining_forward_trading_days"]), 60)
        self.assertEqual(
            summary.loc[0, "forward_observation_status"],
            "waiting_for_post_freeze_market_data",
        )
        self.assertEqual(int(summary.loc[0, "pending_signal_count"]), 1)
        self.assertEqual(summary.loc[0, "latest_pending_signal_date"], "2026-07-27")
        self.assertEqual(summary.loc[0, "next_pending_execution_date"], "2026-07-28")
        self.assertEqual(summary.loc[0, "first_attribution_requires_close_after"], "2026-07-28")
        self.assertIn("first_attribution_close_after=2026-07-28", summary.loc[0, "message"])
        self.assertTrue(bool(summary.loc[0, "benchmark_mapping_evidence_passed"]))
        self.assertTrue(bool(summary.loc[0, "shadow_evaluation_evidence_passed"]))
        self.assertEqual(len(str(summary.loc[0, "shadow_evaluation_sha256"])), 64)
        self.assertTrue(bool(summary.loc[0, "forward_risk_evaluation_evidence_passed"]))
        self.assertEqual(len(str(summary.loc[0, "forward_risk_evaluation_sha256"])), 64)
        self.assertTrue(bool(summary.loc[0, "market_data_current"]))
        self.assertEqual(summary.loc[0, "complete_market_end"], "2026-07-27")

    def test_forward_daily_refreshes_only_registered_candidate_and_observes(self):
        output = Path.cwd() / "test_outputs" / "cli_forward_daily"
        candidate_output = output / "candidate"
        candidate_output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output), "cache_dir": str(output / "base-cache")},
            "validation": {
                "forward_only_candidate_configs": ["configs/frozen_candidate.yaml"],
            },
        }
        candidate_config = {
            "paths": {
                "output_dir": str(candidate_output),
                "cache_dir": str(candidate_output / "cache"),
                "data_dir": str(candidate_output / "data"),
                "state_db": str(candidate_output / "state.sqlite3"),
            },
            "data": {},
            "validation": {
                "research_only": True,
                "experiment_status": "preregistered_forward_only",
            },
        }
        args = argparse.Namespace(refresh=True, allow_stale=False, quiet=True)
        store = MagicMock()
        fetched_config = []

        def fake_fetch(store_arg, cfg):
            self.assertIs(store_arg, store)
            fetched_config.append(cfg)
            return [candidate_output / "prices.parquet"]

        def fake_observe(cfg, allow_stale=False):
            self.assertIs(cfg, config)
            self.assertFalse(allow_stale)
            output.mkdir(parents=True, exist_ok=True)
            summary = output / "forward_only_candidate_observation.csv"
            pd.DataFrame([{"status": "observed", "message": "ok"}]).to_csv(summary, index=False)
            return {"forward_only_candidate_observation.csv": summary}

        with (
            patch("ashare_quant.cli.forward_candidate_cache_isolation", return_value=(True, "isolated")),
            patch("ashare_quant.cli.load_config", return_value=candidate_config),
            patch("ashare_quant.cli.DataStore.from_config", return_value=store),
            patch("ashare_quant.cli.fetch_akshare", side_effect=fake_fetch),
            patch("ashare_quant.cli.run_forward_only_candidate_observations", side_effect=fake_observe),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
            patch("ashare_quant.cli.generate_control_panel", return_value=output / "control_panel.html"),
        ):
            paths = run_forward_daily(config, args)

        self.assertEqual(fetched_config, [candidate_config])
        self.assertTrue(candidate_config["data"]["force_cached_universe"])
        self.assertFalse(candidate_config["data"]["enrich_metadata"])
        store.init_db.assert_called_once_with()
        store.log_run.assert_called_once()
        self.assertIn("forward-observation:forward_only_candidate_observation.csv", paths)
        self.assertIn("report.html", paths)
        self.assertIn("control_panel.html", paths)

    def test_forward_daily_refuses_shared_candidate_cache_before_fetch(self):
        config = {
            "paths": {"output_dir": str(Path.cwd() / "test_outputs" / "cli_forward_shared")},
            "validation": {"forward_only_candidate_configs": ["configs/shared.yaml"]},
        }
        args = argparse.Namespace(refresh=True, allow_stale=False, quiet=True)

        with (
            patch(
                "ashare_quant.cli.forward_candidate_cache_isolation",
                return_value=(False, "configs/shared.yaml=shared_with_base"),
            ),
            patch("ashare_quant.cli.fetch_akshare") as fetch,
        ):
            with self.assertRaisesRegex(RuntimeError, "cache isolation failed"):
                run_forward_daily(config, args)

        fetch.assert_not_called()

    def test_forward_daily_fails_closed_when_observation_fails(self):
        output = Path.cwd() / "test_outputs" / "cli_forward_daily_failed"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "validation": {
                "forward_only_candidate_configs": ["configs/frozen_candidate.yaml"],
            },
        }
        args = argparse.Namespace(refresh=False, allow_stale=False, quiet=True)

        def fake_observe(cfg, allow_stale=False):
            summary = output / "forward_only_candidate_observation.csv"
            pd.DataFrame(
                [{"status": "failed", "message": "frozen input hash mismatch"}]
            ).to_csv(summary, index=False)
            return {"forward_only_candidate_observation.csv": summary}

        with patch(
            "ashare_quant.cli.run_forward_only_candidate_observations",
            side_effect=fake_observe,
        ):
            with self.assertRaisesRegex(RuntimeError, "frozen input hash mismatch"):
                run_forward_daily(config, args)

    def test_observe_runs_non_executable_observation_steps(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_observe"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(refresh=False, max_symbols=None, cached_universe=False, no_metadata=False, allow_stale=True, skip_stress=True)

        def fake_backtest(config):
            calls.append("backtest")
            return {"equity_curve.csv": output / "equity_curve.csv"}

        def fake_shadow(config, allow_stale=False):
            calls.append(f"shadow:{allow_stale}")
            return {"shadow_orders.csv": output / "shadow_orders.csv"}

        def fake_shadow_audit(config):
            calls.append("shadow-audit")
            return output / "shadow_forward_audit.csv"

        def fake_report(config):
            calls.append("report")
            return output / "report.html"

        with (
            patch("ashare_quant.cli.run_backtest", side_effect=fake_backtest),
            patch("ashare_quant.cli.run_shadow_signals", side_effect=fake_shadow),
            patch("ashare_quant.cli.run_shadow_audit", side_effect=fake_shadow_audit),
            patch("ashare_quant.cli.regenerate_report", side_effect=fake_report),
        ):
            paths = run_observe(config, args)

        self.assertEqual(calls, ["backtest", "shadow:True", "shadow-audit", "report"])
        self.assertIn("shadow_forward_audit.csv", paths)
        self.assertIn("report.html", paths)

    def test_observe_reuses_current_backtest_when_manifest_is_current(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_observe_reuse"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}, "validation": {"observation_reuse_current_backtest": True}}
        args = argparse.Namespace(refresh=False, max_symbols=None, cached_universe=False, no_metadata=False, allow_stale=True, skip_stress=True)

        with (
            patch("ashare_quant.cli.primary_challenger_backtest_issues", return_value=[]),
            patch("ashare_quant.cli.run_backtest", side_effect=lambda config: calls.append("backtest") or {}),
            patch("ashare_quant.cli.run_shadow_signals", return_value={}),
            patch("ashare_quant.cli.run_shadow_audit", return_value=output / "shadow_forward_audit.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_observe(config, args)

        self.assertNotIn("backtest", calls)
        self.assertEqual(paths["backtest:run_manifest.csv"], output / "run_manifest.csv")

    def test_observe_candidates_runs_all_configs_and_updates_promotion(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_observe_candidates"
        default_output = output / "default"
        candidate_output = output / "candidate"
        default_output.mkdir(parents=True, exist_ok=True)
        candidate_output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(default_output)}}
        candidate_config = {"paths": {"output_dir": str(candidate_output)}, "strategy": {"name": "candidate"}}
        args = argparse.Namespace(
            refresh=False,
            max_symbols=None,
            cached_universe=False,
            no_metadata=False,
            allow_stale=True,
            skip_stress=True,
            candidate_config=[],
        )

        def fake_observe(cfg, observe_args):
            calls.append((cfg["paths"]["output_dir"], observe_args.allow_stale, observe_args.skip_stress))
            out = Path(cfg["paths"]["output_dir"])
            return {"report.html": out / "report.html", "shadow_forward_audit.csv": out / "shadow_forward_audit.csv"}

        with (
            patch("ashare_quant.cli.candidate_observation_configs", return_value=[("default", config), ("candidate", candidate_config)]),
            patch("ashare_quant.cli.run_observe", side_effect=fake_observe),
            patch("ashare_quant.cli.compare_backtest_outputs", return_value=default_output / "strategy_backtest_compare.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=default_output / "report.html"),
        ):
            paths = run_observe_candidates(config, args)

        self.assertEqual(calls, [(str(default_output), True, True), (str(candidate_output), True, True)])
        self.assertIn("default:report.html", paths)
        self.assertIn("candidate:shadow_forward_audit.csv", paths)
        self.assertEqual(paths["strategy_backtest_compare.csv"], default_output / "strategy_backtest_compare.csv")

    def test_observe_candidates_refreshes_configured_primary_summary(self):
        output = Path.cwd() / "test_outputs" / "cli_observe_candidates_primary"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "validation": {"primary_challenger_config": "configs/robust_blend.yaml"},
        }
        args = argparse.Namespace(
            refresh=False, max_symbols=None, cached_universe=False, no_metadata=False,
            allow_stale=True, skip_stress=True, candidate_config=[],
        )
        summary = output / "primary_challenger_observation.csv"
        challenger_output = output / "challenger"
        pd.DataFrame(
            [
                {
                    "status": "observed",
                    "candidate_output_dir": str(challenger_output),
                }
            ]
        ).to_csv(summary, index=False)

        with (
            patch("ashare_quant.cli.candidate_observation_configs", return_value=[("default", config)]),
            patch("ashare_quant.cli.run_observe", return_value={}),
            patch("ashare_quant.cli.run_primary_challenger_observation", return_value={"primary_challenger_observation.csv": summary}) as primary,
            patch("ashare_quant.cli.compare_backtest_outputs", return_value=output / "strategy_backtest_compare.csv") as compare_mock,
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_observe_candidates(config, args)

        primary.assert_called_once_with(config, allow_stale=True, skip_stress=True)
        self.assertEqual(paths["primary-challenger:primary_challenger_observation.csv"], summary)
        compared_outputs = [Path(value).resolve() for value in compare_mock.call_args.args[1]]
        self.assertEqual(compared_outputs, [output.resolve(), challenger_output.resolve()])


    def test_primary_challenger_observation_refreshes_only_configured_candidate(self):
        output = Path.cwd() / "test_outputs" / "cli_primary_challenger"
        champion_output = output / "champion"
        candidate_output = output / "candidate"
        champion_output.mkdir(parents=True, exist_ok=True)
        candidate_output.mkdir(parents=True, exist_ok=True)
        candidate_config_path = output / "candidate.json"
        candidate_config_path.write_text(
            '{"paths":{"output_dir":"' + str(candidate_output).replace("\\", "\\\\") +
            '"},"strategy":{"name":"primary-candidate"}}',
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(champion_output)},
            "validation": {"primary_challenger_config": str(candidate_config_path)},
        }
        calls = []

        with (
            patch("ashare_quant.cli.run_backtest", side_effect=lambda cfg: calls.append("backtest") or {"performance_metrics.csv": candidate_output / "performance_metrics.csv"}),
            patch("ashare_quant.cli.run_shadow_signals", side_effect=lambda cfg, allow_stale=False: calls.append(f"shadow:{allow_stale}") or {"shadow_orders.csv": candidate_output / "shadow_orders.csv"}),
            patch("ashare_quant.cli.run_shadow_audit", side_effect=lambda cfg: calls.append("shadow-audit") or candidate_output / "shadow_forward_audit.csv"),
            patch("ashare_quant.cli.run_candidate_audit", side_effect=lambda cfg: calls.append("candidate-audit") or candidate_output / "live_candidate_validation.csv"),
            patch("ashare_quant.cli.compare_backtest_outputs", side_effect=lambda cfg, dirs: calls.append(("compare", dirs)) or champion_output / "strategy_backtest_compare.csv"),
            patch("ashare_quant.cli.write_strategy_promotion_decision", side_effect=lambda cfg: calls.append("promotion") or champion_output / "strategy_promotion_decision.csv"),
        ):
            paths = run_primary_challenger_observation(config, allow_stale=True, skip_stress=True)

        summary = pd.read_csv(paths["primary_challenger_observation.csv"])
        self.assertEqual(summary.loc[0, "status"], "observed")
        self.assertEqual(summary.loc[0, "candidate_name"], "primary-candidate")
        self.assertEqual(summary.loc[0, "backtest_mode"], "refreshed")
        self.assertIn("missing_artifacts", summary.loc[0, "backtest_reason"])
        self.assertEqual(calls[:4], ["backtest", "shadow:True", "shadow-audit", "candidate-audit"])
        self.assertIn("promotion", calls)
        self.assertIn("strategy_backtest_compare.csv", paths)

    def test_primary_challenger_observation_tracks_registered_set_together(self):
        output = Path.cwd() / "test_outputs" / "cli_primary_challenger_set"
        champion_output = output / "champion"
        champion_output.mkdir(parents=True, exist_ok=True)
        config_paths = []
        candidate_outputs = []
        for idx in (1, 2):
            candidate_output = output / f"candidate_{idx}"
            candidate_output.mkdir(parents=True, exist_ok=True)
            candidate_config_path = output / f"candidate_{idx}.json"
            candidate_config_path.write_text(
                json.dumps(
                    {
                        "paths": {"output_dir": str(candidate_output)},
                        "strategy": {"name": f"candidate-{idx}"},
                    }
                ),
                encoding="utf-8",
            )
            config_paths.append(str(candidate_config_path))
            candidate_outputs.append(candidate_output)
        config = {
            "paths": {"output_dir": str(champion_output)},
            "validation": {
                "primary_challenger_configs": config_paths,
                "primary_challenger_reuse_current_backtest": True,
            },
        }
        observed_names = []

        with (
            patch("ashare_quant.cli.primary_challenger_backtest_issues", return_value=[]),
            patch("ashare_quant.cli.run_backtest") as backtest_mock,
            patch(
                "ashare_quant.cli.run_shadow_signals",
                side_effect=lambda cfg, allow_stale=False: observed_names.append(cfg["strategy"]["name"]) or {},
            ),
            patch(
                "ashare_quant.cli.run_shadow_audit",
                side_effect=lambda cfg: Path(cfg["paths"]["output_dir"]) / "shadow_forward_audit.csv",
            ),
            patch(
                "ashare_quant.cli.run_candidate_audit",
                side_effect=lambda cfg: Path(cfg["paths"]["output_dir"]) / "live_candidate_validation.csv",
            ),
            patch(
                "ashare_quant.cli.compare_backtest_outputs",
                return_value=champion_output / "strategy_backtest_compare.csv",
            ) as compare_mock,
            patch(
                "ashare_quant.cli.write_strategy_promotion_decision",
                return_value=champion_output / "strategy_promotion_decision.csv",
            ),
        ):
            paths = run_primary_challenger_observation(config, allow_stale=True, skip_stress=True)

        summary = pd.read_csv(paths["primary_challenger_observation.csv"])
        backtest_mock.assert_not_called()
        self.assertEqual(summary["status"].tolist(), ["observed", "observed"])
        self.assertEqual(observed_names, ["candidate-1", "candidate-2"])
        compared = compare_mock.call_args.args[1]
        self.assertEqual([Path(value).resolve() for value in compared], [champion_output.resolve(), *[path.resolve() for path in candidate_outputs]])

    def test_primary_challenger_reuses_current_backtest_but_refreshes_forward_evidence(self):
        output = Path.cwd() / "test_outputs" / "cli_primary_challenger_reuse"
        champion_output = output / "champion"
        candidate_output = output / "candidate"
        champion_output.mkdir(parents=True, exist_ok=True)
        candidate_output.mkdir(parents=True, exist_ok=True)
        candidate_config_path = output / "candidate.json"
        candidate_config_path.write_text(
            json.dumps({
                "paths": {"output_dir": str(candidate_output)},
                "strategy": {"name": "reused-candidate"},
            }),
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(champion_output)},
            "validation": {
                "primary_challenger_config": str(candidate_config_path),
                "primary_challenger_reuse_current_backtest": True,
            },
        }
        calls = []

        with (
            patch("ashare_quant.cli.primary_challenger_backtest_issues", return_value=[]),
            patch("ashare_quant.cli.run_backtest") as backtest_mock,
            patch("ashare_quant.cli.run_shadow_signals", side_effect=lambda cfg, allow_stale=False: calls.append("shadow") or {}),
            patch("ashare_quant.cli.run_shadow_audit", side_effect=lambda cfg: calls.append("audit") or candidate_output / "shadow_forward_audit.csv"),
            patch("ashare_quant.cli.run_candidate_audit", side_effect=lambda cfg: calls.append("candidate-audit") or candidate_output / "live_candidate_validation.csv"),
            patch("ashare_quant.cli.compare_backtest_outputs", return_value=champion_output / "strategy_backtest_compare.csv"),
            patch("ashare_quant.cli.write_strategy_promotion_decision", return_value=champion_output / "strategy_promotion_decision.csv"),
        ):
            paths = run_primary_challenger_observation(config, allow_stale=True, skip_stress=True)

        summary = pd.read_csv(paths["primary_challenger_observation.csv"])
        backtest_mock.assert_not_called()
        self.assertEqual(calls, ["shadow", "audit", "candidate-audit"])
        self.assertEqual(summary.loc[0, "status"], "observed")
        self.assertEqual(summary.loc[0, "backtest_mode"], "reused")
        self.assertEqual(summary.loc[0, "backtest_reason"], "manifest_current")
        self.assertIn("reused", summary.loc[0, "message"])

    def test_research_candidate_observation_reuses_current_evidence_and_stays_isolated(self):
        output = Path.cwd() / "test_outputs" / "cli_research_observation"
        champion_output = output / "champion"
        candidate_output = output / "candidate"
        champion_output.mkdir(parents=True, exist_ok=True)
        candidate_output.mkdir(parents=True, exist_ok=True)
        candidate_config_path = output / "candidate.json"
        candidate_config_path.write_text(
            json.dumps({
                "paths": {"output_dir": str(candidate_output)},
                "validation": {"research_only": True},
                "strategy": {"name": "breadth-research"},
            }),
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(champion_output)},
            "validation": {"research_observation_configs": [str(candidate_config_path)]},
        }
        calls = []

        with (
            patch("ashare_quant.cli.primary_challenger_backtest_issues", return_value=[]),
            patch("ashare_quant.cli.run_backtest") as backtest_mock,
            patch("ashare_quant.cli.run_shadow_signals", side_effect=lambda cfg, allow_stale=False: calls.append(f"shadow:{allow_stale}") or {}),
            patch("ashare_quant.cli.run_shadow_audit", side_effect=lambda cfg: calls.append("audit") or candidate_output / "shadow_forward_audit.csv"),
            patch("ashare_quant.cli.regenerate_report", side_effect=lambda cfg: calls.append("report") or candidate_output / "report.html"),
        ):
            paths = run_research_candidate_observations(config, allow_stale=True, skip_stress=True)

        summary = pd.read_csv(paths["research_candidate_observation.csv"])
        backtest_mock.assert_not_called()
        self.assertEqual(calls, ["shadow:True", "audit", "report"])
        self.assertEqual(summary.loc[0, "status"], "observed")
        self.assertEqual(summary.loc[0, "backtest_mode"], "reused")
        self.assertTrue(str(summary.loc[0, "strategy_hash"]))

    def test_research_candidate_observation_rejects_promotable_config(self):
        output = Path.cwd() / "test_outputs" / "cli_research_observation_guard"
        output.mkdir(parents=True, exist_ok=True)
        candidate_config_path = output / "candidate.json"
        candidate_config_path.write_text(
            json.dumps({"paths": {"output_dir": str(output / "candidate")}, "validation": {"research_only": False}}),
            encoding="utf-8",
        )
        config = {
            "paths": {"output_dir": str(output / "champion")},
            "validation": {"research_observation_configs": [str(candidate_config_path)]},
        }

        paths = run_research_candidate_observations(config, skip_stress=True)

        summary = pd.read_csv(paths["research_candidate_observation.csv"])
        self.assertEqual(summary.loc[0, "status"], "failed")
        self.assertIn("research_only=true", summary.loc[0, "message"])

    def test_candidate_audit_refreshes_all_candidate_readiness_artifacts(self):
        output = Path.cwd() / "test_outputs" / "cli_candidate_audit_refresh"
        output.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"date": "2026-07-10", "strategy_nav": 1.0, "benchmark_nav": 1.0}]).to_csv(
            output / "benchmark_comparison.csv", index=False
        )
        config = {"paths": {"output_dir": str(output)}}
        candidate = pd.DataFrame([{"item": "candidate_id", "value": "candidate-1"}])
        validation = pd.DataFrame([{"check": "candidate_exists", "passed": True}])
        progress = pd.DataFrame([{"candidate_id": "candidate-1", "status": "collecting_forward_data"}])
        live = pd.DataFrame([{"check": "forward_validation_status", "passed": False}])
        practical = pd.DataFrame([{"check": "data", "passed": True}])
        roadmap = pd.DataFrame([{"priority": 1, "action": "observe"}])

        with (
            patch("ashare_quant.cli.load_candidate_manifest", return_value={"candidate_id": "candidate-1"}),
            patch("ashare_quant.cli.candidate_frame", return_value=candidate),
            patch("ashare_quant.cli.build_candidate_validation", return_value=validation),
            patch("ashare_quant.cli.build_candidate_forward_progress", return_value=progress),
            patch("ashare_quant.cli.build_live_readiness_from_outputs", return_value=live),
            patch("ashare_quant.cli.build_practical_readiness_from_outputs", return_value=practical),
            patch("ashare_quant.cli.build_live_readiness_roadmap", return_value=roadmap),
            patch("ashare_quant.cli.write_strategy_health"),
        ):
            result = run_candidate_audit(config)

        self.assertEqual(result, output / "live_candidate_validation.csv")
        self.assertEqual(pd.read_csv(output / "live_candidate_forward_progress.csv").loc[0, "candidate_id"], "candidate-1")
        self.assertEqual(pd.read_csv(output / "live_readiness.csv").loc[0, "check"], "forward_validation_status")
        self.assertTrue(bool(pd.read_csv(output / "practical_readiness.csv").loc[0, "passed"]))
        self.assertEqual(pd.read_csv(output / "live_readiness_roadmap.csv").loc[0, "action"], "observe")

    def test_primary_challenger_backtest_issues_require_artifacts_before_manifest_check(self):
        output = Path.cwd() / "test_outputs" / "cli_primary_challenger_artifacts"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}

        with patch("ashare_quant.cli.readiness_evidence_issues") as evidence_mock:
            issues = primary_challenger_backtest_issues(config)

        self.assertTrue(issues[0].startswith("missing_artifacts="))
        evidence_mock.assert_not_called()

    def test_primary_challenger_backtest_issues_propagate_data_or_config_mismatch(self):
        output = Path.cwd() / "test_outputs" / "cli_primary_challenger_mismatch"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        for name in (
            "performance_metrics.csv",
            "benchmark_comparison.csv",
            "run_manifest.csv",
            "live_readiness.csv",
            "practical_readiness.csv",
            "strategy_reliability.csv",
        ):
            (output / name).write_text("item,value\n", encoding="utf-8")

        with patch("ashare_quant.cli.readiness_evidence_issues", return_value=["prices_sha256 不一致"]) as evidence_mock:
            issues = primary_challenger_backtest_issues(config)

        self.assertEqual(issues, ["prices_sha256 不一致"])
        evidence_mock.assert_called_once_with(config)

    def test_primary_challenger_failure_is_recorded_without_hiding_main_run(self):
        output = Path.cwd() / "test_outputs" / "cli_primary_challenger_missing"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "validation": {"primary_challenger_config": str(output / "missing.yaml")},
        }

        paths = run_primary_challenger_observation(config)
        summary = pd.read_csv(paths["primary_challenger_observation.csv"])

        self.assertEqual(summary.loc[0, "status"], "failed")
        self.assertIn("not found", summary.loc[0, "message"])

    def test_observe_challengers_command_dispatches_registered_set_only(self):
        output = Path.cwd() / "test_outputs" / "cli_observe_challengers_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(
            command="observe-challengers",
            allow_stale=True,
            skip_stress=True,
            quiet=True,
        )
        report = output / "report.html"
        with patch("ashare_quant.cli.run_primary_challenger_observation", return_value={}) as mocked, patch(
            "ashare_quant.cli.regenerate_report", return_value=report
        ) as report_mock:
            run_command(args, config)

        mocked.assert_called_once_with(config, allow_stale=True, skip_stress=True)
        report_mock.assert_called_once_with(config)

    def test_auto_live_run_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_run_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="auto-live-run", dry_run=True)
        with patch("ashare_quant.cli.run_auto_live", return_value={"auto_live_run.csv": output / "auto_live_run.csv"}) as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config, dry_run=True)

    def test_auto_live_cycle_command_is_dispatched_and_refreshes_views(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_cycle_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="auto-live-cycle", submit=True, confirm_account_sync=True)
        with (
            patch("ashare_quant.cli.run_auto_live_cycle", return_value={"auto_live_cycle.csv": output / "auto_live_cycle.csv"}) as cycle_mock,
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html") as report_mock,
            patch("ashare_quant.cli.generate_control_panel", return_value=output / "control_panel.html") as dashboard_mock,
        ):
            run_command(args, config)
        cycle_mock.assert_called_once_with(config, submit=True, confirm_account_sync=True)
        report_mock.assert_called_once_with(config)
        dashboard_mock.assert_called_once_with(config)

    def test_auto_live_window_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_window_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="auto-live-window")
        with patch("ashare_quant.cli.write_auto_live_window_audit", return_value=output / "auto_live_window_audit.csv") as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config)

    def test_auto_live_lock_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_lock_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="auto-live-lock")
        with patch("ashare_quant.cli.write_auto_live_submit_lock_audit", return_value=output / "auto_live_submit_lock_audit.csv") as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config)

    def test_auto_live_risk_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_risk_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="auto-live-risk")
        with patch(
            "ashare_quant.cli.write_auto_live_status",
            return_value={"auto_live_risk_audit.csv": output / "auto_live_risk_audit.csv"},
        ) as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config)

    def test_auto_live_service_command_is_dispatched_and_refreshes_views(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_service_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(
            command="auto-live-service",
            submit=False,
            confirm_account_sync=False,
            loop=False,
            interval_seconds=None,
            max_cycles=None,
        )
        service_paths = {"auto_live_service_summary.csv": output / "auto_live_service_summary.csv"}
        with (
            patch("ashare_quant.cli.run_auto_live_service", return_value=service_paths) as service_mock,
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html") as report_mock,
            patch("ashare_quant.cli.generate_control_panel", return_value=output / "control_panel.html") as dashboard_mock,
        ):
            run_command(args, config)
        service_mock.assert_called_once_with(
            config,
            submit=False,
            confirm_account_sync=False,
            loop=False,
            interval_seconds=None,
            max_cycles=None,
        )
        report_mock.assert_called_once_with(config)
        dashboard_mock.assert_called_once_with(config)

    def test_auto_live_service_controls_are_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_service_controls"
        config = {"paths": {"output_dir": str(output)}}
        with patch(
            "ashare_quant.cli.activate_auto_live_service_stop",
            return_value={"auto_live_service.stop": output / "auto_live_service.stop"},
        ) as stop_mock:
            run_command(argparse.Namespace(command="auto-live-service-stop", reason="maintenance"), config)
        stop_mock.assert_called_once_with(config, reason="maintenance")
        with patch(
            "ashare_quant.cli.clear_auto_live_service_stop",
            return_value={"auto_live_service_stop_audit.csv": output / "auto_live_service_stop_audit.csv"},
        ) as resume_mock:
            run_command(argparse.Namespace(command="auto-live-service-resume", confirm=True), config)
        resume_mock.assert_called_once_with(config, confirm=True)
        with patch(
            "ashare_quant.cli.write_auto_live_service_lock_audit",
            return_value=output / "auto_live_service_lock_audit.csv",
        ) as lock_mock:
            run_command(argparse.Namespace(command="auto-live-service-lock"), config)
        lock_mock.assert_called_once_with(config)

    def test_auto_live_adapter_files_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_adapter_files"
        config = {"paths": {"output_dir": str(output)}}
        with patch(
            "ashare_quant.cli.write_command_adapter_file_audit",
            return_value=output / "auto_live_adapter_file_audit.csv",
        ) as mocked:
            run_command(argparse.Namespace(command="auto-live-adapter-files"), config)
        mocked.assert_called_once_with(config)

    def test_auto_live_journal_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_journal"
        config = {"paths": {"output_dir": str(output)}}
        expected = {"auto_live_journal_summary.csv": output / "auto_live_journal_summary.csv"}
        with patch("ashare_quant.cli.write_auto_live_journal_audit", return_value=expected) as mocked:
            run_command(argparse.Namespace(command="auto-live-journal"), config)
        mocked.assert_called_once_with(config)

    def test_auto_live_account_binding_commands_are_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_binding"
        config = {"paths": {"output_dir": str(output)}}
        expected = {"auto_live_account_binding_audit.csv": output / "auto_live_account_binding_audit.csv"}
        with patch("ashare_quant.cli.write_auto_live_account_binding_audit", return_value=expected) as audit_mock:
            run_command(argparse.Namespace(command="auto-live-account-binding"), config)
        audit_mock.assert_called_once_with(config)
        with patch("ashare_quant.cli.bind_auto_live_account", return_value=expected) as bind_mock:
            run_command(argparse.Namespace(command="auto-live-bind-account", confirm=True), config)
        bind_mock.assert_called_once_with(config, confirm=True)
        with patch("ashare_quant.cli.unbind_auto_live_account", return_value=expected) as unbind_mock:
            run_command(argparse.Namespace(command="auto-live-unbind-account", confirm=True), config)
        unbind_mock.assert_called_once_with(config, confirm=True)

    def test_auto_live_recovery_audit_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_recovery"
        config = {"paths": {"output_dir": str(output)}}
        expected = {"auto_live_recovery_audit.csv": output / "auto_live_recovery_audit.csv"}
        with patch("ashare_quant.cli.write_auto_live_recovery_audit", return_value=expected) as mocked:
            run_command(argparse.Namespace(command="auto-live-recovery-audit"), config)
        mocked.assert_called_once_with(config)

    def test_auto_live_alert_commands_are_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_alerts"
        config = {"paths": {"output_dir": str(output)}}
        expected = {"auto_live_alert_audit.csv": output / "auto_live_alert_audit.csv"}
        with patch("ashare_quant.cli.write_auto_live_alert_audit", return_value=expected) as audit_mock:
            run_command(argparse.Namespace(command="auto-live-alert-audit"), config)
        audit_mock.assert_called_once_with(config)
        with patch("ashare_quant.cli.write_auto_live_alert_selftest", return_value=expected) as selftest_mock:
            run_command(argparse.Namespace(command="auto-live-alert-selftest"), config)
        selftest_mock.assert_called_once_with(config)
        with patch("ashare_quant.cli.retry_auto_live_alerts", return_value=expected) as retry_mock:
            run_command(argparse.Namespace(command="auto-live-alert-retry"), config)
        retry_mock.assert_called_once_with(config)

    def test_observation_alert_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_observation_alert"
        config = {"paths": {"output_dir": str(output)}}
        expected = {"observation_alert_status.csv": output / "observation_alert_status.csv"}
        args = argparse.Namespace(command="observation-alert", strategy_output="outputs")
        with patch("ashare_quant.cli.write_observation_alert_status", return_value=expected) as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config, "outputs")

    def test_auto_live_rollout_control_commands_are_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_rollout"
        config = {"paths": {"output_dir": str(output)}}
        expected = {"auto_live_rollout_audit.csv": output / "auto_live_rollout_audit.csv"}
        with patch("ashare_quant.cli.approve_auto_live_rollout", return_value=expected) as approve_mock:
            run_command(argparse.Namespace(command="auto-live-rollout-approve", stage="canary", confirm=True, reason="test"), config)
        approve_mock.assert_called_once_with(config, stage="canary", confirm=True, reason="test")
        with patch("ashare_quant.cli.revoke_auto_live_rollout", return_value=expected) as revoke_mock:
            run_command(argparse.Namespace(command="auto-live-rollout-revoke", confirm=True, reason="stop"), config)
        revoke_mock.assert_called_once_with(config, confirm=True, reason="stop")

    def test_auto_live_adapter_replay_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_replay_dispatch"
        replay = output / "replay"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="auto-live-adapter-replay", replay_dir=str(replay))
        expected = {
            "auto_live_adapter_replay_audit.csv": output / "auto_live_adapter_replay_audit.csv",
            "auto_live_adapter_replay_summary.csv": output / "auto_live_adapter_replay_summary.csv",
        }
        with patch("ashare_quant.cli.write_auto_live_adapter_replay_audit", return_value=expected) as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config, replay_dir=str(replay))

    def test_auto_live_stop_and_resume_commands_are_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_auto_live_stop_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        with patch("ashare_quant.cli.activate_auto_live_stop", return_value={"auto_live.stop": output / "auto_live.stop"}) as stop_mock:
            run_command(argparse.Namespace(command="auto-live-stop", reason="operator"), config)
        stop_mock.assert_called_once_with(config, reason="operator")
        with patch("ashare_quant.cli.clear_auto_live_stop", return_value={"auto_live_stop_audit.csv": output / "auto_live_stop_audit.csv"}) as resume_mock:
            run_command(argparse.Namespace(command="auto-live-resume", confirm=True), config)
        resume_mock.assert_called_once_with(config, confirm=True)

    def test_fetch_account_prices_command_is_dispatched(self):
        output = Path.cwd() / "test_outputs" / "cli_fetch_account_prices_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="fetch-account-prices")
        with patch("ashare_quant.cli.fetch_account_prices", return_value={"x": output / "x.csv"}) as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config)

    def test_fetch_full_history_disables_incremental_fetch(self):
        output = Path.cwd() / "test_outputs" / "cli_fetch_full_history"
        calls = []
        config = {
            "paths": {"output_dir": str(output)},
            "data": {"incremental_fetch": True},
        }

        class FakeStore:
            def init_db(self):
                calls.append("init_db")

            def log_run(self, *args):
                calls.append(("log", args[0]))

        def fake_fetch(store, cfg):
            calls.append(("fetch", cfg["data"]["incremental_fetch"]))
            return [output / "prices.parquet"]

        args = argparse.Namespace(
            command="fetch",
            max_symbols=None,
            no_metadata=True,
            cached_universe=True,
            full_history=True,
            benchmark_only=False,
            sample=False,
        )
        with (
            patch("ashare_quant.cli.DataStore.from_config", return_value=FakeStore()),
            patch("ashare_quant.cli.fetch_akshare", side_effect=fake_fetch),
        ):
            run_command(args, config)

    def test_fetch_account_prices_targets_account_positions_and_rechecks_state(self):
        output = Path.cwd() / "test_outputs" / "cli_fetch_account_prices"
        config = {"paths": {"output_dir": str(output), "cache_dir": str(output / "cache"), "state_db": str(output / "state.sqlite3")}, "data": {"max_symbols": 800}}
        calls = []

        class FakeStore:
            state_db = output / "state.sqlite3"

            def init_db(self):
                calls.append("init_db")

        def fake_fetch(store, cfg):
            calls.append(("fetch", cfg["data"]["force_cached_universe"], cfg["data"]["max_symbols"], cfg["data"]["enrich_metadata"]))
            return [output / "cache" / "prices.parquet"]

        def fake_account_state(cfg):
            calls.append("account-state")
            return {"account_state_audit.csv": output / "account_state_audit.csv"}

        with (
            patch("ashare_quant.cli.DataStore.from_config", return_value=FakeStore()),
            patch("ashare_quant.cli.required_fetch_symbols", return_value=["000001"]),
            patch("ashare_quant.cli.fetch_akshare", side_effect=fake_fetch),
            patch("ashare_quant.cli.export_account_state", side_effect=fake_account_state),
        ):
            paths = fetch_account_prices(config)

        self.assertEqual(calls[0], "init_db")
        self.assertEqual(calls[1], ("fetch", True, 0, False))
        self.assertEqual(calls[2], "account-state")
        self.assertIn("fetch:prices.parquet", paths)
        self.assertIn("account-state:account_state_audit.csv", paths)

    def test_default_candidate_observation_includes_robust_blend(self):
        output = Path.cwd() / "test_outputs" / "cli_observe_defaults"
        config = {"paths": {"output_dir": str(output / "default")}}

        configs = candidate_observation_configs(config, [])
        labels = [label for label, _ in configs]

        self.assertIn("default", labels)
        self.assertIn("liqrob100_adaptive_trend", labels)
        self.assertIn("robust_liq45_lv25_m10_ld15_lm05", labels)
        self.assertIn("liqrob100_adaptive_robust", labels)
        self.assertIn("liqrob100_adaptive_redundant", labels)

    def test_generic_candidate_loop_excludes_registered_primary_set(self):
        output = Path.cwd() / "test_outputs" / "cli_observe_registered_defaults"
        config = {
            "paths": {"output_dir": str(output / "default")},
            "validation": {
                "primary_challenger_configs": [
                    "configs/robust_blend.yaml",
                    "configs/adaptive_trend.yaml",
                    "configs/adaptive_redundant.yaml",
                ]
            },
        }

        labels = [label for label, _ in candidate_observation_configs(config, [])]

        self.assertEqual(labels, ["default"])

    def test_explicit_candidate_is_kept_when_primary_registry_exists(self):
        output = Path.cwd() / "test_outputs" / "cli_observe_explicit"
        config = {
            "paths": {"output_dir": str(output / "default")},
            "validation": {
                "primary_challenger_configs": ["configs/robust_blend.yaml"]
            },
        }

        labels = [
            label for label, _ in candidate_observation_configs(
                config, ["configs/adaptive_robust.yaml"]
            )
        ]

        self.assertEqual(labels, ["default", "liqrob100_adaptive_robust"])

    def test_observe_summary_prints_only_key_paths(self):
        output = Path.cwd() / "test_outputs" / "cli_observe_summary"
        paths = {
            "backtest:live_readiness.csv": output / "live_readiness.csv",
            "cost_stress.csv": output / "cost_stress.csv",
            "backtest:orders.csv": output / "orders.csv",
            "shadow:shadow_provenance.csv": output / "shadow_provenance.csv",
            "shadow_forward_audit.csv": output / "shadow_forward_audit.csv",
            "report.html": output / "report.html",
        }
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            print_observe_summary(paths)
        text = stream.getvalue()
        self.assertIn("live_readiness.csv", text)
        self.assertIn("cost_stress.csv", text)
        self.assertIn("shadow_forward_audit.csv", text)
        self.assertNotIn("orders.csv", text)

    def test_observe_refreshes_cost_stress_when_required(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_observe_stress"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "live_guard": {"require_cost_stress": True},
        }
        args = argparse.Namespace(refresh=False, max_symbols=None, cached_universe=False, no_metadata=False, allow_stale=True, skip_stress=False)

        with (
            patch("ashare_quant.cli.run_backtest", return_value={"live_readiness.csv": output / "live_readiness.csv"}),
            patch("ashare_quant.cli.ensure_cost_stress_current", side_effect=lambda config: calls.append("stress") or output / "cost_stress.csv"),
            patch("ashare_quant.cli.run_shadow_signals", return_value={"shadow_provenance.csv": output / "shadow_provenance.csv"}),
            patch("ashare_quant.cli.run_shadow_audit", return_value=output / "shadow_forward_audit.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_observe(config, args)

        self.assertEqual(calls, ["stress"])
        self.assertEqual(paths["cost_stress.csv"], output / "cost_stress.csv")

    def test_write_cost_stress_can_refresh_one_scenario_and_keep_existing_rows(self):
        output = Path.cwd() / "test_outputs" / "cli_cost_stress_partial_refresh"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}, "stress_test": {"enabled": True}}
        pd.DataFrame(
            [
                {"scenario": "base", "excess_total_return": 0.01},
                {"scenario": "high_cost", "excess_total_return": -0.99},
            ]
        ).to_csv(output / "cost_stress.csv", index=False)

        with patch("ashare_quant.cli.run_cost_stress", return_value=pd.DataFrame([{"scenario": "high_cost", "excess_total_return": 0.03}])) as run:
            path = write_cost_stress(config, scenario_names=["high_cost"], merge_existing=True)

        run.assert_called_once()
        refreshed = pd.read_csv(path)
        values = dict(zip(refreshed["scenario"], refreshed["excess_total_return"]))
        self.assertEqual(values["base"], 0.01)
        self.assertEqual(values["high_cost"], 0.03)

    def test_write_cost_stress_drops_existing_rows_from_an_old_evidence_signature(self):
        output = Path.cwd() / "test_outputs" / "cli_cost_stress_stale_merge"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}, "stress_test": {"enabled": True}}
        pd.DataFrame(
            [
                {
                    "scenario": "base",
                    "excess_total_return": 0.01,
                    "config_sha256": "old-config",
                    "prices_sha256": "same-prices",
                },
                {
                    "scenario": "high_cost",
                    "excess_total_return": -0.99,
                    "config_sha256": "old-config",
                    "prices_sha256": "same-prices",
                },
            ]
        ).to_csv(output / "cost_stress.csv", index=False)
        current = pd.DataFrame(
            [
                {
                    "scenario": "high_cost",
                    "excess_total_return": 0.03,
                    "config_sha256": "new-config",
                    "prices_sha256": "same-prices",
                }
            ]
        )

        with patch("ashare_quant.cli.run_cost_stress", return_value=current):
            path = write_cost_stress(config, scenario_names=["high_cost"], merge_existing=True)

        refreshed = pd.read_csv(path)
        self.assertEqual(refreshed["scenario"].tolist(), ["high_cost"])
        self.assertEqual(refreshed["config_sha256"].iloc[0], "new-config")

    def test_cost_stress_current_hashes_do_not_need_refresh(self):
        output = Path.cwd() / "test_outputs" / "cli_cost_stress_refresh"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "live_guard": {
                "cost_stress_scenario": "high_cost",
                "require_current_cost_stress": True,
            },
        }
        manifest_hashes = {
            "stocks_sha256": "stocks-hash",
            "prices_sha256": "prices-hash",
            "fundamentals_sha256": "fundamentals-hash",
            "benchmark_sha256": "benchmark-hash",
        }
        pd.DataFrame([{"item": key, "value": value} for key, value in manifest_hashes.items()]).to_csv(
            output / "run_manifest.csv",
            index=False,
        )
        pd.DataFrame(
            [
                {
                    "scenario": "high_cost",
                    "excess_total_return": 0.04,
                    "max_drawdown": -0.12,
                    "config_sha256": backtest_config_sha256(config),
                    **manifest_hashes,
                }
            ]
        ).to_csv(output / "cost_stress.csv", index=False)

        self.assertFalse(cost_stress_needs_refresh(config))

        pd.DataFrame([{"item": "stocks_sha256", "value": "new-stocks-hash"}]).to_csv(output / "run_manifest.csv", index=False)
        self.assertTrue(cost_stress_needs_refresh(config))

    def test_ensure_cost_stress_prunes_stale_rows_even_when_required_scenario_is_current(self):
        output = Path.cwd() / "test_outputs" / "cli_cost_stress_prune"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "live_guard": {
                "require_cost_stress": True,
                "require_current_cost_stress": True,
                "cost_stress_scenario": "high_cost",
            },
        }
        hashes = {
            "stocks_sha256": "stocks",
            "prices_sha256": "prices",
            "fundamentals_sha256": "fundamentals",
            "benchmark_sha256": "benchmark",
        }
        pd.DataFrame([{"item": key, "value": value} for key, value in hashes.items()]).to_csv(
            output / "run_manifest.csv", index=False
        )
        current_hash = backtest_config_sha256(config)
        pd.DataFrame(
            [
                {"scenario": "base", "config_sha256": "old", **hashes},
                {
                    "scenario": "high_cost",
                    "excess_total_return": 0.03,
                    "max_drawdown": -0.10,
                    "config_sha256": current_hash,
                    **hashes,
                },
            ]
        ).to_csv(output / "cost_stress.csv", index=False)

        path = ensure_cost_stress_current(config)

        self.assertEqual(path, output / "cost_stress.csv")
        pruned = pd.read_csv(path)
        self.assertEqual(pruned["scenario"].tolist(), ["high_cost"])

    def test_daily_practical_uses_practical_guard(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_daily_practical"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}, "practical_guard": {"require_cost_stress": True}}
        args = argparse.Namespace(
            refresh=False,
            max_symbols=None,
            cached_universe=False,
            no_metadata=False,
            allow_stale=True,
            allow_weak_strategy=False,
            practical=True,
            skip_stress=False,
        )

        def fake_paper(config, allow_stale=False, allow_weak_strategy=False, practical_mode=False):
            calls.append(f"paper:{practical_mode}")
            return {"orders.csv": output / "orders.csv"}

        with (
            patch("ashare_quant.cli.ensure_cost_stress_current", side_effect=lambda config, guard_name="live_guard": calls.append(f"stress:{guard_name}") or None),
            patch("ashare_quant.cli.write_practical_readiness", side_effect=lambda config: calls.append("ready") or output / "practical_readiness.csv"),
            patch("ashare_quant.cli.write_strategy_health", side_effect=lambda config: calls.append("health") or output / "strategy_health.csv"),
            patch("ashare_quant.cli.run_paper", side_effect=fake_paper),
            patch("ashare_quant.cli.export_rpa_orders", return_value=output / "rpa_orders.csv"),
            patch("ashare_quant.cli.preview_rpa_orders", return_value="preview"),
        ):
            paths = run_daily(config, args)

        self.assertEqual(calls, ["stress:practical_guard", "ready", "health", "paper:True"])
        self.assertEqual(paths["practical_readiness.csv"], output / "practical_readiness.csv")

    def test_execution_drill_uses_isolated_paths_and_full_chain(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_execution_drill"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output), "state_db": str(output / "main.sqlite3")},
            "backtest": {"initial_cash": 1000000},
            "rpa": {"require_strategy_health": True},
        }
        args = argparse.Namespace(output_dir=None, reset_cash=123456.0, allow_stale=True, skip_backtest=False, skip_stress=False)
        seen = {}

        def remember(name, filename):
            def inner(cfg, *args, **kwargs):
                calls.append(name)
                seen[name] = cfg
                path = Path(cfg["paths"]["output_dir"]) / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                if filename.endswith(".csv"):
                    pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]).to_csv(path, index=False)
                else:
                    path.write_text("ok", encoding="utf-8")
                return path
            return inner

        def fake_backtest(cfg):
            calls.append("backtest")
            seen["backtest"] = cfg
            out = Path(cfg["paths"]["output_dir"])
            out.mkdir(parents=True, exist_ok=True)
            return {"equity_curve.csv": out / "equity_curve.csv"}

        def fake_reset(cfg, cash=None, confirm=False):
            calls.append(f"reset:{cash}:{confirm}")
            seen["reset"] = cfg
            out = Path(cfg["paths"]["output_dir"])
            return {"account_reset_audit.csv": out / "account_reset_audit.csv"}

        def fake_account_state(cfg):
            calls.append("account-state")
            seen["account-state"] = cfg
            out = Path(cfg["paths"]["output_dir"])
            return {"account_state_audit.csv": out / "account_state_audit.csv"}

        def fake_paper(cfg, allow_stale=False, practical_mode=False):
            calls.append(f"paper:{allow_stale}:{practical_mode}")
            seen["paper"] = cfg
            out = Path(cfg["paths"]["output_dir"])
            return {"orders.csv": out / "orders.csv"}

        with (
            patch("ashare_quant.cli.run_backtest", side_effect=fake_backtest),
            patch("ashare_quant.cli.write_cost_stress", side_effect=remember("stress", "cost_stress.csv")),
            patch("ashare_quant.cli.compare_backtest_outputs", side_effect=remember("compare", "strategy_backtest_compare.csv")),
            patch("ashare_quant.cli.write_practical_readiness", side_effect=remember("ready", "practical_readiness.csv")),
            patch("ashare_quant.cli.reset_account_state", side_effect=fake_reset),
            patch("ashare_quant.cli.export_account_state", side_effect=fake_account_state),
            patch("ashare_quant.cli.run_paper", side_effect=fake_paper),
            patch("ashare_quant.cli.write_strategy_health", side_effect=remember("health", "strategy_health.csv")),
            patch("ashare_quant.cli.export_rpa_orders", side_effect=remember("rpa", "rpa_orders.csv")),
            patch("ashare_quant.cli.write_rpa_preflight", side_effect=remember("rpa-preflight", "rpa_preflight.csv")),
            patch("ashare_quant.cli.write_execution_readiness", side_effect=remember("execution", "execution_readiness.csv")),
            patch("ashare_quant.cli.regenerate_report", side_effect=remember("report", "report.html")),
            patch("ashare_quant.cli.generate_control_panel", side_effect=remember("dashboard", "control_panel.html")),
        ):
            paths = run_execution_drill(config, args)

        drill_cfg = seen["paper"]
        self.assertEqual(Path(drill_cfg["paths"]["output_dir"]), output / "execution_drill")
        self.assertEqual(Path(drill_cfg["paths"]["state_db"]), output / "execution_drill" / "state.sqlite3")
        self.assertNotEqual(drill_cfg["paths"]["state_db"], config["paths"]["state_db"])
        self.assertIn("reset:123456.0:True", calls)
        self.assertIn("paper:True:True", calls)
        self.assertIn("execution_drill_summary.csv", paths)
        self.assertTrue(paths["execution_drill_summary.csv"].exists())

    def test_default_execution_drill_cash_respects_daily_rpa_limit(self):
        config = {"backtest": {"initial_cash": 1000000}, "rpa": {"max_daily_order_amount": 500000}}
        self.assertEqual(default_execution_drill_cash(config), 400000.0)
        config = {"backtest": {"initial_cash": 300000}, "rpa": {"max_daily_order_amount": 500000}}
        self.assertEqual(default_execution_drill_cash(config), 300000.0)

    def test_execution_drill_config_accepts_custom_output_dir(self):
        output = Path.cwd() / "test_outputs" / "custom_drill"
        config = {"paths": {"output_dir": "outputs", "state_db": "data/state.sqlite3"}, "rpa": {}}
        drill = execution_drill_config(config, str(output))
        self.assertEqual(Path(drill["paths"]["output_dir"]), output)
        self.assertEqual(Path(drill["paths"]["state_db"]), output / "state.sqlite3")
        self.assertTrue(drill["rpa"]["require_strategy_health"])

    def test_practical_can_reset_account_before_orders(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_practical_reset"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}, "practical_guard": {"require_cost_stress": False}}
        args = argparse.Namespace(
            refresh=False,
            max_symbols=None,
            cached_universe=False,
            no_metadata=False,
            allow_stale=True,
            skip_stress=True,
            reset_account=True,
            reset_cash=500000.0,
            confirm_account_reset=True,
        )

        def fake_reset(config, cash=None, confirm=False):
            calls.append(f"reset:{cash}:{confirm}")
            return {"account_reset_audit.csv": output / "account_reset_audit.csv"}

        with (
            patch("ashare_quant.cli.readiness_evidence_issues", return_value=[]),
            patch("ashare_quant.cli.write_practical_readiness", side_effect=lambda config: calls.append("ready") or output / "practical_readiness.csv"),
            patch("ashare_quant.cli.write_strategy_health", side_effect=lambda config: calls.append("health") or output / "strategy_health.csv"),
            patch("ashare_quant.cli.reset_account_state", side_effect=fake_reset),
            patch("ashare_quant.cli.export_account_state", side_effect=lambda config: calls.append("account-state") or {"account_state_audit.csv": output / "account_state_audit.csv"}),
            patch("ashare_quant.cli.run_paper", side_effect=lambda config, allow_stale=False, practical_mode=False: calls.append(f"paper:{allow_stale}:{practical_mode}") or {"orders.csv": output / "orders.csv"}),
            patch("ashare_quant.cli.export_rpa_orders", side_effect=lambda config: calls.append("rpa") or output / "rpa_orders.csv"),
            patch("ashare_quant.cli.regenerate_report", side_effect=lambda config: calls.append("report") or output / "report.html"),
            patch("ashare_quant.cli.preview_rpa_orders", return_value="preview"),
        ):
            paths = run_practical(config, args)

        self.assertEqual(calls, ["ready", "health", "reset:500000.0:True", "account-state", "paper:True:True", "rpa", "report"])
        self.assertEqual(paths["account-reset:account_reset_audit.csv"], output / "account_reset_audit.csv")


    def test_one_click_run_refreshes_safe_outputs_without_broker_submit_by_default(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_one_click"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(
            refresh=False,
            max_symbols=None,
            cached_universe=False,
            no_metadata=False,
            allow_stale=True,
            skip_stress=True,
            auto_live_dry_run=False,
            skip_shadow=False,
        )

        def remember(name, filename):
            def inner(cfg, *args, **kwargs):
                calls.append(name)
                path = output / filename
                if filename.endswith(".csv"):
                    pd.DataFrame([{"check": "ok", "passed": True, "next_action": ""}]).to_csv(path, index=False)
                else:
                    path.write_text("ok", encoding="utf-8")
                return path
            return inner

        def fake_practical(cfg, practical_args):
            calls.append(f"practical:{practical_args.allow_stale}:{practical_args.skip_stress}:{practical_args.continue_without_orders_when_blocked}")
            return {"report.html": output / "report.html"}

        with (
            patch("ashare_quant.cli.run_practical", side_effect=fake_practical),
            patch("ashare_quant.cli.run_shadow_signals", side_effect=lambda cfg, allow_stale=False: calls.append(f"shadow:{allow_stale}") or {"shadow_orders.csv": output / "shadow_orders.csv"}),
            patch("ashare_quant.cli.run_shadow_audit", side_effect=lambda cfg: calls.append("shadow-audit") or output / "shadow_forward_audit.csv"),
            patch("ashare_quant.cli.run_primary_challenger_observation", side_effect=lambda *args, **kwargs: calls.append("challenger") or {"primary_challenger_observation.csv": output / "primary_challenger_observation.csv"}) as primary_observe,
            patch("ashare_quant.cli.run_research_candidate_observations", side_effect=lambda *args, **kwargs: calls.append("research-candidates") or {"research_candidate_observation.csv": output / "research_candidate_observation.csv"}) as research_observe,
            patch("ashare_quant.cli.run_forward_only_candidate_observations", side_effect=lambda *args, **kwargs: calls.append("forward-only") or {"forward_only_candidate_observation.csv": output / "forward_only_candidate_observation.csv"}),
            patch("ashare_quant.cli.write_data_source_health", side_effect=remember("source", "data_source_health.csv")),
            patch("ashare_quant.cli.write_account_exclusion_impact", side_effect=lambda cfg: calls.append("account-exclusion") or {"account_exclusion_impact.csv": output / "account_exclusion_impact.csv", "account_exclusion_impact_detail.csv": output / "account_exclusion_impact_detail.csv"}),
            patch("ashare_quant.cli.write_rpa_preflight", side_effect=remember("rpa-preflight", "rpa_preflight.csv")),
            patch("ashare_quant.cli.write_execution_readiness", side_effect=remember("execution", "execution_readiness.csv")),
            patch("ashare_quant.cli.run_auto_live_cycle", side_effect=lambda cfg: calls.append("auto-cycle") or {"auto_live_summary.csv": output / "auto_live_summary.csv"}),
            patch("ashare_quant.cli.write_evidence_plan", side_effect=remember("evidence", "evidence_plan.csv")),
            patch("ashare_quant.cli.write_observation_schedule", side_effect=remember("observation", "observation_schedule.csv")),
            patch("ashare_quant.cli.write_auto_live_remediation_plan", side_effect=remember("remediation", "auto_live_remediation_plan.csv")),
            patch("ashare_quant.cli.write_auto_live_verification_plan", side_effect=remember("verification", "auto_live_verification_plan.csv")),
            patch("ashare_quant.cli.run_auto_live", side_effect=lambda *args, **kwargs: calls.append("auto-run") or {}),
            patch("ashare_quant.cli.regenerate_report", side_effect=remember("report", "report.html")),
            patch("ashare_quant.cli.generate_control_panel", side_effect=remember("dashboard", "control_panel.html")),
        ):
            paths = run_one_click(config, args)

        self.assertIn("practical:True:True:True", calls)
        self.assertIn("shadow:True", calls)
        self.assertIn("shadow-audit", calls)
        self.assertIn("forward-only", calls)
        self.assertNotIn("challenger", calls)
        self.assertNotIn("research-candidates", calls)
        primary_observe.assert_not_called()
        research_observe.assert_not_called()
        self.assertIn("account-exclusion", calls)
        self.assertIn("rpa-preflight", calls)
        self.assertIn("auto-cycle", calls)
        self.assertIn("remediation", calls)
        self.assertIn("verification", calls)
        self.assertNotIn("auto-run", calls)
        self.assertTrue((output / "one_click_summary.csv").exists())
        self.assertIn("one_click_summary.csv", paths)

    def test_practical_can_continue_observation_without_generating_blocked_orders(self):
        output = Path.cwd() / "test_outputs" / "practical_observation_only"
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        (output / "rpa_orders.csv").write_text("ticket_id,status\nold,ready\n", encoding="utf-8")
        (output / "orders_provenance.csv").write_text("item,value\nsource,paper\n", encoding="utf-8")
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(
            refresh=False,
            allow_stale=True,
            skip_stress=True,
            reset_account=False,
            skip_report=True,
            quiet=True,
            continue_without_orders_when_blocked=True,
        )
        practical_path = output / "practical_readiness.csv"
        health_path = output / "strategy_health.csv"
        pd.DataFrame([{"check": "ready", "passed": False}]).to_csv(practical_path, index=False)

        def blocked_health(_config):
            pd.DataFrame([
                {
                "check": "paper_orders_allowed",
                "passed": False,
                "value": False,
                "severity": "paper_blocker",
                },
                {
                    "check": "strategy_health_status",
                    "passed": False,
                    "value": "pause_paper_orders",
                },
            ]).to_csv(health_path, index=False)
            return health_path

        with (
            patch("ashare_quant.cli.readiness_evidence_issues", return_value=[]),
            patch("ashare_quant.cli.write_practical_readiness", return_value=practical_path),
            patch("ashare_quant.cli.write_strategy_health", side_effect=blocked_health),
            patch("ashare_quant.cli.export_account_state", return_value={}),
            patch("ashare_quant.cli.run_paper") as paper,
            patch("ashare_quant.cli.export_rpa_orders") as export,
        ):
            paths = run_practical(config, args)

        paper.assert_not_called()
        export.assert_not_called()
        status = pd.read_csv(paths["order_generation_status.csv"])
        self.assertEqual(status.loc[0, "status"], "skipped_strategy_health")
        self.assertFalse(bool(status.loc[0, "orders_generated"]))
        self.assertEqual(status.loc[0, "reason"], "pause_paper_orders")
        self.assertFalse((output / "rpa_orders.csv").exists())
        self.assertFalse((output / "orders_provenance.csv").exists())
        invalidation = pd.read_csv(paths["invalidation:order_artifact_invalidation.csv"])
        self.assertEqual(set(invalidation["status"]), {"quarantined"})
        self.assertEqual(set(invalidation["reason"]), {"practical_cycle_started"})


    def test_one_click_summary_prioritizes_account_blocker_next_action(self):
        output = Path.cwd() / "test_outputs" / "cli_one_click_summary_priority"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        pd.DataFrame([{"status": "blocked", "auto_live_allowed": False, "blockers": "account_state_audit"}]).to_csv(output / "auto_live_summary.csv", index=False)
        pd.DataFrame([{"check": "account_state_audit", "passed": False}]).to_csv(output / "execution_readiness.csv", index=False)
        pd.DataFrame(
            [
                {"area": "forward_validation", "status": "collecting", "next_action": "继续每日观察。"},
                {"area": "account_audit", "status": "blocked", "next_action": "先修复账户缺价。"},
            ]
        ).to_csv(output / "evidence_plan.csv", index=False)

        path = write_one_click_summary(config)
        summary = pd.read_csv(path)

        self.assertEqual(summary["next_action"].iloc[0], "先修复账户缺价。")

    def test_one_click_summary_routes_paused_strategy_to_successor_observation(self):
        output = Path.cwd() / "test_outputs" / "cli_one_click_successor"
        output.mkdir(parents=True, exist_ok=True)
        candidate_config = "configs/robust.yaml"
        config = {
            "paths": {"output_dir": str(output)},
            "validation": {"primary_challenger_config": candidate_config},
        }
        pd.DataFrame(
            [
                {"check": "paper_orders_allowed", "value": False},
                {"check": "strict_live_allowed", "value": False},
                {"check": "strategy_health_status", "value": "pause_paper_orders"},
            ]
        ).to_csv(output / "strategy_health.csv", index=False)
        pd.DataFrame(
            [
                {
                    "candidate_strategy_name": "robust",
                    "decision": "research_only",
                }
            ]
        ).to_csv(output / "strategy_promotion_decision.csv", index=False)
        pd.DataFrame(
            [
                {
                    "status": "observed",
                    "candidate_config": candidate_config,
                    "candidate_name": "robust",
                    "backtest_mode": "reused",
                }
            ]
        ).to_csv(output / "primary_challenger_observation.csv", index=False)
        pd.DataFrame(
            [{"status": "blocked", "auto_live_allowed": False, "blockers": "account"}]
        ).to_csv(output / "auto_live_summary.csv", index=False)

        summary = pd.read_csv(write_one_click_summary(config))

        self.assertEqual(summary.loc[0, "status"], "observation_only")
        self.assertEqual(
            summary.loc[0, "strategy_operating_mode"],
            "successor_observation",
        )
        self.assertIn("robust", summary.loc[0, "next_action"])
        self.assertFalse(bool(summary.loc[0, "paper_orders_allowed"]))
    def test_one_click_summary_routes_quarantined_legacy_to_formal_forward_candidates(self):
        output = Path.cwd() / "test_outputs" / "cli_one_click_formal_successors"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "observation_task": {"max_age_hours": 36},
            "validation": {
                "forward_only_candidate_configs": ["configs/formal_a.yaml", "configs/formal_b.yaml"],
            },
        }
        pd.DataFrame([
            {"check": "paper_orders_allowed", "value": False},
            {"check": "strict_live_allowed", "value": False},
            {"check": "strategy_health_status", "value": "pause_paper_orders"},
        ]).to_csv(output / "strategy_health.csv", index=False)
        pd.DataFrame([{
            "candidate_strategy_name": "legacy",
            "decision": "do_not_promote_quarantined_dependency",
        }]).to_csv(output / "strategy_promotion_decision.csv", index=False)
        now = pd.Timestamp.now(tz="UTC").isoformat()
        pd.DataFrame([
            {
                "candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "candidate_config": candidate_config,
                "status": "observed",
                "max_observed_trading_days": observed,
                "required_forward_trading_days": 60,
                "frozen_input_evidence_passed": True,
                "membership_evidence_passed": True,
                "benchmark_mapping_evidence_passed": True,
                "shadow_evaluation_evidence_passed": True,
                "forward_risk_evaluation_evidence_passed": True,
                "generated_at": now,
            }
            for candidate_id, candidate_name, candidate_config, observed in (
                ("frozen-a", "formal_a", "configs/formal_a.yaml", 2),
                ("frozen-b", "formal_b", "configs/formal_b.yaml", 1),
            )
        ]).to_csv(output / "forward_only_candidate_observation.csv", index=False)
        pd.DataFrame([{"status": "blocked", "auto_live_allowed": False, "blockers": "strategy"}]).to_csv(
            output / "auto_live_summary.csv", index=False
        )

        summary = pd.read_csv(write_one_click_summary(config))

        self.assertEqual(summary.loc[0, "status"], "observation_only")
        self.assertEqual(summary.loc[0, "strategy_operating_mode"], "successor_observation")
        self.assertTrue(bool(summary.loc[0, "formal_candidate_observation_ok"]))
        self.assertIn("forward=1/60", summary.loc[0, "formal_candidate_observation"])
        self.assertIn("coverage=2/2", summary.loc[0, "formal_candidate_observation"])
        self.assertIn("formal_a", summary.loc[0, "next_action"])
        self.assertIn("formal_b", summary.loc[0, "next_action"])
        self.assertIn("不得生成工单", summary.loc[0, "next_action"])
    def test_one_click_summary_exposes_failed_primary_challenger(self):
        output = Path.cwd() / "test_outputs" / "cli_one_click_challenger_failure"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "validation": {"primary_challenger_config": "configs/candidate.yaml"},
        }
        pd.DataFrame([{"status": "manual_review", "auto_live_allowed": False, "blockers": ""}]).to_csv(
            output / "auto_live_summary.csv", index=False
        )
        pd.DataFrame(
            [{"status": "failed", "candidate_name": "candidate", "message": "backtest failed", "generated_at": "2026-07-13T13:00:00"}]
        ).to_csv(output / "primary_challenger_observation.csv", index=False)

        summary = pd.read_csv(write_one_click_summary(config))

        self.assertEqual(summary.loc[0, "status"], "observation_degraded")
        self.assertFalse(bool(summary.loc[0, "challenger_observation_ok"]))
        self.assertEqual(summary.loc[0, "challenger_status"], "failed")
        self.assertFalse(bool(summary.loc[0, "challenger_config_match"]))
        self.assertEqual(summary.loc[0, "challenger_message"], "backtest failed")

    def test_one_click_summary_rejects_observed_previous_challenger(self):
        output = Path.cwd() / "test_outputs" / "one_click_previous_challenger"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "validation": {"primary_challenger_config": "configs/current.yaml"},
        }
        pd.DataFrame([{"status": "manual_review", "auto_live_allowed": False, "blockers": ""}]).to_csv(
            output / "auto_live_summary.csv", index=False
        )
        pd.DataFrame([{
            "status": "observed",
            "candidate_config": "configs/previous.yaml",
            "candidate_name": "previous",
            "message": "Primary challenger evidence refreshed.",
            "generated_at": "2026-07-13T13:00:00",
        }]).to_csv(output / "primary_challenger_observation.csv", index=False)

        summary = pd.read_csv(write_one_click_summary(config))

        self.assertEqual(summary.loc[0, "status"], "observation_degraded")
        self.assertFalse(bool(summary.loc[0, "challenger_config_match"]))
        self.assertIn("配置已变更", summary.loc[0, "challenger_message"])
        self.assertIn("配置已变更", summary.loc[0, "next_action"])

    def test_workflow_runs_research_baselines_and_report(self):
        calls = []
        output = Path.cwd() / "test_outputs" / "cli_workflow_baselines"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}

        with (
            patch("ashare_quant.cli.run_strategy_research", side_effect=lambda config: calls.append("research") or output / "strategy_research.csv"),
            patch("ashare_quant.cli.run_strategy_baselines", side_effect=lambda config: calls.append("baselines") or output / "strategy_baselines.csv"),
            patch("ashare_quant.cli.run_factor_robustness", side_effect=lambda config: calls.append("factor") or {"factor_robustness.csv": output / "factor_robustness.csv", "factor_robustness_summary.csv": output / "factor_robustness_summary.csv"}),
            patch("ashare_quant.cli.run_backtest", side_effect=lambda config: calls.append("backtest") or {"orders.csv": output / "orders.csv"}),
            patch("ashare_quant.cli.write_cost_stress", side_effect=lambda config: calls.append("stress") or output / "cost_stress.csv"),
            patch("ashare_quant.cli.compare_backtest_outputs", side_effect=lambda config: calls.append("compare") or output / "strategy_backtest_compare.csv"),
            patch("ashare_quant.cli.regenerate_report", side_effect=lambda config: calls.append("report") or output / "report.html"),
        ):
            paths = run_workflow(config)

        self.assertEqual(calls, ["research", "baselines", "factor", "backtest", "stress", "compare", "report"])
        self.assertEqual(paths["strategy_baselines.csv"], output / "strategy_baselines.csv")
        self.assertEqual(paths["factor_robustness.csv"], output / "factor_robustness.csv")

    def test_report_render_issues_detects_mojibake(self):
        output = Path.cwd() / "test_outputs" / "cli_report_render"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}

        (output / "report.html").write_text("A 股量化交易系统报告 策略收益 严格实盘 实用状态", encoding="utf-8")
        self.assertEqual(report_render_issues(config), [])

        (output / "report.html").write_text("A ����������ϵͳ���� 策略收益", encoding="utf-8")
        issues = report_render_issues(config)
        self.assertTrue(any("missing Chinese term" in issue for issue in issues))

    def test_write_execution_readiness_from_outputs(self):
        output = Path.cwd() / "test_outputs" / "cli_execution_ready"
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "paths": {"output_dir": str(output)},
            "strategy": {"name": "test_strategy"},
            "rpa": {"require_manual_final_confirm": True, "never_click_submit": True},
        }
        orders = pd.DataFrame([{"date": "2026-01-02", "symbol": "600000", "side": "buy", "quantity": 100}])
        orders.to_csv(output / "orders.csv", index=False)
        pd.DataFrame(
            [
                {"item": "source", "value": "paper"},
                {"item": "allow_weak_strategy", "value": "False"},
                {"item": "strategy_name", "value": "test_strategy"},
                {"item": "config_sha256", "value": mapping_sha256(config)},
                {"item": "execution_date", "value": "2026-01-02"},
                {"item": "order_count", "value": "1"},
                {"item": "orders_sha256", "value": orders_frame_sha256(orders)},
            ]
        ).to_csv(output / "orders_provenance.csv", index=False)
        passed_check = pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}])
        for name in ["account_reconciliation.csv", "account_state_audit.csv", "rpa_pre_trade_checks.csv", "practical_readiness.csv"]:
            passed_check.to_csv(output / name, index=False)
        pd.DataFrame([{"status": "ready", "estimated_amount": 1000.0}]).to_csv(output / "rpa_orders.csv", index=False)
        pd.DataFrame([{"decision": "observe_only", "can_promote": False, "candidate_strategy_name": "candidate", "required_action": "python -m ashare_quant observe-candidates --quiet"}]).to_csv(
            output / "strategy_promotion_decision.csv",
            index=False,
        )

        def fake_decision(config):
            path = output / "strategy_promotion_decision.csv"
            pd.DataFrame([{"decision": "observe_only", "can_promote": False, "candidate_strategy_name": "candidate", "required_action": "python -m ashare_quant observe-candidates --quiet"}]).to_csv(
                path,
                index=False,
            )
            return path

        with (
            patch("ashare_quant.cli.build_live_readiness_from_outputs", return_value=passed_check),
            patch("ashare_quant.cli.build_practical_readiness_from_outputs", return_value=passed_check),
            patch("ashare_quant.cli.write_strategy_promotion_decision", side_effect=fake_decision),
            patch("ashare_quant.execution.optional_dependency_status", return_value={"pyautogui": True}),
        ):
            path = write_execution_readiness(config)

        readiness = pd.read_csv(path)
        source = pd.read_csv(output / "order_source_diagnostics.csv")
        roadmap = pd.read_csv(output / "execution_readiness_roadmap.csv")
        modes = pd.read_csv(output / "execution_modes.csv")
        playbook = pd.read_csv(output / "execution_playbook.csv")
        self.assertTrue(bool(readiness.loc[readiness["check"] == "rpa_ticket_status", "passed"].iloc[0]))
        self.assertFalse(bool(readiness.loc[readiness["check"] == "strategy_promotion_decision", "passed"].iloc[0]))
        self.assertEqual(source.loc[source["check"] == "orders_file_context", "value"].iloc[0], "paper_executable")
        self.assertFalse(roadmap.empty)
        self.assertFalse(modes.empty)
        self.assertFalse(playbook.empty)

    def test_promotion_decision_command_writes_decision(self):
        output = Path.cwd() / "test_outputs" / "cli_promotion_decision"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="promotion-decision")
        decision_path = output / "strategy_promotion_decision.csv"

        with patch("ashare_quant.cli.write_strategy_promotion_decision", return_value=decision_path):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("strategy_promotion_decision.csv", stdout.getvalue())

    def test_promotion_decision_command_compares_full_registered_challenger_set(self):
        root = Path.cwd() / "test_outputs" / "cli_promotion_full_set"
        main_output = root / "outputs"
        main_output.mkdir(parents=True, exist_ok=True)
        challenger_paths = []
        challenger_outputs = []
        for index in range(2):
            candidate_path = root / f"challenger_{index}.json"
            candidate_output = root / f"outputs_challenger_{index}"
            candidate_path.write_text(json.dumps({"paths": {"output_dir": str(candidate_output)}}), encoding="utf-8")
            challenger_paths.append(str(candidate_path))
            challenger_outputs.append(str(candidate_output.resolve()))
        config = {
            "paths": {"output_dir": str(main_output)},
            "validation": {"primary_challenger_configs": challenger_paths},
        }
        args = argparse.Namespace(command="promotion-decision")
        decision_path = main_output / "strategy_promotion_decision.csv"

        with (
            patch("ashare_quant.cli.compare_backtest_outputs", return_value=main_output / "strategy_backtest_compare.csv") as compare,
            patch("ashare_quant.cli.write_strategy_promotion_decision", return_value=decision_path),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            run_command(args, config)

        compared = compare.call_args.args[1]
        self.assertEqual(compared[0], str(main_output.resolve()))
        self.assertEqual(compared[1:], challenger_outputs)
    def test_remediation_plan_command_writes_plan(self):
        output = Path.cwd() / "test_outputs" / "cli_remediation_plan"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="remediation-plan")
        plan_path = output / "auto_live_remediation_plan.csv"

        with patch("ashare_quant.cli.write_auto_live_remediation_plan", return_value=plan_path):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("auto_live_remediation_plan.csv", stdout.getvalue())


    def test_verification_plan_command_writes_plan(self):
        output = Path.cwd() / "test_outputs" / "cli_verification_plan"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="verification-plan")
        plan_path = output / "auto_live_verification_plan.csv"

        with patch("ashare_quant.cli.write_auto_live_verification_plan", return_value=plan_path):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("auto_live_verification_plan.csv", stdout.getvalue())



    def test_account_exclusion_audit_command_writes_outputs(self):
        output = Path.cwd() / "test_outputs" / "cli_account_exclusion"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="account-exclusion-audit")
        paths = {"account_exclusion_impact.csv": output / "account_exclusion_impact.csv", "account_exclusion_impact_detail.csv": output / "account_exclusion_impact_detail.csv"}

        with patch("ashare_quant.cli.write_account_exclusion_impact", return_value=paths):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("account_exclusion_impact.csv", stdout.getvalue())
        self.assertIn("account_exclusion_impact_detail.csv", stdout.getvalue())

    def test_rpa_preflight_command_writes_audit(self):
        output = Path.cwd() / "test_outputs" / "cli_rpa_preflight"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="rpa-preflight", tickets=None, layout="configs/rpa_template.yaml")
        audit_path = output / "rpa_preflight.csv"

        with patch("ashare_quant.cli.write_rpa_preflight", return_value=audit_path):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("rpa_preflight.csv", stdout.getvalue())

    def test_order_audit_command_writes_audit(self):
        output = Path.cwd() / "test_outputs" / "cli_order_audit"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="order-audit")
        audit_path = output / "order_liquidity_audit.csv"

        with patch("ashare_quant.cli.write_order_liquidity_audit", return_value=audit_path):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("order_liquidity_audit.csv", stdout.getvalue())

    def test_strategy_health_command_writes_health(self):
        output = Path.cwd() / "test_outputs" / "cli_strategy_health"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="strategy-health")
        health_path = output / "strategy_health.csv"

        with patch("ashare_quant.cli.write_strategy_health", return_value=health_path):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("strategy_health.csv", stdout.getvalue())

    def test_dashboard_command_writes_control_panel(self):
        output = Path.cwd() / "test_outputs" / "cli_dashboard"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="dashboard")
        dashboard_path = output / "control_panel.html"

        with patch("ashare_quant.cli.generate_control_panel", return_value=dashboard_path):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("control_panel.html", stdout.getvalue())

    def test_account_plan_command_writes_plan(self):
        output = Path.cwd() / "test_outputs" / "cli_account_plan"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="account-plan")
        plan_path = output / "account_readiness_plan.csv"
        decision_path = output / "account_path_decision.csv"

        with (
            patch("ashare_quant.cli.write_account_readiness_plan", return_value=plan_path),
            patch("ashare_quant.cli.write_account_path_decision", return_value=decision_path),
        ):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        self.assertIn("account_readiness_plan.csv", stdout.getvalue())
        self.assertIn("account_path_decision.csv", stdout.getvalue())

    def test_account_repair_template_command_writes_paths(self):
        output = Path.cwd() / "test_outputs" / "cli_account_repair_template"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="account-repair-template", date=None)
        paths = {
            "account_import_from_state_template.csv": output / "account_import_from_state_template.csv",
            "account_repair_review.csv": output / "account_repair_review.csv",
        }

        with patch("ashare_quant.cli.write_account_repair_template", return_value=paths):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        text = stdout.getvalue()
        self.assertIn("account_import_from_state_template.csv", text)
        self.assertIn("account_repair_review.csv", text)

    def test_account_import_check_command_writes_paths_and_summary(self):
        output = Path.cwd() / "test_outputs" / "cli_account_import_check"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="account-import-check", file=str(output / "account.csv"), date=None)
        audit_path = output / "account_import_check_audit.csv"
        pd.DataFrame([{"check": "ok", "passed": True, "value": "ok", "threshold": "ok", "message": ""}]).to_csv(audit_path, index=False)
        paths = {
            "account_import_check_audit.csv": audit_path,
            "account_import_check_positions.csv": output / "account_import_check_positions.csv",
            "account_import_check_snapshot.csv": output / "account_import_check_snapshot.csv",
        }

        with patch("ashare_quant.cli.check_account_import_file", return_value=paths):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                run_command(args, config)

        text = stdout.getvalue()
        self.assertIn("account_import_check_audit.csv", text)
        self.assertIn("Account import check: passed", text)


    def local_run_fixture(self, name: str, *, skip_broker: bool) -> tuple[dict, argparse.Namespace, Path, Path]:
        output = Path.cwd() / "test_outputs" / name
        live_output = output / "live"
        output.mkdir(parents=True, exist_ok=True)
        live_output.mkdir(parents=True, exist_ok=True)
        health = output / "data_source_health.csv"
        pd.DataFrame([{"check": "price_cache_age_days", "passed": True, "value": 0, "threshold": 10}]).to_csv(health, index=False)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(
            refresh=False,
            max_symbols=None,
            cached_universe=False,
            no_metadata=False,
            allow_stale=False,
            skip_stress=True,
            skip_shadow=True,
            skip_broker=skip_broker,
            broker_optional=False,
            live_config="configs/test_live.yaml",
        )
        return config, args, health, live_output

    def huatai_read_only_evidence_config(self, output: Path) -> dict:
        return {
            "auto_live": {
                "desktop_canary_certification": {
                    "refresh_read_only_evidence_during_broker_refresh": True,
                    "preview_probe": {
                        "symbol": "600000",
                        "price": 1.0,
                        "quantity": 100,
                        "max_notional": 1000,
                    },
                    "evidence": {
                        "buy_preview": str(output / "buy.json"),
                        "sell_preview": str(output / "sell.json"),
                        "orders_monitor": str(output / "orders.json"),
                        "trades_monitor": str(output / "trades.json"),
                        "cancels_monitor": str(output / "cancels.json"),
                    },
                }
            }
        }

    def huatai_read_only_test_output(self, name: str) -> Path:
        output = Path.cwd() / "test_outputs" / name
        output.mkdir(parents=True, exist_ok=True)
        return output

    def test_huatai_read_only_evidence_refresh_disabled_runs_nothing(self):
        runner = MagicMock()
        self.assertEqual(
            refresh_huatai_canary_read_only_evidence({"auto_live": {}}, runner=runner),
            {},
        )
        runner.assert_not_called()

    def test_huatai_read_only_evidence_refresh_runs_five_safe_probes(self):
        output = self.huatai_read_only_test_output("huatai_read_only_evidence")
        config = self.huatai_read_only_evidence_config(output)
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            target = Path(command[command.index("--output") + 1])
            if "--side" in command:
                payload = {
                    "status": "preview_verified",
                    "form_readback_verified": True,
                    "submit_invoked": False,
                    "cancel_invoked": False,
                }
            else:
                payload = {
                    "status": "empty_verified",
                    "submit_invoked": False,
                    "cancel_invoked": False,
                }
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload), encoding="utf-8")
            return argparse.Namespace(returncode=0, stdout="verified", stderr="")

        paths = refresh_huatai_canary_read_only_evidence(config, runner=runner)

        self.assertEqual(len(calls), 5)
        self.assertEqual(len(paths), 5)
        self.assertEqual(
            [call[0][call[0].index("--side") + 1] for call in calls[:2]],
            ["buy", "sell"],
        )
        self.assertEqual(
            [call[0][call[0].index("--page") + 1] for call in calls[2:]],
            ["orders", "trades", "cancels"],
        )
        for command, kwargs in calls:
            self.assertFalse(kwargs["shell"])
            self.assertFalse(kwargs["check"])
            self.assertEqual(kwargs["timeout"], 30)
            self.assertEqual(command[0], __import__("sys").executable)

    def test_huatai_read_only_evidence_rejects_excess_notional_before_process(self):
        output = self.huatai_read_only_test_output("huatai_read_only_excess_notional")
        config = self.huatai_read_only_evidence_config(output)
        config["auto_live"]["desktop_canary_certification"]["preview_probe"]["price"] = 10.01
        runner = MagicMock()

        with self.assertRaisesRegex(ValueError, "exceeds configured maximum"):
            refresh_huatai_canary_read_only_evidence(config, runner=runner)

        runner.assert_not_called()

    def test_huatai_read_only_evidence_fails_closed_on_unsafe_payload(self):
        output = self.huatai_read_only_test_output("huatai_read_only_unsafe_payload")
        config = self.huatai_read_only_evidence_config(output)

        def runner(command, **kwargs):
            target = Path(command[command.index("--output") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps({
                    "status": "preview_verified",
                    "form_readback_verified": True,
                    "submit_invoked": True,
                    "cancel_invoked": False,
                }),
                encoding="utf-8",
            )
            return argparse.Namespace(returncode=0, stdout="", stderr="")

        with self.assertRaisesRegex(RuntimeError, "unsafe or invalid evidence"):
            refresh_huatai_canary_read_only_evidence(config, runner=runner)

    def test_huatai_read_only_refresh_keeps_canary_evidence_failure_separate(self):
        output = self.huatai_read_only_test_output("huatai_read_only_evidence_failure")
        snapshot_path = output / "snapshot.json"
        snapshot_path.write_text(json.dumps({"status": "ok", "positions": []}), encoding="utf-8")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {}}
        completed = argparse.Namespace(returncode=0, stdout="snapshot ready", stderr="")
        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.refresh_huatai_canary_read_only_evidence", side_effect=RuntimeError("unsafe evidence")),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}) as account_mock,
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={}),
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", return_value=output / "sandbox.json"),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        account_mock.assert_called_once_with(config, raise_on_blocked=False)
        status = pd.read_csv(paths["broker:huatai_read_only_refresh.csv"])
        self.assertEqual(status.loc[0, "status"], "ok")
        self.assertEqual(status.loc[0, "stage"], "completed")
        self.assertIn("Canary remains blocked: unsafe evidence", status.loc[0, "message"])

    def test_huatai_read_only_refresh_success_writes_structured_status(self):
        output = Path.cwd() / "test_outputs" / "huatai_read_only_refresh_success"
        output.mkdir(parents=True, exist_ok=True)
        snapshot_path = output / "snapshot.json"
        snapshot_path.write_text(json.dumps({
            "schema_version": "ashare_quant.huatai_desktop_snapshot.v1",
            "status": "ok",
            "environment": "live",
            "account": {"account_id": "masked"},
            "positions": [],
        }), encoding="utf-8")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {}}
        completed = argparse.Namespace(returncode=0, stdout="snapshot ready", stderr="")
        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.query_auto_live_account", return_value={"account.csv": output / "account.csv"}) as account_mock,
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}) as preview_mock,
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={"capital.csv": output / "capital.csv"}) as capital_mock,
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={"order_preview.csv": output / "order_preview.csv"}) as order_preview_mock,
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", return_value=output / "sandbox.json"),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        status = pd.read_csv(paths["broker:huatai_read_only_refresh.csv"])
        account_mock.assert_called_once_with(config, raise_on_blocked=False)
        preview_mock.assert_called_once_with(config)
        capital_mock.assert_called_once_with(config)
        order_preview_mock.assert_called_once_with(config)
        self.assertEqual(status.loc[0, "status"], "ok")
        self.assertEqual(status.loc[0, "stage"], "completed")
        self.assertEqual(status.loc[0, "snapshot_status"], "ok")
        self.assertIn("broker:account.csv", paths)

    def test_huatai_read_only_refresh_prepares_absent_live_ledger_without_commit(self):
        output = Path.cwd() / "test_outputs" / "huatai_read_only_refresh_initialization_preview"
        output.mkdir(parents=True, exist_ok=True)
        state_db = output / "live.sqlite3"
        if state_db.exists():
            state_db.unlink()
        snapshot_path = output / "snapshot.json"
        snapshot_path.write_text(json.dumps({
            "schema_version": "ashare_quant.huatai_desktop_snapshot.v1",
            "status": "ok",
            "environment": "live",
            "account": {"account_id": "masked"},
            "positions": [],
        }), encoding="utf-8")
        pd.DataFrame([{
            "snapshot_run_id": "snapshot-1",
            "account_id": "masked-account",
        }]).to_csv(output / "auto_live_broker_account_snapshot.csv", index=False)
        config = {"paths": {"output_dir": str(output), "state_db": str(state_db)}, "auto_live": {}}
        completed = argparse.Namespace(returncode=0, stdout="snapshot ready", stderr="")
        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)
        init_audit = output / "broker_account_initialization_audit.csv"

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}),
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.initialize_broker_account_from_snapshot", return_value={"broker_account_initialization_audit.csv": init_audit}) as init_mock,
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={}),
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", return_value=output / "sandbox.json"),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        init_mock.assert_called_once_with(
            config,
            expected_snapshot_run_id="snapshot-1",
            expected_account_id="masked-account",
            confirm=False,
        )
        self.assertIn("broker:broker_account_initialization_audit.csv", paths)
        self.assertFalse(state_db.exists())

    def test_huatai_read_only_refresh_keeps_canary_blocker_separate(self):
        output = Path.cwd() / "test_outputs" / "huatai_read_only_refresh_canary_blocked"
        output.mkdir(parents=True, exist_ok=True)
        snapshot_path = output / "snapshot.json"
        snapshot_path.write_text(json.dumps({
            "schema_version": "ashare_quant.huatai_desktop_snapshot.v1",
            "status": "ok",
            "environment": "live",
            "account": {"account_id": "masked"},
            "positions": [],
        }), encoding="utf-8")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {}}
        completed = argparse.Namespace(returncode=0, stdout="snapshot ready", stderr="")
        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}),
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={}),
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", side_effect=RuntimeError("monitor evidence stale")),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        status = pd.read_csv(paths["broker:huatai_read_only_refresh.csv"])
        self.assertEqual(status.loc[0, "status"], "ok")
        self.assertEqual(status.loc[0, "stage"], "completed")
        self.assertIn("Canary remains blocked", status.loc[0, "message"])

    def test_huatai_read_only_refresh_rejects_blocked_account_preview(self):
        output = Path.cwd() / "test_outputs" / "huatai_read_only_refresh_account_blocked"
        output.mkdir(parents=True, exist_ok=True)
        snapshot_path = output / "snapshot.json"
        snapshot_path.write_text(json.dumps({
            "schema_version": "ashare_quant.huatai_desktop_snapshot.v1",
            "status": "ok",
            "environment": "live",
            "account": {"account_id": "masked"},
            "positions": [],
        }), encoding="utf-8")
        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": False}]).to_csv(preview_audit, index=False)
        config = {"paths": {"output_dir": str(output)}, "auto_live": {}}
        completed = argparse.Namespace(returncode=0, stdout="snapshot ready", stderr="")

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}),
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            with self.assertRaisesRegex(RuntimeError, "broker account preview blocked"):
                run_huatai_read_only_refresh(config)

        status = pd.read_csv(output / "huatai_read_only_refresh.csv")
        self.assertEqual(status.loc[0, "status"], "failed")
        self.assertEqual(status.loc[0, "stage"], "account_preview")

    def test_huatai_read_only_refresh_retries_once_after_windowless_wake(self):
        output = self.huatai_read_only_test_output("huatai_read_only_window_wake")
        snapshot_path = output / "snapshot.json"
        config = {
            "paths": {"output_dir": str(output)},
            "desktop_client": {
                "wake_windowless_on_refresh": True,
                "window_wake_wait_seconds": 0,
            },
            "auto_live": {},
        }
        calls = {"count": 0}

        def snapshot_runner(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                snapshot_path.write_text(json.dumps({
                    "status": "failed",
                    "diagnostics": {"stage": "client_window_absent"},
                    "message": "window absent",
                }), encoding="utf-8")
                return argparse.Namespace(returncode=1, stdout="window absent", stderr="")
            snapshot_path.write_text(json.dumps({
                "status": "ok",
                "environment": "live",
                "account": {"account_id": "masked"},
                "positions": [],
                "diagnostics": {"stage": "account_snapshot_ready", "visible_client_windows": 1},
            }), encoding="utf-8")
            return argparse.Namespace(returncode=0, stdout="snapshot ready", stderr="")

        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)
        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", side_effect=snapshot_runner),
            patch("ashare_quant.cli.request_huatai_client_window_wake", return_value=(True, "requested")) as wake_mock,
            patch("ashare_quant.cli.time.sleep"),
            patch("ashare_quant.cli.write_huatai_client_status", return_value=output / "client.csv"),
            patch("ashare_quant.cli.refresh_huatai_canary_read_only_evidence", return_value={}),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}),
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={}),
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", return_value=output / "sandbox.json"),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        wake_mock.assert_called_once_with(config)
        self.assertEqual(calls["count"], 2)
        status = pd.read_csv(paths["broker:huatai_read_only_refresh.csv"])
        self.assertTrue(bool(status.loc[0, "window_wake_attempted"]))
        self.assertEqual(int(status.loc[0, "snapshot_attempts"]), 2)
        self.assertEqual(status.loc[0, "status"], "ok")

    def test_huatai_read_only_refresh_retries_after_minimized_window_wake(self):
        output = self.huatai_read_only_test_output("huatai_read_only_minimized_wake")
        snapshot_path = output / "snapshot.json"
        config = {
            "paths": {"output_dir": str(output)},
            "desktop_client": {
                "wake_windowless_on_refresh": True,
                "window_wake_wait_seconds": 0,
            },
            "auto_live": {},
        }
        calls = {"count": 0}

        def snapshot_runner(*args, **kwargs):
            calls["count"] += 1
            ready = calls["count"] == 3
            snapshot_path.write_text(json.dumps({
                "status": "ok" if ready else "failed",
                "environment": "live" if ready else "",
                "account": {"account_id": "masked"} if ready else {},
                "positions": [],
                "diagnostics": {
                    "stage": (
                        "account_snapshot_ready"
                        if ready
                        else "client_window_minimized" if calls["count"] == 1
                        else "trading_page_unavailable"
                    ),
                    "visible_client_windows": 1 if ready else 0,
                    "total_client_windows": 1,
                    "minimized_client_windows": 0 if ready else 1,
                },
                "message": "" if ready else "window minimized",
            }), encoding="utf-8")
            return argparse.Namespace(
                returncode=0 if ready else 1,
                stdout="snapshot ready" if ready else "window minimized",
                stderr="",
            )

        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)
        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", side_effect=snapshot_runner),
            patch("ashare_quant.cli.request_huatai_client_window_wake", return_value=(True, "requested")) as wake_mock,
            patch("ashare_quant.cli.time.sleep"),
            patch("ashare_quant.cli.write_huatai_client_status", return_value=output / "client.csv"),
            patch("ashare_quant.cli.refresh_huatai_canary_read_only_evidence", return_value={}),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}),
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={}),
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", return_value=output / "sandbox.json"),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        wake_mock.assert_called_once_with(config)
        self.assertEqual(calls["count"], 3)
        status = pd.read_csv(paths["broker:huatai_read_only_refresh.csv"])
        self.assertTrue(bool(status.loc[0, "window_wake_attempted"]))
        self.assertEqual(int(status.loc[0, "snapshot_attempts"]), 3)
        self.assertEqual(status.loc[0, "desktop_stage"], "account_snapshot_ready")
        self.assertEqual(status.loc[0, "status"], "ok")
    def test_huatai_read_only_refresh_waits_through_slow_window_startup(self):
        output = self.huatai_read_only_test_output("huatai_read_only_slow_window")
        snapshot_path = output / "snapshot.json"
        config = {
            "paths": {"output_dir": str(output)},
            "desktop_client": {
                "wake_windowless_on_refresh": True,
                "window_wake_wait_seconds": 0,
                "window_ready_timeout_seconds": 30,
                "window_ready_poll_seconds": 0.25,
                "window_ready_max_attempts": 5,
            },
            "auto_live": {},
        }
        calls = {"count": 0}

        def snapshot_runner(*args, **kwargs):
            calls["count"] += 1
            ready = calls["count"] == 4
            snapshot_path.write_text(json.dumps({
                "status": "ok" if ready else "failed",
                "environment": "live" if ready else "",
                "account": {"account_id": "masked"} if ready else {},
                "positions": [],
                "diagnostics": {
                    "stage": "account_snapshot_ready" if ready else "client_window_absent",
                    "visible_client_windows": 1 if ready else 0,
                    "xiadan_native_window_count": 4 if ready else 0,
                    "xiadan_visible_window_count": 2 if ready else 0,
                    "xiadan_top_level_window_count": 1 if ready else 0,
                },
                "message": "" if ready else "window absent",
            }), encoding="utf-8")
            return argparse.Namespace(
                returncode=0 if ready else 1,
                stdout="snapshot ready" if ready else "window absent",
                stderr="",
            )

        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)
        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", side_effect=snapshot_runner),
            patch("ashare_quant.cli.request_huatai_client_window_wake", return_value=(True, "requested")),
            patch("ashare_quant.cli.time.sleep"),
            patch("ashare_quant.cli.write_huatai_client_status", return_value=output / "client.csv"),
            patch("ashare_quant.cli.refresh_huatai_canary_read_only_evidence", return_value={}),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}),
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={}),
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", return_value=output / "sandbox.json"),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        self.assertEqual(calls["count"], 4)
        status = pd.read_csv(paths["broker:huatai_read_only_refresh.csv"])
        self.assertEqual(int(status.loc[0, "snapshot_attempts"]), 4)
        self.assertEqual(status.loc[0, "desktop_stage"], "account_snapshot_ready")
        self.assertEqual(int(status.loc[0, "xiadan_native_window_count"]), 4)
        self.assertEqual(int(status.loc[0, "xiadan_visible_window_count"]), 2)
        self.assertEqual(int(status.loc[0, "xiadan_top_level_window_count"]), 1)
        self.assertEqual(status.loc[0, "status"], "ok")

    def test_huatai_read_only_refresh_waits_for_autostarted_trading_process(self):
        output = self.huatai_read_only_test_output("huatai_read_only_autostart_race")
        snapshot_path = output / "snapshot.json"
        config = {
            "paths": {"output_dir": str(output)},
            "desktop_client": {
                "auto_start": True,
                "window_wake_wait_seconds": 0,
                "window_ready_timeout_seconds": 30,
                "window_ready_poll_seconds": 0.25,
                "window_ready_max_attempts": 5,
            },
            "auto_live": {},
        }
        calls = {"count": 0}

        def snapshot_runner(*args, **kwargs):
            calls["count"] += 1
            stages = ["client_process_missing", "trading_module_ui_not_loaded", "account_snapshot_ready"]
            stage = stages[calls["count"] - 1]
            ready = stage == "account_snapshot_ready"
            snapshot_path.write_text(json.dumps({
                "status": "ok" if ready else "failed",
                "environment": "live" if ready else "",
                "account": {"account_id": "masked"} if ready else {},
                "positions": [],
                "diagnostics": {"stage": stage, "visible_client_windows": int(ready)},
                "message": "" if ready else stage,
            }), encoding="utf-8")
            return argparse.Namespace(
                returncode=0 if ready else 1,
                stdout="snapshot ready" if ready else stage,
                stderr="",
            )

        preview_audit = output / "broker_account_import_preview_audit.csv"
        pd.DataFrame([{"check": "broker_account_preview_ready", "passed": True}]).to_csv(preview_audit, index=False)
        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.subprocess.run", side_effect=snapshot_runner),
            patch("ashare_quant.cli.request_huatai_client_window_wake") as wake_mock,
            patch("ashare_quant.cli.time.sleep"),
            patch("ashare_quant.cli.write_huatai_client_status", return_value=output / "client.csv"),
            patch("ashare_quant.cli.refresh_huatai_canary_read_only_evidence", return_value={}),
            patch("ashare_quant.cli.query_auto_live_account", return_value={}),
            patch("ashare_quant.cli.write_broker_readonly_history", return_value={}),
            patch("ashare_quant.cli.write_broker_account_import_preview", return_value={"broker_account_import_preview_audit.csv": preview_audit}),
            patch("ashare_quant.cli.write_broker_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_canary_capital_feasibility", return_value={}),
            patch("ashare_quant.cli.write_broker_order_preview", return_value={}),
            patch("ashare_quant.cli.write_huatai_execution_sandbox_summary", return_value=output / "sandbox.json"),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            paths = run_huatai_read_only_refresh(config)

        wake_mock.assert_not_called()
        self.assertEqual(calls["count"], 3)
        status = pd.read_csv(paths["broker:huatai_read_only_refresh.csv"])
        self.assertFalse(bool(status.loc[0, "window_wake_attempted"]))
        self.assertEqual(int(status.loc[0, "snapshot_attempts"]), 3)
        self.assertEqual(status.loc[0, "desktop_stage"], "account_snapshot_ready")
        self.assertEqual(status.loc[0, "status"], "ok")

    def test_huatai_read_only_refresh_updates_client_status_before_snapshot_failure(self):
        output = Path.cwd() / "test_outputs" / "huatai_read_only_refresh_snapshot_failed"
        output.mkdir(parents=True, exist_ok=True)
        snapshot_path = output / "snapshot.json"
        snapshot_path.write_text(json.dumps({
            "schema_version": "ashare_quant.huatai_desktop_snapshot.v1",
            "status": "failed",
            "diagnostics": {"stage": "client_process_missing"},
            "message": "client window not found",
        }), encoding="utf-8")
        config = {"paths": {"output_dir": str(output)}, "auto_live": {}}
        completed = argparse.Namespace(returncode=1, stdout="client window not found", stderr="")
        client_status_path = output / "huatai_client_status.csv"

        with (
            patch("ashare_quant.cli.huatai_snapshot_path", return_value=snapshot_path),
            patch("ashare_quant.cli.write_huatai_client_status", return_value=client_status_path) as client_status_mock,
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=output / "report.html"),
        ):
            with self.assertRaisesRegex(RuntimeError, "client window not found"):
                run_huatai_read_only_refresh(config)

        client_status_mock.assert_called_once_with(config, start_if_needed=True)
        status = pd.read_csv(output / "huatai_read_only_refresh.csv")
        self.assertEqual(status.loc[0, "status"], "failed")
        self.assertEqual(status.loc[0, "desktop_stage"], "client_process_missing")

    def test_broker_account_init_dispatches_explicit_identity_without_submit(self):
        output = Path.cwd() / "test_outputs" / "broker_account_init_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(
            command="broker-account-init",
            snapshot_run_id="snapshot-1",
            account_id="masked-account",
            confirm=False,
        )
        expected = {"audit.csv": output / "audit.csv"}
        with patch("ashare_quant.cli.initialize_broker_account_from_snapshot", return_value=expected) as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(
            config,
            expected_snapshot_run_id="snapshot-1",
            expected_account_id="masked-account",
            confirm=False,
        )

    def test_broker_refresh_dispatches_without_submit_path(self):
        output = Path.cwd() / "test_outputs" / "broker_refresh_dispatch"
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(command="broker-refresh", live_config="configs/test_live.yaml")
        with patch("ashare_quant.cli.run_broker_refresh", return_value={"status": output / "status.csv"}) as mocked:
            run_command(args, config)
        mocked.assert_called_once_with(config, args)

    def test_refresh_live_deployment_evidence_never_calls_order_or_submit_paths(self):
        output = Path.cwd() / "test_outputs" / "refresh_live_deployment"
        live_output = output / "live"
        config = {"paths": {"output_dir": str(output)}}
        live_config = {"paths": {"output_dir": str(live_output)}}
        calls = []

        with (
            patch("ashare_quant.cli.sync_live_strategy_evidence", side_effect=lambda source, live: calls.append("sync") or {"sync": live_output / "sync.csv"}),
            patch("ashare_quant.cli.run_candidate_audit", side_effect=lambda cfg: calls.append("candidate") or live_output / "candidate.csv"),
            patch("ashare_quant.cli.regenerate_report", side_effect=lambda cfg: calls.append("report") or live_output / "report.html"),
            patch("ashare_quant.cli.generate_control_panel", side_effect=lambda cfg: calls.append("dashboard") or live_output / "control_panel.html"),
            patch("ashare_quant.cli.run_paper") as paper_mock,
            patch("ashare_quant.cli.run_auto_live") as auto_live_mock,
        ):
            paths = refresh_live_deployment_evidence(config, live_config)

        self.assertEqual(calls, ["sync", "candidate", "report", "dashboard"])
        paper_mock.assert_not_called()
        auto_live_mock.assert_not_called()
        self.assertIn("live:report.html", paths)

    def test_local_run_strategy_failure_writes_status_then_raises(self):
        config, args, health, live_output = self.local_run_fixture("local_run_strategy_error", skip_broker=True)
        captured = {}

        def fake_status(config, live_config, phase_errors=None):
            captured.update(phase_errors or {})
            return {"local_operator_status.html": Path(config["paths"]["output_dir"]) / "local_operator_status.html"}

        with (
            patch("ashare_quant.cli.write_data_source_health", return_value=health),
            patch("ashare_quant.cli.run_one_click", side_effect=RuntimeError("strategy boom")),
            patch("ashare_quant.cli.load_config", return_value={"paths": {"output_dir": str(live_output)}, "auto_live": {}}),
            patch("ashare_quant.cli.refresh_live_deployment_evidence", return_value={"live:report.html": live_output / "report.html"}),
            patch("ashare_quant.cli.write_local_operator_status", side_effect=fake_status),
        ):
            with self.assertRaisesRegex(RuntimeError, "local-run blocked: strategy=RuntimeError: strategy boom"):
                run_local_system(config, args)

        self.assertIn("strategy", captured)

    def test_local_run_broker_failure_writes_status_then_raises(self):
        config, args, health, live_output = self.local_run_fixture("local_run_broker_error", skip_broker=False)
        completed = argparse.Namespace(returncode=1, stdout="", stderr="client missing")
        captured = {}

        def fake_status(config, live_config, phase_errors=None):
            captured.update(phase_errors or {})
            return {"local_operator_status.html": Path(config["paths"]["output_dir"]) / "local_operator_status.html"}

        with (
            patch("ashare_quant.cli.write_data_source_health", return_value=health),
            patch("ashare_quant.cli.run_one_click", return_value={}),
            patch("ashare_quant.cli.load_config", return_value={"paths": {"output_dir": str(live_output)}, "auto_live": {}}),
            patch("ashare_quant.cli.refresh_live_deployment_evidence", return_value={"live:report.html": live_output / "report.html"}),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=live_output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=live_output / "report.html"),
            patch("ashare_quant.cli.write_local_operator_status", side_effect=fake_status),
        ):
            with self.assertRaisesRegex(RuntimeError, "local-run blocked: broker=RuntimeError: client missing"):
                run_local_system(config, args)

        self.assertIn("broker", captured)

    def test_local_run_optional_broker_offline_keeps_strategy_result(self):
        config, args, health, live_output = self.local_run_fixture("local_run_broker_optional", skip_broker=False)
        args.broker_optional = True
        completed = argparse.Namespace(
            returncode=1,
            stdout="",
            stderr="Huatai client process has no desktop window; reopen the client and log in",
        )
        captured = {}

        def fake_status(config, live_config, phase_errors=None):
            captured.update(phase_errors or {})
            return {"local_operator_status.html": Path(config["paths"]["output_dir"]) / "local_operator_status.html"}

        with (
            patch("ashare_quant.cli.write_data_source_health", return_value=health),
            patch("ashare_quant.cli.run_one_click", return_value={}),
            patch("ashare_quant.cli.load_config", return_value={"paths": {"output_dir": str(live_output)}, "auto_live": {}}),
            patch("ashare_quant.cli.refresh_live_deployment_evidence", return_value={"live:report.html": live_output / "report.html"}),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=live_output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=live_output / "report.html"),
            patch("ashare_quant.cli.write_deployment_gate", return_value={"deployment_gate.csv": Path(config["paths"]["output_dir"]) / "deployment_gate.csv"}) as gate_mock,
            patch("ashare_quant.cli.write_local_operator_status", side_effect=fake_status),
        ):
            paths = run_local_system(config, args)

        self.assertIn("local_operator_status.html", paths)
        self.assertNotIn("broker", captured)
        gate_mock.assert_called_once()

    def test_local_run_optional_does_not_hide_non_offline_broker_errors(self):
        config, args, health, live_output = self.local_run_fixture("local_run_broker_optional_error", skip_broker=False)
        args.broker_optional = True
        completed = argparse.Namespace(returncode=1, stdout="", stderr="snapshot schema invalid")
        with (
            patch("ashare_quant.cli.write_data_source_health", return_value=health),
            patch("ashare_quant.cli.run_one_click", return_value={}),
            patch("ashare_quant.cli.load_config", return_value={"paths": {"output_dir": str(live_output)}, "auto_live": {}}),
            patch("ashare_quant.cli.refresh_live_deployment_evidence", return_value={"live:report.html": live_output / "report.html"}),
            patch("ashare_quant.cli.subprocess.run", return_value=completed),
            patch("ashare_quant.cli.write_huatai_canary_arming_audit", return_value=live_output / "canary.csv"),
            patch("ashare_quant.cli.regenerate_report", return_value=live_output / "report.html"),
            patch("ashare_quant.cli.write_local_operator_status", return_value={"local_operator_status.html": Path(config["paths"]["output_dir"]) / "status.html"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "snapshot schema invalid"):
                run_local_system(config, args)

    def test_local_run_explicit_skip_broker_can_succeed(self):
        config, args, health, live_output = self.local_run_fixture("local_run_skip_broker", skip_broker=True)
        status_path = Path(config["paths"]["output_dir"]) / "local_operator_status.html"
        with (
            patch("ashare_quant.cli.write_data_source_health", return_value=health),
            patch("ashare_quant.cli.run_one_click", return_value={}),
            patch("ashare_quant.cli.load_config", return_value={"paths": {"output_dir": str(live_output)}, "auto_live": {}}),
            patch("ashare_quant.cli.refresh_live_deployment_evidence", return_value={"live:report.html": live_output / "report.html"}),
            patch("ashare_quant.cli.write_local_operator_status", return_value={"local_operator_status.html": status_path}),
        ):
            paths = run_local_system(config, args)

        self.assertEqual(paths["local_operator_status.html"], status_path)

    def test_local_run_stale_cache_preflight(self):
        output = Path.cwd() / "test_outputs" / "local_run_preflight"
        output.mkdir(parents=True, exist_ok=True)
        path = output / "data_source_health.csv"
        pd.DataFrame([
            {"check": "price_cache_age_days", "passed": False, "value": 13, "threshold": 10}
        ]).to_csv(path, index=False)
        message = local_run_stale_cache_message(path)
        self.assertIn("age=13", message)
        self.assertIn("--refresh", message)
        pd.DataFrame([
            {"check": "price_cache_age_days", "passed": True, "value": 2, "threshold": 10}
        ]).to_csv(path, index=False)
        self.assertEqual(local_run_stale_cache_message(path), "")

    def test_practical_blocks_before_order_generation_on_execution_identity_failure(self):
        output = Path.cwd() / "test_outputs" / "practical_execution_identity_block"
        output.mkdir(parents=True, exist_ok=True)
        config = {"paths": {"output_dir": str(output)}}
        args = argparse.Namespace(
            refresh=False,
            max_symbols=None,
            cached_universe=False,
            no_metadata=False,
            allow_stale=False,
            skip_stress=True,
            reset_account=False,
            reset_cash=None,
            confirm_account_reset=False,
            continue_without_orders_when_blocked=False,
        )

        with (
            patch("ashare_quant.cli.require_active_execution_consistency", side_effect=RuntimeError("identity blocked")),
            patch("ashare_quant.cli.run_paper") as paper,
        ):
            with self.assertRaisesRegex(RuntimeError, "identity blocked"):
                run_practical(config, args)

        paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
