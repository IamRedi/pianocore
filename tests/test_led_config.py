"""PIANO-P014: LED configuration tests (fail-closed validation)."""

import unittest

from piano_core.config import AppConfig, ConfigError
from piano_core.led.model import LedMode


class LedConfigTests(unittest.TestCase):
    def test_safe_defaults(self):
        config = AppConfig.from_mapping({})
        self.assertFalse(config.led_enabled)  # safe default: DISABLED
        self.assertEqual(config.led_ws_host, "127.0.0.1")
        self.assertEqual(config.led_ws_port, 8787)
        self.assertIs(config.led_mode, LedMode.PLAYBACK)
        self.assertEqual(config.led_brightness, 100)

    def test_env_roundtrip(self):
        config = AppConfig.from_env(
            {
                "PIANO_CORE_LED_ENABLED": "true",
                "PIANO_CORE_LED_WS_HOST": "192.168.1.50",
                "PIANO_CORE_LED_WS_PORT": "8787",
                "LED_MODE": "all",  # non-prefixed: ignored
            }
        )
        self.assertTrue(config.led_enabled)
        self.assertEqual(config.led_ws_host, "192.168.1.50")
        self.assertEqual(config.led_ws_port, 8787)
        self.assertIs(config.led_mode, LedMode.PLAYBACK)

    def test_modes_parse_case_insensitively(self):
        for raw, expected in (
            ("off", LedMode.OFF),
            ("PLAYBACK", LedMode.PLAYBACK),
            ("Physical", LedMode.PHYSICAL),
            ("all", LedMode.ALL),
        ):
            config = AppConfig.from_mapping({"led_mode": raw})
            self.assertIs(config.led_mode, expected)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"led_mode": "disco"})

    def test_brightness_bounds(self):
        for value in (0, 50, 100, "0", "100"):
            config = AppConfig.from_mapping({"led_brightness": value})
            self.assertEqual(config.led_brightness, int(value))
        for value in (-1, 101, "101", "1.5", True, "high"):
            with self.assertRaises(ConfigError):
                AppConfig.from_mapping({"led_brightness": value})

    def test_bind_and_port_validation(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"led_ws_host": "led.local"})
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"led_ws_host": "::1"})
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"led_ws_port": "0"})
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"led_ws_port": 70000})
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"led_enabled": "maybe"})

    def test_network_independent_host_accepted(self):
        """PIANO-P015-HF2: 0.0.0.0 (all interfaces) is the appliance
        bind value — network-independent, cold-boot-safe."""
        config = AppConfig.from_mapping({"led_ws_host": "0.0.0.0"})
        self.assertEqual(config.led_ws_host, "0.0.0.0")

    def test_unknown_led_env_rejected(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_env({"PIANO_CORE_LED_BAUD": "115200"})


if __name__ == "__main__":
    unittest.main()
