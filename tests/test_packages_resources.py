"""R2 resource ownership: retained real handles, exception paths and native 50-row HTTP fanout.

The disposable subprocess deliberately disables cyclic GC for BOTH baseline and
candidate: explicit connection ownership must not depend on collection timing.
Production GC and descriptor limits are never changed by this test.
"""
import concurrent.futures
from contextlib import closing
from dataclasses import asdict
import gc
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

from piano_core.api.server import ApiServer
from piano_core.learn.catalog import LearnCatalog
from piano_core.learn.model import lesson_id_for
from piano_core.learn.service import LearnService
from piano_core.packages.service import PackageService
from piano_core.packages.sessions import record_wait_session
from piano_core.packages.store import PackageRow, PackageStore
from tests.test_learn_library import _seed_catalog

REAL_CONNECT = sqlite3.connect


def package_row(ident, root):
    return PackageRow(ident, ident, 1, 'a'*64, 'b'*64, None, 'available',
                      1, 0, 8., 0, 8, 'synthetic', None, {}, str(root),
                      '2026-10-10T00:00:00Z', 'active')


def fd_inventory():
    # listdir's transient directory FD is closed before readlink; disappearing
    # descriptors from concurrent workers are skipped, never confused with FDSize.
    result = {}
    for name in os.listdir('/proc/self/fd'):
        try: result[name] = os.readlink('/proc/self/fd/' + name)
        except FileNotFoundError: pass
    return result


class ConnectionOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = PackageStore(self.root/'packages.db')
        self.row = package_row('pkg-0', self.root)
        self.store.insert_package(self.row, {})

    def tracked_call(self, fn, factory=sqlite3.Connection):
        held = []
        def connect(*args, **kwargs):
            conn = REAL_CONNECT(*args, **kwargs, factory=factory)
            held.append(conn)  # Keep every real handle alive: no GC-based PASS.
            return conn
        with patch('piano_core.packages.store.sqlite3.connect', side_effect=connect):
            try: fn()
            finally:
                try:
                    self.assertTrue(held)
                    for conn in held:
                        with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed database'):
                            conn.execute('SELECT 1')
                finally:
                    for conn in held: conn.close()  # Test-only cleanup, even on baseline FAIL.

    def test_every_store_operation_explicitly_closes_materialized_connections(self):
        operations = [lambda: PackageStore(self.root/'fresh.db'),
            lambda: self.store.get_package('pkg-0'),
            lambda: self.store.find_by_midi_sha('a'*64),
            self.store.list_packages,
            lambda: self.store.insert_package(package_row('pkg-1', self.root), {}),
            lambda: self.store.replace_package(self.row, {}),
            lambda: self.store.package_path('pkg-0'),
            lambda: self.record('session-0'),
            lambda: self.store.sessions_for_package('pkg-0'),
            self.store.learning_progress,
            lambda: self.store.progress_summary('pkg-0'),
            lambda: self.store.progress_summaries(['pkg-0']),
            lambda: self.store.trouble_sections('pkg-0')]
        for fn in operations:
            with self.subTest(operation=str(fn)): self.tracked_call(fn)

    def record(self, ident):
        record_wait_session(self.store, session_id=ident, package_id='pkg-0',
            mode='WAIT_FOR_NOTE', hand_selection=None, section_id='s1',
            tempo_scale=1., started_at='2026-10-10T00:00:00Z', ended_at=None,
            total_steps=2, completed_steps=1, incorrect_attempts=0, completed=False)

    def test_success_commits_and_survives_fresh_connection(self):
        self.tracked_call(lambda: self.record('committed'))
        with closing(REAL_CONNECT(self.store._path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone(), (1,))

    def test_body_exception_rolls_back_and_closes(self):
        def run():
            with self.assertRaisesRegex(RuntimeError, 'body fault'):
                with self.store._connect() as conn:
                    conn.execute("INSERT INTO meta VALUES ('rolled-back', 'no')")
                    raise RuntimeError('body fault')
        self.tracked_call(run)
        with closing(REAL_CONNECT(self.store._path)) as conn:
            self.assertIsNone(conn.execute("SELECT value FROM meta WHERE key='rolled-back'").fetchone())

    def test_journal_pragma_exception_closes_before_yield(self):
        class Fault(sqlite3.Connection):
            def execute(self, sql, *args):
                if 'journal_mode' in sql: raise sqlite3.OperationalError('pragma fault')
                return super().execute(sql, *args)
        def run():
            with self.assertRaisesRegex(sqlite3.OperationalError, 'pragma fault'):
                self.store.list_packages()
        self.tracked_call(run, Fault)

    def test_row_factory_configuration_exception_closes(self):
        class Fault(sqlite3.Connection):
            def __setattr__(self, name, value):
                if name == 'row_factory': raise RuntimeError('row factory fault')
                return super().__setattr__(name, value)
        def run():
            with self.assertRaisesRegex(RuntimeError, 'row factory fault'):
                self.store.list_packages()
        self.tracked_call(run, Fault)

    def test_foreign_key_pragma_exception_closes_before_yield(self):
        class Fault(sqlite3.Connection):
            def execute(self, sql, *args):
                if 'foreign_keys' in sql: raise sqlite3.OperationalError('foreign key pragma fault')
                return super().execute(sql, *args)
        def run():
            with self.assertRaisesRegex(sqlite3.OperationalError, 'pragma fault'):
                self.store.list_packages()
        self.tracked_call(run, Fault)

    def test_schema_exception_closes(self):
        class Fault(sqlite3.Connection):
            def executescript(self, *args): raise sqlite3.OperationalError('schema fault')
        def run():
            with self.assertRaisesRegex(sqlite3.OperationalError, 'schema fault'):
                PackageStore(self.root/'schema-fault.db')
        self.tracked_call(run, Fault)

    def test_commit_exception_still_closes_and_discards_uncommitted_write(self):
        class Fault(sqlite3.Connection):
            def __exit__(self, typ, value, tb):
                if typ is None: raise sqlite3.OperationalError('commit fault')
                return super().__exit__(typ, value, tb)
        def run():
            with self.assertRaisesRegex(sqlite3.OperationalError, 'commit fault'):
                self.record('failed-commit')
        self.tracked_call(run, Fault)
        self.assertEqual(self.store.sessions_for_package('pkg-0'), [])

    def test_rollback_exception_still_closes(self):
        class Fault(sqlite3.Connection):
            def __exit__(self, typ, value, tb): raise sqlite3.OperationalError('rollback fault')
        def run():
            with self.assertRaisesRegex(sqlite3.OperationalError, 'rollback fault'):
                with self.store._connect() as conn:
                    conn.execute("INSERT INTO meta VALUES ('discard', 'no')")
                    raise RuntimeError('original body fault')
        self.tracked_call(run, Fault)
        with closing(REAL_CONNECT(self.store._path)) as conn:
            self.assertIsNone(conn.execute("SELECT value FROM meta WHERE key='discard'").fetchone())

    def test_connect_failure_propagates_without_unbound_cleanup(self):
        with patch('piano_core.packages.store.sqlite3.connect', side_effect=sqlite3.OperationalError('open fault')):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'open fault'):
                self.store.list_packages()

    def test_read_exception_closes_and_next_operation_recovers(self):
        class Fault(sqlite3.Connection):
            def execute(self, sql, *args):
                if 'SELECT * FROM packages' in sql: raise sqlite3.OperationalError('read fault')
                return super().execute(sql, *args)
        def run():
            with self.assertRaisesRegex(sqlite3.OperationalError, 'read fault'):
                self.store.get_package('pkg-0')
        self.tracked_call(run, Fault)
        self.assertEqual(self.store.get_package('pkg-0').package_id, 'pkg-0')


def resource_child(output):
    """Own disposable process/DB/server. Partial evidence survives a single failed run."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(1024, soft), hard))
    gc.disable()
    report = {'python':sys.version, 'sqlite':sqlite3.sqlite_version,
              'soft_fd_limit':resource.getrlimit(resource.RLIMIT_NOFILE)[0],
              'cyclic_gc':'disabled identically for negative and positive control',
              'conditions':'50 lessons;4 readers*10 full50rowHTTPpages +1 writer*50 sessions;no retries',
              'samples':[], 'completed_reads':0, 'completed_writes':0, 'errors':[]}
    lock = threading.Lock(); stop = threading.Event()
    # Reserve the evidence handle before load: even a descriptor-exhaustion
    # negative control must preserve its first failure without opening a file.
    evidence = open(output, 'w')
    def save():
        with lock:
            evidence.seek(0); evidence.truncate()
            json.dump(report, evidence, indent=2); evidence.write('\n'); evidence.flush()
    def sample(phase):
        inv = fd_inventory()
        with lock: report['samples'].append({'phase':phase,'monotonic':time.monotonic(),
            'count':len(inv),'sqlite_count':sum('.db' in p or '.sqlite' in p for p in inv.values()),
            'descriptors':inv})
        if len(inv) >= 768:
            stop.set()
            raise RuntimeError('predeclared FD headroom abort: actual descriptors >= 768')
    exit_code = 1
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);packages=PackageService(root/'packages',root/'packages.db');store=packages.store
        db=root/'catalog.sqlite3';_seed_catalog(db);cat=LearnCatalog(db)
        template=asdict(cat.lessons()[0]);course=asdict(cat.courses()[0]);course['lesson_count']=50
        lessons=[]
        for n in range(50):
            ident=f'pkg-{n}';store.insert_package(package_row(ident,root),{})
            key=f'pianio:r2:lesson-{n}';lessons.append({**template,'lesson_id':lesson_id_for(key),
                'lesson_key':key,'package_id':ident,'order_index':n,'title':f'Resource Lesson {n}'})
        cat.replace_catalog([course],lessons);cat.close()
        service=LearnService(db,progress_lookup=store.progress_summary,progress_all_lookup=store.learning_progress,
                             progress_many_lookup=store.progress_summaries)
        server=ApiServer(SimpleNamespace(learn_service=service,learn_ready=True,packages_service=packages),'127.0.0.1',0,serve_ui=False)
        server.start();host,port=server.bound_address;barrier=threading.Barrier(5)
        sample('before')
        def reader(n):
            barrier.wait()
            for i in range(10):
                if stop.is_set():break
                try:
                    with urllib.request.urlopen(f'http://{host}:{port}/api/v1/learn/lessons?limit=50',timeout=10) as f:
                        assert f.status==200;data=json.load(f)
                    assert data['count']==50
                    assert {x['package_id'] for x in data['lessons']}=={f'pkg-{j}' for j in range(50)}
                    assert all(x['progress_available'] and x['progress'] is not None for x in data['lessons'])
                    with lock:report['completed_reads']+=1
                    sample('during-reader')
                except Exception as e:
                    with lock:report['errors'].append(type(e).__name__+': '+str(e))
                    stop.set();break
        def writer():
            barrier.wait()
            for i in range(50):
                if stop.is_set():break
                try:
                    record_wait_session(store,session_id=f'r2-{i}',package_id=f'pkg-{i}',mode='WAIT_FOR_NOTE',
                        hand_selection=None,section_id=None,tempo_scale=1.,started_at=f'2026-10-10T00:00:{i:02d}Z',
                        ended_at=None,total_steps=2,completed_steps=1,incorrect_attempts=0,completed=False)
                    with lock:report['completed_writes']+=1
                    sample('during-writer')
                except Exception as e:
                    with lock:report['errors'].append(type(e).__name__+': '+str(e))
                    stop.set();break
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures=[pool.submit(reader,n) for n in range(4)]+[pool.submit(writer)]
                for f in futures:f.result(timeout=90)
            sample('after-load')
            # A fresh explicitly closed connection checks every committed session.
            with closing(REAL_CONNECT(store._path)) as conn:
                rows=conn.execute('SELECT session_id,package_id,expected_count,correct_count,missed_count FROM sessions').fetchall()
                report['persisted_sessions']=len(rows)
                assert set(rows)=={(f'r2-{i}',f'pkg-{i}',2,1,1) for i in range(report['completed_writes'])}
            assert report['errors']==[],report['errors']
            assert report['completed_reads']==40 and report['completed_writes']==50
            assert len(fd_inventory())<=report['samples'][0]['count']+4
            assert max(x['count'] for x in report['samples'])<=report['samples'][0]['count']+40
            exit_code=0
        except Exception as e:
            report['errors'].append(type(e).__name__+': '+str(e))
        finally:
            # Save before cleanup as well: shutdown must not erase a failed run.
            report['verdict']='PASS' if exit_code==0 else 'FAIL';save()
            server.shutdown();service.close()
            try:
                sample('after-service-close')
                assert report['samples'][-1]['count'] <= report['samples'][0]['count']
            except Exception as e:
                report['errors'].append(type(e).__name__+': '+str(e))
                exit_code = 1
                report['verdict'] = 'FAIL'
            save(); evidence.close()
    return exit_code


class NativeResourceTests(unittest.TestCase):
    def test_full50_lesson_parallel_progress_with_writes_has_bounded_real_fds(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'resource.json'
            env=dict(os.environ)
            project=Path(__file__).resolve().parents[1]
            env['PYTHONPATH']=str(project/'src')+os.pathsep+str(project)
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--resource-child',str(out)],
                cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr[-8000:]+(out.read_text() if out.exists() else 'no evidence'))
            data=json.loads(out.read_text());self.assertEqual(data['verdict'],'PASS')
            self.assertEqual(data['persisted_sessions'],50)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--resource-child':sys.exit(resource_child(sys.argv[2]))
    unittest.main()
