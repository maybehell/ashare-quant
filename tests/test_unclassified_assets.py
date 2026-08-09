import json
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from ashare_quant.autolive import auto_live_config_sha256
from ashare_quant.autolive_account import (
    build_auto_live_broker_account_audit,
    normalize_command_adapter_account_snapshot,
)
from ashare_quant.broker_preview import write_broker_account_import_preview


class UnclassifiedAssetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / "test_outputs" / self._testMethodName
        self.output = self.root / "outputs_live"
        self.output.mkdir(parents=True, exist_ok=True)
        for path in self.output.glob("*"):
            if path.is_file():
                path.unlink()

    def config(self) -> dict:
        config = {
            "paths": {
                "output_dir": str(self.output),
                "state_db": str(self.root / "live.sqlite3"),
            },
            "reconciliation": {"cash_tolerance": 1.0},
            "auto_live": {"account_snapshot_max_age_seconds": 300},
        }
        return config

    def test_normalization_keeps_unexplained_total_asset_out_of_cash(self):
        result = self.output / "account.json"
        result.write_text(
            json.dumps(
                {
                    "broker": "huatai",
                    "environment": "live",
                    "account": {
                        "account_id": "masked",
                        "cash": 0.0,
                        "available_cash": 0.0,
                        "frozen_cash": 0.0,
                        "market_value": 0.0,
                        "total_asset": 2567.44,
                    },
                    "positions": [],
                }
            ),
            encoding="utf-8",
        )

        snapshot, _ = normalize_command_adapter_account_snapshot("run-1", result)

        self.assertEqual(float(snapshot.loc[0, "cash"]), 0.0)
        self.assertEqual(float(snapshot.loc[0, "unclassified_asset_value"]), 2567.44)

    def test_account_audit_reconciles_but_blocks_unclassified_assets(self):
        config = self.config()
        run_id = "run-2"
        snapshot = pd.DataFrame(
            [
                {
                    "snapshot_run_id": run_id,
                    "broker": "huatai",
                    "account_id": "masked",
                    "environment": "live",
                    "cash": 0.0,
                    "available_cash": 0.0,
                    "frozen_cash": 0.0,
                    "market_value": 0.0,
                    "total_asset": 2567.44,
                    "unclassified_asset_value": 2567.44,
                    "positions_count": 0,
                    "broker_time": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "auto_live_config_sha256": auto_live_config_sha256(config),
                    "message": "",
                }
            ]
        )

        audit = build_auto_live_broker_account_audit(
            config, snapshot, pd.DataFrame(), run_id, True, "command_ready"
        ).set_index("check")

        self.assertTrue(bool(audit.loc["broker_total_asset_consistent", "passed"]))
        self.assertFalse(bool(audit.loc["broker_unclassified_asset_zero", "passed"]))

    def test_import_preview_uses_zero_cash_and_stays_blocked(self):
        config = self.config()
        fingerprint = auto_live_config_sha256(config)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        pd.DataFrame(
            [
                {
                    "snapshot_run_id": "run-3",
                    "broker": "huatai",
                    "account_id": "masked",
                    "environment": "live",
                    "cash": 0.0,
                    "available_cash": 0.0,
                    "frozen_cash": 0.0,
                    "market_value": 0.0,
                    "total_asset": 2567.44,
                    "unclassified_asset_value": 2567.44,
                    "positions_count": 0,
                    "broker_time": now,
                    "generated_at": now,
                    "auto_live_config_sha256": fingerprint,
                    "message": "",
                }
            ]
        ).to_csv(self.output / "auto_live_broker_account_snapshot.csv", index=False)
        pd.DataFrame(columns=["snapshot_run_id", "symbol", "quantity", "sellable_quantity", "cost", "market_value", "last_price", "message"]).to_csv(
            self.output / "auto_live_broker_positions.csv", index=False
        )
        pd.DataFrame([{"check": "broker_account_snapshot_present", "passed": True}]).to_csv(
            self.output / "auto_live_broker_account_audit.csv", index=False
        )

        def passing_precheck(_config, _source):
            audit = self.output / "account_import_check_audit.csv"
            positions = self.output / "account_import_check_positions.csv"
            snapshot = self.output / "account_import_check_snapshot.csv"
            pd.DataFrame([{"check": "dry_run", "passed": True}]).to_csv(audit, index=False)
            pd.DataFrame().to_csv(positions, index=False)
            pd.DataFrame([{"cash": 0.0, "equity": 0.0}]).to_csv(snapshot, index=False)
            return {
                "account_import_check_audit.csv": audit,
                "account_import_check_positions.csv": positions,
                "account_import_check_snapshot.csv": snapshot,
            }

        with patch(
            "ashare_quant.broker_preview.check_account_import_file",
            side_effect=passing_precheck,
        ):
            paths = write_broker_account_import_preview(config)

        preview = pd.read_csv(paths["broker_account_import_preview.csv"])
        audit = pd.read_csv(paths["broker_account_import_preview_audit.csv"]).set_index("check")
        self.assertEqual(float(preview["cash"].dropna().iloc[0]), 0.0)
        self.assertFalse(bool(audit.loc["unclassified_assets_block_initialization", "passed"]))
        self.assertFalse(bool(audit.loc["broker_account_preview_ready", "passed"]))
        self.assertFalse(Path(config["paths"]["state_db"]).exists())


if __name__ == "__main__":
    unittest.main()
