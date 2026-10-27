"""R6 single-attempt migration decision; never turns the original journal FAIL into PASS."""
from pathlib import Path
import hashlib
import json
import re
import os

BAD = re.compile(r'\b(?:ERROR|CRITICAL|Traceback)\b|(?:RuntimeError|Exception|Error):|\b500\b|stop-sigterm.*timed out|status=9/KILL|Failed with result|Killing process|State .*stop.*timed out', re.I)
HEADER = re.compile(r'^\d{4}-\d\d-\d\dT[^ ]+ ERROR piano_core\.api unhandled API failure on GET /api/v1/live-notes/stream$')

def digest(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def message(row):
    value=row['MESSAGE']; assert isinstance(value,str),'nontext journal message'
    return value

def normalized(value):
    return re.sub(r'^\d{4}-\d\d-\d\dT[^ ]+ ERROR ', 'ERROR ',value)

def validate_events(events):
    assert events,'empty journal'
    cursors=[row['__CURSOR'] for row in events]
    assert len(cursors)==len(set(cursors)),'duplicate journal cursor'
    stamps=[int(row['__MONOTONIC_TIMESTAMP']) for row in events]
    assert stamps==sorted(stamps),'journal time order'
    for row in events:
        message(row); assert row.get('_BOOT_ID') and row.get('_PID'),'missing journal identity'

def matches(row,identity):
    return (row.get('_PID')==str(identity['core']['MainPID']) and
            row.get('_SYSTEMD_INVOCATION_ID')==identity['core']['InvocationID'] and
            row.get('_BOOT_ID')==identity['boot_id'] and
            row.get('_SYSTEMD_UNIT')=='piano-core.service')

def exclusive_json(path,value):
    path=Path(path)
    with path.open('x') as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)

class SingleAttempt:
    """Durable exclusive receipt precedes helper; failure also consumes the permit."""
    def __init__(self,root):self.root=Path(root)
    def claim(self,identity):
        path=self.root/'migration-attempt-used.json'
        exclusive_json(path,{'task':'U8-P7-R6','identity':identity,'permit':'CONSUMED_BEFORE_INITIAL_HELPER; NEVER_REUSABLE'})
        return path

def classify_initial(events,old,new,template,attempt_start_us,baseline_verified):
    validate_events(events)
    assert baseline_verified,'baseline source not verified'
    assert old['core']['MainPID']!=new['core']['MainPID'],'PID did not change'
    assert old['core']['InvocationID']!=new['core']['InvocationID'],'invocation did not change'
    assert old['boot_id']==new['boot_id'],'boot changed'
    assert old['legacy']==new['legacy'],'legacy ownership drift'
    assert new['core']['ActiveState']=='active' and new['core']['NRestarts']=='0','candidate service not active cleanly'
    def find(identity,suffix):
        rows=[row for row in events if matches(row,identity) and message(row).endswith(suffix)]
        assert len(rows)==1,('missing/duplicate lifecycle marker',suffix)
        return rows[0]
    stop=find(old,'INFO piano_core.app shutdown request reason=signal')
    api_stop=find(old,'INFO piano_core.api api server stopped')
    stopped=find(old,'INFO piano_core.app stopped')
    startup=[row for row in events if matches(row,new) and ' INFO piano_core.app startup product=Piano Core runtime=piano-core ' in message(row)]
    assert len(startup)==1,'candidate startup missing/duplicate'
    ready=find(new,'INFO piano_core.app ready')
    a,b,c,d,e=[int(row['__MONOTONIC_TIMESTAMP']) for row in [stop,api_stop,stopped,startup[0],ready]]
    assert attempt_start_us<=a<=b<=c<d<=e,'incorrect lifecycle order'
    assert c-a<=10_000_000,'old shutdown exceeded bounded10seconds'
    assert e-d<=15_000_000,'candidate startup exceeded15seconds'
    accepted={};blocks=[];i=0
    trace=template['trace_messages'];assert trace[0]=='ERROR piano_core.api unhandled API failure on GET /api/v1/live-notes/stream' and trace[-1]=='RuntimeError: live-note publication is closed'
    while i<len(events):
        row=events[i]
        if HEADER.fullmatch(message(row)):
            block=events[i:i+len(trace)]
            assert len(block)==len(trace) and [normalized(message(x)) for x in block]==trace,'unknown traceback payload'
            assert all(matches(x,old) for x in block),'trace identity not exact oldPID/invocation/unit/boot'
            assert all(b<=int(x['__MONOTONIC_TIMESTAMP'])<=c for x in block),'trace outside old API-stopped to app-stopped phase'
            assert int(block[-1]['__MONOTONIC_TIMESTAMP'])-int(block[0]['__MONOTONIC_TIMESTAMP'])<=2_000_000,'trace collection exceeded2seconds'
            assert len(blocks)<2,'more than2 inherited errors'
            blocks.append([x['__CURSOR'] for x in block])
            accepted.update({x['__CURSOR']:digest(x) for x in block});i+=len(trace);continue
        assert not BAD.search(message(row)),('unexpected journal failure',row['__CURSOR'],message(row))
        assert int(row.get('PRIORITY','6'))>3,('unexpected journal severity',row['__CURSOR'])
        i+=1
    return {'migration_decision':'PASS_WITH_AUTHORIZED_ONE_TIME_LEGACY_EXCEPTION' if blocks else 'PASS_NO_EXCEPTION_REQUIRED',
            'original_frozen_whole_window_gate':'FAIL' if blocks else 'PASS',
            'candidate_runtime_gate':'PASS','exception_used':bool(blocks),'known_legacy_errors':len(blocks),
            'old_identity':old,'initial_candidate_identity':new,'accepted_historical_records':accepted,
            'exception_expired':True,'future_exception_authority':False,'shutdown_start_us':a,'shutdown_end_us':c,
            'candidate_start_us':d,'candidate_ready_us':e}

def validate_whole(events,decision):
    """Only identical already-adjudicated historical records are recognized, never new exceptions."""
    validate_events(events);assert decision['exception_expired'] and not decision['future_exception_authority']
    historical=decision['accepted_historical_records'];seen={}
    for row in events:
        cursor=row['__CURSOR']
        if cursor in historical:
            assert digest(row)==historical[cursor],'historical evidence changed';seen[cursor]=historical[cursor];continue
        assert not BAD.search(message(row)),('new/unknown journal error',cursor,message(row))
        assert int(row.get('PRIORITY','6'))>3,('unexpected severity',cursor)
    assert seen==historical,'accepted original evidence missing'
    return {'original_frozen_whole_window_gate':decision['original_frozen_whole_window_gate'],
            'candidate_runtime_gate':'PASS','new_exceptions_accepted':0,
            'historical_legacy_record_hashes_exact':len(seen),'raw_record_count':len(events)}

def rollback_target(initial_anchor,lifecycle_anchor=None):
    assert initial_anchor and re.fullmatch(r'/var/lib/piano-core-deploy/backups/piano-core-rollback-[A-Za-z0-9.\-]+',initial_anchor)
    assert lifecycle_anchor is None or lifecycle_anchor!=initial_anchor
    return initial_anchor

def migrate_once(root,old,template,baseline_verified,deploy,read_deploy_log,observe_candidate,capture_events,set_rollback):
    """The exact initial protocol; all device operations are explicit injected adapters."""
    root=Path(root);assert baseline_verified,'unverified baseline'
    SingleAttempt(root).claim(old)
    try:
        try:
            deploy()
        finally:
            anchors=re.findall(r'/var/lib/piano-core-deploy/backups/piano-core-rollback-[A-Za-z0-9.\-]+',read_deploy_log())
            if anchors:set_rollback(rollback_target(anchors[0]))
        assert anchors,'helper did not record B0'
        new=observe_candidate()  # Error responses and failed READY must propagate.
    except BaseException as primary:
        try:capture_events()
        except BaseException as capture_error:
            primary.add_note('Initial raw journal capture failed: '+repr(capture_error))
            try:exclusive_json(root/'initial-failure-journal-capture-error.json',{'error':repr(capture_error)})
            except BaseException as persistence_error:primary.add_note('Capture error persistence failed: '+repr(persistence_error))
        raise
    events=capture_events()
    decision=classify_initial(events,old,new,template,old['monotonic_us'],baseline_verified)
    exclusive_json(root/'migration-decision.json',decision)
    return decision

def wait_candidate_ready(get_health,get_identity,old,monotonic,sleep):
    import urllib.error
    end=monotonic()+15
    while monotonic()<end:
        try:health=get_health(max(.001,end-monotonic()))
        except urllib.error.HTTPError:raise
        except (urllib.error.URLError,ConnectionError,TimeoutError) as exc:
            reason=getattr(exc,'reason',exc)
            assert isinstance(reason,(ConnectionRefusedError,ConnectionResetError,TimeoutError)),('unexpected readiness transport error',repr(exc))
            sleep(.05);continue
        assert monotonic()<end,'READY health response arrived after deadline'
        assert health.get('status')=='ok',('candidate degraded',health)
        if health['application']['state']=='READY':
            current=get_identity(max(.001,end-monotonic()))
            assert monotonic()<end,'identity arrived after READY deadline'
            assert current['core']['MainPID']!=old['core']['MainPID'],'old runtime masquerading as READY'
            return current
        assert health['application']['state']=='STARTING','unexpected candidate lifecycle'
        sleep(.05)
    raise AssertionError('no candidate READY within15seconds')

def inactive_startup_recovery_allowed(old,current,evidence,events,age_seconds,owned_work_started):
    """A fail-closed recovery decision only; current physical input remains NOT_VERIFIED."""
    validate_events(events)
    assert 0<=age_seconds<=60 and not owned_work_started,'stale/owned-work recovery'
    assert current['boot_id']==old['boot_id'] and current['legacy']==old['legacy'],'owner/boot drift'
    core=current['core'];assert core['MainPID']=='0' and core['ActiveState'] in ['inactive','failed'] and core['SubState'] in ['dead','failed'],'candidate may be live'
    assert evidence['cgroup_inspected'] and evidence['cgroup_pids']==[] and evidence['job'] in ['', '0'],'pending/live unit work'
    assert evidence['alsa_output_clients']==[],'MIDI owner remains'
    assert evidence['process_scan_complete'] and not evidence['candidate_process_alive'],'candidate process uncertain'
    assert evidence['complete_since_initial_capture'] and evidence['no_musical_commands'],'incomplete startup evidence'
    for row in events:
        if matches(row,old):continue
        assert 'piano_core.api api listening' not in message(row) and not message(row).endswith('INFO piano_core.app ready'),'candidate exposed API'
    return {'recovery_only':'PASS_INACTIVE_NEVER_LISTENED_INITIAL_STARTUP','physical_input_current':'NOT_VERIFIED','owner_acceptance':'NOT_RUN','rollback_target':'B0_ONLY'}
