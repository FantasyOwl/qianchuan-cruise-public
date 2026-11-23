import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from tests.test_official_rule_auto import RuleAutoTests
from monitor.hybrid_chase import HybridChase
from monitor.hybrid_rules import HybridRules
from monitor.official_api import OfficialBusinessError
from monitor.schema import now_cn


class HybridRuleTests(unittest.TestCase):
    def test_midnight_releases_old_read_cycle_but_preserves_original_unknown_writes(self):
        self.at=self.at.replace(hour=12,minute=0,second=0)
        self.f1();self.start();self.at+=timedelta(days=1);self.bridge.tick()
        self.assertEqual(self.bridge.status()[0]['state'],'cancelled');self.assertEqual(self.queue.status(),[])
        self.assertEqual(self.posts,[])

    def test_new_saved_revision_releases_an_old_unsubmitted_read_cycle(self):
        self.f1();c=self.start()
        original=self.queue.profile
        self.queue.profile=lambda aid:(original(aid)[0],'new-revision')
        self.bridge.tick();self.assertEqual(self.bridge.status()[0]['state'],'cancelled')
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_native_quota_shortage_skips_budget_addition_without_disabling_grant(self):
        self.f2();c=self.start();s=self.surface();s['context']['native_available_budget']=0
        self.feed(c,rows=[s]);self.assertEqual(self.queue.status(),[])
        self.assertEqual(self.bridge.status()[0]['state'],'completed')
        self.assertTrue(any('平台可追加预算' in x.get('reason','') for x in self.bridge.status()[0]['diagnostics']))
        self.assertTrue(self.queue.rule_auto.row(self.account['id'])['enabled'])

    def test_explicit_native_budget_rejection_requires_independent_unchanged_readback(self):
        self.f2();c=self.ready();permit=self.begin(c);self.consume(c,permit);self.at+=timedelta(seconds=2)
        verify=self.command();v=self.observed(verify,doc='document2');i=c['action']['intent']
        proof={'kind':'native_budget_insufficient','task_id':i['task_id'],'name':i['browser_surface']['name'],'material_count':i['browser_surface']['material_count'],
               'before':i['before'],'after':i['after'],'available':1,'captured_at':self.at.isoformat(),'message':'不得超过当前可用预算，请重新填写'}
        v['validation_rejection']=proof
        bad=copy.deepcopy(v);bad['validation_rejection']['name']='foreign task'
        with self.assertRaises(ValueError):self.bridge.result(dict(run_id=verify['run_id'],id=verify['id'],lease=verify['lease'],ok=True,message='rejected',observation=bad))
        self.assertEqual(self.queue.status()[0]['state'],'unknown')
        self.bridge.result(dict(run_id=verify['run_id'],id=verify['id'],lease=verify['lease'],ok=True,message='rejected',observation=v))
        a=self.queue.status()[0];self.assertEqual(a['state'],'rejected');self.assertEqual(a['result']['ad_changes'],0)
        self.assertEqual(self.bridge.status()[0]['phase'],'scan');self.assertEqual(self.posts,[])

    def test_failed_readback_automatically_resumes_after_its_retry_time_without_replaying(self):
        self.f2();c=self.ready();permit=self.begin(c);self.consume(c,permit)
        with self.queue.db() as db:
            r=db.execute('SELECT * FROM hybrid_rule_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(r['data'])
            d.update(paused=True,failures=5,command=None,retry_at=(self.at+timedelta(minutes=5)).isoformat());self.bridge.save(db,r,d)
        self.at+=timedelta(minutes=6);self.bridge.tick()
        self.assertFalse(self.bridge.status()[0].get('paused'));self.assertEqual(self.command()['rule_phase'],'verify')
        self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertEqual(self.posts,[])

    def test_profile_change_cancels_an_unsubmitted_preflight(self):
        self.f2();c=self.ready();self.bridge.profile_changed(self.account['id'])
        self.assertEqual(next(r for r in self.queue.status() if r['id']==c['action']['id'])['state'],'cancelled')
        self.assertEqual(self.bridge.status()[0]['state'],'cancelled');self.assertEqual(self.posts,[])

    def test_profile_change_never_cancels_or_replays_a_consumed_submission(self):
        self.f2();c=self.ready();permit=self.begin(c);self.consume(c,permit)
        self.bridge.profile_changed(self.account['id'])
        self.assertEqual(next(r for r in self.queue.status() if r['id']==c['action']['id'])['state'],'unknown')
        self.assertEqual(self.bridge.status()[0]['phase'],'verify');self.assertEqual(self.posts,[])

    tearDown=RuleAutoTests.tearDown
    transport=RuleAutoTests.transport
    authorize=RuleAutoTests.authorize
    f1=RuleAutoTests.f1
    f2=RuleAutoTests.f2

    def setUp(self):
        RuleAutoTests.setUp(self)
        self.account['tab_group']='测试户'
        self.profile['safety']['max_actions_per_round']=1
        self.policy={'revision':'a'*64,'accounts':[self.account]}
        self.queue.hybrid=HybridChase(self.queue,lambda:self.policy)
        self.bridge=self.queue.hybrid_rules=HybridRules(self.queue)
        self.queue.hybrid.configure({'account_id':self.account['id'],'mode':'api_preferred'})
        self.worker={'protocol':1,'worker_id':'worker','epoch':'epoch','armed':True,'revision':self.policy['revision'],'extension_version':'1.9.0'}
        self.at=self.queue.clock().replace(microsecond=0)
        self.queue.clock=lambda:self.at

    def start(self,preview=False):
        self.authorize();self.assertTrue(self.bridge.request(self.account['id'],reason='test: API read 40100',preview=preview))
        return self.command()

    def command(self):
        c=self.bridge.next(self.worker)['command'];self.assertIsNotNone(c)
        self.assertEqual(c['phase'],'lookup' if c['rule_phase']=='verify' else 'tasks');return c

    def surface(self,**changes):
        return dict(task_id='10001',account_id=self.account['id'],plan_id=self.pid,name='追投任务',budget='100',duration_minutes='120',running=True,status='调控中',
            actions=['control_pause','control_budget_up','control_duration_up'],metrics={'spend':50,'roi':4},metric_basis='control_payment',material_count=1,
            context={'control_type':'material_chase','control_delivery_mode':'cost_control','observed_duration_mode':'fixed','observed_duration_hours':2,
                     'created_at':(self.at-timedelta(hours=4)).isoformat()},captured_at=self.at.isoformat())|changes

    def observed(self,c,rows=None,doc='document1',total=None):
        rows=rows if rows is not None else [self.surface()]
        total=len(rows) if total is None else total
        return dict(account_id=self.account['id'],plan_id=self.pid,path='/overall-prom/detail',group='测试户',captured_at=self.at.isoformat(),tab_id=11,document_id=doc,
            page=c['page'],page_size=50,total=total,has_next=c['page']*50<total,
            rows=[{'id':s['task_id'],'budget':s['budget'],'status':s['status']} for s in rows],rule_rows=rows,
            parent_budget='10000',running=True,date_start=c['date_start'],date_end=c['date_end'],delivery_entry='chengfang',parent_bid_type='SMART_BID_CUSTOM',
            plan_surface={'account_id':self.account['id'],'plan_id':self.pid,'target_roi':'3.4','roi_basis':'marketing','actions':['roi_target_up','roi_target_down'],'captured_at':self.at.isoformat()})

    def feed(self,c,**kw):return self.bridge.result(dict(run_id=c['run_id'],id=c['id'],lease=c['lease'],ok=True,message='read',observation=self.observed(c,**kw)))

    def ready(self):
        c=self.start();self.feed(c);c=self.command();self.assertEqual(c['rule_phase'],'prepare');return c

    def begin(self,c):return self.bridge.begin(dict(run_id=c['run_id'],id=c['id'],lease=c['lease'],action_id=c['action']['id'],observation=self.observed(c)))

    def consume(self,c,p):return self.bridge.final(dict(run_id=c['run_id'],action_id=c['action']['id'],submit_token=p['submit_token']))

    def test_pause_uses_shared_ledger_and_independent_readback_with_no_api_post(self):
        self.f1();c=self.ready();p=self.begin(c);self.consume(c,p)
        self.assertEqual(self.queue.status()[0]['state'],'unknown')
        self.at+=timedelta(seconds=2);v=self.command()
        self.feed(v,rows=[self.surface(running=False,status='已暂停')],doc='document2')
        self.assertEqual(self.queue.status()[0]['state'],'verified');self.assertEqual(self.posts,[])
        self.assertEqual(self.bridge.status()[0]['completed'],1)

    def test_f2_preserves_absolute_budget(self):
        self.f2();c=self.ready();self.assertEqual(c['action']['intent']['after'],'150.00')
        p=self.begin(c);self.consume(c,p);self.at+=timedelta(seconds=2)
        self.feed(self.command(),rows=[self.surface(budget='150')],doc='document2')
        self.assertEqual(self.queue.status()[0]['state'],'verified')

    def test_scoped_claim_never_claims_a_different_account(self):
        self.f1();self.start()
        self.assertIsNone(self.bridge.next(self.worker,account_id='other')['command'])
        self.assertEqual(self.bridge.next(self.worker,account_id=self.account['id'])['command']['account_id'],self.account['id'])

    def test_paused_unsubmitted_draft_releases_the_account_automatically(self):
        self.f2();c=self.ready()
        with self.queue.db() as db:
            r=db.execute('SELECT * FROM hybrid_rule_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(r['data']);d['paused']=True;self.bridge.save(db,r,d)
        self.bridge.tick()
        self.assertFalse(self.bridge.active(self.account['id']));self.assertEqual(self.queue.status()[0]['state'],'cancelled');self.assertEqual(self.posts,[])

    def test_paused_consumed_submit_keeps_the_original_readback_lock(self):
        self.f1();c=self.ready();p=self.begin(c);self.consume(c,p)
        with self.queue.db() as db:
            r=db.execute('SELECT * FROM hybrid_rule_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(r['data']);d['paused']=True;self.bridge.save(db,r,d)
        self.at+=timedelta(minutes=30);self.bridge.tick()
        self.assertTrue(self.bridge.active(self.account['id']));self.assertEqual(self.queue.status()[0]['state'],'unknown')

    def test_blocked_absolute_budget_reports_task_and_reason_to_main_status(self):
        self.f2();self.profile['features']['f2']['rules'][0]['target_control_budget']=200
        c=self.start();self.feed(c)
        status=self.queue.rule_auto.row(self.account['id'])
        self.assertEqual(status['state'],'watching')
        self.assertIn('50%',status['message'])
        blocked=next(d for d in status['diagnostics'] if d.get('before')=='100.00')
        self.assertEqual(blocked['entity_id'],'10001');self.assertEqual(blocked['after'],'200.00')
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_explicit_account_budget_policy_allows_exact_saved_target_above_fifty_percent(self):
        self.f2();self.profile['features']['f2'].update(budget_policy='saved_target')
        self.profile['features']['f2']['rules'][0]['target_control_budget']=200
        c=self.ready();i=c['action']['intent']
        self.assertEqual(i['after'],'200.00');self.assertTrue(i['cost_guarantee']['allowed'])
        self.assertTrue(self.queue.rule_auto.saved_budget_target(i))
        forged=copy.deepcopy(i);forged['after']='201';forged['rule_check']['after']='201'
        self.assertFalse(self.queue.rule_auto.saved_budget_target(forged))
        manual=copy.deepcopy(i);manual.pop('rule_auto_authority')
        self.assertFalse(self.queue.rule_auto.saved_budget_target(manual))
        self.profile['features']['f2']['budget_policy']='fifty_percent'
        self.assertFalse(self.queue.rule_auto.saved_budget_target(i))

    def test_expired_inventory_rescans_instead_of_retrying_an_obsolete_edit(self):
        self.f2();c=self.ready()
        self.bridge.result(dict(run_id=c['run_id'],id=c['id'],lease=c['lease'],ok=False,message='全量预算清单已过期，未提交',observation=None))
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        self.assertEqual(self.command()['rule_phase'],'scan');self.assertEqual(self.posts,[])

    def test_urgent_claim_skips_readonly_scans_and_claims_the_prepared_rule(self):
        self.f1();c=self.start();self.assertIsNone(self.bridge.urgent(self.worker)['command'])
        self.feed(c);prepared=self.bridge.urgent(self.worker)['command']
        self.assertEqual(prepared['rule_phase'],'prepare');self.assertEqual(prepared['page_size'],50)

    def test_complete_fresh_preflight_replaces_all_expired_inventory_rows(self):
        self.f2();c=self.start()
        other=lambda:self.surface(task_id='10002',metrics={'spend':10,'roi':1})
        self.feed(c,rows=[self.surface(),other()]);c=self.command();self.at+=timedelta(seconds=100)
        result=self.bridge.begin(dict(run_id=c['run_id'],id=c['id'],lease=c['lease'],action_id=c['action']['id'],observation=self.observed(c,rows=[self.surface(),other()])))
        self.assertTrue(result['submit_once']);self.assertEqual(self.posts,[])

    def test_changed_full_inventory_cannot_be_used_as_a_partial_quota_refresh(self):
        self.f2();c=self.start();self.feed(c,rows=[self.surface(),self.surface(task_id='10002',metrics={'spend':10,'roi':1})]);c=self.command()
        with self.assertRaisesRegex(ValueError,'清单变化'):self.begin(c)
        self.assertEqual(self.queue.status()[0]['state'],'draft');self.assertEqual(self.posts,[])

    def test_f3_duration_converts_minutes_to_native_hours(self):
        self.profile['features']['f3'].update(enabled=True,rules=[{'cost_metric':'control_spend','min_control_cost':40,'max_control_cost':100,'min_roi':3,'target_control_budget':100,'increase_control_duration':1,'cooldown_minutes':5}])
        c=self.start();self.feed(c,rows=[self.surface(context=self.surface()['context']|{'control_delivery_mode':'volume'})]);c=self.command()
        self.assertEqual(c['action']['intent']['action'],'task_duration');self.assertEqual(c['action']['intent']['after'],'3.00')

    def test_unknown_action_does_not_generate_fallback(self):
        self.queue.clock=now_cn
        self.f1();self.authorize()
        with patch.object(self.queue,'writer',side_effect=OSError('uncertain')):self.queue.rule_auto.tick()
        self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertFalse(self.bridge.active(self.account['id']))

    def test_read_rate_limit_queues_once_without_changing_grant(self):
        self.f1();self.authorize();grant=copy.deepcopy(self.queue.rule_auto.row(self.account['id']))
        with patch.object(self.api,'scan_workflow',side_effect=OfficialBusinessError(40100,reason='rate_limited')):
            self.queue.rule_auto.tick()
        self.assertTrue(self.bridge.active(self.account['id']));self.assertEqual(self.posts,[])
        self.assertEqual(self.queue.rule_auto.row(self.account['id'])['grant_id'],grant['grant_id'])

    def test_preview_while_unarmed_never_creates_intent(self):
        self.f1();self.worker['armed']=False;c=self.start(preview=True);self.feed(c)
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.bridge.status()[0]['state'],'completed')

    def test_old_extension_cannot_claim_new_rule_jobs(self):
        self.f1();self.authorize();self.bridge.request(self.account['id'],reason='test')
        self.worker['extension_version']='1.8.0';self.assertIsNone(self.bridge.next(self.worker)['command'])

    def test_draft_never_reaches_api_confirm(self):
        self.f1();c=self.ready()
        with self.assertRaisesRegex(ValueError,'网页一次执行'):
            self.queue.confirm({'id':c['action']['id'],'intent_digest':c['action']['intent_digest'],'confirmed':True})
        self.assertEqual(self.posts,[])

    def test_final_permit_is_one_use_and_restart_does_not_replay(self):
        self.f1();c=self.ready();p=self.begin(c);self.consume(c,p)
        with self.assertRaises(ValueError):self.consume(c,p)
        self.bridge=HybridRules(self.queue);self.queue.hybrid_rules=self.bridge
        self.at+=timedelta(seconds=130)
        self.assertEqual(self.command()['rule_phase'],'verify')
        self.assertEqual(len(self.queue.status()),1)

    def test_unconsumed_permit_expires_as_cancelled(self):
        self.f1();c=self.ready();self.begin(c);self.at+=timedelta(seconds=130)
        self.assertIsNone(self.bridge.next(self.worker)['command']);self.assertEqual(self.queue.status()[0]['state'],'cancelled')

    def test_same_document_and_wrong_value_cannot_verify(self):
        self.f1();c=self.ready();p=self.begin(c);self.consume(c,p);self.at+=timedelta(seconds=2);v=self.command()
        with self.assertRaisesRegex(ValueError,'独立核验'):self.feed(v,rows=[self.surface(running=False,status='已暂停')])
        with self.assertRaisesRegex(ValueError,'不重发'):self.feed(v,doc='document2')
        self.assertEqual(self.queue.status()[0]['state'],'unknown')

    def test_rule_revision_change_revokes_final_permit(self):
        self.f1();c=self.ready();p=self.begin(c);self.profile['features']['f1']['max_roi']=2
        with self.assertRaisesRegex(ValueError,'规则已变化'):self.consume(c,p)

    def test_missing_roi_or_cost_is_not_zero(self):
        self.f1();c=self.start(preview=True);self.feed(c,rows=[self.surface(metrics={'spend':50},metric_basis='unknown')])
        self.assertEqual(self.queue.status(),[]);self.assertIn('支付ROI',str(self.bridge.status()[0]['diagnostics']))

    def test_manual_hold_releases_account_but_retains_unknown_record(self):
        self.f1();c=self.ready();p=self.begin(c);self.consume(c,p)
        self.queue.hold_task({'id':c['action']['id'],'account_id':self.account['id']})
        self.at+=timedelta(seconds=130);self.assertIsNone(self.bridge.next(self.worker)['command'])
        self.assertFalse(self.bridge.active(self.account['id']));self.assertEqual(self.queue.status()[0]['state'],'unknown')

    def test_low_spend_reads_creation_to_now_before_pause(self):
        self.profile['features']['f4'].update(enabled=True,pause_rule='low_spend',low_spend_hours=3,low_spend_max=60,low_spend_scope='both')
        c=self.start();self.feed(c);life=self.command();self.assertEqual(life['rule_phase'],'lifetime')
        self.feed(life);self.assertEqual(self.command()['action']['intent']['rule_check']['feature_id'],'f4')

    def test_api_only_or_disabled_authority_never_queues(self):
        self.f1();self.authorize();self.queue.hybrid.configure({'account_id':self.account['id'],'mode':'api_only'})
        self.assertFalse(self.bridge.request(self.account['id'],reason='limit'))

    def test_f6_missing_windows_is_explicit_and_never_uses_today_as_window(self):
        self.profile['features']['f6'].update(enabled=True,min_overall_roi=1,max_online_users=100,min_15m_roi=1,min_30m_roi=1,target_roi_delta=.1,min_interval_minutes=60)
        c=self.start(preview=True);self.feed(c);self.assertEqual(self.queue.status(),[])
        self.assertIn('15/30',str(self.bridge.status()[0]['diagnostics']))

    def test_f6_with_proven_official_windows_binds_native_target(self):
        self.profile['features']['f6'].update(enabled=True,min_overall_roi=1,max_online_users=100,min_15m_roi=1,min_30m_roi=1,target_roi_delta=.1,min_interval_minutes=60)
        self.queue.rule_auto.live_provider=lambda aid,pid:{'account_id':aid,'plan_id':pid,'source':'official_api','metric_basis':'overall_payment','metric_date':self.at.date().isoformat(),'captured_at':self.at.isoformat(),
            'windows':{name:{'start_at':(self.at-timedelta(minutes=minutes)).isoformat(),'end_at':self.at.isoformat()} for name,minutes in [('roi_15m',15),('roi_30m',30)]},'metrics':{'roi':4,'roi_15m':4,'roi_30m':4,'online_users':10}}
        c=self.ready();self.assertEqual(c['action']['intent']['action'],'plan_roi');self.assertEqual(float(c['action']['intent']['after']),3.5)
        p=self.begin(c);self.consume(c,p);self.at+=timedelta(seconds=2);v=self.command();obs=self.observed(v,doc='doc2');obs['plan_surface']['target_roi']='3.5'
        self.bridge.result(dict(run_id=v['run_id'],id=v['id'],lease=v['lease'],ok=True,message='read',observation=obs))
        self.assertEqual(self.queue.status()[0]['state'],'verified')

    def test_full_pagination_is_required_and_duplicate_page_is_rejected(self):
        self.f1();c=self.start(preview=True)
        rows=[self.surface(task_id=str(10001+n)) for n in range(50)]
        self.feed(c,rows=rows,total=51);self.assertEqual(self.bridge.status()[0]['state'],'waiting')
        v=self.command();self.assertEqual(v['page'],2)
        with self.assertRaisesRegex(ValueError,'重复任务'):self.feed(v,rows=[rows[0]],total=51)
        self.feed(v,rows=[self.surface(task_id='10099')],total=51)
        self.assertEqual(self.bridge.status()[0]['tasks_read'],51);self.assertEqual(self.queue.status(),[])

    def test_browser_budget_cannot_bypass_original_f5_cap_or_call_api_for_quota(self):
        self.f2();self.queue.auto.row=lambda aid:{'budget_cap':'120'}
        self.queue.auto.owned=lambda *args:{'10001'}
        c=self.start();calls=len(self.calls);self.feed(c)
        self.assertEqual(len(self.calls),calls);self.assertEqual(self.queue.status(),[])
        self.assertIn('预算合计',str(self.bridge.status()[0]['diagnostics']))

    def test_definitive_platform_write_limit_queues_fallback_once(self):
        self.queue.clock=now_cn;self.f1();self.authorize()
        with patch.object(self.queue,'writer',side_effect=OfficialBusinessError(40100,reason='rate_limited')) as post:self.queue.rule_auto.tick()
        self.assertEqual(post.call_count,1);self.assertEqual(self.queue.status()[0]['state'],'rejected')
        self.assertTrue(self.bridge.active(self.account['id']))

    def test_lost_response_never_turns_into_browser_fallback(self):
        self.queue.clock=now_cn;self.f1();self.authorize()
        with patch.object(self.queue,'writer',side_effect=OfficialBusinessError(51010,reason='service_busy')):self.queue.rule_auto.tick()
        self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertFalse(self.bridge.active(self.account['id']))

    def test_manual_stop_cancels_an_unconsumed_permit(self):
        self.f1();c=self.ready();p=self.begin(c);self.bridge.control({'account_id':self.account['id']},'stop')
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        with self.assertRaises(ValueError):self.consume(c,p)

    def test_form_preview_is_ephemeral_and_cannot_request_any_submission(self):
        self.f1();self.authorize();self.worker['armed']=False
        self.bridge.control({'account_id':self.account['id']},'preview');c=self.command();self.feed(c)
        c=self.command();self.assertEqual(c['rule_phase'],'dry_prepare');self.assertEqual(self.queue.status(),[])
        with self.assertRaises(ValueError):self.begin(c)
        self.feed(c);self.assertEqual(self.bridge.status()[0]['state'],'completed');self.assertEqual(self.queue.status(),[])

    def test_readonly_f5_sweep_yields_but_an_existing_action_is_never_preempted(self):
        self.f1();self.authorize();self.queue.hybrid.request(self.account['id'],reason='old F5 sweep',preview=True)
        self.assertTrue(self.queue.hybrid.active(self.account['id']))
        self.assertTrue(self.bridge.request(self.account['id'],reason='F1 priority'))
        self.assertFalse(self.queue.hybrid.active(self.account['id']))

    def test_manual_retry_invalidates_only_a_read_lease_and_restarts_full_sweep(self):
        self.f1();c=self.start(preview=True)
        self.bridge.control({'account_id':self.account['id']},'retry')
        with self.assertRaisesRegex(ValueError,'命令身份'):self.feed(c)
        next_c=self.command();self.assertNotEqual(next_c['id'],c['id']);self.assertEqual(next_c['page'],1)
        self.assertEqual(self.queue.status(),[])

    def test_changed_pre_submit_setting_cancels_draft_and_rescans_instead_of_retrying_old_target(self):
        self.f1();c=self.ready();self.bridge.result(dict(run_id=c['run_id'],id=c['id'],lease=c['lease'],ok=False,message='原任务设置已变化：调控预算 100 → 200',observation=None))
        self.assertEqual(self.queue.status()[0]['state'],'cancelled');self.assertEqual(self.command()['rule_phase'],'scan')
        self.assertEqual(self.posts,[])
