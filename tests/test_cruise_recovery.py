import copy
import json
import unittest
from datetime import timedelta

from tests.test_hybrid_chase import HybridTests
from tests.test_chase_progress import ChaseProgressTests


class RecoveryTests(unittest.TestCase):
    setUp=HybridTests.setUp
    tearDown=HybridTests.tearDown
    transport=HybridTests.transport
    command=HybridTests.command
    observed=HybridTests.observed
    feed=HybridTests.feed
    tasks=HybridTests.tasks
    page_materials=HybridTests.page_materials
    ready=HybridTests.ready
    begin_web=HybridTests.begin_web
    start_materials=ChaseProgressTests.start_materials

    def finish_first(self):
        c=self.ready();p=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']})
        self.at+=timedelta(seconds=2);v=self.command();b=v['action']['intent']['browser_batch']
        self.feed(v,[{'id':'10000009','name':b['task_name'],'material_ids':b['material_ids'],'after':b['after'],'status':'调控中'}],document='new-document')

    def failure(self,c,message):
        return self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,'message':message,'observation':None})

    def test_missing_historical_id_is_bounded_and_reserved_not_marked_stopped(self):
        self.finish_first();old=copy.deepcopy(self.queue.status()[0]);self.at+=timedelta(seconds=1)
        self.hybrid.request(self.account['id'],reason='next sweep');self.feed(self.command(),self.tasks())
        c=self.command();self.assertEqual(c['lookup_id'],'10000009');self.feed(c,[])
        c=self.command();self.feed(c,[]);c=self.command();self.assertEqual(c['phase'],'materials')
        reserve=self.hybrid.status()[0]['reserved_tasks'][0]
        self.assertEqual(reserve['id'],'10000009');self.assertEqual(float(reserve['budget']),200);self.assertEqual(reserve['status'],'未核实保留额度')
        self.assertEqual(self.queue.status()[0],old)

    def test_reserved_missing_task_still_counts_against_cap(self):
        self.finish_first();self.at+=timedelta(seconds=1);self.queue.auto.configure({'account_id':self.account['id'],'plan_id':self.pid,'profile_revision':'r1','enabled':True,'active_budget_cap':'300','max_new_batches_per_day':'3','confirmed':True})
        self.hybrid.request(self.account['id'],reason='next');self.feed(self.command(),self.tasks())
        self.feed(self.command(),[]);self.feed(self.command(),[])
        self.materials_data=[('82244138676545106',400,4),('80015353086142109',300,4)]
        self.feed(self.command(),self.page_materials());self.feed(self.command(),self.tasks())
        self.assertEqual(self.hybrid.status()[0]['state'],'completed');self.assertIn('预算上限',self.hybrid.status()[0]['message'])
        self.assertEqual(len(self.queue.status()),1)

    def test_stale_id_search_reloads_and_uses_original_name_but_checks_exact_id(self):
        self.finish_first();original=copy.deepcopy(self.queue.status()[0]);self.at+=timedelta(seconds=1)
        self.hybrid.request(self.account['id'],reason='next');self.feed(self.command(),self.tasks())
        first=self.command();self.assertEqual(first['lookup_id'],'10000009');self.feed(first,[])
        second=self.command();self.assertEqual(second['lookup_id'],original['intent']['browser_batch']['task_name'])
        self.assertGreater(second['document_cycle'],first['document_cycle'])
        self.feed(second,[{'id':'10000009','budget':'200','status':'已暂停'}])
        self.assertEqual(self.command()['phase'],'materials');self.assertEqual(self.hybrid.status()[0]['reserved_tasks'],[])
        self.assertEqual(self.queue.status()[0],original)

    def test_two_material_failures_defer_only_that_batch_and_continue_other_materials(self):
        c=self.start_materials();self.feed(c,self.page_materials());self.feed(self.command(),self.tasks());c=self.command()
        bad=c['action']['intent']['material_ids'];self.failure(c,'表单素材集合不一致：期望2条，实际加载1条；未提交')
        self.assertEqual(self.queue.deferred_materials(self.account['id'],self.pid),set())
        self.feed(self.command(),self.page_materials());self.feed(self.command(),self.tasks());c=self.command()
        self.failure(c,'表单素材集合不一致：期望2条，实际加载1条；未提交')
        self.assertEqual(self.queue.deferred_materials(self.account['id'],self.pid),set(bad))
        self.feed(self.command(),self.page_materials());self.feed(self.command(),self.tasks());c=self.command()
        self.assertFalse(set(c['action']['intent']['material_ids'])&set(bad));self.assertEqual(self.posts,[])

    def test_temporary_deferral_expires_and_does_not_modify_permanent_exclusion_or_authority(self):
        before=copy.deepcopy(self.queue.auto.row(self.account['id']));profile=copy.deepcopy(self.profile)
        ids=['82244138676545106','80015353086142109']
        self.queue.note_material_failure(self.account['id'],self.pid,ids,'条件变化');self.queue.note_material_failure(self.account['id'],self.pid,ids,'条件变化')
        self.assertEqual(self.queue.deferred_materials(self.account['id'],self.pid),set(ids));self.assertEqual(self.queue.material_exclusions(self.account['id']),[])
        self.at+=timedelta(minutes=16);self.assertEqual(self.queue.deferred_materials(self.account['id'],self.pid),set())
        self.assertEqual(self.queue.auto.row(self.account['id']),before);self.assertEqual(self.profile,profile)

    def test_watchdog_expires_no_progress_even_if_worker_keeps_claiming_same_read(self):
        self.hybrid.request(self.account['id'],reason='test');self.command()
        self.at+=timedelta(minutes=9);self.command()
        self.at+=timedelta(minutes=3);self.hybrid.tick()
        self.assertEqual(self.hybrid.status()[0]['state'],'cancelled');self.assertTrue(self.queue.auto.row(self.account['id'])['enabled']);self.assertEqual(self.posts,[])

    def test_real_page_progress_prevents_watchdog_from_truncating_long_sweeps(self):
        self.hybrid.request(self.account['id'],reason='test');self.feed(self.command(),self.tasks())
        self.at+=timedelta(minutes=8);c=self.command()
        self.materials_data=[(str(80268324757947817+i),500-i,4) for i in range(100)]
        self.at+=timedelta(minutes=1);self.feed(c,self.page_materials(),total=200)
        self.at+=timedelta(minutes=2);self.hybrid.tick();self.assertTrue(self.hybrid.active(self.account['id']))

    def test_watchdog_never_cancels_or_replays_a_consumed_unknown_submission(self):
        c=self.ready();p=self.begin_web(c);self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']})
        self.at+=timedelta(hours=2);self.hybrid.tick()
        self.assertTrue(self.hybrid.active(self.account['id']));self.assertIn(self.queue.status()[0]['state'],{'submitting','unknown'})
        self.assertEqual(self.command()['phase'],'verify');self.assertEqual(self.posts,[])

    def test_repeated_readback_failures_keep_automatic_read_only_retries(self):
        c=self.ready();p=self.begin_web(c);self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']})
        for _ in range(5):
            self.at+=timedelta(seconds=301);self.failure(self.command(),'原任务素材ID尚未全部回读')
        state=self.hybrid.status()[0];self.assertFalse(state['auto_retry_paused']);self.assertIsNotNone(state['retry_at'])
        self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertEqual(self.posts,[])

    def test_main_rule_read_is_not_blocked_by_read_only_f5_and_writes_yield_safely(self):
        c=self.start_materials();i={'account_id':self.account['id'],'plan_id':self.pid,'action':'auto_rule_scan'}
        with self.queue.db() as db:self.queue._pending(db,i)
        with self.queue.db() as db:
            with self.assertRaisesRegex(ValueError,'网页补位'):self.queue._pending(db,{**i,'action':'pause_task'})
        self.assertTrue(self.hybrid.yield_readonly(self.account['id']))
        with self.queue.db() as db:self.queue._pending(db,{**i,'action':'pause_task'})
        with self.assertRaisesRegex(ValueError,'已结束'):self.feed(c,[])
        self.assertTrue(self.queue.auto.row(self.account['id'])['enabled'])

    def test_preparing_or_unknown_job_cannot_be_preempted_by_main_rules(self):
        c=self.ready();self.assertFalse(self.hybrid.read_only_active(self.account['id']));self.assertFalse(self.hybrid.yield_readonly(self.account['id']))
        self.begin_web(c);self.assertFalse(self.hybrid.yield_readonly(self.account['id']))

    def test_automatic_cancellation_is_not_labelled_as_user_cancellation(self):
        c=self.ready();self.queue.cancel({'id':c['action']['id']},reason='本轮数据变化，未提交')
        r=self.queue.status()[0];self.assertEqual(r['result']['phase'],'automatic_cancel');self.assertNotIn('用户',r['result']['message'])

    def test_unknown_readback_after_midnight_uses_original_creation_day(self):
        c=self.ready();original_day=c['date_end'];p=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']})
        self.at+=timedelta(days=1);v=self.command()
        self.assertEqual(v['phase'],'verify');self.assertEqual(v['date_start'],original_day);self.assertEqual(v['date_end'],original_day)

    def test_read_only_verification_can_finish_after_parent_stops_but_new_write_cannot(self):
        c=self.ready();p=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']})
        self.at+=timedelta(seconds=2);v=self.command();b=v['action']['intent']['browser_batch']
        observation=self.observed(v,[{'id':'10000009','name':b['task_name'],'material_ids':b['material_ids'],'after':b['after'],'status':'已暂停'}],document='new-document')
        observation['running']=False
        with self.assertRaisesRegex(ValueError,'母计划未明确投放中'):self.hybrid.observation(observation,{'account_id':self.account['id'],'plan_id':self.pid})
        self.hybrid.result({'run_id':v['run_id'],'id':v['id'],'lease':v['lease'],'ok':True,'message':'只读回读','observation':observation})
        self.assertEqual(self.queue.status()[0]['state'],'verified');self.assertEqual(self.posts,[])

    def test_explicit_audit_rejection_defers_12h_immediately_while_loading_and_in_review_stay_short(self):
        rejected='88020054452791027';in_review='80523516050441559';loading='88632839551534594';deleted='83385420738728366'
        r1=self.queue.note_material_failure(self.account['id'],self.pid,[rejected],'添加视频审核未通过 '+rejected+'（审核不通过）')
        self.assertTrue(r1['explicit_rejection']);self.assertIsNotNone(r1['until_at'])
        self.assertIn(rejected,self.queue.deferred_materials(self.account['id'],self.pid))
        self.assertEqual(self.queue.material_exclusions(self.account['id']),[])

        r2=self.queue.note_material_failure(self.account['id'],self.pid,[deleted],'本批素材已删除 '+deleted+'；未提交')
        self.assertTrue(r2['explicit_rejection']);self.assertIn(deleted,self.queue.deferred_materials(self.account['id'],self.pid))

        r3=self.queue.note_material_failure(self.account['id'],self.pid,[in_review],'添加视频审核未通过 '+in_review+'（审核中）')
        self.assertFalse(r3['explicit_rejection']);self.assertIsNone(r3['until_at'])
        self.assertNotIn(in_review,self.queue.deferred_materials(self.account['id'],self.pid))

        r4=self.queue.note_material_failure(self.account['id'],self.pid,[loading],'等待素材审核状态异步加载 '+loading+'；未提交')
        self.assertFalse(r4['explicit_rejection']);self.assertIsNone(r4['until_at'])
        self.queue.note_material_failure(self.account['id'],self.pid,[loading],'等待素材审核状态异步加载 '+loading+'；未提交')
        self.assertIn(loading,self.queue.deferred_materials(self.account['id'],self.pid))

        self.at+=timedelta(minutes=20)
        self.assertNotIn(loading,self.queue.deferred_materials(self.account['id'],self.pid))
        self.assertIn(rejected,self.queue.deferred_materials(self.account['id'],self.pid))
        self.assertIn(deleted,self.queue.deferred_materials(self.account['id'],self.pid))

        self.at+=timedelta(hours=12)
        self.assertEqual(self.queue.deferred_materials(self.account['id'],self.pid),set())
        self.assertEqual(self.queue.material_exclusions(self.account['id']),[])
        self.assertEqual(self.posts,[])
