"""Read-only actual process sampling; any missing sample/ALSA/MIDI drift fails closed."""
import datetime,json,threading,time,urllib.request
from pathlib import Path
from quiescence import observe_quiescence
from bounded_http_json import fetch
class Monitor:
 def __init__(self,root):
  self.root=Path(root);self.stop=threading.Event();self.failed=threading.Event();self.phase='before-load';self.samples=[];self.failure=None;self.condition=threading.Condition();self.final_quiescence=None
  self.log=(self.root/'live-fd-samples.jsonl').open('w');self.pid=None;self.before=None;self.alsa=None
 def sample(self):
  request_started=time.monotonic()
  result=fetch('http://192.168.1.50:8788/api/v1/health',timeout=5,include_headers=True)
  headers={key.lower():value for key,value in result['headers'].items()}
  assert result['status']==200 and headers['x-piano-fd-sample']=='sampled','actual sample unavailable'
  pid=int(headers['x-piano-process-pid']);fds=int(headers['x-piano-process-fds']);alsa=int(headers['x-piano-alsa-fds']);data=result['data']
  item={'request_started_monotonic':request_started,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'monotonic':time.monotonic(),'phase':self.phase,'pid':pid,'fds':fds,'alsa_fds':alsa,'midi':data['midi_input']['connection'],'health':data['status'],'application':data['application']['state']}
  if self.phase=='second499-during-real-flow-persistence':
   if self.stop.is_set():return
   session=fetch('http://192.168.1.50:8788/api/v1/learning',timeout=5)
   item.update(learning_state=session['state'],progress_pending_count=session.get('progress_pending_count'),session_id=session.get('session_id'),flow_position=(session.get('flow') or {}).get('position_seconds'))
   if session['state']=='COMPLETED' and session.get('progress_pending_count')==0:
    if self.stop.is_set():return
    progress=fetch('http://192.168.1.50:8788/api/v1/packages/0c690861-8520-5a18-8795-d44b9c526e8c/progress',timeout=5)
    item['full_lesson_completions']=progress['full_lesson_completions']
   item['persistence_observed_monotonic']=time.monotonic()
  with self.condition:
   self.samples.append(item);self.condition.notify_all()
  self.log.write(json.dumps(item)+'\n');self.log.flush()
  if self.pid is None:
   self.pid=pid;self.before=fds;self.alsa=alsa;assert alsa>=2,'input/output ALSA descriptor floor'
  assert pid==self.pid and alsa==self.alsa and item['midi']=='CONNECTED' and item['health']=='ok' and item['application']=='READY',item
  assert fds<768,'predeclared actual FD headroom abort'
 def run(self):
  try:
   self.sample()
   while not self.stop.wait(.1):self.sample()
  except BaseException as exc:
   self.failure=repr(exc);self.failed.set();(self.root/'live-resource-abort.txt').write_text(self.failure+'\n')
 def start(self):
  self.thread=threading.Thread(target=self.run,name='read-only-resource-monitor');self.thread.start()
  deadline=time.monotonic()+5
  while self.pid is None and not self.failed.is_set() and time.monotonic()<deadline:time.sleep(.01)
  self.check();assert self.pid is not None
 def check(self):assert not self.failed.is_set(),self.failure
 def quiescent(self,barrier,*,final=False):
  self.check();self.phase="final-quiescent" if final else "owned-http-closed-recovery"
  proof=observe_quiescence(self.samples,self.condition,after=barrier,limit=self.before+8,pid=self.pid,alsa=self.alsa,timeout=5,required=3)
  self.check()
  if final:self.final_quiescence=proof
  (self.root/("final-quiescence.json" if final else "first-load-quiescence.json")).write_text(json.dumps(proof,indent=2)+"\n")
  return proof
 def finish(self,require_final=True):
  self.stop.set();self.thread.join(timeout=6);assert not self.thread.is_alive();self.log.close()
  summary={'samples':len(self.samples),'pid':self.pid,'before':self.before,'peak':max((s['fds'] for s in self.samples),default=None),'after':self.samples[-1]['fds'] if self.samples else None,'alsa_fds':self.alsa,'failure':self.failure,'final_quiescence':self.final_quiescence,'phases':{phase:{'samples':len(rows),'min':min(x['fds'] for x in rows),'max':max(x['fds'] for x in rows)} for phase in {s['phase'] for s in self.samples} if (rows:=[s for s in self.samples if s['phase']==phase])}}
  (self.root/'live-fd-summary.json').write_text(json.dumps(summary,indent=2)+'\n');self.check()
  assert summary['peak']<=self.before+64,summary
  if require_final:
   assert self.final_quiescence and self.final_quiescence['status']=='PASS','final measurement requires verified owned cleanup barrier'
   assert summary['after']<=self.before+8,summary
  return summary
