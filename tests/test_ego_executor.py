import copy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from monitor.ego_executor import EgoExecutor
from tests import test_hybrid_chase as fixtures


class EgoExecutorTests(unittest.TestCase):
    def test_user_control_marks_every_queue_row_paused_without_changing_grants(self):
        self.attach(True);self.e.queue=[{'account_id':self.h.account['id'],'kind':'chase','phase':'tasks','ready':True,'reason':''}]
        self.e.dispatch('event',{'message':'Control of this task space is with the user','suspended':True})
        status=self.e.status();self.assertFalse(status['scheduler']['queue'][0]['ready']);self.assertIn('控制权待恢复',status['scheduler']['queue'][0]['reason'])
        self.assertTrue(self.h.queue.auto.row(self.h.account['id'])['enabled']);self.assertEqual(self.h.posts,[])

    def test_slow_begin_keeps_status_and_stop_event_responsive(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        c=self.ready();entered=threading.Event();release=threading.Event();original=self.h.hybrid.begin
        def slow_begin(p):entered.set();release.wait(5);return original(p)
        self.h.hybrid.begin=slow_begin
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(self.begin,c);self.assertTrue(entered.wait(1))
            try:
                status=pool.submit(self.e.status).result(timeout=1)
                self.assertTrue(status['armed'])
                self.e.dispatch('event',{'message':'平台核验中','busy':True})
            finally:release.set()
            self.assertTrue(pending.result(timeout=2)['submit_once'])
        self.assertEqual(self.h.posts,[])

    def setUp(self):
        self.h=fixtures.HybridTests('runTest');self.h.setUp()
        self.tmp=TemporaryDirectory();root=Path(self.tmp.name);(root/'data').mkdir()
        self.e=EgoExecutor(SimpleNamespace(root=root,port=8877,official_actions=self.h.queue))
        self.e.exe=Path(__file__)
        self.h.queue.hybrid_rules=SimpleNamespace(next=lambda p:{'ok':True,'command':None},active=lambda aid:False)

    def tearDown(self):
        self.h.tearDown();self.tmp.cleanup()

    def attach(self,armed=False):
        self.e.configure({'provider':'ego','enabled':True,'armed':armed})
        self.h.queue.browser_executor=self.e

    def ready(self):
        c=self.h.ready()
        self.attach(True)
        # Simulate an expired read lease while keeping the actual draft and quota.
        with self.h.queue.db() as db:
            row=db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()
            d=json.loads(row['data']);d['command']['expires_at']='2000-01-01T00:00:00+08:00'
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        return self.e.dispatch('next',{})['command']

    def begin(self,c):
        return self.e.dispatch('begin',{'kind':'chase','body':{
            'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'action_id':c['action']['id'],
            'observation':self.h.observed(c,self.h.page_materials(),document='ego-before-submit')}})

    def test_local_secret_is_not_in_public_status_or_settings(self):
        self.assertTrue(self.e.authenticate(self.e.secret))
        self.assertFalse(self.e.authenticate('wrong'))
        self.assertNotIn(self.e.secret,json.dumps(self.e.status()))
        self.attach();self.assertNotIn(self.e.secret,self.e.path.read_text())

    def test_migration_excludes_extension_and_forbids_forged_arm(self):
        self.attach()
        with self.assertRaises(ValueError):self.h.hybrid.worker(self.h.worker)
        with self.e.ego_context():
            self.h.hybrid.worker(self.e.metadata())
            forged=self.e.metadata();forged['armed']=True
            with self.assertRaises(ValueError):self.h.hybrid.worker(forged)
        self.assertEqual(self.h.hybrid.status()[0]['bridge']['kind'],'ego')
        self.assertFalse(self.h.hybrid.status()[0]['bridge']['armed'])

    def test_ego_keeps_original_budget_guard_and_consumes_final_once(self):
        c=self.ready();result=self.begin(c)
        body={'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':result['submit_token']}
        self.assertTrue(self.e.dispatch('final',{'kind':'chase','body':body})['allow'])
        with self.assertRaises(ValueError):self.e.dispatch('final',{'kind':'chase','body':body})
        self.assertEqual(self.h.posts,[])
        self.assertEqual(self.h.queue.auto.daily(self.h.account['id']),1)

    def test_turning_execution_off_revokes_an_issued_final_permit(self):
        c=self.ready();result=self.begin(c)
        self.e.configure({'provider':'ego','enabled':True,'armed':False})
        with self.assertRaises(ValueError):
            self.e.dispatch('final',{'kind':'chase','body':{'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':result['submit_token']}})
        self.assertEqual(self.h.queue.status()[0]['state'],'submitting')
        self.assertEqual(self.h.posts,[])

    def test_checkpoint_after_begin_does_not_reissue_a_submit(self):
        c=self.ready();self.begin(c)
        self.assertTrue(self.e.dispatch('checkpoint',{'kind':'chase','body':{k:c[k] for k in ('run_id','id','lease')}})['ok'])
        with self.assertRaises(ValueError):self.begin(c)

    def test_editor_proof_does_not_release_an_isolated_original_or_use_the_recent_list(self):
        c=self.ready();body={k:c[k] for k in ('run_id','id','lease')}
        old='0123456789abcdef00000001';name='QCX_1118_'+old[:16]
        current=c['action']['id']
        with self.h.queue.db() as db:
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(current,)).fetchone()
            intent=json.loads(row['intent']);intent['browser_batch']['task_name']=name
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',(old,'unknown',json.dumps(intent),row['digest'],row['created_at'],row['expires_at'],row['updated_at'],None))
            run=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()
            expires=(self.h.queue.clock()-timedelta(minutes=15)).isoformat()
            data={'action_id':old,'phase':'verify','isolated_readback':True,'stop_requested':True,
                'permit':{'action_id':old,'consumed':True,'expires_at':expires}}
            changed=list(run);keys=list(run.keys());changed[keys.index('id')]='old-run';changed[keys.index('state')]='waiting'
            changed[keys.index('data')]=json.dumps(data)
            db.execute('INSERT INTO hybrid_runs VALUES('+','.join('?' for _ in changed)+')',changed)
        def proof(task_name):return self.e.dispatch('editor-proof',{'kind':'chase','body':{**body,'task_name':task_name}})['editor']
        self.assertIsNone(proof(c['action']['intent']['browser_batch']['task_name']))
        self.assertIsNone(proof('QCX_1117_'+old[:16]))
        self.assertEqual(proof(name)['action_id'],old)
        with self.h.queue.db() as db:
            self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(old,)).fetchone()[0],'unknown')
            self.assertEqual(json.loads(db.execute("SELECT data FROM hybrid_runs WHERE id='old-run'").fetchone()[0]),data)
            data['isolated_readback']=False
            db.execute("UPDATE hybrid_runs SET data=? WHERE id='old-run'",(json.dumps(data),))
        self.assertIsNone(proof(name))

    def test_native_wrong_account_still_cannot_get_begin(self):
        c=self.ready();v=self.h.observed(c,self.h.page_materials());v['account_id']='8356939714484742'
        with self.assertRaises(ValueError):self.e.dispatch('begin',{'kind':'chase','body':{'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'action_id':c['action']['id'],'observation':v}})
        self.assertEqual(self.h.posts,[])

    def test_suspension_needs_explicit_resume_and_does_not_enable_execution(self):
        self.attach()
        self.e.dispatch('event',{'message':'用户接管','suspended':True})
        self.assertIsNone(self.e.dispatch('next',{})['command'])
        self.e.resume({})
        self.assertFalse(self.e.status()['suspended']);self.assertFalse(self.e.status()['armed'])

    def test_idle_heartbeat_does_not_hide_a_failed_native_stage(self):
        self.attach()
        self.e.dispatch('event',{'message':'等待重试','error':'原生表单暂不可用'})
        self.e.dispatch('event',{'message':'本机连接正常','connected':True})
        self.assertEqual(self.e.status()['error'],'原生表单暂不可用')
        self.e.dispatch('event',{'message':'阶段成功','error':None})
        self.assertIsNone(self.e.status()['error'])

    def test_rule_scans_do_not_starve_other_account_chase_commands(self):
        self.attach(True)
        from unittest.mock import Mock
        self.h.queue.hybrid.next=Mock(return_value={'ok':True,'command':{'run_id':'chase'}})
        self.h.queue.hybrid_rules.next=Mock(return_value={'ok':True,'command':{'run_id':'rules'}})
        self.assertEqual([self.e.dispatch('next',{})['kind'] for _ in range(4)],['chase','rules','chase','rules'])

    def test_prepared_rule_edit_is_not_delayed_by_a_new_chase_batch(self):
        self.attach(True)
        from unittest.mock import Mock
        self.h.queue.hybrid.next=Mock(return_value={'ok':True,'command':{'run_id':'chase'}})
        self.h.queue.hybrid_rules.urgent=Mock(return_value={'ok':True,'command':{'run_id':'prepared-rule'}})
        reply=self.e.dispatch('next',{})
        self.assertEqual(reply['kind'],'rules');self.assertEqual(reply['command']['run_id'],'prepared-rule')
        self.h.queue.hybrid.next.assert_not_called()

    def test_live_dispatch_scopes_each_stage_and_rotates_three_accounts(self):
        self.attach(True)
        from unittest.mock import Mock
        rows=[dict(account_id=a,kind='chase',phase='tasks',priority=2,ready=True,reason='',updated_at='same') for a in ('female','bra','cotton')]
        self.e.queue_hints=lambda meta:rows
        self.h.queue.hybrid.next=Mock(side_effect=lambda p,account_id=None:{'ok':True,'command':{'run_id':account_id,'account_id':account_id}})
        order=[self.e.dispatch('next',{})['command']['account_id'] for _ in range(6)]
        self.assertEqual(len(set(order[:3])),3);self.assertEqual(order[:3],order[3:])
        self.assertEqual(self.h.posts,[])


if __name__=='__main__':unittest.main()
