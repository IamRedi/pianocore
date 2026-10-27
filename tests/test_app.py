"""V2 tests: application lifecycle (startup, ready, stop, shutdown, logs)."""

import logging
import unittest

from piano_core import __version__
from piano_core.app import Application
from piano_core.config import AppConfig, ConfigError, AppMode, LogLevel
from piano_core.health import HealthState, InvalidHealthTransition


class ListHandler(logging.Handler):
    """Minimal in-memory log capture (no I/O, deterministic)."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.app = Application.from_env()
        self.capture = ListHandler()
        app_logger = logging.getLogger("piano_core")
        app_logger.addHandler(self.capture)
        self.addCleanup(app_logger.removeHandler, self.capture)

    def test_initial_health_is_starting(self):
        self.assertIs(self.app.health.state, HealthState.STARTING)

    def test_startup_reaches_ready_and_logs_identity(self):
        self.app.start()
        self.assertIs(self.app.health.state, HealthState.READY)
        joined = "\n".join(self.capture.messages)
        self.assertIn("startup", joined)
        self.assertIn("Piano Core", joined)
        self.assertIn(__version__, joined)
        self.assertIn("development", joined)
        self.assertIn("ready", joined)

    def test_second_start_raises(self):
        self.app.start()
        with self.assertRaises(InvalidHealthTransition):
            self.app.start()

    def test_run_without_stop_times_out_still_ready(self):
        self.app.start()
        requested = self.app.run(timeout=0.01)
        self.assertFalse(requested)
        self.assertIs(self.app.health.state, HealthState.READY)

    def test_request_stop_then_run_returns_true(self):
        self.app.start()
        self.app.request_stop("test")
        self.assertIs(self.app.health.state, HealthState.STOPPING)
        self.assertTrue(self.app.run(timeout=1))
        self.assertIn("shutdown request reason=test", self.capture.messages)

    def test_shutdown_reaches_stopped_and_logs(self):
        self.app.start()
        self.app.request_stop("test")
        self.app.shutdown()
        self.assertIs(self.app.health.state, HealthState.STOPPED)
        self.assertIn("stopped", self.capture.messages)

    def test_shutdown_is_idempotent(self):
        self.app.start()
        self.app.request_stop()
        self.app.shutdown()
        self.app.shutdown()
        self.assertIs(self.app.health.state, HealthState.STOPPED)

    def test_direct_shutdown_without_request_is_tolerated(self):
        self.app.start()
        self.app.shutdown()
        self.assertIs(self.app.health.state, HealthState.STOPPED)

    def test_request_stop_is_idempotent(self):
        self.app.start()
        self.app.request_stop("first")
        self.app.request_stop("second")
        self.assertIs(self.app.health.state, HealthState.STOPPING)
        self.assertEqual(
            sum(1 for m in self.capture.messages if "shutdown request" in m),
            1,
        )


class ConstructionTests(unittest.TestCase):
    def test_from_env_defaults(self):
        app = Application.from_env(
            {"PIANO_CORE_MODE": "production", "PIANO_CORE_LOG_LEVEL": "ERROR"}
        )
        self.assertIs(app.health.state, HealthState.STARTING)
        self.assertEqual(app.config.mode, AppMode.PRODUCTION)
        self.assertEqual(app.config.log_level, LogLevel.ERROR)

    def test_invalid_env_fails_closed_before_application_exists(self):
        with self.assertRaises(ConfigError):
            Application.from_env({"PIANO_CORE_MODE": "carrier"})

    def test_direct_construction_with_typed_config(self):
        app = Application(AppConfig())
        self.assertIs(app.health.state, HealthState.STARTING)

    def test_rejects_non_config(self):
        with self.assertRaises(TypeError):
            Application({"mode": "production"})


if __name__ == "__main__":
    unittest.main()
