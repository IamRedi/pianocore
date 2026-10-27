"""V2 tests: MIDI input adapter (selection, lifecycle, normalization).

All tests run against a scripted fake backend: no mido import, no hardware,
no network. The receive thread is driven with small real sleeps but every
assertion is state-based, so the tests are deterministic.
"""

import threading
import time
import types
import unittest

from piano_core.midi.input import (
    AmbiguousEndpointError,
    ConnectionState,
    EndpointNotFoundError,
    MidiInputAdapter,
    normalize_message,
    select_input_port,
)


def wait_for(predicate, timeout=2.0):
    """Poll a predicate on the main thread with a deadline."""
    deadline = threading.Event()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        deadline.wait(0.005)
    return predicate()


class FakeHandle:
    def __init__(self, backend, port):
        self.backend = backend
        self.port = port
        self.closed = False

    def iter_pending(self):
        while self.backend.pending:
            item = self.backend.pending.pop(0)
            if isinstance(item, Exception):
                raise item
            yield item

    def close(self):
        self.closed = True


class FakeBackend:
    """Scripted input-only backend (no mido, no hardware)."""

    def __init__(self, ports, pending=None):
        self.ports = list(ports)
        self.pending = list(pending or [])
        self.opened: list[str] = []
        self.handles: list[FakeHandle] = []
        self.fail_list_once: Exception | None = None

    def list_input_ports(self):
        if self.fail_list_once is not None:
            exc, self.fail_list_once = self.fail_list_once, None
            raise exc
        return list(self.ports)

    def open_input(self, port):
        self.opened.append(port)
        handle = FakeHandle(self, port)
        self.handles.append(handle)
        return handle

    @staticmethod
    def iter_pending(handle):
        yield from handle.iter_pending()


def msg(msg_type, channel=0, note=60, velocity=64, control=64, value=0):
    """Build a mido-like raw message (duck-typed surface only)."""
    return types.SimpleNamespace(
        type=msg_type,
        channel=channel,
        note=note,
        velocity=velocity,
        control=control,
        value=value,
    )


class SelectPortTests(unittest.TestCase):
    def test_single_match_selected_case_insensitive(self):
        self.assertEqual(
            select_input_port(
                ["System Timer", "Roland Digital Piano MIDI 1"], "ROLAND"
            ),
            "Roland Digital Piano MIDI 1",
        )

    def test_no_match_raises_retryable_error(self):
        with self.assertRaises(EndpointNotFoundError):
            select_input_port(["System Timer"], "roland")

    def test_multiple_matches_fail_closed(self):
        with self.assertRaises(AmbiguousEndpointError):
            select_input_port(
                ["Roland A", "Roland B"], "roland"
            )

    def test_exact_preferred_wins_over_matcher(self):
        ports = ["x Roland One", "Roland Two"]
        self.assertEqual(
            select_input_port(ports, "roland", preferred="Roland Two"),
            "Roland Two",
        )


class NormalizeTests(unittest.TestCase):
    def test_note_on_normalized(self):
        event = normalize_message(msg("note_on", channel=0, note=60, velocity=99), 1.0)
        self.assertEqual((event.type.value, event.channel, event.note, event.velocity), ("note_on", 1, 60, 99))
        self.assertEqual(event.timestamp, 1.0)

    def test_note_on_velocity_zero_becomes_note_off(self):
        event = normalize_message(msg("note_on", channel=2, note=64, velocity=0), 2.0)
        self.assertEqual(event.type.value, "note_off")
        self.assertEqual(event.channel, 3)
        self.assertEqual(event.note, 64)
        self.assertEqual(event.velocity, 0)

    def test_note_off_normalized(self):
        event = normalize_message(msg("note_off", channel=0, note=48, velocity=7), 3.0)
        self.assertEqual(event.type.value, "note_off")
        self.assertEqual(event.note, 48)
        self.assertEqual(event.velocity, 7)

    def test_sustain_cc64_pressed_and_released(self):
        pressed = normalize_message(msg("control_change", control=64, value=127), 4.0)
        released = normalize_message(msg("control_change", control=64, value=0), 5.0)
        self.assertTrue(pressed.pressed)
        self.assertEqual(pressed.value, 127)
        self.assertFalse(released.pressed)

    def test_other_control_changes_dropped(self):
        self.assertIsNone(normalize_message(msg("control_change", control=1, value=40), 0.0))

    def test_realtime_messages_dropped(self):
        for raw in (msg("clock"), msg("active_sensing"), msg("start")):
            self.assertIsNone(normalize_message(raw, 0.0))

    def test_out_of_range_data_dropped_not_raised(self):
        self.assertIsNone(normalize_message(msg("note_on", channel=0, note=999, velocity=99), 0.0))


class AdapterLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.backend = FakeBackend(["Roland Digital Piano"])
        self.adapter = MidiInputAdapter(
            self.backend,
            matcher="roland",
            event_callback=self.received.append,
            poll_interval=0.001,
            port_check_interval=0.02,
        )
        self.addCleanup(self.adapter.stop, 2.0)

    def test_connects_and_stays_connected(self):
        self.adapter.start()
        self.assertTrue(wait_for(lambda: self.adapter.state is ConnectionState.CONNECTED))
        self.assertEqual(self.backend.opened, ["Roland Digital Piano"])

    def test_clean_shutdown_releases_connection(self):
        self.adapter.start()
        wait_for(lambda: self.adapter.state is ConnectionState.CONNECTED)
        self.adapter.stop(2.0)
        self.assertIs(self.adapter.state, ConnectionState.DISCONNECTED)
        self.assertFalse(self.adapter._thread.is_alive())

    def test_events_delivered_in_order(self):
        self.backend.pending = [
            msg("note_on", note=60, velocity=99),
            msg("note_on", note=60, velocity=0),  # normalized to note_off
            msg("note_off", note=60, velocity=5),
        ]
        self.adapter.start()
        self.assertTrue(wait_for(lambda: self.adapter.events_received >= 3))
        self.assertEqual(
            [e.type.value for e in self.received],
            ["note_on", "note_off", "note_off"],
        )
        self.assertEqual(self.received[1].velocity, 0)

    def test_sustain_events_delivered(self):
        self.backend.pending = [
            msg("control_change", control=64, value=127),
            msg("control_change", control=64, value=0),
        ]
        self.adapter.start()
        self.assertTrue(wait_for(lambda: self.adapter.events_received >= 2))
        self.assertTrue(self.received[0].pressed)
        self.assertFalse(self.received[1].pressed)

    def test_no_matching_port_stays_disconnected(self):
        self.backend.ports = ["Something Else"]
        self.adapter.start()
        self.assertTrue(wait_for(lambda: self.adapter.last_error is None))
        self.assertIs(self.adapter.state, ConnectionState.DISCONNECTED)
        self.assertEqual(self.adapter.events_received, 0)

    def test_endpoint_loss_disconnects_then_reconnects(self):
        self.adapter.start()
        self.assertTrue(wait_for(lambda: self.adapter.state is ConnectionState.CONNECTED))
        # Endpoint disappears at the next port check...
        self.backend.ports = []
        self.assertTrue(
            wait_for(lambda: self.adapter.state is ConnectionState.DISCONNECTED, timeout=3)
        )
        # ...and returns; the adapter must reconnect on its own (reconnect
        # delay is 2s, so allow well beyond one full cycle).
        self.backend.ports = ["Roland Digital Piano"]
        self.assertTrue(
            wait_for(lambda: self.adapter.state is ConnectionState.CONNECTED, timeout=5)
        )


class AmbiguityTests(unittest.TestCase):
    def test_ambiguous_ports_fail_closed_and_stop_retrying(self):
        received = []
        backend = FakeBackend(["Roland One", "Roland Two"])
        adapter = MidiInputAdapter(
            backend,
            matcher="roland",
            event_callback=received.append,
            poll_interval=0.001,
        )
        adapter.start()
        self.assertTrue(wait_for(lambda: adapter.last_error is not None))
        self.assertIs(adapter.state, ConnectionState.DISCONNECTED)
        self.assertEqual(received, [])
        self.assertEqual(backend.opened, [])
        adapter.stop(1.0)
        self.assertIs(adapter.state, ConnectionState.DISCONNECTED)

    def test_no_output_surface_exists(self):
        """The adapter must expose no way to transmit MIDI."""
        for name in dir(MidiInputAdapter):
            self.assertNotIn("send", name.lower(), name)
            self.assertNotIn("output", name.lower(), name)
            self.assertNotIn("write", name.lower(), name)
        for name in dir(FakeBackend):
            self.assertNotIn("send", name.lower(), name)
            self.assertNotIn("output", name.lower(), name)


class CaptureFilterTests(unittest.TestCase):
    """The capture CLI's --type filter keeps evidence minimal."""

    def test_filter(self):
        from piano_core.midi import capture
        from piano_core.midi.events import note_off, note_on, sustain

        note_on_ev = note_on(1, 60, 99, 1.0)
        note_off_ev = note_off(1, 60, 0, 2.0)
        sustain_ev = sustain(1, 127, 3.0)

        self.assertTrue(capture.event_matches_filter(note_on_ev, "all"))
        self.assertFalse(capture.event_matches_filter(note_on_ev, "sustain"))
        self.assertTrue(capture.event_matches_filter(sustain_ev, "sustain"))
        self.assertFalse(capture.event_matches_filter(note_off_ev, "sustain"))


class DuplicateInstanceTests(unittest.TestCase):
    def test_second_start_while_running_raises(self):
        backend = FakeBackend(["Roland Digital Piano"])
        adapter = MidiInputAdapter(backend, matcher="roland")
        adapter.start()
        self.addCleanup(adapter.stop, 2.0)
        with self.assertRaises(RuntimeError):
            adapter.start()


def wait_for(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


if __name__ == "__main__":
    unittest.main()
