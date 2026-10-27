"""PIANO-PROD-001 tests: Application lifecycle integration.

The identity is loaded exactly once in ``Application.start()`` (after
logging, before the API), startup NEVER generates an identity, and any
identity failure is auxiliary: musical startup and the HF2 health
semantics are unchanged.
"""

import os
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from piano_core.app import Application
from piano_core.config import AppConfig
from piano_core.device_identity import (
    RECORD_NAME,
    initialize_identity,
    load_identity,
)
from piano_core.health import HealthState
from piano_core.midi.events import note_on as input_note_on
from piano_core.state import StateStore


def _make_identity_dir(tmp_path: Path) -> str:
    directory = tmp_path / "identity"
    directory.mkdir()
    os.chmod(directory, 0o700)
    return str(directory)


def _write_record(directory: str, data: bytes) -> None:
    fd = os.open(
        os.path.join(directory, RECORD_NAME),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(os.path.join(directory, RECORD_NAME), 0o600)


class ApplicationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def _app(self, **config) -> Application:
        return Application(AppConfig(**config))

    def test_unconfigured_identity_leaves_startup_unchanged(self):
        app = self._app()
        app.start()
        try:
            self.assertIsNone(app.device_identity)
            self.assertEqual(app.device_identity_error, "identity_unconfigured")
            self.assertIs(app.health.state, HealthState.READY)
        finally:
            app.shutdown()
        self.assertIs(app.health.state, HealthState.STOPPED)

    def test_valid_identity_loaded(self):
        directory = _make_identity_dir(self.tmp_path)
        expected = initialize_identity(directory)
        app = self._app(device_identity_dir=directory)
        app.start()
        try:
            self.assertEqual(app.device_identity, expected)
            self.assertIsNone(app.device_identity_error)
            self.assertIs(app.health.state, HealthState.READY)
        finally:
            app.shutdown()

    def test_startup_never_generates_identity(self):
        directory = _make_identity_dir(self.tmp_path)
        app = self._app(device_identity_dir=directory)
        app.start()
        app.shutdown()
        self.assertIsNone(app.device_identity)
        self.assertEqual(app.device_identity_error, "identity_missing")
        self.assertEqual(os.listdir(directory), [])

    def test_missing_directory_degrades_identity_only(self):
        absent = str(self.tmp_path / "absent")
        app = self._app(device_identity_dir=absent)
        app.start()
        try:
            self.assertIsNone(app.device_identity)
            self.assertEqual(
                app.device_identity_error, "identity_directory_missing"
            )
            self.assertIs(app.health.state, HealthState.READY)
        finally:
            app.shutdown()

    def test_corrupt_identity_keeps_musical_startup_operational(self):
        directory = _make_identity_dir(self.tmp_path)
        _write_record(directory, b"{corrupt")
        app = self._app(device_identity_dir=directory)
        app.start()
        try:
            self.assertIsNone(app.device_identity)
            self.assertEqual(app.device_identity_error, "identity_invalid_record")
            self.assertIs(app.health.state, HealthState.READY)
            # The canonical StateStore keeps reducing physical input.
            revision_before = app.state_store.snapshot().revision
            app.state_store.apply_event(input_note_on(1, 60, 90, 1.0))
            snapshot = app.state_store.snapshot()
            self.assertEqual(snapshot.pressed_notes, frozenset({60}))
            self.assertEqual(snapshot.revision, revision_before + 1)
        finally:
            app.shutdown()

    def test_identity_failure_does_not_alter_hf2_health_semantics(self):
        """A corrupt identity with an ENABLED, startable API is still a
        fully READY appliance (identity is auxiliary; only an
        ENABLED-but-FAILED API yields DEGRADED)."""
        directory = _make_identity_dir(self.tmp_path)
        _write_record(directory, b"{corrupt")
        app = self._app(
            device_identity_dir=directory, api_enabled=True, api_port=0
        )
        app.start()
        try:
            self.assertIs(app.health.state, HealthState.READY)
            self.assertIsNotNone(app.api_server)
            host, port = app.api_server.bound_address
            with urllib.request.urlopen(
                f"http://{host}:{port}/api/v1/health", timeout=10
            ) as response:
                self.assertEqual(response.status, 200)
        finally:
            app.shutdown()

    def test_identity_loaded_exactly_once(self):
        directory = _make_identity_dir(self.tmp_path)
        initialize_identity(directory)
        app = self._app(device_identity_dir=directory)
        real_load = load_identity
        with mock.patch(
            "piano_core.app.load_identity", wraps=real_load
        ) as spy:
            app.start()
            app.shutdown()
        self.assertEqual(spy.call_count, 1)

    def test_rollback_simulation_identity_bytes_untouched(self):
        """Configured start/unset (pre-PROD-001-style) start never
        modifies the persisted identity bytes."""
        directory = _make_identity_dir(self.tmp_path)
        identity = initialize_identity(directory)
        before = Path(directory, RECORD_NAME).read_bytes()
        app = self._app(device_identity_dir=directory)
        app.start()
        app.shutdown()
        self.assertEqual(Path(directory, RECORD_NAME).read_bytes(), before)
        # Rollback simulation: the variable removed -> pre-PROD-001
        # behavior (no identity I/O); PROD-001 returns -> same identity.
        app = self._app()
        app.start()
        app.shutdown()
        self.assertIsNone(app.device_identity)
        self.assertEqual(Path(directory, RECORD_NAME).read_bytes(), before)
        app = self._app(device_identity_dir=directory)
        app.start()
        try:
            self.assertEqual(app.device_identity, identity)
        finally:
            app.shutdown()


if __name__ == "__main__":
    unittest.main()
