"""Disposable real HTTP controls for absolute client deadline and process reaping."""
import http.server,json,os,tempfile,threading,time,unittest,urllib.error
from pathlib import Path
from release_harness.bounded_http_json import fetch
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        try:
            if self.path=='/header-trickle':
                for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}':self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.02)
                return
            status=int(self.path[1:]) if self.path in ['/500','/503'] else 200
            self.send_response(status);self.end_headers()
            if self.path=='/body-trickle':
                for byte in b'{"application":{"state":"READY"}}':self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.02)
            elif self.path=='/invalid':self.wfile.write(b'not JSON')
            elif self.path=='/large':self.wfile.write(b' '* (2*1024*1024+1))
            else:self.wfile.write(b'{"ok":true}')
        except (BrokenPipeError,ConnectionResetError):pass
class HttpBoundTests(unittest.TestCase):
    def setUp(self):
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01));self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.tmp=tempfile.TemporaryDirectory();self.receipts=Path(self.tmp.name)/'receipts.jsonl'
    def tearDown(self):self.server.shutdown();self.thread.join(2);self.server.server_close();self.tmp.cleanup()
    def call(self,path,timeout=2):return fetch(self.base+path,timeout=timeout,receipt_path=self.receipts)
    def assert_reaped(self):
        for row in map(json.loads,self.receipts.read_text().splitlines()):
            self.assertTrue(row['reaped'])
            with self.assertRaises(ProcessLookupError):os.kill(row['pid'],0)
    def test_valid_JSON_normal_client_exit(self):self.assertTrue(self.call('/')['ok']);self.assert_reaped()
    def test_error_status_never_retried(self):
        for status in [500,503]:
            with self.assertRaises(urllib.error.HTTPError) as caught:self.call('/'+str(status))
            self.assertEqual(caught.exception.code,status)
        self.assertEqual(len(self.receipts.read_text().splitlines()),2);self.assert_reaped()
    def test_invalid_JSON_rejected(self):
        with self.assertRaises(ValueError):self.call('/invalid')
        self.assert_reaped()
    def test_oversize_body_rejected(self):
        with self.assertRaises(ValueError):self.call('/large')
        self.assert_reaped()
    def test_body_trickle_absolute_deadline_and_reaping(self):
        started=time.monotonic()
        with self.assertRaises(TimeoutError):self.call('/body-trickle',.15)
        self.assertLess(time.monotonic()-started,1);self.assert_reaped()
    def test_header_trickle_absolute_deadline_and_reaping(self):
        started=time.monotonic()
        with self.assertRaises(TimeoutError):self.call('/header-trickle',.15)
        self.assertLess(time.monotonic()-started,1);self.assert_reaped()
