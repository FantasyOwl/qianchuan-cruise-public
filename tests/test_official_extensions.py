import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch
from tests import test_official_workflow as fixtures
from monitor.official_api import OfficialTransport,OfficialBusinessError


class ExtensionTests(unittest.TestCase):
    setUp=fixtures.WorkflowTests.setUp
    tearDown=fixtures.WorkflowTests.tearDown
    scanned=fixtures.WorkflowTests.scanned
    prepared=fixtures.WorkflowTests.prepared
    confirm=fixtures.WorkflowTests.confirm

    def transport(self,name,q,token=None):
        if name=='create_control_task':
            self.posts.append(copy.deepcopy(q))
            if self.fail_post:raise OSError('uncertain')
            self.created={'id':10000001,'name':q['name'],'budget':q['budget'],'bid':q.get('bid'),
                'roi2_goal':q.get('roi2_goal'),'duration':q.get('duration',87672),'task_status':'PROCESSING',
                'smart_bid_type':q.get('smart_bid_type'),'external_action_name':'直播间支付',
                'deep_external_action_name':{'AD_CONVERT_TYPE_LIVE_PAY_ROI':'支付ROI','AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI':'净成交ROI'}.get(q.get('deep_external_action')),
                'material_list':[{'material_id':m} for m in q.get('material_ids',[])]}
            return {'id':10000001}
        if name in {'update_task_budget','update_task_duration','update_plan_budget','update_plan_roi','create_smart_control','stop_smart_control'}:
            self.posts.append(copy.deepcopy(q))
            if name=='update_task_budget':self.task_budget=q['update_budget_infos'][0]['budget'];return {'results':[{'task_id':10001,'status':True}]}
            if name=='update_task_duration':self.task_duration=q['update_duration_infos'][0]['duration'];return {'results':{'task_ids':[10001],'errors':[]}}
            if name=='update_plan_budget':self.plan['budget']=q['update_budget_infos'][0]['budget'];return {'results':[{'ad_id':int(self.pid),'status':'SUCCESS'}]}
            if name=='update_plan_roi':self.plan['target_roi']=q['update_roi2_infos'][0]['roi2_goal'];return {'results':[{'ad_id':int(self.pid),'status':'SUCCESS'}]}
            return {}
        if name=='control_tasks':
            keyword=q.get('filtering',{}).get('search_keyword')
            if keyword=='10001':return {'task_list':[{'id':10001,'task_status':'DISABLE' if self.paused_task else 'PROCESSING',
                'budget':getattr(self,'task_budget',100),'duration':getattr(self,'task_duration',2),
                'smart_bid_type':'SMART_BID_CUSTOM','roi2_goal':2.5,'bid':None,'deep_external_action_name':'净成交ROI',
                'external_action_name':'直播间支付','material_list':[{'material_id':int(self.materials[0][0])}],
                'metrics':{'stat_cost_for_roi2_assist':50,'total_prepay_and_pay_order_roi2_assist':4}}],'page_info':{'total_number':1}}
            if keyword=='10000001':return {'task_list':[copy.deepcopy(self.created)],'page_info':{'total_number':1}}
            value=fixtures.WorkflowTests.transport(self,name,q,token)
            for t in value['task_list']:t.update(smart_bid_type='SMART_BID_CUSTOM',bid=None,roi2_goal=2.5,
                deep_external_action_name='净成交ROI',external_action_name='直播间支付',duration=getattr(self,'task_duration',2),budget=getattr(self,'task_budget',t['budget']))
            return value
        if name=='pause_control_task' and q['task_ids']==[10000001]:
            self.posts.append(copy.deepcopy(q));self.created['task_status']='DISABLE';return {'task_ids':[10000001],'errors':[]}
        return fixtures.WorkflowTests.transport(self,name,q,token)

    def prepare(self,action,**fields):
        return self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':action,**fields})

    def test_legacy_roi_uses_only_unique_native_goal_and_never_yuan_bid(self):
        rule=self.profile['features']['f5']['rules'][0];rule.update(optimization_target='综合ROI',bid_value=2.5)
        value=self.scanned();batch=value['batches'][0]
        self.assertTrue(batch['ready']);self.assertEqual(batch['native_contract']['goal_label'],'综合营销ROI')
        r=self.prepare('material_chase',batch_key=batch['key']);body=r['intent']['body']
        self.assertEqual(body['roi2_goal'],2.5);self.assertNotIn('bid',body)
        self.assertEqual(body['deep_external_action'],'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI');self.assertEqual(self.posts,[])
        self.assertEqual(self.confirm(r)['state'],'verified');self.assertEqual(len(self.posts),1)

    def test_stale_plan_refreshes_before_scanning_instead_of_requesting_manual_refresh(self):
        from monitor.schema import now_cn
        self.plan['captured_at']=(now_cn()-timedelta(hours=1)).isoformat();calls=[]
        def refresh(*args,**kwargs):
            calls.append(1);self.plan['captured_at']=now_cn().isoformat()
            return {'ok':True}
        self.api.read_plans=refresh
        self.assertTrue(self.scanned()['batches'][0]['ready']);self.assertEqual(calls,[1]);self.assertEqual(self.posts,[])

    def test_auto_log_counts_material_batches_separately_from_task_rule_candidates(self):
        from monitor.cruise_api import CruiseApp
        from types import SimpleNamespace
        self.api.scan_workflow=lambda *args,**kwargs:self.scanned()
        result=CruiseApp.official_scan(SimpleNamespace(official_api=self.api),self.account,self.profile,'auto')
        self.assertEqual(result['material_batches'],1);self.assertEqual(result['materials_pending_confirmation'],2)
        self.assertEqual(result['material_pages_read'],1);self.assertEqual(result['material_rows_scanned'],3)
        self.assertIn('命中1批共2条待确认',result['summary']);self.assertNotIn('生成0条规则候选',result['summary'])

    def test_explicit_payment_roi_creates_and_reads_its_own_basis(self):
        self.profile['features']['f5']['rules'][0].update(optimization_target='支付ROI',bid_value=3.2)
        r=self.prepared();self.assertEqual(r['intent']['body']['deep_external_action'],'AD_CONVERT_TYPE_LIVE_PAY_ROI')
        self.assertEqual(self.confirm(r)['state'],'verified')

    def test_volume_chase_registers_only_its_created_id_for_confirmed_stop(self):
        self.profile['features']['f5']['rules'][0].update(chase_type='放量追投',total_budget=200,control_duration=2)
        r=self.prepared();self.assertNotIn('duration',r['intent']['body']);self.assertEqual(self.queue.followups(),[])
        out=self.confirm(r);self.assertEqual(out['state'],'verified');self.assertEqual(self.queue.followups()[0]['task_id'],'10000001')
        due=self.queue.followups()[0]['due_at'];from monitor.schema import parse_time
        self.queue.clock=lambda:parse_time(due)+timedelta(seconds=1)
        self.queue.tick_followups();self.assertEqual(self.queue.followups()[0]['state'],'verified')
        self.assertEqual(self.posts[-1]['task_ids'],[10000001]);self.assertEqual(len(self.posts),2)
        self.queue.tick_followups();self.assertEqual(len(self.posts),2)

    def test_task_budget_and_duration_are_absolute_separate_confirmations(self):
        self.scanned();r=self.prepare('task_budget',task_id='10001',value=150)
        self.assertEqual(self.posts,[]);self.assertEqual(self.confirm(r)['state'],'verified')
        self.scanned();r=self.prepare('task_duration',task_id='10001',value=3)
        self.assertEqual(r['intent']['body']['update_duration_infos'][0]['duration'],3)
        self.assertEqual(self.confirm(r)['state'],'verified')

    def test_main_budget_roi_both_verify_native_values_and_preserve_basis(self):
        self.plan['target_roi']=3
        r=self.prepare('plan_budget',value=11000);self.assertEqual(self.confirm(r)['state'],'verified')
        # Use a different plan/account period to avoid the intentional one-hour main-plan cooldown.
        with self.queue.db() as db:db.execute('DELETE FROM api_actions')
        r=self.prepare('plan_roi',value=3.3)
        self.assertEqual(r['intent']['body']['update_roi2_infos'][0]['deep_external_action'],self.plan['deep_external_action'])
        self.assertEqual(self.confirm(r)['state'],'verified')

    def test_slowdown_is_confirmed_in_advance_and_labels_acknowledgement_honestly(self):
        now=self.queue.clock();off=now+timedelta(hours=2)
        r=self.prepare('slowdown_schedule',off_at=off.isoformat(),duration_minutes=30,value=200)
        self.assertEqual(self.posts,[]);out=self.confirm(r);self.assertEqual(out['state'],'scheduled');self.assertEqual(self.posts,[])
        self.queue.clock=lambda:off-timedelta(minutes=20)
        self.queue.schedules.tick();self.assertEqual(self.queue.schedules.status()[0]['state'],'active_acknowledged')
        self.assertFalse(self.queue.schedules.status()[0]['result']['independently_verified'])
        self.queue.clock=lambda:off+timedelta(minutes=10)
        self.queue.schedules.tick();self.assertEqual(self.queue.schedules.status()[0]['state'],'completed_acknowledged')
        self.assertEqual(len(self.posts),2);self.queue.schedules.tick();self.assertEqual(len(self.posts),2)

    def test_slowdown_missed_window_never_creates_or_stops(self):
        off=self.queue.clock()+timedelta(hours=2)
        r=self.prepare('slowdown_schedule',off_at=off.isoformat(),duration_minutes=30,value=200);self.confirm(r)
        self.queue.clock=lambda:off
        self.queue.schedules.tick();self.assertEqual(self.queue.schedules.status()[0]['state'],'missed');self.assertEqual(self.posts,[])

    def test_confirmed_future_schedule_can_be_cancelled_without_ad_write(self):
        off=self.queue.clock()+timedelta(hours=2)
        r=self.prepare('slowdown_schedule',off_at=off.isoformat(),duration_minutes=30,value=200);self.confirm(r)
        self.queue.cancel({'id':r['id']});self.assertEqual(self.queue.schedules.status()[0]['state'],'cancelled')
        self.assertEqual(self.posts,[])

    def test_boost_is_one_native_task_with_budget_duration_goal_readback(self):
        r=self.prepare('boost',value=200,duration=2)
        self.assertEqual(self.posts,[]);self.assertEqual(self.confirm(r)['state'],'verified')
        self.assertEqual(self.posts[0]['scene'],'SMART_BOOST');self.assertNotIn('material_ids',self.posts[0])

    def test_changed_task_budget_rejects_original_confirmation_before_post(self):
        self.scanned();r=self.prepare('task_budget',task_id='10001',value=150);self.task_budget=120
        with self.assertRaisesRegex(ValueError,'已变化'):self.confirm(r)
        self.assertEqual(self.posts,[])

    def test_main_plan_budget_and_roi_percentage_limits_apply_to_manual_api(self):
        self.plan['target_roi']=3
        with self.assertRaisesRegex(ValueError,'20%'):self.prepare('plan_budget',value=12500)
        with self.assertRaisesRegex(ValueError,'20%'):self.prepare('plan_roi',value=3.8)
        self.assertEqual(self.posts,[])

    def test_slowdown_preserves_existing_boost_instead_of_pausing_it(self):
        off=self.queue.clock()+timedelta(hours=2)
        r=self.prepare('slowdown_schedule',off_at=off.isoformat(),duration_minutes=30,value=200);self.confirm(r)
        real=self.api.transport
        def existing_boost(name,q,token=None):
            if name=='control_tasks' and q['scene']=='SMART_BOOST':return {'task_list':[{'id':500,'task_status':'PROCESSING','budget':200,'material_list':None}],'page_info':{'total_number':1}}
            return real(name,q,token)
        self.api.transport=existing_boost;self.queue.clock=lambda:off-timedelta(minutes=20)
        self.queue.schedules.tick();self.assertEqual(self.queue.schedules.status()[0]['state'],'blocked');self.assertEqual(self.posts,[])

    def test_slowdown_network_uncertainty_is_held_and_never_reposted(self):
        off=self.queue.clock()+timedelta(hours=2)
        r=self.prepare('slowdown_schedule',off_at=off.isoformat(),duration_minutes=30,value=200);self.confirm(r)
        calls=[]
        def lost(*args):calls.append(1);raise OSError('response lost')
        self.queue.writer=lost;self.queue.clock=lambda:off-timedelta(minutes=20)
        self.queue.schedules.tick();self.queue.schedules.tick()
        self.assertEqual(len(calls),1);self.assertEqual(self.queue.schedules.status()[0]['state'],'unknown')


class RateLimitTests(unittest.TestCase):
    def test_exhausted_rate_limit_has_operation_and_shared_cooldown(self):
        now=[100.0];calls=[];limited=[True]
        def sleep(n):now[0]+=n
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return json.dumps({'code':40100,'message':'请求频率超限 secret-token'} if limited[0] else {'code':0,'data':{}}).encode()
        class Opener:
            def open(self,*args,**kwargs):calls.append(1);return Response()
        t=OfficialTransport(monotonic=lambda:now[0],sleeper=sleep)
        with patch('monitor.official_api.urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(OfficialBusinessError) as caught:t('plan_materials',{},'paper-token')
            self.assertEqual(caught.exception.reason,'rate_limited');self.assertEqual(caught.exception.operation,'plan_materials')
            self.assertNotIn('secret-token',str(caught.exception));self.assertEqual(len(calls),1)
            with self.assertRaises(OfficialBusinessError):t('plan_materials',{},'paper-token')
            self.assertEqual(len(calls),1)
            now[0]+=61;limited[0]=False;t('plan_materials',{},'paper-token');self.assertEqual(len(calls),2)
