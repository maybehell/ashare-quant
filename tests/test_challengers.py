import unittest

from ashare_quant.challengers import comparison_candidate_config_paths, primary_challenger_config_paths


class ChallengerRegistryTests(unittest.TestCase):
    def test_registered_set_takes_precedence_and_is_deduplicated(self):
        config = {
            "validation": {
                "primary_challenger_config": "configs/legacy.yaml",
                "primary_challenger_configs": [
                    "configs/a.yaml",
                    "configs/b.yaml",
                    "configs/a.yaml",
                    "",
                ],
            }
        }

        self.assertEqual(
            primary_challenger_config_paths(config),
            ["configs/a.yaml", "configs/b.yaml"],
        )

    def test_legacy_single_challenger_remains_supported(self):
        config = {"validation": {"primary_challenger_config": "configs/legacy.yaml"}}

        self.assertEqual(
            primary_challenger_config_paths(config),
            ["configs/legacy.yaml"],
        )

    def test_comparison_candidates_are_independent_and_deduplicated(self):
        config = {
            "validation": {
                "primary_challenger_configs": ["configs/primary.yaml"],
                "comparison_candidate_configs": [
                    "configs/primary.yaml",
                    "configs/archive.yaml",
                    "configs/archive.yaml",
                    "",
                ],
            }
        }

        self.assertEqual(
            comparison_candidate_config_paths(config),
            ["configs/primary.yaml", "configs/archive.yaml"],
        )


if __name__ == "__main__":
    unittest.main()
