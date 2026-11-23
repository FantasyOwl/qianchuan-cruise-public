import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from monitor.budget_recovery import POLICY
from tests import test_official_extensions as fixtures


class BudgetRecoveryTests(unittest.TestCase):
    tearDown=fixtures.ExtensionTests.tearDown

    def setUp(self):
        fixtures.ExtensionTests.setUp(self)
        self.plan.update(target_roi=3,target_roi_basis='overall_payment')
        for cfg in self.profile['features'].values():cfg['enabled']=False
        self.task={'id':10001,'task_status':'OFFLINE_BUDGET','budget':100,'duration':87672,
            'smart_bid_type':'SMART_BID_CUSTOM','bid':20,'roi2_goal':None,
            'create_time':'2026-10-01 08:00:00','external_action_name':'直播间支付',
            'metrics':{'stat_cost_for_roi2_assist':100,'total_prepay_and_pay_order_roi2_assist':4,
                'total_prepay_and_pay_settle_roi2_1h_assist':4}}
        self.operations=[];self.auto_resume=False;self.fail_budget=False;self.apply_unknown=True;self.fail_resume=False
        self.queue.budget_recovery.path.write_text(json.dumps({**POLICY,'enabled':True}),encoding='utf-8')

    def transport(self,name,q,token=None):
        if name=='control_tasks':
            self.calls.append((name,copy.deepcopy(q)))
            status=q.get('filtering',{}).get('task_status')
            rows=[copy.deepcopy(self.task)] if q['scene']=='MATERIAL_ADD_BUDGET' and (not status or status==self.task['task_status']) else []
            return {'task_list':rows,'page_info':{'total_number':len(rows)}}
        if name=='update_task_budget':
            self.posts.append(copy.deepcopy(q));self.operations.append(name)
            if not self.fail_budget or self.apply_unknown:self.task['budget']=q['update_budget_infos'][0]['budget']
            if self.auto_resume:self.task['task_status']='PROCESSING'
            if self.fail_budget:raise OSError('uncertain budget POST')
            return {'results':[{'task_id':10001,'status':True}]}
        if name=='pause_control_task':
            self.posts.append(copy.deepcopy(q));self.operations.append(name)
            if q['opt_type']!='ENABLE':raise AssertionError('recovery must only enable')
            self.task['task_status']='PROCESSING'
            if self.fail_resume:raise OSError('uncertain enable POST')
            return {'task_ids':[10001],'errors':[]}
        return fixtures.ExtensionTests.transport(self,name,q,token)

    def tick(self):self.queue.budget_recovery.tick()

    def due(self):
        with self.queue.db() as db:db.execute('UPDATE api_budget_recovery SET next_at=?',(self.queue.clock().isoformat(),))

    def test_fifty_percent_then_enable_have_separate_verified_records_and_no_replay(self):
        self.tick()
        self.assertEqual(self.operations,['update_task_budget','pause_control_task'])
        self.assertEqual(self.posts[0]['update_budget_infos'],[{'task_id':10001,'budget':150}])
        self.assertEqual(self.posts[1],{'advertiser_id':int(self.account['id']),'task_ids':[10001],'opt_type':'ENABLE'})
        records=self.queue.budget_recovery.records(self.account['id'])
        self.assertEqual({r['intent']['action'] for r in records},{'task_budget','resume_task'})
        self.assertTrue(all(r['state']=='verified' for r in records))
        self.due();self.tick();self.assertEqual(len(self.posts),2)
        self.assertTrue(all(not cfg['enabled'] for cfg in self.profile['features'].values()))

    def test_native_auto_resume_needs_no_enable_post(self):
        self.auto_resume=True;self.tick()
        self.assertEqual(self.operations,['update_task_budget']);self.assertEqual(self.task['task_status'],'PROCESSING')

    def test_roi_must_be_present_and_strictly_above_target(self):
        for roi in [3,2,None,float('nan')]:
            self.task['metrics']['total_prepay_and_pay_order_roi2_assist']=roi
            self.due();self.tick()
        self.assertEqual(self.posts,[])

    def test_net_target_uses_net_metric_and_never_payment_roi(self):
        self.plan.update(deep_external_action='AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI',target_roi_basis='net')
        self.task['metrics']['total_prepay_and_pay_settle_roi2_1h_assist']=2
        self.tick();self.assertEqual(self.posts,[])
        self.task['metrics']['total_prepay_and_pay_order_roi2_assist']=1
        self.task['metrics']['total_prepay_and_pay_settle_roi2_1h_assist']=4
        self.due();self.tick();self.assertEqual(len(self.posts),2)

    def test_manual_pause_other_offline_and_non_live_never_resume(self):
        for status in ['DISABLE','FROZEN','OFFLINE_TIME','OFFLINE_BALANCE','ROI2_DISABLE','DELETED']:
            self.task['task_status']=status;self.due();self.tick()
        self.account['scene']='product';self.task['task_status']='OFFLINE_BUDGET';self.due();self.tick()
        self.assertEqual(self.posts,[])

    def test_rechecks_roi_and_native_status_before_budget_post(self):
        real=self.queue._fresh
        def degrade(*args):
            self.task['metrics']['total_prepay_and_pay_order_roi2_assist']=2
            return real(*args)
        with patch.object(self.queue,'_fresh',side_effect=degrade):self.tick()
        self.assertEqual(self.posts,[])

    def test_unknown_applied_budget_is_read_back_then_only_enable_is_sent(self):
        self.fail_budget=True;self.tick()
        self.assertEqual(self.operations,['update_task_budget'])
        self.assertEqual(self.queue.status()[0]['state'],'unknown')
        self.due();self.tick()
        self.assertEqual(self.operations,['update_task_budget','pause_control_task'])
        self.assertTrue(all(r['state']=='verified' for r in self.queue.budget_recovery.records(self.account['id'])))

    def test_unknown_unapplied_budget_never_replays_or_enables(self):
        self.fail_budget=True;self.apply_unknown=False;self.tick()
        self.due();self.tick();self.due();self.tick()
        self.assertEqual(self.operations,['update_task_budget'])
        self.assertEqual(self.queue.status()[0]['state'],'unknown')

    def test_unknown_enable_is_resolved_by_reads_without_another_post(self):
        self.fail_resume=True;self.tick();self.assertEqual(len(self.posts),2)
        self.due();self.tick();self.assertEqual(len(self.posts),2)
        self.assertTrue(all(r['state']=='verified' for r in self.queue.budget_recovery.records(self.account['id'])))

    def test_stopped_lane_or_disabled_policy_never_writes(self):
        self.queue.rule_auto.lane_provider=lambda _:False;self.tick();self.assertEqual(self.posts,[])
        self.queue.rule_auto.lane_provider=lambda _:True
        self.queue.budget_recovery.path.write_text(json.dumps(POLICY),encoding='utf-8')
        self.due();self.tick();self.assertEqual(self.posts,[])

    def test_revocation_at_last_submission_gate_stops_post(self):
        real=self.queue._fresh
        def revoke(*args):
            result=real(*args)
            self.queue.budget_recovery.path.write_text(json.dumps(POLICY),encoding='utf-8')
            return result
        with patch.object(self.queue,'_fresh',side_effect=revoke):self.tick()
        self.assertEqual(self.posts,[])

    def test_cooldown_preserves_budget_then_allows_next_exhaustion_episode(self):
        self.tick();self.assertEqual(self.task['budget'],150)
        self.task['task_status']='OFFLINE_BUDGET';self.due();self.tick()
        self.assertEqual(len(self.posts),2)
        now=self.queue.clock();self.queue.clock=lambda:now+timedelta(minutes=6)
        self.plan['captured_at']=self.queue.clock().isoformat()
        with patch('monitor.official_workflow.now_cn',side_effect=self.queue.clock):self.tick()
        self.assertEqual(self.task['budget'],225);self.assertEqual(len(self.posts),4)

    def test_parent_budget_cap_and_manual_hold_block_financial_writes(self):
        self.plan['budget']=400;self.tick();self.assertEqual(self.posts,[])
        self.plan['budget']=10000
        with self.queue.db() as db:db.execute('INSERT INTO api_task_holds VALUES(?,?,?,?,?,?,?)',
            (self.account['id'],self.pid,'10001','manual',self.queue.clock().isoformat(),self.queue.clock().isoformat(),1))
        self.due();self.tick();self.assertEqual(self.posts,[])

    def test_queries_all_creation_dates_without_replacing_active_inventory(self):
        self.tick()
        queries=[q for name,q in self.calls if name=='control_tasks' and q.get('filtering',{}).get('task_status')=='OFFLINE_BUDGET']
        self.assertEqual({q['scene'] for q in queries},{'SMART_BOOST','MATERIAL_ADD_BUDGET'})
        self.assertTrue(all('create_start_time' not in q.get('filtering',{}) for q in queries))
        self.assertNotIn(self.account['id']+':'+self.pid,self.api.workflows)

    def test_public_payload_cannot_inject_recovery_authority_or_enable_action(self):
        with self.assertRaises(ValueError):self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'task_id':'10001','action':'resume_task'})
        with self.assertRaises(ValueError):self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'task_id':'10001','action':'task_budget','value':'150','recovery_authority':{'policy_digest':'fake'}})

    def test_full_recovered_budget_is_checked_against_existing_f5_cap(self):
        self.queue.auto.row=lambda _:dict(enabled=False,budget_cap='120')
        self.queue.auto.owned=lambda *_:{'10001'}
        self.tick();self.assertEqual(self.posts,[])
        self.assertIn('预算合计',str(self.queue.budget_recovery.status()['accounts'][0]['details']))

    def test_single_action_round_defers_enable_without_adding_budget_again(self):
        self.profile['safety']['max_actions_per_round']=1
        self.tick();self.assertEqual(self.operations,['update_task_budget'])
        self.due();self.tick();self.assertEqual(self.operations,['update_task_budget','pause_control_task'])
        self.assertEqual(self.task['budget'],150)

    def test_all_registered_live_accounts_are_discovered_without_hardcoded_ids(self):
        other={**self.account,'id':'8929253702525378'}
        self.queue.accounts=lambda:[self.account,other]
        self.api.grant_for_account=lambda _:'g'
        self.api.plan_readings[other['id']]=copy.deepcopy(self.api.plan_readings[self.account['id']])
        tasks={self.account['id']:self.task,other['id']:copy.deepcopy(self.task)}
        real=self.transport
        def routed(name,q,token=None):
            self.task=tasks[str(q['advertiser_id'])]
            return real(name,q,token)
        self.api.transport=routed;self.queue.writer=routed
        self.tick()
        self.assertEqual({str(q['advertiser_id']) for q in self.posts},{self.account['id'],other['id']})
        self.assertEqual(len(self.posts),4)

    def test_disabling_existing_account_auto_authority_also_stops_recovery(self):
        self.queue.rule_auto.configure({'account_id':self.account['id'],'plan_id':self.pid,
            'profile_revision':'r1','features':['f1','f2','f3','f4','f5','f6'],'enabled':True,'confirmed':True})
        self.queue.rule_auto.disable({'account_id':self.account['id']})
        self.tick();self.assertEqual(self.posts,[])

    def test_budget_exhausted_inventory_is_fully_paged(self):
        real=self.api.transport;page_numbers=[]
        def paged(name,q,token=None):
            if name=='control_tasks' and q['scene']=='MATERIAL_ADD_BUDGET' and q.get('filtering',{}).get('task_status')=='OFFLINE_BUDGET':
                page_numbers.append(q['page']);start=(q['page']-1)*q['page_size']
                rows=[]
                for n in range(start,min(start+q['page_size'],101)):
                    task=copy.deepcopy(self.task);task['id']=10001+n;task['metrics']['total_prepay_and_pay_order_roi2_assist']=2;rows.append(task)
                return {'task_list':rows,'page_info':{'total_number':101}}
            return real(name,q,token)
        self.api.transport=paged;self.tick()
        self.assertEqual(page_numbers,[1,2]);self.assertEqual(self.posts,[])
        self.assertEqual(self.queue.budget_recovery.status()['accounts'][0]['details']['scanned'],101)


if __name__=='__main__':unittest.main()
