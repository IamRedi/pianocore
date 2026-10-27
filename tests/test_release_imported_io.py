"""Disposable real socket controls for the imported release consumers."""
import http.server, importlib, json, os, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
from release_harness import bounded_http_json, bounded_sse, quiescence
sys.modules.setdefault('bounded_http_json',bounded_http_json)
sys.modules.setdefault('bounded_sse',bounded_sse)
sys.modules.setdefault('quiescence',quiescence)
from release_harness import sse_lifecycle, fd_monitor

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        try:
            if self.path=='/headers':
                for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}':
                    self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.02)
                return
            self.send_response(200)
            self.send_header('X-Piano-FD-Sample','sampled')
            self.send_header('X-Piano-Process-PID','123')
            self.send_header('X-Piano-Process-FDs','24')
            self.send_header('X-Piano-ALSA-FDs','2')
            self.end_headers()
            if self.path=='/body':
                for byte in b'{"status":"ok"}':
                    self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.02)
            elif self.path=='/frame':
                for byte in b'data: {"runtime_epoch":"old"}\n\n':
                    self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.02)
            elif self.path in ['/coalesced','/split']:
                self.wfile.write(b'data: {"runtime_epoch":"old"}\n\n');self.wfile.flush()
                if self.path=='/split':self.server.release.wait(3)
                self.wfile.write(b'HTTP/1.1 500 Invalid second response\r\n\r\n');self.wfile.flush()
            elif self.path=='/sse':
                self.wfile.write(('data: '+json.dumps({'runtime_epoch':getattr(self.server,'epoch','old')})+'\n\n').encode());self.wfile.flush()
                self.server.release.wait(3)
            else:self.wfile.write(b'{"status":"ok","midi_input":{"connection":"CONNECTED"},"application":{"state":"READY"}}')
        except (BrokenPipeError,ConnectionResetError):pass

class ImportedIoTests(unittest.TestCase):
    def setUp(self):
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True;self.server.release=threading.Event()
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01));self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.tmp=tempfile.TemporaryDirectory()
    def tearDown(self):
        self.server.release.set();self.server.shutdown();self.thread.join(2);self.server.server_close();self.tmp.cleanup()
    def assert_gone(self,pid):
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)
    def test_real_SSE_snapshot_EOF_and_reap(self):
        peer=bounded_sse.Peer(self.base+'/sse',timeout=1)
        self.assertEqual(peer.snapshot['initial_epoch'],'old');self.assertIsNone(peer.child.poll())
        self.server.release.set();self.assertTrue(peer.eof()['eof']);self.assert_gone(peer.child.pid)
    def test_owned_SSE_close_reaps_open_peer(self):
        peer=bounded_sse.Peer(self.base+'/sse',timeout=1);peer.close();peer.close();self.assertTrue(peer.closed);self.assert_gone(peer.child.pid)
    def test_SSE_header_trickle_deadline_and_reap(self):
        self.assert_handshake_fails('/headers')
    def test_SSE_frame_trickle_deadline_and_reap(self):
        self.assert_handshake_fails('/frame')
    def assert_handshake_fails(self,path):
        children=[];popen=bounded_sse.subprocess.Popen
        def owned(*args,**kwargs):
            child=popen(*args,**kwargs);children.append(child);return child
        started=time.monotonic()
        with patch.object(bounded_sse.subprocess,'Popen',owned):
            with self.assertRaises(TimeoutError):bounded_sse.Peer(self.base+path,timeout=.15)
        self.assertLess(time.monotonic()-started,1);self.assertEqual(len(children),1);self.assert_gone(children[0].pid)
    def test_SSE_EOF_deadline_reaps_open_peer(self):
        peer=bounded_sse.Peer(self.base+'/sse',timeout=1)
        with self.assertRaises(bounded_sse.subprocess.TimeoutExpired):peer.eof(timeout=.15)
        self.assert_gone(peer.child.pid)
    def test_coalesced_second_HTTP_response_rejected(self):
        self.assert_second_status_rejected('/coalesced')
    def test_split_second_HTTP_response_rejected(self):
        self.assert_second_status_rejected('/split')
    def assert_second_status_rejected(self,path):
        peer=bounded_sse.Peer(self.base+path,timeout=1);self.server.release.set()
        with self.assertRaises(AssertionError):peer.eof()
        self.assert_gone(peer.child.pid)
    def test_SSE_health_preserves_actual_headers(self):
        def fetch(url,**kwargs):return bounded_http_json.fetch(self.base+'/',**kwargs)
        with patch.object(sse_lifecycle,'fetch',fetch):data=sse_lifecycle.health()
        self.assertEqual((data['pid'],data['fds']),(123,24))
    def test_SSE_health_trickle_deadline(self):
        def fetch(url,**kwargs):return bounded_http_json.fetch(self.base+'/body',**kwargs)
        with patch.object(sse_lifecycle,'fetch',fetch):
            with self.assertRaises(TimeoutError):sse_lifecycle.health(timeout=.15)
    def test_three_real_streams_active_at_actual_helper_boundary(self):
        peers=[]
        def peer(url):
            value=bounded_sse.Peer(self.base+'/sse');peers.append(value);return value
        observations=iter([{'pid':1},{'pid':2,'status':'ok','midi_input':{'connection':'CONNECTED'}}])
        def execute(name,args,before_helper):
            self.assertTrue(all(p.child.poll() is None for p in peers));before_helper()
            self.server.epoch='new';self.server.release.set()
        with patch.object(sse_lifecycle,'Peer',peer),patch.object(sse_lifecycle,'health',lambda **kw:next(observations)):
            proof=sse_lifecycle.run(self.tmp.name,{'wheel':'exact','sha256':'hash'},execute)
        self.assertTrue(proof['complete']);self.assertEqual(proof['active_peers_at_helper_boundary'],3)
        self.assertTrue(proof['owned_readers_stopped']);self.assertEqual(proof['fresh_epoch'],'new')
        for peer in peers:self.assert_gone(peer.child.pid)
    def test_all_EOF_during_safety_check_blocks_actual_helper(self):
        peers=[];mutated=[]
        def peer(url):
            value=bounded_sse.Peer(self.base+'/sse');peers.append(value);return value
        def execute(name,args,before_helper):
            self.server.release.set()
            for p in peers:p.child.wait(timeout=1)
            before_helper();mutated.append(True)
        with patch.object(sse_lifecycle,'Peer',peer),patch.object(sse_lifecycle,'health',lambda:{'pid':1}):
            with self.assertRaises(AssertionError):sse_lifecycle.run(self.tmp.name,{'wheel':'exact','sha256':'hash'},execute)
        self.assertEqual(mutated,[])
        for peer in peers:self.assert_gone(peer.child.pid)
    def test_FD_monitor_retains_actual_samples(self):
        monitor=fd_monitor.Monitor(self.tmp.name)
        def fetch(url,**kwargs):return bounded_http_json.fetch(self.base+'/',**kwargs)
        with patch.object(fd_monitor,'fetch',fetch):monitor.sample()
        self.assertEqual((monitor.pid,monitor.before,monitor.alsa),(123,24,2));monitor.log.close()
    def test_FD_monitor_trickle_fails_closed_and_thread_stops(self):
        monitor=fd_monitor.Monitor(self.tmp.name)
        def fetch(url,**kwargs):return bounded_http_json.fetch(self.base+'/body',timeout=.15,include_headers=True)
        with patch.object(fd_monitor,'fetch',fetch):
            monitor.thread=threading.Thread(target=monitor.run);monitor.thread.start();monitor.thread.join(1)
            self.assertFalse(monitor.thread.is_alive());self.assertTrue(monitor.failed.is_set())
            with self.assertRaises(AssertionError):monitor.finish(require_final=False)
        self.assertTrue(monitor.log.closed)
