"""Disposable loopback Core for Chromium U8-P7 tests. Synthetic input only.

Real P009 MIDI loading, PracticeManager/Engine, HTTP guards, StateStore,
LiveNoteState and SQLite. Fixture catalog/package identities are deliberately
small and synthetic. No hardware adapter, MIDI output or hosted fallback.
"""
import json
import signal
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import mido
from piano_core.api.server import ApiServer
from piano_core.api.handlers import ROUTES, Route
from piano_core.practice.manager import PracticeManager
from piano_core.practice.plan import build_practice_plan
from piano_core.playback.timeline import load_timeline
from piano_core.practice.model import PracticePlan
from piano_core.state import StateStore, MidiConnectionState
from piano_core.live_notes import LiveNoteState
from piano_core.midi.events import note_on, note_off
from piano_core.packages.store import PackageStore, PackageRow
from piano_core.packages.sessions import record_engine_session

with tempfile.TemporaryDirectory(prefix='piano-studio-core-') as folder:
    root = Path(folder)
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack(); midi.tracks.append(track)
    track.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    for i in range(4):
        track.append(mido.Message('note_on', note=60, velocity=85, time=0 if i == 0 else 720))
        track.append(mido.Message('note_off', note=60, velocity=0, time=240))
    track.append(mido.MetaMessage('end_of_track', time=720))
    path = root / 'lesson.mid'; midi.save(path)
    plan = build_practice_plan(load_timeline(path))
    store = StateStore(); store.set_midi_connection(MidiConnectionState.CONNECTED)
    live = LiveNoteState(heartbeat_seconds=0.2); live.publish_physical(store.snapshot())
    db = PackageStore(root / 'progress.db')
    db.insert_package(PackageRow('pkg-1', 'Middle C Pulse', 1, 'a'*64, 'b'*64, 'easy', 'available', 4, 0, 4, 0, 4, 'synthetic', None, {}, folder, '2026-10-09T00:00:00Z', 'active'), {})
    class Library:
        def get(self, song_id):
            return SimpleNamespace(title='Middle C Pulse', filename='lesson.mid') if song_id == 'package:pkg-1' else None
    class Packages:
        def build_plan(self, package_id, *, hand=None, section_id=None):
            if package_id != 'pkg-1': raise ValueError('unknown fixture package')
            chosen = plan if hand != 'LH' else PracticePlan((), 'synthetic', 0, 4)
            return chosen, None
    manager = PracticeManager(Library(), store, lock_path=str(root / 'activity.lock'), session_recorder=lambda result: record_engine_session(db, result))
    context = SimpleNamespace(practice=manager, packages_service=Packages(), state_store=store, live_notes=live, learn_ready=True, learn_service=SimpleNamespace(lesson_by_package=lambda _: {'lesson_id': 'les-1'}))
    def inject(context, request):
        body = json.loads(request.body)
        event = (note_on if body.get('pressed', True) else note_off)(1, body['note'], 85 if body.get('pressed', True) else 0, time.monotonic())
        store.apply_event(event)
        manager.observe_input_event(event)
        live.publish_physical(store.snapshot())
        return 200, {'synthetic': True, 'state': manager.snapshot().state.value}
    def connection(context, request):
        state = MidiConnectionState.CONNECTED if json.loads(request.body)['connected'] else MidiConnectionState.DISCONNECTED
        store.set_midi_connection(state); manager.observe_connection_state(state); live.publish_physical(store.snapshot())
        return 200, {'synthetic': True}
    def progress(context, request):
        return 200, {'progress': db.learning_progress(), 'pending': manager.pending_results}
    server = ApiServer(context, '127.0.0.1', 0, serve_ui=False)
    server._httpd.routes = ROUTES + (Route('POST', '/api/v1/_test/input', inject), Route('POST', '/api/v1/_test/connection', connection), Route('GET', '/api/v1/_test/progress', progress))
    server.start()
    print(json.dumps({'port': server.bound_address[1]}), flush=True)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    try: done.wait()
    finally: live.close(); server.shutdown(); manager.shutdown()
