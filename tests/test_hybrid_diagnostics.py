import copy
import unittest
from datetime import timedelta
from tests import test_hybrid_chase as fixtures


class HybridDiagnosticsTests(unittest.TestCase):
    setUp=fixtures.HybridTests.setUp
    tearDown=fixtures.HybridTests.tearDown
    transport=fixtures.HybridTests.transport
    confirm=fixtures.HybridTests.confirm
    command=fixtures.HybridTests.command
    observed=fixtures.HybridTests.observed
    feed=fixtures.HybridTests.feed
    tasks=fixtures.HybridTests.tasks
    page_materials=fixtures.HybridTests.page_materials
    ready=fixtures.HybridTests.ready
    begin_web=fixtures.HybridTests.begin_web

    def verified_web_action(self):
        c=self.ready();rid=c['action']['id'];permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':rid,'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2);verify=self.command();batch=verify['action']['intent']['browser_batch']
        task={'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],'after':batch['after'],'status':'调控中'}
        self.feed(verify,[task],document='independent-first-verification')
        return rid,task

    def test_existing_browser_task_can_be_rechecked_unarmed_without_changing_ledger_or_daily_count(self):
        rid,task=self.verified_web_action();before=copy.deepcopy(self.queue.status())
        self.at+=timedelta(days=1);self.worker['armed']=False
        grant=copy.deepcopy(self.queue.auto.row(self.account['id']));daily=self.queue.auto.daily(self.account['id'])
        job=self.hybrid.recheck({'action_id':rid});self.assertEqual(job['run_id'],self.hybrid.recheck({'action_id':rid})['run_id'])
        self.assertFalse(self.hybrid.active(self.account['id'],writes_only=True))
        c=self.command();self.assertEqual(c['phase'],'verify');self.assertTrue(c['preview']);self.assertEqual(c['date_start'],before[0]['created_at'][:10])
        with self.assertRaisesRegex(ValueError,'尚未批准提交'):self.begin_web(c)
        self.at+=timedelta(seconds=2)
        self.feed(c,[task],document='independent-recheck')
        state=self.hybrid.status()[0];self.assertTrue(state['readback_only']);self.assertEqual(state['state'],'completed')
        self.assertEqual(state['readback_result']['ad_changes'],0);self.assertEqual(self.queue.status(),before)
        self.assertEqual(self.queue.auto.row(self.account['id']),grant);self.assertEqual(self.queue.auto.daily(self.account['id']),daily);self.assertEqual(self.posts,[])

    def test_empty_recheck_and_different_task_id_preserve_the_original_verified_receipt(self):
        rid,task=self.verified_web_action();before=copy.deepcopy(self.queue.status());self.hybrid.recheck({'action_id':rid})
        self.at+=timedelta(seconds=2)
        self.feed(self.command(),[]);self.assertEqual(self.queue.status(),before)
        c=self.command()
        with self.assertRaisesRegex(ValueError,'实际任务ID'):self.feed(c,[{**task,'id':'10000010'}],document='another-recheck')
        self.assertEqual(self.queue.status(),before);self.assertEqual(self.posts,[])

    def test_recheck_rejects_unsubmitted_actions_and_money_or_task_id_overrides(self):
        c=self.ready();rid=c['action']['id']
        with self.assertRaisesRegex(ValueError,'已核验'):self.hybrid.recheck({'action_id':rid})
        with self.assertRaises(ValueError):self.hybrid.recheck({'action_id':rid,'task_id':'10000009'})
        with self.assertRaises(ValueError):self.hybrid.recheck({'action_id':rid,'budget':200})
        self.assertEqual(self.queue.status()[0]['state'],'draft');self.assertEqual(self.posts,[])

    def test_forgotten_readback_expires_without_reopening_the_original_write(self):
        rid,_=self.verified_web_action();before=copy.deepcopy(self.queue.status());self.hybrid.recheck({'action_id':rid})
        self.at+=timedelta(minutes=21);self.hybrid.tick()
        self.assertEqual(self.hybrid.status()[0]['state'],'cancelled');self.assertEqual(self.queue.status(),before)
        self.assertFalse(self.hybrid.active(self.account['id']));self.assertEqual(self.posts,[])

    def test_late_preparation_failure_is_recorded_without_allowing_late_success_or_submission(self):
        c=self.ready();rid=c['action']['id'];self.at+=timedelta(seconds=121)
        with self.assertRaisesRegex(ValueError,'已过期'):self.feed(c,self.page_materials())
        with self.assertRaisesRegex(ValueError,'已过期'):self.begin_web(c)
        self.fail_command(c,'准备步骤耗时过长，未提交')
        action=next(a for a in self.queue.status() if a['id']==rid);self.assertEqual(action['state'],'cancelled')
        self.assertIn('耗时过长',self.hybrid.status()[0]['last_error']['raw_message']);self.assertEqual(self.posts,[])

    def test_an_expired_replaced_lease_cannot_report_failure_against_the_new_command(self):
        old=self.ready();self.at+=timedelta(seconds=121);current=self.command()
        self.assertNotEqual(old['id'],current['id'])
        with self.assertRaisesRegex(ValueError,'身份不符'):self.fail_command(old,'旧等待结束')
        self.assertEqual(self.queue.status()[0]['state'],'draft');self.assertEqual(self.posts,[])

    def test_reloaded_worker_immediately_resumes_only_the_original_readback_with_a_new_lease(self):
        c=self.ready();rid=c['action']['id'];permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':rid,'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2);old=self.command();self.worker['epoch']='reloaded-epoch';self.worker['armed']=False
        current=self.command();self.assertNotEqual(current['id'],old['id']);self.assertEqual(current['phase'],'verify')
        self.assertEqual(current['action']['id'],rid)
        with self.assertRaisesRegex(ValueError,'身份不符'):self.fail_command(old,'旧页面只读响应')
        self.assertEqual(self.queue.status()[0]['state'],'submitting');self.assertEqual(self.posts,[])

    def test_repeated_same_read_failure_pauses_instead_of_retrying_forever_and_manual_retry_recovers(self):
        # This case exercises 50 minutes of backoff on one statistics day;
        # crossing midnight is covered separately and must not depend on wall time.
        self.at=self.at.replace(hour=10,minute=0,second=0)
        aid=self.account['id'];grant=copy.deepcopy(self.queue.auto.row(aid));self.hybrid.request(aid,reason='只读诊断',preview=True)
        for _ in range(5):
            c=self.command();self.fail_command(c,'等待可见账户ID');self.at+=timedelta(minutes=10)
        self.assertTrue(self.hybrid.status()[0]['auto_retry_paused'])
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.hybrid.retry({'account_id':aid});c=self.command();self.assertEqual(c['recovery_cycle'],1)
        self.feed(c,self.tasks());self.assertFalse(self.hybrid.status()[0]['auto_retry_paused'])
        self.assertEqual(self.queue.auto.row(aid),grant);self.assertEqual(self.posts,[])

    def test_failure_counter_resets_when_actual_cause_changes(self):
        self.hybrid.request(self.account['id'],reason='只读诊断',preview=True)
        self.fail_command(self.command(),'等待可见账户ID');self.at+=timedelta(minutes=6)
        self.fail_command(self.command(),'当前页面有编辑窗口，保留输入')
        self.assertEqual(self.hybrid.status()[0]['last_error']['attempt'],1)
        self.assertEqual(self.queue.status(),[])

    def test_stale_preview_batch_rescans_instead_of_retrying_the_same_wrong_page(self):
        aid=self.account['id'];grant=copy.deepcopy(self.queue.auto.row(aid))
        self.materials_data=[('89581211092803411',400,4),('89041279016210106',300,4.2)]
        self.hybrid.request(aid,reason='只读诊断',preview=True)
        self.feed(self.command(),self.tasks());self.feed(self.command(),self.page_materials())
        c=self.command();self.assertEqual(c['phase'],'dry_prepare');self.fail_command(c,'本批素材不在当前页')
        state=self.hybrid.status()[0];self.assertEqual(state['phase'],'materials');self.assertEqual(state['batches_ready'],0)
        self.hybrid.retry({'account_id':aid});c=self.command();self.assertEqual(c['page'],1)
        self.assertEqual(self.queue.auto.row(aid),grant);self.assertEqual(self.posts,[]);self.assertEqual(self.queue.status(),[])

    def test_stale_real_batch_cancels_only_its_unsubmitted_draft_before_rescanning(self):
        c=self.ready();action_id=c['action']['id'];self.fail_command(c,'本批素材不在当前页')
        state=self.hybrid.status()[0];self.assertEqual(state['phase'],'materials');self.assertEqual(state['batches_ready'],0)
        action=next(a for a in self.queue.status() if a['id']==action_id)
        self.assertEqual(action['state'],'cancelled');self.assertEqual(self.posts,[])

    def test_older_extension_cannot_receive_prepare_command_after_adapter_update(self):
        self.worker['extension_version']='1.7.6';self.materials_data=[('89581211092803411',400,4),('89041279016210106',300,4.2)]
        self.hybrid.request(self.account['id'],reason='原规则继续运行')
        self.feed(self.command(),self.tasks());self.feed(self.command(),self.page_materials());self.feed(self.command(),self.tasks())
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertIn('1.8.0',self.hybrid.status()[0]['message'])
        self.worker['extension_version']='1.8.0';self.assertEqual(self.command()['phase'],'prepare');self.assertEqual(self.posts,[])

    def fail_command(self,c,message='Cannot access contents of the page. Extension manifest must request permission to access the respective host.'):
        return self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':message,'observation':None})

    def test_site_denial_is_explained_and_cooled_without_an_ad_or_reservation(self):
        self.hybrid.request(self.account['id'],reason='预演',preview=True)
        c=self.command();self.fail_command(c)
        state=self.hybrid.status()[0]
        self.assertEqual(state['last_error']['code'],'page_access_denied')
        self.assertEqual(state['last_error']['phase'],'tasks')
        self.assertIn('网站访问权限',state['last_error']['hint'])
        self.assertEqual(state['retry_at'],(self.at+timedelta(minutes=5)).isoformat())
        self.assertTrue(state['retry_available'])
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_local_retry_releases_backoff_and_success_clears_error_without_changing_grant(self):
        aid=self.account['id'];grant=copy.deepcopy(self.queue.auto.row(aid))
        self.hybrid.request(aid,reason='预演',preview=True);self.fail_command(self.command())
        self.hybrid.retry({'account_id':aid})
        c=self.command();self.assertEqual(c['phase'],'tasks');self.feed(c,self.tasks())
        state=self.hybrid.status()[0]
        self.assertIsNone(state['last_error']);self.assertIsNone(state['retry_at'])
        self.assertEqual(state['tasks_read'],1);self.assertEqual(state['task_total'],1)
        self.assertEqual(state['phase'],'materials');self.assertEqual(self.queue.auto.row(aid),grant)
        self.assertEqual(self.posts,[])

    def test_retry_does_not_invalidate_an_active_lease_or_accept_another_account(self):
        aid=self.account['id'];self.hybrid.request(aid,reason='预演',preview=True);c=self.command()
        with self.assertRaisesRegex(ValueError,'仍在执行'):self.hybrid.retry({'account_id':aid})
        self.assertEqual(self.command()['id'],c['id'])
        with self.assertRaises(ValueError):self.hybrid.retry({'account_id':'999'})
        self.assertEqual(self.queue.status(),[])

    def test_retry_of_consumed_submit_permit_only_verifies_original(self):
        c=self.ready();permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.hybrid.retry({'account_id':self.account['id']})
        verify=self.command()
        self.assertEqual(verify['phase'],'verify');self.assertEqual(verify['action']['id'],c['action']['id'])
        self.assertEqual(self.queue.status()[0]['state'],'submitting')
        self.assertEqual(self.queue.auto.daily(self.account['id']),1)
        self.assertEqual(self.posts,[])

    def test_a_waiting_account_does_not_starve_another_accounts_read_only_run(self):
        aid=self.account['id'];self.hybrid.request(aid,reason='预演',preview=True);self.fail_command(self.command())
        second={**self.account,'id':'8929253702525378','tab_group':'第二户'}
        self.queue.accounts=lambda:[self.account,second]
        self.api.plan_readings[second['id']]=copy.deepcopy(self.api.plan_readings[aid])
        self.queue.auto.configure({'account_id':second['id'],'plan_id':self.pid,'profile_revision':'r1',
            'enabled':False,'active_budget_cap':None,'max_new_batches_per_day':None,'confirmed':False})
        self.hybrid.request(second['id'],reason='第二户预演',preview=True)
        c=self.command();self.assertEqual(c['account_id'],second['id'])
        self.assertFalse(self.queue.auto.row(second['id'])['enabled']);self.assertEqual(self.posts,[])

    def test_preview_can_use_main_rule_plan_without_creating_f5_money_authority(self):
        aid=self.account['id']
        with self.queue.db() as db:db.execute('DELETE FROM api_auto WHERE account_id=?',(aid,))
        self.queue.rule_auto.configure({'account_id':aid,'plan_id':self.pid,'profile_revision':'r1',
            'features':['f1','f2','f3','f4','f5','f6'],'enabled':False,'confirmed':False})
        self.worker['armed']=False;self.ready(preview=True)
        self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertEqual(self.queue.auto.status(),[])
        self.assertFalse(self.queue.rule_auto.row(aid)['enabled'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_read_only_preview_wait_does_not_block_authorized_api_execution(self):
        aid=self.account['id'];self.hybrid.request(aid,reason='只读诊断',preview=True)
        self.assertTrue(self.hybrid.active(aid));self.assertFalse(self.hybrid.active(aid,writes_only=True))
        self.at+=timedelta(seconds=2);self.queue.auto.tick()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.queue.status()[0]['state'],'verified')
        self.assertTrue(self.hybrid.active(aid));self.assertTrue(self.queue.auto.row(aid)['enabled'])

    def test_real_browser_run_still_blocks_api_creation(self):
        aid=self.account['id'];self.hybrid.request(aid,reason='正式补位')
        self.assertTrue(self.hybrid.active(aid,writes_only=True))
        self.at+=timedelta(seconds=2);self.queue.auto.tick()
        self.assertEqual(self.posts,[]);self.assertEqual(self.queue.auto.row(aid)['state'],'browser_wait')

    def test_old_rows_after_page_number_changes_refresh_only_current_page_and_keep_completed_pages(self):
        self.hybrid.request(self.account['id'],reason='翻页预演',preview=True)
        self.feed(self.command(),self.tasks())
        def rows(offset):
            return [{'id':str(89581211092803411+offset+n),'name':'素材','spend':str(500-offset-n),'roi':'4',
                'chase_status':'-','approved':True,'selectable':True} for n in range(100)]
        page1=self.command();self.feed(page1,rows(0),total=300)
        page2=self.command();self.assertEqual(page2['page'],2)
        self.feed(page2,rows(0),total=300)
        refreshed=self.command()
        self.assertEqual(refreshed['page'],2);self.assertEqual(refreshed['document_cycle'],1)
        self.assertEqual(self.hybrid.status()[0]['materials_read'],100)
        self.feed(refreshed,rows(100),total=300)
        self.assertEqual(self.hybrid.status()[0]['materials_read'],200)
        self.assertEqual(self.command()['page'],3);self.assertEqual(self.posts,[])

    def test_start_reuses_existing_authority_and_is_idempotent_without_posting_directly(self):
        aid=self.account['id'];before=copy.deepcopy(self.queue.auto.row(aid))
        first=self.hybrid.start({'account_id':aid});second=self.hybrid.start({'account_id':aid})
        self.assertEqual(first['run_id'],second['run_id']);self.assertTrue(second['already_running'])
        self.assertFalse(self.hybrid.status()[0]['preview']);self.assertEqual(self.command()['phase'],'tasks')
        self.assertEqual(self.queue.auto.row(aid),before);self.assertEqual(self.posts,[])

    def test_start_cannot_turn_a_preview_into_an_ad_or_accept_client_money(self):
        aid=self.account['id'];self.hybrid.request(aid,reason='预演',preview=True)
        with self.assertRaisesRegex(ValueError,'预演仍在进行'):self.hybrid.start({'account_id':aid})
        with self.assertRaises(ValueError):self.hybrid.start({'account_id':aid,'budget':1})
        self.assertTrue(self.hybrid.status()[0]['preview']);self.assertEqual(self.queue.status(),[])

    def test_start_requires_enabled_f5_saved_browser_mode_and_a_current_armed_bridge(self):
        aid=self.account['id'];self.worker['armed']=False;self.hybrid.worker(self.worker)
        with self.assertRaisesRegex(ValueError,'网页执行未开启'):self.hybrid.start({'account_id':aid})
        self.hybrid.configure({'account_id':aid,'mode':'api_only'})
        with self.assertRaisesRegex(ValueError,'网页通道'):self.hybrid.start({'account_id':aid})
        self.queue.auto.disable({'account_id':aid})
        with self.assertRaisesRegex(ValueError,'授权尚未启用'):self.hybrid.start({'account_id':aid})
        self.assertEqual(self.posts,[])
