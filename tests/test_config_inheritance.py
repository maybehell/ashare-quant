from pathlib import Path
import unittest
from unittest.mock import patch

from ashare_quant.config import load_config


class ConfigInheritanceTests(unittest.TestCase):
    def test_relative_parent_config_is_deep_merged(self):
        def mapping(path):
            name = Path(path).name
            if name == "base.yaml":
                return {"strategy": {"name": "base", "top_n": 3}, "backtest": {"rebalance": "monthly"}}
            if name == "child.yaml":
                return {"extends": "base.yaml", "strategy": {"name": "child"}, "backtest": {"rebalance": "quarterly"}}
            return {}

        with patch("ashare_quant.config._load_mapping", side_effect=mapping):
            config = load_config(Path("configs") / "child.yaml")

        self.assertEqual(config["strategy"]["name"], "child")
        self.assertEqual(config["strategy"]["top_n"], 3)
        self.assertEqual(config["backtest"]["rebalance"], "quarterly")
        self.assertNotIn("extends", config)

    def test_parent_cycle_fails_closed(self):
        def mapping(path):
            return {"extends": "b.yaml"} if Path(path).name == "a.yaml" else {"extends": "a.yaml"}

        with patch("ashare_quant.config._load_mapping", side_effect=mapping):
            with self.assertRaisesRegex(ValueError, "cycle"):
                load_config(Path("configs") / "a.yaml")


if __name__ == "__main__":
    unittest.main()