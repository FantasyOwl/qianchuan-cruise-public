import json
import os
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs

from monitor.official_api import OfficialAPI, OfficialTransport, CredentialVault, PATHS, ACCOUNT_FIELDS, exact_id


class OfficialAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.at = 10000
        self.calls = []
        self.vault = CredentialVault(self.root/'test-vault', protect=lambda x: b'test:'+x, unprotect=lambda x: x[5:])
        self.api = OfficialAPI(self.root, vault=self.vault, transport=self.transport, clock=lambda: self.at)

    def tearDown(self):
        self.tmp.cleanup()

    def transport(self, name, params, token=None):
        self.calls.append((name, params, token))
        if name in {'exchange', 'refresh'}:
            return {'access_token': 'access-private-'+str(len(self.calls)), 'refresh_token': 'refresh-private-'+str(len(self.calls)),
                    'expires_in': 3600, 'refresh_token_expires_in': 86400,
                    'advertiser_ids': [1999999999999002]}
        if name == 'accounts':
            return {'list': [{'account_id': 1999999999999002, 'account_name': '直播户', 'account_type': 'ADVERTISER'}]}
        if name == 'account_report':
            return {'stat_cost_for_roi2': '152.20', 'total_prepay_and_pay_order_roi2': '3.52',
                    'total_pay_order_gmv_include_coupon_for_roi2': None, 'total_pay_order_count_for_roi2': 0}
        if name == 'report_config':
            return {'custom_config_datas':[{'data_topic':params['data_topics'][0],'dimensions':[],
                'metrics':[{'field':k} for k in ACCOUNT_FIELDS]}]}
        if name == 'report_data':
            return {'page_info':{'total_number':1},'rows':[{'dimensions':{},'metrics':{
                'stat_cost_for_roi2':{'Value':152.2,'ValueStr':'152.20'},
                'total_prepay_and_pay_order_roi2':{'Value':3.52},'total_pay_order_count_for_roi2':{'Value':0}}}]}
        raise AssertionError(name)

    def config(self):
        return {'app_id': '8298408104169202', 'secret': 'local-secret-private',
                'authorization_url': 'https://ad.oceanengine.com/openapi/audit/oauth.html?'+urlencode({
                    'app_id': '8298408104169202', 'redirect_uri': self.api.callback_url(8877), 'scope': '[1]'}),
                'scope_ids': ['1', '99']}

    def connect(self):
        self.api.configure(self.config(), 8877)
        started = self.api.start_authorization('直播与图文')
        query = parse_qs(urlsplit(started['authorization_url']).query)
        result = self.api.finish_authorization({'state':query['state'], 'auth_code':['single-use-code']}, started['browser_nonce'])
        return result['grant_id']

    def test_default_is_unconfigured_and_cannot_make_business_post(self):
        status = self.api.status(8877)
        self.assertEqual(status['stage'], 'awaiting_app')
        self.assertFalse(status['business_write_enabled'])
        self.assertFalse(any(method=='POST' for name, (_, method, _) in PATHS.items() if name not in {'exchange', 'refresh'}))
        with self.assertRaises(ValueError):
            OfficialTransport()('submit_task', {})

    def test_transport_returns_cooldown_to_the_scheduler_without_replaying_a_read(self):
        from monitor.official_api import OfficialBusinessError
        now=[100.0];sleeps=[]
        def sleep(seconds):sleeps.append(seconds);now[0]+=seconds
        class Response:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return json.dumps(self.data).encode()
        replies=[{'code':40100,'message':'系统请求频率超限，请稍后重试。'}, {'code':0,'data':{'ok':True}}, {'code':0,'data':{}}]
        class Opener:
            def open(self,*args,**kwargs):return Response(replies.pop(0))
        t=OfficialTransport(monotonic=lambda:now[0],sleeper=sleep)
        with patch('monitor.official_api.urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(OfficialBusinessError) as limited:t('plans',{},'test-token')
            self.assertEqual(limited.exception.origin,'platform');self.assertEqual(len(replies),2)
            with self.assertRaises(OfficialBusinessError) as local:t('plans',{},'test-token')
            self.assertEqual(local.exception.origin,'local_cooldown');self.assertEqual(sleeps,[])
            now[0]+=limited.exception.retry_after+.1
            self.assertTrue(t('plans',{},'test-token')['ok']);self.assertEqual(len(replies),1)
            t('plans',{},'test-token')
        self.assertGreaterEqual(sum(sleeps),6.1-1e-6);self.assertEqual(replies,[])

    def test_oauth_post_is_not_replayed_on_rate_limit(self):
        calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"code":40100,"message":"rate limit"}'
        class Opener:
            def open(self,*args,**kwargs):calls.append(1);return Response()
        with patch('monitor.official_api.urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(ValueError):OfficialTransport()('exchange',{'auth_code':'single-use'})
        self.assertEqual(len(calls),1)

    def test_state_is_browser_bound_one_use_and_grant_type_is_required(self):
        self.api.configure(self.config(), 8877)
        started = self.api.start_authorization('我的账户')
        query = {'state':parse_qs(urlsplit(started['authorization_url']).query)['state'], 'auth_code':['single-use-code']}
        with self.assertRaises(ValueError):
            self.api.finish_authorization(query, 'different-browser')
        self.assertEqual(self.calls, [])
        self.api.finish_authorization(query, started['browser_nonce'])
        self.assertEqual(self.calls[0][1]['grant_type'], 'auth_code')
        with self.assertRaises(ValueError):
            self.api.finish_authorization(query, started['browser_nonce'])
        self.assertEqual(len(self.calls), 1)

    def test_expired_or_newer_authorization_invalidates_old_state(self):
        self.api.configure(self.config(), 8877)
        started = self.api.start_authorization('一')
        query = {'state':parse_qs(urlsplit(started['authorization_url']).query)['state'], 'auth_code':['code']}
        self.api.start_authorization('二')
        with self.assertRaises(ValueError):
            self.api.finish_authorization(query, started['browser_nonce'])
        self.assertEqual(self.calls, [])

    def test_callback_ignores_extra_fields_but_still_checks_cookie_and_one_use_state(self):
        self.api.configure(self.config(),8877)
        started=self.api.start_authorization('兼容回调')
        query={'state':parse_qs(urlsplit(started['authorization_url']).query)['state'],
               'auth_code':['single-use-code'],'scope':['extra-private-scope'],'additional':['private-extra']}
        with self.assertRaises(ValueError):self.api.finish_authorization(query,'wrong-cookie')
        self.assertEqual(self.calls,[])
        self.api.finish_authorization(query,started['browser_nonce'])
        with self.assertRaises(ValueError):self.api.finish_authorization(query,started['browser_nonce'])
        self.assertEqual(len(self.calls),1)

    def test_callback_diagnostics_do_not_store_arbitrary_exception_text(self):
        self.api.record_authorization_result(False,ValueError('private-code-and-token'))
        raw=self.api.authorization_result_path.read_text('utf-8')
        self.assertNotIn('private-code-and-token',raw)
        self.assertEqual(self.api.status(8877)['last_authorization']['reason'],'local_processing_failed')

    def test_configuration_and_status_never_echo_credentials(self):
        gid = self.connect()
        self.api.discover(gid)
        output = json.dumps(self.api.status(8877))
        for secret in ['local-secret-private', 'access-private', 'refresh-private', 'single-use-code']:
            self.assertNotIn(secret, output)
        self.assertEqual(self.api.status(8877)['grants'][0]['accounts'][0]['id'], '1999999999999002')
        unchanged = self.config();unchanged['secret']=''
        self.api.configure(unchanged, 8877)
        self.assertEqual(len(self.api.status(8877)['grants']), 1)
        changed = self.config();changed['scope_ids']=['1']
        self.api.configure(changed, 8877)
        self.assertEqual(self.api.status(8877)['grants'], [])

    def test_callback_redirect_and_app_scope_validation(self):
        for patch in [{'authorization_url':'https://attacker.example/?app_id=8298408104169202'},
                      {'app_id':8298408104169202.0}, {'scope_ids':[]},
                      {'authorization_url':self.config()['authorization_url'].replace('8877', '8866')}]:
            with self.assertRaises(ValueError):
                self.api.configure({**self.config(), **patch}, 8877)
        for value in [True, 1.0, '001', '1e20', '1;2']:
            with self.assertRaises(ValueError):exact_id(value)

    def test_pc_backend_rid_authorization_link_is_preserved_and_browser_bound(self):
        config=self.config()
        config['authorization_url']='https://qianchuan.jinritemai.com/openapi/qc/audit/oauth.html?'+urlencode({
            'app_id':config['app_id'],'state':'your_custom_params','material_auth':'1','rid':'official-backend-test-id'})
        self.api.configure(config,8877)
        started=self.api.start_authorization('PC授权')
        parsed=urlsplit(started['authorization_url']);query=parse_qs(parsed.query)
        self.assertEqual(parsed.hostname,'qianchuan.jinritemai.com')
        self.assertEqual(query['rid'],['official-backend-test-id'])
        self.assertNotEqual(query['state'],['your_custom_params'])
        self.assertEqual(query['scope'],['[1, 99]'])
        self.assertEqual(query['material_auth'],['0'])
        for link in (config['authorization_url'].replace('/openapi/qc/audit/oauth.html','/other/path'),
                     config['authorization_url'].replace('official-backend-test-id',''),
                     config['authorization_url']+'&app_id='+config['app_id'],
                     config['authorization_url']+'&redirect_uri=https%3A%2F%2Fother.example'):
            with self.subTest(link=link),self.assertRaises(ValueError):
                self.api.configure({**config,'authorization_url':link},8877)

    def test_concurrent_refresh_rotates_once_and_saves_before_read(self):
        gid = self.connect();self.at += 3500
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.api.discover(gid), range(2)))
        self.assertTrue(all(r['ok'] for r in results))
        refresh = [c for c in self.calls if c[0]=='refresh']
        self.assertEqual(len(refresh), 1)
        self.assertEqual(refresh[0][1]['grant_type'], 'refresh_token')
        token = self.vault.load()['grants'][gid]['access_token']
        self.assertEqual([c[2] for c in self.calls if c[0]=='accounts'], [token, token])

    def test_business_read_preserves_null_zero_exact_account_and_scene(self):
        gid = self.connect()
        result = self.api.read_account({'id':'1999999999999002', 'scene':'live'}, gid)
        row=result['reading']
        self.assertIsNone(row['data_at'])
        self.assertEqual(row['metrics']['spend'], 152.2)
        self.assertEqual(row['metrics']['orders'], 0)
        self.assertIsNone(row['metrics']['gmv'])
        call = next(c for c in self.calls if c[0]=='account_report')
        self.assertEqual(call[1]['advertiser_id'], 1999999999999002)
        self.assertEqual(call[1]['marketing_goal'], 'LIVE_PROM_GOODS')
        self.assertIn('start_time',call[1]);self.assertIn('stat_cost_for_roi2',call[1]['fields'])
        self.assertEqual(call[1]['adlab_scene'],'UNI_PROJECT')
        product=self.api.read_account({'id':'1999999999999001', 'scene':'product'}, gid)
        self.assertEqual(product['reading']['marketing_goal'], 'VIDEO_PROM_GOODS')
        self.assertEqual(self.calls[-1][1]['data_topic'], 'SITE_PROMOTION_PRODUCT_AD')
        self.assertEqual(result['ad_changes'], 0)
        raw = self.api.cache_path.read_text('utf-8')
        self.assertNotIn('access-private', raw)
        self.assertNotIn('refresh-private', raw)

    def test_both_entries_query_exact_families_without_averaging_roi(self):
        gid=self.connect()
        def report(name,params,token=None):
            if name=='report_config':return self.transport(name,params,token)
            self.calls.append((name,params,token))
            if name=='account_report':return {'stat_cost_for_roi2':20,'total_prepay_and_pay_order_roi2':4}
            self.assertEqual(name,'report_data')
            return {'page_info':{'total_number':1},'rows':[{'dimensions':{},'metrics':{
                'stat_cost_for_roi2':{'Value':60},'total_prepay_and_pay_order_roi2':{'Value':2}}}]}
        self.api.transport=report
        result=self.api.read_account({'id':'1999999999999002','scene':'live','delivery_entry':'both'},gid)
        self.assertIsNone(result['reading'])
        rows=result['readings'];self.assertEqual(len(rows),2)
        self.assertEqual([r['metrics']['roi'] for r in rows],[4,2])
        self.assertEqual([r['delivery_entry'] for r in rows],['uni_promotion','chengfang'])
        self.assertEqual(rows[1]['data_topic'],'OVERALL_ROI_LIVE_AWEME')
        self.assertEqual(next(c[1] for c in self.calls if c[0]=='account_report')['data_period'],'ALL_DATA')
        self.assertEqual(self.calls[-1][1]['data_period'],'ALL_DATA')
        self.assertEqual(len(self.api.status(8877)['readings']),2)
        self.assertEqual(result['ad_changes'],0)
        self.api.read_account({'id':'1999999999999002','scene':'live','delivery_entry':'chengfang'},gid)
        self.assertEqual(len(self.api.status(8877)['readings']),1)

    def test_live_metric_groups_share_cutoff_and_merge_fields_without_aggregation(self):
        gid=self.connect();old=self.transport
        def limited(name,q,token=None):
            if name!='account_report':return old(name,q,token)
            self.assertLessEqual(len(q['fields']),6);self.calls.append((name,q,token))
            return {k:42 if k=='stat_cost_for_roi2' else 3 if k=='total_prepay_and_pay_order_roi2' else 0 for k in q['fields']}
        self.api.transport=limited
        row=self.api.read_account({'id':'1999999999999002','scene':'live'},gid)['reading']
        queries=[q for name,q,_ in self.calls if name=='account_report']
        self.assertEqual(len(queries),3)
        self.assertEqual(len({(q['start_time'],q['end_time'],q['adlab_scene']) for q in queries}),1)
        self.assertEqual([k for q in queries for k in q['fields']],list(ACCOUNT_FIELDS))
        self.assertEqual(row['metrics']['spend'],42);self.assertEqual(row['metrics']['roi'],3)

    def test_background_poll_is_registered_account_only_fair_and_read_only(self):
        self.assertEqual(self.api.poll_next([])['state'],'not_connected')
        gid=self.connect();seen=[]
        self.api.read_plans=lambda a,g,**kw:seen.append(('plans',a['id'],g))
        self.api.read_account=lambda a,g:seen.append(('account',a['id'],g))
        rows=[{'id':'11','scene':'live'},{'id':'22','scene':'product'},{'id':'33','observe':False}]
        self.assertEqual(self.api.poll_next(rows)['account_id'],'11')
        self.assertEqual(self.api.poll_next(rows)['account_id'],'22')
        self.assertEqual(self.api.poll_next(rows)['state'],'idle')
        self.assertEqual([x[1] for x in seen],['11','11','22','22'])
        self.assertTrue(all(x[2]==gid for x in seen))
        self.at+=301;self.assertEqual(self.api.poll_next(rows)['account_id'],'11')
        self.api.read_plans=lambda a,g,**kw:(_ for _ in ()).throw(ValueError('官方接口未成功，业务码：40100'))
        failed=self.api.poll_next(rows)
        self.assertEqual(failed['state'],'partial');self.assertEqual(failed['next_due'],self.at+60)
        self.assertEqual(failed['ad_changes'],0)

    def test_restart_retains_successful_read_interval(self):
        from datetime import datetime,timezone
        self.connect();stamp=datetime.fromtimestamp(self.at,timezone.utc).isoformat()
        self.api.plan_readings={'11':{'captured_at':stamp,'entries':[{'delivery_entry':'uni_promotion'}]}}
        self.api.readings={'11:uni_promotion':{'captured_at':stamp}}
        self.api._save_plans();self.api._save_readings()
        resumed=OfficialAPI(self.root,vault=self.vault,transport=self.transport,clock=lambda:self.at)
        self.assertEqual(resumed.poll_next([{'id':'11'}])['state'],'idle')
        self.assertEqual(resumed.polling['11']['next_due'],self.at+300)

    def test_second_entry_failure_does_not_publish_partial_success(self):
        gid=self.connect()
        self.api.read_account({'id':'1999999999999002','scene':'live'},gid)
        before=self.api.status(8877)['readings']
        def report(name,params,token=None):
            if name=='report_config':raise ValueError('权限尚未开通')
            return {'stat_cost_for_roi2':999}
        self.api.transport=report
        with self.assertRaises(ValueError):
            self.api.read_account({'id':'1999999999999002','scene':'live','delivery_entry':'both'},gid)
        self.assertEqual(self.api.status(8877)['readings'],before)

    def test_empty_entry_is_no_matching_rows_and_never_synthetic_zero(self):
        gid=self.connect()
        def report(name,params,token=None):
            if name=='report_config':return self.transport(name,params,token)
            return {'page_info':{'total_number':0},'rows':[]}
        self.api.transport=report
        row=self.api.read_account({'id':'1999999999999001','scene':'product','delivery_entry':'chengfang'},gid)['reading']
        self.assertEqual(row['data_status'],'no_rows')
        self.assertTrue(all(v is None for v in row['metrics'].values()))

    def test_grouped_or_formatted_only_report_is_not_an_account_summary(self):
        gid=self.connect()
        invalid=[{'page_info':{'total_number':2},'rows':[{'dimensions':{},'metrics':{}}]},
                 {'page_info':{'total_number':1},'rows':[{'dimensions':{'ad_id':'123'},'metrics':{}}]},
                 {'page_info':{'total_number':1},'rows':[{'dimensions':{},'metrics':{'stat_cost_for_roi2':{'ValueStr':'1,234'}}}]}]
        for result in invalid:
            def report(name,params,token=None):
                return self.transport(name,params,token) if name=='report_config' else result
            self.api.transport=report
            with self.subTest(result=result),self.assertRaises(ValueError):
                self.api.read_account({'id':'1999999999999001','scene':'product','delivery_entry':'chengfang'},gid)
        self.assertEqual(self.api.readings,{})

    def test_bad_entry_cannot_request_any_endpoint(self):
        for entry in ('standard',None,{},True):
            with self.subTest(entry=entry),self.assertRaises(ValueError):
                self.api.read_account({'id':'1999999999999002','scene':'live','delivery_entry':entry},'x')
        self.assertEqual(self.calls,[])

    def test_review_status_does_not_pretend_to_have_an_app_id_or_token(self):
        self.api.application_path.write_text(json.dumps({'name':'千川巡航','status':'review',
            'checked_at':'2026-10-27T22:00:00+08:00','scope_ids':['21010000','200000064','22100600','200000037']}),'utf-8')
        status=self.api.status(8877)
        self.assertEqual(status['stage'],'application_review')
        self.assertFalse(status['configured']);self.assertFalse(status['secret_saved'])
        self.assertEqual(status['app_id'],'');self.assertEqual(status['grants'],[])

    def test_background_plans_rate_limit_backs_off_two_minutes_without_slowing_account_reports(self):
        from monitor.official_api import OfficialBusinessError
        self.connect()
        self.api.read_account=lambda a,g:None
        self.api.read_plans=lambda a,g,**kw:(_ for _ in ()).throw(OfficialBusinessError(40100,operation='plans',reason='rate_limited',retry_after=40))
        res=self.api.poll_next([{'id':'11','scene':'live'}])
        self.assertEqual(res['state'],'partial')
        self.assertEqual(res['components']['plans']['state'],'deferred')
        self.assertEqual(res['components']['plans']['next_due'],self.at+120)
        self.assertEqual(res['components']['account']['state'],'ok')

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_real_windows_vault_is_encrypted_and_round_trips(self):
        vault=CredentialVault(self.root/'real.dpapi')
        value={'version':1, 'app':{'secret':'dpapi-test-only'}, 'grants':{}, 'pending':{}}
        vault.save(value)
        self.assertNotIn(b'dpapi-test-only', vault.path.read_bytes())
        self.assertEqual(vault.load(), value)


if __name__ == '__main__':
    unittest.main()
