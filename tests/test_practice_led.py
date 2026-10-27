"""PIANO-P015: practice LED visualization integration tests (ADR D5).

Drives the full canonical production path: canonical events are reduced
into the StateStore first, then PracticeManager.observe_input_event
observes (exactly what Application._on_input_event does), and display
changes flow through the P014 LedKeyboardAdapter's bounded queue.
"""

import logging
import tempfile
import unittest
from pathlib import Path

from piano_core.led.adapter import LedKeyboardAdapter
from piano_core.led.integration import LedIntegration
from piano_core.led.model import LedMode
from piano_core.midi.events import note_off as ie_off
from piano_core.midi.events import note_on as ie_on
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.practice.manager import PracticeManager
from tests.test_led_adapter import FakeTransport
from tests.test_library import write_midi
from tests.test_midi_input import wait_for


def _note_on_frames(transport):
    return [
        f
        for f in transport.frames
        if '"event":"note_on"' in f
    ]


def _note_off_frames(transport):
    return [
        f
        for f in transport.frames
        if '"event":"note_off"' in f
    ]


class PracticeLedHarness(unittest.TestCase):
    def setUp(self):
        logging.getLogger("piano_core.practice").setLevel(logging.CRITICAL)
        self.addCleanup(
            logging.getLogger("piano_core.practice").setLevel, logging.INFO
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "root"
        root.mkdir()
        # One song: C4, then C4+E4+G4 chord.
        write_midi(
            root / "practice.mid",
            notes=((60, 80), (64, 80), (67, 80)),
        )
        self.library = LibraryService(
            LibraryConfig(
                root=str(root),
                index_path=str(Path(tmp.name) / "index" / "library.sqlite3"),
            )
        )
        self.library.scan(enrich=False)
        self.song_id = self.library.list_songs()[0].song_id

        self.transport = FakeTransport()
        adapter = LedKeyboardAdapter(
            "127.0.0.1",
            8787,
            enabled=True,
            mode=LedMode.PLAYBACK,
            transport_factory=lambda host, port: self.transport,
        )
        adapter.start()
        self.addCleanup(adapter.stop)
        self.adapter = adapter
        self.led = LedIntegration(adapter)
        self.transport.connected = True
        self.transport.on_connect()

        from piano_core.state import MidiConnectionState, StateStore

        self.store = StateStore()
        # Production reality (U4-F1): the canonical input path is
        # CONNECTED before any practice grading can occur; a DISCONNECTED
        # store must never advance a session.
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.manager = PracticeManager(
            library=self.library,
            state_store=self.store,
            lock_path=str(Path(tmp.name) / "activity.lock"),
            led=self.led,
            timeline_loader=lambda path, tempo_scale=1.0: _practice_timeline(),
        )
        self.addCleanup(self.manager.shutdown)

    # -- canonical-path helpers (mirror Application._on_input_event) ----
    def play(self, note, velocity=90):
        event = ie_on(1, note, velocity, 0.0)
        self.store.apply_event(event)
        self.led.observe_input_event(event)
        self.manager.observe_input_event(event)

    def release(self, note):
        event = ie_off(1, note, 0, 0.0)
        self.store.apply_event(event)
        self.led.observe_input_event(event)
        self.manager.observe_input_event(event)

    def start(self):
        return self.manager.start(self.song_id)


def _practice_timeline():
    from piano_core.midi.output_events import note_on
    from piano_core.playback.timeline import Timeline, TimelineEvent

    return Timeline(
        events=(
            TimelineEvent(0.0, note_on(1, 60, 80)),
            TimelineEvent(1.0, note_on(1, 60, 80)),
            TimelineEvent(2.0, note_on(1, 64, 80)),
            TimelineEvent(2.0, note_on(1, 67, 80)),
        ),
        duration=3.0,
        source="practice.mid",
    )


class PracticeLedTests(PracticeLedHarness):
    def test_first_target_lights(self):
        session = self.start()
        self.assertEqual(session.state.value, "WAITING")
        self.assertTrue(
            wait_for(
                lambda: '"event":"note_on","note":60' in "".join(
                    _note_on_frames(self.transport)
                ),
                timeout=5.0,
            )
        )
        self.assertTrue(self.adapter.snapshot().practice_active)
        self.assertEqual(self.adapter.snapshot().active_note_count, 1)

    def test_correct_target_replaces_and_chord_lights_all(self):
        self.start()
        self.play(60)  # step 1 completes -> repeated C4 target (disarmed, held)
        self.release(60)  # re-arms the repeated target
        self.play(60)  # fresh press completes step 2 -> chord target (64, 67)
        self.assertTrue(
            wait_for(
                lambda: self.adapter.snapshot().active_note_count == 2,
                timeout=5.0,
            )
        )
        lit_notes = sorted(
            int(f.split('"note":')[1].split(",")[0])
            for f in _note_on_frames(self.transport)
        )
        self.assertIn(64, lit_notes)
        self.assertIn(67, lit_notes)

    def test_stop_clears_practice_leds(self):
        self.start()
        self.play(60)  # step 1 completes; the repeated target stays lit
        self.assertTrue(
            wait_for(lambda: self.adapter.snapshot().active_note_count >= 1, timeout=5.0)
        )
        self.manager.stop()
        self.assertTrue(
            wait_for(lambda: self.adapter.snapshot().active_note_count == 0, timeout=5.0)
        )
        self.assertFalse(self.adapter.snapshot().practice_active)

    def test_completion_clears_practice_leds(self):
        self.start()
        self.play(60)
        self.release(60)
        self.play(60)  # step 2 (repeated) needs release+fresh press
        self.release(60)
        self.play(64)
        self.play(67)  # chord completes the song
        self.assertTrue(
            wait_for(
                lambda: self.manager.snapshot().state.value == "COMPLETED",
                timeout=5.0,
            )
        )
        self.assertTrue(
            wait_for(lambda: self.adapter.snapshot().active_note_count == 0, timeout=5.0)
        )
        self.assertFalse(self.adapter.snapshot().practice_active)

    def test_led_disconnected_never_breaks_the_session(self):
        self.transport.connected = False  # "LED unavailable"
        session = self.start()
        self.assertEqual(session.state.value, "WAITING")
        self.play(60)
        self.release(60)
        self.play(60)  # completes repeated note without any LED feedback
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot.state.value, "WAITING")
        self.assertEqual(snapshot.completed_steps, 2)
        self.assertIsNotNone(snapshot.current_target)  # chord still readable
        self.assertEqual(snapshot.current_target.notes, (64, 67))

    def test_led_reenable_hf1_cycle_untouched_by_practice(self):
        self.start()
        self.play(60)
        self.adapter.set_enabled(False)
        self.assertFalse(self.adapter.snapshot().enabled)
        self.adapter.set_enabled(True)  # HF1: re-enable stays offered/working
        self.assertTrue(self.adapter.snapshot().enabled)
        # The ESP32 redials (client reconnect): the connect refresh must
        # restore the still-active practice target.
        frames_before = len(self.transport.frames)
        self.transport.connected = True
        self.transport.on_connect()
        self.assertTrue(
            wait_for(
                lambda: len(self.transport.frames) > frames_before
                and '"event":"note_on","note":60' in self.transport.frames[-1],
                timeout=5.0,
            )
        )
        self.assertTrue(self.adapter.snapshot().practice_active)
        self.manager.stop()

    def test_physical_and_playback_visualization_unchanged_outside_practice(self):
        # No practice session: physical mirroring follows the mode as P014
        # (mode "all" shows the physical source).
        self.adapter.set_mode(LedMode.ALL)
        self.play(62)
        self.assertTrue(
            wait_for(
                lambda: '"event":"note_on","note":62' in "".join(
                    _note_on_frames(self.transport)
                ),
                timeout=5.0,
            )
        )
        self.release(62)
        # During practice, physical mirroring is suppressed (target-only).
        self.start()
        self.play(62)
        self.assertTrue(
            wait_for(lambda: self.adapter.snapshot().practice_active, timeout=5.0)
        )
        snapshot = self.adapter.snapshot()
        self.assertEqual(snapshot.active_note_count, 1)  # only the C4 target
        self.manager.stop()
        self.release(62)


if __name__ == "__main__":
    unittest.main()
