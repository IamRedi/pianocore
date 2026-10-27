"""Bound the entire owned HTTP client lifetime, including trickling headers/body."""
import json,os,signal,subprocess,sys,time,urllib.error
from pathlib import Path
CHILD=r'''
import json,sys,urllib.request,urllib.error,ctypes,os,signal
parent=int(sys.argv[1])
assert ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)==0,'PDEATHSIG unavailable'
if os.getppid()!=parent:os._exit(125)
spec=json.load(sys.stdin)
try:
 req=urllib.request.Request(spec['url'],data=None if spec['body'] is None else json.dumps(spec['body']).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=spec['timeout']) as response:
  data=response.read(2*1024*1024+1)
  if len(data)>2*1024*1024:raise ValueError('JSON response exceeds2MiB')
  result=json.loads(data)
 print(json.dumps({'ok':True,'data':result,'headers':dict(response.headers),'status':response.status}))
except urllib.error.HTTPError as exc:print(json.dumps({'ok':False,'kind':'HTTPError','status':exc.code}))
except BaseException as exc:
 reason=getattr(exc,'reason',exc)
 print(json.dumps({'ok':False,'kind':type(reason).__name__,'message':str(reason)[:200]}))
'''
def fetch(url,body=None,timeout=10,receipt_path=None,include_headers=False):
    assert 0<timeout<=15,'bounded request timeout'
    start=time.monotonic();child=subprocess.Popen([sys.executable,'-c',CHILD,str(os.getpid())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    forced=False
    try:
        try:out,err=child.communicate(json.dumps({'url':url,'body':body,'timeout':timeout}),timeout=max(.001,timeout-(time.monotonic()-start)))
        except subprocess.TimeoutExpired as exc:raise TimeoutError('owned HTTP absolute deadline') from exc
        assert time.monotonic()-start<timeout,'owned HTTP result after absolute deadline'
        assert child.returncode==0,('owned HTTP child failed',child.returncode,err[:200])
        result=json.loads(out)
        if result['ok']:return result if include_headers else result['data']
        kind=result['kind']
        if kind=='HTTPError':raise urllib.error.HTTPError(url,result['status'],'release request failed',{},None)
        if kind in ['ConnectionRefusedError','ConnectionResetError','TimeoutError']:
            reason={'ConnectionRefusedError':ConnectionRefusedError,'ConnectionResetError':ConnectionResetError,'TimeoutError':TimeoutError}[kind](result.get('message',''))
            raise urllib.error.URLError(reason)
        raise ValueError('owned HTTP '+kind+': '+result.get('message',''))
    finally:
        if child.poll() is None:
            forced=True
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        child.wait(timeout=2)
        for stream in [child.stdin,child.stdout,child.stderr]:
            if stream is not None:stream.close()
        if receipt_path:
            with Path(receipt_path).open('a') as f:
                f.write(json.dumps({'pid':child.pid,'exit_code':child.returncode,'forced_owned_client_cleanup':forced,'reaped':True,'elapsed_seconds':time.monotonic()-start,'budget_seconds':timeout})+'\n');f.flush();os.fsync(f.fileno())
