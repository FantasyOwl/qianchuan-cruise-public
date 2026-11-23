"""API metric sweeps handed to the existing once-only browser writer."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import timedelta
from tests import test_hybrid_chase as fixtures
from monitor.official_api import OfficialBusinessError


class APIBrowserChaseTests(unittest.TestCase):
    def test_queued_first_inventory_is_not_preempted_between_api_completion_and_worker_claim(self):
        from monitor.hybrid_rules import HybridRules
        self.queue.hybrid_rules=HybridRules(self.queue)
        self.queue.rule_auto.configure({'account_id':self.account['id'],'plan_id':self.pid,'profile_revision':'r1',
            'features':['f'+str(i) for i in range(1,7)],'enabled':True,'confirmed':True})
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        self.assertFalse(self.queue.hybrid_rules.request(self.account['id'],reason='素材刚读完时周期巡查到期'))
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()['data'])
        self.assertIsNone(d.get('command'));self.assertFalse(d.get('yielding',False));self.assertEqual(d['rule_yield_after_batch'],1)
        self.assertEqual(self.command()['phase'],'tasks');self.assertEqual(self.posts,[])

    def test_stale_candidates_do_not_hold_back_rule_scanning_without_a_live_lease(self):
        from monitor.hybrid_rules import HybridRules
        self.queue.hybrid_rules=HybridRules(self.queue)
        self.queue.rule_auto.configure({'account_id':self.account['id'],'plan_id':self.pid,'profile_revision':'r1',
            'features':['f'+str(i) for i in range(1,7)],'enabled':True,'confirmed':True})
        rid=self.start_api();self.hybrid.scan_api_materials(rid);self.at+=timedelta(seconds=601)
        self.assertTrue(self.queue.hybrid_rules.request(self.account['id'],reason='素材已过期，不锁住暂停规则'))
        self.assertEqual(self.posts,[])

    def test_rule_yield_never_revokes_an_inflight_native_read_lease(self):
        rid=self.start_api();self.hybrid.scan_api_materials(rid);c=self.command()
        self.assertFalse(self.hybrid.yield_readonly(self.account['id']))
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()['data'])
        self.assertEqual(d['command']['id'],c['id']);self.assertFalse(d.get('yielding',False));self.assertEqual(self.posts,[])

    def test_periodic_rule_fallback_waits_for_partial_native_inventory_and_keeps_its_lease(self):
        from monitor.hybrid_rules import HybridRules
        self.queue.hybrid_rules=HybridRules(self.queue)
        self.queue.rule_auto.configure({'account_id':self.account['id'],'plan_id':self.pid,'profile_revision':'r1',
            'features':['f'+str(i) for i in range(1,7)],'enabled':True,'confirmed':True})
        rid=self.start_api();self.hybrid.scan_api_materials(rid);c=self.command()
        self.assertFalse(self.queue.hybrid_rules.request(self.account['id'],reason='API读取限流'))
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()['data'])
        self.assertEqual(d['command']['id'],c['id']);self.assertEqual(d['rule_yield_after_batch'],1)
        self.assertFalse(d.get('yielding',False));self.assertEqual(self.posts,[])

    def test_verified_first_batch_hands_back_a_deferred_main_rule_scan(self):
        c=self.ready_api();permit=self.begin_api(c)
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data']);d['rule_yield_after_batch']=1
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2);v=self.command()
        self.feed(v,rows=[{'id':'8423542490044341','name':c['action']['intent']['browser_batch']['task_name'],
            'material_ids':c['action']['intent']['material_ids'],'after':c['action']['intent']['browser_batch']['after'],'status':'调控中'}],document='after-submit')
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertTrue(d['yielding']);self.assertNotIn('rule_yield_after_batch',d)
        self.assertGreater(d['rule_yield_queued_until'],self.at.isoformat())
        self.assertEqual(next(r for r in self.queue.status() if r['id']==c['action']['id'])['state'],'verified')

    def test_native_inventory_commands_bound_their_page_size_without_truncating_the_total(self):
        self.worker.update(worker_id='ego:inventory',extension_version='1.19.0')
        rid=self.start_api();self.worker['extension_version']='1.19.0';self.hybrid.scan_api_materials(rid)
        c=self.command();self.assertTrue(c['direct_home']);self.assertEqual(c['inventory_page_size'],10)
        self.assertEqual(c['phase'],'tasks');self.assertEqual(self.posts,[])

    def test_changing_read_errors_cannot_reset_the_failure_deadline(self):
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        with self.queue.db() as db:
            row=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone();d=json.loads(row['data'])
            d.update(phase='tasks',yielding=True,auto_retry_paused=False,command=None,resumed_at=self.at.isoformat(),
                read_stalled_at=(self.at-timedelta(minutes=11)).isoformat(),last_error={'at':self.at.isoformat(),'attempt':1,'raw_message':'另一个任务的浮层拦截'})
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),rid))
        self.queue.hybrid_rules=SimpleNamespace(active=lambda aid:True);self.hybrid.tick()
        with self.queue.db() as db:r=db.execute('SELECT state FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
        self.assertEqual(r['state'],'cancelled');self.assertEqual(self.posts,[])

    def test_failed_read_expiry_is_not_renewed_by_repeated_rule_priority_yields(self):
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        with self.queue.db() as db:
            row=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone();d=json.loads(row['data'])
            d.update(phase='tasks',yielding=True,auto_retry_paused=True,resumed_at=self.at.isoformat(),command=None,
                last_error={'phase':'tasks','attempt':5,'at':(self.at-timedelta(minutes=11)).isoformat(),'raw_message':'Ego在投素材明细未展开'})
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),rid))
        self.queue.hybrid_rules=SimpleNamespace(active=lambda aid:True)
        self.hybrid.tick()
        with self.queue.db() as db:r=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
        self.assertEqual(r['state'],'cancelled');self.assertLessEqual(self.queue.auto.row(self.account['id'])['next_at'],self.at.isoformat());self.assertEqual(self.posts,[])

    def test_failed_read_expiry_never_discards_a_consumed_unknown_write(self):
        c=self.ready_api();permit=self.begin_api(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        with self.queue.db() as db:
            row=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(row['data'])
            d.update(yielding=True,auto_retry_paused=True,command=None,last_error={'at':(self.at-timedelta(minutes=11)).isoformat()})
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
            db.execute("UPDATE api_actions SET state='unknown' WHERE id=?",(c['action']['id'],))
        self.queue.hybrid_rules=SimpleNamespace(active=lambda aid:True);self.hybrid.tick()
        self.assertEqual(next(r for r in self.queue.status() if r['id']==c['action']['id'])['state'],'unknown')
        with self.queue.db() as db:r=db.execute('SELECT state FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()
        self.assertNotEqual(r['state'],'cancelled');self.assertEqual(self.posts,[])

    def use_range(self):
        self.profile['features']['f5'].update(batch_size_min=2,batch_size_max=5,batch_size=5)
        self.queue.refresh_automatic_profile(self.account['id'])

    def test_range_keeps_a_three_material_tail_and_its_whole_batch_budget(self):
        self.use_range();c=self.ready_api();batch=c['action']['intent']['browser_batch']
        self.assertEqual(len(batch['material_ids']),3)
        self.assertEqual(float(c['action']['intent']['body']['budget']),200)
        self.assertEqual(self.posts,[])

    def test_range_skips_one_failed_material_and_prepares_two_with_the_same_budget(self):
        self.use_range();c=self.ready_api();mid=c['action']['intent']['browser_batch']['material_ids'][0]
        reply=self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,'message':'添加视频ID未在搜索结果唯一出现：'+mid+'；未提交','observation':None})
        self.assertTrue(reply['continue_round']);next_command=self.command()
        self.assertEqual(next_command['action']['intent']['browser_batch']['material_ids'],[m[0] for m in self.materials[1:]])
        self.assertEqual(float(next_command['action']['intent']['body']['budget']),200)
        self.assertEqual(self.queue.auto.daily(self.account['id']),1);self.assertEqual(self.posts,[])

    def test_repeated_unavailable_material_defers_just_that_id_across_rounds(self):
        for _ in range(2):
            c=self.ready_api(preview=True);mid=c['action']['intent']['browser_batch']['material_ids'][0]
            self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,'message':'添加视频ID未在搜索结果唯一出现：'+mid+'；未提交','observation':None})
            self.hybrid.stop({'account_id':self.account['id']})
        self.assertEqual(self.queue.deferred_materials(self.account['id'],self.pid),{mid})
        self.assertEqual(self.queue.material_exclusions(self.account['id']),[])
        c=self.ready_api(preview=True);self.assertNotIn(mid,c['action']['intent']['browser_batch']['material_ids'])
        self.assertEqual(self.posts,[])

    def test_verified_recent_history_does_not_reserve_but_unknown_write_still_does(self):
        c=self.ready_api();rid=c['action']['id'];mids=c['action']['intent']['browser_batch']['material_ids']
        with self.queue.db() as db:db.execute("UPDATE api_actions SET state='verified' WHERE id=?",(rid,))
        self.assertFalse(set(mids)&self.queue.recent_chase_materials(self.account['id'],self.pid))
        with self.queue.db() as db:db.execute("UPDATE api_actions SET state='unknown' WHERE id=?",(rid,))
        self.assertTrue(set(mids)<=self.queue.recent_chase_materials(self.account['id'],self.pid))

    def setUp(self):
        fixtures.HybridTests.setUp(self)
        self.plan['captured_at']=self.at.isoformat()
        clock=patch('monitor.official_workflow.now_cn',lambda:self.at);clock.start();self.addCleanup(clock.stop)
    tearDown=fixtures.HybridTests.tearDown
    transport=fixtures.HybridTests.transport
    confirm=fixtures.HybridTests.confirm
    command=fixtures.HybridTests.command
    observed=fixtures.HybridTests.observed
    feed=fixtures.HybridTests.feed
    tasks=fixtures.HybridTests.tasks

    def start_api(self,preview=False):
        self.worker['extension_version']='1.10.0'
        self.hybrid.configure({'account_id':self.account['id'],'mode':'api_data_browser'})
        self.assertTrue(self.hybrid.request(self.account['id'],reason='API取数网页追投测试',preview=preview))
        with self.hybrid.a.db() as db:
            row=db.execute("SELECT * FROM hybrid_runs WHERE state NOT IN ('completed','cancelled')").fetchone()
        return row['id']

    def ready_api(self,preview=False):
        rid=self.start_api(preview)
        self.hybrid.scan_api_materials(rid)
        tasks=self.command();self.assertEqual(tasks['phase'],'tasks');self.feed(tasks,self.tasks())
        return self.command()

    def selected(self,c):
        batch=c['action']['intent']['browser_batch']
        proof=[{'id':mid,'approved':True,'selectable':True,'chase_status':'-',
            'captured_at':self.at.isoformat()} for mid in batch['material_ids']]
        value=self.observed(c,[proof[-1]],page_size=20)
        return {**value,'selection_source':'official_api','selected_materials':proof}

    def begin_api(self,c,observation=None):
        return self.hybrid.begin({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],
            'action_id':c['action']['id'],'observation':observation or self.selected(c)})

    def home_selected(self,c):
        pre=self.selected(c);aweme='79987870170';base={k:pre[k] for k in ('account_id','plan_id','group','captured_at','parent_budget','delivery_entry','parent_bid_type','tab_id','document_id')}
        binding={**base,'aweme_id':aweme,'detail_url':f'https://qianchuan.jinritemai.com/overall-prom/detail?aavid={self.account["id"]}&adId={self.pid}'}
        home={**base,'path':'/overall-prom','url':f'https://qianchuan.jinritemai.com/overall-prom?aavid={self.account["id"]}',
            'aweme_id':aweme,'row_plan_id':None,'row_aweme_id':aweme,'running':True,'document_id':'home-form-document'}
        return {'entry_source':'home_plus','selection_source':'official_api','preflight':pre,'binding':binding,'home':home,'form_material_ids':c['action']['intent']['browser_batch']['material_ids'][:]}

    def ready_home(self,preview=False,version='1.11.0'):
        rid=self.start_api(preview);self.worker['extension_version']=version;self.hybrid.scan_api_materials(rid)
        self.feed(self.command(),self.tasks());c=self.command();self.assertEqual(c['preferred_entry'],'home_plus');return c

    def ready_direct(self):
        rid=self.start_api();self.worker.update(worker_id='ego:test',extension_version='1.14.0');self.hybrid.worker(self.worker)
        self.hybrid.scan_api_materials(rid);c=self.command();self.assertTrue(c['direct_home'])
        self.feed(c,[{**t,'material_ids':[],'material_type':'LIVE_ROOM'} for t in self.tasks()]);c=self.command();self.assertTrue(c['direct_home']);return c

    def test_ego_reader_prefetches_scoped_api_task_materials_without_requiring_native_material_search(self):
        rid=self.start_api();self.queue.browser_executor=SimpleNamespace(selected=lambda:True)
        self.hybrid.scan_api_materials(rid)
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()['data'])
        snapshot=d['api_active_snapshot'];self.assertEqual(snapshot['account_id'],self.account['id']);self.assertEqual(snapshot['plan_id'],self.pid)
        self.assertEqual(snapshot['task_materials']['10001'],[self.materials[0][0]]);self.assertEqual(self.posts,[])

    def direct_selected(self,c):
        v=self.home_selected(c);pre=v['preflight']
        v['preflight']={k:pre[k] for k in ('account_id','plan_id','group','path','parent_budget','running','delivery_entry','parent_bid_type','captured_at','tab_id','document_id')}
        v['preflight']['kind']='direct_home_identity';v['picker_materials']=[{k:p[k] for k in ('id','approved','selectable','captured_at')} for p in pre['selected_materials']];return v

    def test_direct_home_once_only_submit_accepts_picker_approval_without_a_main_material_search(self):
        c=self.ready_direct();v=self.direct_selected(c);permit=self.begin_api(c,v)
        self.assertTrue(permit['submit_once']);self.assertEqual(self.posts,[])
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        with self.assertRaises(ValueError):self.begin_api(c,v)

    def test_direct_home_never_accepts_missing_review_foreign_ids_or_an_incomplete_active_inventory(self):
        c=self.ready_direct();base=self.direct_selected(c)
        changes=[]
        v=copy.deepcopy(base);v['picker_materials'][0]['approved']=False;changes.append(v)
        v=copy.deepcopy(base);v['picker_materials'][0]['id']='80830973211148672';changes.append(v)
        v=copy.deepcopy(base);v['picker_materials'].pop();changes.append(v)
        v=copy.deepcopy(base);v['preflight']['account_id']='8086606404052108';changes.append(v)
        for v in changes:
            with self.assertRaises(ValueError):self.begin_api(c,v)
        with self.queue.db() as db:
            r=db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(r['data']);d['direct_inventory_complete']=False;db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        with self.assertRaises(ValueError):self.begin_api(c,base)
        self.assertEqual(self.queue.status()[0]['state'],'draft');self.assertEqual(self.posts,[])

    def test_direct_home_filters_all_ids_from_the_complete_active_inventory_before_grouping(self):
        self.materials.append(('88712204312382425',200,4.8));rid=self.start_api();self.worker.update(worker_id='ego:test',extension_version='1.14.0');self.hybrid.worker(self.worker)
        self.hybrid.scan_api_materials(rid);c=self.command();active=self.materials[0][0]
        self.feed(c,[{**t,'material_ids':[active]} for t in self.tasks()]);c=self.command()
        self.assertNotIn(active,c['action']['intent']['browser_batch']['material_ids']);self.assertEqual(self.posts,[])

    def test_direct_inventory_keeps_live_room_budget_and_only_deduplicates_video_ids(self):
        self.materials.append(('88712204312382425',200,4.8));rid=self.start_api()
        self.worker.update(worker_id='ego:test',extension_version='1.14.2');self.hybrid.worker(self.worker)
        self.hybrid.scan_api_materials(rid);c=self.command();active=self.materials[0][0]
        self.feed(c,[{'id':'10001','budget':'100','status':'调控中','material_ids':[active],'material_type':'VIDEO'},
            {'id':'10002','budget':'200','status':'调控中','material_ids':[],'material_type':'LIVE_ROOM'}])
        c=self.command();self.assertNotIn(active,c['action']['intent']['browser_batch']['material_ids'])
        with self.queue.db() as db:
            r=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone();d=json.loads(r['data'])
            self.assertEqual(d['tasks']['10002']['budget'],'200');self.assertEqual(d['direct_active_material_ids'],[active])
            d['parent_budget']='1000'
            with self.assertRaisesRegex(ValueError,'母计划预算30%'):self.hybrid.budget_check(r,d,1)
        self.assertEqual(self.posts,[])

    def test_direct_inventory_rejects_untyped_empty_ids_and_live_room_with_video_ids(self):
        rid=self.start_api();self.worker.update(worker_id='ego:test',extension_version='1.14.2');self.hybrid.worker(self.worker)
        self.hybrid.scan_api_materials(rid);c=self.command()
        for row in [dict(material_ids=[]),dict(material_ids=[],material_type='VIDEO'),dict(material_ids=[self.materials[0][0]],material_type='LIVE_ROOM')]:
            with self.assertRaisesRegex(ValueError,'素材类型与ID清单不符'):self.feed(c,[{**self.tasks()[0],**row}])
        self.assertEqual(self.posts,[]);self.assertEqual(self.queue.status(),[])

    def test_home_preview_preserves_real_home_document_and_detail_preflight_without_a_write(self):
        c=self.ready_home(preview=True);v=self.home_selected(c)
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':True,'message':'首页填写后取消','observation':v})
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        evidence=d['preview_evidence'];self.assertEqual(evidence['entry_source'],'home_plus');self.assertEqual(evidence['path'],'/overall-prom')
        self.assertEqual(d['preview_entry_message'],'首页填写后取消')
        self.assertEqual(evidence['document_id'],'home-form-document');self.assertEqual(evidence['home_context']['binding']['document_id'],'document-read')
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_home_submit_uses_same_once_only_ledger_and_independent_original_readback(self):
        c=self.ready_home();permit=self.begin_api(c,self.home_selected(c))
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2);verify=self.command();batch=verify['action']['intent']['browser_batch']
        self.feed(verify,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],'after':batch['after'],'status':'调控中'}],document='independent-detail-readback')
        self.assertEqual(self.queue.status()[0]['state'],'verified');self.assertEqual(self.posts,[])
        with self.assertRaises(ValueError):self.begin_api(c,self.home_selected(c))

    def test_home_scope_id_set_budget_source_document_and_age_conflicts_never_get_a_permit(self):
        c=self.ready_home();base=self.home_selected(c);variants=[]
        for key,field,value in [('binding','account_id','99999999'),('binding','document_id','foreign-source'),('binding','aweme_id','11111111'),
            ('home','row_plan_id','99999999'),('home','row_aweme_id','11111111'),('home','tab_id',12),('home','running',False),
            ('home','parent_budget','20000'),('home','captured_at',(self.at-timedelta(seconds=91)).isoformat()),
            ('home','url',base['home']['url']+'&aavid=99999999'),('binding','detail_url','https://example.com/overall-prom/detail')]:
            v=copy.deepcopy(base);v[key][field]=value;variants.append(v)
        v=copy.deepcopy(base);v['form_material_ids'][0]='99999999';variants.append(v)
        v=copy.deepcopy(base);v['form_material_ids'].append(v['form_material_ids'][0]);variants.append(v)
        v=copy.deepcopy(base);v['preflight']['selected_materials'][0]['approved']=False;variants.append(v)
        v=copy.deepcopy(base);v['home']['row_aweme_id']=None;variants.append(v)
        for v in variants:
            with self.subTest(v=v),self.assertRaises(ValueError):self.begin_api(c,v)
        self.assertEqual(self.queue.status()[0]['state'],'draft');self.assertEqual(self.posts,[])

    def test_old_api_extension_keeps_detail_route_and_cannot_return_home_form_proof(self):
        c=self.ready_api();self.assertNotIn('preferred_entry',c)
        with self.assertRaisesRegex(ValueError,'1.11.0'):self.begin_api(c,self.home_selected(c))

    def test_a_hidden_detail_uid_can_use_a_fresh_unique_official_plan_anchor_without_an_extra_request(self):
        self.plan['account_id']=self.account['id'];c=self.ready_home(preview=True,version='1.11.2');calls=len(self.calls)
        self.assertEqual(c['parent_anchor']['aweme_id'],self.uid);v=self.home_selected(c);v['binding']['aweme_source']='official_plan_list'
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':True,'message':'首页核验后取消','observation':v})
        self.assertEqual(len(self.calls),calls);self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_old_stale_foreign_or_nonunique_plan_anchors_are_not_accepted_as_a_fresh_uid(self):
        self.plan['account_id']=self.account['id'];run={'account_id':self.account['id'],'plan_id':self.pid};base=copy.deepcopy(self.plan)
        self.assertEqual(self.hybrid.parent_anchor(run)['aweme_id'],self.uid)
        for change in ({'captured_at':(self.at-timedelta(seconds=601)).isoformat()},{'account_id':'99999999'}, {'anchors':[]},{'configuration_source':'guessed'}):
            with self.subTest(change=change):
                self.plan.clear();self.plan.update(copy.deepcopy(base),**change);self.assertIsNone(self.hybrid.parent_anchor(run))
        self.plan.clear();self.plan.update(base);self.api.plan_readings[self.account['id']]['plans'].append({**base,'plan_id':'88888888'})
        self.assertIsNone(self.hybrid.parent_anchor(run));self.assertEqual(self.posts,[])

    def test_a_home_form_cannot_invent_an_official_uid_source_without_the_issued_command_proof(self):
        c=self.ready_home();v=self.home_selected(c);v['binding']['aweme_source']='official_plan_list'
        with self.assertRaises(ValueError):self.begin_api(c,v)
        self.assertEqual(self.queue.status()[0]['state'],'draft');self.assertEqual(self.posts,[])

    def test_home_url_allows_one_native_empty_ad_id_but_rejects_duplicate_query_values(self):
        c=self.ready_home();v=self.home_selected(c);v['home']['url']+='&adId=&adId='
        with self.assertRaises(ValueError):self.begin_api(c,v)
        v=self.home_selected(c);v['home']['url']+='&adId='
        self.assertTrue(self.begin_api(c,v)['submit_once']);self.assertEqual(self.posts,[])

    def probe_command(self,version='1.11.3'):
        self.hybrid.configure({'account_id':self.account['id'],'mode':'api_data_browser'})
        self.worker.update(extension_version=version,armed=False)
        self.hybrid.request_probe({'account_id':self.account['id'],'target':'home','selectors':['tr','input']})
        return self.hybrid.next(self.worker)['command']

    def test_rendered_dom_probe_needs_no_api_metric_request_and_cannot_obtain_a_write_permit(self):
        calls=len(self.calls);c=self.probe_command();self.assertEqual(c['phase'],'dom_probe');self.assertIsNone(c['action']);self.assertTrue(c['preview'])
        with self.queue.db() as db:self.assertTrue(self.hybrid.preview_ready(db))
        with self.assertRaises(ValueError):self.hybrid.begin({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'action_id':'invented','observation':{}})
        v={'account_id':self.account['id'],'plan_id':self.pid,'group':self.account['tab_group'],'path':'/overall-prom',
            'url':'https://qianchuan.jinritemai.com/overall-prom?aavid='+self.account['id'],'captured_at':self.at.isoformat(),
            'tab_id':11,'document_id':'probe-document','rows':{'tr':[{'tag':'TR','text':'投放中'}],'input':[]}}
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':True,'message':'只读结构','observation':v})
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertEqual(d['probe_evidence'],v);self.assertNotIn('preview_evidence',d);self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertEqual(len(self.calls),calls);self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_dom_probe_rejects_unrequested_accounts_selectors_or_oversized_results(self):
        c=self.probe_command();base={'account_id':self.account['id'],'plan_id':self.pid,'group':self.account['tab_group'],'path':'/overall-prom',
            'url':'https://qianchuan.jinritemai.com/overall-prom?aavid='+self.account['id'],'captured_at':self.at.isoformat(),
            'tab_id':11,'document_id':'probe-document','rows':{'tr':[],'input':[]}}
        variants=[]
        for field,value in [('account_id','99999999'),('group','另一个账户'),('url','https://example.com/overall-prom'),('path','/overall-prom/detail')]:
            v=copy.deepcopy(base);v[field]=value;variants.append(v)
        v=copy.deepcopy(base);v['rows']['script']=[];variants.append(v)
        v=copy.deepcopy(base);v['rows']['tr']=['x']*9;variants.append(v)
        v=copy.deepcopy(base);v['rows']['tr']=['x'*48000];variants.append(v)
        for v in variants:
            with self.subTest(v=list(v)),self.assertRaises(ValueError):self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':True,'message':'只读结构','observation':v})
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_dom_probe_requires_its_reader_version_and_rejects_arbitrary_code(self):
        self.assertIsNone(self.probe_command('1.11.2'));self.assertIn('1.11.3',self.hybrid.status()[0]['message'])
        for change in ({'code':'alert(1)'},{'selectors':['x'*181]},{'target':'create'}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.hybrid.request_probe({'account_id':self.account['id'],'target':'home','selectors':['tr'],**change})
        self.worker['extension_version']='1.11.3';self.assertEqual(self.command()['phase'],'dom_probe');self.assertEqual(self.queue.status(),[])

    def test_metric_candidates_do_not_call_throttled_task_list_or_per_material_identity(self):
        original=self.api.transport
        def transport(name,q,token=None):
            if name in {'control_tasks','plan_materials'}:raise AssertionError('候选扫描不应依赖任务或逐素材配置接口')
            return original(name,q,token)
        self.api.transport=transport
        c=self.ready_api()
        self.assertEqual(c['phase'],'prepare')
        self.assertEqual(c['account_binding'],{'kind':'official_api_material_scope','account_id':self.account['id'],'plan_id':self.pid,'verified_at':self.at.isoformat()})
        batch=c['action']['intent']['browser_batch']
        self.assertEqual(batch['material_ids'],[m[0] for m in self.materials[:2]])
        self.assertEqual(batch['selection_source'],'official_api')
        value=self.api.workflows[self.account['id']+':'+self.pid]
        self.assertTrue(value['eligible_range_complete']);self.assertFalse(value['tasks_complete'])
        self.assertFalse(value['batches'][0]['ready'])
        self.assertFalse(value['materials'][0]['approved'])
        self.assertEqual(self.posts,[])

    def test_browser_candidate_metrics_do_not_wait_for_a_throttled_fresh_plan_list_when_native_routing_is_known(self):
        self.plan['captured_at']=(self.at-timedelta(hours=2)).isoformat()
        with patch.object(self.api,'read_plans',side_effect=OfficialBusinessError(40100,operation='plans',reason='rate_limited',retry_after=600)) as read:
            c=self.ready_api(preview=True);self.assertEqual(c['phase'],'dry_prepare');read.assert_not_called()
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_normal_api_workflows_still_require_fresh_plan_configuration(self):
        self.plan['captured_at']=(self.at-timedelta(hours=2)).isoformat()
        with patch.object(self.api,'read_plans',side_effect=OfficialBusinessError(40100,operation='plans',reason='rate_limited',retry_after=600)):
            with self.assertRaises(OfficialBusinessError):self.api.scan_workflow(self.account,self.profile,self.pid,materials=True)
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_complete_existing_api_task_ids_filter_running_materials_without_another_task_request(self):
        rid=self.start_api();key=self.account['id']+':'+self.pid
        self.api.workflows[key]={'account_id':self.account['id'],'plan_id':self.pid,'date':self.at.date().isoformat(),
            'captured_at':self.at.isoformat(),'tasks_complete':True,'tasks':[{'account_id':self.account['id'],
            'plan_id':self.pid,'scene':'MATERIAL_ADD_BUDGET','status':'PROCESSING','material_ids':[self.materials[0][0]]}]}
        self.hybrid.scan_api_materials(rid);calls=len(self.calls)
        self.assertFalse(self.api.workflows[key]['tasks_complete'])
        self.feed(self.command(),self.tasks());c=self.command()
        self.assertEqual(c['action']['intent']['browser_batch']['material_ids'],[m[0] for m in self.materials[1:]])
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()['data'])
        self.assertEqual(d['api_running_materials'],[self.materials[0][0]])
        self.assertEqual(len(self.calls),calls);self.assertEqual(self.posts,[])

    def test_stale_incomplete_or_foreign_api_task_reads_cannot_filter_this_accounts_candidates(self):
        for change in ({'captured_at':(self.at-timedelta(seconds=301)).isoformat()}, {'tasks_complete':False}, {'account_id':'8086606404052108'}):
            with self.subTest(change=change):
                rid=self.start_api(preview=True);key=self.account['id']+':'+self.pid
                self.api.workflows[key]={'account_id':self.account['id'],'plan_id':self.pid,'date':self.at.date().isoformat(),
                    'captured_at':self.at.isoformat(),'tasks_complete':True,'tasks':[{'account_id':self.account['id'],
                    'plan_id':self.pid,'scene':'MATERIAL_ADD_BUDGET','status':'PROCESSING','material_ids':[self.materials[0][0]]}],**change}
                self.hybrid.scan_api_materials(rid);self.feed(self.command(),self.tasks());c=self.command()
                self.assertEqual(c['action']['intent']['browser_batch']['material_ids'],[m[0] for m in self.materials[:2]])
                self.hybrid.stop({'account_id':self.account['id']})
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_browser_receipt_requires_exact_selected_ids_then_verifies_original_once(self):
        c=self.ready_api();permit=self.begin_api(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2)
        verify=self.command();batch=verify['action']['intent']['browser_batch']
        self.feed(verify,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],
            'after':batch['after'],'status':'调控中'}],document='fresh-readback-document')
        self.assertEqual(self.queue.status()[0]['state'],'verified')
        self.assertEqual(self.queue.auto.daily(self.account['id']),1)
        self.assertEqual(self.posts,[])
        with self.assertRaises(ValueError):self.begin_api(c)

    def test_wrong_id_status_stale_api_snapshot_and_wrong_date_cannot_get_a_permit(self):
        c=self.ready_api();base=self.selected(c)
        variants=[]
        v=copy.deepcopy(base);v['selected_materials'][0]['id']='44444444';variants.append(v)
        v=copy.deepcopy(base);v['selected_materials'][0]['chase_status']='调控中';variants.append(v)
        v=copy.deepcopy(base);v['selected_materials'][0]['approved']=False;variants.append(v)
        v=copy.deepcopy(base);v['selected_materials'][0]['selectable']=False;variants.append(v)
        v=copy.deepcopy(base);v['selected_materials'][0]['captured_at']=(self.at-timedelta(seconds=91)).isoformat();variants.append(v)
        v=copy.deepcopy(base);v['date_start']='2000-01-01';variants.append(v)
        for value in variants:
            with self.subTest(value=value),self.assertRaises(ValueError):self.begin_api(c,value)
        self.assertEqual(self.queue.status()[0]['state'],'draft');self.assertEqual(self.posts,[])

    def test_stale_metrics_refresh_via_api_instead_of_native_material_scan(self):
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        self.at+=timedelta(seconds=601)
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertEqual(self.hybrid.status()[0]['phase'],'api_materials')
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_api_refresh_retains_this_sweeps_native_running_exclusions_and_regroups_other_ids(self):
        self.materials=[(str(89581211092803411+n),500-n,4) for n in range(6)]
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        with self.queue.db() as db:
            r=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone();d=json.loads(r['data'])
            mid=d['batches'][0]['material_ids'][0];d['native_running_materials']=[mid]
            self.hybrid.reset_materials(d,'fresh API data required');self.hybrid.save(db,r,d)
        self.hybrid.scan_api_materials(rid)
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone()['data'])
        ids=[mid for batch in d['batches'] for mid in batch['material_ids']]
        self.assertNotIn(mid,ids);self.assertEqual(len(ids),4);self.assertEqual(len(set(ids)),4)
        self.assertEqual(self.posts,[]);self.assertEqual(self.queue.status(),[])

    def test_inflight_budget_limit_is_shared_with_api_actions(self):
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        with self.queue.db() as db:db.execute('UPDATE api_auto SET budget_cap=? WHERE account_id=?',('199',self.account['id']))
        self.feed(self.command(),self.tasks())
        self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertIn('超过在投预算上限',self.hybrid.status()[0]['message'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_report_rate_limit_waits_without_web_material_scan_or_partial_batches(self):
        rid=self.start_api();original=self.api.transport
        def transport(name,q,token=None):
            if name=='report_data':raise OfficialBusinessError(40100,operation=name,reason='rate_limited',retry_after=600)
            return original(name,q,token)
        self.api.transport=transport;self.hybrid.scan_api_materials(rid)
        state=self.hybrid.status()[0]
        self.assertEqual(state['phase'],'api_materials')
        self.assertEqual(state['batches_ready'],0)
        self.assertEqual(self.hybrid.next(self.worker)['command'],None)
        self.assertIn('不会退回网页全量扫描',state['message'])
        self.assertEqual(state['retry_at'],(self.at+timedelta(seconds=600)).isoformat())
        self.assertEqual(self.queue.status(),[])

    def test_old_extension_cannot_receive_api_selected_batch(self):
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        self.worker['extension_version']='1.9.2'
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertIn('1.10.0',self.hybrid.status()[0]['message'])
        self.assertEqual(self.queue.status(),[])

    def test_api_metrics_can_queue_with_web_execution_closed_but_no_native_command_is_issued(self):
        self.worker.update(extension_version='1.10.0',armed=False);self.hybrid.worker(self.worker)
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertEqual(self.hybrid.status()[0]['state'],'waiting')
        self.assertIn('等待原Chrome开启',self.hybrid.status()[0]['message'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])
        self.worker['armed']=True;self.assertEqual(self.command()['phase'],'tasks')

    def test_api_read_preview_is_allowed_behind_unknown_action_but_cannot_prepare_or_submit(self):
        with self.queue.db() as db:
            intent={'account_id':self.account['id'],'plan_id':self.pid,'action':'pause_task','task_id':'10001'}
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('original-unknown','unknown',json.dumps(intent),'test',self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),json.dumps({'message':'原单待核验'})))
        class BusyRules:
            def active(self,aid):return True
        self.queue.hybrid_rules=BusyRules()
        rid=self.start_api(preview=True);self.hybrid.scan_api_materials(rid)
        self.assertTrue(self.hybrid.status()[0]['eligible_range_complete'])
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertEqual(len(self.queue.status()),1);self.assertEqual(self.queue.status()[0]['state'],'unknown')
        self.assertEqual(self.posts,[])

    def test_api_candidate_preview_only_fills_and_cancels_with_no_quota_reservation(self):
        c=self.ready_api(preview=True);self.assertEqual(c['phase'],'dry_prepare')
        self.worker['armed']=False
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':True,
            'message':'预演并取消','observation':self.selected(c)})
        self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertEqual(self.queue.auto.daily(self.account['id']),0);self.assertEqual(self.queue.status(),[])
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertEqual([r['id'] for r in d['preview_evidence']['selected_materials']],c['action']['intent']['browser_batch']['material_ids'])

    def test_complete_api_pages_group_across_page_boundary_without_limit_to_first_100(self):
        original=self.api.transport
        self.materials=[(str(81231121243009031+i),500-i,4.2) for i in range(205)]
        def transport(name,q,token=None):
            result=original(name,q,token)
            if name=='report_data' and q['data_topic']=='OVERALL_ROI_LIVE_MATERIAL_VIDEO':
                result['rows']=result['rows'][(q['page']-1)*q['page_size']:q['page']*q['page_size']]
            return result
        self.api.transport=transport
        rid=self.start_api();self.hybrid.scan_api_materials(rid)
        state=self.hybrid.status()[0]
        self.assertEqual((state['pages_read'],state['materials_read']),(2,205))
        self.assertEqual(state['spend_threshold_materials'],205)
        self.assertEqual(state['batches_ready'],102);self.assertEqual(state['unbatched_materials'],1)
        self.assertTrue(state['eligible_range_complete']);self.assertEqual(self.posts,[])

    def test_cancelling_during_scan_discards_result_and_does_not_block_other_scope(self):
        rid=self.start_api();original=self.api.transport
        def transport(name,q,token=None):
            result=original(name,q,token)
            if name=='report_data' and q['data_topic']=='OVERALL_ROI_LIVE_MATERIAL_VIDEO':self.hybrid.stop({'account_id':self.account['id']})
            return result
        self.api.transport=transport;self.hybrid.scan_api_materials(rid)
        self.assertEqual(self.hybrid.status()[0]['state'],'cancelled')
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_an_older_unknown_action_in_another_account_does_not_consume_the_browser_poll(self):
        rid=self.start_api(preview=True);self.hybrid.scan_api_materials(rid)
        with self.queue.db() as db:
            own=db.execute('SELECT data FROM hybrid_runs WHERE id=?',(rid,)).fetchone();data=json.loads(own['data'])
            db.execute('INSERT INTO hybrid_runs VALUES(?,?,?,?,?,?)',('blocked-other','88888888','77777777','waiting',(self.at-timedelta(hours=1)).isoformat(),json.dumps(data)))
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('unknown-other','unknown',json.dumps({'account_id':'88888888','plan_id':'77777777','action':'pause_task'}),'test',self.at.isoformat(),self.at.isoformat(),self.at.isoformat(),'{}'))
        c=self.command();self.assertEqual(c['run_id'],rid);self.assertEqual(c['phase'],'tasks')
        self.assertEqual(self.posts,[])

    def test_explicit_readonly_preview_survives_api_rule_yield_and_can_take_one_unarmed_browser_slot(self):
        rid=self.start_api(preview=True);self.hybrid.scan_api_materials(rid)
        self.hybrid.yield_readonly(self.account['id'])
        self.assertEqual(self.hybrid.status()[0]['state'],'waiting')
        with self.queue.db() as db:self.assertTrue(self.hybrid.preview_ready(db))
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_readonly_preview_replaces_an_unsent_automatic_sweep_instead_of_attaching_to_a_live_run(self):
        original=self.start_api(preview=False)
        self.assertTrue(self.hybrid.request(self.account['id'],reason='明确请求只读预演',preview=True))
        with self.queue.db() as db:
            old=db.execute('SELECT state FROM hybrid_runs WHERE id=?',(original,)).fetchone()
            current=db.execute("SELECT id,data FROM hybrid_runs WHERE state NOT IN ('completed','cancelled')").fetchone()
        self.assertEqual(old['state'],'cancelled');self.assertNotEqual(current['id'],original)
        self.assertTrue(json.loads(current['data'])['preview'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_a_changed_live_total_restarts_the_complete_read_once_without_using_partial_batches(self):
        self.materials=[(str(81231121243009031+i),500-i,4.2) for i in range(205)]
        original=self.api.transport;pages=[]
        def transport(name,q,token=None):
            result=original(name,q,token)
            if name=='report_data' and q['data_topic']=='OVERALL_ROI_LIVE_MATERIAL_VIDEO':
                pages.append(q['page']);result['rows']=result['rows'][(q['page']-1)*q['page_size']:q['page']*q['page_size']]
                if pages==[1,2]:result['page_info']['total_number']+=1
            return result
        self.api.transport=transport;rid=self.start_api(preview=True);self.hybrid.scan_api_materials(rid)
        self.assertEqual(pages,[1,2,1,2]);self.assertEqual(self.hybrid.status()[0]['materials_read'],205)
        self.assertTrue(self.hybrid.status()[0]['eligible_range_complete'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_live_rule_priority_preserves_api_candidates_then_resumes_without_a_metric_rescan(self):
        rid=self.start_api();self.hybrid.scan_api_materials(rid);calls=len(self.calls)
        before=self.hybrid.status()[0]['batches_ready'];self.hybrid.yield_readonly(self.account['id'])
        self.assertTrue(self.hybrid.active(self.account['id']));self.assertFalse(self.hybrid.active(self.account['id'],writes_only=True))
        self.assertTrue(self.hybrid.status()[0]['yielding']);self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.hybrid.tick();self.assertFalse(self.hybrid.status()[0]['yielding'])
        self.assertEqual(self.hybrid.status()[0]['batches_ready'],before);self.assertEqual(len(self.calls),calls)
        self.assertEqual(self.command()['phase'],'tasks');self.assertEqual(self.posts,[])

    def test_a_running_material_is_skipped_and_remainders_are_regrouped_in_the_same_readonly_preview(self):
        c=self.ready_api(preview=True);calls=len(self.calls);mid=c['action']['intent']['browser_batch']['material_ids'][0]
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'提交前素材条件已变化：素材正在追投 '+mid,'observation':None})
        self.assertEqual(self.hybrid.status()[0]['state'],'waiting');self.assertEqual(self.hybrid.status()[0]['phase'],'dry_prepare')
        again=self.command()
        self.assertEqual(again['phase'],'dry_prepare')
        self.assertEqual(again['action']['intent']['browser_batch']['material_ids'],[m[0] for m in self.materials[1:]])
        self.assertEqual(len(self.calls),calls);self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_regrouping_cancels_only_an_unsent_draft_and_keeps_shared_quota_available(self):
        c=self.ready_api();mid=c['action']['intent']['material_ids'][0]
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'提交前素材条件已变化：素材正在追投 '+mid,'observation':None})
        old=next(r for r in self.queue.status() if r['id']==c['action']['id'])
        self.assertEqual(old['state'],'cancelled');self.assertEqual(self.queue.auto.daily(self.account['id']),1)
        self.hybrid.stop({'account_id':self.account['id']});self.assertEqual(self.queue.auto.daily(self.account['id']),0)

    def test_native_deleted_material_skips_only_that_id_without_permanent_exclusion_or_quota(self):
        c=self.ready_api();mid=c['action']['intent']['browser_batch']['material_ids'][0]
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'本批素材已删除 '+mid+'；未提交','observation':None})
        with self.queue.db() as db:
            data=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertEqual(data['unavailable_materials'],[mid]);self.assertEqual(self.queue.material_exclusions(self.account['id']),[])
        self.assertFalse(any(mid in b['material_ids'] for b in data['batches']))
        self.assertEqual(self.queue.auto.daily(self.account['id']),1);self.assertEqual(self.posts,[])
        self.hybrid.stop({'account_id':self.account['id']});self.assertEqual(self.queue.auto.daily(self.account['id']),0)

    def test_multiple_native_running_ids_are_regrouped_once_and_never_create_or_consume_quota(self):
        c=self.ready_api();mids=c['action']['intent']['browser_batch']['material_ids']
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'提交前素材条件已变化：'+'；'.join('素材正在追投 '+mid for mid in mids),'observation':None})
        with self.queue.db() as db:
            data=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertTrue(set(mids)<=set(data['native_running_materials']))
        self.assertFalse(set(mids)&{mid for batch in data['batches'] for mid in batch['material_ids']})
        self.assertEqual(self.queue.auto.daily(self.account['id']),0);self.assertEqual(self.posts,[])
        self.assertEqual(next(r for r in self.queue.status() if r['id']==c['action']['id'])['state'],'cancelled')

    def test_native_skip_cannot_poison_candidates_outside_the_bound_batch(self):
        c=self.ready_api();mids=c['action']['intent']['browser_batch']['material_ids'];foreign=self.materials[-1][0]
        self.assertNotIn(foreign,mids)
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'素材正在追投 '+mids[0]+'；素材正在追投 '+foreign,'observation':None})
        with self.queue.db() as db:
            data=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertNotIn(foreign,data.get('native_running_materials',[]));self.assertEqual(self.posts,[])
        self.assertEqual(self.posts,[])

    def test_only_a_bound_unavailable_id_is_skipped_without_pausing_all_candidates(self):
        for phrase in ('本批素材审核未通过 ','本批素材左侧选择框已禁用 ','添加视频审核未通过 ','添加视频左侧选择框已禁用 ','本批素材ID搜索为空 '):
            self.queue.clear_deferrals({'account_id':self.account['id']})
            c=self.ready_api(preview=True);mid=c['action']['intent']['browser_batch']['material_ids'][0]
            reply=self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,'message':phrase+mid,'observation':None})
            self.assertTrue(reply['continue_round']);self.assertEqual(self.command()['action']['intent']['browser_batch']['material_ids'],[m[0] for m in self.materials[1:]])
            self.hybrid.stop({'account_id':self.account['id']})
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_an_unavailable_id_from_outside_the_bound_batch_cannot_change_its_candidates(self):
        c=self.ready_api(preview=True)
        reply=self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,'message':'本批素材ID搜索为空 8086606404052108','observation':None})
        self.assertNotIn('continue_round',reply)
        self.assertEqual(self.hybrid.status()[0]['phase'],'dry_prepare');self.assertIsNotNone(self.hybrid.status()[0]['last_error'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_an_ambiguous_inspection_failure_never_blacklists_a_candidate(self):
        c=self.ready_api(preview=True);mid=c['action']['intent']['browser_batch']['material_ids'][0]
        reply=self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'本批素材审核或勾选状态不符：'+mid,'observation':None})
        self.assertNotIn('continue_round',reply)
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertFalse(d.get('unavailable_materials'));self.assertEqual(d['batches'][0]['material_ids'],c['action']['intent']['browser_batch']['material_ids'])
        self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_unknown_audit_skips_only_the_unclear_id_and_keeps_other_candidates_without_consuming_quota(self):
        self.materials.append(('88712204312382425',200,4.8))
        c=self.ready_api();mids=c['action']['intent']['browser_batch']['material_ids']
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'等待素材审核状态异步加载 '+mids[0]+'；未提交','observation':None})
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        with self.queue.db() as db:data=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertEqual(data['unavailable_materials'],[mids[0]])
        again=self.command();new=again['action']['intent']['browser_batch']['material_ids']
        self.assertNotIn(mids[0],new);self.assertIn(mids[1],new);self.assertEqual(len(new),len(mids))
        self.assertEqual(self.queue.deferred_materials(self.account['id'],self.pid),set())
        self.assertEqual(self.queue.material_exclusions(self.account['id']),[])
        self.assertEqual(self.queue.auto.daily(self.account['id']),1);self.assertEqual(self.posts,[])
        self.hybrid.stop({'account_id':self.account['id']});self.assertEqual(self.queue.auto.daily(self.account['id']),0)

    def test_regroup_duplicate_fence_sees_cancel_in_its_transaction_and_still_reserves_unknown_submissions(self):
        c=self.ready_api();intent=c['action']['intent'];mids=intent['material_ids'];other=copy.deepcopy(intent);other['material_ids']=['80830973211148672']
        with self.queue.db() as db:
            original=db.execute('SELECT * FROM api_actions WHERE id=?',(c['action']['id'],)).fetchone()
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('other-unknown','unknown',json.dumps(other),'test-digest',original['created_at'],original['expires_at'],original['updated_at'],None))
            db.execute("UPDATE api_actions SET state='cancelled' WHERE id=?",(c['action']['id'],))
            reserved=self.queue.recent_chase_materials(self.account['id'],self.pid,db=db)
            self.assertFalse(set(mids)&reserved);self.assertEqual(reserved,{'80830973211148672'})
        self.assertEqual(self.posts,[])

    def test_missing_or_reviewing_picker_id_is_replaced_without_discarding_the_other_good_candidate(self):
        self.materials.append(('88712204312382425',200,4.8))
        for reason in ('添加视频ID未在搜索结果唯一出现：','添加视频审核未通过 '):
            with self.subTest(reason=reason):
                c=self.ready_api();mids=c['action']['intent']['browser_batch']['material_ids'];calls=len(self.calls)
                self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
                    'message':reason+mids[0]+'；未提交','observation':None})
                again=self.command();new=again['action']['intent']['browser_batch']['material_ids']
                self.assertIn(mids[1],new);self.assertNotIn(mids[0],new);self.assertEqual(len(new),len(mids))
                self.assertEqual(len(self.calls),calls);self.assertEqual(self.posts,[])
                self.hybrid.stop({'account_id':self.account['id']})

    def test_a_precise_new_reader_rechecks_old_temporary_unavailable_results_without_touching_permanent_exclusions(self):
        c=self.ready_api(preview=True);mid=c['action']['intent']['browser_batch']['material_ids'][0]
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data']);d['unavailable_materials']=[mid]
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        before=self.queue.material_exclusions(self.account['id'])
        self.hybrid.worker({**self.worker,'extension_version':'1.10.6'})
        self.assertEqual(self.hybrid.status()[0]['phase'],'api_materials')
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertEqual(d['unavailable_materials'],[]);self.assertTrue(d['selection_reader_fixed'])
        self.assertEqual(self.queue.material_exclusions(self.account['id']),before);self.assertEqual(self.posts,[])

    def test_reader_migration_keeps_an_uncertain_original_action(self):
        c=self.ready_api();action=c['action']['id']
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data']);d['unavailable_materials']=['89581211092803411']
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']));db.execute("UPDATE api_actions SET state='unknown' WHERE id=?",(action,))
        self.hybrid.worker({**self.worker,'extension_version':'1.10.6'})
        with self.queue.db() as db:d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
        self.assertEqual(d['action_id'],action);self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertEqual(self.posts,[])

    def test_expired_metrics_never_discard_an_uncertain_action_even_if_its_phase_lags(self):
        c=self.ready_api();action=c['action']['id'];self.at+=timedelta(seconds=601)
        with self.queue.db() as db:db.execute("UPDATE api_actions SET state='unknown' WHERE id=?",(action,))
        verify=self.command();self.assertEqual(verify['phase'],'verify');self.assertEqual(verify['action']['id'],action)
        self.assertEqual(self.queue.status()[0]['state'],'unknown');self.assertEqual(self.posts,[])

    def test_running_material_regroup_reads_inventory_again_when_the_completed_inventory_is_stale(self):
        c=self.ready_api(preview=True);mid=c['action']['intent']['browser_batch']['material_ids'][0]
        self.at+=timedelta(seconds=181)
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'提交前素材条件已变化：素材正在追投 '+mid,'observation':None})
        self.assertEqual(self.command()['phase'],'tasks');self.assertEqual(self.queue.status(),[])

    def test_running_material_regroup_does_not_reuse_an_incomplete_task_inventory(self):
        c=self.ready_api(preview=True);mid=c['action']['intent']['browser_batch']['material_ids'][0]
        with self.queue.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()['data'])
            d['task_total']=len(d['tasks'])+1
            db.execute('UPDATE hybrid_runs SET data=? WHERE id=?',(json.dumps(d),c['run_id']))
        self.hybrid.result({'run_id':c['run_id'],'id':c['id'],'lease':c['lease'],'ok':False,
            'message':'提交前素材条件已变化：素材正在追投 '+mid,'observation':None})
        self.assertEqual(self.command()['phase'],'tasks');self.assertEqual(self.queue.status(),[])


if __name__=='__main__':unittest.main()
