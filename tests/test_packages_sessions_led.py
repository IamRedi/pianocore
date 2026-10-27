"""M5 LED semantic contract + session/progress tests."""

import tempfile
import unittest
from pathlib import Path

from piano_core.packages.contract import LED_SEMANTIC_VERSION, parse_package
from piano_core.packages.led_frames import (
    LedNoteState,
    RecordingLedRenderer,
    build_frame,
)
from piano_core.packages.practice import GuidedRun
from piano_core.packages.sessions import (
    record_guidance_session,
    record_wait_session,
)
from piano_core.packages.store import PackageStore
from tests.test_packages_contract import build_members


class Harness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.store = PackageStore(self.tmp / "packages.db")

    def _package(self, **kwargs):
        return parse_package(build_members(self.tmp, **kwargs))


class LedFrameTests(Harness):
    def test_frame_carries_version_and_semantics(self):
        package = self._package()
        run = GuidedRun(package=package)
        run.seek(0.25)
        current, upcoming = run.window(0.6)  # horizon 0.85 covers note 62 @0.75
        frame = build_frame(
            practice_state="FOLLOW",
            song_time=run.position,
            current=current,
            upcoming=upcoming,
            lookahead_ms=300,
            tempo_scale=1.0,
        )
        self.assertEqual(frame.version, LED_SEMANTIC_VERSION)
        payload = frame.to_dict()
        self.assertEqual(payload["schema"], "led-practice-frame/v1")
        self.assertEqual(payload["led_semantic_version"], LED_SEMANTIC_VERSION)
        self.assertEqual(payload["practice_state"], "FOLLOW")
        states = {note["state"] for note in payload["current_notes"]}
        self.assertEqual(states, {"ACTIVE"})
        states = {note["state"] for note in payload["upcoming_notes"]}
        self.assertEqual(states, {"UPCOMING"})
        hands = {note["hand"] for note in payload["current_notes"]}
        self.assertTrue(hands <= {"RH", "LH", None})

    def test_hand_filtered_frame(self):
        package = self._package()
        run = GuidedRun(package=package)
        run.seek(0.25)
        current, upcoming = run.window(0.4)
        frame = build_frame(
            practice_state="FOLLOW",
            song_time=run.position,
            current=[n for n in current if n.hand == "RH"],
            upcoming=[n for n in upcoming if n.hand == "RH"],
            lookahead_ms=400,
            hand="RH",
        )
        self.assertTrue(all(note.hand == "RH" for note in frame.current_notes))
        self.assertEqual(frame.hand, "RH")

    def test_recording_renderer_deterministic(self):
        renderer = RecordingLedRenderer()
        package = self._package()
        run = GuidedRun(package=package)
        for step in range(4):
            run.tick(0.25)
            current, upcoming = run.window(0.25)
            renderer.render(
                build_frame(
                    practice_state="LISTEN",
                    song_time=round(run.position, 6),
                    current=current,
                    upcoming=upcoming,
                    lookahead_ms=250,
                )
            )
        self.assertEqual(len(renderer.frames), 4)
        times = [frame.song_time for frame in renderer.frames]
        self.assertEqual(times, sorted(times))
        again = RecordingLedRenderer()
        run2 = GuidedRun(package=package)
        for step in range(4):
            run2.tick(0.25)
            current, upcoming = run2.window(0.25)
            again.render(
                build_frame(
                    practice_state="LISTEN",
                    song_time=round(run2.position, 6),
                    current=current,
                    upcoming=upcoming,
                    lookahead_ms=250,
                )
            )
        self.assertEqual(
            [f.to_dict() for f in renderer.frames],
            [f.to_dict() for f in again.frames],
        )

    def test_note_states_enum_covers_contract(self):
        expected = {"UPCOMING", "ACTIVE", "WAITING", "MATCHED", "MISSED", "EXTRA"}
        self.assertEqual({state.value for state in LedNoteState}, expected)


class SessionTests(Harness):
    PACKAGE_ID = "package-under-test"

    def _seed_package(self):
        package = self._package()
        from piano_core.packages.importer import _summary_of

        self.store.insert_package(
            _summary_of(
                package,
                str(self.tmp / "pkgdir"),
                "2026-01-01T00:00:00Z",
            ),
            package.provenance,
        )
        return package

    def test_wait_session_formula_is_exact(self):
        self._seed_package()
        result = record_wait_session(
            self.store,
            session_id="s1",
            package_id=self.store.list_packages()[0].package_id,
            mode="WAIT_FOR_NOTE",
            hand_selection="RH",
            section_id="section:0",
            tempo_scale=1.0,
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:01:00Z",
            total_steps=10,
            completed_steps=7,
            incorrect_attempts=3,
            completed=False,
        )
        # 10 expected, 7 correct, 3 missed, 3 extra; accuracy 0.7
        self.assertEqual(result.expected_count, 10)
        self.assertEqual(result.correct_count, 7)
        self.assertEqual(result.missed_count, 3)
        self.assertEqual(result.extra_count, 3)
        self.assertEqual(result.accuracy, 0.7)
        self.assertFalse(result.completed)

    def test_completed_session_has_no_missed(self):
        package = self._seed_package()
        result = record_wait_session(
            self.store,
            session_id="s2",
            package_id=package.package_id,
            mode="WAIT_FOR_NOTE",
            hand_selection=None,
            section_id=None,
            tempo_scale=0.6,
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:02:00Z",
            total_steps=3,
            completed_steps=3,
            incorrect_attempts=1,
            completed=True,
        )
        self.assertEqual(result.missed_count, 0)
        self.assertEqual(result.accuracy, 1.0)

    def test_guidance_session_is_passive(self):
        package = self._seed_package()
        result = record_guidance_session(
            self.store,
            session_id="g1",
            package_id=package.package_id,
            mode="LISTEN",
            section_id=None,
            tempo_scale=1.0,
            started_at="2026-01-01T00:00:00Z",
            note_groups=3,
            completed=True,
            elapsed_seconds=2.0,
            loop_count=1,
        )
        # passive runs invent NO grading
        self.assertEqual(result.correct_count, 0)
        self.assertEqual(result.missed_count, 0)
        self.assertEqual(result.extra_count, 0)
        self.assertTrue(result.completed)

    def test_progress_summary_and_trouble_sections(self):
        package = self._package()
        pid = package.package_id
        self._seed_package()
        # two Wait sessions in section:0 with misses, one clean elsewhere
        record_wait_session(
            self.store, session_id="p1", package_id=pid, mode="WAIT_FOR_NOTE",
            hand_selection=None, section_id="section:0", tempo_scale=1.0,
            started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T00:01:00Z",
            total_steps=4, completed_steps=1, incorrect_attempts=5,
            completed=False,
        )
        record_wait_session(
            self.store, session_id="p2", package_id=pid, mode="WAIT_FOR_NOTE",
            hand_selection=None, section_id="section:0", tempo_scale=0.75,
            started_at="2026-01-02T00:00:00Z", ended_at="2026-01-02T00:01:00Z",
            total_steps=4, completed_steps=2, incorrect_attempts=2,
            completed=False,
        )
        record_wait_session(
            self.store, session_id="p3", package_id=pid, mode="WAIT_FOR_NOTE",
            hand_selection=None, section_id="section:1", tempo_scale=1.0,
            started_at="2026-01-03T00:00:00Z", ended_at="2026-01-03T00:01:00Z",
            total_steps=2, completed_steps=2, incorrect_attempts=0,
            completed=True,
        )
        progress = self.store.progress_summary(pid)
        self.assertEqual(progress["attempts"], 3)
        self.assertEqual(progress["expected_notes"], 10)
        self.assertEqual(progress["correct_notes"], 5)
        self.assertEqual(progress["note_accuracy"], 0.5)
        self.assertEqual(progress["best_completed_tempo"], 1.0)
        self.assertEqual(progress["recent_tempo"], 1.0)
        self.assertEqual(progress["last_practiced"], "2026-01-03T00:00:00Z")
        trouble = progress["trouble_sections"]
        self.assertEqual(trouble[0]["section_id"], "section:0")
        self.assertEqual(trouble[0]["miss_rate"], 0.625)
        # section:1 completed cleanly with zero misses
        self.assertEqual(trouble[1]["miss_rate"], 0.0)



if __name__ == "__main__":
    unittest.main()
