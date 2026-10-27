"""V2 tests: typed configuration foundation (defaults, validation, env)."""

import dataclasses
import logging
import unittest

from piano_core import config as config_module
from piano_core.config import (
    ENV_PREFIX,
    AppMode,
    AppConfig,
    ConfigError,
    LogLevel,
)


class DefaultsTests(unittest.TestCase):
    def test_explicit_defaults(self):
        cfg = AppConfig()
        self.assertEqual(cfg.mode, AppMode.DEVELOPMENT)
        self.assertEqual(cfg.log_level, LogLevel.INFO)

    def test_config_is_frozen(self):
        cfg = AppConfig()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.mode = AppMode.PRODUCTION

    def test_logging_level_mapping(self):
        self.assertEqual(AppConfig().logging_level(), logging.INFO)
        self.assertEqual(
            LogLevel.WARNING.to_logging_level(), logging.WARNING
        )


class FromMappingTests(unittest.TestCase):
    def test_valid_values_case_insensitive(self):
        cfg = AppConfig.from_mapping(
            {"mode": "PRODUCTION", "log_level": " debug "}
        )
        self.assertEqual(cfg.mode, AppMode.PRODUCTION)
        self.assertEqual(cfg.log_level, LogLevel.DEBUG)

    def test_partial_mapping_uses_defaults(self):
        cfg = AppConfig.from_mapping({"mode": "test"})
        self.assertEqual(cfg.mode, AppMode.TEST)
        self.assertEqual(cfg.log_level, LogLevel.INFO)

    def test_empty_mapping_gives_defaults(self):
        cfg = AppConfig.from_mapping({})
        self.assertEqual(cfg, AppConfig())

    def test_invalid_mode_fails_closed(self):
        with self.assertRaises(ConfigError) as ctx:
            AppConfig.from_mapping({"mode": "staging"})
        self.assertIn("mode", str(ctx.exception))

    def test_invalid_log_level_fails_closed(self):
        with self.assertRaises(ConfigError) as ctx:
            AppConfig.from_mapping({"log_level": "verbose"})
        self.assertIn("log_level", str(ctx.exception))

    def test_non_string_value_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"mode": 3})

    def test_unknown_key_fails_closed(self):
        with self.assertRaises(ConfigError) as ctx:
            AppConfig.from_mapping({"midi_port": "pluto"})
        self.assertIn("midi_port", str(ctx.exception))

    def test_non_mapping_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping(["mode=production"])

    def test_enum_members_rejected_in_raw_mapping(self):
        """from_mapping is the strict raw-input path; typed members belong
        to direct construction (AppConfig(mode=AppMode.PRODUCTION))."""
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"mode": AppMode.PRODUCTION})


class MidiInputConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        cfg = AppConfig()
        self.assertFalse(cfg.midi_input_enabled)

    def test_enabled_parsed_case_insensitive(self):
        cfg = AppConfig.from_mapping({"midi_input_enabled": "True"})
        self.assertTrue(cfg.midi_input_enabled)

    def test_invalid_boolean_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"midi_input_enabled": "yes"})

    def test_port_matcher_default_and_parsing(self):
        self.assertEqual(AppConfig().midi_input_port_matcher, "roland")
        cfg = AppConfig.from_mapping({"midi_input_port_matcher": " Roland "})
        self.assertEqual(cfg.midi_input_port_matcher, "Roland")

    def test_empty_matcher_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"midi_input_port_matcher": "  "})

    def test_non_string_matcher_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"midi_input_port_matcher": 28})

    def test_midi_env_vars_parsed(self):
        cfg = AppConfig.from_env(
            {
                "PIANO_CORE_MIDI_INPUT_ENABLED": "true",
                "PIANO_CORE_MIDI_INPUT_PORT_MATCHER": "roland",
            }
        )
        self.assertTrue(cfg.midi_input_enabled)
        self.assertEqual(cfg.midi_input_port_matcher, "roland")

    def test_unknown_midi_env_var_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_env({"PIANO_CORE_MIDI_OUTPUT": "true"})


class FromEnvTests(unittest.TestCase):
    def test_prefixed_vars_are_read(self):
        cfg = AppConfig.from_env(
            {"PIANO_CORE_MODE": "production", "PIANO_CORE_LOG_LEVEL": "ERROR"}
        )
        self.assertEqual(cfg.mode, AppMode.PRODUCTION)
        self.assertEqual(cfg.log_level, LogLevel.ERROR)

    def test_non_prefixed_vars_ignored(self):
        cfg = AppConfig.from_env({"HOME": "/home/dev", "PATH": "/usr/bin"})
        self.assertEqual(cfg, AppConfig())

    def test_unknown_piano_core_var_fails_closed(self):
        with self.assertRaises(ConfigError) as ctx:
            AppConfig.from_env({"PIANO_CORE_LOGLEVEL": "debug"})
        self.assertIn("PIANO_CORE_LOGLEVEL", str(ctx.exception))

    def test_invalid_value_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_env({"PIANO_CORE_MODE": "games"})

    def test_empty_value_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_env({"PIANO_CORE_MODE": "  "})

    def test_prefix_constant(self):
        self.assertEqual(ENV_PREFIX, "PIANO_CORE_")


if __name__ == "__main__":
    unittest.main()
