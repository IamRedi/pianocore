"""PIANO-P014: playback -> LED visualization integration tests.

Contract under test (ADR-LED-KEYBOARD-V1 D4): the LED observer follows
canonical P009 playback events without creating a second scheduler, and
LED failure can never fail, stall, or deschedule playback. StateStore and
MIDI ownership semantics are untouched (asserted here again).
"""

import logging
import tempfile
import unittest
from pathlib import Path

from piano_core.led.adapter import LedKeyboardAdapter
from piano_core.led.model import LedMode
from piano_core.midi.output import MidiOutputAdapter
from piano_core.midi.output_events import note_off, note_on
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.playback.engine import PlaybackEngine, PlaybackState
from piano_core.playback.manager import PlaybackManager
from piano_core.playback.timeline import Timeline, TimelineEvent
from tests.test_led_adapter import FakeTransport
from tests.test_library import write_midi
from tests.test_midi_input import wait_for
from tests.test_midi_output import FakeOutputBackend


def _fake_transport_adapter(mode=LedMode.ALL):
    transport = FakeTransport()
    adapter = LedKeyboardAdapter(
        "127.0.0.1",
        8787,
        enabled=True,
        mode=mode,
        transport_factory=lambda host, port: transport,
    )
    adapter.start()
    return adapter, transport


def _short_timeline():
    return Timeline(
        events=(
            TimelineEvent(0.0, note_on(1, 60, 80)),
            TimelineEvent(0.0, note_on(1, 64, 80)),
            TimelineEvent(1.0, note_off(1, 60, 0)),
            TimelineEvent(2.0, note_off(1, 64, 0)),
        ),
        duration=2.0,
        source="<test>",
    )


class PlaybackLedIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeOutputBackend(["Roland Digital Piano MIDI 1"])
        self.output = MidiOutputAdapter(self.backend, matcher="roland")
        self.output.open()
        self.addCleanup(self.output.close)
        self.adapter, self.transport = _fake_transport_adapter()
        self.addCleanup(self.adapter.stop)

    def test_engine_observer_attribute_exists(self):
        # The integration surface used by PlaybackManager.
        from piano_core.led.integration import LedIntegration

        integration = LedIntegration(self.adapter)
        self.assertTrue(callable(integration.observe_output_command))
        self.assertTrue(callable(integration.observe_input_event))
        self.assertTrue(callable(integration.session_ended))

    def test_note_on_off_drive_leds_and_completion_clears(self):
        from piano_core.led.integration import LedIntegration

        integration = LedIntegration(self.adapter)
        engine = PlaybackEngine(
            self.output, event_observer=integration.observe_output_command
        )
        self.transport.connected = True
        self.transport.on_connect()
        engine.play(_short_timeline())
        self.assertTrue(engine.wait(5.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertTrue(wait_for(lambda: self.adapter.snapshot().active_note_count == 0))
        self.assertIn(
            '{"type":"midi","event":"note_on","note":60,"velocity":80}',
            self.transport.frames,
        )
        self.assertIn('{"type":"midi","event":"note_off","note":60}', self.transport.frames)

    def test_chord_and_repeated_note(self):
        from piano_core.led.integration import LedIntegration

        integration = LedIntegration(self.adapter)
        engine = PlaybackEngine(
            self.output, event_observer=integration.observe_output_command
        )
        self.transport.connected = True
        self.transport.on_connect()
        timeline = Timeline(
            events=(
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(0.0, note_on(1, 64, 80)),
                TimelineEvent(0.0, note_on(1, 67, 80)),
                TimelineEvent(0.0, note_on(1, 60, 80)),  # repeated (deduped)
            ),
            duration=0.5,
        )
        engine.play(timeline)
        self.assertTrue(engine.wait(5.0))
        # The LED worker drains asynchronously; wait for the frames.
        lit = lambda: [
            f for f in self.transport.frames if '"event":"note_on"' in f
        ]
        self.assertTrue(wait_for(lambda: len(lit()) >= 3, timeout=5.0))
        self.assertEqual(len(lit()), 3)  # 60/64/67, repeat deduped

    def test_failing_observer_never_fails_playback(self):
        def bad_observer(command):
            raise RuntimeError("led exploded")

        engine = PlaybackEngine(self.output, event_observer=bad_observer)
        engine.play(_short_timeline())
        self.assertTrue(engine.wait(5.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual(engine.error, None)

    def test_midi_output_ownership_unaffected_by_observer(self):
        from piano_core.led.integration import LedIntegration

        integration = LedIntegration(self.adapter)
        engine = PlaybackEngine(
            self.output, event_observer=integration.observe_output_command
        )
        self.transport.connected = True
        engine.play(_short_timeline())
        self.assertTrue(engine.wait(5.0))
        # Output ownership bookkeeping is exactly as without LEDs.
        self.assertEqual(len(self.output.owned_notes), 0)
        self.assertEqual(len(self.output.owned_sustain_channels), 0)
        sent = [entry[0] for entry in self.backend.outputs[-1].sent]
        self.assertIn("note_on", sent)
        self.assertIn("note_off", sent)
        self.assertNotIn("panic", sent)


class PlaybackManagerSessionEndTests(unittest.TestCase):
    def _manager(self):
        # PlaybackManager requires a real MidiOutputAdapter + LibraryService
        # (song_id-only play is the canonical path — P012).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "root"
        root.mkdir()
        index_path = Path(tmp.name) / "index" / "library.sqlite3"
        backend = FakeOutputBackend(["Roland Digital Piano MIDI 1"])
        output = MidiOutputAdapter(backend, matcher="roland")
        output.open()
        self.addCleanup(output.close)
        library = LibraryService(
            LibraryConfig(root=str(root), index_path=str(index_path))
        )
        write_midi(root / "a.mid", notes=((60, 80),))
        library.scan(enrich=False)
        song_id = library.list_songs()[0].song_id
        ends = []
        manager = PlaybackManager(
            output_adapter=output,
            library=library,
            timeline_loader=lambda path, tempo_scale=1.0: _short_timeline(),
            lock_path=str(Path(tmp.name) / "playback.lock"),
            on_session_end=lambda: ends.append(True),
        )
        return manager, song_id, ends

    def test_on_session_end_called_on_natural_completion(self):
        manager, song_id, ends = self._manager()
        manager.play(song_id, tempo_multiplier=4.0)
        self.assertTrue(wait_for(lambda: ends, timeout=10.0))

    def test_on_session_end_called_on_stop(self):
        manager, song_id, ends = self._manager()
        manager.play(song_id, tempo_multiplier=0.25)
        manager.stop()
        self.assertTrue(wait_for(lambda: ends, timeout=10.0))


if __name__ == "__main__":
    unittest.main()
