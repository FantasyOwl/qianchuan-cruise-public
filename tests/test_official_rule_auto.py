import copy
import unittest
from datetime import timedelta
from tests import test_official_extensions as fixtures
from monitor.official_api import OfficialBusinessError
from unittest.mock import patch


class RuleAutoTests(unittest.TestCase):
    def test_missing_comprehensive_cost_routes_to_native_rules_without_changing_the_basis(self):
        from types import SimpleNamespace
        self.f1();self.profile['features']['f1']['cost_metric']='control_cost';self.authorize();requested=[]
        self.queue.hybrid=SimpleNamespace(mode=lambda aid:'api_preferred',active=lambda *a,**k:False,read_only_active=lambda aid:False)
        from monitor.hybrid_rules import HybridRules
        self.queue.hybrid_rules=HybridRules(self.queue);self.queue.hybrid_rules.request=lambda aid,**kw:requested.append((aid,kw)) or True
        with patch.object(self.queue.rule_auto,'candidates',return_value=([],[{'feature_id':'f1','rule_enabled':True,'reason':'缺少明确调控综合成本'}])):
            self.queue.rule_auto.tick()
        self.assertEqual(len(requested),1);self.assertIn('同一口径',requested[0][1]['reason'])
        self.assertEqual(self.profile['features']['f1']['cost_metric'],'control_cost')
        self.assertEqual(self.queue.rule_auto.row(self.account['id'])['state'],'waiting_browser');self.assertEqual(self.posts,[])

    def test_normal_unmatched_roi_and_api_only_never_request_native_fallback(self):
        from types import SimpleNamespace
        self.f1();self.authorize();requested=[]
        self.queue.hybrid=SimpleNamespace(mode=lambda aid:'api_only',active=lambda *a,**k:False,read_only_active=lambda aid:False)
        from monitor.hybrid_rules import HybridRules
        self.queue.hybrid_rules=HybridRules(self.queue);self.queue.hybrid_rules.request=lambda aid,**kw:requested.append(aid) or True
        with patch.object(self.queue.rule_auto,'candidates',return_value=([],[{'feature_id':'f1','rule_enabled':True,'reason':'缺少明确调控综合成本'}])):self.queue.rule_auto.tick()
        self.queue.hybrid.mode=lambda aid:'api_preferred'
        with self.queue.db() as db:db.execute('UPDATE api_rule_auto SET next_at=?',(self.queue.clock().isoformat(),))
        with patch.object(self.queue.rule_auto,'candidates',return_value=([],[{'feature_id':'f1','rule_enabled':True,'reason':'任务支付ROI未严格低于上限'}])):self.queue.rule_auto.tick()
        self.assertEqual(requested,[]);self.assertEqual(self.posts,[])

    tearDown=fixtures.ExtensionTests.tearDown
    transport=fixtures.ExtensionTests.transport

    def setUp(self):
        fixtures.ExtensionTests.setUp(self)
        for cfg in self.profile['features'].values():cfg['enabled']=False

    def authorize(self):
        return self.queue.rule_auto.configure({'account_id':self.account['id'],'plan_id':self.pid,
            'profile_revision':'r1','features':['f1','f2','f3','f4','f5','f6'],'enabled':True,'confirmed':True})

    def f1(self):self.profile['features']['f1'].update(enabled=True,cost_metric='control_spend',min_control_cost=40,max_roi=5)
    def f2(self):self.profile['features']['f2'].update(enabled=True,rules=[{'cost_metric':'control_spend','min_control_cost':40,'max_control_cost':100,'min_roi':3,'target_control_budget':150,'cooldown_minutes':5}])

    def test_authorization_does_not_enable_disabled_rules_or_fill_blank_f5_limits(self):
        profile=copy.deepcopy(self.profile);self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(self.posts,[]);self.assertEqual(self.profile,profile);self.assertEqual(self.queue.auto.status(),[])
        self.assertTrue(self.queue.rule_auto.status()[0]['enabled'])
        self.assertEqual(len(self.queue.rule_auto.status()[0]['diagnostics']),6)

    def test_f1_automatically_pauses_exact_task_and_independently_verifies_once(self):
        self.f1();self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.posts[0]['task_ids'],[10001])
        self.assertEqual(self.queue.status()[0]['state'],'verified')
        self.assertEqual(self.queue.status()[0]['intent']['rule_check']['feature_id'],'f1')
        self.queue.rule_auto.tick();self.assertEqual(len(self.posts),1)

    def test_missing_comprehensive_cost_remains_missing_after_authorization(self):
        self.f1();self.profile['features']['f1']['cost_metric']='control_cost'
        self.authorize();self.queue.rule_auto.tick();self.assertEqual(self.posts,[])
        self.assertIn('缺少明确调控综合成本',str(self.queue.rule_auto.status()[0]['diagnostics']))

    def test_f2_automatically_applies_exact_absolute_budget(self):
        self.f2();self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.posts[0]['update_budget_infos'],[{'task_id':10001,'budget':150}])
        self.assertEqual(self.queue.status()[0]['state'],'verified')

    def test_f2_cannot_raise_program_task_above_existing_f5_budget_cap(self):
        self.f2();self.authorize()
        self.queue.auto.row=lambda _:dict(enabled=False,budget_cap='120')
        self.queue.auto.owned=lambda *_:{'10001'}
        self.queue.rule_auto.tick();self.assertEqual(self.posts,[])
        self.assertIn('预算合计',str(self.queue.rule_auto.status()[0]['diagnostics']))

    def test_f3_automatically_applies_budget_and_duration_separately(self):
        self.profile['features']['f3'].update(enabled=True,rules=[{'cost_metric':'control_spend','min_control_cost':40,'max_control_cost':100,'min_roi':3,'target_control_budget':150,'increase_control_duration':1,'cooldown_minutes':5}])
        real=self.api.transport
        def volume(name,q,token=None):
            value=real(name,q,token)
            if name=='control_tasks':
                for t in value['task_list']:t['smart_bid_type']='SMART_BID_CONSERVATIVE'
            return value
        self.api.transport=volume;self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(self.posts[0]['update_budget_infos'][0]['budget'],150)
        self.assertEqual(self.posts[1]['update_duration_infos'][0]['duration'],3)
        self.assertTrue(all(r['state']=='verified' for r in self.queue.status()))

    def test_f4_pauses_only_when_native_runtime_and_selected_metric_match(self):
        self.profile['features']['f4'].update(enabled=True,cost_metric='control_spend',control_cost_equals=50,min_runtime_minutes=30)
        real=self.api.transport
        def volume(name,q,token=None):
            value=real(name,q,token)
            if name=='control_tasks':
                for t in value['task_list']:t.update(smart_bid_type='SMART_BID_CONSERVATIVE',create_time=(self.queue.clock()-timedelta(minutes=31)).isoformat())
            return value
        self.api.transport=volume;self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.posts[0]['task_ids'],[10001])

    def f6(self):
        self.plan['target_roi']=3
        self.profile['features']['f6'].update(enabled=True,min_overall_roi=3,min_15m_roi=3,min_30m_roi=3,max_online_users=30,target_roi_delta=-.1)
        now=self.queue.clock()
        return {'account_id':self.account['id'],'plan_id':self.pid,'source':'official_api','metric_basis':'overall_payment',
            'metric_date':now.date().isoformat(),'captured_at':now.isoformat(),'metrics':{'roi':4,'roi_15m':4,'roi_30m':4,'online_users':20},
            'windows':{k:{'start_at':(now-timedelta(minutes=m)).isoformat(),'end_at':now.isoformat()} for k,m in (('roi_15m',15),('roi_30m',30))}}

    def test_f6_missing_live_metrics_does_not_fall_back_to_daily_roi(self):
        self.f6();self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(self.posts,[]);self.assertIn('近15/30分钟ROI',str(self.queue.rule_auto.status()[0]['diagnostics']))

    def test_f6_uses_same_room_verified_windows_and_rechecks_before_native_roi_write(self):
        value=self.f6();self.queue.rule_auto.live_provider=lambda *_:value
        self.authorize();self.queue.rule_auto.tick();self.assertEqual(len(self.posts),1)
        self.assertEqual(self.posts[0]['update_roi2_infos'][0]['roi2_goal'],2.9)
        self.assertEqual(self.queue.status()[0]['state'],'verified')

    def test_f6_rejects_foreign_room_or_wrong_time_window(self):
        value=self.f6();value['windows']['roi_15m']['start_at']=value['captured_at'];self.queue.rule_auto.live_provider=lambda *_:value
        self.authorize();self.queue.rule_auto.tick();self.assertEqual(self.posts,[])
        self.assertIn('窗口',str(self.queue.rule_auto.status()[0]['diagnostics']))

    def test_disable_during_preflight_prevents_actual_write(self):
        self.f1();self.authorize();real=self.queue._fresh
        def disable(*args):
            self.queue.rule_auto.disable({'account_id':self.account['id']});return real(*args)
        self.queue._fresh=disable;self.queue.rule_auto.tick()
        self.assertEqual(self.posts,[]);self.assertFalse(self.queue.rule_auto.status()[0]['enabled'])
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')

    def test_changed_rule_or_stopped_lane_never_dispatches(self):
        self.f1();self.authorize();self.queue.rule_auto.lane_provider=lambda _:False;self.queue.rule_auto.tick()
        self.assertEqual(self.posts,[]);self.assertEqual(self.queue.rule_auto.status()[0]['state'],'idle')
        self.profile['features']['f1']['max_roi']=6
        now=self.queue.clock();self.queue.clock=lambda:now+timedelta(minutes=6);self.queue.rule_auto.tick()
        self.assertFalse(self.queue.rule_auto.status()[0]['enabled']);self.assertEqual(self.posts,[])

    def test_unknown_rule_post_stops_without_retry(self):
        self.f1();self.authorize()
        def fail(name,q,token):self.posts.append(copy.deepcopy(q));raise OSError('uncertain')
        self.queue.writer=fail;self.queue.rule_auto.tick();self.assertEqual(len(self.posts),1)
        self.assertFalse(self.queue.rule_auto.status()[0]['enabled']);self.assertEqual(self.queue.status()[0]['state'],'unknown')
        self.queue.rule_auto.tick();self.assertEqual(len(self.posts),1)

    def test_native_quota_rejection_preserves_authority_for_fresh_next_round(self):
        self.f1();self.authorize();real=self.queue.writer
        def limited(name,q,token):
            self.posts.append(copy.deepcopy(q))
            raise OfficialBusinessError(40100,operation=name,reason='rate_limited',retry_after=40)
        self.queue.writer=limited;self.queue.rule_auto.tick()
        self.assertTrue(self.queue.rule_auto.row(self.account['id'])['enabled'])
        self.assertEqual(self.queue.rule_auto.row(self.account['id'])['state'],'waiting')
        self.assertEqual(self.queue.status()[0]['state'],'rejected')
        self.assertEqual(len(self.posts),1)
        with self.queue.db() as db:db.execute("UPDATE api_rule_auto SET next_at=?",(self.queue.clock().isoformat(),))
        self.queue.writer=real;self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),2);self.assertEqual(self.queue.status()[0]['state'],'verified')

    def test_local_write_admission_wait_never_disables_or_claims_a_post(self):
        self.f1();self.authorize()
        def limited(name,q,token):raise OfficialBusinessError(40100,operation=name,reason='rate_limited',retry_after=20,origin='local_cooldown')
        self.queue.writer=limited;self.queue.rule_auto.tick()
        self.assertTrue(self.queue.rule_auto.row(self.account['id'])['enabled'])
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        self.assertFalse(self.queue.status()[0]['result']['post_sent']);self.assertEqual(self.posts,[])

    def timed_pause(self,applied):
        self.f1();self.authorize();real=self.api.transport
        def reads(name,q,token=None):
            if name=='control_tasks' and q.get('filtering',{}).get('task_status')=='PROCESSING' and self.paused_task:
                return {'task_list':[],'page_info':{'total_number':0}}
            return real(name,q,token)
        def timed(name,q,token):
            self.posts.append(copy.deepcopy(q));self.paused_task=applied
            raise OfficialBusinessError(51010,operation=name,reason='service_busy',hint='Internal service timed out.')
        self.api.transport=reads;self.queue.writer=timed;self.queue.rule_auto.tick()
        return self.queue.status()[0]

    def test_timed_pause_applied_is_resolved_by_reads_without_another_post(self):
        record=self.timed_pause(True)
        self.assertEqual(record['state'],'unknown');self.assertTrue(record['result']['submission_uncertain'])
        self.assertTrue(self.queue.rule_auto.row(self.account['id'])['enabled'])
        with self.queue.db() as db:db.execute("UPDATE api_rule_auto SET next_at=?",(self.queue.clock().isoformat(),))
        self.queue.rule_auto.tick()
        self.assertEqual(self.queue.status()[0]['state'],'verified');self.assertEqual(len(self.posts),1)
        self.assertTrue(self.queue.status()[0]['result']['no_resubmission'])

    def test_timed_pause_still_active_requires_settle_time_and_fresh_rule_before_new_intent(self):
        record=self.timed_pause(False)
        with self.assertRaisesRegex(ValueError,'状态尚未稳定'):self.queue.recheck({'id':record['id']})
        with self.queue.db() as db:
            db.execute("UPDATE api_actions SET updated_at=? WHERE id=?",((self.queue.clock()-timedelta(seconds=61)).isoformat(),record['id']))
            db.execute("UPDATE api_rule_auto SET next_at=?",(self.queue.clock().isoformat(),))
        self.queue.writer=self.transport;self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),2)
        rows=self.queue.status();self.assertEqual(rows[0]['state'],'verified');self.assertNotEqual(rows[0]['id'],record['id'])
        self.assertEqual(rows[1]['result']['observed_status'],'PROCESSING')

    def test_timed_pause_readback_limited_keeps_uncertainty_and_permission(self):
        record=self.timed_pause(False)
        def blocked(*args,**kwargs):raise OfficialBusinessError(40100,operation='control_tasks',reason='rate_limited',retry_after=40)
        with self.queue.db() as db:db.execute("UPDATE api_rule_auto SET next_at=?",(self.queue.clock().isoformat(),))
        with patch.object(self.queue,'_reconcile_timed_task',side_effect=blocked):self.queue.rule_auto.tick()
        self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertEqual(len(self.posts),1)
        self.assertTrue(self.queue.rule_auto.row(self.account['id'])['enabled'])

    def test_timed_create_is_never_resolved_by_pause_readback_or_replayed(self):
        self.profile['features']['f5'].update(enabled=True)
        value=self.api.scan_workflow(self.account,self.profile,self.pid,materials=True)
        record=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'material_chase','batch_key':value['batches'][0]['key']})
        def timed(name,q,token):
            self.posts.append(copy.deepcopy(q));raise OfficialBusinessError(51010,operation=name,reason='service_busy')
        self.queue.writer=timed
        out=self.queue.confirm({'id':record['id'],'intent_digest':record['intent_digest'],'confirmed':True})
        self.assertEqual(out['state'],'unknown')
        with self.assertRaises(ValueError):self.queue.recheck({'id':record['id']})
        self.assertEqual(len(self.posts),1)

    def timed_budget(self):
        self.f2()
        self.authorize()
        def timed(operation,body,token):
            self.posts.append(copy.deepcopy(body))
            raise OfficialBusinessError(51010,operation=operation,reason='service_busy',request_id='budget-timeout')
        self.queue.writer=timed
        self.queue.rule_auto.tick()
        record=self.queue.status()[0]
        self.assertEqual(record['state'],'unknown')
        self.assertEqual(record['intent']['action'],'task_budget')
        return record

    def test_old_timed_budget_recovers_from_deleted_task_after_rule_authority_renewal(self):
        record=self.timed_budget()
        original=self.queue.clock()-timedelta(days=2)
        with self.queue.db() as db:
            db.execute('UPDATE api_actions SET created_at=?,updated_at=? WHERE id=?',
                (original.isoformat(),original.isoformat(),record['id']))
        self.authorize()
        reads=[]
        native=self.api.transport
        def readback(name,query,token=None):
            if name!='control_tasks' or query.get('filtering',{}).get('search_keyword')!='10001':
                return native(name,query,token)
            reads.append(query)
            rows=[]
            if query['filtering'].get('task_status')=='DELETED':
                rows=[{'id':10001,'task_status':'DELETED','budget':150}]
            return {'task_list':rows,'page_info':{'total_number':len(rows)}}
        self.api.transport=readback
        with patch.object(self.queue.rule_auto,'candidates',return_value=([],[])):
            self.queue.rule_auto.tick()
        checked=self.queue.status()[0]
        self.assertEqual(checked['id'],record['id'])
        self.assertEqual(checked['state'],'verified')
        self.assertEqual(checked['result']['request_id'],'budget-timeout')
        self.assertEqual(checked['result']['observed_budget'],'150')
        self.assertEqual(checked['result']['observed_status'],'DELETED')
        self.assertEqual(reads[-1]['start_time'],original.date().isoformat()+' 00:00:00')
        self.assertEqual(reads[-1]['ad_id'],int(self.pid))
        self.assertEqual(self.queue.rule_auto.status()[0]['state'],'watching')
        self.assertEqual(len(self.posts),1)

    def test_timed_budget_only_releases_unapplied_amount_after_stabilizing(self):
        record=self.timed_budget()
        self.task_budget=125
        with self.assertRaisesRegex(ValueError,'预算为125元'):
            self.queue.recheck({'id':record['id']})
        self.task_budget=100
        with self.assertRaisesRegex(ValueError,'预算为100元'):
            self.queue.recheck({'id':record['id']})
        self.assertEqual(self.queue.status()[0]['state'],'unknown')
        with self.queue.db() as db:
            db.execute('UPDATE api_actions SET updated_at=? WHERE id=?',
                ((self.queue.clock()-timedelta(seconds=61)).isoformat(),record['id']))
        checked=self.queue.recheck({'id':record['id']})
        self.assertEqual(checked['state'],'rejected')
        self.assertEqual(checked['result']['observed_budget'],'100')
        self.assertEqual(len(self.posts),1)

    def complete_detail(self,**changes):
        real=self.api.transport
        def reads(name,q,token=None):
            value=real(name,q,token)
            if name=='plan_detail':
                value.update(adlab_scene=1,status='DELIVERY_OK',delivery_setting={
                    'smart_bid_type':self.plan['smart_bid_type'],'budget':self.plan['budget'],
                    'deep_external_action':self.plan['deep_external_action'],'roi2_goal':self.plan.get('target_roi',3)})
                value.update(changes)
            return value
        self.api.transport=reads

    def test_complete_documented_numeric_detail_avoids_plan_list_and_still_verifies_pause(self):
        self.f1();self.authorize();self.complete_detail()
        def unavailable(*args,**kwargs):raise AssertionError('task pause must not require whole-family list')
        self.api.read_plans=unavailable;self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.queue.status()[0]['state'],'verified')

    def test_numeric_detail_cannot_cross_entry_or_bypass_changed_parent_budget(self):
        for changes in [{'adlab_scene':0},{'delivery_setting':{'smart_bid_type':'SMART_BID_CUSTOM','budget':9000,'deep_external_action':self.plan['deep_external_action'],'roi2_goal':3}}]:
            with self.subTest(changes=changes):
                self.f1();self.authorize();old=self.api.transport;self.complete_detail(**changes)
                self.queue.rule_auto.tick();self.assertEqual(self.posts,[])
                self.api.transport=old

    def test_missing_native_setting_still_uses_fresh_list_instead_of_cached_approval(self):
        self.f1();self.authorize();self.complete_detail(delivery_setting={'budget':10000})
        calls=[];real=self.api.read_plans
        def lists(*args,**kwargs):calls.append(kwargs);return real(*args,**kwargs)
        self.api.read_plans=lists;self.queue.rule_auto.tick()
        self.assertEqual(len(calls),1);self.assertEqual(len(self.posts),1)

    def test_preflight_quota_wait_is_not_reported_as_rule_watch_and_actual_changes_are_logged(self):
        self.f1();self.authorize();real=self.queue._fresh;events=[]
        self.queue.auto.audit_writer=lambda aid,event,detail:events.append((event,detail))
        def blocked(*args):raise OfficialBusinessError(40100,operation='plan_detail',reason='rate_limited',retry_after=40)
        self.queue._fresh=blocked;self.queue.rule_auto.tick()
        self.assertEqual(self.queue.rule_auto.row(self.account['id'])['state'],'waiting');self.assertEqual(self.posts,[])
        self.queue._fresh=real
        with self.queue.db() as db:db.execute("UPDATE api_rule_auto SET next_at=?",(self.queue.clock().isoformat(),))
        self.queue.rule_auto.tick();self.assertEqual(events[-1][1]['ad_changes'],1)

    def test_public_payload_cannot_inject_rule_authority(self):
        with self.assertRaises(ValueError):self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'pause_task','task_id':'10001','rule_auto_authority':{'grant_id':'fake'}})
        self.assertEqual(self.posts,[])

    def test_unsubmitted_automatic_draft_can_be_cancelled_and_re_evaluated_after_restart(self):
        self.f1();self.authorize();proposals,_=self.queue.rule_auto.candidates(self.account,self.profile,self.pid)
        p=proposals[0];old=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'pause_task','task_id':'10001'},
            rule_check={k:p[k] for k in ('feature_id','entity_id','action','before','after')},
            rule_auto_authority={'grant_id':self.queue.rule_auto.row(self.account['id'])['grant_id']})
        self.queue.rule_auto.tick();self.assertEqual(len(self.posts),1)
        self.assertEqual(next(r for r in self.queue.status() if r['id']==old['id'])['state'],'cancelled')

    def test_permission_can_be_granted_while_human_draft_waits_without_approving_it(self):
        self.f1();human=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'pause_plan'})
        self.authorize();self.queue.rule_auto.tick();self.assertEqual(self.posts,[])
        self.assertEqual(next(r for r in self.queue.status() if r['id']==human['id'])['state'],'draft')
        self.assertEqual(self.queue.rule_auto.status()[0]['state'],'waiting')

    def low_spend_rule(self,*,grow=False):
        self.profile['features']['f4'].update(enabled=True,pause_rule='low_spend',low_spend_hours=2,low_spend_max=20,low_spend_scope='both')
        created=(self.queue.clock()-timedelta(hours=27)).strftime('%Y-%m-%d %H:%M:%S')
        real=self.api.transport;reads=[]
        def observed(name,q,token=None):
            value=real(name,q,token)
            if name=='control_tasks':
                for t in value['task_list']:
                    t['create_time']=created;t['metrics']={'stat_cost_for_roi2_assist':0}
                if q.get('fields')==['stat_cost_for_roi2_assist']:
                    reads.append(q)
                    for t in value['task_list']:t['metrics']={'stat_cost_for_roi2_assist':25 if grow and len(reads)>1 else 19}
            return value
        self.api.transport=observed
        return reads,created

    def test_low_spend_pause_reads_creation_to_now_and_closes_exact_task_automatically(self):
        reads,created=self.low_spend_rule();self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.posts[0]['task_ids'],[10001])
        self.assertTrue(len(reads)>=2);self.assertEqual(reads[0]['start_time'],created[:10]+' 00:00:00')
        self.assertEqual(self.queue.status()[0]['state'],'verified')

    def test_low_spend_threshold_is_rechecked_before_pause(self):
        self.low_spend_rule(grow=True);self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(self.posts,[]);self.assertEqual(self.queue.status()[0]['state'],'cancelled')

    def test_f4_reads_only_today_and_rechecks_roi_before_sending_pause(self):
        at=self.queue.clock().replace(hour=12,minute=0,second=0,microsecond=0)
        self.queue.clock=lambda:at
        self.plan['captured_at']=at.isoformat()
        created=(at-timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        self.profile['features']['f4'].update(enabled=True,pause_rule='daily_spend_roi',
            daily_runtime_hours=2,daily_spend_max=20,daily_roi_max=1.5)
        original=self.api.transport;reads=[]
        def observed(name,q,token=None):
            value=original(name,q,token)
            if name=='control_tasks' and q['scene']=='MATERIAL_ADD_BUDGET':
                self.assertEqual(q['start_time'],at.strftime('%Y-%m-%d 00:00:00'))
                self.assertIn('total_prepay_and_pay_order_roi2_assist',q['fields'])
                reads.append(q)
                for t in value['task_list']:
                    t['create_time']=created
                    t['metrics']={'stat_cost_for_roi2_assist':19,'total_prepay_and_pay_order_roi2_assist':1 if len(reads)==1 else 1.5}
            return value
        self.api.transport=observed
        with patch('monitor.official_workflow.now_cn',return_value=at):
            self.authorize();self.queue.rule_auto.tick()
        self.assertEqual(len(reads),2)
        self.assertEqual(self.posts,[])
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')

    def test_explicit_save_update_rotates_existing_authority_without_enabling_inactive_channels(self):
        self.f1();self.authorize();old=self.queue.rule_auto.row(self.account['id'])['grant_id']
        self.profile['features']['f1']['max_roi']=6
        changed=self.queue.refresh_automatic_profile(self.account['id'])
        self.assertEqual(changed,['api_rule_auto']);self.assertEqual(self.queue.auto.status(),[])
        self.assertNotEqual(self.queue.rule_auto.row(self.account['id'])['grant_id'],old)
        self.queue.rule_auto.tick();self.assertEqual(len(self.posts),1)
