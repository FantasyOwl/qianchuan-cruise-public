"""No network writes: account routing, process concurrency, revocation and recovery."""
import copy
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from tests import test_ego_executor as executor_fixtures
from monitor.ego_executor import EgoExecutor


class AccountPoolTests(unittest.TestCase):
    setUp=executor_fixtures.EgoExecutorTests.setUp
    tearDown=executor_fixtures.EgoExecutorTests.tearDown
    attach=executor_fixtures.EgoExecutorTests.attach

    def setup_pool(self):
        self.attach(True)
        self.ids=[self.h.account['id'],'8531163845726695','8929253702525378']
        self.accounts=[{**self.h.account,'id':aid,'name':str(i)} for i,aid in enumerate(self.ids)]
        self.h.queue.accounts=lambda:self.accounts
        self.e.pool.configure({'mode':'account_parallel','max_parallel':3})
        self.ws=[self.e.pool.ensure(aid) for aid in self.ids]

    def ready(self):
        c=self.h.ready();self.setup_pool()
        with self.h.queue.db() as db:
            r=db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(r[0]);d['command']['expires_at']='2000-01-01T00:00:00+08:00'
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        return self.e.dispatch('next',{},worker=self.ids[0])['command']

    def begin(self,c,aid=None):
        return self.e.dispatch('begin',{'kind':'chase','body':{'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'action_id':c['action']['id'],
            'observation':self.h.observed(c,self.h.page_materials(),document='pool-before-submit')}},worker=aid or self.ids[0])

    def test_account_credentials_and_runtime_directories_are_disjoint_and_private(self):
        self.setup_pool()
        self.assertEqual(len({w.token for w in self.ws}),3);self.assertEqual(len({w.worker_id for w in self.ws}),3)
        for w in self.ws:
            self.assertEqual(self.e.credential(w.token),w.account_id)
            self.assertEqual(json.loads((w.directory/'private.json').read_text())['account_id'],w.account_id)
            self.assertNotIn(w.token,json.dumps(self.e.status()))
        self.assertFalse(self.e.authenticate(self.e.secret))
        with self.assertRaises(ValueError):self.e.dispatch('next',{})
        with self.assertRaises(ValueError):self.e.pool.ensure('../foreign')

    def test_scoped_workers_only_receive_their_own_queue_and_session(self):
        self.setup_pool();calls=[]
        self.e.queue_hints=lambda meta:[dict(account_id=aid,kind='chase',phase='tasks',priority=2,ready=True,updated_at='same') for aid in self.ids]
        def next_command(meta,account_id=None):
            calls.append((account_id,meta['worker_id']))
            return {'command':{'run_id':account_id,'account_id':account_id,'plan_id':self.h.pid}}
        self.h.hybrid.next=next_command
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda aid:self.e.dispatch('next',{},worker=aid),self.ids))
        self.assertEqual([r['command']['account_id'] for r in results],self.ids)
        self.assertEqual(dict(calls),{w.account_id:w.worker_id for w in self.ws})
        self.assertEqual(self.h.posts,[])

    def test_foreign_result_begin_and_final_are_rejected_before_business_handler(self):
        c=self.ready()
        for operation,body in [('result',{k:c[k] for k in ('run_id','id','lease')}),('begin',{'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'action_id':c['action']['id']}),('final',{'run_id':c['run_id'],'submit_token':'foreign'})]:
            with self.assertRaisesRegex(ValueError,'不属于'):self.e.dispatch(operation,{'kind':'chase','body':body},worker=self.ids[1])
        self.assertEqual(self.h.posts,[])
        permit=self.begin(c)
        p={'kind':'chase','body':{'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']}}
        self.assertTrue(self.e.dispatch('final',p,worker=self.ids[0])['allow'])
        with self.assertRaises(ValueError):self.e.dispatch('final',p,worker=self.ids[0])
        self.assertEqual(self.h.queue.auto.daily(self.ids[0]),1)

    def test_pausing_one_account_revokes_its_old_permit_even_after_start(self):
        c=self.ready();permit=self.begin(c);other_epoch=self.ws[1].epoch
        self.e.pool.control({'account_id':self.ids[0],'operation':'pause'})
        self.e.pool.control({'account_id':self.ids[0],'operation':'start'})
        with self.assertRaises(ValueError):self.e.dispatch('final',{'kind':'chase','body':{'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']}},worker=self.ids[0])
        self.assertEqual(self.ws[1].epoch,other_epoch)
        self.assertEqual(self.h.queue.status()[0]['state'],'submitting');self.assertEqual(self.h.posts,[])

    def test_user_takeover_is_scoped_persisted_and_requires_explicit_resume(self):
        self.setup_pool()
        for aid in self.ids:self.e.dispatch('event',{'message':'connected','connected':True},worker=aid)
        self.e.dispatch('event',{'message':'用户接管','suspended':True},worker=self.ids[0])
        self.assertIsNone(self.e.dispatch('next',{},worker=self.ids[0])['command'])
        self.assertFalse(self.e.bridge(self.ids[0])['armed']);self.assertTrue(self.e.bridge(self.ids[1])['armed'])
        restored=EgoExecutor(self.e.app)
        self.assertTrue(restored.pool.ensure(self.ids[0]).suspended)
        self.e.pool.control({'account_id':self.ids[0],'operation':'start'});self.assertTrue(self.ws[0].suspended)
        self.e.pool.control({'account_id':self.ids[0],'operation':'resume'});self.assertFalse(self.ws[0].suspended)
        self.assertEqual(self.ws[0].resume_generation,1)

    def test_concurrent_account_status_events_do_not_overwrite_others(self):
        self.setup_pool()
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda aid:self.e.dispatch('event',{'message':aid,'busy':True,'connected':True},worker=aid),self.ids))
        self.e.dispatch('event',{'message':'本户错误','error':'timeout','busy':False},worker=self.ids[0])
        rows={r['account_id']:r for r in self.e.status()['account_workers']}
        self.assertEqual(rows[self.ids[0]]['error'],'timeout');self.assertTrue(rows[self.ids[1]]['busy']);self.assertIsNone(rows[self.ids[1]]['error'])

    def test_pool_runs_different_accounts_concurrently_never_two_for_one_account(self):
        self.setup_pool();self.e.pool.configure({'mode':'account_parallel','max_parallel':2})
        self.e.queue_hints=lambda meta:[dict(account_id=aid,kind='chase',phase='tasks',priority=2,ready=True,updated_at='same') for aid in self.ids]
        processes=[]
        def popen(*args,**kwargs):
            p=Mock();p.poll.return_value=None;p.returncode=0;p.communicate.return_value=(b'',b'');processes.append(p);return p
        with patch('monitor.ego_pool.subprocess.Popen',side_effect=popen) as spawn:
            self.e.pool.tick(threading.Event());self.assertEqual(spawn.call_count,2)
            active=[w for w in self.ws if w.process];self.assertEqual(len(active),2)
            self.e.pool.tick(threading.Event());self.assertEqual(spawn.call_count,2)
            active[0].process.poll.return_value=0
            self.e.pool.tick(threading.Event());self.assertEqual(spawn.call_count,3)
            self.assertTrue(all(w.last_launch for w in self.ws));processes[1].terminate.assert_not_called()

    def test_timeout_and_backoff_only_stop_the_owning_cli_child(self):
        self.setup_pool();now=time.monotonic()
        for w in self.ws[:2]:
            w.process=Mock();w.process.poll.return_value=None;w.process.returncode=1;w.process.communicate.return_value=(b'',b'');w.started=now
        expired=self.ws[0].process;other=self.ws[1].process;self.ws[0].started=now-181
        self.e.pool.poll(threading.Event())
        expired.terminate.assert_called_once();other.terminate.assert_not_called()
        self.assertIsNone(self.ws[0].process);self.assertIs(self.ws[1].process,other)
        self.assertGreater(self.ws[0].next_launch,now+20)

    def test_explicit_start_drops_idle_and_failure_delays_without_waiting_for_an_api_command(self):
        self.setup_pool();w=self.ws[0];w.paused=True;w.last_launch=time.monotonic();w.next_launch=w.last_launch+300
        self.e.pool.control({'account_id':w.account_id,'operation':'start'})
        self.assertEqual(w.next_launch,0);self.assertEqual(w.last_launch,0)
        self.e.queue_hints=lambda meta:[]
        with patch.object(self.e.pool,'_launch') as launch:self.e.pool.tick(threading.Event())
        self.assertIn(w,[call.args[0] for call in launch.call_args_list]);self.assertEqual(self.h.posts,[])

    def test_restart_cancels_only_unsent_lease_and_stops_only_its_cli_child(self):
        c=self.ready();w=self.ws[0];other=self.ws[1];old_epoch=w.epoch;other_epoch=other.epoch
        w.process=Mock();w.process.poll.return_value=None;w.process.communicate.return_value=(b'',b'');w.process.returncode=1
        other.process=Mock();other.process.poll.return_value=None;other.started=time.monotonic()
        old=w.process;other_proc=other.process
        self.e.pool.control({'account_id':w.account_id,'operation':'restart'})
        self.assertNotEqual(w.epoch,old_epoch);self.assertEqual(other.epoch,other_epoch)
        with self.h.queue.db() as db:
            self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(c['action']['id'],)).fetchone()[0],'cancelled')
            self.assertEqual(db.execute('SELECT state FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()[0],'cancelled')
        self.e.pool.poll(threading.Event());old.terminate.assert_called_once();other_proc.terminate.assert_not_called()
        self.assertEqual(w.next_launch,0);self.assertEqual(self.h.posts,[])

    def test_restart_keeps_uncertain_submission_and_never_replays_the_consumed_permit(self):
        c=self.ready();permit=self.begin(c)
        self.e.dispatch('final',{'kind':'chase','body':{'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']}},worker=self.ids[0])
        self.e.pool.control({'account_id':self.ids[0],'operation':'restart'})
        with self.h.queue.db() as db:
            self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(c['action']['id'],)).fetchone()[0],'submitting')
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()[0]);self.assertTrue(d['permit']['consumed']);self.assertEqual(d['phase'],'verify')
        with self.assertRaises(ValueError):self.e.dispatch('final',{'kind':'chase','body':{'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']}},worker=self.ids[0])
        self.assertEqual(self.h.posts,[])

    def test_restart_can_retire_the_same_accounts_lease_from_a_previous_service(self):
        c=self.ready()
        with self.h.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()[0]);d['command']['worker_id']='ego:previous-service'
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        self.e.pool.control({'account_id':self.ids[0],'operation':'restart'})
        with self.h.queue.db() as db:self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(c['action']['id'],)).fetchone()[0],'cancelled')
        self.assertEqual(self.h.posts,[])
    def test_previous_batch_consumed_permit_cannot_make_a_new_draft_look_submitted(self):
        c=self.ready()
        with self.h.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()[0]);d['permit']={'action_id':'previous-verified-batch','consumed':True}
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        self.e.pool.control({'account_id':self.ids[0],'operation':'restart'})
        with self.h.queue.db() as db:self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(c['action']['id'],)).fetchone()[0],'cancelled')
        self.assertEqual(self.h.posts,[])

    def test_slow_account_does_not_lock_others_heartbeat_or_status(self):
        c=self.ready();entered=threading.Event();release=threading.Event();original=self.h.hybrid.begin
        def slow(p):entered.set();release.wait(4);return original(p)
        self.h.hybrid.begin=slow
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending=pool.submit(self.begin,c);self.assertTrue(entered.wait(1))
            try:
                self.assertTrue(pool.submit(self.e.dispatch,'event',{'message':'other alive','connected':True},worker=self.ids[1]).result(timeout=1)['ok'])
                self.assertEqual(len(pool.submit(self.e.status).result(timeout=1)['account_workers']),3)
            finally:release.set()
            self.assertTrue(pending.result(timeout=2)['submit_once'])

    def test_global_execution_switch_revokes_pool_permits_without_enabling_grants(self):
        c=self.ready();permit=self.begin(c)
        self.e.configure({'provider':'ego','enabled':True,'armed':False});self.e.configure({'provider':'ego','enabled':True,'armed':True})
        with self.assertRaises(ValueError):self.e.dispatch('final',{'kind':'chase','body':{'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']}},worker=self.ids[0])
        self.assertEqual(self.h.posts,[])

    def test_running_process_prevents_mode_switch_and_invalid_limits_fail(self):
        self.setup_pool();self.ws[0].process=Mock()
        with self.assertRaises(ValueError):self.e.pool.configure({'mode':'serial','max_parallel':3})
        for limit in [True,0,9,'3']:
            with self.assertRaises(ValueError):self.e.pool.configure({'mode':'account_parallel','max_parallel':limit})

    def test_one_accounts_preview_does_not_block_another_accounts_rules(self):
        self.setup_pool();self.h.hybrid.request(self.ids[0],reason='只读隔离验证',preview=True)
        with self.h.queue.db() as db:
            self.assertTrue(self.h.hybrid.preview_ready(db,self.ids[0]))
            self.assertFalse(self.h.hybrid.preview_ready(db,self.ids[1]))
            self.assertTrue(self.h.hybrid.preview_ready(db))

    def test_actual_ego_handback_resumes_only_that_account_without_claiming_or_changing_authority(self):
        self.setup_pool();w=self.ws[0];old_epoch=w.epoch;generation=w.resume_generation
        self.e.dispatch('event',{'message':'EGO_USER_CONTROL: 常驻 Space 当前由用户控制','suspended':True},worker=w.account_id)
        w.space_id=5
        self.assertEqual(self.e.pool.apply_ownership([{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}]),[w.account_id])
        self.assertFalse(w.suspended);self.assertNotEqual(w.epoch,old_epoch);self.assertEqual(w.resume_generation,generation)
        self.assertTrue(self.e.settings['armed']);self.assertTrue(self.h.queue.auto.row(w.account_id)['enabled']);self.assertEqual(self.h.posts,[])

    def test_user_delegated_missing_renamed_and_manual_pause_states_never_resume_automatically(self):
        self.setup_pool();w=self.ws[0];w.space_id=5;w.suspended=True;w.control_wait=True
        for rows in [[],[{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'user'}],[{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'agentDelegatedToUser'}],[{'id':6,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}],[{'id':5,'name':'other space','ownership':'agent'}]]:
            self.assertEqual(self.e.pool.apply_ownership(rows),[]);self.assertTrue(w.suspended)
        w.paused=True
        self.assertEqual(self.e.pool.apply_ownership([{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}]),[]);self.assertTrue(w.suspended)

    def test_browser_permission_or_login_suspension_is_not_a_control_handback(self):
        self.setup_pool();w=self.ws[0];w.space_id=5
        self.e.dispatch('event',{'message':'登录状态需要用户确认','suspended':True},worker=w.account_id)
        self.assertFalse(w.control_wait)
        self.assertEqual(self.e.pool.apply_ownership([{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}]),[])

    def test_saved_user_control_failure_can_be_reconciled_after_service_restart(self):
        self.setup_pool();w=self.ws[0];w.space_id=5
        self.e.dispatch('event',{'message':'EGO_USER_CONTROL: 常驻 Space 当前由用户控制','suspended':True},worker=w.account_id)
        (w.directory/'runtime.json').write_text(json.dumps({'spaceId':5,'trace':[{'phase':'failed','message':'EGO_USER_CONTROL: 常驻 Space 当前由用户控制'}]}),'utf-8')
        restored=EgoExecutor(self.e.app);current=restored.pool.ensure(w.account_id)
        self.assertTrue(current.suspended);self.assertTrue(current.control_wait)
        restored.pool.apply_ownership([{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}]);self.assertFalse(current.suspended)

    def test_automatic_handback_cannot_reuse_a_previous_submit_permit(self):
        c=self.ready();permit=self.begin(c);w=self.ws[0];w.space_id=5
        self.e.dispatch('event',{'message':'EGO_USER_CONTROL: 常驻 Space 当前由用户控制','suspended':True},worker=w.account_id)
        self.e.pool.apply_ownership([{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}])
        with self.assertRaises(ValueError):self.e.dispatch('final',{'kind':'chase','body':{'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']}},worker=w.account_id)
        self.assertEqual(self.h.posts,[]);self.assertEqual(self.h.queue.status()[0]['state'],'submitting')

    def test_control_probe_accepts_only_one_complete_metadata_record(self):
        self.setup_pool();w=self.ws[0];w.space_id=5;w.suspended=True;w.control_wait=True
        for output in [b'noise only',b'QCX_OWNERSHIP_V1:invalid',b'QCX_OWNERSHIP_V1:[]\nQCX_OWNERSHIP_V1:[]']:
            proc=Mock();proc.poll.return_value=0;proc.returncode=0;proc.communicate.return_value=(output,b'')
            self.e.pool.control_process=proc;self.e.pool.control_started=time.monotonic();self.e.pool.poll_control(threading.Event());self.assertTrue(w.suspended)
        payload=json.dumps([{'id':5,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}]).encode()
        proc.communicate.return_value=(b'notice\nQCX_OWNERSHIP_V1:'+payload,b'');self.e.pool.control_process=proc;self.e.pool.control_started=time.monotonic()
        self.e.pool.poll_control(threading.Event());self.assertFalse(w.suspended);self.assertIsNone(self.e.pool.control_error)

    def test_windows_ego_stderr_metadata_resumes_and_conflicting_streams_are_rejected(self):
        self.setup_pool();w=self.ws[0];w.space_id=6;w.suspended=True;w.control_wait=True
        record=b'QCX_OWNERSHIP_V1:'+json.dumps([{'id':6,'name':'千川巡航 · '+w.account_id,'ownership':'agent'}]).encode()+b'\r\n'
        proc=Mock();proc.poll.return_value=0;proc.returncode=0
        proc.communicate.return_value=(record,record)
        self.e.pool.control_process=proc;self.e.pool.control_started=time.monotonic()
        self.e.pool.poll_control(threading.Event());self.assertTrue(w.suspended);self.assertIsNotNone(self.e.pool.control_error)
        proc.communicate.return_value=(b'',b'[ego-browser:notice] update available\r\n'+record)
        self.e.pool.control_process=proc;self.e.pool.control_started=time.monotonic()
        self.e.pool.poll_control(threading.Event());self.assertFalse(w.suspended);self.assertIsNone(self.e.pool.control_error)


if __name__=='__main__':unittest.main()
