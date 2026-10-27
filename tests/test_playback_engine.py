"""V2/V3 tests: playback engine lifecycle, ownership, cleanup (PIANO-P009).

All tests run against the scripted fake output backend (no mido, no
hardware, no sound). Determinism comes from injectable clocks:

- ``AutoClock`` advances virtual time instantly — full-timeline runs
  (ordering, tempo scaling, completion) finish in zero real time;
- ``GatedClock`` additionally blocks the worker at every wait until the
  test releases it, so pause/stop/resume are injected at EXACT points
  of the schedule (no real-time races).

Safety assertions that run across scenarios: cleanup uses
``STOP_AUTOMATED_NOTES`` semantics only (owned ``note_off`` + CC64=0);
no CC123/CC120 sweep is ever sent; the StateStore is never touched.
"""

import glob
import threading
import time
import unittest
from pathlib import Path

from piano_core.midi.output import MidiOutputAdapter
from piano_core.playback.engine import (
    PlaybackAlreadyActiveError,
    PlaybackEngine,
    PlaybackError,
    PlaybackState,
)
from piano_core.live_notes import LiveNoteState
from piano_core.midi.output_events import OutputCommandType, note_off, note_on, sustain
from piano_core.playback.timeline import TempoScaleError, Timeline, TimelineEvent
from piano_core.state import PianoState, StateStore
from tests.test_midi_input import wait_for
from tests.test_midi_output import FakeOutputBackend, SelectiveReleaseFailureBackend

_PLAYBACK_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "piano_core" / "playback"
)


class AutoClock:
    """Deterministic instant-advance clock (virtual monotonic time)."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def wait(self, event, timeout):
        if event.is_set():
            return True
        self.t += timeout
        return event.is_set()

    def wait_bounded(self, event, timeout):
        """Safety-chunk waits self-advance (never gated; tiny real pause)."""
        if event.is_set():
            return True
        time.sleep(0.001)
        self.t += timeout
        return event.is_set()


class GatedClock(AutoClock):
    """AutoClock whose SCHEDULE waits BLOCK until ``release()`` is called.

    Only ``wait`` (schedule delays) is gated — ``wait_bounded`` (pause
    safety chunks) self-advances, so a release is always consumed by the
    schedule wait the test intends. Controller calls (pause/stop/resume)
    still wake a gated wait immediately through the engine's wake event.
    ``release()`` must only be issued while the worker is parked inside a
    schedule wait (``wait_parked``).
    """

    def __init__(self):
        super().__init__()
        self._release = threading.Event()
        self._parked = 0
        self.wait_calls = []

    def parked_count(self):
        """Number of worker schedule-waits currently blocked on a release."""
        return self._parked

    def wait_parked(self, timeout=2.0):
        """Block until the worker is parked inside a gated schedule wait."""
        return wait_for(lambda: self._parked >= 1, timeout)

    def release(self):
        self._release.set()

    def wait(self, event, timeout):
        self.wait_calls.append(timeout)
        if event.is_set():
            return True
        self._parked += 1
        try:
            while not self._release.wait(0.005):
                if event.is_set():
                    return True
        finally:
            self._parked -= 1
        self._release.clear()
        self.t += timeout
        return event.is_set()


class ClockSpyAdapter:
    """Delegating adapter recording the virtual send time of each command."""

    def __init__(self, inner, clock):
        self._inner = inner
        self._clock = clock
        self.send_times = []

    @property
    def generation(self):
        return self._inner.generation

    def apply_for_generation(self, command, generation):
        assert generation == self.generation
        self.apply(command)

    @property
    def is_open(self):
        return self._inner.is_open

    @property
    def owned_notes(self):
        return self._inner.owned_notes

    @property
    def owned_sustain_channels(self):
        return self._inner.owned_sustain_channels

    def apply(self, command):
        self.send_times.append((self._clock.t, command.describe()))
        self._inner.apply(command)


class FailOnNthNoteOnAdapter:
    """Adapter whose NOTE_ON send fails on the Nth occurrence."""

    def __init__(self, inner, fail_on):
        self._inner = inner
        self._fail_on = fail_on
        self._note_ons = 0

    @property
    def generation(self):
        return self._inner.generation

    def apply_for_generation(self, command, generation):
        assert generation == self.generation
        self.apply(command)

    @property
    def is_open(self):
        return True

    @property
    def owned_notes(self):
        return self._inner.owned_notes

    @property
    def owned_sustain_channels(self):
        return self._inner.owned_sustain_channels

    def apply(self, command):
        from piano_core.midi.output_events import OutputCommandType

        if command.type is OutputCommandType.NOTE_ON:
            self._note_ons += 1
            if self._note_ons == self._fail_on:
                raise RuntimeError("simulated backend send failure")
        self._inner.apply(command)


def make_timeline(*pairs, source="<test>"):
    events = tuple(TimelineEvent(time=t, command=c) for t, c in pairs)
    return Timeline(events=events, duration=pairs[-1][0] if pairs else 0.0, source=source)


def simple_session():
    """note_on 60 at t=0, note_off at t=10 (one held note in between)."""
    return make_timeline(
        (0.0, note_on(1, 60, 80)),
        (10.0, note_off(1, 60)),
    )


class EngineTestCase(unittest.TestCase):
    """Shared harness; every engine is guaranteed terminal at teardown."""

    def setUp(self):
        self.backend = FakeOutputBackend(["Roland Digital Piano 1"])
        self.adapter = MidiOutputAdapter(self.backend, matcher="roland")
        self.adapter.open()
        self.engines = []

    def make_engine(self, clock):
        engine = PlaybackEngine(self.adapter, clock=clock)
        self.engines.append(engine)
        return engine

    def tearDown(self):
        for engine in self.engines:
            if not engine.state.is_terminal:
                engine.stop()
                engine.wait(2.0)

    @property
    def port(self):
        return self.backend.outputs[-1]

    def assert_no_global_sweep(self):
        for output in self.backend.outputs:
            for record in output.sent:
                if record[0] == "control_change":
                    self.assertNotIn(
                        record[2],
                        (123, 120),
                        f"global sweep sent: {record}",
                    )


class LifecycleTests(EngineTestCase):
    def test_play_requires_open_adapter(self):
        self.adapter.close()
        engine = PlaybackEngine(self.adapter, clock=AutoClock())
        with self.assertRaises(PlaybackError):
            engine.play(simple_session())
        self.assertIs(engine.state, PlaybackState.IDLE)

    def test_play_requires_timeline_type(self):
        engine = self.make_engine(AutoClock())
        with self.assertRaises(TypeError):
            engine.play(["not", "a", "timeline"])

    def test_engine_requires_adapter(self):
        with self.assertRaises(TypeError):
            PlaybackEngine(None)

    def test_natural_completion_sends_everything_in_order(self):
        timeline = make_timeline(
            (0.0, note_on(1, 60, 80)),
            (0.0, note_on(1, 64, 80)),       # chord
            (5.0, sustain(1, 127)),
            (10.0, note_off(1, 60)),
            (10.0, note_off(1, 64)),
            (12.0, sustain(1, 0)),
        )
        engine = self.make_engine(AutoClock())
        engine.play(timeline)
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertIsNone(engine.error)
        self.assertEqual(engine.events_sent, 6)
        self.assertEqual(
            self.port.sent,
            [
                ("note_on", 1, 60, 80),
                ("note_on", 1, 64, 80),
                ("control_change", 1, 64, 127),
                ("note_off", 1, 60, 0),
                ("note_off", 1, 64, 0),
                ("control_change", 1, 64, 0),
            ],
        )
        self.assertEqual(adapter_owned(self.adapter), ())
        self.assertEqual(adapter_sustain(self.adapter), ())
        self.assert_no_global_sweep()

    def test_empty_timeline_completes_immediately(self):
        engine = self.make_engine(AutoClock())
        engine.play(make_timeline())
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual(engine.events_sent, 0)
        self.assertEqual(self.port.sent, [])

    def test_malformed_song_end_releases_residual_owned_notes(self):
        # Final NOTE_ON with no NOTE_OFF: natural completion must not
        # leak a hanging note — cleanup releases Piano Core-owned state.
        timeline = make_timeline(
            (0.0, note_on(1, 60, 80)),
            (5.0, note_on(1, 64, 80)),
        )
        engine = self.make_engine(AutoClock())
        engine.play(timeline)
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual(adapter_owned(self.adapter), ())
        self.assertEqual(
            self.port.sent,
            [
                ("note_on", 1, 60, 80),
                ("note_on", 1, 64, 80),
                ("note_off", 1, 60, 0),
                ("note_off", 1, 64, 0),
            ],
        )
        self.assert_no_global_sweep()

    def test_completion_cleanup_reaches_live_note_publication(self):
        channel = LiveNoteState(runtime_epoch="completion-test")

        def observe():
            channel.publish_playback(
                available=True,
                owned_notes=self.adapter.owned_notes,
                sustain_channels=self.adapter.owned_sustain_channels,
            )

        timeline = make_timeline((0.0, note_on(1, 60, 80)))
        engine = PlaybackEngine(
            self.adapter, clock=AutoClock(), ownership_observer=observe
        )
        self.engines.append(engine)
        engine.play(timeline)
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual(channel.snapshot().playback_voices, ())

    def test_sustain_left_down_at_completion_is_released(self):
        timeline = make_timeline((0.0, note_on(1, 60, 80)), (1.0, sustain(1, 127)))
        engine = self.make_engine(AutoClock())
        engine.play(timeline)
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual(adapter_sustain(self.adapter), ())
        self.assertIn(("control_change", 1, 64, 0), self.port.sent)
        self.assert_no_global_sweep()


def adapter_owned(adapter):
    return adapter.owned_notes


def adapter_sustain(adapter):
    return adapter.owned_sustain_channels


class SingleActiveTests(EngineTestCase):
    def test_play_while_playing_rejected(self):
        clock = GatedClock()
        engine = self.make_engine(clock)
        engine.play(simple_session())
        self.assertTrue(wait_for(lambda: engine.events_sent == 1))
        with self.assertRaises(PlaybackAlreadyActiveError):
            engine.play(simple_session())
        self.assertIs(engine.state, PlaybackState.PLAYING)
        engine.stop()
        self.assertTrue(engine.wait(2.0))

    def test_play_while_paused_rejected(self):
        clock = GatedClock()
        engine = self.make_engine(clock)
        engine.play(simple_session())
        self.assertTrue(wait_for(lambda: engine.events_sent == 1))
        engine.pause()
        with self.assertRaises(PlaybackAlreadyActiveError):
            engine.play(simple_session())
        engine.stop()
        self.assertTrue(engine.wait(2.0))

    def test_play_after_completion_allowed(self):
        engine = self.make_engine(AutoClock())
        engine.play(simple_session())
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        engine.play(simple_session())
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual(self.port.sent.count(("note_on", 1, 60, 80)), 2)


class StopTests(EngineTestCase):
    def test_stop_during_active_notes_releases_owned_only(self):
        clock = GatedClock()
        engine = self.make_engine(clock)
        engine.play(simple_session())
        self.assertTrue(wait_for(lambda: engine.events_sent == 1))
        self.assertEqual(adapter_owned(self.adapter), ((1, 60),))
        self.assertIs(engine.state, PlaybackState.PLAYING)

        self.assertTrue(engine.stop())  # returns after bounded join
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.IDLE)
        self.assertEqual(adapter_owned(self.adapter), ())
        self.assertEqual(
            self.port.sent,
            [("note_on", 1, 60, 80), ("note_off", 1, 60, 0)],
        )
        self.assert_no_global_sweep()

    def test_stop_while_sustain_owned_releases_pedal_and_notes(self):
        timeline = make_timeline(
            (0.0, sustain(1, 127)),
            (0.0, note_on(1, 60, 80)),
            (10.0, note_off(1, 60)),
            (12.0, sustain(1, 0)),
        )
        clock = GatedClock()
        engine = self.make_engine(clock)
        engine.play(timeline)
        self.assertTrue(wait_for(lambda: engine.events_sent == 2))
        self.assertEqual(adapter_sustain(self.adapter), (1,))
        self.assertTrue(engine.stop())
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.IDLE)
        self.assertEqual(adapter_owned(self.adapter), ())
        self.assertEqual(adapter_sustain(self.adapter), ())
        # Pedal release precedes nothing else owned; both owned items released.
        self.assertIn(("control_change", 1, 64, 0), self.port.sent)
        self.assertIn(("note_off", 1, 60, 0), self.port.sent)
        self.assert_no_global_sweep()

    def test_stop_from_paused(self):
        clock = GatedClock()
        engine = self.make_engine(clock)
        engine.play(simple_session())
        self.assertTrue(wait_for(lambda: engine.events_sent == 1))
        engine.pause()
        self.assertTrue(engine.stop())
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.IDLE)
        self.assertEqual(adapter_owned(self.adapter), ())
        self.assert_no_global_sweep()

    def test_stop_is_idempotent_and_safe_when_idle(self):
        engine = self.make_engine(AutoClock())
        self.assertFalse(engine.stop())  # nothing active
        engine.play(simple_session())
        self.assertTrue(engine.wait(2.0))
        self.assertFalse(engine.stop())  # already terminal (COMPLETED)
        self.assertTrue(engine.stop() is False)


class PauseResumeTests(EngineTestCase):
    def test_pause_requires_playing(self):
        engine = self.make_engine(AutoClock())
        with self.assertRaises(PlaybackError):
            engine.pause()

    def test_resume_requires_paused(self):
        engine = self.make_engine(AutoClock())
        with self.assertRaises(PlaybackError):
            engine.resume()

    def test_pause_freezes_position_and_releases_owned_notes(self):
        timeline = make_timeline(
            (0.0, note_on(1, 60, 80)),
            (10.0, note_on(1, 64, 80)),
            (20.0, note_off(1, 60)),
            (20.0, note_off(1, 64)),
        )
        clock = GatedClock()
        engine = self.make_engine(clock)
        engine.play(timeline)
        # Advance to song position 10 (second note sounding), then pause.
        self.assertTrue(clock.wait_parked())
        clock.release()
        self.assertTrue(wait_for(lambda: engine.events_sent == 2))
        # The worker is now parked waiting for song position 20.
        self.assertTrue(clock.wait_parked())
        engine.pause()
        self.assertIs(engine.state, PlaybackState.PAUSED)
        self.assertEqual(engine.position_seconds, 10.0)
        # Release-on-pause (performed by the worker after waking): nothing
        # may keep sounding while paused, and a paused engine sends
        # nothing more no matter how long it stays paused.
        self.assertTrue(wait_for(lambda: adapter_owned(self.adapter) == ()))
        self.assertEqual(
            self.port.sent,
            [
                ("note_on", 1, 60, 80),
                ("note_on", 1, 64, 80),
                ("note_off", 1, 60, 0),
                ("note_off", 1, 64, 0),
            ],
        )
        self.assertFalse(engine.wait(0.3))  # still paused (not terminal)
        self.assertEqual(len(self.port.sent), 4)
        self.assert_no_global_sweep()

        # Resume continues from the frozen position to natural completion.
        engine.resume()
        self.assertIs(engine.state, PlaybackState.PLAYING)
        self.assertTrue(clock.wait_parked())
        clock.release()  # elapse the remaining wall delay (song 10 -> end)
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual(engine.events_sent, 4)
        self.assertEqual(engine.position_seconds, 20.0)

    def test_pause_observer_sees_applied_owned_note_cleanup(self):
        clock = GatedClock()
        observed = []
        engine = PlaybackEngine(
            self.adapter,
            clock=clock,
            event_observer=lambda command: observed.append(command.type),
        )
        self.engines.append(engine)
        engine.play(simple_session())
        self.assertTrue(wait_for(lambda: engine.events_sent == 1))
        engine.pause()
        self.assertTrue(wait_for(lambda: adapter_owned(self.adapter) == ()))
        self.assertIn(OutputCommandType.STOP_AUTOMATED_NOTES, observed)
        engine.stop()

    def test_pause_at_song_end_then_resume_completes(self):
        clock = GatedClock()
        engine = self.make_engine(clock)
        engine.play(simple_session())
        self.assertTrue(wait_for(lambda: engine.events_sent == 1))
        self.assertTrue(clock.wait_parked())
        engine.pause()
        self.assertEqual(engine.position_seconds, 0.0)
        engine.resume()
        self.assertTrue(clock.wait_parked())
        clock.release()
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)


class FailureTests(EngineTestCase):
    def test_send_failure_lands_in_failed_with_cleanup(self):
        timeline = make_timeline(
            (0.0, note_on(1, 60, 80)),
            (10.0, note_on(1, 64, 80)),
            (20.0, note_off(1, 60)),
            (20.0, note_off(1, 64)),
        )
        clock = GatedClock()
        inner = self.adapter
        failing = FailOnNthNoteOnAdapter(inner, fail_on=2)
        engine = PlaybackEngine(failing, clock=clock)
        self.engines.append(engine)
        engine.play(timeline)
        self.assertTrue(wait_for(lambda: engine.events_sent == 1))
        self.assertTrue(clock.wait_parked())
        clock.release()  # second NOTE_ON fails
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertIsNotNone(engine.error)
        self.assertIn("note_on", engine.error)
        # Best-effort cleanup released the still-owned first note.
        self.assertEqual(adapter_owned(inner), ())
        self.assertIn(("note_off", 1, 60, 0), self.port.sent)
        self.assert_no_global_sweep()
        # Terminal FAILED allows a fresh session (fresh gate: release the
        # shared clock's wait for the second session's first timed event).
        engine2 = self.make_engine(clock)
        engine2.play(simple_session())
        self.assertTrue(wait_for(lambda: engine2.events_sent == 1))
        self.assertTrue(clock.wait_parked())
        clock.release()
        self.assertTrue(engine2.wait(2.0))
        self.assertIs(engine2.state, PlaybackState.COMPLETED)

    def test_failure_cleanup_reaches_live_note_publication(self):
        channel = LiveNoteState(runtime_epoch="failure-test")
        failing = FailOnNthNoteOnAdapter(self.adapter, fail_on=2)

        def observe():
            channel.publish_playback(
                available=True,
                owned_notes=failing.owned_notes,
                sustain_channels=failing.owned_sustain_channels,
            )

        engine = PlaybackEngine(
            failing,
            clock=AutoClock(),
            ownership_observer=observe,
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (1.0, note_on(1, 64, 80)),
            )
        )
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertEqual(channel.snapshot().playback_voices, ())

    def test_send_failure_on_first_event_still_fails_cleanly(self):
        failing = FailOnNthNoteOnAdapter(self.adapter, fail_on=1)
        engine = PlaybackEngine(failing, clock=AutoClock())
        self.engines.append(engine)
        engine.play(simple_session())
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertEqual(adapter_owned(self.adapter), ())


class PartialCleanupReconciliationTests(EngineTestCase):
    def make_failing_adapter(
        self, *, fail_notes=(), fail_voices=(), fail_sustain_channels=()
    ):
        backend = SelectiveReleaseFailureBackend(
            ["Roland Digital Piano 1"],
            fail_notes=fail_notes,
            fail_voices=fail_voices,
            fail_sustain_channels=fail_sustain_channels,
        )
        adapter = MidiOutputAdapter(backend, matcher="roland")
        adapter.open()
        self.addCleanup(adapter.close)
        return adapter, backend

    def make_publication_observer(self, adapter):
        channel = LiveNoteState(runtime_epoch="partial-cleanup-test")

        def reconcile():
            channel.publish_playback(
                available=True,
                owned_notes=adapter.owned_notes,
                sustain_channels=adapter.owned_sustain_channels,
            )

        return channel, reconcile

    def test_natural_completion_partial_cleanup_publishes_remaining_sustain(self):
        adapter, _backend = self.make_failing_adapter(fail_sustain_channels={1})
        channel, reconcile = self.make_publication_observer(adapter)
        engine = PlaybackEngine(
            adapter, clock=AutoClock(), ownership_observer=reconcile
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (0.0, sustain(1, 127)),
            )
        )
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertIn("cleanup (completion) failed", engine.error)
        self.assertEqual(adapter.owned_notes, ())
        self.assertEqual(adapter.owned_sustain_channels, (1,))
        self.assertEqual(channel.snapshot().playback_voices, ())
        self.assertEqual(channel.snapshot().playback_sustain_channels, (1,))

    def test_explicit_stop_partial_cleanup_publishes_remaining_note(self):
        adapter, _backend = self.make_failing_adapter(fail_notes={62})
        channel, reconcile = self.make_publication_observer(adapter)
        clock = GatedClock()
        engine = PlaybackEngine(
            adapter, clock=clock, ownership_observer=reconcile
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (0.0, note_on(1, 62, 80)),
                (10.0, note_off(1, 60)),
            )
        )
        self.assertTrue(wait_for(lambda: engine.events_sent == 2))
        self.assertTrue(engine.stop())
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertIn("cleanup (stop) failed", engine.error)
        self.assertEqual(adapter.owned_notes, ((1, 62),))
        self.assertEqual(channel.snapshot().playback_voices, ((1, 62),))

    def test_pause_partial_cleanup_publishes_remaining_note_and_fails(self):
        adapter, _backend = self.make_failing_adapter(fail_notes={62})
        channel, reconcile = self.make_publication_observer(adapter)
        clock = GatedClock()
        engine = PlaybackEngine(
            adapter, clock=clock, ownership_observer=reconcile
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (0.0, note_on(1, 62, 80)),
                (10.0, note_off(1, 60)),
            )
        )
        self.assertTrue(wait_for(lambda: engine.events_sent == 2))
        engine.pause()
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertIn("cleanup (pause) failed", engine.error)
        self.assertEqual(channel.snapshot().playback_voices, ((1, 62),))

    def test_failure_cleanup_publishes_partial_result_and_keeps_failure(self):
        adapter, _backend = self.make_failing_adapter(fail_notes={60})
        channel, reconcile = self.make_publication_observer(adapter)
        failing = FailOnNthNoteOnAdapter(adapter, fail_on=2)
        engine = PlaybackEngine(
            failing, clock=AutoClock(), ownership_observer=reconcile
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (1.0, note_on(1, 64, 80)),
            )
        )
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertIn("output command failed", engine.error)
        self.assertIn("cleanup failed", engine.error)
        self.assertEqual(adapter.owned_notes, ((1, 60),))
        self.assertEqual(channel.snapshot().playback_voices, ((1, 60),))

    def test_cleanup_failure_before_first_release_preserves_publication(self):
        adapter, _backend = self.make_failing_adapter(fail_notes={60})
        channel, reconcile = self.make_publication_observer(adapter)
        engine = PlaybackEngine(
            adapter, clock=AutoClock(), ownership_observer=reconcile
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (0.0, note_on(1, 64, 80)),
            )
        )
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertEqual(adapter.owned_notes, ((1, 60), (1, 64)))
        self.assertEqual(
            channel.snapshot().playback_voices, ((1, 60), (1, 64))
        )

    def test_successful_sustain_release_then_note_cleanup_failure_is_accurate(self):
        adapter, _backend = self.make_failing_adapter(fail_notes={60})
        channel, reconcile = self.make_publication_observer(adapter)
        engine = PlaybackEngine(
            adapter, clock=AutoClock(), ownership_observer=reconcile
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (0.0, sustain(1, 127)),
                (0.0, sustain(1, 0)),
            )
        )
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertEqual(channel.snapshot().playback_voices, ((1, 60),))
        self.assertEqual(channel.snapshot().playback_sustain_channels, ())

    def test_same_pitch_two_channels_stays_active_after_one_voice_fails(self):
        adapter, _backend = self.make_failing_adapter(fail_voices={(2, 60)})
        channel, reconcile = self.make_publication_observer(adapter)
        engine = PlaybackEngine(
            adapter, clock=AutoClock(), ownership_observer=reconcile
        )
        self.engines.append(engine)
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (0.0, note_on(2, 60, 80)),
            )
        )
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.FAILED)
        self.assertEqual(adapter.owned_notes, ((2, 60),))
        self.assertEqual(channel.snapshot().playback_voices, ((2, 60),))


class TempoScaleTests(EngineTestCase):
    def test_scale_halves_speed_doubles_wall_duration(self):
        clock = AutoClock()
        spy = ClockSpyAdapter(self.adapter, clock)
        engine = PlaybackEngine(spy, clock=clock)
        self.engines.append(engine)
        engine.play(simple_session(), tempo_scale=0.5)
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)
        self.assertEqual([t for t, _ in spy.send_times], [0.0, 20.0])

    def test_scale_double_speed_halves_wall_duration(self):
        clock = AutoClock()
        spy = ClockSpyAdapter(self.adapter, clock)
        engine = PlaybackEngine(spy, clock=clock)
        self.engines.append(engine)
        engine.play(simple_session(), tempo_scale=2.0)
        self.assertTrue(engine.wait(2.0))
        self.assertEqual([t for t, _ in spy.send_times], [0.0, 5.0])

    def test_scale_bounds_validated_synchronously_before_start(self):
        engine = self.make_engine(AutoClock())
        for bad in (0.1, 5.0, -1.0):
            with self.assertRaises(TempoScaleError):
                engine.play(simple_session(), tempo_scale=bad)
        self.assertIs(engine.state, PlaybackState.IDLE)
        self.assertEqual(self.port.sent, [])
        # A valid scale still works afterwards (no engine corruption).
        engine.play(simple_session(), tempo_scale=0.25)
        self.assertTrue(engine.wait(2.0))
        self.assertIs(engine.state, PlaybackState.COMPLETED)

    def test_tempo_scale_property(self):
        engine = self.make_engine(AutoClock())
        self.assertEqual(engine.tempo_scale, 1.0)
        engine.play(simple_session(), tempo_scale=0.5)
        self.assertEqual(engine.tempo_scale, 0.5)
        engine.wait(2.0)


class PositionTests(EngineTestCase):
    def test_position_is_zero_before_play(self):
        engine = self.make_engine(AutoClock())
        self.assertEqual(engine.position_seconds, 0.0)
        self.assertEqual(engine.duration, 0.0)

    def test_position_advances_and_clamps_to_duration(self):
        clock = AutoClock()
        engine = self.make_engine(clock)
        engine.play(simple_session())
        self.assertTrue(engine.wait(2.0))
        self.assertEqual(engine.position_seconds, 10.0)  # clamped to duration
        self.assertEqual(engine.duration, 10.0)


class StateStoreSeparationTests(EngineTestCase):
    def test_playback_never_mutates_the_statestore(self):
        store = StateStore()
        before = store.snapshot()
        engine = self.make_engine(AutoClock())
        engine.play(
            make_timeline(
                (0.0, note_on(1, 60, 80)),
                (1.0, sustain(1, 127)),
                (2.0, note_off(1, 60)),
            )
        )
        self.assertTrue(engine.wait(2.0))
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(store.events_received(), 0)

    def test_playback_sources_reference_no_statestore_or_input_surface(self):
        # Code-shaped patterns only: docstrings may legitimately MENTION
        # the boundaries — code must not reference them.
        forbidden = (
            "piano_core.state",
            "from piano_core.midi.events",
            "StateStore(",
            "apply_event(",
            "MidiInputAdapter(",
            "MidiInputEvent(",
            "open_input(",
            "iter_pending(",
            "list_input_ports(",
        )
        offenders = []
        for path in sorted(glob.glob(str(_PLAYBACK_DIR / "*.py"))):
            text = Path(path).read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{Path(path).name}: {token!r}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
