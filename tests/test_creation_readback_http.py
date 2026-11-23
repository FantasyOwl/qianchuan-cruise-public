import copy
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from monitor.creation_readback_demo import creation_readback_example
from monitor.server import App,Handler,Server
from test_control_creation import fixture


class CreationReadbackHTTPTests(unittest.TestCase):
    def setUp(self):
        _,self.cfg,_,_,_=fixture()
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'config.json').write_text(json.dumps(self.cfg),'utf-8')
        self.app=App(self.root)
        self.server=Server(('127.0.0.1',0),Handler);self.app.port=self.server.server_address[1]
        self.server.app=self.app;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.document=creation_readback_example(self.cfg)['example_document']
        self.at=datetime.fromisoformat(self.document['query_context']['received_at'])

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def post(self,path,body,*,auth=True,origin=None):
        headers={'Content-Type':'application/json'}
        if auth:headers['X-Monitor-CSRF']=self.app.csrf
        if origin:headers['Origin']=origin
        req=urllib.request.Request(f'http://127.0.0.1:{self.app.port}'+path,data=json.dumps(body).encode(),headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)

    def state(self):
        with closing(sqlite3.connect(self.root/'data/monitor.db')) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in tables}
        return tables,counts,{p.name for p in (self.root/'data').iterdir()}

    def test_supplied_file_matches_exact_task_without_persisting_or_changing_attempts(self):
        before=self.state()
        with patch('monitor.control_creation_readback.now_cn',return_value=self.at):
            code,out=self.post('/api/control-creation-review',{'document_text':json.dumps(self.document)})
        self.assertEqual(code,200);self.assertEqual(out['comparison']['task_id'],'8753264005940210')
        self.assertEqual(out['comparison']['status'],'matches_requested_configuration')
        for flag in ('executed','persisted','ai_sent','attempt_state_changed','can_release_hold'):self.assertIs(out[flag],False)
        self.assertEqual(self.state(),before)

    def test_file_auth_credentials_and_unknown_native_routes_are_rejected(self):
        before=self.state()
        self.assertEqual(self.post('/api/control-creation-review',self.document,auth=False)[0],403)
        self.assertEqual(self.post('/api/control-creation-review',self.document,origin='https://evil.invalid')[0],403)
        doc=copy.deepcopy(self.document);doc['access_token']='PRIVATE_SECRET'
        code,out=self.post('/api/control-creation-review',doc)
        self.assertEqual(code,400);self.assertNotIn('PRIVATE_SECRET',json.dumps(out))
        for route in ('creation-read','creation-reconcile','creation-enable','creation-unlock'):
            self.assertEqual(self.post('/api/control-'+route,{})[0],404)
        self.assertEqual(self.state(),before)

    def test_demo_uses_fictitious_account_and_does_not_create_native_ledger(self):
        before=self.state();code,out=self.post('/api/simulate',{'scenario':'creation_readback'})
        self.assertEqual(code,200);review=out['creation_readback']
        self.assertTrue(out['synthetic']);self.assertTrue(review['synthetic'])
        self.assertEqual(review['comparison']['account_id'],'9990001')
        self.assertEqual(review['comparison']['reported_task_status'],'FROZEN')
        self.assertFalse(review['can_release_hold']);self.assertFalse(review['attempt_state_changed'])
        after=self.state();expected=copy.deepcopy(before);expected[1]['audit']+=1
        self.assertEqual(after,expected)

    def test_deadline_demo_only_reopens_temporary_storage_without_native_clients(self):
        before=self.state();code,out=self.post('/api/simulate',{'scenario':'control_deadline'})
        self.assertEqual(code,200);demo=out['control_deadline']
        self.assertEqual(demo['duration_hours'],2);self.assertIsNone(demo['task_id'])
        self.assertTrue(demo['temporary_database_removed']);self.assertTrue(demo['material_creation_still_blocked'])
        self.assertFalse(demo['native_client_instantiated']);self.assertIsNone(demo['native_url'])
        self.assertEqual([r['due'] for r in demo['timeline']],[False,False,True])
        expected=copy.deepcopy(before);expected[1]['audit']+=1;self.assertEqual(self.state(),expected)

    def test_deadline_has_no_http_registration_dispatch_end_or_unlock_route(self):
        before=self.state()
        for route in ('deadline-register','deadline-dispatch','deadline-end','deadline-enable','deadline-unlock'):
            self.assertEqual(self.post('/api/control-'+route,{})[0],404)
        self.assertEqual(self.state(),before)

    def test_smart_control_demo_keeps_parent_stop_and_duration_without_persisting(self):
        before=self.state();cfg_before=self.app.cfg()
        code,out=self.post('/api/simulate',{'scenario':'smart_control'})
        self.assertEqual(code,200);demo=out['smart_control']
        self.assertEqual(demo['account_id'],'9990001');self.assertEqual(demo['local_duration_policy']['duration_minutes'],30)
        self.assertIsNone(demo['create_request']['url']);self.assertIsNone(demo['stop_request']['url'])
        self.assertEqual(set(json.loads(demo['stop_request']['body_json'])),{'advertiser_id','ad_id','opt_type'})
        for flag in ('persisted','executed','native_client_enabled','deadline_registered','state_readback_verified'):
            self.assertIs(demo[flag],False)
        self.assertEqual(self.app.cfg(),cfg_before)
        expected=copy.deepcopy(before);expected[1]['audit']+=1;self.assertEqual(self.state(),expected)

    def test_smart_control_has_no_creation_stop_registration_or_unlock_http_route(self):
        before=self.state()
        self.assertEqual(self.post('/api/simulate',{'scenario':'smart_control'},auth=False)[0],403)
        for route in ('create','stop','register','unlock'):
            self.assertEqual(self.post('/api/smart-control-'+route,{})[0],404)
        self.assertEqual(self.state(),before)

    def test_material_chase_preview_has_no_execution_or_deadline_side_effect(self):
        from monitor.material_chase_demo import chase_creation_scenario
        from monitor.evaluation import evaluate_all
        rows,cfg,at,payload=chase_creation_scenario(self.cfg)
        for row in rows:row['source']['kind']='import'
        evaluation=evaluate_all(rows,cfg,at)
        payload['proposal_id']=next(p['id'] for p in evaluation['recommendations'] if p['action']=='create_chase' and p['eligible'])
        before=self.state()
        with patch('monitor.server.now_cn',return_value=at),patch.object(self.app,'current',return_value=(rows,evaluation)):
            code,out=self.post('/api/chase-creation-preview',{'document_text':json.dumps(payload)})
        self.assertEqual(code,200);self.assertTrue(out['prepared']);self.assertEqual(out['draft']['material_id'],'8753264005940210')
        self.assertFalse(out['deadline_registered']);self.assertFalse(out['native_submission_ready']);self.assertIsNone(out['native_url'])
        self.assertEqual(self.state(),before)

    def test_material_chase_demo_is_synthetic_and_creates_no_native_tables(self):
        before=self.state();code,out=self.post('/api/simulate',{'scenario':'material_chase_creation'})
        self.assertEqual(code,200);preview=out['material_chase_creation'];self.assertTrue(preview['synthetic']);self.assertTrue(preview['prepared'])
        self.assertFalse(preview['executed']);self.assertFalse(preview['deadline_registered'])
        expected=copy.deepcopy(before);expected[1]['audit']+=1;self.assertEqual(self.state(),expected)

    def test_native_chase_client_worker_delegation_and_lease_have_no_http_entry(self):
        before=self.state()
        for route in ('/api/chase-create','/api/chase-submit','/api/deadline-service/start','/api/deadline-service/authorize','/api/deadline-service/lease'):
            self.assertEqual(self.post(route,{})[0],404)
        self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main()
