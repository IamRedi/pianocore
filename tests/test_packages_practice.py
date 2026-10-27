"""M5 package practice tests: plans, hands, sections, guidance timeline."""

import tempfile
import unittest
from pathlib import Path

from piano_core.packages.contract import parse_package
from piano_core.packages.practice import (
    GuidedRun,
    PackagePracticeError,
    build_package_plan,
    hands_mode_available,
    plan_section,
)
from piano_core.practice.model import PracticeSection
from tests.test_packages_contract import build_members


class PlanHarness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def _package(self, **kwargs):
        return parse_package(build_members(self.tmp, **kwargs))


class PackagePlanTests(PlanHarness):
    def test_plan_groups_chords_and_is_deterministic(self):
        package = self._package(
            notes=((60, 90, "RH"), (64, 90, "RH"), (67, 90, "RH")),
        )
        plan_a = build_package_plan(package)
        plan_b = build_package_plan(package)
        self.assertEqual(
            [t.target_id for t in plan_a.targets],
            [t.target_id for t in plan_b.targets],
        )
        self.assertEqual(plan_a.total_steps, 3)

    def test_rh_mode_only_rh_events(self):
        package = self._package()  # RH, LH, RH
        plan = build_package_plan(package, hand="RH")
        self.assertTrue(all(target.notes in ((60,), (64,)) for target in plan.targets))
        self.assertEqual(plan.total_steps, 2)

    def test_lh_mode_only_lh_events(self):
        package = self._package()
        plan = build_package_plan(package, hand="LH")
        self.assertEqual(plan.total_steps, 1)
        self.assertEqual(plan.targets[0].notes, (62,))

    def test_hand_mode_without_evidence_fails_closed(self):
        package = self._package(hands_availability="unknown")
        self.assertFalse(hands_mode_available(package))
        with self.assertRaises(PackagePracticeError):
            build_package_plan(package, hand="RH")

    def test_invalid_hand_rejected(self):
        package = self._package()
        with self.assertRaises(PackagePracticeError):
            build_package_plan(package, hand="both")

    def test_section_scope(self):
        package = self._package()
        section = package.section("section:0")
        assert section is not None
        scoped = PracticeSection(section.start_time, section.end_time)
        plan = build_package_plan(package, section=scoped)
        self.assertEqual(plan.total_steps, section.note_count)
        with self.assertRaises(PackagePracticeError):
            plan_section(package, "section:99")


class GuidedRunTests(PlanHarness):
    def _run(self, tempo_scale=1.0, **kwargs):
        package = self._package(notes=((60, 90, "RH"), (62, 90, "LH"),
                                       (64, 90, "RH"), (65, 90, "LH")))
        return GuidedRun(package=package, tempo_scale=tempo_scale, **kwargs)

    def test_listen_progression_and_finish(self):
        run = self._run()
        self.assertFalse(run.finished)
        # at song position 0 nothing sounds yet; note 60 starts at 0.25s
        current, upcoming = run.window(0.3)
        self.assertEqual([note.note for note in current], [])
        self.assertEqual([note.note for note in upcoming], [60])
        run.tick(0.25)
        current, _ = run.window(0.1)
        self.assertEqual([note.note for note in current], [60])
        run.tick(2.0)  # 2 wall seconds = 2 song seconds at scale 1.0
        self.assertTrue(run.finished)

    def test_tempo_scaling_proportional(self):
        slow = self._run(tempo_scale=0.5)
        fast = self._run(tempo_scale=1.0)
        slow.tick(1.0)
        fast.tick(1.0)
        self.assertAlmostEqual(slow.position, 0.5)
        self.assertAlmostEqual(fast.position, 1.0)
        # lookahead is song-time; at 50% speed the same song-time window
        # corresponds to double the wall-clock guidance time
        _, upcoming = slow.window(0.125)
        _, upcoming_fast = fast.window(0.125)
        self.assertEqual(
            [n.note for n in upcoming], [n.note for n in upcoming_fast]
        )

    def test_seek_and_window(self):
        run = self._run()
        run.seek(0.75)
        current, _ = run.window(0.3)
        self.assertEqual([note.note for note in current], [62])

    def test_loop_section_wraps_deterministically(self):
        run = self._run(loop_start=0.25, loop_end=1.25)
        run.tick(1.25)  # reaches the loop end exactly -> wraps to start
        self.assertAlmostEqual(run.position, 0.25, delta=1e-6)
        self.assertEqual(run.loop_count, 1)
        run.tick(0.5)
        self.assertAlmostEqual(run.position, 0.75, delta=1e-6)
        self.assertEqual(run.loop_count, 1)  # not crossed again
        # loop bounds are validated
        with self.assertRaises(PackagePracticeError):
            self._run(loop_start=1.25, loop_end=0.25)

    def test_invalid_tempo_scale_rejected(self):
        with self.assertRaises(PackagePracticeError):
            self._run(tempo_scale=9.0)

    def test_invalid_seek_rejected(self):
        with self.assertRaises(PackagePracticeError):
            self._run().seek(-1.0)

    def test_tick_after_finish_is_noop(self):
        run = self._run()
        run.tick(10.0)
        self.assertTrue(run.finished)
        position = run.position
        run.tick(5.0)
        self.assertEqual(run.position, position)


if __name__ == "__main__":
    unittest.main()
