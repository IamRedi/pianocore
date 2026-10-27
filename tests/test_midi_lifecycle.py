"""V2 tests: production MIDI lifecycle integration (PIANO-P005, F2).

Application-level tests with a scripted fake backend: no mido import, no
hardware, deterministic state-based assertions. Since PIANO-P006 the
application's canonical MIDI sink is the StateStore.
"""

import logging
import time
import unittest

from piano_core.app import Application
from piano_core.config import AppConfig
from piano_core.midi.input import ConnectionState
from piano_core.state import MidiConnectionState, PianoState
from tests.test_midi_input import FakeBackend, msg, wait_for


class ListHandler(logging.Handler):
    """Minimal in-memory log capture (no I/O, deterministic)."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class EnabledMidiTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend(["Roland Digital Piano MIDI 1 28:0"])
        self.factory_calls = []

        def factory():
            self.factory_calls.append(1)
            return self.backend

        self.app = Application(
            AppConfig(
                midi_input_enabled=True, midi_input_port_matcher="roland"
            ),
            midi_backend_factory=factory,
        )
        capture = ListHandler()
        capture.setLevel(logging.DEBUG)
        self.records = capture
        logger = logging.getLogger("piano_core")
        logger.addHandler(capture)
        self.addCleanup(logger.removeHandler, capture)
        self.addCleanup(self.app.shutdown)

    def test_enabled_starts_adapter_and_connects(self):
        self.app.start()
        adapter = self.app.midi_adapter
        self.assertIsNotNone(adapter)
        self.assertTrue(
            wait_for(lambda: adapter.state is ConnectionState.CONNECTED)
        )
        self.assertEqual(self.factory_calls, [1])

    def test_shutdown_stops_adapter_releases_thread_and_handle(self):
        self.app.start()
        adapter = self.app.midi_adapter
        self.assertTrue(
            wait_for(lambda: adapter.state is ConnectionState.CONNECTED)
        )
        self.app.shutdown()
        self.assertIs(adapter.state, ConnectionState.DISCONNECTED)
        self.assertFalse(adapter._thread.is_alive())
        self.assertTrue(all(handle.closed for handle in self.backend.handles))

    def test_events_reach_statestore_and_shutdown_reports_summary(self):
        self.app.start()
        self.backend.pending = [msg("note_on", note=60, velocity=88)]
        self.assertTrue(
            wait_for(
                lambda: 60 in self.app.state_store.snapshot().pressed_notes
            )
        )
        self.app.shutdown()
        self.assertEqual(self.app.health.state.value, "STOPPED")
        self.assertIn("note_on=1", self.app.state_store.event_counts_summary())

    def test_midi_receive_thread_terminates(self):
        self.app.start()
        thread = self.app.midi_adapter._thread  # noqa: SLF001 (test assertion)
        self.app.shutdown()
        self.assertTrue(
            wait_for(lambda: not thread.is_alive()), "reader thread must exit"
        )


class DisabledMidiTests(unittest.TestCase):
    """enabled=false must not touch the MIDI backend at all (review F5.1)."""

    def test_disabled_config_never_calls_backend_factory(self):
        calls = []

        def factory():
            calls.append(1)
            return FakeBackend(["Roland Digital Piano MIDI 1 28:0"])

        app = Application(
            AppConfig(midi_input_enabled=False), midi_backend_factory=factory
        )
        self.addCleanup(app.shutdown)
        app.start()
        self.assertEqual(calls, [])
        self.assertIsNone(app.midi_adapter)
        self.assertEqual(app.health.state.value, "READY")
        app.shutdown()
        self.assertEqual(calls, [])
        self.assertEqual(app.health.state.value, "STOPPED")

    def test_disabled_midi_still_exposes_valid_empty_statestore(self):
        """P006: no adapter, but the canonical store exists and stays empty."""
        app = Application(AppConfig(midi_input_enabled=False))
        self.addCleanup(app.shutdown)
        app.start()
        self.assertEqual(app.state_store.snapshot(), PianoState.initial())
        app.shutdown()
        self.assertEqual(app.state_store.snapshot(), PianoState.initial())


class StateStoreIntegrationTests(unittest.TestCase):
    """P006: adapter transitions and events land in the canonical store."""

    def setUp(self):
        self.backend = FakeBackend(["Roland Digital Piano MIDI 1 28:0"])
        self.app = Application(
            AppConfig(
                midi_input_enabled=True, midi_input_port_matcher="roland"
            ),
            midi_backend_factory=lambda: self.backend,
        )
        self.addCleanup(self.app.shutdown)

    def test_connection_transitions_reach_statestore(self):
        self.app.start()
        self.assertTrue(
            wait_for(
                lambda: self.app.state_store.snapshot().midi_connection
                is MidiConnectionState.CONNECTED
            )
        )
        self.app.shutdown()
        self.assertEqual(
            self.app.state_store.snapshot().midi_connection,
            MidiConnectionState.DISCONNECTED,
        )

    def test_note_and_sustain_events_reach_statestore(self):
        self.app.start()
        self.backend.pending = [
            msg("note_on", note=60, velocity=88),
            msg("control_change", control=64, value=100),
        ]
        self.assertTrue(
            wait_for(
                lambda: self.app.state_store.snapshot().sustain_pressed
            )
        )
        snapshot = self.app.state_store.snapshot()
        self.assertIn(60, snapshot.pressed_notes)
        self.assertEqual(snapshot.sustain_value, 100)
        self.assertGreaterEqual(snapshot.revision, 2)

    def test_shutdown_preserves_coherent_final_snapshot(self):
        self.app.start()
        self.backend.pending = [msg("note_on", note=62, velocity=70)]
        self.assertTrue(
            wait_for(
                lambda: 62 in self.app.state_store.snapshot().pressed_notes
            )
        )
        self.app.shutdown()
        final = self.app.state_store.snapshot()
        # Coherent: valid typed state, frozen, stable across repeated reads,
        # with the connection released and the revision frozen at shutdown.
        self.assertIsInstance(final, PianoState)
        self.assertEqual(final.midi_connection, MidiConnectionState.DISCONNECTED)
        self.assertEqual(final.revision, self.app.state_store.snapshot().revision)
        self.assertEqual(final, self.app.state_store.snapshot())
        self.assertEqual(final.pressed_notes, frozenset())
        self.assertFalse(final.sustain_pressed)


class FromEnvFactoryInjectionTests(unittest.TestCase):
    """from_env must preserve and use the injected backend factory (review F1)."""

    def test_from_env_preserves_and_uses_injected_factory(self):
        backend = FakeBackend(["Roland Digital Piano MIDI 1 28:0"])
        calls = []

        def factory():
            calls.append(1)
            return backend

        app = Application.from_env(
            {"PIANO_CORE_MIDI_INPUT_ENABLED": "true"},
            midi_backend_factory=factory,
        )
        self.addCleanup(app.shutdown)
        app.start()
        self.assertEqual(calls, [1])
        self.assertIsNotNone(app.midi_adapter)
        self.assertTrue(
            wait_for(lambda: app.midi_adapter.state is ConnectionState.CONNECTED)
        )
        # The injected fake backend (not MidoBackend) served the connection.
        self.assertEqual(backend.opened, ["Roland Digital Piano MIDI 1 28:0"])


class BackendErrorTests(unittest.TestCase):
    def test_backend_factory_error_keeps_application_healthy(self):
        def broken_factory():
            raise RuntimeError("no midi backend available")

        app = Application(
            AppConfig(midi_input_enabled=True), midi_backend_factory=broken_factory
        )
        app.start()
        self.addCleanup(app.shutdown)
        self.assertIsNone(app.midi_adapter)
        self.assertEqual(app.health.state.value, "READY")


class AmbiguityTests(unittest.TestCase):
    def test_ambiguous_endpoints_fail_closed_application_stays_healthy(self):
        backend = FakeBackend(["Roland One", "Roland Two"])
        app = Application(
            AppConfig(midi_input_enabled=True), midi_backend_factory=lambda: backend
        )
        app.start()
        self.addCleanup(app.shutdown)
        adapter = app.midi_adapter
        self.assertTrue(wait_for(lambda: adapter.last_error is not None))
        self.assertIs(adapter.state, ConnectionState.DISCONNECTED)
        self.assertEqual(backend.opened, [])
        self.assertEqual(app.health.state.value, "READY")


class LossReconnectTests(unittest.TestCase):
    def test_endpoint_loss_and_return_do_not_crash_application(self):
        backend = FakeBackend(["Roland Digital Piano MIDI 1 28:0"])
        app = Application(
            AppConfig(midi_input_enabled=True), midi_backend_factory=lambda: backend
        )
        app.start()
        self.addCleanup(app.shutdown)
        self.assertTrue(
            wait_for(lambda: app.midi_adapter.state is ConnectionState.CONNECTED)
        )
        backend.ports = []  # endpoint disappears
        self.assertTrue(
            wait_for(
                lambda: app.midi_adapter.state is ConnectionState.DISCONNECTED,
                timeout=3,
            )
        )
        self.assertEqual(app.health.state.value, "READY")
        backend.ports = ["Roland Digital Piano MIDI 1 28:0"]  # endpoint returns
        self.assertTrue(
            wait_for(
                lambda: app.midi_adapter.state is ConnectionState.CONNECTED,
                timeout=5,
            )
        )
        self.assertEqual(app.health.state.value, "READY")


if __name__ == "__main__":
    unittest.main()
