import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from monitor.cruise_runtime import CruiseRuntime
from tests.test_cruise_rules import NOW,ACCOUNT,PLAN


class AccountEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'runtime.db';self.now=NOW
        self.accounts=[{'id':ACCOUNT,'name':'甲','scene':'live','tab_group':'甲组','observe':True},{'id':'202','name':'乙','scene':'product','tab_group':'乙组','observe':True}]
        self.runtime=self.build()
    def build(self):return CruiseRuntime(self.path,lambda:self.accounts,lambda aid:[],clock=lambda:self.now)
    def tearDown(self):self.tmp.cleanup()
    def job(self):return self.runtime.worker_jobs('scope',True)['jobs'][0]
    def receipt(self,job,**changes):
        entry={'code':'ready','account_id':ACCOUNT,'group':'甲组','url':f'https://qianchuan.jinritemai.com/uni-prom/detail?aavid={ACCOUNT}&adId={PLAN}&ct=1#uniDetail=%7B%22tb%22%3A%22creative%22%7D',
               'plan_id':PLAN,'captured_at':self.now.isoformat(),'tab_id':10,'document_id':'native-doc','diagnostics':{}}
        entry.update(changes);return {'id':job['id'],'ok':entry['code']=='ready','message':'只读账户进入','entry':entry}
    def test_both_lanes_share_one_entry_job_then_continue_from_verified_parent(self):
        self.runtime.control({'account_id':ACCOUNT,'lane':'main','running':True});self.runtime.control({'account_id':ACCOUNT,'lane':'auto','running':True})
        jobs=self.runtime.worker_jobs('scope',True)['jobs'];self.assertEqual(len(jobs),1);self.assertEqual(jobs[0]['job_kind'],'account_entry')
        self.assertEqual(self.runtime.job_result(self.receipt(jobs[0]))['ad_changes'],0)
        jobs=self.runtime.worker_jobs('scope',True)['jobs'];self.assertEqual({j['job_kind'] for j in jobs},{'material_scan'})
        # Material acquisition holds the same account's main view temporarily.
        self.assertEqual(parse_qs(urlsplit(jobs[0]['url']).query)['adId'],[PLAN])
        self.runtime=self.build();self.assertEqual(self.runtime.status()['accounts'][0]['entry']['plan_id'],PLAN)
        self.assertEqual(self.runtime.status()['accounts'][0]['bindings'],{'control_page_url':'','material_page_url':''})
    def test_manual_read_enters_by_id_then_queues_today_data_without_enabling_a_lane(self):
        self.runtime.request_data(ACCOUNT);job=self.job();self.assertEqual(job['job_kind'],'account_entry')
        self.runtime.job_result(self.receipt(job));jobs=self.runtime.worker_jobs('scope',True)['jobs']
        self.assertEqual([j['job_kind'] for j in jobs],['plan_data'])
        self.assertFalse(any(l['running'] for l in self.runtime.status()['accounts'][0]['lanes'].values()))
    def test_other_account_group_parent_and_stale_receipts_cannot_create_a_route(self):
        self.runtime.request_account_entry(ACCOUNT);job=self.job()
        for changes in ({'account_id':'202'},{'group':'乙组'},{'plan_id':'9999999'},{'url':'https://evil.example/'},{'captured_at':(self.now-timedelta(minutes=2)).isoformat()}):
            with self.assertRaises(ValueError):self.runtime.job_result(self.receipt(job,**changes))
        self.assertEqual(self.job()['id'],job['id']);self.assertEqual(self.runtime.status()['accounts'][0]['entry']['state'],'queued')
    def test_loading_reuses_one_job_and_expiry_remains_bounded(self):
        self.runtime.request_account_entry(ACCOUNT);job=self.job()
        self.runtime.job_result(self.receipt(job,code='loading',url=None,plan_id=None))
        self.runtime.request_account_entry(ACCOUNT,'auto');self.assertEqual(len(self.runtime.worker_jobs('scope',True)['jobs']),1)
        self.now+=timedelta(minutes=11);self.assertEqual(self.runtime.worker_jobs('scope',True)['jobs'],[])
    def test_disabled_account_and_unknown_account_are_not_entered(self):
        self.accounts[0]['observe']=False
        with self.assertRaises(ValueError):self.runtime.request_account_entry(ACCOUNT)
        with self.assertRaises(ValueError):self.runtime.request_account_entry('999')
    def test_product_entry_keeps_its_own_id_and_queues_separate_views(self):
        self.runtime.request_account_entry('202');job=self.job()
        receipt=self.receipt(job,account_id='202',group='乙组',url='https://qianchuan.jinritemai.com/overall-prom?aavid=202',plan_id=None)
        self.runtime.job_result(receipt);jobs=self.runtime.worker_jobs('scope',True)['jobs']
        self.assertEqual(len(jobs),2);self.assertTrue(all(j['job_kind']=='plan_data' and j['account_id']=='202' for j in jobs))
    def test_blocking_editor_is_reported_without_a_ready_route(self):
        self.runtime.request_account_entry(ACCOUNT);job=self.job()
        self.runtime.job_result(self.receipt(job,code='editor_open',url=None,plan_id=None))
        self.assertEqual(self.runtime.worker_jobs('scope',True)['jobs'],[])
        status=self.runtime.status()['accounts'][0]['entry'];self.assertEqual(status['state'],'failed');self.assertEqual(status['code'],'editor_open')

if __name__=='__main__':unittest.main()
