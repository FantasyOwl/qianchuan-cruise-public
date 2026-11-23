import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
from unittest.mock import patch

from monitor.server import App, Handler, ROOT, Server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OfficialAPIHTTPTests(unittest.TestCase):
    def test_parallel_worker_http_authentication_is_account_scoped_and_admin_controls_need_csrf(self):
        e=self.app.ego_executor;e.exe=Path(__file__)
        p={'mode':'account_parallel','max_parallel':3}
        self.assertEqual(self.request('/api/ego-executor/pool',p)[0],403)
        self.assertEqual(self.request('/api/ego-executor/pool',p,self.ui)[0],200)
        e.configure({'provider':'ego','enabled':True,'armed':False})
        ids=[a['id'] for a in self.app.cfg()['accounts'][:2]];one,two=[e.pool.ensure(aid) for aid in ids]
        path='/api/ego-executor/worker/event'
        self.assertEqual(self.request(path,{'message':'legacy'},{'X-Qianchuan-Ego':e.secret})[0],403)
        self.assertEqual(self.request(path,{'message':'foreign origin'},{'X-Qianchuan-Ego':one.token,'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request(path,{'message':'本户已接管','suspended':True},{'X-Qianchuan-Ego':one.token})[0],200)
        self.assertTrue(one.suspended);self.assertFalse(two.suspended)
        self.assertEqual(self.request('/api/ego-executor/account',{'account_id':ids[0],'operation':'resume'})[0],403)
        self.assertEqual(self.request('/api/ego-executor/account',{'account_id':ids[0],'operation':'resume'},self.ui)[0],200)
        with self.app.official_actions.db() as db:
            db.execute('INSERT INTO hybrid_runs VALUES(?,?,?,?,?,?)',('scope-test',ids[0],'1234567','waiting',e.now(),'{}'))
        code,_,raw=self.request('/api/ego-executor/worker/checkpoint',{'kind':'chase','body':{'run_id':'scope-test','id':'forged','lease':'forged'}},{'X-Qianchuan-Ego':two.token})
        self.assertEqual(code,400);self.assertIn('不属于',json.loads(raw)['error']);self.assertEqual(self.calls,[])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        shutil.copyfile(ROOT/'config.json',root/'config.json')
        self.app=App(root)
        self.server=Server(('127.0.0.1',0),Handler);self.server.app=self.app
        self.app.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.ui={'X-Monitor-CSRF':self.app.csrf}
        self.calls=[]
        def transport(name, params, token=None):
            self.calls.append(name)
            if name=='exchange':return {'access_token':'test-access-not-live','refresh_token':'test-refresh-not-live',
                'expires_in':3600,'refresh_token_expires_in':86400,'advertiser_ids':[]}
            raise AssertionError('Unexpected request')
        self.app.official_api.transport=transport

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,body=None,headers=None):
        request=urllib.request.Request(f'http://127.0.0.1:{self.app.port}'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json',**(headers or {})})
        try:response=urllib.request.build_opener(NoRedirect()).open(request,timeout=5)
        except urllib.error.HTTPError as error:response=error
        with response:return response.status,response.headers,response.read()

    def config(self):
        return {'app_id':'8298408104169202','secret':'test-secret-not-live','scope_ids':['1'],
            'authorization_url':'https://ad.oceanengine.com/openapi/audit/oauth.html?'+urlencode({
                'app_id':'8298408104169202','redirect_uri':self.app.official_api.callback_url(self.app.port)})}

    def test_material_exclusions_are_csrf_protected_and_account_scoped_without_ads(self):
        aid=self.app.cfg()['accounts'][0]['id'];body={'account_id':aid,'material_ids':['8206994744541607698']}
        path='/api/official-api/material-exclusions'
        self.assertEqual(self.request(path,body)[0],403)
        code,_,raw=self.request(path,body,self.ui);self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['material_ids'],body['material_ids'])
        self.assertEqual(self.app.official_actions.material_exclusions(aid),body['material_ids'])
        self.assertEqual(self.app.official_actions.material_exclusions('999'),[])
        self.assertEqual(self.request('/api/official-api/hybrid/start-test',{'account_id':aid})[0],403)
        self.assertEqual(self.calls,[])

    def test_branding_is_local_csrf_protected_and_does_not_touch_account_settings(self):
        from tests.test_branding import PNG
        original=self.app.config_path.read_bytes();body={'name':'自定义工作台','subtitle':'直播与图文','icon_data_url':PNG}
        self.assertEqual(self.request('/api/branding',body)[0],403)
        code,_,raw=self.request('/api/branding',body,self.ui);self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['branding'],body)
        code,_,raw=self.request('/api/branding');self.assertEqual(code,200);self.assertEqual(json.loads(raw)['branding'],body)
        self.assertEqual(self.app.config_path.read_bytes(),original);self.assertEqual(self.calls,[])

    def test_recovery_endpoints_are_csrf_protected_local_and_do_not_change_ads(self):
        aid=self.app.cfg()['accounts'][0]['id'];pid='8218616704149013';tid='10001'
        actions=self.app.official_actions
        intent={'account_id':aid,'plan_id':pid,'task_id':tid,'action':'task_budget','before':600,'after':800}
        with actions.db() as db:
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',
                ('stuck','unknown',json.dumps(intent),'digest','2026-11-10','2026-11-10','2026-11-10','{"message":"unknown"}'))
        path='/api/official-api/actions/hold_task';body={'id':'stuck','account_id':aid}
        self.assertEqual(self.request(path,body)[0],403)
        self.assertEqual(self.request(path,body,{**self.ui,'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request(path,body,self.ui)[0],200)
        self.assertEqual(len(actions.holds(aid,pid)),1)
        self.assertEqual(actions.status()[0]['state'],'unknown')
        self.assertEqual(self.request('/api/official-api/actions/resume_task',
            {'account_id':aid,'plan_id':pid,'task_id':tid},self.ui)[0],200)
        self.assertEqual(actions.holds(),[]);self.assertEqual(self.calls,[])

    def test_activity_history_is_account_scoped_read_only_and_blocks_cross_origin(self):
        aid=self.app.cfg()['accounts'][0]['id']
        path='/api/official-api/actions/history?'+urlencode({'account_id':aid,'state':'verified'})
        code,_,raw=self.request(path)
        self.assertEqual(code,200)
        result=json.loads(raw)
        self.assertEqual(result['account_id'],aid)
        self.assertEqual(result['records'],[])
        self.assertEqual(self.request(path,headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request(path+'&account_id='+aid)[0],400)
        self.assertEqual(self.request(path+'&page_size=1000')[0],400)
        self.assertEqual(self.request('/api/official-api/actions/history?account_id=999')[0],400)
        self.assertEqual(self.calls,[])

    def test_activity_assets_are_served_without_api_calls(self):
        target=self.app.root/'static';target.mkdir(exist_ok=True)
        for name in ('activity-log.js','activity-log.css'):
            shutil.copyfile(ROOT/'static'/name,target/name)
            code,headers,raw=self.request('/'+name)
            self.assertEqual(code,200)
            self.assertEqual(raw,(ROOT/'static'/name).read_bytes())
        self.assertEqual(self.calls,[])

    def test_hybrid_configuration_requires_ui_csrf_and_workers_require_extension_token(self):
        aid=self.app.cfg()['accounts'][0]['id']
        payload={'account_id':aid,'mode':'api_preferred'}
        self.assertEqual(self.request('/api/official-api/hybrid/configure',payload)[0],403)
        self.assertEqual(self.request('/api/official-api/hybrid/configure',payload,self.ui)[0],200)
        self.assertEqual(self.request('/api/official-api/hybrid/configure',{'account_id':aid,'mode':'inject'},self.ui)[0],400)
        worker={'protocol':1,'worker_id':'test-worker','epoch':'test-epoch','armed':False,'revision':self.app.cruise_worker_jobs()['revision']}
        path='/api/cruise/hybrid/worker-next'
        self.assertEqual(self.request(path,worker,self.ui)[0],403)
        headers={'X-Monitor-Token':self.app.token,'Origin':'chrome-extension://test-extension'}
        code,_,raw=self.request(path,worker,headers)
        self.assertEqual(code,200);self.assertIsNone(json.loads(raw)['command'])
        self.assertEqual(self.request(path,worker,{**headers,'Origin':'https://qianchuan.jinritemai.com'})[0],403)
        self.assertEqual(self.calls,[])

    def test_hybrid_worker_preflight_allows_only_extension_origins(self):
        for name in ('worker-next','worker-result','worker-begin','worker-final','worker-ping'):
            request=urllib.request.Request(f'http://127.0.0.1:{self.app.port}/api/cruise/hybrid/'+name,
                method='OPTIONS',headers={'Origin':'chrome-extension://test-extension',
                'Access-Control-Request-Headers':'content-type,x-monitor-token'})
            with urllib.request.urlopen(request,timeout=5) as response:
                self.assertEqual(response.status,204)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'],'chrome-extension://test-extension')
            request.add_header('Origin','https://other.example')
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request,timeout=5)
            self.assertEqual(caught.exception.code,403)
        self.assertEqual(self.calls,[])

    def test_extension_connection_probe_updates_version_without_leasing_or_executing(self):
        worker={'protocol':1,'worker_id':'test-worker','epoch':'test-epoch','armed':False,
            'revision':self.app.cruise_worker_jobs()['revision'],'extension_version':'1.10.0'}
        path='/api/cruise/hybrid/worker-ping'
        self.assertEqual(self.request(path,worker,self.ui)[0],403)
        headers={'X-Monitor-Token':self.app.token,'Origin':'chrome-extension://test-extension'}
        with patch.object(self.app.official_actions.hybrid,'next',side_effect=AssertionError('探测不能领取命令')):
            code,_,raw=self.request(path,worker,headers)
        self.assertEqual(code,200);reply=json.loads(raw);self.assertFalse(reply['armed'])
        self.assertEqual(reply['extension_version'],'1.10.0');self.assertEqual(reply['ad_changes'],0)
        self.assertEqual(self.app.official_actions.status(),[]);self.assertEqual(self.calls,[])

    def test_hybrid_retry_requires_ui_csrf_and_calls_only_the_account_local_recovery(self):
        payload={'account_id':self.app.cfg()['accounts'][0]['id']}
        path='/api/official-api/hybrid/retry'
        with patch.object(self.app.official_actions.hybrid,'retry',return_value={'ok':True,'ad_changes':0}) as retry:
            self.assertEqual(self.request(path,payload)[0],403)
            self.assertEqual(self.request(path,payload,{**self.ui,'Origin':'https://other.example'})[0],403)
            retry.assert_not_called()
            self.assertEqual(self.request(path,payload,self.ui)[0],200)
            retry.assert_called_once_with(payload)
        self.assertEqual(self.calls,[])

    def test_hybrid_start_requires_ui_csrf_and_cannot_be_called_by_a_website(self):
        payload={'account_id':self.app.cfg()['accounts'][0]['id']}
        path='/api/official-api/hybrid/start'
        with patch.object(self.app.official_actions.hybrid,'start',return_value={'ok':True,'ad_changes':0}) as start:
            self.assertEqual(self.request(path,payload)[0],403)
            self.assertEqual(self.request(path,payload,{**self.ui,'Origin':'https://other.example'})[0],403)
            start.assert_not_called()
            self.assertEqual(self.request(path,payload,self.ui)[0],200)
            start.assert_called_once_with(payload)
        self.assertEqual(self.calls,[])

    def test_new_routes_require_local_ui_csrf_and_never_return_secret(self):
        self.assertEqual(self.request('/api/official-api/configure',self.config())[0],403)
        self.assertEqual(self.request('/api/official-api/configure',self.config(),self.ui)[0],200)
        code,_,raw=self.request('/api/official-api')
        self.assertEqual(code,200);self.assertNotIn(b'test-secret-not-live',raw)
        self.assertEqual(json.loads(raw)['stage'],'awaiting_authorization')
        self.assertEqual(self.request('/api/official-api',headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request('/api/official-api/configure',self.config(),{
            'X-Monitor-CSRF':self.app.csrf,'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.calls,[])

    def test_callback_cookie_one_use_and_clean_redirect(self):
        self.request('/api/official-api/configure',self.config(),self.ui)
        code,headers,raw=self.request('/api/official-api/authorize',{'label':'隔离测试'},self.ui)
        self.assertEqual(code,200)
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Lax',headers['Set-Cookie'])
        state=parse_qs(urlsplit(json.loads(raw)['authorization_url']).query)['state'][0]
        callback='/oauth/oceanengine/callback?'+urlencode({'state':state,'auth_code':'test-one-use'})
        self.assertEqual(self.request(callback)[1]['Location'],'/api-setup?oauth=failed')
        self.assertEqual(self.calls,[])
        cookie=headers['Set-Cookie'].split(';')[0]
        code,headers,raw=self.request(callback,headers={'Cookie':cookie})
        self.assertEqual(code,303);self.assertEqual(headers['Location'],'/api-setup?oauth=connected')
        self.assertEqual(raw,b'');self.assertEqual(headers['Referrer-Policy'],'no-referrer')
        self.assertEqual(self.request(callback,headers={'Cookie':cookie})[1]['Location'],'/api-setup?oauth=failed')
        self.assertEqual(self.calls,['exchange'])

    def test_read_cannot_expand_registered_accounts_or_accept_write_paths(self):
        self.assertEqual(self.request('/api/official-api/read',{'account_id':'999','grant_id':'x'},self.ui)[0],400)
        self.assertEqual(self.request('/api/official-api/plans',{'account_id':'999','grant_id':'x'},self.ui)[0],400)
        self.assertEqual(self.request('/api/official-api/submit',{},self.ui)[0],404)
        self.assertEqual(self.calls,[])

    def test_api_read_account_cannot_reuse_a_browser_action(self):
        aid=self.app.cfg()['accounts'][0]['id']
        self.app.official_api.plan_readings[aid]={'plans':[]}
        with self.assertRaisesRegex(ValueError,'不能沿用浏览器确认单'):
            self.app.validate_cost_action({'account_id':aid})

    def test_automatic_authority_routes_require_local_csrf_and_exact_disable_scope(self):
        seen=[]
        self.app.official_actions.auto.configure=lambda p:seen.append(p) or {'ok':True,'ad_changes':0}
        payload={'enabled':False}
        self.assertEqual(self.request('/api/official-api/auto/configure',payload)[0],403)
        self.assertEqual(self.request('/api/official-api/auto/configure',payload,{
            **self.ui,'Origin':'https://other.example'})[0],403)
        self.assertEqual(seen,[])
        self.assertEqual(self.request('/api/official-api/auto/configure',payload,self.ui)[0],200)
        self.assertEqual(seen,[payload]);self.assertEqual(self.calls,[])
        self.assertEqual(self.request('/api/official-api/auto/disable',{'account_id':'1','enabled':True},self.ui)[0],400)

    def test_six_feature_authority_routes_require_csrf_and_do_not_accept_raw_write_payload(self):
        seen=[]
        self.app.official_actions.rule_auto.configure=lambda p:seen.append(p) or {'ok':True,'ad_changes':0}
        payload={'enabled':False}
        self.assertEqual(self.request('/api/official-api/rule-auto/configure',payload)[0],403)
        self.assertEqual(seen,[])
        self.assertEqual(self.request('/api/official-api/rule-auto/configure',payload,self.ui)[0],200)
        self.assertEqual(seen,[payload]);self.assertEqual(self.calls,[])
        self.assertEqual(self.request('/api/official-api/rule-auto/disable',{'account_id':'1','body':{}},self.ui)[0],400)

    def test_entry_selection_persists_without_changing_other_accounts_or_strategy(self):
        before=self.app.cfg()
        code,_,raw=self.request('/api/accounts');self.assertEqual(code,200)
        registry=json.loads(raw);accounts=registry['accounts']
        selected='chengfang' if accounts[0].get('delivery_entry')=='both' else 'both'
        accounts[0]['delivery_entry']=selected
        code,_,raw=self.request('/api/accounts',{'accounts':accounts,'expected_revision':registry['revision']},self.ui)
        self.assertEqual(code,200,raw)
        after=self.app.cfg()
        self.assertEqual(after['accounts'][0]['delivery_entry'],selected)
        for old,new in zip(before['accounts'],after['accounts']):
            self.assertEqual({k:v for k,v in old.items() if k!='delivery_entry'},
                             {k:v for k,v in new.items() if k!='delivery_entry'})
        self.assertEqual(after['rules'],before['rules'])
        self.assertEqual(self.app.cruise_accounts()[0]['delivery_entry'],selected)
        # An old revision may not overwrite another tab's entry choice.
        self.assertEqual(self.request('/api/accounts',{'accounts':accounts,'expected_revision':registry['revision']},self.ui)[0],400)
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
