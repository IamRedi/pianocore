"""V2/V3 tests: service-side playback session manager (PIANO-P012).

Deterministic, mido-free: the timeline loader is injected, the output
backend is the scripted P007 fake, and the library is a real synthetic
LibraryService. Proves the canonical flow (song_id -> lookup -> resolve
-> timeline -> engine), the appliance advisory-lock participation, the
typed error surface, and that STOP never becomes PANIC.
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from piano_core.library.model import LibraryConfigError, LibraryPathError
from piano_core.library.service import LibraryConfig, LibraryService
from piano_core.midi.output import MidiOutputAdapter
from piano_core.midi.output_events import note_off, note_on
from piano_core.playback.advisory import LOCK_FILENAME, acquire_playback_lock
from piano_core.playback.engine import (
    PlaybackAlreadyActiveError,
    PlaybackError,
    PlaybackState,
)
from piano_core.playback.manager import (
    PlaybackLockBusyError,
    PlaybackManager,
    PlaybackNotAvailableError,
)
from piano_core.playback.timeline import Timeline, TimelineEvent
from piano_core.state import StateStore
from tests.test_library import write_midi
from tests.test_midi_output import FakeOutputBackend

_ROLAND = ["Roland Digital Piano MIDI 1"]


def _tiny_timeline() -> Timeline:
    """A 0.2s session: one owned note attack + release."""
    return Timeline(
        events=(
            TimelineEvent(0.0, note_on(1, 60, 80)),
            TimelineEvent(0.2, note_off(1, 60, 0)),
        ),
        duration=0.2,
        source="<test>",
    )


def _slow_timeline() -> Timeline:
    """An attack followed by a long gap (for pause/resume/stop windows)."""
    return Timeline(
        events=(
            TimelineEvent(0.0, note_on(1, 60, 80)),
            TimelineEvent(30.0, note_off(1, 60, 0)),
        ),
        duration=30.0,
        source="<test>",
    )


def _settle() -> None:
    """Give a just-started playback worker a bounded moment to run."""
    time.sleep(0.05)


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()
        self.index_path = Path(self.tmp.name) / "index" / "library.sqlite3"
        self.lock_path = str(Path(self.tmp.name) / LOCK_FILENAME)
        self.backend = FakeOutputBackend(_ROLAND)
        self.adapter = MidiOutputAdapter(self.backend, matcher="roland")
        self.adapter.open()
        self.port = self.backend.outputs[-1]
        self.library = LibraryService(
            LibraryConfig(root=str(self.root), index_path=str(self.index_path))
        )
        write_midi(self.root / "a.mid")
        self.library.scan(enrich=False)
        self.song_id = self.library.list_songs()[0].song_id
        self.manager = self._make_manager()

    def _make_manager(self, library="default", timeline_loader=None):
        return PlaybackManager(
            output_adapter=self.adapter,
            library=self.library if library == "default" else library,
            timeline_loader=timeline_loader
            or (lambda path, tempo_scale=1.0: _slow_timeline()),
            lock_path=self.lock_path,
        )

    def tearDown(self):
        self.manager.shutdown()
        self.adapter.close()


class PlaybackControlTests(ManagerTestCase):
    def test_play_by_song_id_sets_session(self):
        session = self.manager.play(self.song_id, tempo_multiplier=1.5)
        self.assertEqual(session.state, "PLAYING")
        self.assertEqual(session.song_id, self.song_id)
        self.assertEqual(session.tempo_multiplier, 1.5)
        self.assertTrue(session.available)

    def test_stop_releases_owned_notes_and_clears_session(self):
        self.manager.play(self.song_id)
        _settle()
        session, stopped = self.manager.stop()
        self.assertTrue(stopped)
        self.assertEqual(session.state, "IDLE")
        self.assertIsNone(session.song_id)
        self.assertIsNone(session.title)
        # owned-only release happened on the wire
        note_offs = [s for s in self.port.sent if s[0] == "note_off"]
        self.assertTrue(any(s[2] == 60 for s in note_offs))
        # the lock is free again
        probe = acquire_playback_lock(self.lock_path)
        probe.release()

    def test_repeated_stop_is_idempotent(self):
        self.manager.play(self.song_id)
        _settle()
        first, stopped1 = self.manager.stop()
        second, stopped2 = self.manager.stop()
        self.assertTrue(stopped1)
        self.assertFalse(stopped2)
        self.assertEqual(first.state, "IDLE")
        self.assertEqual(second.state, "IDLE")

    def test_pause_resume_roundtrip(self):
        self.manager.play(self.song_id)
        _settle()
        paused = self.manager.pause()
        self.assertEqual(paused.state, "PAUSED")
        resumed = self.manager.resume()
        self.assertEqual(resumed.state, "PLAYING")
        self.manager.stop()

    def test_pause_requires_playing(self):
        with self.assertRaises(PlaybackError):
            self.manager.pause()

    def test_resume_requires_paused(self):
        with self.assertRaises(PlaybackError):
            self.manager.resume()

    def test_play_requires_non_empty_song_id(self):
        with self.assertRaises(ValueError):
            self.manager.play("  ")

    def test_natural_completion_retains_canonical_session_identity(self):
        manager = self._make_manager(
            timeline_loader=lambda path, tempo_scale=1.0: _tiny_timeline()
        )
        session = manager.play(self.song_id)
        self.assertEqual(session.state, "PLAYING")
        self.assertTrue(manager._engine.wait(5.0))
        for _ in range(50):
            if manager._watcher is None:
                break
            time.sleep(0.05)
        final = manager.snapshot()
        self.assertEqual(final.state, PlaybackState.COMPLETED.value)
        self.assertEqual(final.song_id, self.song_id)
        self.assertTrue(final.title)
        self.assertEqual(final.position_seconds, final.duration_seconds)
        # lock released by the watcher
        probe = acquire_playback_lock(self.lock_path)
        probe.release()
        manager.shutdown()

    def test_natural_failure_retains_canonical_session_identity(self):
        manager = self._make_manager(
            timeline_loader=lambda path, tempo_scale=1.0: _tiny_timeline()
        )

        def fail_send(*args):
            raise RuntimeError("simulated output failure")

        self.backend.send_note_on = fail_send
        manager.play(self.song_id)
        self.assertTrue(manager._engine.wait(5.0))
        for _ in range(50):
            if manager._watcher is None:
                break
            time.sleep(0.05)
        final = manager.snapshot()
        self.assertEqual(final.state, PlaybackState.FAILED.value)
        self.assertEqual(final.song_id, self.song_id)
        self.assertTrue(final.title)
        self.assertTrue(final.error)
        probe = acquire_playback_lock(self.lock_path)
        probe.release()
        manager.shutdown()


class ManagerErrorTests(ManagerTestCase):
    def test_unknown_song_id_raises_library_path_error(self):
        with self.assertRaises(LibraryPathError):
            self.manager.play("00000000-0000-5000-8000-000000000000")

    def test_playback_unavailable_when_output_closed(self):
        self.adapter.close()
        with self.assertRaises(PlaybackNotAvailableError):
            self.manager.play(self.song_id)
        with self.assertRaises(PlaybackNotAvailableError):
            self.manager.stop()

    def test_library_unavailable_raises_library_config_error(self):
        manager = self._make_manager(library=None)
        with self.assertRaises(LibraryConfigError):
            manager.play(self.song_id)

    def test_second_play_conflicts(self):
        self.manager.play(self.song_id)
        _settle()
        with self.assertRaises(PlaybackAlreadyActiveError):
            self.manager.play(self.song_id)
        self.manager.stop()

    def test_advisory_lock_busy_raises_typed_error(self):
        external = acquire_playback_lock(self.lock_path)
        try:
            with self.assertRaises(PlaybackLockBusyError):
                self.manager.play(self.song_id)
        finally:
            external.release()

    def test_concurrent_play_exactly_one_wins(self):
        results = []
        errors = []
        barrier = threading.Barrier(4)

        def attempt():
            barrier.wait()
            try:
                results.append(self.manager.play(self.song_id))
            except PlaybackAlreadyActiveError:
                errors.append("conflict")

        threads = [threading.Thread(target=attempt) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 3)
        self.manager.stop()


class ManagerBoundaryTests(ManagerTestCase):
    def test_stop_never_sends_panic_sweep(self):
        self.manager.play(self.song_id)
        _settle()
        self.manager.stop()
        for entry in self.port.sent:
            if entry[0] == "control_change":
                self.assertNotIn(entry[2], (120, 123), f"global sweep: {entry}")

    def test_playback_never_touches_statestore(self):
        store = StateStore()
        before = store.snapshot()
        self.manager.play(self.song_id)
        _settle()
        self.manager.stop()
        self.assertEqual(store.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
