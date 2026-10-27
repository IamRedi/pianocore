"""V2 tests: appliance deployment contract (service unit + env example).

Durable contract checks — the deployment artifacts must stay in sync with
the application configuration surface. These are invariant checks, not
text snapshots.
"""

import unittest
from pathlib import Path

from piano_core import config as config_module

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVICE = _REPO_ROOT / "ops" / "systemd" / "piano-core.service"
_ENV_EXAMPLE = _REPO_ROOT / "ops" / "systemd" / "piano-core.env.example"

# The documented appliance layout (docs/operations/APPLIANCE_BASELINE.md).
_EXPECTED_EXEC_START = "/opt/piano-core/venv/bin/piano-core"
_EXPECTED_ENV_FILE = "/etc/piano-core/piano-core.env"
_EXPECTED_SERVICE_USER = "piano-core"


def _parse_unit(path: Path) -> dict:
    """Minimal systemd unit parser: section -> {key: value} (last wins)."""
    parsed: dict[str, dict[str, str]] = {}
    section = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            parsed.setdefault(section, {})
            continue
        if section is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[section][key.strip()] = value.strip()
    return parsed


class ServiceUnitTests(unittest.TestCase):
    """The systemd contract must express the appliance service."""

    def setUp(self):
        self.unit = _parse_unit(_SERVICE)

    def test_required_sections_present(self):
        for section in ("Unit", "Service", "Install"):
            self.assertIn(section, self.unit)

    def test_description_present(self):
        self.assertTrue(self.unit["Unit"].get("Description", "").strip())

    def test_exec_start_matches_documented_layout(self):
        self.assertEqual(
            self.unit["Service"].get("ExecStart"), _EXPECTED_EXEC_START
        )

    def test_environment_file_matches_documented_layout(self):
        self.assertEqual(
            self.unit["Service"].get("EnvironmentFile"), _EXPECTED_ENV_FILE
        )

    def test_dedicated_service_user_declared(self):
        """The dedicated user is part of the installation contract."""
        self.assertEqual(
            self.unit["Service"].get("User"), _EXPECTED_SERVICE_USER
        )

    def test_graceful_sigterm_shutdown_is_bounded(self):
        self.assertEqual(self.unit["Service"].get("KillSignal"), "SIGTERM")
        self.assertIn("TimeoutStopSec", self.unit["Service"])

    def test_restart_policy_bounded(self):
        self.assertEqual(self.unit["Service"].get("Restart"), "on-failure")
        self.assertRegex(self.unit["Service"].get("RestartSec", ""), r"^\d+s$")
        self.assertIn("StartLimitIntervalSec", self.unit["Unit"])
        self.assertIn("StartLimitBurst", self.unit["Unit"])

    def test_no_shell_wrapper(self):
        exec_start = self.unit["Service"].get("ExecStart", "")
        self.assertFalse(
            any(token in exec_start for token in ("sh ", "bash ", "/bin/sh")),
            "ExecStart must not wrap the runtime in a shell",
        )

    def test_enabled_in_multi_user(self):
        self.assertIn(
            "multi-user.target", self.unit["Install"].get("WantedBy", "")
        )


class EnvExampleTests(unittest.TestCase):
    """The env example may only expose the AppConfig configuration surface."""

    def setUp(self):
        self.lines = [
            line.strip()
            for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_only_appconfig_supported_variables(self):
        supported = {f"{config_module.ENV_PREFIX}{key.upper()}"
                     for key in config_module.KNOWN_KEYS}
        for line in self.lines:
            key = line.partition("=")[0].strip()
            self.assertTrue(key.startswith(config_module.ENV_PREFIX), key)
            self.assertIn(key, supported, f"unsupported variable: {key}")

    def test_example_values_are_accepted_by_appconfig(self):
        """Every populated variable must survive AppConfig validation."""
        data = {}
        for line in self.lines:
            key, _, value = line.partition("=")
            data[key.strip()] = value.strip()
        cfg = config_module.AppConfig.from_env(data)
        self.assertEqual(cfg.mode, config_module.AppMode.PRODUCTION)
        self.assertEqual(cfg.log_level, config_module.LogLevel.INFO)

    def test_no_secrets_in_example(self):
        """Active settings must not contain secret-like keys or values
        (comments are documentation and are not scanned)."""
        banned = ("password", "secret", "token", "api_key", "apikey")
        for line in self.lines:
            lowered = line.lower()
            for word in banned:
                self.assertNotIn(word, lowered, line)

    def test_invalid_example_value_would_fail_closed(self):
        """Guard the fail-closed contract the env example relies on."""
        with self.assertRaises(config_module.ConfigError):
            config_module.AppConfig.from_env({"PIANO_CORE_MODE": "carrier"})


if __name__ == "__main__":
    unittest.main()
