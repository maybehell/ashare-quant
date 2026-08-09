from pathlib import Path
import unittest

import pandas as pd

from ashare_quant.execution_quality import build_execution_quality, write_execution_quality


class ExecutionQualityTests(unittest.TestCase):
    def test_matched_fill_with_small_slippage_passes(self):
        config = self.make_config("exec_quality_pass", max_slippage=50)
        fills = pd.DataFrame(
            [{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 10.02, "fee": 5, "amount": 1002, "reason": "manual"}]
        )
        match = pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "fill_quantity": 100, "order_quantity": 100, "matched": True, "message": "matched"}])
        rpa_orders = pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "limit_price": 10.0}])

        summary, detail = build_execution_quality(config, fills=fills, order_match=match, rpa_orders=rpa_orders)

        self.assertTrue(summary["passed"].astype(bool).all())
        self.assertEqual(detail["status"].iloc[0], "ok")
        self.assertAlmostEqual(float(detail["adverse_slippage_bps"].iloc[0]), 20.0)

    def test_large_adverse_slippage_requires_review(self):
        config = self.make_config("exec_quality_slippage", max_slippage=50)
        fills = pd.DataFrame(
            [{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "price": 10.20, "fee": 5, "amount": 1020, "reason": "manual"}]
        )
        match = pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "fill_quantity": 100, "order_quantity": 100, "matched": True, "message": "matched"}])
        rpa_orders = pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "buy", "quantity": 100, "limit_price": 10.0}])

        summary, detail = build_execution_quality(config, fills=fills, order_match=match, rpa_orders=rpa_orders)

        failed = set(summary.loc[~summary["passed"].astype(bool), "check"])
        self.assertIn("max_adverse_slippage_bps", failed)
        self.assertIn("bad_slippage_count", failed)
        self.assertEqual(detail["status"].iloc[0], "review")
        self.assertEqual(detail["message"].iloc[0], "adverse_slippage_exceeds_threshold")

    def test_write_execution_quality_reads_output_files(self):
        config = self.make_config("exec_quality_write", max_slippage=50)
        output_dir = Path(config["paths"]["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [{"date": "2026-06-17", "symbol": "000001", "side": "sell", "quantity": 100, "price": 9.98, "fee": 5, "amount": 998, "reason": "manual"}]
        ).to_csv(output_dir / "fill_import_trades.csv", index=False)
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "sell", "fill_quantity": 100, "order_quantity": 100, "matched": True, "message": "matched"}]).to_csv(
            output_dir / "fill_order_match.csv",
            index=False,
        )
        pd.DataFrame([{"date": "2026-06-17", "symbol": "000001", "side": "sell", "quantity": 100, "limit_price": 10.0}]).to_csv(output_dir / "rpa_orders.csv", index=False)

        paths = write_execution_quality(config)

        self.assertTrue(paths["execution_quality.csv"].exists())
        self.assertTrue(paths["execution_quality_detail.csv"].exists())
        summary = pd.read_csv(paths["execution_quality.csv"])
        detail = pd.read_csv(paths["execution_quality_detail.csv"], dtype={"symbol": str})
        self.assertTrue(summary["passed"].astype(bool).all())
        self.assertEqual(detail["symbol"].iloc[0], "000001")
        self.assertAlmostEqual(float(detail["adverse_slippage_bps"].iloc[0]), 20.0)

    def make_config(self, name: str, max_slippage: float) -> dict:
        root = Path.cwd() / "test_outputs" / name
        return {
            "paths": {"output_dir": str(root / "outputs")},
            "execution": {"max_fill_adverse_slippage_bps": max_slippage},
        }


if __name__ == "__main__":
    unittest.main()
