"""Deterministic tests for the Learn Library ingest pipeline (PIANO-LEARN-001).

Covers: the Pianio token grammar (notes, chords, rests, ties, dotted
durations, grand-staff hand evidence, dynamics), the difficulty model,
the package builder round-trip through the production contract parser,
and the catalog store. Pure unittest; no network, no mido required.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from piano_core.learn.builder import build_package
from piano_core.learn.difficulty import (
    compute_metrics,
    evaluate_level,
    level_label,
)
from piano_core.learn.model import LearnItem, LearnNote, lesson_id_for
from piano_core.learn.sources_pianio import iter_pianio_items
from piano_core.packages.contract import parse_package


def write_course(root: Path, course_id: str, level: int, exercises: list) -> None:
    folder = root / course_id
    folder.mkdir(parents=True)
    payload = {
        "courseId": course_id,
        "title": f"Course {course_id}",
        "level": level,
        "summary": "test course",
        "sourcePolicy": "original_only",
        "exercises": exercises,
    }
    (folder / "course.json").write_text(json.dumps(payload), encoding="utf-8")


def make_exercise(ex_id: str, measures, **overrides) -> dict:
    exercise = {
        "id": ex_id,
        "slug": ex_id,
        "title": f"Exercise {ex_id}",
        "summary": "test",
        "goal": "test",
        "hands": "right",
        "clef": "G",
        "tempoBpm": 60,
        "timeSignature": [4, 4],
        "sourceKind": "original",
        "license": {"type": "CC0-1.0", "attribution": "t", "notes": ""},
        "measures": measures,
    }
    exercise.update(overrides)
    return exercise


class PianioAdapterTests(unittest.TestCase):
    def _items(self, exercises, level=1):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "content" / "method"
            root.mkdir(parents=True)
            write_course(root, "level-1", level, exercises)
            return list(iter_pianio_items(root.parent))

    def test_single_notes_and_rests(self):
        items = self._items([
            make_exercise("ex-1", [["C4:q", "D4:q", "R:q", "E4:q"]]),
        ])
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertFalse(item.problems)
        onsets = [(round(n.onset, 6), round(n.offset, 6), n.midi) for n in item.notes]
        self.assertEqual(onsets, [(0.0, 1.0, 60), (1.0, 2.0, 62), (3.0, 4.0, 64)])

    def test_chords_preserve_exact_onsets(self):
        items = self._items([
            make_exercise("ex-c", [["C4:q", "[E4,G4]:q"]], hands="together"),
        ])
        item = items[0]
        self.assertFalse(item.problems)
        self.assertEqual(len(item.notes), 3)
        self.assertEqual(len({n.onset for n in item.notes[1:]}), 1)
        # single-staff "together" never fabricates per-note hands
        self.assertEqual(item.hands_availability, "partial")

    def test_tie_extends_duration_across_measures(self):
        items = self._items([
            make_exercise("ex-t", [["C4:h~", "C4:h~"], ["C4:h~", "C4:h"]]),
        ])
        item = items[0]
        self.assertFalse(item.problems)
        self.assertEqual(len(item.notes), 1)
        self.assertEqual(round(item.notes[0].offset, 6), 8.0)

    def test_dotted_durations(self):
        items = self._items([
            make_exercise("ex-d", [["C4:dq", "D4:de", "E4:de", "F4:q"]]),
        ])
        item = items[0]
        self.assertFalse(item.problems)
        durs = [round(n.offset - n.onset, 6) for n in item.notes]
        self.assertEqual(durs, [1.5, 0.75, 0.75, 1.0])

    def test_grand_staff_yields_hand_evidence(self):
        items = self._items([
            make_exercise(
                "ex-g",
                [["C4:w"]],
                hands="together",
                measuresLeft=[["C3:w"]],
            ),
        ])
        item = items[0]
        self.assertFalse(item.problems)
        self.assertEqual(item.hands_availability, "available")
        hands = {n.hand for n in item.notes}
        self.assertEqual(hands, {"RH", "LH"})

    def test_dynamics_map_to_deterministic_velocities(self):
        items = self._items([
            make_exercise(
                "ex-v",
                [["C4:q", "D4:q", "C4:q", "D4:q"]],
                dynamics=[{"measure": 1, "beat": 2, "marking": "p"}],
            ),
        ])
        item = items[0]
        self.assertFalse(item.problems)
        self.assertEqual(item.notes[0].velocity, 80)  # default mf before p
        self.assertEqual(item.notes[1].velocity, 49)  # p from beat 2
        self.assertEqual(item.notes[2].velocity, 49)

    def test_unsupported_classifications(self):
        # out-of-range note
        items = self._items([make_exercise("bad-1", [["C9:w"]])])
        self.assertTrue(any("outside 88-key range" in p for p in items[0].problems))
        # unparseable token
        items = self._items([make_exercise("bad-2", [["X9:w"]])])
        self.assertTrue(any("unparseable token" in p for p in items[0].problems))
        # measure math mismatch (3 beats in 4/4)
        items = self._items([make_exercise("bad-3", [["C4:q", "D4:q", "E4:q"]])])
        self.assertTrue(any("measures sum" in p for p in items[0].problems))
        # non-CC0 license
        items = self._items([
            make_exercise(
                "bad-4",
                [["C4:w"]],
                license={"type": "CC-BY", "attribution": "x", "notes": ""},
            ),
        ])
        self.assertTrue(any("CC0-1.0 required" in p for p in items[0].problems))

    def test_level_hints_floor_map(self):
        items = self._items([make_exercise("lv", [["C4:w"]])], level=3)
        self.assertEqual(items[0].level_hint, 2)

    def test_lesson_identity_is_deterministic(self):
        a = lesson_id_for("pianio:method-level-1a:m1a-001")
        b = lesson_id_for("pianio:method-level-1a:m1a-001")
        self.assertEqual(a, b)
        self.assertNotEqual(a, lesson_id_for("pianio:method-level-1a:m1a-002"))


class DifficultyTests(unittest.TestCase):
    def test_first_notes_gate_places_monophonic_at_level_zero(self):
        notes = tuple(
            LearnNote(onset=float(i), offset=float(i) + 0.5, midi=60, velocity=80, hand="RH", track=0)
            for i in range(4)
        )
        decision = evaluate_level(compute_metrics(notes, tempo_bpm=60.0), source_level=0)
        self.assertEqual(decision.level, 0)
        self.assertIn("first-notes evidence gate met", decision.reasons[1])

    def test_source_floor_cannot_place_polyphony_at_level_zero(self):
        notes = tuple(
            LearnNote(onset=float(i), offset=float(i) + 0.5, midi=48 + (i % 12), velocity=80,
                      hand="LH" if i % 2 else "RH", track=0)
            for i in range(40)
        )
        decision = evaluate_level(compute_metrics(notes, tempo_bpm=120.0), source_level=0)
        self.assertGreaterEqual(decision.level, 1)

    def test_harder_material_scores_higher(self):
        simple = tuple(
            LearnNote(onset=float(i), offset=float(i) + 0.5, midi=60 + (i % 5), velocity=80, hand="RH", track=0)
            for i in range(16)
        )
        complex_notes = tuple(
            LearnNote(onset=float(i) * 0.5, offset=float(i) * 0.5 + 0.4,
                      midi=36 + ((i * 7) % 55), velocity=96,
                      hand="RH" if i % 2 else "LH", track=0)
            for i in range(160)
        )
        s = compute_metrics(simple, tempo_bpm=60.0)
        hard = compute_metrics(complex_notes, tempo_bpm=160.0)
        self.assertLess(evaluate_level(s).score, evaluate_level(hard).score)

    def test_labels_and_levels_consistent(self):
        self.assertEqual(level_label(0), "First Notes")
        self.assertEqual(level_label(4), "Advanced")
        with self.assertRaises(ValueError):
            level_label(5)


class BuilderRoundTripTests(unittest.TestCase):
    def _item(self):
        notes = (
            LearnNote(onset=0.0, offset=1.0, midi=60, velocity=80, hand="RH", track=0),
            LearnNote(onset=1.0, offset=2.0, midi=62, velocity=80, hand="RH", track=0),
            LearnNote(onset=2.0, offset=3.0, midi=64, velocity=80, hand="RH", track=0),
            LearnNote(onset=3.0, offset=4.0, midi=48, velocity=80, hand="LH", track=0),
        )
        return LearnItem(
            lesson_key="pianio:test:ex-1",
            title="Builder Exercise",
            composer=None,
            source="pianio",
            source_detail={"course_id": "test", "course_level": 1},
            license_name="CC0-1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            attribution="test",
            notes=notes,
            tempo_bpm=60.0,
            time_signature=(4, 4),
            level_hint=1,
            hands_availability="available",
        )

    def test_package_round_trips_through_contract_parser(self):
        build = build_package(self._item(), source_version_id=7)
        parsed = parse_package(dict(build.members))
        self.assertEqual(parsed.package_id, build.package_id)
        self.assertEqual(parsed.identity.title, "Builder Exercise")
        self.assertEqual(parsed.identity.hands_availability, "available")
        self.assertEqual(parsed.identity.rh_count, 3)
        self.assertEqual(parsed.identity.lh_count, 1)
        events = parsed.events
        self.assertEqual([e.note for e in events], [60, 62, 64, 48])
        self.assertEqual([e.hand for e in events], ["RH", "RH", "RH", "LH"])

    def test_unsupported_item_refuses_to_build(self):
        item = self._item().with_problems("synthetic reason")
        with self.assertRaises(ValueError):
            build_package(item, source_version_id=1)

    def test_honest_hands_downgrade(self):
        item = self._item()
        # one unassigned note breaks the "available" claim -> partial
        patched = LearnItem(
            lesson_key=item.lesson_key,
            title=item.title,
            composer=item.composer,
            source=item.source,
            source_detail=item.source_detail,
            license_name=item.license_name,
            license_url=item.license_url,
            attribution=item.attribution,
            notes=item.notes[:-1] + (LearnNote(onset=3.0, offset=4.0, midi=48, velocity=80, hand=None, track=0),),
            tempo_bpm=item.tempo_bpm,
            time_signature=item.time_signature,
            level_hint=item.level_hint,
            hands_availability="available",
        )
        build = build_package(patched, source_version_id=2)
        manifest = json.loads(build.members["manifest.json"])
        self.assertEqual(manifest["hands"]["availability"], "partial")


class CatalogStoreTests(unittest.TestCase):
    def _catalog(self, tmp):
        from piano_core.learn.catalog import LearnCatalog

        return LearnCatalog(Path(tmp) / "learn.sqlite3")

    def test_replace_and_query_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = self._catalog(tmp)
            catalog.replace_catalog(
                [{"course_id": "c1", "title": "Course One", "level": 0,
                  "summary": "s", "source": "pianio", "order_index": 0,
                  "lesson_count": 2}],
                [
                    {"lesson_id": "L1", "lesson_key": "k1", "package_id": "P1",
                     "course_id": "c1", "title": "Gymnopédie", "composer": "Satie",
                     "source": "mutopia", "level": 2, "difficulty": "medium",
                     "duration_seconds": 200.0, "order_index": 0,
                     "hands_availability": "available", "license_name": "PD",
                     "license_url": None, "attribution": None,
                     "metrics": {}, "level_decision": {}, "summary": ""},
                    {"lesson_id": "L2", "lesson_key": "k2", "package_id": "P2",
                     "course_id": "c1", "title": "Pulse", "composer": None,
                     "source": "pianio", "level": 0, "difficulty": "easy",
                     "duration_seconds": 8.0, "order_index": 1,
                     "hands_availability": "available", "license_name": "CC0",
                     "license_url": None, "attribution": None,
                     "metrics": {}, "level_decision": {}, "summary": ""},
                ],
            )
            self.assertEqual(catalog.stats()["lesson_count"], 2)
            rows = catalog.lessons(text="gymnopedie")  # accent-folded
            self.assertEqual(rows[0].lesson_id, "L1")
            by_level = catalog.lessons(level=0)
            self.assertEqual([r.lesson_id for r in by_level], ["L2"])
            # Global curriculum order: L2 (level 0) precedes L1 (level 2).
            prev2, nxt2 = catalog.neighbors(catalog.lesson("L2"))
            self.assertIsNone(prev2)
            self.assertEqual(nxt2.lesson_id, "L1")
            prev1, _ = catalog.neighbors(catalog.lesson("L1"))
            self.assertEqual(prev1.lesson_id, "L2")
            catalog.close()

    def test_stale_schema_raises_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = self._catalog(tmp)
            catalog.close()
            # hand-craft a foreign schema_version
            import sqlite3

            conn = sqlite3.connect(Path(tmp) / "learn.sqlite3")
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', '999')"
            )
            conn.commit()
            conn.close()
            catalog = self._catalog(tmp)
            with self.assertRaises(Exception):
                catalog.stats()
            catalog.close()


if __name__ == "__main__":
    unittest.main()
