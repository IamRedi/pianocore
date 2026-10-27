"""Disposable real SQLite/LearnService/API/UI, synthetic fixture data only."""
import json
import signal
import tempfile
import threading
import time
import os
from pathlib import Path
from types import SimpleNamespace
from piano_core.api.server import ApiServer
from piano_core.api.handlers import ROUTES, Route
from piano_core.learn.service import LearnService
from piano_core.packages.store import PackageStore, PackageRow
from piano_core.live_notes import LiveNoteState
from piano_core.state import StateStore, MidiConnectionState
from tests.test_learn_library import _seed_catalog

with tempfile.TemporaryDirectory(prefix='piano-release-harness-core-') as folder:
    root = Path(folder)
    _seed_catalog(root / 'catalog.db')
    store = PackageStore(root / 'progress.db')
    for ident in ['pkg-1', 'pkg-2']:
        store.insert_package(PackageRow(ident, ident, 1, 'a'*64, 'b'*64, 'easy',
            'available', 4, 0, 4, 0, 4, 'synthetic', None, {}, folder,
            '2026-10-12T00:00:00Z', 'active'), {})
    def record(package):
        store.record_session(session_id=str(time.monotonic_ns()), package_id=package,
            mode='FLOW', hand_selection=None, section_id=None, tempo_scale=1,
            started_at='2026-10-12T00:00:01Z', ended_at='2026-10-12T00:00:02Z',
            expected_count=4, correct_count=0, missed_count=4, extra_count=0,
            chord_completions=0, best_streak=0, elapsed_seconds=8, completed=True,
            detail={'full_lesson': True})
    record('pkg-2')
    learn = LearnService(root / 'catalog.db', progress_lookup=store.progress_summary,
                         progress_all_lookup=store.learning_progress,
                         progress_many_lookup=store.progress_summaries)
    state = StateStore(); state.set_midi_connection(MidiConnectionState.CONNECTED)
    live = LiveNoteState(heartbeat_seconds=0.05); live.publish_physical(state.snapshot())
    context = SimpleNamespace(learn_service=learn, learn_ready=True, state_store=state, live_notes=live)
    release = threading.Event()
    def complete(context, request):
        record(json.loads(request.body)['package_id'])
        return 200, {'synthetic_disposable': True}
    def resources(context, request):
        return 200, {'actual_fds': len(os.listdir('/proc/self/fd')), 'live_subscribers': live._subscribers}
    def slow(context, request):
        assert release.wait(10), 'owned slow request release deadline'
        return 200, {'released': True}
    def unblock(context, request):
        release.set()
        return 200, {'released': True}
    server = ApiServer(context, '127.0.0.1', 0, serve_ui=True)
    server._httpd.routes = ROUTES + (Route('POST', '/api/v1/_test/complete', complete), Route('GET', '/api/v1/_test/resources', resources), Route('GET', '/api/v1/_test/slow', slow), Route('POST', '/api/v1/_test/release', unblock))
    server.start()
    print(json.dumps({'port': server.bound_address[1], 'lessons': learn.lessons()['lessons']}), flush=True)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    try: done.wait()
    finally: live.close(); server.shutdown(); learn.close()
