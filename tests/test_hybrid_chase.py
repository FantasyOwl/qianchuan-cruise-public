import copy
import json
from datetime import timedelta

import unittest
from tests import test_official_workflow as fixtures
from monitor.hybrid_chase import HybridChase
from monitor.schema import now_cn
from monitor.official_api import OfficialBusinessError


class HybridTests(unittest.TestCase):
    transport=fixtures.WorkflowTests.transport
    tearDown=fixtures.WorkflowTests.tearDown
    confirm=fixtures.WorkflowTests.confirm

    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        self.account['tab_group']='测试户'
        self.policy={'revision':'a'*64,'accounts':[self.account]}
        self.hybrid=HybridChase(self.queue,lambda:self.policy)
        self.queue.hybrid=self.hybrid
        # Most scenarios advance minutes within one business day. Pin noon
        # rather than accidentally crossing midnight when this suite runs late.
        self.at=now_cn().replace(hour=12,minute=0,second=0,microsecond=0)
        self.queue.clock=lambda:self.at
        self.grant=self.queue.auto.configure({'account_id':self.account['id'],'plan_id':self.pid,
            'profile_revision':'r1','enabled':True,'active_budget_cap':'500','max_new_batches_per_day':'3','confirmed':True})
        self.worker={'protocol':1,'worker_id':'test-worker','epoch':'test-epoch','armed':True,'revision':self.policy['revision'],'extension_version':'1.8.0'}
        self.hybrid.worker(self.worker)
        self.hybrid.configure({'account_id':self.account['id'],'mode':'api_preferred'})
        self.read_calls=len(self.calls)

    def command(self):
        result=self.hybrid.next(self.worker)
        self.assertIsNotNone(result.get('command'),result)
        return result['command']

    def observed(self,c,rows,*,total=None,document='document-read',page_size=None):
        material=c['phase'] in {'materials','prepare','dry_prepare'}
        return {'account_id':self.account['id'],'plan_id':self.pid,'path':'/overall-prom/detail','group':'测试户',
            'captured_at':self.at.isoformat(),'tab_id':11,'document_id':document,'page':c['page'],
            'page_size':page_size or (100 if material else 50),'total':len(rows) if total is None else total,
            'has_next':(total or len(rows))>c['page']*(page_size or (100 if material else 50)),
            'rows':copy.deepcopy(rows),'parent_budget':'10000','running':True,
            'date_start':c['date_start'] if material else c['date_end'],'date_end':c['date_end'],
            'delivery_entry':'chengfang','parent_bid_type':'SMART_BID_CUSTOM'}

    def feed(self,c,rows,**kwargs):
        return self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':True,'message':'测试观察',
            'observation':self.observed(c,rows,**kwargs)})

    def tasks(self):return [{'id':'10001','budget':'100','status':'调控中'}]

    def page_materials(self):
        return [{'id':mid,'name':'测试素材','spend':str(cost),'roi':str(roi),
            'chase_status':'-','approved':True,'selectable':True} for mid,cost,roi in self.materials_data]

    def ready(self,*,preview=False):
        self.materials_data=[('89581211092803411',400,4),('89041279016210106',300,4.2),('84300024958326955',200,4.5)]
        self.hybrid.request(self.account['id'],reason='测试40100，创建尚未提交',preview=preview)
        c=self.command();self.assertEqual(c['phase'],'tasks');self.feed(c,self.tasks())
        c=self.command();self.assertEqual(c['phase'],'materials');self.feed(c,self.page_materials())
        if preview:
            c=self.command();self.assertEqual(c['phase'],'dry_prepare');self.feed(c,self.page_materials());return None
        c=self.command();self.assertEqual(c['phase'],'tasks');self.feed(c,self.tasks())
        c=self.command();self.assertEqual(c['phase'],'prepare');return c

    def begin_web(self,c):
        return self.hybrid.begin({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],
            'action_id':c['action']['id'],'observation':self.observed(c,self.page_materials(),document='submitted-document')})

    def test_web_action_uses_same_daily_budget_ledger_and_verifies_without_api_post(self):
        c=self.ready();rid=c['action']['id']
        self.assertEqual(self.queue.auto.daily(self.account['id']),1)
        self.assertEqual(c['action']['intent']['execution_channel'],'browser')
        permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':rid,'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2)
        verify=self.command();self.assertEqual(verify['phase'],'verify')
        batch=verify['action']['intent']['browser_batch']
        self.feed(verify,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],
            'after':batch['after'],'status':'调控中'}],document='independent-new-document')
        self.assertEqual(self.queue.status()[0]['state'],'verified')
        self.assertEqual(self.queue.auto.owned(self.account['id'],self.pid),{'10000009'})
        self.assertEqual(self.queue.auto.daily(self.account['id']),1)
        self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertEqual(self.posts,[])
        self.assertEqual(len(self.calls),self.read_calls)

    def test_read_only_preview_never_creates_or_reserves_an_ad(self):
        self.worker['armed']=False
        self.ready(preview=True)
        self.assertEqual(self.queue.status(),[])
        self.assertEqual(self.queue.auto.daily(self.account['id']),0)
        self.assertEqual(self.hybrid.status()[0]['batches_ready'],1)
        self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertEqual(self.posts,[])

    def test_historical_task_lookup_uses_creation_day_without_treating_absence_as_zero(self):
        created=(self.at-timedelta(days=13)).isoformat()
        with self.queue.db() as db:
            intent={'account_id':self.account['id'],'plan_id':self.pid,'action':'material_chase'}
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('old-created','verified',json.dumps(intent),'test',created,created,created,json.dumps({'task_id':'10000009'})))
        self.hybrid.request(self.account['id'],reason='历史任务核对')
        self.feed(self.command(),self.tasks())
        lookup=self.command()
        self.assertEqual(lookup['phase'],'lookup')
        self.assertEqual(lookup['date_end'],created[:10])
        self.feed(lookup,[])
        self.assertIn('返回0条',self.hybrid.status()[0]['message'])
        lookup=self.command()
        self.assertEqual(lookup['lookup_id'],'10000009')
        self.feed(lookup,[{'id':'10000009','budget':'200','status':'已暂停'}])
        self.assertEqual(self.command()['phase'],'materials')
        self.assertEqual(self.posts,[])

    def test_cancelled_form_preview_does_not_require_historical_budget_lookups(self):
        with self.queue.db() as db:
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('old-preview','verified',json.dumps({'account_id':self.account['id'],'plan_id':self.pid,'action':'material_chase'}),'test',self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),json.dumps({'task_id':'10000009'})))
        self.hybrid.request(self.account['id'],reason='只预演并取消',preview=True)
        self.feed(self.command(),self.tasks())
        self.assertEqual(self.command()['phase'],'materials')
        self.assertEqual(self.queue.auto.daily(self.account['id']),0)

    def test_verify_reads_one_exact_task_without_claiming_the_other_table_rows_were_verified(self):
        c=self.ready();permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2)
        verify=self.command();batch=verify['action']['intent']['browser_batch']
        self.feed(verify,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],'after':batch['after'],'status':'调控中'}],total=50,page_size=50,document='independent-document')
        self.assertEqual(self.queue.status()[0]['state'],'verified')
        self.assertEqual(self.posts,[])

    def test_readback_can_find_the_original_on_a_later_page_without_another_creation(self):
        c=self.ready();permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2)
        first=self.command();self.feed(first,[{'id':'10000008','budget':'200','status':'已暂停'}],total=2,page_size=1,document='independent-document')
        second=self.command();self.assertEqual(second['page'],2);batch=second['action']['intent']['browser_batch']
        self.feed(second,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],'after':batch['after'],'status':'调控中'}],total=2,page_size=1,document='independent-document')
        self.assertEqual(self.queue.status()[0]['state'],'verified');self.assertEqual(self.queue.auto.daily(self.account['id']),1);self.assertEqual(self.posts,[])

    def test_missing_original_keeps_unknown_and_refreshes_only_the_readback_cycle(self):
        c=self.ready();permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2)
        self.feed(self.command(),[],document='independent-document')
        self.at+=timedelta(seconds=30)
        again=self.command();self.assertEqual(again['phase'],'verify');self.assertEqual(again['page'],1)
        self.assertEqual(again['document_cycle'],1);self.assertEqual(self.queue.status()[0]['state'],'unknown')
        self.assertEqual(self.queue.auto.daily(self.account['id']),1);self.assertEqual(len(self.queue.status()),1);self.assertEqual(self.posts,[])

    def test_a_long_stalled_readback_slows_down_instead_of_scanning_forever(self):
        # 25h of five-minute scans produced no match; only the cadence changes,
        # the original action stays unknown with its occupancy untouched.
        self.assertEqual(HybridChase.verify_wait(3),timedelta(minutes=5))
        self.assertEqual(HybridChase.verify_wait(12),timedelta(minutes=10))
        self.assertEqual(HybridChase.verify_wait(24),timedelta(minutes=30))
        self.assertEqual(HybridChase.verify_wait(48),timedelta(minutes=60))

    def test_one_permit_cannot_be_replayed_or_sent_by_api_confirm(self):
        c=self.ready();permit=self.begin_web(c)
        with self.assertRaises(ValueError):self.begin_web(c)
        p={'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']}
        self.assertTrue(self.hybrid.final(p)['allow'])
        with self.assertRaises(ValueError):self.hybrid.final(p)
        with self.assertRaisesRegex(ValueError,'不可转为API'):
            self.confirm(c['action'])
        self.assertEqual(self.posts,[])

    def test_disabling_authority_after_preparation_prevents_submission(self):
        c=self.ready();self.queue.auto.disable({'account_id':self.account['id']})
        with self.assertRaisesRegex(ValueError,'关闭'):
            self.begin_web(c)
        self.assertEqual(self.queue.status()[0]['state'],'draft')
        self.assertEqual(self.posts,[])

    def test_disabling_authority_after_begin_prevents_final_click_but_keeps_unknown_reserved(self):
        c=self.ready();permit=self.begin_web(c)
        self.queue.auto.disable({'account_id':self.account['id']})
        with self.assertRaisesRegex(ValueError,'关闭'):
            self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.assertEqual(self.queue.auto.daily(self.account['id']),1)
        self.assertEqual(self.command()['phase'],'verify')

    def test_stop_cancels_unsent_draft_but_never_resubmits_a_consumed_permit(self):
        c=self.ready();self.hybrid.stop({'account_id':self.account['id']})
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        self.assertFalse(self.hybrid.active(self.account['id']))
        self.assertEqual(self.queue.auto.daily(self.account['id']),0)

    def test_cross_account_incomplete_pages_and_stale_data_are_rejected(self):
        self.hybrid.request(self.account['id'],reason='测试')
        c=self.command();base=self.observed(c,self.tasks())
        for change in ({'account_id':'999'},{'plan_id':'888'},{'total':100},{'group':'另一个户'},
                {'captured_at':(self.at-timedelta(minutes=5)).isoformat()},{'running':False}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':True,
                    'message':'测试','observation':{**base,**change}})
        self.assertEqual(self.queue.status(),[])

    def test_result_uncertainty_keeps_the_same_original_action_after_command_expiry(self):
        c=self.ready();permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(minutes=3)
        self.worker['epoch']='reopened-epoch'
        verify=self.command()
        self.assertEqual(verify['phase'],'verify');self.assertEqual(verify['action']['id'],c['action']['id'])
        self.assertEqual(len(self.queue.status()),1)
        self.assertEqual(self.posts,[])

    def test_held_unknown_task_budget_keeps_the_original_verify_readable(self):
        c=self.ready();rid=c['action']['id']
        permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':rid,'submit_token':permit['submit_token']})
        # 本户另一笔预算修改结果不明，但已按准确任务跳过。跳过只解决该任务，
        # 不能把本户原网页单的只读回读一起停住，否则原单永远无法核验。
        with self.queue.db() as db:
            intent={'account_id':self.account['id'],'plan_id':self.pid,'action':'task_budget','task_id':'10001'}
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('held-budget','unknown',json.dumps(intent),'d',
                self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),None))
            db.execute('INSERT INTO api_task_holds VALUES(?,?,?,?,?,?,1)',(self.account['id'],self.pid,'10001','held-budget',
                self.at.isoformat(),self.at.isoformat()))
        self.at+=timedelta(minutes=3)
        verify=self.command()
        self.assertEqual(verify['phase'],'verify');self.assertEqual(verify['action']['id'],rid)
        self.assertEqual(self.posts,[])

    def test_unheld_unknown_action_still_parks_the_verify_readback(self):
        c=self.ready();permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        with self.queue.db() as db:
            intent={'account_id':self.account['id'],'plan_id':self.pid,'action':'task_budget','task_id':'10001'}
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('unheld-budget','unknown',json.dumps(intent),'d',
                self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),None))
        self.at+=timedelta(minutes=3)
        result=self.hybrid.next(self.worker)
        self.assertIsNone(result.get('command'))
        with self.queue.db() as db:
            row=db.execute("SELECT json_extract(data,'$.message') message,json_extract(data,'$.phase') phase FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(self.account['id'],)).fetchone()
        self.assertEqual(row['phase'],'verify');self.assertIn('待核验',row['message'])

    def held_budget(self,*,state='unknown',held=True,action='task_budget',hold_plan=None):
        intent={'account_id':self.account['id'],'plan_id':self.pid,'action':action,'task_id':'10001',
                'task_scene':'MATERIAL_ADD_BUDGET','before':'100','after':'200'}
        with self.queue.db() as db:
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('held-budget',state,json.dumps(intent),'d',
                self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),None))
            if held:db.execute('INSERT INTO api_task_holds VALUES(?,?,?,?,?,?,1)',(self.account['id'],hold_plan or self.pid,'10001','held-budget',self.at.isoformat(),self.at.isoformat()))

    def test_held_unknown_budget_allows_new_chase_read_but_keeps_largest_reservation(self):
        self.held_budget()
        self.assertTrue(self.hybrid.request(self.account['id'],reason='原预算已按准确任务跳过，读取其他素材'))
        self.assertEqual(self.command()['phase'],'tasks')
        budget=self.queue.budget_tasks_with_holds(self.account['id'],self.pid,[])
        self.assertEqual(len(budget),1);self.assertEqual(budget[0]['id'],'10001');self.assertEqual(float(budget[0]['budget']),200)
        original=next(r for r in self.queue.status() if r['id']=='held-budget')
        self.assertEqual(original['state'],'unknown');self.assertEqual(self.posts,[])

    def test_unheld_unknown_budget_still_blocks_new_chase(self):
        self.held_budget(held=False)
        with self.assertRaisesRegex(ValueError,'结果不明'):self.hybrid.request(self.account['id'],reason='不能绕过未隔离原单')
        self.assertFalse(self.hybrid.active(self.account['id']))

    def test_new_material_batch_can_reach_one_final_permit_while_held_budget_stays_unknown(self):
        self.held_budget()
        c=self.ready();rid=c['action']['id'];permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':rid,'submit_token':permit['submit_token']})
        original=next(r for r in self.queue.status() if r['id']=='held-budget')
        self.assertEqual(original['state'],'unknown')
        tasks=self.queue.budget_tasks_with_holds(self.account['id'],self.pid,[{'id':'10001','budget':'100','scene':'MATERIAL_ADD_BUDGET'}])
        self.assertEqual(float(tasks[0]['budget']),200)
        with self.assertRaisesRegex(ValueError,'已消费|重复|许可'):
            self.hybrid.final({'run_id':c['run_id'],'action_id':rid,'submit_token':permit['submit_token']})
        self.assertEqual(self.posts,[])

    def test_a_hold_on_another_parent_cannot_unlock_this_chase(self):
        self.held_budget(hold_plan='999999999')
        with self.assertRaisesRegex(ValueError,'结果不明'):self.hybrid.request(self.account['id'],reason='不能使用其他母计划的跳过')

    def test_a_held_submitting_budget_is_not_treated_as_completed(self):
        self.held_budget(state='submitting')
        with self.assertRaisesRegex(ValueError,'结果不明'):self.hybrid.request(self.account['id'],reason='原提交仍在进行')

    def test_holding_an_unknown_creation_never_unlocks_another_creation(self):
        self.held_budget(action='material_chase')
        with self.assertRaisesRegex(ValueError,'结果不明'):self.hybrid.request(self.account['id'],reason='不能把未知创建当成预算隔离')

    def test_held_uncertain_pause_keeps_native_budget_and_allows_other_materials(self):
        self.held_budget(action='pause_task')
        with self.queue.db() as db:
            row=db.execute("SELECT intent FROM api_actions WHERE id='held-budget'").fetchone();i=json.loads(row['intent'])
            i.update(before=True,after=False,browser_surface={'budget':'300'})
            db.execute("UPDATE api_actions SET intent=? WHERE id='held-budget'",(json.dumps(i),))
        self.assertTrue(self.hybrid.request(self.account['id'],reason='准确暂停任务已隔离'))
        tasks=self.queue.budget_tasks_with_holds(self.account['id'],self.pid,[])
        self.assertEqual(float(tasks[0]['budget']),300)
        self.assertEqual(next(r for r in self.queue.status() if r['id']=='held-budget')['state'],'unknown')
        self.assertEqual(self.posts,[])

    def test_held_pause_missing_native_budget_cannot_unlock_new_chase(self):
        self.held_budget(action='pause_task')
        with self.queue.db() as db:
            i=json.loads(db.execute("SELECT intent FROM api_actions WHERE id='held-budget'").fetchone()['intent']);i.update(before=True,after=False)
            db.execute("UPDATE api_actions SET intent=? WHERE id='held-budget'",(json.dumps(i),))
        with self.assertRaisesRegex(ValueError,'结果不明'):self.hybrid.request(self.account['id'],reason='缺少暂停任务预算证据')

    def test_shared_budget_blocks_next_batch_and_common_daily_limit_applies(self):
        c=self.ready();rid=c['action']['id']
        with self.queue.db() as db:
            row=db.execute('SELECT intent FROM api_actions WHERE id=?',(rid,)).fetchone()
            intent=json.loads(row['intent'])
            db.execute("UPDATE api_actions SET state='verified',result=? WHERE id=?",(json.dumps({'task_id':'10000009'}),rid))
        with self.assertRaisesRegex(ValueError,'在投'):
            self.queue.auto.check_budget({'account_id':self.account['id'],'plan_id':self.pid,'body':{'budget':400}},
                [{'id':'10000009','budget':200}],500)
        with self.queue.db() as db:db.execute('UPDATE api_auto SET batch_cap=1 WHERE account_id=?',(self.account['id'],))
        with self.assertRaisesRegex(ValueError,'新增批数'):
            self.queue.auto.verify_scope({'account_id':self.account['id'],'plan_id':self.pid,'auto_authority':{'grant_id':intent['auto_authority']['grant_id']}})

    def test_api_read_rate_limit_requests_browser_without_posting_or_changing_grant(self):
        self.at+=timedelta(seconds=2)
        self.api.scan_workflow=lambda *a,**k:(_ for _ in ()).throw(OfficialBusinessError(40100,operation='report_data',reason='rate_limited',retry_after=60))
        grant_before=self.queue.auto.row(self.account['id'])['grant_id']
        self.queue.auto.tick()
        self.assertTrue(self.hybrid.active(self.account['id']))
        self.assertEqual(self.command()['phase'],'tasks')
        self.assertEqual(self.queue.auto.row(self.account['id'])['grant_id'],grant_before)
        self.assertEqual(self.queue.auto.row(self.account['id'])['state'],'browser_wait')
        self.assertEqual(self.posts,[])

    def test_unknown_api_action_prevents_any_browser_fallback(self):
        with self.queue.db() as db:
            intent={'account_id':self.account['id'],'plan_id':self.pid,'action':'material_chase'}
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('unknown-old','unknown',json.dumps(intent),'d',self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),None))
        with self.assertRaisesRegex(ValueError,'结果不明'):
            self.hybrid.request(self.account['id'],reason='测试限流')
        self.assertFalse(self.hybrid.active(self.account['id']))

    def test_api_only_does_not_expand_authority_or_start_browser_jobs(self):
        self.hybrid.configure({'account_id':self.account['id'],'mode':'api_only'})
        self.assertFalse(self.hybrid.request(self.account['id'],reason='40100'))
        self.assertEqual(self.hybrid.next(self.worker)['command'],None)
        self.assertTrue(self.queue.auto.row(self.account['id'])['enabled'])

    def test_disable_browser_mode_after_begin_keeps_only_original_verification(self):
        c=self.ready();self.begin_web(c)
        self.hybrid.configure({'account_id':self.account['id'],'mode':'api_only'})
        self.assertEqual(self.command()['phase'],'verify')
        self.assertEqual(self.queue.auto.daily(self.account['id']),1)

    def test_unconsumed_final_permit_expires_as_known_unsent_and_releases_the_reservation(self):
        c=self.ready();self.begin_web(c)
        self.at+=timedelta(minutes=3);self.hybrid.tick()
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        self.assertEqual(self.queue.auto.daily(self.account['id']),0)
        self.assertEqual(self.command()['phase'],'tasks')
