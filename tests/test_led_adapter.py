"""PIANO-P014: LED keyboard adapter tests (deterministic, fake transport).

The fake transport records every frame exactly as the wire would carry
it; connection presence is scriptable so degraded behavior is provable.
"""

import logging
import threading
import time
import unittest

from piano_core.led.adapter import LedKeyboardAdapter
from piano_core.led.model import LedConnectionState, LedError, LedMode


class FakeTransport:
    """Scriptable transport double (no sockets)."""

    def __init__(self) -> None:
        self.frames: list[str] = []
        self.connected = False
        self.started = False
        self.stopped = False
        self.fail_sends = False
        self._lock = threading.Lock()
        self.on_message = None
        self.on_connect = None
        self.on_disconnect = None

    @property
    def bound_address(self):
        return ("127.0.0.1", 8787) if self.started else None

    @property
    def client_connected(self) -> bool:
        return self.connected

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        self.started = False
        self.connected = False

    def send_text(self, text: str, timeout: float = 2.0) -> bool:
        if self.fail_sends or not self.connected:
            return False
        with self._lock:
            self.frames.append(text)
        return True


def make_adapter(**kwargs):
    transport = FakeTransport()
    defaults = dict(
        host="127.0.0.1",
        port=8787,
        enabled=True,
        mode=LedMode.ALL,
        brightness=100,
        logger=logging.getLogger("test.led"),
        transport_factory=lambda host, port: transport,
    )
    defaults.update(kwargs)
    adapter = LedKeyboardAdapter(**defaults)
    adapter.start()
    return adapter, transport


class LedKeyboardAdapterTests(unittest.TestCase):
    def _connect(self, transport: FakeTransport) -> None:
        transport.connected = True
        transport.on_connect()

    def _drain(self, adapter: LedKeyboardAdapter) -> None:
        """Give the worker a moment to drain the queue."""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if adapter._queue.empty():
                time.sleep(0.02)
                return
            time.sleep(0.01)

    def _connect_and_reset(self, adapter, transport) -> None:
        """Connect, let the reconnect refresh flush, then reset frames."""
        self._connect(transport)
        self._drain(adapter)
        transport.frames.clear()

    def test_disabled_by_default_reports_disabled(self):
        adapter, transport = make_adapter(enabled=False)
        state = adapter.snapshot()
        self.assertFalse(state.enabled)
        self.assertEqual(state.connection, LedConnectionState.DISABLED)
        self.assertFalse(transport.started)

    def test_enabled_starts_listening(self):
        adapter, transport = make_adapter()
        self._drain(adapter)
        state = adapter.snapshot()
        self.assertTrue(state.enabled)
        self.assertEqual(state.connection, LedConnectionState.LISTENING)
        self.assertTrue(transport.started)
        adapter.stop()

    def test_bind_failure_degrades_not_raises(self):
        def failing_factory(host, port):
            transport = FakeTransport()

            def start():
                raise OSError("address in use")

            transport.start = start
            return transport

        adapter = LedKeyboardAdapter(
            "127.0.0.1",
            8787,
            enabled=True,
            transport_factory=failing_factory,
        )
        adapter.start()
        try:
            state = adapter.snapshot()
            self.assertEqual(state.connection, LedConnectionState.UNAVAILABLE)
            self.assertIn("bind failed", state.last_error)
        finally:
            adapter.stop()

    def test_note_on_lights_and_note_off_clears(self):
        adapter, transport = make_adapter()
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        self.assertIn(
            '{"type":"midi","event":"note_on","note":60,"velocity":80}',
            transport.frames,
        )
        adapter.observe_note(60, 0, "playback", on=False)
        self._drain(adapter)
        self.assertIn(
            '{"type":"midi","event":"note_off","note":60}',
            transport.frames,
        )
        self.assertEqual(adapter.snapshot().active_note_count, 0)
        adapter.stop()

    def test_repeated_note_on_is_idempotent_per_source(self):
        adapter, transport = make_adapter()
        self._connect(transport)
        for _ in range(3):
            adapter.observe_note(64, 70, "playback", on=True)
        self._drain(adapter)
        lit = [
            f for f in transport.frames if '"note":64,"velocity":70' in f
        ]
        self.assertEqual(len(lit), 1)
        adapter.stop()

    def test_out_of_range_notes_are_ignored(self):
        adapter, transport = make_adapter()
        self._connect_and_reset(adapter, transport)
        adapter.observe_note(200, 64, "playback", on=True)
        adapter.observe_note(-1, 64, "playback", on=True)
        adapter.observe_note(60, 0, "playback", on=True)  # invalid velocity
        self._drain(adapter)
        self.assertEqual(transport.frames, [])
        adapter.stop()

    def test_union_blending_between_sources(self):
        adapter, transport = make_adapter(mode=LedMode.ALL)
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        adapter.observe_note(60, 90, "physical", on=True)
        self._drain(adapter)
        # Physical note-off must NOT clear the playback-held key.
        adapter.observe_note(60, 0, "physical", on=False)
        self._drain(adapter)
        self.assertNotIn(
            '{"type":"midi","event":"note_off","note":60}',
            transport.frames,
        )
        self.assertEqual(adapter.snapshot().active_note_count, 1)
        # Playback note-off clears it.
        adapter.observe_note(60, 0, "playback", on=False)
        self._drain(adapter)
        self.assertEqual(adapter.snapshot().active_note_count, 0)
        adapter.stop()

    def test_mode_filters_sources(self):
        adapter, transport = make_adapter(mode=LedMode.PLAYBACK)
        self._connect_and_reset(adapter, transport)
        adapter.observe_note(60, 80, "physical", on=True)
        self._drain(adapter)
        self.assertEqual(transport.frames, [])  # physical invisible
        self.assertEqual(adapter.snapshot().active_note_count, 0)
        adapter.set_mode(LedMode.ALL)
        self._drain(adapter)
        # Refresh promotes the already-held physical note.
        self.assertIn(
            '{"type":"midi","event":"note_on","note":60,"velocity":80}',
            transport.frames,
        )
        adapter.stop()

    def test_off_mode_clears(self):
        adapter, transport = make_adapter(mode=LedMode.ALL)
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        transport.frames.clear()
        adapter.set_mode(LedMode.OFF)
        self._drain(adapter)
        self.assertIn('{"type":"playback","status":"all_notes_off"}', transport.frames)
        self.assertEqual(adapter.snapshot().active_note_count, 0)
        adapter.stop()

    def test_brightness_scales_velocity(self):
        adapter, transport = make_adapter(brightness=50)
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        self.assertIn(
            '{"type":"midi","event":"note_on","note":60,"velocity":40}',
            transport.frames,
        )
        adapter.stop()

    def test_brightness_zero_suppresses_display_but_keeps_state(self):
        adapter, transport = make_adapter(brightness=0)
        self._connect_and_reset(adapter, transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        self.assertEqual(transport.frames, [])
        self.assertEqual(adapter.snapshot().active_note_count, 1)
        adapter.stop()

    def test_brightness_change_refreshes_display(self):
        adapter, transport = make_adapter(brightness=100)
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        transport.frames.clear()
        adapter.set_brightness(50)
        self._drain(adapter)
        self.assertIn('{"type":"playback","status":"all_notes_off"}', transport.frames)
        self.assertIn(
            '{"type":"midi","event":"note_on","note":60,"velocity":40}',
            transport.frames,
        )
        adapter.stop()

    def test_no_client_state_persists_and_reconnect_refreshes(self):
        adapter, transport = make_adapter()
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        self.assertEqual(transport.frames, [])
        self.assertEqual(adapter.snapshot().active_note_count, 1)
        # The ESP32 reconnects: display must be restored.
        self._connect(transport)
        self._drain(adapter)
        self.assertIn('{"type":"playback","status":"all_notes_off"}', transport.frames)
        self.assertIn(
            '{"type":"midi","event":"note_on","note":60,"velocity":80}',
            transport.frames,
        )
        adapter.stop()

    def test_send_failure_degrades_without_raising(self):
        adapter, transport = make_adapter()
        self._connect(transport)
        transport.fail_sends = True
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        self.assertEqual(adapter.snapshot().active_note_count, 1)
        self.assertEqual(adapter.snapshot().last_error, None)
        adapter.stop()

    def test_playback_ended_clears_playback_only(self):
        adapter, transport = make_adapter(mode=LedMode.ALL)
        self._connect_and_reset(adapter, transport)
        adapter.observe_note(60, 80, "playback", on=True)
        adapter.observe_note(64, 80, "physical", on=True)
        self._drain(adapter)
        transport.frames.clear()
        adapter.playback_ended()
        self._drain(adapter)
        self.assertIn('{"type":"midi","event":"note_off","note":60}', transport.frames)
        self.assertNotIn('{"type":"midi","event":"note_off","note":64}', transport.frames)
        self.assertEqual(adapter.snapshot().active_note_count, 1)
        adapter.stop()

    def test_hard_clear_wipes_all_state(self):
        adapter, transport = make_adapter(mode=LedMode.ALL)
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        adapter.observe_note(64, 80, "physical", on=True)
        self._drain(adapter)
        transport.frames.clear()
        adapter.clear()
        self._drain(adapter)
        self.assertIn('{"type":"playback","status":"all_notes_off"}', transport.frames)
        self.assertEqual(adapter.snapshot().active_note_count, 0)
        # A later playback note-off is a no-op (nothing held anymore).
        transport.frames.clear()
        adapter.observe_note(60, 0, "playback", on=False)
        self._drain(adapter)
        self.assertEqual(transport.frames, [])
        adapter.stop()

    def test_disable_sends_clear_and_closes(self):
        adapter, transport = make_adapter()
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        transport.frames.clear()
        adapter.set_enabled(False)
        self._drain(adapter)
        self.assertIn('{"type":"playback","status":"all_notes_off"}', transport.frames)
        state = adapter.snapshot()
        self.assertFalse(state.enabled)
        self.assertEqual(state.connection, LedConnectionState.DISABLED)
        adapter.stop()

    def test_disable_then_reenable_reopens_transport_and_refreshes(self):
        """P014-HF1 regression: re-enable must cleanly reopen the closed
        transport and restore the display for a reconnecting client."""
        adapter, transport = make_adapter()
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        adapter.set_enabled(False)
        self._drain(adapter)
        self.assertFalse(transport.started)
        self.assertEqual(adapter.snapshot().active_note_count, 0)

        adapter.set_enabled(True)
        self._drain(adapter)
        # The listening transport is open again.
        self.assertTrue(transport.started)
        state = adapter.snapshot()
        self.assertTrue(state.enabled)
        self.assertEqual(state.connection, LedConnectionState.LISTENING)
        # The ESP32 reconnects: held playback state was cleared by the
        # disable, so the refresh is an empty display (deterministic).
        self._connect(transport)
        self._drain(adapter)
        self.assertIn('{"type":"playback","status":"all_notes_off"}', transport.frames)
        adapter.stop()

    def test_queue_overflow_drops_oldest_and_counts(self):
        # Drive the overflow path directly through the enqueue helper.
        adapter, transport = make_adapter()
        for i in range(2000):
            adapter._enqueue(("note_on", 21 + (i % 88), 64, "playback"))
        self.assertGreater(adapter.snapshot().dropped_events, 0)
        adapter.stop()

    def test_invalid_source_rejected(self):
        adapter, _ = make_adapter()
        with self.assertRaises(LedError):
            adapter.observe_note(60, 64, "midi", on=True)
        adapter.stop()

    def test_snapshot_counts_frames(self):
        adapter, transport = make_adapter()
        self._connect(transport)
        adapter.observe_note(60, 80, "playback", on=True)
        self._drain(adapter)
        self.assertGreater(adapter.snapshot().frames_sent, 0)
        adapter.stop()


if __name__ == "__main__":
    unittest.main()
