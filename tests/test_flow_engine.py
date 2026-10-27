"""U8-P7 deterministic physical-observation and clock boundary proofs."""
import unittest
from piano_core.practice.engine import PracticeEngine
from piano_core.practice.model import LearningMode, PracticePlan, PracticeTarget, PracticeState, PracticeStaleCommandError, PracticeInvalidStateError
from piano_core.midi.events import note_on, note_off
from piano_core.api.serialization import practice_session_payload


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.held = set()
        self.targets = []
        self.clears = []
        self.releases = []
        self.engine = PracticeEngine(clock=lambda: self.now, on_target=self.targets.append, on_clear=lambda: self.clears.append(True), on_release=lambda: self.releases.append(True))

    def snapshot(self):
        return self.engine.snapshot(available=True, midi_connection='CONNECTED')

    def start(self, groups=((0.0, (60,)), (1.0, (60,))), duration=2, speed=1):
        self.plan = PracticePlan(tuple(PracticeTarget(notes, onset, i) for i, (onset, notes) in enumerate(groups)), "canonical", 0, duration)
        self.engine.start_session(self.plan, "package:fixture", "Fixture", frozenset(self.held), mode=LearningMode.FLOW, speed=speed)

    def press(self, note, when):
        self.now = when
        self.held.add(note)
        self.engine.observe_event(note_on(1, note, 80, when), frozenset(self.held))

    def release(self, note, when):
        self.now = when
        self.held.discard(note)
        self.engine.observe_event(note_off(1, note, 0, when), frozenset(self.held))

    def tick(self, when):
        self.now = when
        self.engine.tick_flow()
        return self.snapshot()

    def pause(self, value):
        s = self.snapshot()
        self.engine.flow_pause(value, frozenset(self.held), expected_session_id=s.session_id, expected_version=s.version)

    def test_clock_continues_and_completes_without_input(self):
        self.start()
        s = self.tick(103.5)
        self.assertEqual(s.flow['missed_events'], 2)
        self.assertEqual(s.state, PracticeState.WAITING)
        self.assertEqual(s.flow['position_seconds'], 1.5)
        self.assertEqual(self.tick(104.231).state, PracticeState.COMPLETED)
        self.assertEqual(len(self.releases), 1)
        self.tick(105)
        self.assertEqual(len(self.releases), 1)

    def test_wrong_press_does_not_score_event(self):
        self.start()
        self.press(61, 102)
        self.assertEqual(self.snapshot().flow['wrong_presses'], 1)
        self.assertEqual(self.snapshot().completed_steps, 0)

    def test_repeated_note_requires_release_and_new_press(self):
        self.start()
        self.press(60, 102)
        self.press(60, 103)
        self.assertEqual(self.snapshot().completed_steps, 1)
        self.release(60, 103.01)
        self.press(60, 103.02)
        self.assertEqual(self.snapshot().completed_steps, 2)

    def test_held_at_start_cannot_satisfy_first_event(self):
        self.held.add(60)
        self.start()
        self.press(60, 102)
        self.assertEqual(self.snapshot().completed_steps, 0)
        self.release(60, 102.01)
        self.press(60, 102.02)
        self.assertEqual(self.snapshot().completed_steps, 1)

    def test_chord_requires_simultaneous_held_keys(self):
        self.start(((0, (60, 64, 67)),))
        self.press(60, 101.95)
        self.release(60, 101.97)
        self.press(64, 102)
        self.press(67, 102.02)
        self.assertEqual(self.snapshot().completed_steps, 0)
        self.press(60, 102.04)
        self.assertEqual(self.snapshot().completed_steps, 1)

    def test_chord_attack_spread_is_bounded(self):
        self.start(((0, (60, 64)),))
        self.press(60, 101.83)
        self.press(64, 102.17)
        self.assertEqual(self.snapshot().completed_steps, 0)
        self.assertEqual(self.tick(102.231).flow['missed_events'], 1)

    def test_early_late_and_on_time_measure_real_attacks(self):
        self.start(((0, (60,)), (1, (62,)), (2, (64,))), duration=3)
        self.press(60, 101.9)
        self.assertEqual(self.snapshot().latest_outcome.value, 'EARLY')
        self.press(62, 103.1)
        self.assertEqual(self.snapshot().latest_outcome.value, 'LATE')
        self.press(64, 104)
        s = self.snapshot().flow
        self.assertEqual((s['early_attacks'], s['late_attacks'], s['on_time_attacks']), (1, 1, 1))
        self.assertAlmostEqual(s['mean_absolute_error_ms'], 66.7)

    def test_one_attack_cannot_match_overlapping_same_pitch_events(self):
        self.start(((0, (60,)), (0.1, (60,))))
        self.press(60, 102.08)
        self.assertEqual(self.snapshot().completed_steps, 1)
        self.assertEqual(self.engine._flow.results, {1: 'CORRECT'})
        self.release(60, 102.09)
        self.press(60, 102.1)
        self.assertEqual(self.snapshot().completed_steps, 2)

    def test_pause_freezes_and_resume_preserves_remaining_opportunity(self):
        self.start()
        self.tick(102.5)
        self.pause(True)
        self.assertEqual(self.tick(110).flow['position_seconds'], 0.5)
        self.pause(False)
        self.press(60, 110.5)
        self.assertEqual(self.snapshot().completed_steps, 1)

    def test_disconnect_pauses_and_reconnect_requires_explicit_resume(self):
        self.start()
        self.now = 102.5
        self.engine.observe_input_availability(False, frozenset())
        self.tick(110)
        self.engine.observe_input_availability(True, frozenset())
        self.assertTrue(self.snapshot().flow['paused'])
        self.assertEqual(self.snapshot().flow['position_seconds'], 0.5)

    def test_capture_before_resume_is_unscored_and_requires_fresh_release(self):
        self.start()
        self.now = 102.5
        self.pause(True)
        self.now = 110
        self.pause(False)
        self.held.add(60)
        self.engine.observe_event(note_on(1, 60, 80, 104), frozenset(self.held))
        self.assertEqual(self.snapshot().incorrect_attempts, 0)
        self.assertEqual(self.snapshot().completed_steps, 0)
        self.release(60, 110.4)
        self.press(60, 110.5)
        self.assertEqual(self.snapshot().completed_steps, 1)

    def test_pause_guard_rejects_obsolete_session(self):
        self.start()
        with self.assertRaises(PracticeStaleCommandError):
            self.engine.flow_pause(True, frozenset(), expected_session_id='obsolete', expected_version=1)

    def test_slow_speed_scales_canonical_onsets_not_acceptance_window(self):
        self.start(speed=0.5)
        self.press(60, 102)
        self.release(60, 102.01)
        self.press(60, 104.1)
        self.assertEqual(self.snapshot().completed_steps, 2)
        self.assertEqual(self.tick(105).state, PracticeState.WAITING)
        self.assertEqual(self.tick(106.231).state, PracticeState.COMPLETED)

    def test_count_in_does_not_penalize_preparation(self):
        self.start()
        self.press(70, 100.5)
        self.assertEqual(self.snapshot().incorrect_attempts, 0)

    def test_no_double_score_and_terminal_clock_is_frozen(self):
        self.start(((0, (60,)),), duration=1)
        self.press(60, 102)
        self.release(60, 102.01)
        self.press(60, 102.02)
        s = self.tick(103.231)
        self.assertEqual(s.completed_steps, 1)
        self.assertEqual(self.tick(110).flow, s.flow)

    def test_nonfinite_or_future_or_reordered_input_is_rejected(self):
        for timestamp in (float('nan'), float('inf'), 999, 99):
            with self.subTest(timestamp=timestamp):
                self.setUp(); self.start(); self.now = 102
                if timestamp == 99:
                    self.press(61, 102)
                with self.assertRaises(ValueError):
                    self.engine.observe_event(note_on(1, 60, 80, timestamp), frozenset({60}))

    def test_delayed_in_window_input_uses_capture_timestamp(self):
        self.start()
        self.now = 102.21
        self.engine.observe_event(note_on(1, 60, 80, 102.17), frozenset({60}))
        self.assertEqual(self.snapshot().completed_steps, 1)

    def test_excessive_ingress_delay_fails_visibly_at_manager_boundary(self):
        self.start()
        self.now = 102.1
        with self.assertRaises(ValueError):
            self.engine.observe_event(note_on(1, 60, 80, 102.0), frozenset({60}))

    def test_invalid_speed_or_timing_refused_before_start(self):
        for speed in (False, 0, 1.1, float('nan'), float('inf')):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                self.start(speed=speed)
        self.assertEqual(self.engine.state, PracticeState.IDLE)

    def test_legacy_restart_does_not_mutate_flow_clock(self):
        self.start()
        with self.assertRaises(PracticeInvalidStateError):
            self.engine.restart_section(frozenset())
        self.assertEqual(self.snapshot().flow['position_seconds'], -2)

    def test_wait_has_no_fabricated_timing_statistics(self):
        p = PracticePlan((PracticeTarget((60,), 0),), 'wait', 0, 1)
        self.engine.start_session(p, 'wait', 'Wait')
        self.assertIsNone(practice_session_payload(self.snapshot())['flow'])


if __name__ == '__main__':
    unittest.main()
