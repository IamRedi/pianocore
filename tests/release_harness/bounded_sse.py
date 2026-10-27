"""Real owned SSE peers with absolute handshake and EOF deadlines; no reader threads."""
import json, os, selectors, signal, subprocess, sys, time

CHILD = r'''
import http.client,json,sys,time,urllib.parse,ctypes,os,signal
parent=int(sys.argv[2])
assert ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)==0,'PDEATHSIG unavailable'
if os.getppid()!=parent:os._exit(125)
url=urllib.parse.urlsplit(sys.argv[1])
conn=http.client.HTTPConnection(url.hostname,url.port,timeout=20)
try:
 conn.request('GET',url.path)
 response=conn.getresponse()
 assert response.status==200,response.status
 frame=b''
 while b'\n\n' not in frame:
  chunk=response.read1(min(4096,65537-len(frame)))
  assert chunk,'SSE closed before initial snapshot'
  frame+=chunk
  assert len(frame)<=65536,'SSE initial snapshot byte limit'
 first_frame,prefix=frame.split(b'\n\n',1)
 data=json.loads(next(line[6:] for line in first_frame.splitlines() if line.startswith(b'data: ')))
 print(json.dumps({'headers_status':response.status,'initial_epoch':data['runtime_epoch'],'snapshot_received':True,'established_monotonic':time.monotonic()}),flush=True)
 tail=prefix+response.read(2*1024*1024+1-len(prefix))
 assert len(tail)<=2*1024*1024,'SSE tail byte limit'
 assert b'HTTP/' not in tail,'second HTTP status in established SSE'
 print(json.dumps({'eof':True,'tail_bytes':len(tail),'ended_monotonic':time.monotonic()}),flush=True)
finally:conn.close()
'''

class Peer:
    def __init__(self,url,timeout=10):
        self.child=subprocess.Popen([sys.executable,'-c',CHILD,url,str(os.getpid())],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,bufsize=0)
        self.closed=False
        try:
            deadline=time.monotonic()+timeout;buf=b''
            with selectors.DefaultSelector() as selector:
                selector.register(self.child.stdout,selectors.EVENT_READ)
                while b'\n' not in buf:
                    remaining=deadline-time.monotonic()
                    if remaining<=0 or not selector.select(remaining):raise TimeoutError('owned SSE absolute handshake deadline')
                    chunk=os.read(self.child.stdout.fileno(),1)
                    assert chunk,'owned SSE exited before initial snapshot'
                    buf+=chunk
                    assert len(buf)<=65536,'owned SSE receipt size limit'
            assert time.monotonic()<deadline,'owned SSE snapshot after absolute deadline'
            self.snapshot=json.loads(buf)
        except BaseException:
            self.close();raise

    def eof(self,timeout=2):
        try:
            out,err=self.child.communicate(timeout=timeout)
            assert self.child.returncode==0,('owned SSE failed',self.child.returncode,err[:1000])
            row=json.loads(out)
            assert row['eof'],row
            return row
        finally:self.close()

    def close(self):
        if self.closed:return
        if self.child.poll() is None:
            try:os.killpg(self.child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        self.child.wait(timeout=2)
        for stream in [self.child.stdout,self.child.stderr]:stream.close()
        self.closed=True
