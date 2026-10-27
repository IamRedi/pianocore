"""V2 tests: API configuration (PIANO-P012).

Safe defaults (disabled + loopback), fail-closed validation of the new
API keys, and the strict env contract.
"""

import unittest

from piano_core.config import (
    KNOWN_ENV_VARS,
    KNOWN_KEYS,
    AppConfig,
    ConfigError,
)


class ApiConfigDefaultTests(unittest.TestCase):
    def test_safe_defaults(self):
        config = AppConfig()
        self.assertFalse(config.api_enabled)
        self.assertEqual(config.api_host, "127.0.0.1")
        self.assertEqual(config.api_port, 8788)

    def test_new_keys_are_known(self):
        for key in ("api_enabled", "api_host", "api_port"):
            self.assertIn(key, KNOWN_KEYS)
            self.assertIn(f"PIANO_CORE_{key.upper()}", KNOWN_ENV_VARS)

    def test_default_env_parses_disabled_loopback(self):
        config = AppConfig.from_env({})
        self.assertFalse(config.api_enabled)
        self.assertEqual(config.api_host, "127.0.0.1")
        self.assertEqual(config.api_port, 8788)


class ApiConfigValidTests(unittest.TestCase):
    def test_env_values_parse(self):
        config = AppConfig.from_env(
            {
                "PIANO_CORE_API_ENABLED": "true",
                "PIANO_CORE_API_HOST": "192.168.1.50",
                "PIANO_CORE_API_PORT": "8788",
            }
        )
        self.assertTrue(config.api_enabled)
        self.assertEqual(config.api_host, "192.168.1.50")
        self.assertEqual(config.api_port, 8788)

    def test_port_accepts_int_and_digit_string(self):
        self.assertEqual(AppConfig.from_mapping({"api_port": 8080}).api_port, 8080)
        self.assertEqual(AppConfig.from_mapping({"api_port": "1"}).api_port, 1)
        self.assertEqual(
            AppConfig.from_mapping({"api_port": " 65535 "}).api_port, 65535
        )

    def test_host_accepts_localhost_and_ipv4(self):
        self.assertEqual(
            AppConfig.from_mapping({"api_host": " localhost "}).api_host,
            "localhost",
        )
        self.assertEqual(
            AppConfig.from_mapping({"api_host": "0.0.0.0"}).api_host, "0.0.0.0"
        )

    def test_network_independent_bind_config_accepted(self):
        """PIANO-P015-HF2: the appliance binds 0.0.0.0 (all interfaces)
        for BOTH the API and the LED endpoint — the configuration surface
        accepts the network-independent bind explicitly."""
        config = AppConfig.from_env(
            {
                "PIANO_CORE_API_ENABLED": "true",
                "PIANO_CORE_API_HOST": "0.0.0.0",
                "PIANO_CORE_API_PORT": "8788",
                "PIANO_CORE_LED_ENABLED": "true",
                "PIANO_CORE_LED_WS_HOST": "0.0.0.0",
                "PIANO_CORE_LED_WS_PORT": "8787",
            }
        )
        self.assertEqual(config.api_host, "0.0.0.0")
        self.assertEqual(config.led_ws_host, "0.0.0.0")


class ApiConfigInvalidTests(unittest.TestCase):
    def test_invalid_ports_fail_closed(self):
        for raw in (0, -1, 65536, True, "abc", "8.5", "", None, [8080]):
            with self.assertRaises(ConfigError, msg=f"port {raw!r}"):
                AppConfig.from_mapping({"api_port": raw})

    def test_invalid_hosts_fail_closed(self):
        for raw in ("", "example.com", "pi.local", "::1", None, 123):
            with self.assertRaises(ConfigError, msg=f"host {raw!r}"):
                AppConfig.from_mapping({"api_host": raw})

    def test_invalid_enabled_flag_fails_closed(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_mapping({"api_enabled": "yes"})

    def test_unknown_env_var_still_rejected(self):
        with self.assertRaises(ConfigError):
            AppConfig.from_env({"PIANO_CORE_API_TIMEOUT": "5"})


if __name__ == "__main__":
    unittest.main()
