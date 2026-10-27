"""Linux-owned release clients cannot outlive a killed harness parent."""
import ctypes
import http.server
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from release_harness import bounded_http_json,bounded_sse

class ParentDeathTests(unittest.TestCase):
 def run_owned_parent(self,kind):
  accepted=threading.Event();finished=threading.Event()
  class Handler(http.server.BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    self.send_response(200);self.send_header('Content-Type','text/event-stream' if kind=='sse' else 'application/json');self.end_headers()
    try:
     self.wfile.write(b'data: {"runtime_epoch":"owned-test"}\n\n' if kind=='sse' else b'{"trickle":"');self.wfile.flush();accepted.set()
     while not finished.wait(.02):self.wfile.write(b' ');self.wfile.flush()
    except (BrokenPipeError,ConnectionResetError):pass
  server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
  thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02});thread.start()
  libc=ctypes.CDLL(None,use_errno=True);previous=ctypes.c_int();self.assertEqual(libc.prctl(37,ctypes.byref(previous),0,0,0),0);self.assertEqual(libc.prctl(36,1,0,0,0),0)
  parent=None;worker=None
  try:
   with tempfile.TemporaryDirectory() as directory:
    receipt=Path(directory)/'child.json';ready=Path(directory)/'ready'
    code='''import sys,subprocess,json,time
from pathlib import Path
from release_harness import bounded_http_json,bounded_sse
original=subprocess.Popen
def tracked(*args,**kwargs):
 child=original(*args,**kwargs);Path(sys.argv[3]).write_text(json.dumps({'pid':child.pid}));return child
subprocess.Popen=tracked
if sys.argv[1]=='http':bounded_http_json.fetch(sys.argv[2],timeout=1)
else:
 peer=bounded_sse.Peer(sys.argv[2]);Path(sys.argv[4]).write_text('established');time.sleep(30)
'''
    parent=subprocess.Popen([sys.executable,'-c',code,kind,f'http://127.0.0.1:{server.server_port}/stream',str(receipt),str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    self.assertTrue(accepted.wait(3),'owned client never reached socket barrier')
    deadline=time.monotonic()+3
    while not receipt.exists() and time.monotonic()<deadline:time.sleep(.005)
    worker=json.loads(receipt.read_text())['pid']
    if kind=='sse':
     while not ready.exists() and time.monotonic()<deadline:time.sleep(.005)
     self.assertTrue(ready.exists(),'SSE handshake was not established')
    os.kill(parent.pid,signal.SIGKILL);parent.communicate(timeout=2)
    deadline=time.monotonic()+2;reaped=False
    while time.monotonic()<deadline:
     got,_=os.waitpid(worker,os.WNOHANG)
     if got==worker:reaped=True;break
     time.sleep(.005)
    self.assertTrue(reaped,'owned worker survived killed parent')
    worker=None
  finally:
   if parent:
    if parent.poll() is None:parent.kill()
    parent.communicate(timeout=2)
   if worker:
    try:os.kill(worker,signal.SIGKILL)
    except ProcessLookupError:pass
    try:os.waitpid(worker,0)
    except ChildProcessError:pass
   finished.set();server.shutdown();server.server_close();thread.join(timeout=2)
   libc.prctl(36,previous.value,0,0,0)
 def test_trickling_http_worker_dies_with_its_parent(self):self.run_owned_parent('http')
 def test_established_sse_worker_dies_with_its_parent(self):self.run_owned_parent('sse')
 def test_http_bootstrap_parent_race_fails_before_io(self):
  p=subprocess.run([sys.executable,'-c',bounded_http_json.CHILD,str(os.getpid()+1000000)],input='{}',capture_output=True,text=True,timeout=2)
  self.assertEqual(p.returncode,125)
 def test_sse_bootstrap_parent_race_fails_before_io(self):
  p=subprocess.run([sys.executable,'-c',bounded_sse.CHILD,'http://127.0.0.1:1/never',str(os.getpid()+1000000)],capture_output=True,text=True,timeout=2)
  self.assertEqual(p.returncode,125)
if __name__=='__main__':unittest.main(verbosity=2)
