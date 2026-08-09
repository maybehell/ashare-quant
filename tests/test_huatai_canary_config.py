from hashlib import sha256
from pathlib import Path
import unittest

from ashare_quant.config import load_config


class HuataiCanaryConfigTests(unittest.TestCase):
    def test_canary_config_loads_without_bom_and_remains_fail_closed(self):
        path = Path.cwd() / "configs" / "huatai_desktop_canary.yaml"
        self.assertNotIn(b"\xef\xbb\xbf", path.read_bytes())
        config = load_config(path)
        auto = config["auto_live"]
        self.assertEqual(config["paths"]["output_dir"], "outputs_live")
        self.assertEqual(config["paths"]["state_db"], "data/huatai_live_state.sqlite3")
        self.assertNotEqual(config["paths"]["state_db"], "data/state.sqlite3")
        self.assertTrue(auto["emergency_stop"])
        self.assertFalse(auto["allow_broker_submit"])
        self.assertFalse(auto["service"]["enabled"])
        self.assertFalse(auto["service"]["allow_submit"])
        self.assertTrue(auto["action_confirmation"]["required_before_submit"])
        self.assertEqual(
            auto["command_adapter"]["command"][-1],
            "configs/huatai_bridge_desktop_canary.yaml",
        )

    def test_all_distributed_huatai_configs_default_to_fail_closed(self):
        config_paths = (
            "configs/huatai_auto_live_template.yaml",
            "configs/huatai_desktop_read_only.yaml",
            "configs/huatai_desktop_canary.yaml",
        )
        for config_path in config_paths:
            with self.subTest(config=config_path):
                loaded = load_config(config_path)
                auto = loaded["auto_live"]
                if "desktop" in config_path:
                    self.assertEqual(loaded["paths"]["state_db"], "data/huatai_live_state.sqlite3")
                    self.assertTrue(auto["require_strategy_evidence_sync"])
                self.assertTrue(auto["emergency_stop"])
                self.assertFalse(auto["allow_broker_submit"])
                self.assertFalse(auto["service"]["enabled"])
                self.assertFalse(auto["service"]["allow_submit"])

    def test_huatai_config_pins_match_reviewed_files(self):
        config_paths = (
            "configs/huatai_auto_live_template.yaml",
            "configs/huatai_desktop_read_only.yaml",
            "configs/huatai_desktop_canary.yaml",
        )
        for config_path in config_paths:
            with self.subTest(config=config_path):
                config = load_config(config_path)
                pinned = config["auto_live"]["command_adapter"]["pinned_files"]
                required_paths = set(config["auto_live"]["command_adapter"].get("required_pinned_paths", []))
                self.assertTrue(config["auto_live"]["command_adapter"].get("require_complete_pin_manifest"))
                self.assertGreaterEqual(len(pinned), 9)
                self.assertEqual(required_paths, {item["path"] for item in pinned})
                for item in pinned:
                    with self.subTest(config=config_path, file=item["path"]):
                        actual = sha256(Path(item["path"]).read_bytes()).hexdigest()
                        self.assertEqual(item["sha256"], actual)


if __name__ == "__main__":
    unittest.main()
