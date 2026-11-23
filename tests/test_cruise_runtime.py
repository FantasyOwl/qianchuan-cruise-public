import copy
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from contextlib import closing
from urllib.parse import parse_qs,urlsplit,unquote

from monitor.cruise_runtime import CruiseRuntime,view_url
from tests.test_cruise_rules import NOW,ACCOUNT,PLAN,TASK,task


class CruiseRuntimeTests(unittest.TestCase):
    def test_batch_range_save_changes_only_f5_counts_and_preserves_other_accounts(self):
        self.save();a=self.account();before=copy.deepcopy(a['profile']);other=self.account('202')
        p={'account_id':ACCOUNT,'expected_revision':a['revision'],'batch_size_min':2,'batch_size_max':5,'confirmed':True}
        result=self.runtime.save_batch_range(p);after=self.account()
        before['features']['f5'].update(batch_size_min=2,batch_size_max=5,batch_size=5)
        self.assertEqual(after['profile'],before);self.assertEqual(self.account('202'),other)
        self.assertEqual(result['revision'],after['revision']);self.assertEqual(result['ad_changes'],0)
        with self.assertRaises(ValueError):self.runtime.save_batch_range(p)
        for low,high in ((1,5),(5,2),(2,21),(2.5,5),(True,5)):
            with self.assertRaises(ValueError):self.runtime.save_batch_range({**p,'expected_revision':after['revision'],'batch_size_min':low,'batch_size_max':high})
        with self.assertRaises(ValueError):self.runtime.save_batch_range({**p,'confirmed':False})

    def test_api_rate_limit_defers_auto_by_configured_interval_instead_of_one_minute(self):
        from monitor.official_api import OfficialBusinessError
        calls=[]
        self.save()
        def limited(*args):
            calls.append(1)
            raise OfficialBusinessError(40100,operation='plan_materials',reason='rate_limited',retry_after=60)
        self.runtime.official_scanner=limited
        with self.runtime._tx() as db:db.execute('UPDATE cruise_accounts SET auto_running=1 WHERE account_id=?',(ACCOUNT,))
        self.runtime.tick();a=self.account();self.assertEqual(len(calls),1)
        self.assertEqual(a['lanes']['auto']['next_at'],(self.now+timedelta(seconds=600)).isoformat())
        self.runtime.tick();self.assertEqual(len(calls),1)
        logs=[l for l in self.runtime.status()['logs'] if l['event']=='scan_deferred']
        self.assertEqual(logs[0]['detail']['operation'],'plan_materials');self.assertEqual(logs[0]['detail']['ad_changes'],0)

    def test_live_home_read_is_account_scoped_and_temporarily_holds_other_views(self):
        self.save();self.runtime.request_data(ACCOUNT)
        result=self.runtime.request_live_home(ACCOUNT);self.assertEqual(result['ad_changes'],0)
        jobs=self.runtime.worker_jobs('scope',True)['jobs'];self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['job_kind'],'plan_data')
        self.assertEqual(jobs[0]['id'],result['parent_job_id']);self.runtime.job_result({'id':result['parent_job_id'],'ok':True,'message':'计划详情已只读刷新'})
        jobs=self.runtime.worker_jobs('scope',True)['jobs'];self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['job_kind'],'live_home')
        self.assertEqual(parse_qs(urlsplit(jobs[0]['url']).query)['aavid'],[ACCOUNT])
        self.runtime.job_result({'id':result['id'],'ok':True,'message':'已刷新首页'})
        self.assertEqual(self.runtime.worker_jobs('scope',True)['jobs'],[])
        self.now+=timedelta(minutes=6);self.assertTrue(self.runtime.worker_jobs('scope',True)['jobs'])
        with self.assertRaises(ValueError):self.runtime.request_live_home('202')

    def test_live_home_keeps_verified_delivery_view_and_parent_failure_stops_home(self):
        self.save();a=self.account();bindings=dict(a['bindings']);bindings['control_page_url']+='&ct=1#umg=2&uniDetail=%7B%22tb%22%3A%22data%22%7D'
        self.runtime.save({'account_id':ACCOUNT,'expected_revision':a['revision'],'profile':a['profile'],'bindings':bindings})
        result=self.runtime.request_live_home(ACCOUNT);job=self.runtime.worker_jobs('scope',True)['jobs'][0]
        self.assertEqual(job['id'],result['parent_job_id']);self.runtime.job_result({'id':job['id'],'ok':True,'message':'只读刷新成功'})
        home=self.runtime.worker_jobs('scope',True)['jobs'][0];self.assertEqual(dict(parse_qs(urlsplit(home['url']).fragment))['umg'],['2']);self.assertIn('ct=1',home['url']);self.assertNotIn('uniDetail',home['url'])
        result=self.runtime.request_live_home(ACCOUNT);self.runtime.job_result({'id':result['parent_job_id'],'ok':False,'message':'保留编辑窗口'})
        self.assertEqual(self.runtime.worker_jobs('scope',True)['jobs'],[])

    def test_parent_mapping_needs_recent_explicit_detail_scope_and_unique_owner(self):
        self.save();row={'account_id':ACCOUNT,'entity_id':PLAN,'scope':'plan','context':{'aweme_id':'79987870170'},'source':{'kind':'browser_dom','url':'https://qianchuan.jinritemai.com/uni-prom/detail?aavid='+ACCOUNT,'captured_at':self.now.isoformat()}}
        self.rows.append(row);maps=self.runtime.live_parent_mappings();self.assertEqual(len(maps),1);self.assertEqual(maps[0]['plan_id'],PLAN);self.assertEqual(maps[0]['aweme_id'],'79987870170')
        self.rows.append({**row,'context':{'aweme_id':'79987870171'}});self.assertEqual(self.runtime.live_parent_mappings(),[]);self.rows.pop()
        self.now+=timedelta(minutes=11);self.assertEqual(self.runtime.live_parent_mappings(),[])

    def test_detail_observations_cannot_complete_a_homepage_job(self):
        self.save();result=self.runtime.request_live_home(ACCOUNT);self.now+=timedelta(seconds=1)
        self.runtime.observe_live_home([{'account_id':ACCOUNT,'plan_id':PLAN,'path':'/uni-prom','captured_at':self.now.isoformat()}])
        with self.runtime._tx() as db:self.assertEqual(db.execute('select state from cruise_jobs where id=?',(result['id'],)).fetchone()[0],'queued')
        control=task();control['source']['captured_at']=self.now.isoformat();self.runtime.observations_arrived([control])
        with self.runtime._tx() as db:self.assertEqual(db.execute('select state from cruise_jobs where id=?',(result['id'],)).fetchone()[0],'queued')
        self.runtime.job_result({'id':result['parent_job_id'],'ok':True,'message':'详情已刷新'});self.runtime.job_result({'id':result['id'],'ok':True,'message':'首页已刷新'})
        self.runtime.observe_live_home([{'account_id':ACCOUNT,'plan_id':'1234567','path':'/uni-prom','captured_at':self.now.isoformat()}])
        with self.runtime._tx() as db:self.assertEqual(db.execute('select state from cruise_jobs where id=?',(result['id'],)).fetchone()[0],'navigated')
        self.runtime.observe_live_home([{'account_id':ACCOUNT,'plan_id':PLAN,'path':'/uni-prom','captured_at':self.now.isoformat()}])
        with self.runtime._tx() as db:self.assertEqual(db.execute('select state from cruise_jobs where id=?',(result['id'],)).fetchone()[0],'completed')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'cruise.db'
        self.now=NOW
        self.accounts=[{'id':ACCOUNT,'name':'甲直播','scene':'live','observe':True,'tab_group':'甲组'},
                       {'id':'202','name':'乙图文','scene':'product','observe':True,'tab_group':'乙组'},
                       {'id':'303','name':'丙直播','scene':'live','observe':True,'tab_group':'丙组'}]
        self.rows=[task()];self.history=[]
        self.runtime=self.build()

    def build(self):
        return CruiseRuntime(self.path,lambda:self.accounts,
            lambda aid:copy.deepcopy([r for r in self.rows if r['account_id']==aid]),
            clock=lambda:self.now,history_provider=lambda aid:self.history)

    def tearDown(self):self.tmp.cleanup()

    def account(self,aid=ACCOUNT):
        return next(a for a in self.runtime.status()['accounts'] if a['id']==aid)

    def save(self,aid=ACCOUNT,feature='f1',**fields):
        a=self.account(aid);p=a['profile'];p['enabled']=True
        p['features'][feature].update(enabled=True,**({'max_roi':2.25} if feature=='f1' else fields))
        self.runtime.save({'account_id':aid,'expected_revision':a['revision'],'profile':p,
            'bindings':{'control_page_url':f'https://qianchuan.jinritemai.com/uni-prom/detail?aavid={aid}&adId={PLAN}','material_page_url':''}})

    def test_account_configuration_is_independent_and_restart_persistent(self):
        self.save()
        self.assertTrue(self.account()['profile']['features']['f1']['enabled'])
        self.assertFalse(self.account('303')['profile']['features']['f1']['enabled'])
        self.runtime=self.build()
        self.assertEqual(self.account()['profile']['features']['f1']['max_roi'],2.25)
        self.assertIsNone(self.account('202')['profile']['features']['f1']['max_roi'])

    def test_running_material_opt_in_is_saved_per_account_and_survives_restart(self):
        self.save(feature='f5',allow_running_materials=True)
        self.runtime=self.build()
        self.assertTrue(self.account()['profile']['features']['f5']['allow_running_materials'])
        self.assertFalse(self.account('303')['profile']['features']['f5']['allow_running_materials'])
        self.assertFalse(self.account('202')['profile']['features']['f5']['allow_running_materials'])

    def test_manual_scan_produces_only_candidates_and_does_not_start_lanes(self):
        self.save();r=self.runtime.scan(ACCOUNT)
        self.assertEqual(len(r['result']['proposals']),1)
        self.assertEqual(r['ad_changes'],0)
        self.assertFalse(self.account()['lanes']['main']['running'])
        self.assertTrue(self.runtime.worker_jobs('rev',True)['navigation_allowed'])
        self.assertEqual(len(self.runtime.worker_jobs('rev',False)['jobs']),1)
        self.assertFalse(self.runtime.worker_jobs('rev',False)['navigation_allowed'])

    def test_auto_reads_material_view_from_verified_parent_when_material_binding_is_empty(self):
        self.save()
        a=self.account();a['bindings']['control_page_url']+='&ct=1#uniDetail=%7B%22bcf%22%3A%22retained%22%7D'
        self.runtime.save({'account_id':ACCOUNT,'expected_revision':a['revision'],'profile':a['profile'],'bindings':a['bindings']})
        result=self.runtime.scan(ACCOUNT,'auto')
        self.assertTrue(result['collection_requested']);self.assertEqual(result['ad_changes'],0)
        self.assertFalse(self.account()['lanes']['auto']['running'])
        job=next(j for j in self.runtime.worker_jobs('rev',True)['jobs'] if j['lane']=='auto')
        u=urlsplit(job['url']);q=parse_qs(u.query);detail=__import__('json').loads(parse_qs(u.fragment)['uniDetail'][0])
        self.assertEqual(q['aavid'],[ACCOUNT]);self.assertEqual(q['adId'],[PLAN]);self.assertEqual(q['ct'],['1'])
        self.assertEqual(detail['bcf'],'retained');self.assertEqual(detail['tb'],'creative')
        self.assertEqual(__import__('json').loads(detail['cc']),{'p':'1','ps':'100','sf':'stat_cost','st':'desc'})
        self.assertEqual(q['dr'],['2026-10-21,2026-10-25'])
        self.assertEqual(self.account()['bindings']['material_page_url'],'')

    def test_read_identity_loading_retries_within_expiry_but_editor_failure_does_not(self):
        self.save();self.runtime.scan(ACCOUNT,'auto')
        job=self.runtime.worker_jobs('rev',True)['jobs'][0]
        message='页面账户标识核验尚未通过，未刷新；请等待页面加载后重新扫描'
        self.assertEqual(self.runtime.job_result({'id':job['id'],'ok':False,'message':message})['ad_changes'],0)
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'][0]['id'],job['id'])
        self.now+=timedelta(minutes=11)
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])
        self.runtime.scan(ACCOUNT,'auto');job=self.runtime.worker_jobs('rev',True)['jobs'][0]
        self.runtime.job_result({'id':job['id'],'ok':False,'message':'当前页面有编辑窗口，未刷新以保留输入'})
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])

    def test_auto_explicit_material_binding_keeps_its_own_verified_parent(self):
        self.save();a=self.account();other='8596950771049743213'
        a['bindings']['material_page_url']=f'https://qianchuan.jinritemai.com/uni-prom/detail?aavid={ACCOUNT}&adId={other}&ct=1'
        self.runtime.save({'account_id':ACCOUNT,'expected_revision':a['revision'],'profile':a['profile'],'bindings':a['bindings']})
        self.runtime.scan(ACCOUNT,'auto')
        job=next(j for j in self.runtime.worker_jobs('rev',True)['jobs'] if j['lane']=='auto')
        self.assertEqual(parse_qs(urlsplit(job['url']).query)['adId'],[other])

    def test_auto_without_any_parent_queues_account_id_entry(self):
        result=self.runtime.scan(ACCOUNT,'auto')
        self.assertTrue(result['collection_requested'])
        self.assertIn('account_entry_pending',[d['status'] for d in result['result']['diagnostics']])
        jobs=self.runtime.worker_jobs('rev',True)['jobs'];self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['job_kind'],'account_entry');self.assertEqual(parse_qs(urlsplit(jobs[0]['url']).query)['aavid'],[ACCOUNT])

    def test_locate_original_batch_returns_exact_parent_range_and_page_without_rule_changes(self):
        self.save();before=self.account()['profile'];intent={'account_id':ACCOUNT,'plan_id':PLAN,'date_start':'2026-10-21','date_end':'2026-10-25','surface':{'page':2,'group':'甲组'}}
        self.runtime.scan(ACCOUNT,'auto');r=self.runtime.locate_material_batch(intent);self.assertEqual(r['ad_changes'],0)
        job=self.runtime.worker_jobs('rev',True)['jobs'][0];u=urlsplit(job['url']);d=__import__('json').loads(parse_qs(u.fragment)['uniDetail'][0])
        self.assertEqual(__import__('json').loads(d['cc'])['p'],'2');self.assertEqual(parse_qs(u.query)['adId'],[PLAN]);self.assertEqual(self.account()['profile'],before)
        with self.assertRaises(ValueError):self.runtime.locate_material_batch({**intent,'plan_id':'1234567'})

    def test_material_acquisition_waits_after_navigation_and_holds_main_until_review_window_ends(self):
        self.save();self.runtime.control({'account_id':ACCOUNT,'lane':'main','running':True})
        self.runtime.control({'account_id':ACCOUNT,'lane':'auto','running':True})
        jobs=self.runtime.worker_jobs('rev',True)['jobs'];self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['job_kind'],'material_scan')
        self.runtime.job_result({'id':jobs[0]['id'],'ok':True,'message':'只读进入素材'})
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])
        self.now+=timedelta(seconds=2)
        self.runtime.observe_material_pages([{'account_id':ACCOUNT,'plan_id':PLAN,'editor_open':False,'date_start':'2026-10-21','date_end':'2026-10-25','captured_at':self.now.isoformat(),'page':1,'page_size':100,'sort':'spend_desc'}])
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])
        self.now+=timedelta(seconds=121)
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'][0]['job_kind'],'control')

    def test_material_read_repeats_only_on_explicit_rescan_not_automatic_round(self):
        self.save();self.runtime.scan(ACCOUNT,'auto');job=self.runtime.worker_jobs('rev',True)['jobs'][0]
        self.runtime.job_result({'id':job['id'],'ok':True,'message':'已刷新素材页'})
        self.runtime.scan(ACCOUNT,'auto');self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])
        self.runtime.scan(ACCOUNT,'auto',force_read=True);self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'][0]['id'],job['id'])

    def test_original_batch_page_waits_for_navigation_and_matching_actual_page(self):
        self.save();intent={'account_id':ACCOUNT,'plan_id':PLAN,'date_start':'2026-10-21','date_end':'2026-10-25','surface':{'page':2,'group':'甲组'}}
        request=self.runtime.locate_material_batch(intent);self.now+=timedelta(seconds=1)
        surface={'account_id':ACCOUNT,'plan_id':PLAN,'editor_open':False,'date_start':'2026-10-21','date_end':'2026-10-25','captured_at':self.now.isoformat(),'page':2,'page_size':100,'sort':'spend_desc'}
        self.runtime.observe_material_pages([surface]);self.assertTrue(self.runtime.worker_jobs('scope',True)['jobs'])
        self.runtime.job_result({'id':request['id'],'ok':True,'message':'已导航第二页'})
        self.runtime.observe_material_pages([{**surface,'page':1}])
        with self.runtime._tx() as db:self.assertEqual(db.execute('select state from cruise_jobs where id=?',(request['id'],)).fetchone()[0],'navigated')
        self.runtime.scan(ACCOUNT,'auto')
        with self.runtime._tx() as db:self.assertEqual(db.execute('select state from cruise_jobs where id=?',(request['id'],)).fetchone()[0],'navigated')
        self.runtime.observe_material_pages([surface])
        with self.runtime._tx() as db:self.assertEqual(db.execute('select state from cruise_jobs where id=?',(request['id'],)).fetchone()[0],'completed')

    def test_material_acquisition_never_holds_another_accounts_main_view(self):
        self.save();self.save('303');self.runtime.scan(ACCOUNT,'auto');self.runtime.scan('303','main')
        jobs=self.runtime.worker_jobs('rev',True)['jobs']
        self.assertEqual(jobs[0]['job_kind'],'material_scan')
        self.assertTrue(any(j['account_id']=='303' and j['job_kind']=='control' for j in jobs))

    def test_lanes_accounts_and_desired_state_are_independent(self):
        self.save();self.runtime.control({'account_id':ACCOUNT,'lane':'main','running':True})
        self.assertTrue(self.account()['lanes']['main']['running'])
        self.assertFalse(self.account()['lanes']['auto']['running'])
        self.assertFalse(self.account('303')['lanes']['main']['running'])
        self.runtime=self.build();self.assertTrue(self.account()['lanes']['main']['running'])
        self.runtime.control({'account_id':ACCOUNT,'lane':'main','running':False})
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])

    def test_stale_profile_and_cross_account_bindings_are_rejected(self):
        a=self.account();self.save()
        with self.assertRaisesRegex(ValueError,'已改变'):
            self.runtime.save({'account_id':ACCOUNT,'expected_revision':a['revision'],'profile':a['profile'],'bindings':a['bindings']})
        a=self.account('303');a['bindings']['control_page_url']=f'https://qianchuan.jinritemai.com/uni-prom/detail?aavid={ACCOUNT}&adId={PLAN}'
        with self.assertRaises(ValueError):self.runtime.save({'account_id':'303','expected_revision':a['revision'],'profile':a['profile'],'bindings':a['bindings']})

    def test_rule_draft_is_rechecked_for_current_values_and_configuration(self):
        self.save();p=self.runtime.scan(ACCOUNT)['result']['proposals'][0]
        fresh=self.runtime.proposal(ACCOUNT,p['id'])
        intent={'account_id':ACCOUNT,'plan_id':PLAN,'task_id':TASK,'action':'control_pause','before':True,'after':False}
        self.runtime.bind_action('action-a',fresh);self.runtime.validate_action('action-a',intent)
        self.rows[0]['metrics']['roi']=4
        with self.assertRaisesRegex(ValueError,'不满足'):self.runtime.validate_action('action-a',intent)
        self.rows[0]['metrics']['roi']=2;self.save()
        # Same effective configuration retains the same revision; change it.
        a=self.account();a['profile']['features']['f1']['max_roi']=2.5
        self.runtime.save({'account_id':ACCOUNT,'expected_revision':a['revision'],'profile':a['profile'],'bindings':a['bindings']})
        with self.assertRaisesRegex(ValueError,'参数已改变'):self.runtime.validate_action('action-a',intent)

    def test_snapshot_id_refresh_does_not_lose_an_unchanged_candidate(self):
        self.save();p=self.runtime.scan(ACCOUNT)['result']['proposals'][0]
        self.rows[0]['id']='new-capture';self.rows[0]['source']['captured_at']=(self.now+timedelta(seconds=1)).isoformat()
        self.now+=timedelta(seconds=1)
        self.assertEqual(self.runtime.proposal(ACCOUNT,p['id'])['entity_id'],TASK)

    def test_jobs_complete_only_after_parent_account_scope_and_time_match(self):
        self.save();self.runtime.scan(ACCOUNT)
        job=self.runtime.worker_jobs('rev',True)['jobs'][0]
        self.runtime.job_result({'id':job['id'],'ok':True,'message':'已导航，尚未确认采集'})
        old=copy.deepcopy(self.rows);self.runtime.observations_arrived(old)
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])
        # Navigated is hidden, but it is not completed until later observation.
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT state FROM cruise_jobs').fetchone()[0],'navigated')
        self.now+=timedelta(seconds=1);self.rows[0]['source']['captured_at']=self.now.isoformat()
        wrong=copy.deepcopy(self.rows);wrong[0]['source']['parent_entity_id']='999';wrong[0]['context']['parent_plan_id']='999'
        self.runtime.observations_arrived(wrong)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT state FROM cruise_jobs').fetchone()[0],'navigated')
        self.runtime.observations_arrived(self.rows)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT state FROM cruise_jobs').fetchone()[0],'completed')

    def test_operation_window_and_broadcast_protection_prevent_navigation(self):
        self.save();self.now=NOW.replace(hour=3)
        r=self.runtime.scan(ACCOUNT);self.assertFalse(r['collection_requested']);self.assertEqual(r['result']['proposals'],[])
        self.now=NOW;a=self.account();a['profile']['broadcast_protection']={'enabled':True,'start':'11:00','end':'13:00'}
        self.runtime.save({'account_id':ACCOUNT,'expected_revision':a['revision'],'profile':a['profile'],'bindings':a['bindings']})
        self.assertFalse(self.runtime.scan(ACCOUNT)['collection_requested'])
        self.assertFalse(self.runtime.scan(ACCOUNT,'auto')['collection_requested'])

    def test_disabled_or_removed_account_cannot_scan_or_receive_jobs(self):
        self.save();self.runtime.scan(ACCOUNT);self.accounts[0]['observe']=False
        with self.assertRaises(ValueError):self.runtime.scan(ACCOUNT)
        self.assertEqual(self.runtime.worker_jobs('rev',True)['jobs'],[])
        self.accounts.pop(0)
        with self.assertRaises(ValueError):self.runtime.scan(ACCOUNT)

    def test_url_requires_exact_identity_and_uses_real_original_view_names(self):
        url=f'https://qianchuan.jinritemai.com/uni-prom/detail?aavid={ACCOUNT}&adId={PLAN}'
        for lane,view in [('main','uni_task_center'),('auto','creative')]:
            value=view_url(url,ACCOUNT,lane,'2026-10-26')
            self.assertEqual(parse_qs(urlsplit(value).query)['dr'],['2026-10-26,2026-10-26'])
            self.assertIn(view,unquote(urlsplit(value).fragment))
        for bad in [url.replace('https:','http:'),url+'&adId=999',url.replace(ACCOUNT,'303'),url.replace('.com/','.com.evil/')]:
            with self.assertRaises(ValueError):view_url(bad,ACCOUNT,'main','2026-10-26')


    def test_read_only_today_refresh_handles_live_and_product_without_starting_rules(self):
        self.save()
        for aid in [ACCOUNT,'202']:
            result=self.runtime.request_data(aid);self.assertEqual(result['ad_changes'],0)
            self.assertFalse(self.account(aid)['lanes']['main']['running'])
        jobs=self.runtime.worker_jobs('rev',True)['jobs']
        self.assertEqual(len(jobs),3);self.assertTrue(all(j['job_kind']=='plan_data' for j in jobs))
        live=next(j for j in jobs if j['account_id']==ACCOUNT)
        self.assertIn('data',unquote(urlsplit(live['url']).fragment))
        product=next(j for j in jobs if j['account_id']=='202')
        self.assertEqual(parse_qs(urlsplit(product['url']).query)['dateRange'],['2026-10-25,2026-10-25'])

    def test_read_refresh_requires_observed_live_binding_and_exact_account(self):
        entry=self.runtime.request_data('303');self.assertEqual(entry['ad_changes'],0)
        with self.assertRaises(ValueError):self.runtime.request_data('999')

    def test_product_subviews_require_their_own_observation_before_completion(self):
        self.runtime.request_data('202');self.now+=timedelta(seconds=1)
        observed=task(account_id='202',scope='plan',entity_id='999',source={'captured_at':self.now.isoformat(),'metric_date':'2026-10-25'},context={'observed_plan_view':'1'})
        self.runtime.observations_arrived([observed])
        jobs=self.runtime.worker_jobs('rev',True)['jobs']
        self.assertEqual(len(jobs),1);self.assertIn('uniVideoTab=2',jobs[0]['url'])


if __name__=='__main__':unittest.main()
