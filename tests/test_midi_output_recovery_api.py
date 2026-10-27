"""Real local Application/API recovery with disposable library and fake MIDI."""
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from piano_core.app import Application
from piano_core.library.service import LibraryConfig,LibraryService
from tests.test_midi_input import wait_for
from tests.test_midi_output_recovery import DisconnectableOutput
from tests.weblogic.real_backend_fixture import midi,port

class RecordingOutput(DisconnectableOutput):
    def __init__(self):
        super().__init__()
        self.attack=threading.Event()
    def send_note_on(self,*args):
        super().send_note_on(*args)
        self.attack.set()

class APIRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='piano-output-api-')
        root=Path(self.temp.name);source=root/'midi';source.mkdir()
        midi(source/'held.mid',[[72]],tail=96000)
        service=LibraryService(LibraryConfig(str(source),str(root/'index.db')))
        service.scan();self.song=service.list_songs(limit=1)[0].song_id
        source.chmod(0o555)
        self.output=RecordingOutput()
        self.app=Application.from_env({'PIANO_CORE_MODE':'test','PIANO_CORE_LOG_LEVEL':'ERROR',
            'PIANO_CORE_MIDI_OUTPUT_ENABLED':'true','PIANO_CORE_LIBRARY_ENABLED':'true',
            'PIANO_CORE_LIBRARY_ROOT':str(source),'PIANO_CORE_LIBRARY_INDEX_PATH':str(root/'index.db'),
            'PIANO_CORE_API_ENABLED':'true','PIANO_CORE_API_HOST':'127.0.0.1','PIANO_CORE_API_PORT':str(port())},
            midi_output_backend_factory=lambda:self.output,playback_lock_path=str(root/'activity.lock'))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(source.chmod,0o755)
        self.addCleanup(self.app.shutdown)
        self.app.start();host,number=self.app.api_server.bound_address
        self.origin=f'http://{host}:{number}/api/v1'

    def api(self,path,body=None):
        request=urllib.request.Request(self.origin+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:
            self.assertEqual(response.status,200);return json.load(response)

    def reconnect(self):
        old=self.output.outputs[-1];old.connected=False
        self.assertTrue(wait_for(lambda:len(self.output.outputs)>=2 and self.app.midi_output.is_open,2))
        self.assertTrue(old.closed)
        self.assertEqual(self.output.max_handles,1)
        return self.output.outputs[-1]

    def test_active_playback_fails_then_explicit_fresh_play_once(self):
        first=self.api('/playback/play',{'song_id':self.song})
        self.assertTrue(self.output.attack.wait(2))
        self.output.outputs[0].connected=False
        self.assertTrue(wait_for(lambda:self.api('/playback')['state']=='FAILED',2))
        failed=self.api('/playback')
        self.assertEqual(failed['song_id'],first['song_id'])
        self.assertIn('output',failed['error'])
        self.assertTrue(wait_for(lambda:len(self.output.outputs)==2 and self.app.midi_output.is_open,2))
        fresh=self.output.outputs[1]
        self.assertEqual(fresh.sent,[('note_off',1,72,0)])
        self.assertEqual(self.api('/playback')['state'],'FAILED')
        self.assertTrue(self.api('/playback')['available'])
        self.output.attack.clear()
        self.api('/playback/play',{'song_id':self.song})
        self.assertTrue(self.output.attack.wait(2))
        self.assertEqual([x for x in fresh.sent if x[0]=='note_on'],[('note_on',1,72,80)])
        self.api('/playback/stop',{})
        notes=self.api('/live-notes')['playback']
        self.assertEqual(notes['notes'],[]);self.assertEqual(notes['voices'],[])
        self.assertEqual(notes['sustain_channels'],[])
        self.assertEqual(self.output.max_handles,1)

    def test_output_reconnect_preserves_silent_practice_identity_and_target(self):
        self.api('/learning/start',{'song_id':self.song})
        before=self.api('/learning')
        fresh=self.reconnect()
        after=self.api('/learning')
        for field in ['session_id','song_id','state','target','completed_steps','version']:
            self.assertEqual(after[field],before[field],field)
        self.assertEqual(fresh.sent,[])
        self.assertEqual(self.api('/state')['pressed_notes'],[])
        self.api('/learning/stop',{})

    def test_stop_during_retry_does_not_replay_after_reconnect(self):
        self.api('/playback/play', {'song_id':self.song})
        self.assertTrue(self.output.attack.wait(2))
        self.output.ports = []
        self.assertTrue(wait_for(lambda:self.api('/playback')['state']=='FAILED',2))
        self.assertFalse(self.app.midi_output.is_open)
        # Existing manager contract rejects all transport commands when
        # output is unavailable. The active worker must already be failed.
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.api('/playback/stop', {})
        self.assertEqual(failure.exception.code, 503)
        stopped = self.api('/playback')['state']
        self.assertEqual(stopped, 'FAILED')
        self.output.ports = ['Roland OUT 20:0']
        self.assertTrue(wait_for(lambda:len(self.output.outputs)==2 and self.app.midi_output.is_open,2))
        self.assertEqual(self.api('/playback')['state'], stopped)
        self.assertEqual(self.output.outputs[-1].sent, [('note_off',1,72,0)])
        self.assertEqual(self.app.midi_output.owned_notes, ())
        self.assertEqual(self.output.max_handles, 1)
