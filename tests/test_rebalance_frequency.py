from copy import deepcopy
import unittest

from ashare_quant.rebalance_frequency import comparable_config_sha256


class RebalanceFrequencyResearchTests(unittest.TestCase):
    def test_comparable_identity_ignores_only_name_and_frequency(self):
        monthly = {
            "backtest": {"start": "2023-01-01", "initial_cash": 600000, "rebalance": "monthly"},
            "strategy": {"name": "monthly", "top_n": 100, "weights": {"liquidity": 1.0}},
            "trading": {"lot_size": 100},
        }
        quarterly = deepcopy(monthly)
        quarterly["backtest"]["initial_cash"] = 600000.0
        quarterly["backtest"]["rebalance"] = "quarterly"
        quarterly["strategy"]["name"] = "quarterly"

        self.assertEqual(comparable_config_sha256(monthly), comparable_config_sha256(quarterly))

        quarterly["strategy"]["top_n"] = 80
        self.assertNotEqual(comparable_config_sha256(monthly), comparable_config_sha256(quarterly))


if __name__ == "__main__":
    unittest.main()
