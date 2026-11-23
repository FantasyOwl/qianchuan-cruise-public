import unittest
import copy
from unittest.mock import patch
from datetime import timedelta
from tests import test_official_extensions as fixtures
from monitor.official_api import OfficialBusinessError


class AutoTests(unittest.TestCase):
    setUp=fixtures.ExtensionTests.setUp
    tearDown=fixtures.ExtensionTests.tearDown
    transport=fixtures.ExtensionTests.transport
    scanned=fixtures.ExtensionTests.scanned
    prepared=fixtures.ExtensionTests.prepared
    confirm=fixtures.ExtensionTests.confirm

    def configure(self,*,enabled=True,cap='300',batches='1',**choices):
        return self.queue.auto.configure({'account_id':self.account['id'],'plan_id':self.pid,'profile_revision':'r1',
            'enabled':enabled,'active_budget_cap':cap,'max_new_batches_per_day':batches,'confirmed':enabled,**choices})

    def test_unlimited_choices_are_explicit_persistent_and_do_not_submit_on_configure(self):
        self.configure(cap=None,batches=None,active_budget_unlimited=True,daily_batches_unlimited=True)
        row=self.queue.auto.row(self.account['id'])
        self.assertTrue(row['budget_unlimited']);self.assertTrue(row['batches_unlimited'])
        self.assertIsNone(row['budget_cap']);self.assertIsNone(row['batch_cap']);self.assertEqual(self.posts,[])
        from monitor.official_auto import AutoChase
        self.assertTrue(AutoChase(self.queue).row(self.account['id'])['budget_unlimited'])

    def test_unlimited_budget_still_obeys_a_finite_daily_batch_limit(self):
        self.multiple_tasks();self.configure(cap=None,batches='2',active_budget_unlimited=True)
        self.queue.auto.tick();self.assertEqual(len(self.posts),2)
        self.assertIn('每天最多新增批数',self.queue.auto.row(self.account['id'])['message'])

    def test_unlimited_daily_batches_still_obey_a_finite_active_budget(self):
        self.multiple_tasks();self.configure(cap='400',batches=None,daily_batches_unlimited=True)
        self.queue.auto.tick();self.assertEqual(len(self.posts),2)
        self.assertIn('预算合计',self.queue.auto.row(self.account['id'])['message'])

    def test_explicit_unlimited_f5_does_not_hit_the_old_daily_operation_ceiling(self):
        self.multiple_tasks();self.profile['safety']['max_actions_per_day']=1
        self.configure(cap=None,batches=None,active_budget_unlimited=True,daily_batches_unlimited=True)
        self.queue.auto.tick();self.assertEqual(len(self.posts),3)
        self.assertTrue(self.queue.auto.row(self.account['id'])['enabled'])

    def test_unlimited_chases_do_not_exhaust_rule_control_allowance_or_remove_audit_records(self):
        self.multiple_tasks();self.profile['safety']['max_actions_per_day']=1
        self.configure(cap=None,batches=None,active_budget_unlimited=True,daily_batches_unlimited=True);self.queue.auto.tick()
        self.assertEqual(len(self.queue._history()),3);self.assertEqual(self.queue.history(self.account['id']),[])
        intent=copy.deepcopy(self.queue.status()[0]['intent']);intent.update(action='pause_task',body={},task_id='10001',task_scene='MATERIAL_ADD_BUDGET')
        self.assertTrue(self.queue._guard(intent,self.account)['allowed'])
        self.assertEqual(len(self.queue.status()),3)
        self.queue.auto.disable({'account_id':self.account['id']})
        self.assertEqual(self.queue.history(self.account['id']),[])
        self.assertTrue(self.queue._guard(intent,self.account)['allowed'])

    def test_unlimited_cannot_mean_an_implicit_blank_or_conflicting_numeric_limit(self):
        for choices in ({'active_budget_unlimited':True}, {'daily_batches_unlimited':True}):
            with self.assertRaisesRegex(ValueError,'自行填写'):self.configure(cap=None,batches=None,**choices)
        with self.assertRaisesRegex(ValueError,'不能同时'):self.configure(active_budget_unlimited=True)
        with self.assertRaisesRegex(ValueError,'必须明确'):self.configure(active_budget_unlimited='true')
        self.assertEqual(self.posts,[])

    def test_rule_queue_wait_does_not_charge_a_complete_material_sweep_interval(self):
        from types import SimpleNamespace
        self.configure();at=self.queue.clock();self.queue.hybrid_rules=SimpleNamespace(active=lambda aid:True)
        self.queue.auto.tick();row=self.queue.auto.row(self.account['id'])
        from monitor.schema import parse_time
        self.assertLessEqual((parse_time(row['next_at'])-at).total_seconds(),16)
        self.assertEqual(row['state'],'browser_wait');self.assertEqual(self.posts,[])

    def multiple_tasks(self):
        import copy
        created={};real=self.api.transport
        def transport(name,q,token=None):
            if name=='control_tasks' and q.get('filtering',{}).get('search_keyword') in created:
                return {'task_list':[copy.deepcopy(created[q['filtering']['search_keyword']])],'page_info':{'total_number':1}}
            value=real(name,q,token)
            if name=='create_control_task':
                tid=str(10000000+len(created)+1);self.created['id']=int(tid)
                created[tid]=dict(copy.deepcopy(self.created),metrics={'stat_cost_for_roi2_assist':0})
                return {'id':int(tid)}
            if name=='control_tasks' and q.get('filtering',{}).get('task_status')=='PROCESSING' and q['scene']=='MATERIAL_ADD_BUDGET':
                value['task_list'].extend(copy.deepcopy(list(created.values())));value['page_info']['total_number']+=len(created)
            return value
        self.api.transport=transport
        self.queue.writer=transport
        self.profile['features']['f5']['allow_running_materials']=True
        self.materials=[(str(89581211092803411+i),500-i,4) for i in range(6)]

    def test_one_sweep_executes_all_non_overlapping_full_batches_without_an_interval_between_them(self):
        self.multiple_tasks();self.configure(cap='1000',batches='5');self.queue.auto.tick()
        self.assertEqual(len(self.posts),3)
        self.assertEqual(len({m for p in self.posts for m in p['material_ids']}),6)
        self.assertTrue(all(r['state']=='verified' for r in self.queue.status()))
        self.assertIn('3/3批',self.queue.auto.status()[0]['message'])
        self.queue.auto.tick();self.assertEqual(len(self.posts),3)

    def test_cap_blocks_remaining_batches_within_same_complete_sweep(self):
        self.multiple_tasks();self.configure(cap='400',batches='5');self.queue.auto.tick()
        self.assertEqual(len(self.posts),2);self.assertEqual(len(self.api.workflows[self.account['id']+':'+self.pid]['batches']),3)
        self.assertIn('预算合计',self.queue.auto.status()[0]['message'])

    def test_daily_cap_blocks_remaining_batches_within_same_sweep(self):
        self.multiple_tasks();self.configure(cap='1000',batches='2');self.queue.auto.tick()
        self.assertEqual(len(self.posts),2);self.assertIn('每天最多新增批数',self.queue.auto.status()[0]['message'])

    def test_new_task_omitted_from_processing_list_still_reserves_budget_by_actual_id(self):
        self.multiple_tasks();real=self.api.transport
        def lagging_list(name,q,token=None):
            value=real(name,q,token)
            if name=='control_tasks' and q.get('filtering',{}).get('task_status')=='PROCESSING':
                value['task_list']=[t for t in value['task_list'] if t['id']==10001];value['page_info']['total_number']=len(value['task_list'])
            return value
        self.api.transport=lagging_list;self.configure(cap='400',batches='5');self.queue.auto.tick()
        self.assertEqual(len(self.posts),2);self.assertIn('预算合计',self.queue.auto.status()[0]['message'])

    def test_long_sweep_services_previously_authorized_deadlines_and_waits_interval_after_finishing(self):
        # Keep this duration test away from midnight; crossing the statistics
        # date intentionally stops creation and is a different condition.
        now=self.queue.clock().replace(hour=12,minute=0,second=0,microsecond=0)
        self.queue.clock=lambda:now;self.plan['captured_at']=now.isoformat()
        with patch('monitor.official_workflow.now_cn',return_value=now):self.configure()
        calls=[];real=self.api.scan_workflow
        def delayed(*args,**kwargs):
            if kwargs.get('materials'):
                self.queue.clock=lambda:now+timedelta(minutes=20)
                kwargs['progress']();calls.append('deadlines')
            return real(*args,**kwargs)
        self.api.scan_workflow=delayed
        with patch('monitor.official_workflow.now_cn',return_value=now):self.queue.auto.tick()
        self.assertTrue(calls);self.assertEqual(len(self.posts),1)
        self.assertEqual(self.queue.auto.status()[0]['next_at'],(now+timedelta(minutes=30)).isoformat())

    def test_both_limits_start_empty_and_neither_scanning_nor_saving_enables_spending(self):
        self.assertEqual(self.queue.auto.status(),[]);self.queue.auto.tick();self.assertEqual(self.posts,[])
        self.configure(enabled=False,cap=None,batches=None)
        r=self.queue.auto.status()[0];self.assertIsNone(r['budget_cap']);self.assertIsNone(r['batch_cap']);self.assertFalse(r['enabled'])
        self.queue.auto.tick();self.assertEqual(self.posts,[])
        with self.assertRaisesRegex(ValueError,'自行填写'):self.configure(cap=None,batches=None)

    def test_explicit_native_quota_rejection_keeps_f5_authority_and_releases_daily_reservation(self):
        self.configure()
        def limited(name,q,token):
            self.posts.append(copy.deepcopy(q));raise OfficialBusinessError(40100,operation=name,reason='rate_limited',retry_after=40)
        self.queue.writer=limited;self.queue.auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.queue.status()[0]['state'],'rejected')
        row=self.queue.auto.row(self.account['id']);self.assertTrue(row['enabled']);self.assertEqual(row['state'],'waiting');self.assertEqual(row['day_batches_reserved'],0)

    def test_local_create_admission_wait_cancels_unsent_draft_without_disabling_f5(self):
        self.configure()
        def limited(name,q,token):raise OfficialBusinessError(40100,operation=name,reason='rate_limited',retry_after=20,origin='local_cooldown')
        self.queue.writer=limited;self.queue.auto.tick()
        self.assertEqual(self.posts,[]);self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        row=self.queue.auto.row(self.account['id']);self.assertTrue(row['enabled']);self.assertEqual(row['day_batches_reserved'],0)

    def test_one_explicit_rule_authority_executes_then_daily_cap_blocks_next_cycle(self):
        self.configure();self.assertEqual(self.posts,[]);self.queue.auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.queue.status()[0]['state'],'verified')
        self.assertIn('auto_authority',self.queue.status()[0]['intent'])
        now=self.queue.clock();self.queue.clock=lambda:now+timedelta(minutes=11)
        self.queue.auto.tick();self.assertEqual(len(self.posts),1)
        self.assertEqual(self.queue.auto.status()[0]['state'],'quota_wait')

    def test_automatic_volume_chase_registers_stop_for_only_its_new_task(self):
        self.profile['features']['f5']['rules'][0].update(chase_type='放量追投',total_budget=200,control_duration=2)
        self.configure();self.queue.auto.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.posts[0]['smart_bid_type'],'SMART_BID_CONSERVATIVE')
        self.assertEqual(self.queue.followups()[0]['task_id'],'10000001')

    def test_disable_during_preflight_prevents_post_and_cancels_only_owned_draft(self):
        self.configure();real=self.queue._fresh
        def disable(*args):
            self.queue.auto.disable({'account_id':self.account['id']});return real(*args)
        self.queue._fresh=disable;self.queue.auto.tick()
        self.assertEqual(self.posts,[]);self.assertFalse(self.queue.auto.status()[0]['enabled'])
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')

    def test_rule_change_invalidates_standing_authority_without_silent_reapproval(self):
        self.configure();self.profile['features']['f5']['batch_size']=3
        self.queue.auto.tick();self.assertFalse(self.queue.auto.status()[0]['enabled']);self.assertEqual(self.posts,[])

    def test_unknown_post_disables_automatic_retries(self):
        self.configure();self.fail_post=True;self.queue.auto.tick();self.assertEqual(len(self.posts),1)
        self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertFalse(self.queue.auto.status()[0]['enabled'])
        self.queue.auto.tick();self.assertEqual(len(self.posts),1)

    def test_active_program_budget_cap_is_rechecked_across_cycles(self):
        self.profile['features']['f5']['allow_running_materials']=True
        self.configure(cap='300',batches='5');self.queue.auto.tick();self.assertEqual(len(self.posts),1)
        real=self.api.transport
        def include_created(name,q,token=None):
            value=real(name,q,token)
            if name=='control_tasks' and q.get('filtering',{}).get('task_status')=='PROCESSING' and q['scene']=='MATERIAL_ADD_BUDGET':
                value['task_list'].append(dict(self.created,metrics={'stat_cost_for_roi2_assist':0}));value['page_info']['total_number']+=1
            return value
        self.api.transport=include_created;now=self.queue.clock();self.queue.clock=lambda:now+timedelta(minutes=11)
        self.queue.auto.tick();self.assertEqual(len(self.posts),1);self.assertIn('预算合计',self.queue.auto.status()[0]['message'])

    def test_authority_cannot_be_injected_into_public_prepare_fields(self):
        value=self.scanned()
        with self.assertRaisesRegex(ValueError,'字段不匹配'):
            self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'material_chase',
                'batch_key':value['batches'][0]['key'],'auto_authority':{'grant_id':'fake'}})
        self.assertEqual(self.posts,[])

    def test_enabling_chase_does_not_approve_or_cancel_a_pending_human_plan_draft(self):
        human=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'pause_plan'})
        self.configure();self.queue.auto.tick();self.assertEqual(self.posts,[])
        self.assertEqual(next(r for r in self.queue.status() if r['id']==human['id'])['state'],'draft')
        self.assertEqual(self.queue.auto.status()[0]['state'],'waiting')
