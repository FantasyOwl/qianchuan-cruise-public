import copy
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path

from monitor.accounts import collection_policy
from monitor.server import App,Handler,ROOT,Server
from tests.test_browser_actions import surface
from tests.test_cruise_actions import task


class CruiseHTTPTests(unittest.TestCase):
    def test_console_reads_share_one_slow_build_then_recover(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        original = self.app.cruise_status
        def slow_status(**options):
            calls.append(options)
            started.set()
            release.wait(3)
            return original(**options)
        self.app.cruise_status = slow_status
        try:
            self.assertIsNone(self.app.console_status(timeout=.01))
            self.assertTrue(started.wait(1))
            self.assertIsNone(self.app.console_status(timeout=.01))
            self.assertEqual(calls, [{'console':True}])
            job = self.app.console_read
        finally:
            release.set()
        self.assertTrue(job['done'].wait(2))
        self.assertEqual(self.req('/api/cruise?view=console')[0], 200)

    def test_console_omits_raw_material_rows_but_keeps_batches_and_full_endpoint(self):
        key = self.live['id'] + ':1234567'
        self.app.official_api.workflows[key] = {'materials':[{'raw':'scan row'}],
            'unresolved_material_rows':[{'raw':'missing id'}], 'tasks':[], 'batches':[{'key':'batch'}]}
        code, snapshot = self.req('/api/cruise?view=console')
        self.assertEqual(code, 200, snapshot)
        workflow = snapshot['api_workflows'][key]
        self.assertEqual(workflow['material_count'], 1)
        self.assertEqual(workflow['unresolved_material_count'], 1)
        self.assertNotIn('materials', workflow)
        self.assertEqual(workflow['batches'], [{'key':'batch'}])
        self.assertEqual(self.req('/api/cruise')[1]['api_workflows'][key]['materials'], [{'raw':'scan row'}])

    def test_dashboard_snapshot_does_not_wait_for_financial_preflight_gate(self):
        # The holder represents a slow platform request. Reading UI state
        # must finish while the holder still owns the submission gate.
        entered=threading.Event();release=threading.Event()
        def hold_gate():
            with self.app.action_claim_lock:entered.set();release.wait(5)
        holder=threading.Thread(target=hold_gate);holder.start();self.assertTrue(entered.wait(1))
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.req,'/api/cruise')
                code,value=future.result(timeout=2)
            self.assertEqual(code,200,value);self.assertTrue(holder.is_alive())
            self.assertEqual(value['accounts'][0]['id'],self.app.cruise.status()['accounts'][0]['id'])
        finally:release.set();holder.join()

    def test_batch_range_update_is_local_preserves_existing_grant_caps_and_disabled_accounts(self):
        aid=self.live['id'];a=next(a for a in self.app.cruise.status()['accounts'] if a['id']==aid)
        q=self.app.official_actions;from monitor.official_workflow import digest
        with q.db() as db:
            db.execute('INSERT INTO api_auto(account_id,plan_id,grant_id,profile_digest,profile_revision,enabled,budget_cap,batch_cap,next_at,state,message) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(aid,'1234567','original',digest(a['profile']),a['revision'],1,'9000',75,None,'enabled','test'))
            db.execute('INSERT INTO api_auto_attempts VALUES(?,?,?,?)',('old-attempt',aid,'original',q.clock().date().isoformat()))
        body={'account_id':aid,'expected_revision':a['revision'],'batch_size_min':2,'batch_size_max':5,'confirmed':True}
        self.assertEqual(self.req('/api/cruise/f5-batch-range',body,self.worker)[0],403)
        code,result=self.req('/api/cruise/f5-batch-range',body,self.ui);self.assertEqual(code,200,result)
        self.assertEqual(result['auto_authorizations_updated'],['api_auto'])
        with q.db() as db:
            grant=dict(db.execute('SELECT * FROM api_auto WHERE account_id=?',(aid,)).fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM api_auto_attempts').fetchone()[0],1)
        self.assertEqual((grant['budget_cap'],grant['batch_cap'],grant['enabled']),('9000',75,1))
        self.assertNotEqual(grant['grant_id'],'original');self.assertEqual(q.rule_auto.status(),[])
        new=next(a for a in self.app.cruise.status()['accounts'] if a['id']==aid)
        self.assertEqual(grant['profile_digest'],digest(new['profile']));self.assertEqual(grant['profile_revision'],new['revision'])
        self.assertEqual(self.req('/api/cruise/f5-batch-range',body,self.ui)[0],400)

    def test_api_account_report_is_visible_before_plan_parser_succeeds_and_scan_reports_error(self):
        aid=self.live['id'];at=self.app.cruise.clock()
        self.app.official_api.readings[aid+':uni_promotion']={'account_id':aid,'delivery_entry':'uni_promotion',
            'entry_label':'全域','date':at.date().isoformat(),'captured_at':at.isoformat(),
            'data_status':'observed','metrics':{'spend':123.4,'roi':2.8}}
        self.app.official_api.polling[aid]={'state':'partial','components':{'plans':{'state':'error','error':'抖音号UID未能核验'}}}
        code,value=self.req('/api/cruise');self.assertEqual(code,200)
        account=next(a for a in value['monitoring']['accounts'] if a['account_id']==aid)
        self.assertEqual(account['backend'],'official_api');self.assertTrue(account['data_connected'])
        self.assertEqual(account['summaries'][0]['metrics']['spend'],123.4);self.assertEqual(account['plans'],[])
        result=self.app.cruise.scan(aid,'main')
        self.assertFalse(result['collection_requested']);self.assertIn('抖音号UID未能核验',result['result']['summary'])
        self.assertEqual(result['result']['proposals'],[]);self.assertEqual(result['ad_changes'],0)

    def test_save_and_update_automatic_rules_requires_ui_and_never_creates_a_new_grant(self):
        account=next(a for a in self.app.cruise.status()['accounts'] if a['id']==self.live['id'])
        profile=copy.deepcopy(account['profile']);profile['features']['f4'].update(enabled=True,pause_rule='daily_spend_roi',
            daily_runtime_hours=2,daily_spend_max=20,daily_roi_max=1.5)
        body={'account_id':self.live['id'],'expected_revision':account['revision'],'profile':profile,'bindings':account['bindings']}
        self.assertEqual(self.req('/api/cruise/save-auto',body,self.worker)[0],403)
        code,result=self.req('/api/cruise/save-auto',body,self.ui);self.assertEqual(code,200,result)
        self.assertEqual(result['auto_authorizations_updated'],[]);self.assertEqual(result['ad_changes'],0)
        self.assertEqual(self.app.official_actions.auto.status(),[]);self.assertEqual(self.app.official_actions.rule_auto.status(),[])
        saved=next(a for a in self.app.cruise.status()['accounts'] if a['id']==account['id'])
        self.assertEqual(saved['profile']['features']['f4']['daily_roi_max'],1.5)

    def test_account_id_entry_requires_ui_origin_and_a_supported_collector(self):
        body={'account_id':self.live['id']}
        self.assertEqual(self.req('/api/cruise/enter-account',body,self.worker)[0],403)
        code,value=self.req('/api/cruise/enter-account',body,self.ui);self.assertEqual(code,200,value);self.assertEqual(value['ad_changes'],0)
        self.app.store.patch_diagnostic('extension',{'extension_version':'1.6.3'})
        old=self.app.cruise_worker_jobs();self.assertFalse(any(j['job_kind']=='account_entry' for j in old['jobs']))
        self.app.store.patch_diagnostic('extension',{'extension_version':'1.6.4'})
        new=self.app.cruise_worker_jobs();self.assertTrue(any(j['job_kind']=='account_entry' and j['account_id']==self.live['id'] for j in new['jobs']))
        self.assertEqual(self.req('/api/cruise/worker-job-result',{'id':value['id'],'ok':False,'message':'x','entry':{}},self.ui)[0],403)

    def test_close_worker_and_ui_routes_cannot_approve_each_other(self):
        from tests.test_live_close import surface as close_surface
        from datetime import timedelta
        row=close_surface(self.now);row.update(account_id=self.live['id'],group=self.live['tab_group'],home_url='https://qianchuan.jinritemai.com/uni-prom?aavid='+self.live['id'])
        body={'protocol':1,'worker_id':'close_worker','epoch':'close_epoch','armed':True,'revision':self.policy['revision'],'surfaces':[row]}
        self.assertEqual(self.req('/api/cruise/close/worker-observe',body,self.ui)[0],403)
        self.assertEqual(self.req('/api/cruise/close/worker-observe',body,self.worker)[0],200)
        self.assertEqual(self.req('/api/cruise/close/worker-parents',{'revision':self.policy['revision']},self.ui)[0],403)
        code,parents=self.req('/api/cruise/close/worker-parents',{'revision':self.policy['revision']},self.worker);self.assertEqual(code,200,parents);self.assertEqual(parents['parents'],[])
        self.assertEqual(self.req('/api/cruise/close/approve',{},self.worker)[0],403)
        self.assertEqual(self.req('/api/cruise/close/read-home',{'account_id':self.live['id']},self.worker)[0],403)
        code,read=self.req('/api/cruise/close/read-home',{'account_id':self.live['id']},self.ui);self.assertEqual(code,200,read);self.assertEqual(read['ad_changes'],0)
        create={'worker_id':'close_worker','epoch':'close_epoch','account_id':row['account_id'],'plan_id':row['plan_id'],'off_at':(self.now+timedelta(hours=2)).isoformat(),'budget':'200','duration_minutes':30}
        self.assertEqual(self.req('/api/cruise/close/create',create,self.worker)[0],403)
        code,d=self.req('/api/cruise/close/create',create,self.ui);self.assertEqual(code,200,d)
        self.assertEqual(self.req('/api/cruise/close/approve',{'id':d['id'],'intent_digest':d['intent_digest'],'confirmed':True},self.ui)[0],200)
        self.assertTrue(self.app.cruise_worker_jobs()['navigation_allowed'])
        self.now=datetime.fromisoformat(d['intent']['trigger_at']);row['captured_at']=self.now.isoformat()
        self.assertEqual(self.req('/api/cruise/close/worker-observe',body,self.worker)[0],200)
        self.assertFalse(self.app.cruise_worker_jobs()['navigation_allowed'])
        self.assertEqual(self.req('/api/cruise/actions/worker-claim',{'worker_id':'no','epoch':'no'},self.worker)[1]['command'],None)
        code,c=self.req('/api/cruise/close/worker-claim',{'worker_id':'close_worker','epoch':'close_epoch'},self.worker);self.assertEqual(code,200,c);self.assertIsNotNone(c['command'])

    def test_loading_material_status_does_not_complete_a_read_or_advance_page(self):
        from tests.test_batch_chase import surface as material_surface
        from monitor.batch_chase import date_range
        row=material_surface();row.update(account_id=self.live['id'],group=self.live['tab_group'],captured_at=self.now.isoformat())
        row['date_start'],row['date_end']=date_range(self.now,5)
        for item in row['materials']:item.update(chase_status='',approved=False)
        completed=[];advanced=[]
        self.app.cruise.observe_material_pages=lambda rows:completed.extend(rows)
        self.app.cruise.next_material_page=lambda surface:advanced.append(surface)
        body={'protocol':1,'worker_id':'batch_loading','epoch':'loading_epoch','armed':True,'revision':self.policy['revision'],'surfaces':[row]}
        self.assertEqual(self.req('/api/cruise/batch/worker-observe',body,self.worker)[0],200)
        self.assertEqual(completed,[]);self.assertEqual(advanced,[])
        for item in row['materials']:item.update(chase_status='-',approved=True)
        self.assertEqual(self.req('/api/cruise/batch/worker-observe',body,self.worker)[0],200)
        self.assertEqual(len(completed),1)

    def test_batch_worker_routes_require_pairing_and_ui_creation_requires_csrf(self):
        body={'protocol':1,'worker_id':'batch_worker','epoch':'batch_epoch','armed':False,'revision':self.policy['revision'],'surfaces':[]}
        self.assertEqual(self.req('/api/cruise/batch/worker-observe',body,self.ui)[0],403)
        self.assertEqual(self.req('/api/cruise/batch/worker-observe',body,self.worker)[0],200)
        self.assertEqual(self.req('/api/cruise/batch/create',{},self.worker)[0],403)
        self.assertEqual(self.req('/api/cruise/batch/create-selected',{},self.worker)[0],403)
        self.assertEqual(self.req('/api/cruise/batch/create-selected',{},self.ui)[0],400)
        self.assertEqual(self.req('/api/cruise/batch/renew',{'id':'x'},self.worker)[0],403)
        self.assertEqual(self.req('/api/cruise/batch/locate',{'id':'x'},self.worker)[0],403)
        self.assertEqual(self.req('/api/cruise/batch/locate',{'id':'x'},self.ui)[0],400)
        self.assertEqual(self.req('/api/cruise/batch/renew',{'id':'x'},self.ui)[0],400)
        self.assertEqual(self.req('/api/cruise/batch/approve',{'id':'x','intent_digest':'x','confirmed':True},self.ui)[0],400)
        code,state=self.req('/api/cruise');self.assertEqual(code,200)
        self.assertEqual(state['batch_control']['actions'],[]);self.assertFalse(state['batch_control']['execution_available'])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        shutil.copyfile(ROOT/'config.json',root/'config.json')
        self.app=App(root);self.now=datetime.now(timezone.utc)
        self.app.browser_actions.clock=lambda:self.now
        self.app.cruise_actions.clock=lambda:self.now
        self.app.cruise.clock=lambda:self.now
        self.policy=collection_policy(self.app.cfg())
        self.live=next(a for a in self.policy['accounts'] if a['scene']=='live')
        self.product=next(a for a in self.policy['accounts'] if a['scene']=='product')
        self.mainrow=surface(self.now,account_id=self.product['id'],group=self.product['tab_group'])
        self.taskrow=task(self.now,account_id=self.live['id'],group=self.live['tab_group'])
        self.server=Server(('127.0.0.1',0),Handler);self.app.port=self.server.server_address[1];self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.worker={'X-Monitor-Token':self.app.token,'Origin':'chrome-extension://test'}
        self.ui={'X-Monitor-CSRF':self.app.csrf}

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def req(self,path,body=None,headers=None):
        request=urllib.request.Request(f'http://127.0.0.1:{self.app.port}{path}',
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json',**(headers or {})})
        try:r=urllib.request.urlopen(request,timeout=8)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,json.loads(r.read())

    def observe(self):
        for path,rows in [('/api/browser-actions/worker-observe',[self.mainrow]),('/api/cruise/actions/worker-observe',[self.taskrow])]:
            code,value=self.req(path,{'protocol':1,'worker_id':'worker_http','epoch':'epoch_http','armed':True,
                'revision':self.policy['revision'],'surfaces':rows},self.worker)
            self.assertEqual(code,200,value)

    def create(self,task_action=True):
        body={'worker_id':'worker_http','epoch':'epoch_http','reason':'隔离测试明确单条操作',
            'account_id':self.live['id'] if task_action else self.product['id'],
            'plan_id':self.taskrow['plan_id'] if task_action else self.mainrow['plan_id'],
            'action':'control_budget_up' if task_action else 'budget_up',
            'after':'2000.00' if task_action else '1100.00'}
        if task_action:body['task_id']=self.taskrow['task_id']
        path='/api/cruise/actions/' if task_action else '/api/browser-actions/'
        code,d=self.req(path+'create',body,self.ui);self.assertEqual(code,200,d)
        return path,d

    def approve(self,path,d):
        code,value=self.req(path+'approve',{'id':d['id'],'intent_digest':d['intent_digest'],'confirmed':True},self.ui)
        self.assertEqual(code,200,value)

    def test_new_home_status_is_account_isolated_and_does_not_enable_actions(self):
        code,d=self.req('/api/cruise');self.assertEqual(code,200)
        self.assertEqual(len(d['accounts']),len(self.app.cfg()['accounts']))
        self.assertTrue(all(not a['lanes']['main']['running'] and not a['lanes']['auto']['running'] for a in d['accounts']))
        self.assertFalse(d['browser_control']['execution_available']);self.assertFalse(d['task_control']['execution_available'])
        self.assertEqual(d['task_control']['actions'],[])
        self.assertEqual(self.app.store.snapshots(),[])

    def test_new_auth_paths_never_exchange_worker_token_for_user_confirmation(self):
        self.assertEqual(self.req('/api/cruise/actions/approve',{},self.worker)[0],403)
        self.assertEqual(self.req('/api/cruise/actions/worker-claim',{},self.ui)[0],403)
        self.assertEqual(self.req('/api/cruise/worker-jobs')[0],403)
        self.assertEqual(self.req('/api/cruise/worker-jobs',headers=self.worker)[0],200)
        self.assertEqual(self.req('/api/cruise/actions/worker-observe',{},dict(self.worker,Origin='https://evil.example'))[0],403)
        self.assertEqual(self.req('/api/cruise',headers={'Origin':'https://evil.example'})[0],403)

    def test_cross_queue_claims_are_atomic_even_for_different_accounts(self):
        self.observe();a=self.create(True);b=self.create(False)
        self.approve(*a);self.approve(*b)
        def claim(path):return self.req(path+'worker-claim',{'worker_id':'worker_http','epoch':'epoch_http'},self.worker)
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(claim,[a[0],b[0]]))
        self.assertTrue(all(code==200 for code,_ in rows),rows)
        self.assertEqual(sum(d.get('command') is not None for _,d in rows),1)
        code,jobs=self.req('/api/cruise/worker-jobs',headers=self.worker)
        self.assertFalse(jobs['navigation_allowed'])

    def test_draft_blocks_navigation_and_cancel_releases_it_without_platform_io(self):
        self.observe();path,d=self.create()
        self.assertFalse(self.req('/api/cruise/worker-jobs',headers=self.worker)[1]['navigation_allowed'])
        self.assertEqual(self.req(path+'cancel',{'id':d['id']},self.ui)[0],200)
        self.assertTrue(self.req('/api/cruise/worker-jobs',headers=self.worker)[1]['navigation_allowed'])

    def test_task_protocol_full_confirm_submit_readback_and_no_automatic_claim(self):
        self.observe();path,d=self.create()
        claim={'worker_id':'worker_http','epoch':'epoch_http'}
        self.assertIsNone(self.req(path+'worker-claim',claim,self.worker)[1]['command'])
        self.approve(path,d);code,value=self.req(path+'worker-claim',claim,self.worker)
        c=value['command'];self.assertEqual(code,200)
        self.assertEqual(self.req(path+'worker-begin',{'id':c['id'],'lease':c['lease'],'observation':self.taskrow},self.worker)[0],200)
        self.assertEqual(self.req(path+'worker-begin',{'id':c['id'],'lease':c['lease'],'observation':self.taskrow},self.worker)[0],400)
        obs={k:self.taskrow[k] for k in ['account_id','plan_id','task_id','tab_id','document_id']}
        obs.update(readback_document_id='fresh_doc',field='budget',value='2000.00',captured_at=self.now.isoformat(),reloaded=True)
        code,value=self.req(path+'worker-receipt',{'id':c['id'],'lease':c['lease'],'submitted':True,'observation':obs,'message':'隔离刷新回读'},self.worker)
        self.assertEqual(code,200,value);self.assertEqual(value['state'],'verified')
        self.assertEqual(self.req('/api/cruise')[1]['task_control']['actions'][0]['state'],'verified')

    def test_main_plan_cost_callback_is_rechecked_before_approval(self):
        self.observe();path,d=self.create(False)
        self.app.cost_review=lambda intent:{'allowed':False,'blocking_reasons':['当前条件已变化']}
        code,value=self.req(path+'approve',{'id':d['id'],'intent_digest':d['intent_digest'],'confirmed':True},self.ui)
        self.assertEqual(code,400);self.assertIn('条件已变化',value['error'])

    def test_data_refresh_needs_local_user_intent_and_does_not_start_or_approve(self):
        payload={'account_id':self.product['id']}
        self.assertEqual(self.req('/api/cruise/refresh-data',payload,self.worker)[0],403)
        code,result=self.req('/api/cruise/refresh-data',payload,self.ui)
        self.assertEqual(code,200,result);self.assertEqual(result['ad_changes'],0)
        data=self.req('/api/cruise')[1]
        self.assertEqual(data['task_control']['actions'],[]);self.assertEqual(data['browser_control']['actions'],[])
        self.assertTrue(all(not a['lanes']['main']['running'] for a in data['accounts']))
        self.assertIn('metric_catalog',data['monitoring'])


if __name__=='__main__':unittest.main()
