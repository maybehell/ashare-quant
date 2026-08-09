import unittest

import pandas as pd

from ashare_quant.readiness import build_live_readiness


class BootstrapReadinessTests(unittest.TestCase):
    def readiness(self, bootstrap):
        config = {
            "live_guard": {
                "enabled": True,
                "require_excess_bootstrap": True,
                "min_bootstrap_probability_positive": 0.95,
                "min_bootstrap_lower_95_excess_return": 0.0,
                "require_industry_metadata": False,
                "require_listing_metadata": False,
            },
            "universe": {"scope": "curated"},
        }
        return build_live_readiness(
            config,
            {"excess_total_return": 0.2, "max_drawdown": -0.1, "annualized_turnover": 1.0},
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            excess_bootstrap=bootstrap,
        ).set_index("check")

    def test_weak_bootstrap_evidence_blocks_strict_live(self):
        bootstrap = pd.DataFrame(
            [
                {
                    "status": "ok",
                    "probability_excess_positive": 0.8255,
                    "lower_95_excess_return": -0.28,
                }
            ]
        )
        readiness = self.readiness(bootstrap)
        self.assertFalse(bool(readiness.loc["excess_bootstrap_probability_positive", "passed"]))
        self.assertFalse(bool(readiness.loc["excess_bootstrap_lower_95", "passed"]))

    def test_strong_bootstrap_evidence_passes_its_checks(self):
        bootstrap = pd.DataFrame(
            [
                {
                    "status": "ok",
                    "probability_excess_positive": 0.98,
                    "lower_95_excess_return": 0.03,
                }
            ]
        )
        readiness = self.readiness(bootstrap)
        checks = readiness.loc[
            [
                "excess_bootstrap_status",
                "excess_bootstrap_probability_positive",
                "excess_bootstrap_lower_95",
            ],
            "passed",
        ]
        self.assertTrue(checks.astype(bool).all())


if __name__ == "__main__":
    unittest.main()
