"""FR-01 selected-port open race regressions (canonical RtMidi exception)."""

import os
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from rtmidi import InvalidPortError

from piano_core.midi.input import ConnectionState, MidiInputAdapter
from tests.test_fr01_recovery import Backend, RetryGate
from tests.test_midi_input import msg


class OpenPortRaceTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.received = queue.Queue()
        self.states = queue.Queue()
        self.adapter = MidiInputAdapter(
            self.backend,
            event_callback=self.received.put,
            on_connection_state=self.states.put,
            reconnect_delay=0.05,
            poll_interval=0.001,
            port_check_interval=60,
        )
        self.gate = RetryGate(0.05)
        self.adapter._stop_requested = self.gate
        self.addCleanup(self.adapter.stop)

    def _state(self, expected):
        self.assertIs(self.states.get(timeout=2), expected)

    def _wait_for_retry(self):
        self.assertEqual(self.gate.waits.get(timeout=2), 0.05)
        self.assertTrue(self.adapter._thread.is_alive())
        self.assertIsNone(self.adapter.last_error)

    def _new_connection(self):
        handle = self.backend.opened.get(timeout=2)
        self._state(ConnectionState.CONNECTING)
        self._state(ConnectionState.CONNECTED)
        return handle

    def test_real_invalid_port_open_race_recovers_and_delivers_once(self):
        self.backend.open_errors.put(InvalidPortError("selected port vanished"))
        self.adapter.start()
        self._state(ConnectionState.CONNECTING)
        self._state(ConnectionState.DISCONNECTED)
        self._wait_for_retry()
        self.assertEqual(self.backend.handles, [])
        self.gate.release()
        handle = self._new_connection()
        handle.pending.put(msg("note_on", note=65))
        self.assertEqual(self.received.get(timeout=2).note, 65)
        self.adapter.stop()
        self.assertEqual(self.adapter.events_received, 1)
        self.assertTrue(self.received.empty())
        self.assertEqual(handle.close_count, 1)
        self.assertEqual(len(self.backend.reader_threads), 1)

    def test_repeated_invalid_port_races_have_bounded_retries_and_one_reader(self):
        attempts = []
        original_open = self.backend.open_input

        def traced_open(port):
            attempts.append(port)
            return original_open(port)

        self.backend.open_input = traced_open
        for index in range(4):
            self.backend.open_errors.put(InvalidPortError(f"port race {index}"))
        self.adapter.start()
        for index in range(4):
            self._state(ConnectionState.CONNECTING)
            self._state(ConnectionState.DISCONNECTED)
            self._wait_for_retry()
            self.assertEqual(len(attempts), index + 1)
            self.assertEqual(self.backend.handles, [])
            self.gate.release()
        handle = self._new_connection()
        self.assertEqual(len(attempts), 5)
        self.assertEqual(set(attempts), {"Roland test"})
        handle.pending.put(msg("note_on", note=67))
        self.assertEqual(self.received.get(timeout=2).note, 67)
        self.adapter.stop()
        self.assertEqual(self.backend.max_readers, 1)
        self.assertEqual(len(self.backend.reader_threads), 1)
        self.assertEqual(len(self.backend.handles), 1)
        self.assertEqual(handle.close_count, 1)
        self.assertEqual(self.adapter.events_received, 1)

    def test_read_fault_then_open_race_then_fresh_input(self):
        self.adapter.start()
        first = self._new_connection()
        first.pending.put(msg("note_on", note=60))
        self.assertEqual(self.received.get(timeout=2).note, 60)
        self.backend.open_errors.put(InvalidPortError("port vanished on reconnect"))
        first.pending.put(OSError("read transport fault"))
        self.assertTrue(first.closed.wait(2))
        self._state(ConnectionState.DISCONNECTED)
        self._wait_for_retry()
        first.pending.put(msg("note_on", note=99))  # old input is stale.
        self.gate.release()
        self._state(ConnectionState.CONNECTING)
        self._state(ConnectionState.DISCONNECTED)
        self._wait_for_retry()
        self.assertEqual(len(self.backend.handles), 1)
        self.gate.release()
        second = self._new_connection()
        second.pending.put(msg("note_on", note=65))
        self.assertEqual(self.received.get(timeout=2).note, 65)
        self.adapter.stop()
        self.assertTrue(self.received.empty())
        self.assertEqual(self.adapter.events_received, 2)
        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)
        self.assertEqual(self.backend.max_readers, 1)

    def test_unrelated_valueerror_during_open_remains_fatal(self):
        self.backend.open_errors.put(ValueError("invalid backend configuration"))
        with self.assertLogs("piano_core.midi.input", level="ERROR"):
            self.adapter.start()
            self.adapter._thread.join(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertEqual(self.adapter.state, ConnectionState.DISCONNECTED)
        self.assertIn("invalid backend configuration", self.adapter.last_error)
        self.assertTrue(self.gate.waits.empty())
        self.assertEqual(self.backend.handles, [])

    def test_invalid_port_exception_from_callback_is_not_open_recovery(self):
        def faulty_consumer(_event):
            raise InvalidPortError("consumer defect")

        self.adapter._event_callback = faulty_consumer
        self.adapter.start()
        handle = self._new_connection()
        with self.assertLogs("piano_core.midi.input", level="ERROR"):
            handle.pending.put(msg("note_on"))
            self.assertTrue(handle.closed.wait(2))
            self.adapter._thread.join(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertIn("consumer defect", self.adapter.last_error)
        self.assertTrue(self.gate.waits.empty())
        self.assertEqual(len(self.backend.handles), 1)

    def test_stop_before_invalid_port_open_returns_starts_no_retry(self):
        entered, release = threading.Event(), threading.Event()
        self.backend.open_entered = entered
        self.backend.open_release = release
        self.addCleanup(release.set)
        self.backend.open_errors.put(InvalidPortError("port vanished during open"))
        attempts = []
        original_open = self.backend.open_input

        def traced_open(port):
            attempts.append(port)
            return original_open(port)

        self.backend.open_input = traced_open
        self.adapter.start()
        self.assertTrue(entered.wait(2))
        self.adapter.stop(0.01)
        self.assertTrue(self.adapter._thread.is_alive())
        release.set()
        self.adapter.stop(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertEqual(attempts, ["Roland test"])
        self.assertEqual(self.backend.handles, [])
        self.assertNotIn(ConnectionState.CONNECTED, list(self.states.queue))

    def test_successful_open_after_stop_closes_late_handle(self):
        entered, release = threading.Event(), threading.Event()
        self.backend.open_entered = entered
        self.backend.open_release = release
        self.addCleanup(release.set)
        self.adapter.start()
        self.assertTrue(entered.wait(2))
        self.adapter.stop(0.01)
        release.set()
        self.adapter.stop(1)
        self.assertFalse(self.adapter._thread.is_alive())
        self.assertEqual(len(self.backend.handles), 1)
        self.assertEqual(self.backend.handles[0].close_count, 1)
        self.assertNotIn(ConnectionState.CONNECTED, list(self.states.queue))
        self.assertTrue(self.received.empty())

    def test_adapter_import_and_start_without_optional_rtmidi(self):
        # -S excludes site-packages. The critical tests above still require
        # the real canonical class; this separate process proves lazy import.
        source = Path(__file__).resolve().parents[1] / "src"
        script = """
import sys, threading
assert 'rtmidi' not in sys.modules
from piano_core.midi.input import MidiInputAdapter
assert 'rtmidi' not in sys.modules
class Backend:
    def __init__(self): self.opened = threading.Event()
    def list_input_ports(self): return ['Roland test']
    def open_input(self, port): self.opened.set(); return object()
    def iter_pending(self, handle): return iter(())
backend = Backend()
adapter = MidiInputAdapter(backend, reconnect_delay=0.05)
adapter.start()
assert backend.opened.wait(2)
adapter.stop()
assert not adapter._thread.is_alive()
assert 'rtmidi' not in sys.modules
class BrokenBackend(Backend):
    def open_input(self, port): raise ValueError('invalid optional-backend configuration')
broken = MidiInputAdapter(BrokenBackend(), reconnect_delay=0.05)
broken.start()
broken._thread.join(1)
assert not broken._thread.is_alive()
assert 'invalid optional-backend configuration' in broken.last_error
assert 'rtmidi' not in sys.modules
"""
        env = dict(os.environ, PYTHONPATH=str(source))
        result = subprocess.run(
            [sys.executable, "-S", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
