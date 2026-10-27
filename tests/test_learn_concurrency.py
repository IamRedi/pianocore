"""R1: complete connection ownership, deterministic negative control, real mixed HTTP/SQLite.

Barrier probes prove the execute-through-fetch boundary, not native SQLite bug
reproduction. Native reproduction is the separately retained Python3.13 experiment.
"""
import concurrent.futures
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.request

from piano_core.api.server import ApiServer
from piano_core.learn.catalog import LearnCatalog
from piano_core.learn.service import LearnService
from piano_core.packages.store import PackageStore, PackageRow
from piano_core.packages.service import PackageService
from piano_core.packages.sessions import record_wait_session
from tests.test_learn_library import _seed_catalog
from piano_core.learn.model import lesson_id_for


class ObservedLock:
    def __init__(self):
        self.raw = threading.RLock()
        self.contender_attempted = threading.Event()

    def __enter__(self):
        if threading.current_thread().name == 'contender':
            self.contender_attempted.set()
        self.raw.acquire()
        return self

    def __exit__(self, *exc):
        self.raw.release()


class ParkedConnection:
    def __init__(self, conn, lock, predicate, execute_only=False, invalidate_on_overlap=False):
        self.conn, self.lock, self.predicate = conn, lock, predicate
        self.execute_only = execute_only
        self.invalidate_on_overlap = invalidate_on_overlap
        self.parked, self.release = threading.Event(), threading.Event()
        self.active_fetch = False
        self.overlap = False

    def execute(self, sql, *args):
        if self.active_fetch and threading.current_thread().name == 'contender':
            self.overlap = True
            self.lock.contender_attempted.set()
        if self.execute_only:
            with self.lock:
                cursor = self.conn.execute(sql, *args)
        else:
            cursor = self.conn.execute(sql, *args)
        owner = self
        class Cursor:
            def fetch(inner, method):
                if (threading.current_thread().name == 'reader'
                        and owner.predicate(sql) and not owner.parked.is_set()):
                    owner.active_fetch = True
                    owner.parked.set()
                    if not owner.release.wait(3):
                        raise AssertionError('reader barrier was not released')
                    try:
                        if owner.invalidate_on_overlap and owner.overlap:
                            # Deterministic fault model of the separately native-proven
                            # cached-statement result loss; never presented as native proof.
                            return None
                        return getattr(cursor, method)()
                    finally:
                        owner.active_fetch = False
                return getattr(cursor, method)()
            def fetchone(inner): return inner.fetch('fetchone')
            def fetchall(inner): return inner.fetch('fetchall')
        return Cursor()

    def close(self):
        if self.active_fetch: self.overlap = True
        return self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


class CatalogOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'catalog.sqlite3'
        _seed_catalog(self.db)
        self.cat = LearnCatalog(self.db)
        self.addCleanup(self.cat.close)
        self.first = self.cat.lessons()[0]
        self.courses = [asdict(c) for c in self.cat.courses()]
        self.lessons = [asdict(l) for l in self.cat.lessons()]

    def ownership_probe(self, call, predicate, contender=None, execute_only=False):
        lock = ObservedLock()
        self.cat._lock = lock
        connection = ParkedConnection(self.cat._conn, lock, predicate, execute_only)
        self.cat._conn = connection
        errors = []
        def run(fn):
            try: fn()
            except Exception as e: errors.append(e)
        reader = threading.Thread(target=run, args=(call,), name='reader')
        second = None
        reader.start()
        try:
            self.assertTrue(connection.parked.wait(2), 'reader did not reach fetch barrier')
            # Deterministic ownership proof, with no sleep/scheduling assumption.
            acquired = lock.raw.acquire(blocking=False)
            if acquired: lock.raw.release()
            self.assertFalse(acquired, 'connection ownership ended before fetch')
            second = threading.Thread(target=run, args=(contender or self.cat.schema_version,), name='contender')
            second.start()
            self.assertTrue(lock.contender_attempted.wait(2), 'second caller never attempted ownership')
            self.assertFalse(connection.overlap)
        finally:
            connection.release.set()
            reader.join(3)
            if second: second.join(3)
        self.assertFalse(reader.is_alive())
        if second: self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(connection.overlap)

    def test_schema_fetch_owned(self):
        self.ownership_probe(self.cat.schema_version, lambda s: 'FROM meta' in s)

    def test_stats_all_fetches_owned(self):
        self.ownership_probe(self.cat.stats, lambda s: 'COUNT(*), MIN' in s)

    def test_courses_fetch_owned(self):
        self.ownership_probe(self.cat.courses, lambda s: 'FROM courses' in s)

    def test_lesson_page_fetch_owned(self):
        self.ownership_probe(self.cat.lessons, lambda s: 'FROM lessons' in s)

    def test_lesson_detail_fetch_owned(self):
        self.ownership_probe(lambda: self.cat.lesson(self.first.lesson_id), lambda s: 'WHERE lesson_id' in s)

    def test_package_lookup_fetch_owned(self):
        self.ownership_probe(lambda: self.cat.lesson_by_package('pkg-1'), lambda s: 'WHERE package_id' in s)

    def test_neighbor_fetch_owned(self):
        self.ownership_probe(lambda: self.cat.neighbors(self.first), lambda s: 'WHERE (level <' in s)

    def test_course_progress_fetch_owned(self):
        self.ownership_probe(self.cat.course_progress, lambda s: 'GROUP BY course_id' in s)

    def test_execute_only_negative_control_exposes_fetch_gap(self):
        unwrapped = getattr(LearnCatalog.lessons, '__wrapped__', LearnCatalog.lessons)
        with self.assertRaisesRegex(AssertionError, 'ownership ended before fetch'):
            self.ownership_probe(lambda: unwrapped(self.cat), lambda s: 'FROM lessons' in s, execute_only=True)

    def test_overlapping_cached_cursor_fault_cannot_create_empty_schema(self):
        lock = ObservedLock()
        self.cat._lock = lock
        conn = ParkedConnection(self.cat._conn, lock, lambda s: 'FROM meta' in s,
                                invalidate_on_overlap=True)
        self.cat._conn = conn
        errors, results = [], []
        def read():
            try: results.append(self.cat.schema_version())
            except Exception as e: errors.append(e)
        first = threading.Thread(target=read, name='reader')
        second = threading.Thread(target=read, name='contender')
        first.start()
        try:
            self.assertTrue(conn.parked.wait(2))
            second.start()
            # Correct code signals at the blocked lock acquisition. Unprotected
            # code signals after entering execute and losing the first result.
            self.assertTrue(lock.contender_attempted.wait(2))
        finally:
            conn.release.set()
            first.join(3)
            if second.ident: second.join(3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, ['1', '1'])
        with sqlite3.connect(self.db) as independent:
            self.assertEqual(independent.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone(), ('1',))

    def test_close_waits_for_active_reader(self):
        self.ownership_probe(self.cat.lessons, lambda s: 'FROM lessons' in s, contender=self.cat.close)

    def test_replace_waits_and_publishes_complete_catalog(self):
        self.ownership_probe(self.cat.lessons, lambda s: 'FROM lessons' in s,
                             contender=lambda: self.cat.replace_catalog(self.courses, self.lessons))
        self.assertEqual(self.cat.stats()['lesson_count'], 2)
        self.assertEqual([asdict(x) for x in self.cat.lessons()], self.lessons)

    def test_exception_releases_ownership_and_next_request_recovers(self):
        original = self.cat._conn
        class FailOnce:
            failed = False
            def execute(inner, sql, *args):
                if 'FROM courses' in sql and not inner.failed:
                    inner.failed = True
                    raise sqlite3.OperationalError('controlled query fault')
                return original.execute(sql, *args)
            def close(inner): return original.close()
        self.cat._conn = FailOnce()
        with self.assertRaisesRegex(sqlite3.OperationalError, 'controlled query fault'):
            self.cat.courses()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(len(pool.submit(self.cat.courses).result(timeout=2)), 1)

    def test_progress_callbacks_do_not_hold_catalog_ownership(self):
        observed = []
        service = None
        def lookup(_):
            def probe():
                acquired = service._catalog._lock.acquire(blocking=False)
                if acquired: service._catalog._lock.release()
                return acquired
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                observed.append(pool.submit(probe).result(timeout=2))
            return {'attempts': 0, 'completions': 0}
        service = LearnService(self.db, progress_lookup=lookup)
        self.addCleanup(service.close)
        self.assertEqual(service.lessons()['count'], 2)
        self.assertEqual(observed, [True, True])


class MixedCatalogHttpTests(unittest.TestCase):
    def test_simultaneous_routes_and_real_progress_writes_preserve_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'catalog.sqlite3'
            _seed_catalog(db)
            packages = PackageService(Path(tmp) / 'packages', Path(tmp) / 'packages.sqlite3')
            store = packages.store
            for package in ['pkg-1', 'pkg-2']:
                store.insert_package(PackageRow(package, package, 1, 'a'*64, 'b'*64, None,
                    'available', 1, 0, 8., 0, 8, 'synthetic', None, {}, tmp,
                    '2026-10-10T00:00:00Z', 'active'), {})
            service = LearnService(db, progress_lookup=store.progress_summary,
                                   progress_all_lookup=store.learning_progress)
            context = SimpleNamespace(learn_service=service, learn_ready=True,
                                      packages_service=packages)
            server = ApiServer(context, '127.0.0.1', 0, serve_ui=False)
            server.start()
            host, port = server.bound_address
            barrier = threading.Barrier(9)
            lesson = lesson_id_for('pianio:t:ex-1')
            paths = ['learn', 'learn/lessons?limit=1&offset=1', 'learn/lessons?q=Middle',
                     'learn/lessons?course=c1', 'learn/lessons?level=0',
                     'learn/lessons/'+lesson, 'packages/pkg-1/progress', 'packages/pkg-2/progress']
            def reader(n):
                barrier.wait()
                for i in range(40):
                    path = paths[(n+i)%len(paths)]
                    with urllib.request.urlopen(f'http://{host}:{port}/api/v1/{path}', timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        data = json.load(response)
                    if path == 'learn': self.assertEqual(data['lesson_count'], 2)
                    elif path.startswith('learn/lessons?'):
                        self.assertGreater(data['count'], 0)
                        self.assertTrue(all(x['package_id'] in ['pkg-1', 'pkg-2'] for x in data['lessons']))
                    elif path.startswith('learn/lessons/'):
                        self.assertEqual(data['lesson_id'], lesson)
                        self.assertEqual(data['package_id'], 'pkg-1')
                    else: self.assertGreaterEqual(data['attempts'], 0)
            def writer():
                barrier.wait()
                for i in range(20):
                    record_wait_session(store, session_id=f'synthetic-{i}', package_id='pkg-1',
                        mode='WAIT_FOR_NOTE', hand_selection=None, section_id=None, tempo_scale=1.,
                        started_at=f'2026-10-10T00:00:{i:02d}Z', ended_at=None, total_steps=1,
                        completed_steps=0, incorrect_attempts=0, completed=False)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:
                    futures = [pool.submit(reader, n) for n in range(8)] + [pool.submit(writer)]
                    for future in futures: future.result(timeout=30)
                self.assertEqual(store.progress_summary('pkg-1')['attempts'], 20)
                self.assertEqual(store.progress_summary('pkg-1')['missed_notes'], 20)
                self.assertEqual(service.overview()['progress']['continue_lesson']['lesson_id'], lesson)
                self.assertEqual(service._catalog.stats()['lesson_count'], 2)
            finally:
                server.shutdown()
                service.close()


if __name__ == '__main__': unittest.main()
