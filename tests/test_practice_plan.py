"""PIANO-P015: practice plan derivation tests (ADR-PRACTICE-V1 D2).

Pure and deterministic: timelines are constructed directly from canonical
output commands, so no mido/file I/O is involved. The canonical P009
timeline remains the only MIDI parsing surface (build_practice_plan
consumes an already-built Timeline).
"""

import unittest

from piano_core.midi.output_events import note_off, note_on, sustain
from piano_core.playback.timeline import Timeline, TimelineEvent
from piano_core.practice.model import (
    KEYBOARD_MAX,
    KEYBOARD_MIN,
    PERCUSSION_CHANNEL,
    PracticeSession,
    PracticeState,
    PracticeTarget,
    note_name,
)
from piano_core.practice.plan import build_practice_plan


def _timeline(*events, duration=10.0, source="test.mid"):
    return Timeline(events=tuple(events), duration=duration, source=source)


class NoteNameTests(unittest.TestCase):
    def test_standard_names(self):
        self.assertEqual(note_name(21), "A0")
        self.assertEqual(note_name(60), "C4")
        self.assertEqual(note_name(61), "C#4")
        self.assertEqual(note_name(69), "A4")
        self.assertEqual(note_name(108), "C8")

    def test_keyboard_bounds_and_percussion_constants(self):
        self.assertEqual(KEYBOARD_MIN, 21)
        self.assertEqual(KEYBOARD_MAX, 108)
        self.assertEqual(PERCUSSION_CHANNEL, 10)


class PlanDerivationTests(unittest.TestCase):
    def test_single_note_becomes_single_note_target(self):
        plan = build_practice_plan(_timeline(TimelineEvent(0.5, note_on(1, 60, 80))))
        self.assertEqual(plan.total_steps, 1)
        target = plan.targets[0]
        self.assertEqual(target.notes, (60,))
        self.assertFalse(target.chord)
        self.assertEqual(target.names, ("C4",))
        self.assertEqual(target.onset_seconds, 0.5)

    def test_simultaneous_notes_form_one_chord_target(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(0.0, note_on(1, 64, 80)),
                TimelineEvent(0.0, note_on(1, 67, 80)),
            )
        )
        self.assertEqual(plan.total_steps, 1)
        self.assertEqual(plan.targets[0].notes, (60, 64, 67))
        self.assertTrue(plan.targets[0].chord)

    def test_multi_track_simultaneous_notes_merge_into_same_target(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(0.0, note_on(2, 64, 70)),
                TimelineEvent(0.0, note_on(3, 67, 90)),
            )
        )
        self.assertEqual(plan.total_steps, 1)
        self.assertEqual(plan.targets[0].notes, (60, 64, 67))

    def test_duplicate_same_note_at_one_onset_deduplicates(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(0.0, note_on(1, 60, 100)),
                TimelineEvent(0.0, note_on(2, 60, 70)),
            )
        )
        self.assertEqual(plan.total_steps, 1)
        self.assertEqual(plan.targets[0].notes, (60,))

    def test_repeated_note_at_later_onset_is_a_separate_target(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(1.0, note_on(1, 60, 80)),
            )
        )
        self.assertEqual(plan.total_steps, 2)
        self.assertEqual(plan.targets[0].notes, (60,))
        self.assertEqual(plan.targets[1].notes, (60,))
        self.assertEqual(plan.targets[1].onset_seconds, 1.0)

    def test_note_offs_and_sustain_never_create_targets(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(0.5, note_off(1, 60, 0)),
                TimelineEvent(1.0, sustain(1, 100)),
                TimelineEvent(1.5, sustain(1, 0)),
            )
        )
        self.assertEqual(plan.total_steps, 1)

    def test_out_of_keyboard_range_note_ons_are_skipped(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(1, KEYBOARD_MIN - 1, 80)),
                TimelineEvent(0.0, note_on(1, KEYBOARD_MAX + 1, 80)),
                TimelineEvent(0.0, note_on(1, 60, 80)),
            )
        )
        self.assertEqual(plan.total_steps, 1)
        self.assertEqual(plan.targets[0].notes, (60,))
        self.assertEqual(plan.skipped_note_ons, 2)

    def test_gm_percussion_channel_is_deliberately_skipped(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(PERCUSSION_CHANNEL, 36, 99)),
                TimelineEvent(0.0, note_on(PERCUSSION_CHANNEL, 42, 99)),
                TimelineEvent(0.0, note_on(1, 64, 80)),
            )
        )
        self.assertEqual(plan.total_steps, 1)
        self.assertEqual(plan.targets[0].notes, (64,))
        self.assertEqual(plan.skipped_note_ons, 2)

    def test_zero_playable_targets_yields_empty_plan(self):
        plan = build_practice_plan(
            _timeline(
                TimelineEvent(0.0, note_on(PERCUSSION_CHANNEL, 36, 99)),
                TimelineEvent(0.0, note_on(1, 12, 99)),
            )
        )
        self.assertEqual(plan.total_steps, 0)

    def test_deterministic_ordering_and_stable_step_count(self):
        events = sorted(
            (
                TimelineEvent(2.0, note_on(1, 67, 80)),
                TimelineEvent(0.0, note_on(1, 60, 80)),
                TimelineEvent(1.0, note_on(1, 62, 80)),
                TimelineEvent(0.0, note_on(1, 64, 80)),
            ),
            key=lambda event: event.time,
        )
        first = build_practice_plan(_timeline(*events))
        second = build_practice_plan(_timeline(*events))
        self.assertEqual(first.total_steps, 3)
        self.assertEqual(
            [t.notes for t in first.targets],
            [(60, 64), (62,), (67,)],
        )
        self.assertEqual(first.targets, second.targets)
        self.assertEqual(first.total_steps, second.total_steps)

    def test_source_song_is_not_mutated(self):
        events = (
            TimelineEvent(0.0, note_on(1, 60, 80)),
            TimelineEvent(1.0, note_on(1, 64, 80)),
            TimelineEvent(2.0, note_off(1, 60, 0)),
        )
        timeline = _timeline(*events)
        before = tuple(timeline.events)
        build_practice_plan(timeline)
        self.assertEqual(tuple(timeline.events), before)

    def test_duration_metadata_is_carried(self):
        plan = build_practice_plan(
            _timeline(TimelineEvent(0.0, note_on(1, 60, 80)), duration=42.5)
        )
        self.assertEqual(plan.duration_seconds, 42.5)

    def test_build_requires_a_canonical_timeline(self):
        with self.assertRaises(TypeError):
            build_practice_plan([TimelineEvent(0.0, note_on(1, 60, 80))])


class ModelValidationTests(unittest.TestCase):
    def test_target_rejects_out_of_range_and_unsorted_and_duplicates(self):
        with self.assertRaises(ValueError):
            PracticeTarget((20,), 0.0)
        with self.assertRaises(ValueError):
            PracticeTarget((109,), 0.0)
        with self.assertRaises(ValueError):
            PracticeTarget((64, 60), 0.0)  # not sorted
        with self.assertRaises(ValueError):
            PracticeTarget((60, 60), 0.0)  # duplicates
        with self.assertRaises(TypeError):
            PracticeTarget((60.0,), 0.0)

    def test_target_chord_and_names(self):
        target = PracticeTarget((60, 64, 67), 1.25)
        self.assertTrue(target.chord)
        self.assertEqual(target.names, ("C4", "E4", "G4"))

    def test_plan_rejects_unsorted_targets(self):
        from piano_core.practice.model import PracticePlan

        with self.assertRaises(ValueError):
            PracticePlan(
                targets=(PracticeTarget((67,), 1.0), PracticeTarget((60,), 0.0)),
                source="test.mid",
                skipped_note_ons=0,
                duration_seconds=1.0,
            )

    def test_session_validation_and_progress(self):
        session = PracticeSession(
            PracticeState.WAITING,
            available=True,
            song_id="s",
            step_index=3,
            total_steps=4,
            completed_steps=3,
        )
        self.assertEqual(session.progress, 0.75)
        empty = PracticeSession(PracticeState.IDLE)
        self.assertEqual(empty.progress, 0.0)
        with self.assertRaises(ValueError):
            PracticeSession(PracticeState.WAITING, step_index=-1)
        with self.assertRaises(TypeError):
            PracticeSession("WAITING")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
