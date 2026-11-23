import copy,json,unittest
from datetime import timedelta
from unittest.mock import patch
from tests.test_api_browser_chase import APIBrowserChaseTests as F
from monitor.execution_strategy import MODE
from monitor.hybrid_rules import HybridRules
from monitor.official_api import OfficialBusinessError

class APILaneTests(unittest.TestCase):
    setUp=F.setUp;tearDown=F.tearDown;transport=F.transport;confirm=F.confirm
    observed=F.observed;selected=F.selected;home_selected=F.home_selected;direct_selected=F.direct_selected
    begin_api=F.begin_api
    def start(self):
        self.queue.execution_strategy={'mode':MODE,'version':1}
        self.materials.append(('88712204312382425',200,4.8))
        self.worker.update(worker_id='ego:test',extension_version='1.23.0');self.hybrid.worker(self.worker)
        self.hybrid.configure({'account_id':self.account['id'],'mode':'api_data_browser'})
        self.assertTrue(self.hybrid.request(self.account['id'],reason='仅API取数和调控，网页只创建追投'))
        with self.queue.db() as db:rid=db.execute("SELECT id FROM hybrid_runs WHERE state NOT IN ('completed','cancelled')").fetchone()[0]
        self.hybrid.scan_api_materials(rid)
        return rid
    def command(self):return self.hybrid.next(self.worker).get('command')
    def test_long_prepare_requires_fresh_home_evidence_and_does_not_relabel_detail_time(self):
        self.start();self.worker['extension_version']='1.23.3';c=self.command()
        v=self.direct_selected(c);source_at=v['preflight']['captured_at'];self.at+=timedelta(seconds=110)
        v['home']['captured_at']=self.at.isoformat()
        for proof in v['picker_materials']:proof['captured_at']=self.at.isoformat()
        self.assertEqual(v['binding']['captured_at'],source_at)
        self.assertTrue(self.begin_api(c,v)['submit_once']);self.assertEqual(self.posts,[])
    def test_extended_prepare_cannot_accept_a_stale_home_row_or_crossed_parent(self):
        self.start();self.worker['extension_version']='1.23.3';c=self.command();base=self.direct_selected(c)
        self.at+=timedelta(seconds=110)
        for proof in base['picker_materials']:proof['captured_at']=self.at.isoformat()
        with self.assertRaisesRegex(ValueError,'首页母计划对应关系已过期'):self.begin_api(c,base)
        base['home']['captured_at']=self.at.isoformat();base['home']['row_plan_id']='8200358026205459'
        with self.assertRaisesRegex(ValueError,'首页行未唯一对应'):self.begin_api(c,base)
        self.assertEqual(self.posts,[])
    def test_complete_api_inventory_goes_directly_to_creation_without_browser_task_reads(self):
        rid=self.start();c=self.command();self.assertIsNotNone(c)
        self.assertEqual(c['phase'],'prepare');self.assertEqual(c['execution_scope'],'f5_create_only');self.assertTrue(c['direct_home'])
        self.assertNotIn(self.materials[0][0],c['action']['intent']['material_ids'])
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
        self.assertEqual(d['inventory_source'],'official_api');self.assertTrue(d['direct_inventory_complete']);self.assertEqual(self.posts,[])
    def test_targeted_candidate_reaches_writer_and_cannot_begin_without_matching_native_proof(self):
        audience={'gender':'female','ages':['18-23','31-40'],'exclude_fans':True}
        self.profile['features']['f5']['rules'][0]['audience']=audience
        self.queue.auto.configure({'account_id':self.account['id'],'plan_id':self.pid,'profile_revision':'r1',
            'enabled':True,'active_budget_cap':'500','max_new_batches_per_day':'3','confirmed':True})
        self.start();c=self.command();self.assertEqual(c['action']['intent']['browser_batch']['after']['audience'],audience)
        value=self.direct_selected(c)
        with self.assertRaisesRegex(ValueError,'观察结构不完整'):self.begin_api(c,value)
        value['audience']={**audience,'source':'native_form','captured_at':self.at.isoformat()}
        value['audience']['gender']='male'
        with self.assertRaisesRegex(ValueError,'定向与已保存规则不符'):self.begin_api(c,value)
        value['audience']['gender']='female';self.assertTrue(self.begin_api(c,value)['submit_once'])
        self.assertEqual(self.posts,[])
    def test_api_failure_waits_without_a_browser_inventory_or_partial_submission(self):
        original=self.api.scan_workflow
        def fail(*args,**kw):
            if not kw.get('browser_candidates'):raise ValueError('API任务限流40100')
            return original(*args,**kw)
        with patch.object(self.api,'scan_workflow',side_effect=fail):rid=self.start()
        self.assertIsNone(self.command());self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
        self.assertEqual(d['phase'],'api_materials');self.assertIn('API任务限流',d['message'])
    def test_stale_inventory_returns_to_api_not_browser_tasks(self):
        rid=self.start();c=self.command();self.at+=timedelta(seconds=181)
        self.assertIsNone(self.command())
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
        self.assertEqual(d['phase'],'api_inventory');self.assertEqual(self.posts,[])
    def test_fresh_pool_cannot_reuse_an_old_draft_batch_after_a_rescan(self):
        rid=self.start();c=self.command();old=c['action']['id'];self.at+=timedelta(seconds=601)
        self.plan['captured_at']=self.at.isoformat()
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
            d['material_captured_at']=self.at.isoformat();d['inventory_at']=self.at.isoformat();d['api_inventory']['captured_at']=self.at.isoformat()
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),rid))
        self.assertIsNone(self.command())
        with self.queue.db() as db:
            self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(old,)).fetchone()[0],'cancelled')
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0]);self.assertEqual(d['phase'],'api_materials');self.assertNotIn('action_id',d)
        self.assertEqual(self.posts,[])
    def test_rescanning_retires_old_unsent_action_before_making_new_candidates(self):
        rid=self.start();c=self.command();old=c['action']['id']
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0]);d.update(phase='api_materials',command=None)
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),rid))
        self.hybrid.scan_api_materials(rid);new=self.command();self.assertIsNotNone(new);self.assertNotEqual(new['action']['id'],old)
        with self.queue.db() as db:self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(old,)).fetchone()[0],'cancelled')
        self.assertEqual(self.posts,[])
    def test_rule_browser_dispatch_is_disabled_and_old_unknown_actions_are_preserved(self):
        self.queue.execution_strategy={'mode':MODE,'version':1};rules=HybridRules(self.queue)
        self.assertFalse(rules.request(self.account['id'],reason='API限流'))
        self.assertIsNone(rules.next(self.worker)['command'])
        with self.assertRaisesRegex(ValueError,'统一通过API'):rules.begin({})
        with self.assertRaisesRegex(ValueError,'统一通过API'):rules.final({})
        self.assertEqual(self.posts,[])
    def test_known_mapping_uses_api_current_status_and_never_invents_unknown_materials(self):
        self.start();tid='8351669300737423';mids=[self.materials[1][0],self.materials[2][0]]
        intent={'account_id':self.account['id'],'plan_id':self.pid,'action':'material_chase','material_ids':mids}
        with self.queue.db() as db:db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('old-verified','verified',json.dumps(intent),'digest',self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),json.dumps({'task_id':tid})))
        self.assertEqual(self.hybrid.api_lane.known_materials(self.account['id'],self.pid,tid),sorted(mids))
        self.assertIsNone(self.hybrid.api_lane.known_materials('8086606404052108',self.pid,tid))
        self.assertIsNone(self.hybrid.api_lane.known_materials(self.account['id'],self.pid,'8038032061450581'))
        self.assertEqual(self.posts,[])
    def test_retiring_old_rule_runs_preserves_uncertain_ledger_rows(self):
        self.queue.execution_strategy={'mode':MODE,'version':1};rules=HybridRules(self.queue)
        at=self.at.isoformat();intent={'account_id':self.account['id'],'plan_id':self.pid,'action':'task_budget','task_id':'10001','operation':'browser_rule','execution_channel':'browser'}
        with self.queue.db() as db:
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('old-unknown','unknown',json.dumps(intent),'digest',at,at,at,None))
            db.execute('INSERT INTO hybrid_rule_runs VALUES(?,?,?,?,?,?)',('rule-run',self.account['id'],self.pid,'waiting',at,json.dumps({'phase':'verify','action_id':'old-unknown','command':{'id':'old'}})))
        rules.retire()
        with self.queue.db() as db:
            self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',('old-unknown',)).fetchone()[0],'unknown')
            self.assertEqual(db.execute('SELECT state FROM hybrid_rule_runs WHERE id=?',('rule-run',)).fetchone()[0],'cancelled')
        self.assertEqual(self.posts,[])
    def receipt_case(self):
        rid=self.start();c=self.command();permit=self.begin_api(c,self.direct_selected(c))
        self.hybrid.final({'run_id':rid,'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2);self.assertIsNone(self.command())
        i=c['action']['intent'];raw={'id':'8351669300737423','name':i['browser_batch']['task_name'],
            'advertiser_id':int(self.account['id']),'ad_id':int(self.pid),'scene':'MATERIAL_ADD_BUDGET',
            'material_list':[{'material_id':m} for m in i['material_ids']],'budget':i['body']['budget'],
            'smart_bid_type':'SMART_BID_CUSTOM','external_action_name':'直播间支付','deep_external_action_name':None,
            'bid':i['browser_batch']['after']['bid_value'],'task_status':'PROCESSING'}
        return rid,c,i,raw
    def read_receipt(self,rid,raw,rows=None):
        original=self.api.transport
        def response(method,q,token):
            if method=='control_tasks' and q.get('filtering',{}).get('search_keyword')==raw['name']:
                found=[raw] if rows is None else rows
                return {'task_list':found,'page_info':{'total_number':len(found)}}
            return original(method,q,token)
        with patch.object(self.api,'transport',side_effect=response):self.hybrid.api_lane.scan(rid)
    def test_api_receipt_verifies_original_browser_creation_without_any_second_submit(self):
        rid,c,i,raw=self.receipt_case();self.read_receipt(rid,raw)
        record=next(r for r in self.queue.status() if r['id']==i.get('id',c['action']['id']))
        self.assertEqual(record['state'],'verified');self.assertEqual(record['result']['readback_source'],'official_api');self.assertEqual(self.posts,[])
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
        self.assertNotIn('permit',d);self.assertNotIn('begin_at',d);self.assertNotIn('submission_materials',d)
    def test_missing_api_materials_can_only_use_original_validated_pre_submit_proof(self):
        rid,c,i,raw=self.receipt_case();raw.pop('material_list');self.read_receipt(rid,raw)
        record=next(r for r in self.queue.status() if r['id']==c['action']['id'])
        self.assertEqual(record['state'],'verified');self.assertEqual(record['result']['materials_verification_source'],'pre_submit_native_form');self.assertEqual(self.posts,[])
    def test_old_unknown_without_original_material_proof_is_never_fabricated_or_reposted(self):
        rid,c,i,raw=self.receipt_case();raw.pop('material_list')
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0]);d.pop('submission_materials')
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),rid))
        self.read_receipt(rid,raw)
        record=next(r for r in self.queue.status() if r['id']==c['action']['id'])
        self.assertNotEqual(record['state'],'verified');self.assertEqual(self.posts,[])
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
        self.assertEqual(d['phase'],'verify');self.assertIn('缺少创建前表单核对证据',d['message'])
    def test_contradictory_api_materials_override_pre_submit_evidence_and_keep_original_pending(self):
        rid,c,i,raw=self.receipt_case();raw['material_list']=[{'material_id':'85151461799222442'}];self.read_receipt(rid,raw)
        record=next(r for r in self.queue.status() if r['id']==c['action']['id'])
        self.assertNotEqual(record['state'],'verified');self.assertEqual(self.posts,[])
    def test_wrong_account_and_nonunique_original_name_never_release_a_write_reservation(self):
        rid,c,i,raw=self.receipt_case();raw['advertiser_id']=8086606404052108;self.read_receipt(rid,raw)
        record=next(r for r in self.queue.status() if r['id']==c['action']['id'])
        self.assertNotEqual(record['state'],'verified')
        raw['advertiser_id']=int(self.account['id']);self.read_receipt(rid,raw,[raw,dict(raw,id='8038032061450581')])
        record=next(r for r in self.queue.status() if r['id']==c['action']['id'])
        self.assertNotEqual(record['state'],'verified');self.assertEqual(self.posts,[])

    def test_verify_backoff_tiers_fresh_vs_stale_isolated_and_deprioritizes_isolated_reads(self):
        from monitor.api_chase_lane import APIChaseLane
        from monitor.official_rate import RateDeferred, request_priority, READ_PRIORITY, READBACK_PRIORITY
        now=self.at
        fresh={'begin_at':(now-timedelta(seconds=30)).isoformat(),'verify_cycle':0}
        self.assertEqual(APIChaseLane.verify_retry_seconds(fresh,now),20)
        fresh['verify_cycle']=1;self.assertEqual(APIChaseLane.verify_retry_seconds(fresh,now),30)
        fresh['verify_cycle']=3;self.assertEqual(APIChaseLane.verify_retry_seconds(fresh,now),60)
        self.assertEqual(APIChaseLane.verify_retry_seconds(fresh,now,RateDeferred(40100,'rate_limited',120)),120)
        iso_15m={'begin_at':(now-timedelta(minutes=15)).isoformat(),'isolated_readback':True}
        self.assertEqual(APIChaseLane.verify_retry_seconds(iso_15m,now),300)
        iso_2h={'begin_at':(now-timedelta(hours=2)).isoformat(),'isolated_readback':True}
        self.assertEqual(APIChaseLane.verify_retry_seconds(iso_2h,now),900)
        iso_24h={'begin_at':(now-timedelta(hours=24)).isoformat(),'isolated_readback':True}
        self.assertEqual(APIChaseLane.verify_retry_seconds(iso_24h,now),3600)

        rid,c,i,raw=self.receipt_case()
        seen_priorities=[]
        original=self.api.transport
        def capture(method,q,token):
            if method=='control_tasks' and q.get('filtering',{}).get('search_keyword')==raw['name']:
                seen_priorities.append(request_priority('GET'))
                return {'task_list':[],'page_info':{'total_number':0}}
            return original(method,q,token)
        with patch.object(self.api,'transport',side_effect=capture):
            self.hybrid.api_lane.scan(rid)
        self.assertEqual(seen_priorities,[READBACK_PRIORITY])
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
            self.assertEqual(d['verify_cycle'],1)
            self.assertEqual(d['retry_at'],(self.at+timedelta(seconds=20)).isoformat())
            d['isolated_readback']=True;d['begin_at']=(self.at-timedelta(hours=24)).isoformat();d['retry_at']=None
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),rid))
        with patch.object(self.api,'transport',side_effect=capture):
            self.hybrid.api_lane.scan(rid)
        self.assertEqual(seen_priorities,[READBACK_PRIORITY,READ_PRIORITY])
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()[0])
            self.assertEqual(d['verify_cycle'],2)
            self.assertEqual(d['retry_at'],(self.at+timedelta(hours=1)).isoformat())
            # Stale isolated verify must rank behind active api_materials in schedule()
            d['retry_at']=None
            db.execute('UPDATE hybrid_runs SET data=?,updated_at=? WHERE id=?',(json.dumps(d),(self.at-timedelta(hours=5)).isoformat(),rid))
            db.execute('INSERT INTO hybrid_runs VALUES(?,?,?,?,?,?)',('active-mat',self.account['id'],self.pid,'waiting',self.at.isoformat(),json.dumps({'phase':'api_materials'})))
            picked=db.execute("SELECT id FROM hybrid_runs WHERE state NOT IN ('completed','cancelled') AND json_extract(data,'$.phase') IN ('api_materials','api_inventory','verify') AND (json_extract(data,'$.retry_at') IS NULL OR json_extract(data,'$.retry_at')<=?) ORDER BY CASE WHEN json_extract(data,'$.phase')='verify' AND NOT COALESCE(json_extract(data,'$.isolated_readback'),0) THEN 0 WHEN json_extract(data,'$.phase') IN ('api_materials','api_inventory') THEN 1 ELSE 2 END,updated_at,id LIMIT 1",(self.at.isoformat(),)).fetchone()
            self.assertEqual(picked['id'],'active-mat')
        self.assertEqual(self.posts,[])

del F
if __name__=='__main__':unittest.main()
