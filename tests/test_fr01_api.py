"""FR-01 mandatory real Application/API integration, synthetic MIDI boundary only."""
import json
import os
import queue
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from piano_core.app import Application
from piano_core.library.service import LibraryConfig, LibraryService
from tests.test_fr01_recovery import Backend
from tests.test_midi_input import msg
from tests.weblogic.real_backend_fixture import midi, port, OutputBackend


class RecordingOutput(OutputBackend):
    def __init__(self):
        super().__init__()
        self.note_sent = threading.Event()

    def send_note_on(self, *args):
        super().send_note_on(*args)
        self.note_sent.set()


class RecoveryAPITests(unittest.TestCase):
    def setUp(self):
        self.trace = []
        self.temp = tempfile.TemporaryDirectory(prefix='piano-fr01-')
        self.root = Path(self.temp.name)
        self.source = self.root/'midi'
        self.source.mkdir()
        midi(self.source/'repeated.mid', [[60],[60],[62]], tail=9600)
        midi(self.source/'chords.mid', [[60,64,67],[62,65,69]], tail=9600)
        midi(self.source/'long.mid', [[72]], tail=96000)
        library = LibraryService(LibraryConfig(str(self.source),str(self.root/'index.db')))
        library.scan()
        self.ids = {r.relpath:r.song_id for r in library.list_songs(limit=100)}
        self.source.chmod(0o555)
        self.input = Backend()
        self.output = RecordingOutput()
        self.processed = queue.Queue()
        self.app = Application.from_env({
            'PIANO_CORE_MODE':'test','PIANO_CORE_LOG_LEVEL':'WARNING',
            'PIANO_CORE_MIDI_INPUT_ENABLED':'true','PIANO_CORE_MIDI_INPUT_PORT_MATCHER':'Roland',
            'PIANO_CORE_MIDI_OUTPUT_ENABLED':'true','PIANO_CORE_MIDI_OUTPUT_PORT_MATCHER':'Functional',
            'PIANO_CORE_LIBRARY_ENABLED':'true','PIANO_CORE_LIBRARY_ROOT':str(self.source),
            'PIANO_CORE_LIBRARY_INDEX_PATH':str(self.root/'index.db'),
            'PIANO_CORE_API_ENABLED':'true','PIANO_CORE_API_HOST':'127.0.0.1',
            'PIANO_CORE_API_PORT':str(port()),
        }, midi_backend_factory=lambda:self.input, midi_output_backend_factory=lambda:self.output,
            playback_lock_path=str(self.root/'activity.lock'))
        # Observation barrier AFTER the actual input pipeline, not a replacement.
        original = self.app._on_input_event
        def observe(event):
            original(event)
            self.processed.put(event)
        self.app._on_input_event = observe
        self.addCleanup(self.cleanup)
        self.app.start()
        self.assertIsNotNone(self.app.api_server)
        self.assertTrue(self.app.library_ready)
        host, number = self.app.api_server.bound_address
        self.origin = f'http://{host}:{number}/api/v1'
        self.handle = self.input.opened.get(timeout=2)
        self.send('note_off',60)  # barrier for successful opening and pipeline.

    def cleanup(self):
        for h in self.input.handles:
            if h.close_release is not None:h.close_release.set()
        self.app.shutdown()
        self.source.chmod(0o755)
        self.temp.cleanup()
        evidence = os.environ.get('FR01_EVIDENCE_DIR')
        if evidence:
            directory = Path(evidence)
            directory.mkdir(parents=True,exist_ok=True)
            (directory/(self._testMethodName+'.json')).write_text(json.dumps({
                'backend':'real Application/ApiServer', 'io':'synthetic MIDI adapters',
                'origin':getattr(self,'origin',None), 'observations':self.trace},indent=2)+'\n')

    def api(self,path,body=None):
        request=urllib.request.Request(self.origin+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=3) as r:
                status, result = r.status, json.load(r)
        except urllib.error.HTTPError as e:
            status, result = e.code, json.load(e)
        self.trace.append({'path':path,'request':body,'status':status,'result':result})
        return status,result

    def send(self,kind,note=60,**fields):
        self.handle.pending.put(msg(kind,note=note,**fields))
        return self.processed.get(timeout=2)

    def disconnect(self):
        old=self.handle
        old.close_release=threading.Event()
        old.pending.put(OSError('FR-01 real API injected read failure'))
        self.assertTrue(old.close_entered.wait(2))
        status,health=self.api('/health')
        self.assertEqual(status,200)
        self.assertEqual(health['midi_input']['connection'],'DISCONNECTED')
        status,state=self.api('/state')
        self.assertEqual(state['pressed_notes'],[])
        self.assertFalse(state['sustain']['pressed'])
        return old

    def recover(self,old):
        old.close_release.set()
        self.handle=self.input.opened.get(timeout=5)  # Actual default 2s backoff.
        self.send('note_off',100)  # barrier through the new real connection.
        self.assertEqual(self.api('/health')[1]['midi_input']['connection'],'CONNECTED')
        self.assertEqual(old.close_count,1)
        self.assertEqual(self.input.max_readers,1)
        self.assertEqual(len(self.input.reader_threads),1)

    def test_read_fault_real_api_cleanup_recovery_and_new_input(self):
        self.send('note_on',60)
        self.send('control_change',control=64,value=127)
        self.assertEqual(self.api('/state')[1]['pressed_notes'],[60])
        old=self.disconnect()
        # Old queued events are never replayed through the recovered connection.
        old.pending.put(msg('note_on',note=99))
        self.recover(old)
        before=self.app.midi_adapter.events_received
        self.send('note_on',65)
        state=self.api('/state')[1]
        self.assertEqual(state['pressed_notes'],[65])
        self.assertEqual(state['last_input']['note'],65)
        self.assertEqual(self.app.midi_adapter.events_received,before+1)

    def test_recovery_does_not_clean_playback_owned_output(self):
        status,playing=self.api('/playback/play',{'song_id':self.ids['long.mid']})
        self.assertEqual(status,200)
        self.assertTrue(self.output.note_sent.wait(2))
        self.send('note_on',60)
        self.send('control_change',control=64,value=127)
        sent=list(self.output.sent)
        old=self.disconnect()
        self.assertEqual(self.output.sent,sent)
        self.assertEqual(self.api('/live-notes')[1]['playback']['notes'],[72])
        self.assertEqual(self.api('/playback')[1]['state'],'PLAYING')
        self.recover(old)
        self.assertEqual(self.output.sent,sent)
        self.assertEqual(self.api('/playback')[1]['song_id'],playing['song_id'])
        self.assertEqual(self.api('/learning/start',{'song_id':self.ids['repeated.mid']})[0],409)
        self.assertEqual(self.api('/playback/stop',{})[0],200)

    def test_practice_chord_and_identity_survive_recovery_without_progress(self):
        status,session=self.api('/learning/start',{'song_id':self.ids['chords.mid']})
        self.assertEqual(status,200)
        self.send('note_on',60)
        self.send('note_on',64)
        before=self.api('/learning')[1]
        self.assertEqual(before['completed_steps'],0)
        old=self.disconnect()
        failed=self.api('/learning')[1]
        self.assertEqual(failed['latest_outcome'],'INPUT_UNAVAILABLE')
        self.assertEqual(failed['completed_steps'],0)
        self.assertEqual(failed['accepted_notes'],[])
        old.pending.put(msg('note_on',note=67))
        self.recover(old)
        recovered=self.api('/learning')[1]
        self.assertEqual(recovered['session_id'],session['session_id'])
        self.assertEqual(recovered['target'],session['target'])
        self.assertEqual(recovered['completed_steps'],0)
        self.assertGreater(recovered['version'],before['version'])
        self.assertEqual(self.output.sent,[])
        self.assertEqual(self.api('/playback/play',{'song_id':self.ids['long.mid']})[0],409)
        self.send('note_on',61)  # wrong note remains wrong after recovery.
        self.assertEqual(self.api('/learning')[1]['completed_steps'],0)
        self.send('note_off',61)
        self.send('note_on',67)
        self.assertEqual(self.api('/learning')[1]['completed_steps'],0)
        self.send('note_on',60)
        self.send('note_on',64)
        self.assertEqual(self.api('/learning')[1]['completed_steps'],1)
        self.assertEqual(self.output.sent,[])

    def test_repeated_note_release_rule_and_progress_preserved_across_recovery(self):
        self.assertEqual(self.api('/learning/start',{'song_id':self.ids['repeated.mid']})[0],200)
        self.send('note_on',60)
        self.send('note_on',60)
        self.assertEqual(self.api('/learning')[1]['completed_steps'],1)
        self.send('note_off',60)
        self.send('note_on',60)
        before=self.api('/learning')[1]
        self.assertEqual(before['completed_steps'],2)
        old=self.disconnect()
        self.recover(old)
        after=self.api('/learning')[1]
        self.assertEqual(after['session_id'],before['session_id'])
        self.assertEqual(after['target'],before['target'])
        self.assertEqual(after['completed_steps'],2)
        self.assertEqual(self.output.sent,[])

    def test_read_fault_then_invalid_port_open_race_restores_real_api_input(self):
        """Fault only at the MIDI adapter; Application/API remain authoritative."""
        from rtmidi import InvalidPortError

        self.send('note_on',60)
        old=self.handle
        open_failed=threading.Event()
        original_open=self.input.open_input

        def open_with_signal(port):
            try:
                return original_open(port)
            except InvalidPortError:
                open_failed.set()
                raise

        self.input.open_input=open_with_signal
        self.input.open_errors.put(InvalidPortError('selected port vanished'))
        old.close_release=threading.Event()
        old.pending.put(OSError('read fault before selected-port open race'))
        self.assertTrue(old.close_entered.wait(2))
        self.assertEqual(self.api('/health')[1]['midi_input']['connection'],'DISCONNECTED')
        self.assertEqual(self.api('/state')[1]['pressed_notes'],[])
        old.close_release.set()
        self.assertTrue(open_failed.wait(4))
        self.assertEqual(self.api('/health')[1]['midi_input']['connection'],'DISCONNECTED')
        self.assertEqual(len(self.input.handles),1)
        self.handle=self.input.opened.get(timeout=5)
        before=self.app.midi_adapter.events_received
        self.send('note_on',65)
        self.assertEqual(self.api('/health')[1]['midi_input']['connection'],'CONNECTED')
        state=self.api('/state')[1]
        self.assertEqual(state['pressed_notes'],[65])
        self.assertEqual(state['last_input']['note'],65)
        self.assertEqual(self.app.midi_adapter.events_received,before+1)
        self.assertEqual(old.close_count,1)
        self.assertEqual(self.input.max_readers,1)
        self.assertEqual(len(self.input.reader_threads),1)
        self.assertEqual(self.output.sent,[])
