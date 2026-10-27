"""R3: constant page fanout, exact formulas, fresh snapshots and real writers."""
import concurrent.futures
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from piano_core.learn.catalog import LearnCatalog
from piano_core.learn.service import LearnService
from piano_core.packages.store import PackageStore
from tests.test_learn_library import _seed_catalog
from tests.test_packages_resources import package_row

REAL_CONNECT = sqlite3.connect


class BulkProgressTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = PackageStore(self.root / 'packages.db')
        self.ids = [f'pkg-{n}' for n in range(50)]
        for ident in self.ids:
            self.store.insert_package(package_row(ident, self.root), {})

    def record(self, ident, number, **kw):
        values = dict(session_id=f'{ident}-{number}', package_id=ident,
            mode='FLOW', hand_selection=None, section_id=None, tempo_scale=1.,
            started_at='2026-10-11T00:00:00Z', ended_at=None,
            expected_count=4, correct_count=1, missed_count=3, extra_count=1,
            chord_completions=1, best_streak=1, elapsed_seconds=8., completed=False)
        values.update(kw)
        self.store.record_session(**values)

    def track(self, factory=sqlite3.Connection):
        held, statements = [], []
        def connect(*args, **kwargs):
            c = REAL_CONNECT(*args, **kwargs, factory=factory)
            c.set_trace_callback(statements.append)
            held.append(c)
            return c
        return held, statements, patch('piano_core.packages.store.sqlite3.connect', side_effect=connect)

    def assert_closed(self, held):
        for c in held:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed database'):
                c.execute('SELECT 1')

    def test_exact_parity_for_all_fields_and_session_order_ties(self):
        for ident in self.ids[:49]:
            self.record(ident, 'a', completed=True, detail={'full_lesson': True},
                        tempo_scale=.7, section_id='a')
            self.record(ident, 'b', completed=True, detail={'full_lesson': False},
                        tempo_scale=.8, section_id='b', correct_count=2, missed_count=2)
            # Tied timestamps: descending session ID determines recent tempo;
            # incomplete high tempo must not become best completed tempo.
            self.record(ident, 'z', tempo_scale=1.5, section_id='a')
            self.record(ident, 'zero', section_id='empty', expected_count=0,
                        correct_count=0, missed_count=0)
        ids = self.ids + ['missing']
        expected = {ident: self.store.progress_summary(ident) for ident in ids}
        held, _, tracked = self.track()
        with tracked:
            actual = self.store.progress_summaries(ids)
        self.assertEqual(actual, expected)
        self.assertEqual(actual['pkg-0']['best_completed_tempo'], .8)
        self.assertEqual(actual['pkg-0']['recent_tempo'], 1.)
        self.assertEqual(actual['pkg-0']['full_lesson_completions'], 1)
        self.assertEqual(actual['missing']['attempts'], 0)
        self.assert_closed(held)

    def test_constant_three_selects_one_connection_vs_200_selects_100_connections(self):
        held, statements, tracked = self.track()
        with tracked:
            old = {ident: self.store.progress_summary(ident) for ident in self.ids}
        self.assertEqual(len(held), 100)
        self.assertEqual(sum(q.startswith('SELECT') for q in statements), 200)
        self.assert_closed(held)
        for ids in (self.ids[:1], self.ids, self.ids * 2):
            held, statements, tracked = self.track()
            with tracked:
                result = self.store.progress_summaries(ids)
            self.assertEqual(result, {ident: old[ident] for ident in ids})
            self.assertEqual(len(held), 1)
            self.assertEqual(sum(q.startswith('SELECT') for q in statements), 3)
            self.assert_closed(held)

    def test_empty_duplicate_and_bounded_input(self):
        held, _, tracked = self.track()
        with tracked:
            self.assertEqual(self.store.progress_summaries([]), {})
            with self.assertRaisesRegex(ValueError, '100'):
                self.store.progress_summaries(['pkg-0'] * 101)
        self.assertEqual(held, [])
        self.assertEqual(list(self.store.progress_summaries(['pkg-1','pkg-0','pkg-1'])), ['pkg-1','pkg-0'])

    def test_read_snapshot_does_not_block_50_writes_and_next_call_is_fresh(self):
        old = self.store.progress_summaries(self.ids)
        snapshot_taken, writer_done = threading.Event(), threading.Event()
        class BarrierConnection(sqlite3.Connection):
            def execute(c, sql, *args):
                if 'ROW_NUMBER()' in sql:
                    snapshot_taken.set()
                    if not writer_done.wait(10):
                        raise AssertionError('WAL writer starved by read snapshot')
                return super().execute(sql, *args)
        held = []
        def connect(*args, **kwargs):
            # Apply barrier only to the reader; the writer uses native SQLite.
            # Retained-handle closure is checked later from the test thread.
            kwargs['check_same_thread'] = False
            c = REAL_CONNECT(*args, **kwargs, factory=BarrierConnection if threading.current_thread().name.startswith('bulk-reader') else sqlite3.Connection)
            held.append(c)
            return c
        with patch('piano_core.packages.store.sqlite3.connect', side_effect=connect):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='bulk-reader') as pool:
                pending = pool.submit(self.store.progress_summaries, self.ids)
                try:
                    self.assertTrue(snapshot_taken.wait(10))
                    for ident in self.ids:
                        self.record(ident, 'committed', completed=True, detail={'full_lesson':True})
                finally:
                    writer_done.set()
                self.assertEqual(pending.result(timeout=10), old)
        self.assert_closed(held)
        fresh = self.store.progress_summaries(self.ids)
        self.assertEqual(fresh, {ident:self.store.progress_summary(ident) for ident in self.ids})
        self.assertTrue(all(row['attempts']==1 and row['full_lesson_completions']==1 for row in fresh.values()))
        with closing(REAL_CONNECT(self.store._path)) as c:
            self.assertEqual(set(c.execute('SELECT session_id,package_id,expected_count,correct_count,missed_count FROM sessions')),
                {(ident+'-committed',ident,4,1,3) for ident in self.ids})

    def test_begin_and_each_query_fault_close_and_recover_without_data_loss(self):
        self.record('pkg-0','persisted')
        expected = self.store.progress_summary('pkg-0')
        for fragment in ('BEGIN','COUNT(*) AS attempts','ROW_NUMBER()','AND section_id IS NOT NULL'):
            class Fault(sqlite3.Connection):
                def execute(c, sql, *args):
                    if fragment in sql:
                        raise sqlite3.OperationalError('controlled bulk fault')
                    return super().execute(sql, *args)
            held, _, tracked = self.track(Fault)
            with self.subTest(fragment=fragment):
                with tracked, self.assertRaisesRegex(sqlite3.OperationalError, 'bulk fault'):
                    self.store.progress_summaries(['pkg-0'])
                self.assert_closed(held)
                self.assertEqual(self.store.progress_summaries(['pkg-0']), {'pkg-0':expected})

    def test_learn_page_uses_one_bulk_call_preserves_identity_qualification_and_degrade(self):
        db = self.root/'catalog.db'; _seed_catalog(db)
        calls=[]
        def bulk(ids):
            calls.append(ids)
            return {ident:{'attempts':3,'completions':2,'full_lesson_completions':1} for ident in ids}
        def forbidden(ident):
            raise AssertionError('page must not perform per-lesson progress lookups')
        service=LearnService(db, progress_lookup=forbidden, progress_many_lookup=bulk)
        self.addCleanup(service.close)
        rows=service.lessons(level=0)['lessons']
        self.assertEqual(calls,[['pkg-1','pkg-2']])
        self.assertEqual([r['package_id'] for r in rows],['pkg-1','pkg-2'])
        self.assertTrue(all(r['progress_available'] and r['progress']['completions']==1 for r in rows))
        service._progress_many_lookup=lambda ids: (_ for _ in ()).throw(OSError('controlled unavailable'))
        degraded=service.lessons()['lessons']
        self.assertEqual([r['lesson_id'] for r in degraded],[r['lesson_id'] for r in rows])
        self.assertTrue(all(r['progress'] is None and not r['progress_available'] for r in degraded))


if __name__ == '__main__':
    unittest.main()
