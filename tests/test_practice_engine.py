"""PIANO-P015: practice state machine tests (ADR-PRACTICE-V1 D3).

The engine is exercised directly: canonical input events plus the
authoritative pressed set, exactly the shape the manager feeds it.
"""

import unittest

from piano_core.midi.events import note_off as ie_off
from piano_core.midi.events import note_on as ie_on
from piano_core.midi.events import sustain as ie_sustain
from piano_core.practice.engine import PracticeEngine
from piano_core.practice.model import (
    PracticeAlreadyActiveError,
    PracticeSession,
    PracticeState,
)
from piano_core.practice.plan import build_practice_plan
from piano_core.playback.timeline import Timeline, TimelineEvent
from piano_core.midi.output_events import note_on as out_note_on


def _plan(*notes_per_step):
    """Build a plan from onsets 0..n with the given notes per step."""
    events = []
    for onset, notes in enumerate(notes_per_step):
        for note in notes:
            events.append(TimelineEvent(float(onset), out_note_on(1, note, 80)))
    return build_practice_plan(
        Timeline(events=tuple(events), duration=float(len(notes_per_step)))
    )


class EngineHarness(unittest.TestCase):
    def setUp(self):
        self.targets: list[tuple[int, ...]] = []
        self.clears: list[bool] = []
        self.engine = PracticeEngine(
            on_target=self.targets.append, on_clear=lambda: self.clears.append(True)
        )
        self.addCleanup(self.engine.stop)

    def press(self, note, pressed):
        self.engine.observe_event(ie_on(1, note, 90, 0.0), frozenset(pressed))

    def release(self, note, pressed):
        self.engine.observe_event(ie_off(1, note, 0, 0.0), frozenset(pressed))


class TerminalReleaseHookTests(unittest.TestCase):
    """F-R1: the engine notifies release exactly once, and only on the
    terminal transitions WAITING -> COMPLETED / WAITING -> FAILED —
    never on step advances and never on stop() (the manager releases
    explicitly there, so a post-terminal stop is a no-op)."""

    def _engine_with_release_log(self):
        releases: list[bool] = []
        engine = PracticeEngine(on_release=lambda: releases.append(True))
        self.addCleanup(engine.stop)
        return engine, releases

    def test_release_fires_once_on_completion_not_on_step_or_stop(self):
        engine, releases = self._engine_with_release_log()
        engine.start_session(_plan((60,), (62,)), "s", None)
        engine.observe_event(ie_on(1, 60, 90, 0.0), frozenset({60}))
        self.assertEqual(releases, [])  # step advance is NOT terminal
        engine.observe_event(ie_on(1, 62, 90, 0.0), frozenset({60, 62}))
        self.assertEqual(releases, [True])  # exactly once at COMPLETED
        engine.stop()  # no second release notification
        self.assertEqual(releases, [True])

    def test_release_fires_once_on_failure_and_stays_once(self):
        engine, releases = self._engine_with_release_log()
        engine.start_session(_plan((60,)), "s", None)
        engine.fail("boom")
        self.assertEqual(releases, [True])
        engine.fail("boom again")  # no-op from FAILED
        engine.stop()
        self.assertEqual(releases, [True])


class LifecycleTests(EngineHarness):
    def test_idle_start_enters_waiting_with_first_target(self):
        self.assertIs(self.engine.state, PracticeState.IDLE)
        self.engine.start_session(_plan((60,), (62,)), "song-1", "Song One")
        self.assertIs(self.engine.state, PracticeState.WAITING)
        self.assertEqual(self.targets, [(60,)])
        snapshot = self.engine.snapshot(available=True, midi_connection="CONNECTED")
        self.assertEqual(snapshot.state, PracticeState.WAITING)
        self.assertEqual(snapshot.song_id, "song-1")
        self.assertEqual(snapshot.title, "Song One")
        self.assertEqual(snapshot.step_index, 0)
        self.assertEqual(snapshot.total_steps, 2)
        self.assertIsNotNone(snapshot.started_at)
        self.assertTrue(snapshot.available)

    def test_start_while_waiting_is_typed_conflict(self):
        self.engine.start_session(_plan((60,)), "s", None)
        with self.assertRaises(PracticeAlreadyActiveError):
            self.engine.start_session(_plan((62,)), "t", None)

    def test_start_from_completed_and_failed_is_legal(self):
        plan = _plan((60,))
        self.engine.start_session(plan, "s", None)
        self.press(60, {60})
        self.assertIs(self.engine.state, PracticeState.COMPLETED)
        self.engine.start_session(plan, "t", None)
        self.assertIs(self.engine.state, PracticeState.WAITING)
        self.engine.fail("boom")
        self.assertIs(self.engine.state, PracticeState.FAILED)
        self.engine.start_session(plan, "u", None)
        self.assertIs(self.engine.state, PracticeState.WAITING)


class SingleNoteTests(EngineHarness):
    def test_correct_single_note_advances(self):
        self.engine.start_session(_plan((60,), (62,), (64,)), "s", None)
        self.press(60, {60})
        self.assertEqual(self.targets, [(60,), (62,)])
        snapshot = self.engine.snapshot(available=True, midi_connection="X")
        self.assertEqual(snapshot.completed_steps, 1)
        self.assertEqual(snapshot.step_index, 1)

    def test_wrong_note_never_advances_and_counts_attempt(self):
        self.engine.start_session(_plan((60,), (62,)), "s", None)
        self.press(61, {61})
        self.press(62, {61, 62})
        self.assertEqual(self.targets, [(60,)])
        snapshot = self.engine.snapshot(available=True, midi_connection="X")
        self.assertEqual(snapshot.incorrect_attempts, 2)
        self.assertEqual(snapshot.completed_steps, 0)

    def test_sustain_never_advances_anything(self):
        self.engine.start_session(_plan((60,)), "s", None)
        self.engine.observe_event(ie_sustain(1, 100, 0.0), frozenset())
        self.engine.observe_event(ie_sustain(1, 0, 0.0), frozenset())
        self.assertIs(self.engine.state, PracticeState.WAITING)
        self.assertEqual(self.engine.snapshot(available=True, midi_connection="X").incorrect_attempts, 0)


class RepeatedNoteTests(EngineHarness):
    def test_repeated_note_requires_release_and_fresh_press(self):
        self.engine.start_session(_plan((60,), (60,)), "s", None)
        self.press(60, {60})  # completes step 1
        self.assertEqual(self.targets, [(60,), (60,)])
        # Still holding C4: the held note is DISARMED at step entry, so a
        # redundant NOTE_ON while held can never satisfy step 2.
        self.press(60, {60})
        self.assertIs(self.engine.state, PracticeState.WAITING)
        # Release re-arms; a fresh press completes.
        self.release(60, set())
        self.press(60, {60})
        self.assertIs(self.engine.state, PracticeState.COMPLETED)

    def test_held_at_entry_note_is_disarmed_for_first_step(self):
        self.engine.start_session(
            _plan((60,), (62,)), "s", None, pressed_now=frozenset({60})
        )
        self.press(60, {60, 64})
        self.assertIs(self.engine.state, PracticeState.WAITING)  # disarmed
        self.release(60, {64})
        self.press(60, {60, 64})
        self.assertEqual(self.targets[-1], (62,))


class ChordTests(EngineHarness):
    def test_chord_advances_when_all_required_held_order_independent(self):
        self.engine.start_session(_plan((60, 64, 67), (72,)), "s", None)
        self.press(67, {67})
        self.press(60, {60, 67})
        self.press(64, {60, 64, 67})
        self.assertEqual(self.targets, [(60, 64, 67), (72,)])

    def test_partial_chord_never_completes(self):
        self.engine.start_session(_plan((60, 64, 67)), "s", None)
        self.press(60, {60})
        self.press(64, {60, 64})
        self.assertIs(self.engine.state, PracticeState.WAITING)
        self.release(64, {60})
        self.assertIs(self.engine.state, PracticeState.WAITING)

    def test_extra_wrong_note_is_tolerated_but_never_advances_alone(self):
        self.engine.start_session(_plan((60, 64)), "s", None)
        self.press(70, {70})
        self.assertIs(self.engine.state, PracticeState.WAITING)
        # Extra held note does not prevent completion once all required
        # notes are simultaneously held (documented learner-friendly policy).
        self.press(60, {60, 70})
        self.press(64, {60, 64, 70})
        self.assertIs(self.engine.state, PracticeState.COMPLETED)

    def test_repeated_chord_overlapping_notes_require_rearm(self):
        # Step 1: C4+E4+G4; step 2: C4+F4+A4 (C4 overlaps).
        self.engine.start_session(_plan((60, 64, 67), (60, 65, 69)), "s", None)
        self.press(60, {60})
        self.press(64, {60, 64})
        self.press(67, {60, 64, 67})
        self.assertEqual(self.targets[-1], (60, 65, 69))
        # C4 is still held from step 1 -> disarmed for step 2.
        self.press(65, {60, 64, 65, 67})
        self.press(69, {60, 64, 65, 67, 69})
        self.assertIs(self.engine.state, PracticeState.WAITING)
        self.release(60, {64, 65, 67, 69})
        self.press(60, {60, 64, 65, 67, 69})
        self.assertIs(self.engine.state, PracticeState.COMPLETED)


class CompletionStopFailureTests(EngineHarness):
    def test_final_target_completes_and_clears_display(self):
        self.engine.start_session(_plan((60,), (62,)), "s", None)
        self.press(60, {60})
        self.press(62, {62})
        self.assertIs(self.engine.state, PracticeState.COMPLETED)
        self.assertEqual(self.clears, [True])
        snapshot = self.engine.snapshot(available=True, midi_connection="X")
        self.assertIsNone(snapshot.step_index)
        self.assertIsNone(snapshot.current_target)
        self.assertEqual(snapshot.completed_steps, 2)
        self.assertEqual(snapshot.total_steps, 2)
        self.assertIsNotNone(snapshot.ended_at)
        # Completed-session summary stays observable until stop/start.
        self.assertEqual(snapshot.song_id, "s")

    def test_stop_from_waiting_resets_to_idle_and_clears(self):
        self.engine.start_session(_plan((60,), (62,)), "s", None)
        self.assertTrue(self.engine.stop())
        self.assertIs(self.engine.state, PracticeState.IDLE)
        self.assertEqual(self.clears, [True])
        snapshot = self.engine.snapshot(available=True, midi_connection="X")
        self.assertIsNone(snapshot.song_id)
        self.assertEqual(snapshot.completed_steps, 0)

    def test_stop_is_idempotent(self):
        self.assertFalse(self.engine.stop())  # IDLE: nothing to stop
        self.engine.start_session(_plan((60,)), "s", None)
        self.assertTrue(self.engine.stop())
        self.assertFalse(self.engine.stop())

    def test_stop_from_completed_discards_summary(self):
        self.engine.start_session(_plan((60,)), "s", None)
        self.press(60, {60})
        self.assertTrue(self.engine.stop())
        snapshot = self.engine.snapshot(available=True, midi_connection="X")
        self.assertIs(self.engine.state, PracticeState.IDLE)
        self.assertIsNone(snapshot.song_id)

    def test_fail_is_visible_and_clears_display(self):
        self.engine.start_session(_plan((60,), (62,)), "s", None)
        self.engine.fail("internal practice error: boom")
        self.assertIs(self.engine.state, PracticeState.FAILED)
        self.assertEqual(self.clears, [True])
        snapshot = self.engine.snapshot(available=True, midi_connection="X")
        self.assertIn("boom", snapshot.error)
        # Observation after failure is ignored (no resurrection).
        self.press(60, {60})
        self.assertIs(self.engine.state, PracticeState.FAILED)

    def test_fail_from_idle_is_ignored(self):
        self.engine.fail("nothing")
        self.assertIs(self.engine.state, PracticeState.IDLE)


class BoundaryTests(EngineHarness):
    def test_observation_requires_canonical_event(self):
        with self.assertRaises(TypeError):
            self.engine.observe_event(("note_on", 60), frozenset())

    def test_observation_requires_frozenset_pressed(self):
        self.engine.start_session(_plan((60,)), "s", None)
        with self.assertRaises(TypeError):
            self.engine.observe_event(ie_on(1, 60, 90, 0.0), {60})

    def test_start_requires_plan_and_song_id(self):
        with self.assertRaises(TypeError):
            self.engine.start_session("not-a-plan", "s", None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.engine.start_session(_plan((60,)), "  ", None)

    def test_empty_plan_fails_closed(self):
        with self.assertRaises(ValueError):
            self.engine.start_session(_plan(), "s", None)


if __name__ == "__main__":
    unittest.main()
