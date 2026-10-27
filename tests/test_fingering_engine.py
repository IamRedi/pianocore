"""PIANO-UI-006 deterministic hand/finger intelligence tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from piano_core.fingering import (
    FingeringConfig,
    FingeringService,
    FingeringUnsupportedError,
    generate_plan,
)
from piano_core.playback.score import ScoreProjection, ScoreSpan


def score(notes, song_id="song-a"):
    spans = tuple(
        ScoreSpan(i, midi, channel, onset, duration, 80)
        for i, (midi, onset, duration, channel) in enumerate(notes)
    )
    duration = max((onset + duration for _, onset, duration, _ in notes), default=0)
    return ScoreProjection(song_id, "Test", duration, spans)


class FingeringEngineTests(unittest.TestCase):
    def test_repeat_generation_is_byte_stable_in_identity_and_targets(self):
        projection = score(
            [(48, 0, 0.5, 1), (60, 0, 0.5, 1), (50, 1, 0.5, 1), (62, 1, 0.5, 1)]
        )
        first = generate_plan(projection)
        second = generate_plan(projection)
        self.assertEqual(first, second)
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertTrue(all(t.hand in {"LEFT", "RIGHT"} for t in first.targets))
        self.assertTrue(all(1 <= t.finger <= 5 for t in first.targets))
        self.assertEqual(first.provenance, "DETERMINISTIC_INFERENCE")

    def test_source_or_config_change_changes_plan_identity(self):
        original = score([(60, 0, 0.5, 1), (62, 1, 0.5, 1)])
        changed = score([(60, 0, 0.5, 1), (64, 1, 0.5, 1)])
        self.assertNotEqual(original.source_version, changed.source_version)
        self.assertNotEqual(generate_plan(original).plan_id, generate_plan(changed).plan_id)
        narrow = generate_plan(original, FingeringConfig(max_hand_span_semitones=11))
        self.assertNotEqual(generate_plan(original).plan_id, narrow.plan_id)

    def test_chord_uses_ordered_fingers_and_both_hands(self):
        projection = score([(n, 0, 1, 1) for n in (43, 47, 50, 67, 71, 74)])
        plan = generate_plan(projection)
        left = sorted((t.midi, t.finger) for t in plan.targets if t.hand == "LEFT")
        right = sorted((t.midi, t.finger) for t in plan.targets if t.hand == "RIGHT")
        self.assertTrue(left and right)
        self.assertEqual([f for _, f in left], sorted((f for _, f in left), reverse=True))
        self.assertEqual([f for _, f in right], sorted(f for _, f in right))

    def test_repeated_pitch_prefers_same_finger(self):
        plan = generate_plan(score([(60, 0, 0.2, 1), (60, 0.5, 0.2, 1)]))
        self.assertEqual(plan.targets[0].hand, plan.targets[1].hand)
        self.assertEqual(plan.targets[0].finger, plan.targets[1].finger)

    def test_movement_battery_is_complete_and_bounded(self):
        passages = {
            "ascending": [60, 62, 64, 65, 67, 69, 71, 72],
            "descending": [72, 71, 69, 67, 65, 64, 62, 60],
            "alternating": [60, 67, 60, 67, 60, 67],
            "chromatic": [60, 61, 62, 63, 64, 65],
            "large_leaps": [36, 72, 40, 76, 43, 79],
        }
        for name, notes in passages.items():
            with self.subTest(name=name):
                plan = generate_plan(
                    score([(note, index * 0.25, 0.2, 1) for index, note in enumerate(notes)])
                )
                self.assertEqual(len(notes), len(plan.targets))
                self.assertTrue(all(1 <= target.finger <= 5 for target in plan.targets))

    def test_dyad_through_five_note_and_black_key_chords(self):
        chords = (
            (60, 64),
            (60, 64, 67),
            (60, 62, 65, 69),
            (60, 62, 64, 65, 67),
            (61, 63, 66, 68, 70),
        )
        for notes in chords:
            with self.subTest(notes=notes):
                plan = generate_plan(score([(note, 0, 1, 1) for note in notes]))
                self.assertEqual(len(notes), len(plan.targets))
                self.assertTrue(all(target.alternatives is not None for target in plan.targets))

    def test_duplicates_share_same_key_assignment(self):
        plan = generate_plan(score([(60, 0, 1, 1), (60, 0, 0.5, 2)]))
        self.assertEqual(2, len(plan.targets))
        self.assertEqual(plan.targets[0].hand, plan.targets[1].hand)
        self.assertEqual(plan.targets[0].finger, plan.targets[1].finger)

    def test_exclusions_are_explicit(self):
        plan = generate_plan(score([(60, 0, 1, 10), (20, 1, 1, 1), (64, 2, 1, 1)]))
        self.assertEqual([64], [target.midi for target in plan.targets])
        self.assertEqual(2, plan.excluded_note_count)
        self.assertIn("excluded_non_keyboard_or_percussion_notes:2", plan.warnings)

    def test_impossible_shape_fails_closed(self):
        with self.assertRaisesRegex(FingeringUnsupportedError, "at most 10"):
            generate_plan(score([(40 + i, 0, 1, 1) for i in range(11)]))
        with self.assertRaisesRegex(FingeringUnsupportedError, "hand span"):
            generate_plan(score([(n, 0, 1, 1) for n in (21, 41, 61, 81, 101, 108)]))

    def test_empty_score_is_a_valid_complete_plan(self):
        plan = generate_plan(score([]))
        self.assertEqual((), plan.targets)
        self.assertTrue(plan.plan_id.startswith("fingering-sha256:"))

    def test_held_note_occupancy_prevents_finger_reuse(self):
        plan = generate_plan(score([(23, 0, 10, 1), (22, 1, 1, 1)]))
        held, later = plan.targets
        self.assertEqual(("LEFT", "LEFT"), (held.hand, later.hand))
        self.assertNotEqual(held.finger, later.finger)

    def test_release_boundary_frees_finger_and_zero_duration_does_not_hold(self):
        released = generate_plan(score([(23, 0, 1, 1), (22, 1, 1, 1)]))
        self.assertEqual(released.targets[0].finger, released.targets[1].finger)
        zero = generate_plan(score([(23, 0, 0, 1), (22, 1, 1, 1)]))
        self.assertEqual(zero.targets[0].finger, zero.targets[1].finger)

    def test_held_pitch_constrains_span(self):
        with self.assertRaisesRegex(FingeringUnsupportedError, "hand span"):
            generate_plan(
                score([(23, 0, 10, 1), (70, 0, 10, 1), (40, 1, 1, 1)])
            )

    def test_duplicate_physical_key_uses_one_finger_and_longest_duration(self):
        plan = generate_plan(
            score([(23, 0, 10, 1), (23, 0, 2, 2), (22, 3, 1, 1)])
        )
        first, alias, later = plan.targets
        self.assertEqual((first.hand, first.finger), (alias.hand, alias.finger))
        self.assertNotEqual(first.finger, later.finger)

    def test_alternative_count_excludes_primary_and_is_deterministic(self):
        projection = score([(60, 0, 1, 1)])
        for count in (1, 3, 5):
            with self.subTest(count=count):
                config = FingeringConfig(alternative_count=count)
                first = generate_plan(projection, config).targets[0]
                second = generate_plan(projection, config).targets[0]
                self.assertEqual(first.alternatives, second.alternatives)
                self.assertNotIn(first.finger, first.alternatives)
                self.assertEqual(min(count, 4), len(first.alternatives))
                self.assertEqual(len(set(first.alternatives)), len(first.alternatives))
        chord = generate_plan(
            score([(60 + offset, 0, 1, 1) for offset in range(5)]),
            FingeringConfig(alternative_count=5),
        )
        self.assertTrue(any(len(target.alternatives) < 5 for target in chord.targets))

    def test_plan_identity_binds_schema_engine_generation_config_and_targets(self):
        projection = score([(60, 0, 1, 1)])
        baseline = generate_plan(projection).plan_id
        from piano_core.fingering import engine as engine_module

        dimensions = (
            ("FINGERING_SCHEMA_VERSION", 2),
            ("FINGERING_PLAN_VERSION", 2),
            ("FINGERING_GENERATION_VERSION", 2),
            ("FINGERING_ENGINE_VERSION", "balanced-engine-v2"),
        )
        for name, value in dimensions:
            with self.subTest(name=name), patch.object(engine_module, name, value):
                self.assertNotEqual(baseline, generate_plan(projection).plan_id)
        self.assertNotEqual(
            baseline,
            generate_plan(projection, FingeringConfig(alternative_count=1)).plan_id,
        )
        self.assertNotEqual(
            baseline,
            generate_plan(score([(61, 0, 1, 1)])).plan_id,
        )


class _ScoreService:
    available = True

    def __init__(self, projection):
        self.projection = projection

    def get(self, song_id):
        return self.projection


class FingeringServiceTests(unittest.TestCase):
    def test_cache_reuses_exact_version_and_invalidates_on_source_change(self):
        backend = _ScoreService(score([(60, 0, 1, 1)]))
        service = FingeringService(backend)
        first = service.get("song-a")
        self.assertIs(first, service.get("song-a"))
        backend.projection = score([(61, 0, 1, 1)])
        second = service.get("song-a")
        self.assertIsNot(first, second)
        self.assertNotEqual(first.source_version, second.source_version)


if __name__ == "__main__":
    unittest.main()
