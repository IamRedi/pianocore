"""PIANO-UI-004 deterministic Learning Engine V2 tests."""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from piano_core.api.errors import ApiError, translate_exception
from piano_core.api.handlers import learning_start
from piano_core.api.serialization import practice_session_payload
from piano_core.midi.events import note_off, note_on
from piano_core.midi.output_events import note_on as timeline_note_on
from piano_core.playback.timeline import Timeline, TimelineEvent
from piano_core.practice.engine import PracticeEngine
from piano_core.practice.manager import PracticeManager
from piano_core.practice.model import (
    LearningMode,
    LearningOutcome,
    PracticePlan,
    PracticeInvalidStateError,
    PracticeSection,
    PracticeStaleCommandError,
    PracticeState,
    PracticeTarget,
)
from piano_core.practice.plan import build_practice_plan, select_practice_section
from piano_core.state import MidiConnectionState, StateStore


def _target(notes, onset):
    return PracticeTarget(tuple(notes), onset)


def _plan(*targets, duration=10.0):
    return PracticePlan(tuple(targets), "fixture.mid", 0, duration)


def _on(note, timestamp=1.0):
    return note_on(1, note, 80, timestamp)


def _off(note, timestamp=1.1):
    return note_off(1, note, 0, timestamp)


class LearningModelAndSectionTests(unittest.TestCase):
    def test_target_identity_is_stable_and_content_derived(self):
        first = _target((60, 64), 1.25)
        second = _target((60, 64), 1.25)
        self.assertEqual(first.target_id, second.target_id)
        self.assertNotEqual(first.target_id, _target((60, 67), 1.25).target_id)

    def test_section_is_half_open(self):
        section = PracticeSection(1.0, 2.0)
        self.assertTrue(section.contains(1.0))
        self.assertTrue(section.contains(1.999))
        self.assertFalse(section.contains(2.0))

    def test_invalid_section_bounds_fail_closed(self):
        for start, end in (
            (-1, 2),
            (2, 2),
            (3, 2),
            (False, 2),
            (float("nan"), 2),
            (1, float("inf")),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    PracticeSection(start, end)

    def test_section_projection_preserves_order_and_identity(self):
        source = _plan(
            _target((60,), 0.0),
            _target((62,), 1.0),
            _target((64,), 2.0),
            _target((65,), 3.0),
        )
        projected = select_practice_section(source, PracticeSection(1.0, 3.0))
        self.assertEqual([target.notes for target in projected.targets], [(62,), (64,)])
        self.assertEqual(projected.targets[0].target_id, source.targets[1].target_id)
        self.assertEqual(source.total_steps, 4)

    def test_empty_section_is_explicit_empty_plan(self):
        projected = select_practice_section(
            _plan(_target((60,), 0.0)), PracticeSection(2.0, 3.0)
        )
        self.assertEqual(projected.total_steps, 0)


def _timeline(*events, duration=10.0, source="fixture.mid"):
    return Timeline(events=tuple(events), duration=duration, source=source)


class LearningTargetIdentityTests(unittest.TestCase):
    """U4-F5: distinct canonical targets cannot share a deterministic ID."""

    def test_reviewer_collision_pair_gets_distinct_plan_ids(self):
        # The exact reviewer collision class: the SAME required note at
        # two DISTINCT canonical onsets whose :.9f display rounding is
        # identical — distinct targets, one retired identity.
        first_onset = 1.0 + 4e-10
        second_onset = 1.0 + 4.9e-10
        self.assertNotEqual(first_onset, second_onset)
        self.assertEqual(f"{first_onset:.9f}", f"{second_onset:.9f}")
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(first_onset, timeline_note_on(1, 60, 80)),
                TimelineEvent(second_onset, timeline_note_on(1, 60, 80)),
                duration=2.0,
                source="collision.mid",
            )
        )
        self.assertEqual(plan.total_steps, 2)
        self.assertNotEqual(plan.targets[0].target_id, plan.targets[1].target_id)
        self.assertEqual(plan.targets[0].target_id, "target:0:60")
        self.assertEqual(plan.targets[1].target_id, "target:1:60")

    def test_same_canonical_target_rebuilt_twice_keeps_identity(self):
        def build():
            return build_practice_plan(
                _timeline(
                    TimelineEvent(0.5, timeline_note_on(1, 60, 80)),
                    TimelineEvent(1.25, timeline_note_on(2, 64, 70)),
                    TimelineEvent(2.0, timeline_note_on(1, 67, 80)),
                    duration=3.0,
                    source="stable.mid",
                )
            )

        self.assertEqual(
            [target.target_id for target in build().targets],
            [target.target_id for target in build().targets],
        )
        self.assertEqual(build().targets[1].target_id, "target:1:64")

    def test_unplanned_target_identity_is_exact_and_collision_free(self):
        first = _target((60,), 1.0 + 4e-10)
        second = _target((60,), 1.0 + 4.9e-10)
        # The retired :.9f identity collapsed exactly this canonical pair.
        self.assertEqual(f"{first.onset_seconds:.9f}", f"{second.onset_seconds:.9f}")
        self.assertNotEqual(first.target_id, second.target_id)
        self.assertEqual(first.target_id, _target((60,), 1.0 + 4e-10).target_id)

    def test_same_onset_chord_group_is_one_stable_id(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(1.0, timeline_note_on(1, 60, 80)),
                TimelineEvent(1.0, timeline_note_on(1, 64, 80)),
                TimelineEvent(1.0, timeline_note_on(2, 67, 80)),
                duration=2.0,
                source="chord.mid",
            )
        )
        self.assertEqual(plan.total_steps, 1)
        self.assertEqual(plan.targets[0].target_id, "target:0:60-64-67")

    def test_distinct_canonical_groups_get_distinct_ids(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, timeline_note_on(1, 60, 80)),
                TimelineEvent(1.0, timeline_note_on(1, 60, 80)),
                TimelineEvent(2.0, timeline_note_on(1, 62, 80)),
                duration=3.0,
                source="distinct.mid",
            )
        )
        ids = [target.target_id for target in plan.targets]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, ["target:0:60", "target:1:60", "target:2:62"])

    def test_section_filtering_keeps_full_song_ordinals(self):
        source = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, timeline_note_on(1, 60, 80)),
                TimelineEvent(1.0, timeline_note_on(1, 62, 80)),
                TimelineEvent(2.0, timeline_note_on(1, 64, 80)),
                duration=3.0,
                source="section.mid",
            )
        )
        projected = select_practice_section(source, PracticeSection(1.0, 3.0))
        self.assertEqual([target.ordinal for target in projected.targets], [1, 2])
        self.assertEqual(projected.targets[0].target_id, source.targets[1].target_id)

    def test_invalid_ordinals_fail_closed(self):
        for ordinal in (-1, True, 1.5, "0"):
            with self.subTest(ordinal=ordinal):
                with self.assertRaises(ValueError):
                    PracticeTarget((60,), 0.0, ordinal=ordinal)


class LearningEngineTests(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger(f"test.learning.{id(self)}")
        logger.disabled = True
        self.engine = PracticeEngine(
            logger=logger, now_fn=lambda: "2026-09-17T08:00:00+00:00"
        )

    def start(self, targets=None, **kwargs):
        targets = targets or (_target((60,), 0.0), _target((62,), 1.0))
        self.engine.start_session(
            _plan(*targets), "song", "Song", frozenset(), session_id="session-1", **kwargs
        )
        return self.engine.snapshot(available=True, midi_connection="CONNECTED")

    def snapshot(self, connection="CONNECTED"):
        return self.engine.snapshot(available=True, midi_connection=connection)

    def test_start_exposes_recoverable_identity_and_version(self):
        state = self.start()
        self.assertEqual(state.session_id, "session-1")
        self.assertEqual(state.mode, LearningMode.WAIT_FOR_NOTE)
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.version, 1)
        # Directly constructed targets (no plan ordinal) use the exact
        # binary onset representation — never float-display rounding.
        self.assertEqual(
            state.current_target.target_id, f"target:{(0.0).hex()}:60"
        )

    def test_single_correct_note_advances(self):
        self.start()
        self.engine.observe_event(_on(60), frozenset({60}))
        state = self.snapshot()
        self.assertEqual(state.completed_steps, 1)
        self.assertEqual(state.current_target.notes, (62,))
        self.assertEqual(state.latest_outcome, LearningOutcome.CORRECT)

    def test_incorrect_note_never_advances(self):
        self.start()
        self.engine.observe_event(_on(61), frozenset({61}))
        state = self.snapshot()
        self.assertEqual(state.completed_steps, 0)
        self.assertEqual(state.incorrect_attempts, 1)
        self.assertEqual(state.latest_outcome, LearningOutcome.INCORRECT)

    def test_repeated_incorrect_notes_are_each_recorded(self):
        self.start()
        self.engine.observe_event(_on(61), frozenset({61}))
        self.engine.observe_event(_on(61, 2.0), frozenset({61}))
        self.assertEqual(self.snapshot().incorrect_attempts, 2)

    def test_chord_accepts_both_press_orders(self):
        for order in ((60, 64, 67), (67, 60, 64)):
            with self.subTest(order=order):
                engine = PracticeEngine()
                engine.start_session(_plan(_target((60, 64, 67), 0.0)), "s", None)
                held = set()
                for note in order:
                    held.add(note)
                    engine.observe_event(_on(note), frozenset(held))
                self.assertEqual(
                    engine.snapshot(available=True, midi_connection="CONNECTED").state,
                    PracticeState.COMPLETED,
                )

    def test_missing_chord_note_does_not_advance(self):
        self.start((_target((60, 64, 67), 0.0),))
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_event(_on(64), frozenset({60, 64}))
        state = self.snapshot()
        self.assertEqual(state.state, PracticeState.WAITING)
        self.assertEqual(state.accepted_notes, (60, 64))

    def test_extra_note_is_recorded_but_does_not_block_later_chord(self):
        self.start((_target((60, 64), 0.0),))
        self.engine.observe_event(_on(61), frozenset({61}))
        self.engine.observe_event(_on(60), frozenset({60, 61}))
        self.engine.observe_event(_on(64), frozenset({60, 61, 64}))
        state = self.snapshot()
        self.assertEqual(state.state, PracticeState.COMPLETED)
        self.assertEqual(state.incorrect_attempts, 1)

    def test_repeat_until_correct_requires_fresh_consecutive_presses(self):
        self.start(
            (_target((60,), 0.0),),
            mode=LearningMode.REPEAT_UNTIL_CORRECT,
            required_repetitions=3,
        )
        for index in range(2):
            self.engine.observe_event(_on(60, index + 1), frozenset({60}))
            state = self.snapshot()
            self.assertEqual(state.state, PracticeState.WAITING)
            self.assertEqual(state.current_repetition, index + 1)
            self.engine.observe_event(_off(60, index + 1.1), frozenset())
        self.engine.observe_event(_on(60, 3.0), frozenset({60}))
        state = self.snapshot()
        self.assertEqual(state.state, PracticeState.COMPLETED)
        self.assertEqual(state.successful_repetitions, 3)

    def test_incorrect_note_resets_repeat_streak(self):
        self.start(
            (_target((60,), 0.0),),
            mode=LearningMode.REPEAT_UNTIL_CORRECT,
            required_repetitions=2,
        )
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_event(_on(61, 2.0), frozenset({60, 61}))
        state = self.snapshot()
        self.assertEqual(state.current_repetition, 0)
        self.assertEqual(state.failed_repetitions, 1)
        self.assertEqual(state.completed_steps, 0)

    def test_disconnect_clears_attempt_and_blocks_stale_advance(self):
        self.start((_target((60,), 0.0),))
        self.engine.observe_input_availability(False, frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))
        state = self.snapshot("DISCONNECTED")
        self.assertEqual(state.state, PracticeState.WAITING)
        self.assertEqual(state.completed_steps, 0)
        self.assertEqual(state.latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)

    def test_reconnect_rearms_target_from_authoritative_empty_state(self):
        self.start((_target((60,), 0.0),))
        self.engine.observe_input_availability(False, frozenset())
        self.engine.observe_input_availability(True, frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_restart_target_keeps_progress_and_advances_version(self):
        state = self.start()
        self.engine.restart_target(frozenset())
        restarted = self.snapshot()
        self.assertEqual(restarted.completed_steps, 0)
        self.assertGreater(restarted.version, state.version)

    def test_restart_section_resets_progress_and_attempts(self):
        self.start()
        self.engine.observe_event(_on(61), frozenset({61}))
        self.engine.observe_event(_on(60), frozenset({60, 61}))
        self.engine.restart_section(frozenset())
        state = self.snapshot()
        self.assertEqual(state.step_index, 0)
        self.assertEqual(state.completed_steps, 0)
        self.assertEqual(state.incorrect_attempts, 0)

    def test_stop_returns_explicit_runtime_inactive_truth(self):
        self.start()
        self.engine.stop()
        state = self.snapshot()
        self.assertEqual(state.state, PracticeState.IDLE)
        self.assertIsNone(state.session_id)
        self.assertIsNone(state.current_target)

    def test_zero_duration_target_is_valid_learning_material(self):
        self.engine.start_session(
            _plan(_target((60,), 0.0), duration=0.0),
            "song",
            "Zero duration",
            session_id="zero-duration",
        )
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_restart_commands_require_an_active_session(self):
        with self.assertRaises(PracticeInvalidStateError):
            self.engine.restart_target(frozenset())
        with self.assertRaises(PracticeInvalidStateError):
            self.engine.restart_section(frozenset())

    def test_transition_processing_is_lightweight(self):
        targets = tuple(_target((60 + (index % 12),), float(index)) for index in range(500))
        self.start(targets)
        started = time.perf_counter()
        for index in range(500):
            note = 60 + (index % 12)
            self.engine.observe_event(_on(note, float(index)), frozenset({note}))
            if index < 499:
                self.engine.observe_event(_off(note, index + 0.1), frozenset())
        elapsed = time.perf_counter() - started
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)
        self.assertLess(elapsed, 1.0)


class LearningInputAvailabilityTests(unittest.TestCase):
    """U4-F1: no grading of any kind while authoritative input is unavailable.

    Retained regression for the exact independent reproduction:
    disconnect -> release -> press while still DISCONNECTED.
    """

    def setUp(self):
        logger = logging.getLogger(f"test.learning.avail.{id(self)}")
        logger.disabled = True
        self.engine = PracticeEngine(
            logger=logger, now_fn=lambda: "2026-09-17T08:00:00+00:00"
        )

    def start(self, targets=None, **kwargs):
        targets = targets or (_target((60,), 0.0), _target((62,), 1.0))
        self.engine.start_session(
            _plan(*targets), "song", "Song", frozenset(), session_id="session-1", **kwargs
        )
        return self.engine.snapshot(available=True, midi_connection="CONNECTED")

    def snapshot(self, connection="DISCONNECTED"):
        return self.engine.snapshot(available=True, midi_connection=connection)

    def test_disconnect_then_release_then_press_never_grades(self):
        self.start((_target((60, 64), 0.0),))
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_input_availability(False, frozenset())
        before = self.snapshot()
        # Stale release of a required note cannot re-arm while disconnected.
        self.engine.observe_event(_off(60), frozenset())
        # Stale press cannot grade, re-arm, or advance accepted state.
        self.engine.observe_event(_on(60), frozenset({60}))
        state = self.snapshot()
        self.assertEqual(state.state, PracticeState.WAITING)
        self.assertEqual(state.completed_steps, 0)
        self.assertEqual(state.incorrect_attempts, 0)
        self.assertEqual(state.accepted_notes, ())
        self.assertEqual(state.latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)
        # No stale observation may advance the serialized state either.
        self.assertEqual(state.version, before.version)

    def test_incorrect_events_while_disconnected_do_not_count(self):
        self.start()
        self.engine.observe_input_availability(False, frozenset())
        before = self.snapshot()
        self.engine.observe_event(_on(61), frozenset({61}))
        self.engine.observe_event(_on(62), frozenset({62}))
        state = self.snapshot()
        self.assertEqual(state.incorrect_attempts, 0)
        self.assertEqual(state.latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)
        self.assertEqual(state.version, before.version)

    def test_repeat_streak_does_not_advance_while_disconnected(self):
        self.start(
            (_target((60,), 0.0),),
            mode=LearningMode.REPEAT_UNTIL_CORRECT,
            required_repetitions=3,
        )
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_event(_off(60), frozenset())
        self.assertEqual(
            self.snapshot(connection="CONNECTED").current_repetition, 1
        )
        self.engine.observe_input_availability(False, frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))  # stale press
        state = self.snapshot()
        self.assertEqual(state.current_repetition, 0)
        self.assertEqual(state.successful_repetitions, 1)  # progress preserved
        self.assertEqual(state.state, PracticeState.WAITING)

    def test_reconnect_with_no_keys_held_requires_fresh_press(self):
        self.start((_target((60,), 0.0),))
        self.engine.observe_input_availability(False, frozenset())
        self.engine.observe_input_availability(True, frozenset())
        self.assertEqual(
            self.snapshot(connection="CONNECTED").latest_outcome,
            LearningOutcome.NONE,
        )
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_reconnect_while_target_key_held_requires_release_and_repress(self):
        self.start((_target((60,), 0.0),))
        self.engine.observe_input_availability(False, frozenset())
        # The target key is already held at reconnect (authoritative truth).
        self.engine.observe_input_availability(True, frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.WAITING)
        # A repeated press of the already-held key can never satisfy.
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.WAITING)
        # release -> fresh press satisfies.
        self.engine.observe_event(_off(60), frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_partial_chord_held_across_reconnect(self):
        self.start((_target((60, 64), 0.0),))
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_input_availability(False, frozenset({60}))
        self.engine.observe_input_availability(True, frozenset({60}))
        # Adding the missing chord note cannot satisfy: 60 is held-through.
        self.engine.observe_event(_on(64), frozenset({60, 64}))
        state = self.snapshot()
        self.assertEqual(state.state, PracticeState.WAITING)
        self.assertEqual(state.accepted_notes, (64,))
        # Release the held note, then fresh-press it: chord completes.
        self.engine.observe_event(_off(60), frozenset({64}))
        self.engine.observe_event(_on(60), frozenset({60, 64}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_full_chord_held_across_reconnect(self):
        self.start((_target((60, 64), 0.0),))
        self.engine.observe_input_availability(False, frozenset())
        self.engine.observe_input_availability(True, frozenset({60, 64}))
        self.engine.observe_event(_on(60), frozenset({60, 64}))
        self.engine.observe_event(_on(64), frozenset({60, 64}))
        self.assertEqual(self.snapshot().state, PracticeState.WAITING)
        self.engine.observe_event(_off(60), frozenset({64}))
        self.engine.observe_event(_off(64), frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_event(_on(64), frozenset({60, 64}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_start_while_input_unavailable_disarms_and_reports(self):
        self.engine.start_session(
            _plan(_target((60,), 0.0)),
            "song",
            "Song",
            frozenset({60}),
            session_id="session-1",
            input_available=False,
        )
        state = self.snapshot()
        self.assertEqual(state.latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.WAITING)
        self.engine.observe_input_availability(True, frozenset({60}))
        self.engine.observe_event(_off(60), frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)


class AcceptedNotesTruthTests(unittest.TestCase):
    """U4-F4: accepted_notes describes the CURRENT attempt contribution.

    Retained regression for the exact independent reproduction:
    press one chord note -> accepted; release it -> accepted clears
    and the Learning version advances.
    """

    def setUp(self):
        logger = logging.getLogger(f"test.learning.accepted.{id(self)}")
        logger.disabled = True
        self.engine = PracticeEngine(
            logger=logger, now_fn=lambda: "2026-09-17T08:00:00+00:00"
        )

    def start_chord(self, notes=(60, 64)):
        self.engine.start_session(
            _plan(_target(notes, 0.0)), "song", "Song", frozenset(), session_id="session-1"
        )

    def snapshot(self):
        return self.engine.snapshot(available=True, midi_connection="CONNECTED")

    def test_press_accepts_release_clears_and_version_advances(self):
        self.start_chord()
        version = self.snapshot().version
        self.engine.observe_event(_on(60), frozenset({60}))
        state = self.snapshot()
        self.assertEqual(state.accepted_notes, (60,))
        self.assertEqual(state.version, version + 1)
        self.engine.observe_event(_off(60), frozenset())
        state = self.snapshot()
        self.assertEqual(state.accepted_notes, ())
        self.assertEqual(state.version, version + 2)

    def test_repress_restores_accepted_state_and_completes(self):
        self.start_chord()
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_event(_off(60), frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().accepted_notes, (60,))
        self.engine.observe_event(_on(64), frozenset({60, 64}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_partial_chord_release_keeps_remaining_acceptance(self):
        self.start_chord((60, 64, 67))
        self.engine.observe_event(_on(60), frozenset({60}))
        self.engine.observe_event(_on(64), frozenset({60, 64}))
        self.assertEqual(self.snapshot().accepted_notes, (60, 64))
        self.engine.observe_event(_off(64), frozenset({60}))
        self.assertEqual(self.snapshot().accepted_notes, (60,))
        self.engine.observe_event(_on(64), frozenset({60, 64}))
        self.assertEqual(self.snapshot().accepted_notes, (60, 64))
        self.engine.observe_event(_on(67), frozenset({60, 64, 67}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_release_of_held_through_disarmed_note_changes_nothing(self):
        # 60 is held at activation (disarmed, never accepted). Releasing
        # it only re-arms; serialized truth does not change, so the
        # Learning version must not advance.
        self.engine.start_session(
            _plan(_target((60, 64), 0.0)),
            "song",
            "Song",
            frozenset({60}),
            session_id="session-1",
        )
        state = self.snapshot()
        self.assertEqual(state.accepted_notes, ())
        self.engine.observe_event(_off(60), frozenset())
        after = self.snapshot()
        self.assertEqual(after.accepted_notes, ())
        self.assertEqual(after.version, state.version)

    def test_extra_note_does_not_enter_accepted_notes(self):
        self.start_chord()
        self.engine.observe_event(_on(61), frozenset({61}))
        self.assertEqual(self.snapshot().accepted_notes, ())
        self.engine.observe_event(_on(60), frozenset({60, 61}))
        self.assertEqual(self.snapshot().accepted_notes, (60,))

    def test_repeated_note_release_clears_accepted_between_repeats(self):
        self.engine.start_session(
            _plan(_target((60,), 0.0), _target((60,), 1.0)),
            "song",
            "Song",
            frozenset(),
            session_id="session-1",
        )
        self.engine.observe_event(_on(60), frozenset({60}))
        state = self.snapshot()
        self.assertEqual(state.step_index, 1)
        self.assertEqual(state.accepted_notes, ())
        # Held key cannot satisfy step 2; release clears, press completes.
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.WAITING)
        self.engine.observe_event(_off(60), frozenset())
        self.assertEqual(self.snapshot().accepted_notes, ())
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)

    def test_repeat_until_correct_clears_acceptance_between_repeats(self):
        self.engine.start_session(
            _plan(_target((60,), 0.0)),
            "song",
            "Song",
            frozenset(),
            session_id="session-1",
            mode=LearningMode.REPEAT_UNTIL_CORRECT,
            required_repetitions=2,
        )
        self.engine.observe_event(_on(60), frozenset({60}))
        state = self.snapshot()
        self.assertEqual(state.current_repetition, 1)
        # Held from the successful repetition but disarmed for the next.
        self.assertEqual(state.accepted_notes, ())
        self.engine.observe_event(_off(60), frozenset())
        self.engine.observe_event(_on(60), frozenset({60}))
        self.assertEqual(self.snapshot().state, PracticeState.COMPLETED)


class _Record:
    title = "Fixture"
    filename = "fixture.mid"


class _Library:
    def get(self, song_id):
        return _Record() if song_id == "song" else None

    def resolve(self, song_id):
        return Path("fixture.mid")


class LearningManagerAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore()
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        plan = _plan(
            _target((60,), 0.0),
            _target((62,), 2.0),
            _target((64,), 4.0),
            duration=6.0,
        )
        self.manager = PracticeManager(
            _Library(),
            self.store,
            lock_path=str(Path(self.temp.name) / "learning.lock"),
            timeline_loader=lambda path: Timeline((), 6.0, str(path)),
            plan_builder=lambda timeline: plan,
        )

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_manager_section_start_uses_only_bounded_targets(self):
        state = self.manager.start(
            "song",
            mode=LearningMode.SECTION_PRACTICE,
            section=PracticeSection(1.0, 4.0),
        )
        self.assertEqual(state.total_steps, 1)
        self.assertEqual(state.current_target.notes, (62,))

    def test_section_completion_is_authoritative(self):
        state = self.manager.start(
            "song",
            mode=LearningMode.SECTION_PRACTICE,
            section=PracticeSection(1.0, 4.0),
        )
        self.store.apply_event(_on(62))
        self.manager.observe_input_event(_on(62))
        completed = self.manager.snapshot()
        self.assertEqual(completed.state, PracticeState.COMPLETED)
        self.assertEqual(completed.completed_steps, state.total_steps)

    def test_section_beyond_song_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.start(
                "song",
                mode=LearningMode.SECTION_PRACTICE,
                section=PracticeSection(1.0, 7.0),
            )

    def test_empty_section_is_unplayable(self):
        with self.assertRaises(Exception) as caught:
            self.manager.start(
                "song",
                mode=LearningMode.SECTION_PRACTICE,
                section=PracticeSection(5.0, 6.0),
            )
        self.assertIn("no playable", str(caught.exception))

    def test_stale_restart_command_is_rejected(self):
        state = self.manager.start("song")
        with self.assertRaises(PracticeStaleCommandError):
            self.manager.restart_target(
                expected_session_id=state.session_id,
                expected_version=state.version + 1,
            )
        error = translate_exception(
            PracticeStaleCommandError("stale learning command")
        )
        self.assertEqual((error.status_code, error.code), (409, "stale_learning_command"))

    def test_guarded_restart_succeeds_and_returns_new_version(self):
        state = self.manager.start("song")
        restarted = self.manager.restart_target(
            expected_session_id=state.session_id,
            expected_version=state.version,
        )
        self.assertGreater(restarted.version, state.version)

    def test_connection_observer_marks_input_unavailable_without_progress(self):
        self.manager.start("song")
        self.store.set_midi_connection(MidiConnectionState.DISCONNECTED)
        self.manager.observe_connection_state(MidiConnectionState.DISCONNECTED)
        state = self.manager.snapshot()
        self.assertEqual(state.latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)
        self.assertEqual(state.completed_steps, 0)

    def test_stale_events_cannot_grade_after_disconnect(self):
        self.manager.start("song")
        self.store.set_midi_connection(MidiConnectionState.DISCONNECTED)
        self.manager.observe_connection_state(MidiConnectionState.DISCONNECTED)
        before = self.manager.snapshot()
        self.store.apply_event(_on(60))
        self.manager.observe_input_event(_on(60))  # stale observation
        self.store.apply_event(_off(60))
        self.manager.observe_input_event(_off(60))  # stale observation
        state = self.manager.snapshot()
        self.assertEqual(state.state, PracticeState.WAITING)
        self.assertEqual(state.completed_steps, 0)
        self.assertEqual(state.latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)
        self.assertEqual(state.version, before.version)

    def test_reconnect_with_held_target_key_requires_release_and_repress(self):
        self.manager.start("song")
        self.store.set_midi_connection(MidiConnectionState.DISCONNECTED)
        self.manager.observe_connection_state(MidiConnectionState.DISCONNECTED)
        # The target key becomes held while disconnected (store truth).
        self.store.apply_event(_on(60))
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        self.manager.observe_connection_state(MidiConnectionState.CONNECTED)
        # A repeated press event of the held key cannot satisfy the target.
        self.manager.observe_input_event(_on(60))
        self.assertEqual(self.manager.snapshot().state, PracticeState.WAITING)
        # release -> fresh press satisfies the first target.
        self.store.apply_event(_off(60))
        self.manager.observe_input_event(_off(60))
        self.store.apply_event(_on(60))
        self.manager.observe_input_event(_on(60))
        state = self.manager.snapshot()
        self.assertEqual(state.completed_steps, 1)
        self.assertEqual(state.step_index, 1)
        self.assertEqual(state.current_target.notes, (62,))

    def test_serialization_contains_complete_recovery_model(self):
        state = self.manager.start(
            "song",
            mode=LearningMode.REPEAT_UNTIL_CORRECT,
            required_repetitions=3,
        )
        payload = practice_session_payload(state)
        for key in (
            "session_id",
            "mode",
            "target",
            "accepted_notes",
            "latest_outcome",
            "repetition",
            "generation",
            "version",
            "updated_at",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["target"]["target_id"], state.current_target.target_id)

    def test_learning_start_api_rejects_unsupported_mode(self):
        request = SimpleNamespace(
            body=json.dumps({"song_id": "song", "mode": "FOLLOW"}).encode()
        )
        with self.assertRaises(ApiError) as caught:
            learning_start(SimpleNamespace(practice=self.manager), request)
        self.assertEqual(caught.exception.code, "invalid_learning_mode")

    def test_learning_start_api_validates_section_shape(self):
        request = SimpleNamespace(
            body=json.dumps(
                {
                    "song_id": "song",
                    "mode": "SECTION_PRACTICE",
                    "section": {"start_seconds": 0},
                }
            ).encode()
        )
        with self.assertRaises(ApiError) as caught:
            learning_start(SimpleNamespace(practice=self.manager), request)
        self.assertEqual(caught.exception.code, "invalid_section")

    def test_learning_start_api_returns_capabilities(self):
        request = SimpleNamespace(
            body=json.dumps({"song_id": "song", "mode": "WAIT_FOR_NOTE"}).encode()
        )
        status, payload = learning_start(
            SimpleNamespace(practice=self.manager), request
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "WAIT_FOR_NOTE")
        self.assertFalse(payload["capabilities"]["hand_selection"])
        self.assertFalse(payload["capabilities"]["rhythm_grading"])


class _TrackingLock:
    """Lock that counts acquisitions (deterministic critical-section proof)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.acquisitions = 0

    def __enter__(self):
        self._lock.acquire()
        self.acquisitions += 1
        return self

    def __exit__(self, *exc_info):
        self._lock.release()
        return False


class GuardedMutationAtomicityTests(unittest.TestCase):
    """U4-F2: guard validation and mutation share ONE engine critical section."""

    def setUp(self):
        logger = logging.getLogger(f"test.learning.guarded.{id(self)}")
        logger.disabled = True
        self.engine = PracticeEngine(
            logger=logger, now_fn=lambda: "2026-09-17T08:00:00+00:00"
        )

    def start(self):
        self.engine.start_session(
            _plan(_target((60,), 0.0), _target((62,), 1.0)),
            "song",
            "Song",
            frozenset(),
            session_id="session-1",
        )
        return self.engine.snapshot(available=True, midi_connection="CONNECTED")

    def snapshot(self):
        return self.engine.snapshot(available=True, midi_connection="CONNECTED")

    def test_guarded_restart_target_success_then_stale_rejection(self):
        state = self.start()
        self.engine.guarded_restart_target(
            frozenset(),
            expected_session_id=state.session_id,
            expected_version=state.version,
        )
        self.assertGreater(self.snapshot().version, state.version)
        with self.assertRaises(PracticeStaleCommandError):
            self.engine.guarded_restart_target(
                frozenset(),
                expected_session_id=state.session_id,
                expected_version=state.version,
            )

    def test_guarded_restart_section_and_stop_guards(self):
        state = self.start()
        self.engine.guarded_restart_section(
            frozenset(),
            expected_session_id=state.session_id,
            expected_version=state.version,
        )
        current = self.snapshot()
        self.assertEqual(current.step_index, 0)
        with self.assertRaises(PracticeStaleCommandError):
            self.engine.guarded_stop(
                expected_session_id=state.session_id,
                expected_version=current.version + 1,
            )
        self.assertTrue(
            self.engine.guarded_stop(
                expected_session_id=state.session_id,
                expected_version=current.version,
            )
        )
        self.assertEqual(self.snapshot().state, PracticeState.IDLE)

    def test_guard_requires_both_fields_when_required(self):
        self.start()
        with self.assertRaises(PracticeStaleCommandError):
            self.engine.guarded_restart_target(
                frozenset(), expected_session_id="session-1", expected_version=None
            )

    def test_unguarded_stop_remains_idempotent(self):
        self.start()
        self.assertTrue(self.engine.stop())
        self.assertFalse(self.engine.stop())

    def test_guarded_command_validates_and_mutates_in_one_critical_section(self):
        lock = _TrackingLock()
        self.engine._lock = lock
        state = self.start()
        after_start = lock.acquisitions
        self.engine.guarded_restart_target(
            frozenset(),
            expected_session_id=state.session_id,
            expected_version=state.version,
        )
        # The whole guarded command (guard validation + mutation) took
        # exactly ONE lock acquisition: no validate -> unlock -> mutate
        # interleaving window exists.
        self.assertEqual(lock.acquisitions, after_start + 1)
        self.assertTrue(self.engine.guarded_stop())
        self.assertEqual(lock.acquisitions, after_start + 2)


class _CommandGatedStore(StateStore):
    """StateStore that deterministically parks ONE designated thread
    inside snapshot() until released — the barrier for U4-F2 race tests.
    No sleeps: synchronization uses Events only (timeout is a deadlock
    safety net, never the expected path)."""

    def __init__(self):
        super().__init__()
        self._gated_thread: threading.Thread | None = None
        self.entered_gate = threading.Event()
        self.release_gate = threading.Event()

    def hold_next_snapshot_for_current_thread(self) -> None:
        self._gated_thread = threading.current_thread()

    def snapshot(self):
        if self._gated_thread is threading.current_thread():
            self._gated_thread = None
            self.entered_gate.set()
            if not self.release_gate.wait(timeout=30):
                raise RuntimeError("gate deadlock safety net fired")
        return super().snapshot()


class GuardedCommandRaceTests(unittest.TestCase):
    """U4-F2 deterministic barrier tests: a command whose guard validated
    before physical progression must be rejected, never applied to the
    newer state. Retained regression for the exact independent
    reproduction: version guard validates -> concurrent input advances
    -> command resumes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = _CommandGatedStore()
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)
        plan = _plan(
            _target((60,), 0.0),
            _target((62,), 2.0),
            _target((64,), 4.0),
            duration=6.0,
        )
        self.manager = PracticeManager(
            _Library(),
            self.store,
            lock_path=str(Path(self.temp.name) / "race.lock"),
            timeline_loader=lambda path: Timeline((), 6.0, str(path)),
            plan_builder=lambda timeline: plan,
        )

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def _park_command(self, invoke):
        """Run ``invoke`` on a thread that deterministically parks inside
        the store snapshot() the manager performs before the engine's
        atomic guard+mutation critical section."""
        outcome: dict = {}

        def runner():
            # Arm the barrier for THIS thread so its next snapshot()
            # (the manager's pressed-notes read inside the command)
            # parks until the main thread releases the gate.
            self.store.hold_next_snapshot_for_current_thread()
            try:
                outcome["result"] = invoke()
            except BaseException as exc:  # noqa: BLE001 — tests re-raise below
                outcome["error"] = exc

        thread = threading.Thread(target=runner)
        thread.start()
        # Deterministic barrier: wait until the command thread is parked
        # INSIDE snapshot(), i.e. after entering the command but before
        # the engine guard has run. No sleeps.
        self.assertTrue(self.store.entered_gate.wait(10))
        return thread, outcome

    def _progress_physically(self, note, expected_steps):
        """Advance the session via the canonical physical input path."""
        self.store.apply_event(_on(note))
        self.manager.observe_input_event(_on(note))
        progressed = self.manager.snapshot()
        self.assertEqual(progressed.completed_steps, expected_steps)
        return progressed

    def test_restart_section_race_with_physical_progression(self):
        state = self.manager.start("song")
        thread, outcome = self._park_command(
            lambda: self.manager.restart_section(
                expected_session_id=state.session_id,
                expected_version=state.version,
            )
        )
        progressed = self._progress_physically(60, expected_steps=1)
        self.store.release_gate.set()
        thread.join(10)
        self.assertIsInstance(outcome.get("error"), PracticeStaleCommandError)
        final = self.manager.snapshot()
        self.assertEqual(final.completed_steps, 1)
        self.assertEqual(final.step_index, 1)
        self.assertEqual(final.version, progressed.version)

    def test_restart_target_race_with_physical_progression(self):
        state = self.manager.start("song")
        thread, outcome = self._park_command(
            lambda: self.manager.restart_target(
                expected_session_id=state.session_id,
                expected_version=state.version,
            )
        )
        progressed = self._progress_physically(60, expected_steps=1)
        self.store.release_gate.set()
        thread.join(10)
        self.assertIsInstance(outcome.get("error"), PracticeStaleCommandError)
        final = self.manager.snapshot()
        self.assertEqual(final.completed_steps, 1)
        self.assertEqual(final.version, progressed.version)

    def test_guarded_stop_race_with_physical_progression(self):
        # The atomic guarded stop has NO pre-engine critical section to
        # interleave into (guard+mutation share one engine lock — proven
        # by the one-acquisition test), so the deterministic race proof
        # is: a stop carrying truth that physical progression has
        # superseded can never land, with or without concurrency.
        state = self.manager.start("song")
        progressed = self._progress_physically(60, expected_steps=1)
        outcome: dict = {}

        def command():
            try:
                outcome["result"] = self.manager.stop(
                    expected_session_id=state.session_id,
                    expected_version=state.version,
                )
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc

        thread = threading.Thread(target=command)
        thread.start()
        thread.join(10)
        self.assertIsInstance(outcome.get("error"), PracticeStaleCommandError)
        final = self.manager.snapshot()
        self.assertEqual(final.state, PracticeState.WAITING)
        self.assertEqual(final.version, progressed.version)
        # A guarded stop carrying the CURRENT truth succeeds.
        stopped, stopped_flag = self.manager.stop(
            expected_session_id=final.session_id,
            expected_version=final.version,
        )
        self.assertTrue(stopped_flag)
        self.assertEqual(stopped.state, PracticeState.IDLE)

    def test_old_session_id_is_rejected_after_replacement(self):
        first = self.manager.start("song")
        self.manager.stop()
        second = self.manager.start("song")
        self.assertNotEqual(first.session_id, second.session_id)
        outcome: dict = {}

        def command():
            try:
                outcome["result"] = self.manager.restart_target(
                    expected_session_id=first.session_id,
                    expected_version=first.version,
                )
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc

        thread = threading.Thread(target=command)
        thread.start()
        thread.join(10)
        self.assertIsInstance(outcome.get("error"), PracticeStaleCommandError)
        self.assertEqual(self.manager.snapshot().session_id, second.session_id)

    def test_current_guard_succeeds(self):
        state = self.manager.start("song")
        outcome: dict = {}

        def command():
            try:
                outcome["result"] = self.manager.restart_target(
                    expected_session_id=state.session_id,
                    expected_version=state.version,
                )
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc

        thread = threading.Thread(target=command)
        thread.start()
        thread.join(10)
        self.assertNotIn("error", outcome)
        self.assertGreater(outcome["result"].version, state.version)

    def test_repeated_races_never_apply_stale_commands(self):
        for round_index in range(5):
            with self.subTest(round=round_index):
                self.manager.stop()
                # Release the progression note from the previous round so
                # the fresh session arms it normally (held-through notes
                # are disarmed by design and could not be re-progressed).
                self.store.apply_event(_off(60))
                self.manager.observe_input_event(_off(60))
                state = self.manager.start("song")
                self.store.release_gate.clear()
                self.store.entered_gate.clear()
                thread, outcome = self._park_command(
                    lambda: self.manager.restart_section(
                        expected_session_id=state.session_id,
                        expected_version=state.version,
                    )
                )
                try:
                    self._progress_physically(60, expected_steps=1)
                finally:
                    # Always release the parked command: a failed round
                    # must never leak a thread that holds the manager
                    # lock from inside the store gate.
                    self.store.release_gate.set()
                thread.join(10)
                self.assertIsInstance(
                    outcome.get("error"), PracticeStaleCommandError
                )
                self.assertEqual(self.manager.snapshot().completed_steps, 1)


if __name__ == "__main__":
    unittest.main()
