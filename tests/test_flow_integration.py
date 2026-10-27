"""U8-P7 regression proofs at canonical input, worker and persistence boundaries."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from piano_core.midi.events import note_on
from piano_core.practice.engine import PracticeEngine
from piano_core.practice.manager import PracticeManager
from piano_core.practice.model import LearningMode, LearningOutcome, PracticePlan, PracticeTarget, PracticeState, PracticeNotAvailableError
from piano_core.state import StateStore, MidiConnectionState
from piano_core.packages.store import PackageStore, PackageRow
from piano_core.packages.sessions import record_engine_session
from tests.test_learning_engine import _Library


def plan():
    return PracticePlan((PracticeTarget((60,), 0, 0),), 'canonical', 0, 1)


class FlowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 100.0
        self.store = StateStore()
        self.store.set_midi_connection(MidiConnectionState.CONNECTED)

    def manager(self, recorder=None):
        manager = PracticeManager(_Library(), self.store, lock_path=str(Path(self.temp.name) / 'activity.lock'), plan_builder=lambda _: plan(), timeline_loader=lambda _: None, session_recorder=recorder)
        manager._engine._clock = lambda: self.now
        self.addCleanup(manager.shutdown)
        return manager

    def observe(self, manager, timestamp=None):
        event = note_on(1, 60, 90, self.now if timestamp is None else timestamp)
        self.store.apply_event(event)
        manager.observe_input_event(event)

    def test_wait_dispatch_cannot_grade_new_flow_across_stop_start(self):
        engine = PracticeEngine(clock=lambda: self.now)
        engine.start_session(plan(), 'song', 'Song')
        lock = engine._lock
        parked, proceed = threading.Event(), threading.Event()
        class BarrierLock:
            def __enter__(inner):
                if threading.current_thread().name == 'old-observation':
                    parked.set()
                    if not proceed.wait(2): raise AssertionError('barrier timeout')
                lock.acquire()
            def __exit__(inner, *args): lock.release()
        engine._lock = BarrierLock()
        errors = []
        def observe():
            try: engine.observe_event(note_on(1, 60, 80, 100), frozenset({60}))
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=observe, name='old-observation')
        thread.start()
        self.assertTrue(parked.wait(2))
        try:
            engine.stop()
            engine.start_session(plan(), 'song', 'Song', mode=LearningMode.FLOW)
        finally: proceed.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        state = engine.snapshot(available=True, midi_connection='CONNECTED')
        self.assertEqual(state.state, PracticeState.WAITING)
        self.assertEqual(state.completed_steps, 0)
        self.assertEqual(state.flow['correct_events'], 0)

    def test_disconnected_start_freezes_count_in_without_misses(self):
        self.store.set_midi_connection(MidiConnectionState.DISCONNECTED)
        manager = self.manager()
        manager.start('song', mode=LearningMode.FLOW)
        self.now = 200
        manager._engine.tick_flow()
        state = manager.snapshot()
        self.assertEqual(state.state, PracticeState.WAITING)
        self.assertTrue(state.flow['paused'])
        self.assertEqual(state.flow['position_seconds'], -2)
        self.assertEqual(state.flow['missed_events'], 0)
        self.assertEqual(state.latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)

    def test_disconnect_feedback_survives_heartbeat(self):
        manager = self.manager()
        manager.start('song', mode=LearningMode.FLOW)
        self.store.set_midi_connection(MidiConnectionState.DISCONNECTED)
        manager.observe_connection_state(MidiConnectionState.DISCONNECTED)
        self.now = 110
        manager._engine.tick_flow()
        self.assertEqual(manager.snapshot().latest_outcome, LearningOutcome.INPUT_UNAVAILABLE)

    def test_excessive_ingress_fails_session_and_releases_ownership(self):
        manager = self.manager()
        manager.start('song', mode=LearningMode.FLOW)
        self.now = 102.1
        self.observe(manager, timestamp=102)
        state = manager.snapshot()
        self.assertEqual(state.state, PracticeState.FAILED)
        self.assertIn('ingress budget', state.error)
        self.assertIsNone(manager._advisory)

    def test_blocked_writer_cannot_block_new_flow_clock(self):
        writer_entered, unblock, completed = threading.Event(), threading.Event(), threading.Event()
        def recorder(_):
            writer_entered.set()
            if not unblock.wait(3): raise AssertionError('writer barrier timeout')
        manager = self.manager(recorder)
        self.addCleanup(unblock.set)
        manager.start('song')
        self.observe(manager)
        self.assertTrue(writer_entered.wait(2))
        tick = manager._engine.tick_flow
        def tick_observed():
            tick()
            if manager.snapshot().state is PracticeState.COMPLETED: completed.set()
        manager._engine.tick_flow = tick_observed
        manager.start('song', mode=LearningMode.FLOW)
        self.now = 104
        try:
            self.assertTrue(completed.wait(2), 'Flow stalled behind database writer')
            self.assertEqual(manager.snapshot().flow['missed_events'], 1)
            self.assertIsNone(manager._advisory)
        finally: unblock.set()

    def test_failed_write_retained_and_explicit_idempotent_retry(self):
        attempts, saved = [], []
        def recorder(session):
            attempts.append(session.session_id)
            if len(attempts) == 1: raise OSError('controlled unavailable store')
            saved.append(session.session_id)
        manager = self.manager(recorder)
        session = manager.start('song')
        self.observe(manager)
        manager._records.join()
        self.assertEqual(len(manager._failed_results), 1)
        self.assertIn('controlled unavailable', manager.persistence_error)
        with self.assertRaises(PracticeNotAvailableError): manager.start('song')
        manager.retry_failed_results()
        manager._records.join()
        self.assertEqual(saved, [session.session_id])
        self.assertEqual(attempts, [session.session_id, session.session_id])
        self.assertIsNone(manager.persistence_error)
        self.assertEqual(manager._failed_results, [])

    def test_wait_elapsed_and_section_scope_survive_result_capture(self):
        captured = []
        engine = PracticeEngine(clock=lambda: self.now, on_result=captured.append)
        engine.start_session(plan(), 'package:fixture', 'Song', scope_section_id='section:1')
        self.now = 107
        engine.observe_event(note_on(1, 60, 80, 107), frozenset({60}))
        self.assertEqual(captured[0].elapsed_seconds, 7)
        self.assertEqual(captured[0].scope_section_id, 'section:1')
        self.assertFalse(captured[0].full_lesson)

    def test_sqlite_progress_and_signed_timing_survive_reopen(self):
        db = Path(self.temp.name) / 'progress.db'
        store = PackageStore(db)
        store.insert_package(PackageRow('fixture', 'Fixture', 1, 'a'*64, 'b'*64, None, 'available', 1, 0, 1.0, 0, 1, 'synthetic', None, {}, self.temp.name, '2026-10-09T00:00:00Z', 'active'), {})
        captured = []
        engine = PracticeEngine(clock=lambda: self.now, on_result=captured.append)
        engine.start_session(plan(), 'package:fixture', 'Song', mode=LearningMode.FLOW, full_lesson=True)
        self.now = 101.9
        engine.observe_event(note_on(1, 60, 80, self.now), frozenset({60}))
        self.now = 103.24
        engine.tick_flow()
        record_engine_session(store, captured[0])
        # Same identity is idempotent on explicit retry.
        record_engine_session(store, captured[0])
        reopened = PackageStore(db)
        with reopened._connect() as conn:
            row = conn.execute('SELECT detail_json, elapsed_seconds FROM sessions').fetchone()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
        self.assertEqual(json.loads(row['detail_json'])['signed_attack_errors_ms'], [-100])
        self.assertAlmostEqual(row['elapsed_seconds'], 3.24)
        self.assertEqual(reopened.learning_progress()['fixture']['completions'], 1)
        # A completed RH-only run is an attempt, never another full lesson.
        self.now = 110
        engine.start_session(plan(), 'package:fixture', 'Song', hand_selection='RH', full_lesson=True)
        engine.observe_event(note_on(1, 60, 80, 110), frozenset({60}))
        record_engine_session(reopened, captured[-1])
        self.assertEqual(reopened.learning_progress()['fixture']['attempts'], 2)
        self.assertEqual(reopened.learning_progress()['fixture']['completions'], 1)
