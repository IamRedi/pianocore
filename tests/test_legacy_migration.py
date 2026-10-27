"""Fail-closed R6 controls using retained real R5 journal records, no appliance action."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from release_harness.legacy_migration import classify_initial,validate_whole,SingleAttempt,rollback_target,migrate_once,wait_candidate_ready,inactive_startup_recovery_allowed

FIXTURE=Path(__file__).parent/'release_harness/fixtures'
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.events=[json.loads(line) for line in (FIXTURE/'r5-initial-transition-structured.jsonl').read_text().splitlines()]
        self.template=json.loads((FIXTURE/'legacy-trace-template.json').read_text())
        def identity(pid):
            row=next(x for x in self.events if x['_PID']==str(pid))
            return {'core':{'MainPID':str(pid),'InvocationID':row['_SYSTEMD_INVOCATION_ID'],'NRestarts':'0','ActiveState':'active'},'legacy':{'MainPID':'1474','InvocationID':'unchanged'},'boot_id':row['_BOOT_ID'],'monotonic_us':int(self.events[0]['__MONOTONIC_TIMESTAMP'])-1}
        self.old=identity(461627);self.new=identity(568697)
        self.start=int(self.events[0]['__MONOTONIC_TIMESTAMP'])-1
    def classify(self,events=None,old=None,new=None,verified=True):
        return classify_initial(events or self.events,old or self.old,new or self.new,self.template,self.start,verified)
    def error_index(self):return next(i for i,x in enumerate(self.events) if ' ERROR ' in x['MESSAGE'])
    def test_real_known_old_defect_exact_originalFAIL_preserved(self):
        before=json.dumps(self.events,sort_keys=True);decision=self.classify()
        self.assertEqual(decision['known_legacy_errors'],2);self.assertEqual(decision['original_frozen_whole_window_gate'],'FAIL')
        self.assertTrue(decision['exception_expired']);self.assertFalse(decision['future_exception_authority'])
        self.assertEqual(before,json.dumps(self.events,sort_keys=True));self.assertEqual(validate_whole(self.events,decision)['candidate_runtime_gate'],'PASS')
    def test_clean_transition_requires_no_exception(self):
        decision=self.classify();bad=set(decision['accepted_historical_records']);events=[x for x in self.events if x['__CURSOR'] not in bad]
        result=self.classify(events);self.assertFalse(result['exception_used']);self.assertEqual(result['original_frozen_whole_window_gate'],'PASS')
    def test_reject_wrong_pid(self):
        events=copy.deepcopy(self.events);events[self.error_index()]['_PID']='568697'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_reject_wrong_invocation(self):
        events=copy.deepcopy(self.events);events[self.error_index()]['_SYSTEMD_INVOCATION_ID']='unknown'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_reject_wrong_boot(self):
        events=copy.deepcopy(self.events);events[self.error_index()]['_BOOT_ID']='unknown'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_reject_wrong_unit(self):
        events=copy.deepcopy(self.events);events[self.error_index()]['_SYSTEMD_UNIT']='other.service'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_reject_unknown_exception(self):
        events=copy.deepcopy(self.events);row=next(x for x in events if x['MESSAGE']=='RuntimeError: live-note publication is closed');row['MESSAGE']='RuntimeError: unexpected new defect'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_reject_candidate_error_same_signature(self):
        events=copy.deepcopy(self.events);events[-1]['MESSAGE']='2026-10-12T09:56:03.097Z ERROR piano_core.api unhandled API failure on GET /api/v1/live-notes/stream'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_reject_unknown500(self):
        events=copy.deepcopy(self.events);events[-1]['MESSAGE']='unexpected HTTP500 status 500'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_reject_forcekill_timeout(self):
        for value in ['status=9/KILL','stop-sigterm timed out','Failed with result timeout','Killing process 568697']:
            with self.subTest(value=value):
                events=copy.deepcopy(self.events);events[-1]['MESSAGE']=value
                with self.assertRaises(AssertionError):self.classify(events)
    def test_failed_startup_rejected(self):
        events=[x for x in self.events if not x['MESSAGE'].endswith('INFO piano_core.app ready')]
        with self.assertRaises(AssertionError):self.classify(events)
    def test_duplicate_owner_rejected(self):
        new=copy.deepcopy(self.new);new['core']=copy.deepcopy(self.old['core'])
        with self.assertRaises(AssertionError):self.classify(new=new)
    def test_legacy_owner_drift_rejected(self):
        new=copy.deepcopy(self.new);new['legacy']['MainPID']='1500'
        with self.assertRaises(AssertionError):self.classify(new=new)
    def test_source_unverified_rejected(self):
        with self.assertRaises(AssertionError):self.classify(verified=False)
    def test_missing_lifecycle_rejected(self):
        events=[x for x in self.events if not x['MESSAGE'].endswith('INFO piano_core.api api server stopped')]
        with self.assertRaises(AssertionError):self.classify(events)
    def test_error_outside_shutdown_rejected(self):
        events=copy.deepcopy(self.events);stop=next(x for x in events if ' shutdown request ' in x['MESSAGE']);stop['MESSAGE']='unrelated INFO'
        with self.assertRaises(AssertionError):self.classify(events)
    def test_duplicate_journal_cursor_rejected(self):
        events=copy.deepcopy(self.events);events[-1]['__CURSOR']=events[0]['__CURSOR']
        with self.assertRaises(AssertionError):self.classify(events)
    def test_one_time_expiry_even_failed_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            SingleAttempt(tmp).claim(self.old)
            with self.assertRaises(FileExistsError):SingleAttempt(tmp).claim(self.old)
    def test_B0_never_B1_rollback_target(self):
        a='/var/lib/piano-core-deploy/backups/piano-core-rollback-B0';b='/var/lib/piano-core-deploy/backups/piano-core-rollback-B1'
        self.assertEqual(rollback_target(a,b),a)
        with self.assertRaises(AssertionError):rollback_target(None,b)
        with self.assertRaises(AssertionError):rollback_target(a,a)
    def test_historical_bytes_cannot_be_changed(self):
        decision=self.classify();events=copy.deepcopy(self.events);events[self.error_index()]['MESSAGE']+=' changed'
        with self.assertRaises(AssertionError):validate_whole(events,decision)
    def test_missing_original_error_records_rejected(self):
        decision=self.classify()
        with self.assertRaises(AssertionError):validate_whole(self.events[1:],decision) if self.events[0]['__CURSOR'] in decision['accepted_historical_records'] else validate_whole([x for i,x in enumerate(self.events) if i!=self.error_index()],decision)
    def test_future_identical_old_error_not_tolerated(self):
        decision=self.classify();events=copy.deepcopy(self.events);row=copy.deepcopy(events[self.error_index()]);row['__CURSOR']='new-cursor';row['__MONOTONIC_TIMESTAMP']=str(int(events[-1]['__MONOTONIC_TIMESTAMP'])+1);events.append(row)
        with self.assertRaises(AssertionError):validate_whole(events,decision)
    def exercise_protocol(self,failure=None):
        calls=[];saved=[];data={'progress':{'attempts':8},'legacy_owner':1474,'unowned_clients':['tablet']}
        original=copy.deepcopy(data)
        def deploy():
            calls.append('one_helper_deploy')
            if failure=='helper':raise RuntimeError('install failed after backup')
        def ready():
            calls.append('candidate_ready')
            if failure=='startup':raise RuntimeError('candidate not READY')
            return self.new
        def capture():
            calls.append('raw_journal_capture')
            events=copy.deepcopy(self.events)
            if failure=='unknown':events[-1]['MESSAGE']='ValueError: candidate defect'
            return events
        with tempfile.TemporaryDirectory() as tmp:
            try:
                decision=migrate_once(tmp,self.old,self.template,True,deploy,lambda:'/var/lib/piano-core-deploy/backups/piano-core-rollback-B0',ready,capture,saved.append)
                self.assertIsNone(failure);self.assertTrue(decision['exception_expired'])
            except RuntimeError:
                self.assertIn(failure,['helper','startup']);calls.append('rollback:'+saved[0])
            except AssertionError:
                self.assertEqual(failure,'unknown');calls.append('rollback:'+saved[0])
            self.assertEqual(saved,['/var/lib/piano-core-deploy/backups/piano-core-rollback-B0'])
            with self.assertRaises(FileExistsError):
                migrate_once(tmp,self.old,self.template,True,deploy,lambda:'',ready,capture,saved.append)
        self.assertEqual(data,original);self.assertEqual(calls.count('one_helper_deploy'),1)
        return calls
    def test_protocol_success_exact_order_no_unowned_operation(self):
        self.assertEqual(self.exercise_protocol(),['one_helper_deploy','candidate_ready','raw_journal_capture'])
    def test_protocol_failed_helper_retains_B0_no_candidate_operation(self):
        calls=self.exercise_protocol('helper');self.assertEqual(len(calls),3);self.assertIn('raw_journal_capture',calls)
    def test_protocol_failed_candidate_startup_selects_B0(self):
        calls=self.exercise_protocol('startup');self.assertEqual(len(calls),4);self.assertIn('raw_journal_capture',calls)
    def test_protocol_unknown_error_rolls_back_no_reuse(self):
        self.assertEqual(len(self.exercise_protocol('unknown')),4)

    def test_readiness_HTTP500_and503_fail_without_retry(self):
        import urllib.error
        for status in [500,503]:
            calls=[]
            def get(timeout):calls.append(status);raise urllib.error.HTTPError('http://fixture',status,'error',{},None)
            with self.assertRaises(urllib.error.HTTPError):wait_candidate_ready(get,lambda timeout:self.new,self.old,lambda:0,lambda delay:self.fail('unexpected retry'))
            self.assertEqual(calls,[status])
    def test_readiness_invalidJSON_propagates(self):
        def get(timeout):raise ValueError('invalid JSON')
        with self.assertRaises(ValueError):wait_candidate_ready(get,lambda timeout:self.new,self.old,lambda:0,lambda delay:self.fail('retry'))
    def test_readiness_refusal_only_bounded_and_then_ready(self):
        import urllib.error
        clock=[0];calls=[]
        def get(timeout):
            calls.append(1)
            if len(calls)==1:raise urllib.error.URLError(ConnectionRefusedError())
            return {'status':'ok','application':{'state':'READY'}}
        self.assertEqual(wait_candidate_ready(get,lambda timeout:self.new,self.old,lambda:clock[0],lambda delay:clock.__setitem__(0,clock[0]+delay)),self.new)
        self.assertEqual(len(calls),2)
    def test_readiness_timeout_bounded(self):
        import urllib.error
        clock=[0]
        def get(timeout):raise urllib.error.URLError(ConnectionRefusedError())
        with self.assertRaises(AssertionError):wait_candidate_ready(get,lambda timeout:self.new,self.old,lambda:clock[0],lambda delay:clock.__setitem__(0,clock[0]+delay))
        self.assertLess(clock[0],15.1)
    def recovery_fixture(self):
        current=copy.deepcopy(self.new);current['core'].update(MainPID='0',ActiveState='failed',SubState='failed')
        evidence={'cgroup_inspected':True,'cgroup_pids':[],'job':'','alsa_output_clients':[],'process_scan_complete':True,'candidate_process_alive':False,'complete_since_initial_capture':True,'no_musical_commands':True}
        events=[x for x in self.events if x['_PID']!='568697'];return current,evidence,events
    def test_inactive_never_listened_startup_recovery_only(self):
        current,evidence,events=self.recovery_fixture();result=inactive_startup_recovery_allowed(self.old,current,evidence,events,30,False)
        self.assertEqual(result['physical_input_current'],'NOT_VERIFIED');self.assertEqual(result['rollback_target'],'B0_ONLY')
    def test_api_down_active_runtime_never_restarted(self):
        current,evidence,events=self.recovery_fixture();current['core']['MainPID']='568697'
        with self.assertRaises(AssertionError):inactive_startup_recovery_allowed(self.old,current,evidence,events,30,False)
    def test_inactive_recovery_rejects_former_API_listening(self):
        current,evidence,events=self.recovery_fixture()
        with self.assertRaises(AssertionError):inactive_startup_recovery_allowed(self.old,current,evidence,self.events,30,False)
    def test_inactive_recovery_rejects_pending_or_uncertain_owner(self):
        for key,value in [('job','18'),('cgroup_pids',[99]),('alsa_output_clients',['unowned']),('process_scan_complete',False),('complete_since_initial_capture',False),('no_musical_commands',False),('candidate_process_alive',True)]:
            with self.subTest(key=key):
                current,evidence,events=self.recovery_fixture();evidence[key]=value
                with self.assertRaises(AssertionError):inactive_startup_recovery_allowed(self.old,current,evidence,events,30,False)
    def test_inactive_recovery_rejects_stale_or_owned_work(self):
        current,evidence,events=self.recovery_fixture()
        for age,owned in [(61,False),(30,True)]:
            with self.assertRaises(AssertionError):inactive_startup_recovery_allowed(self.old,current,evidence,events,age,owned)

    def test_late_health_READY_rejected(self):
        clock=[0]
        def get(timeout):clock[0]=20;return {'status':'ok','application':{'state':'READY'}}
        with self.assertRaises(AssertionError):wait_candidate_ready(get,lambda timeout:self.new,self.old,lambda:clock[0],lambda delay:None)
    def test_late_identity_rejected(self):
        clock=[0]
        def identity(timeout):clock[0]=20;return self.new
        with self.assertRaises(AssertionError):wait_candidate_ready(lambda timeout:{'status':'ok','application':{'state':'READY'}},identity,self.old,lambda:clock[0],lambda delay:None)
    def test_receipt_file_and_directory_durable_before_helper(self):
        from unittest.mock import patch
        from release_harness import legacy_migration as module
        calls=[];real=module.os.fsync
        def sync(fd):calls.append('fsync');real(fd)
        with tempfile.TemporaryDirectory() as tmp,patch.object(module.os,'fsync',side_effect=sync):
            migrate_once(tmp,self.old,self.template,True,lambda:calls.append('deploy'),lambda:'/var/lib/piano-core-deploy/backups/piano-core-rollback-B0',lambda:self.new,lambda:self.events,lambda anchor:None)
        self.assertEqual(calls[:3],['fsync','fsync','deploy'])
    def test_receipt_durability_failure_prevents_helper(self):
        from unittest.mock import patch
        from release_harness import legacy_migration as module
        calls=[]
        with tempfile.TemporaryDirectory() as tmp,patch.object(module.os,'fsync',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):migrate_once(tmp,self.old,self.template,True,lambda:calls.append('deploy'),lambda:'',lambda:self.new,lambda:self.events,lambda anchor:None)
            self.assertTrue((Path(tmp)/'migration-attempt-used.json').exists())
        self.assertEqual(calls,[])

    def test_known_trace_before_API_stop_rejected(self):
        events=copy.deepcopy(self.events);i=self.error_index();block=events[i:i+15];del events[i:i+15]
        api=next(j for j,x in enumerate(events) if x['MESSAGE'].endswith('INFO piano_core.api api server stopped'))
        stamp=int(events[0]['__MONOTONIC_TIMESTAMP'])+100
        for offset,row in enumerate(block):row['__MONOTONIC_TIMESTAMP']=str(stamp+offset)
        events[api:api]=block
        with self.assertRaises(AssertionError):self.classify(events)
    def test_more_than_two_known_blocks_rejected(self):
        events=copy.deepcopy(self.events);decision=self.classify();cursors=list(decision['accepted_historical_records'])
        rows=[copy.deepcopy(x) for x in events if x['__CURSOR'] in set(cursors[-15:])]
        index=next(i for i,x in enumerate(events) if x['MESSAGE'].endswith('INFO piano_core.led.transport led transport stopped'))
        stamp=int(events[index-1]['__MONOTONIC_TIMESTAMP'])+1
        for offset,row in enumerate(rows):row['__CURSOR']+='third';row['__MONOTONIC_TIMESTAMP']=str(stamp+offset)
        events[index:index]=rows
        with self.assertRaises(AssertionError):self.classify(events)
    def test_unknown_journal_severity_rejected(self):
        events=copy.deepcopy(self.events);events[-1]['PRIORITY']='2'
        with self.assertRaises(AssertionError):self.classify(events)

    def test_failed_startup_capture_failure_never_masks_primary(self):
        def failed_startup():raise RuntimeError('PRIMARY startup defect')
        def failed_capture():raise ValueError('secondary journal failure')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'PRIMARY startup defect'):
                migrate_once(tmp,self.old,self.template,True,lambda:None,lambda:'/var/lib/piano-core-deploy/backups/piano-core-rollback-B0',failed_startup,failed_capture,lambda anchor:None)
            self.assertIn('secondary journal failure',(Path(tmp)/'initial-failure-journal-capture-error.json').read_text())
