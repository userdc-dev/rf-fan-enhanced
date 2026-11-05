import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "migration",
    Path(__file__).parents[1] / "custom_components" / "rf_remote" / "migration.py",
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class LegacyDomainMigration(unittest.TestCase):
    def test_v1_custom_buttons_are_converted_without_losing_commands(self):
        original = {
            "commands": {"custom_movie": {"timings": [100, -200]}},
            "customs": [{"key": "custom_movie", "name": "Movie"}],
            "name": "Lounge remote",
        }
        migrated = migration.migrate_legacy_data(original, 1)
        self.assertNotIn("customs", migrated)
        self.assertEqual(
            migrated["sub_entities"],
            [{"entity_type": "button", "name": "Movie", "key_prefix": "custom_movie"}],
        )
        self.assertEqual(migrated["commands"], original["commands"])
        self.assertIn("customs", original)

    def test_v1_custom_button_without_a_saved_command_is_not_created(self):
        migrated = migration.migrate_legacy_data(
            {"commands": {}, "customs": [{"key": "missing", "name": "Missing"}]},
            1,
        )
        self.assertEqual(migrated["sub_entities"], [])

    def test_v2_data_and_learned_commands_are_preserved(self):
        original = {
            "commands": {"speed_1": {"timings": [120, -240], "b64": "abc"}},
            "sub_entities": [{"entity_type": "fan", "name": "Bedroom", "key_prefix": "bedroom"}],
        }
        self.assertEqual(migration.migrate_legacy_data(original, 2), original)


if __name__ == "__main__":
    unittest.main()
