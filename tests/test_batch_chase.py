import copy
import json
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from monitor.batch_chase import BatchChaseActions,batch_candidates,normalize_surface,date_range
from monitor.cruise_rules import default_profile

NOW=datetime.fromisoformat('2026-10-26T13:00:00+08:00')
AID='1999999999999002';PID='8218616704149013'
def surface():
    return {'account_id':AID,'plan_id':PID,'tab_id':7,'document_id':'document-a','path':'/uni-prom/detail','group':'示例分组A',
        'captured_at':NOW.isoformat(),'date_start':'2026-10-22','date_end':'2026-10-26','page':1,'page_size':100,'sort':'spend_desc','editor_open':False,
        'materials':[{'id':str(8204031517311756221+i),'name':'素材'+str(i),'spend':500-i*20,'roi':4,'chase_status':'-',
            'approved':True,'selectable':True} for i in range(8)]}
def profile():
    p=default_profile();p['enabled']=True;p['features']['f5'].update(enabled=True,lookback_days=5,batch_size=3,
        rules=[{'material_spend_min':100,'material_spend_max':None,'material_roi_min':3,'chase_type':'控成本追投',
            'daily_budget':200,'bid_value':36.88,'optimization_target':'直播间成交'}]);return p

class BatchChaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=NOW;self.profile=profile();self.revision='profile-one'
        self.policy={'revision':'scope-one','accounts':[{'id':AID,'scene':'live','tab_group':'示例分组A','observe':True}]}
        self.queue=BatchChaseActions(Path(self.tmp.name)/'batch.db',lambda aid:(copy.deepcopy(self.profile),self.revision),lambda:self.now)
        self.raw=surface();self.observe()
    def tearDown(self):self.tmp.cleanup()
    def observe(self,armed=True):
        self.queue.observe({'protocol':1,'worker_id':'worker-a','epoch':'epoch-a','armed':armed,'revision':self.policy['revision'],'surfaces':[self.raw]},self.policy)
    def create(self):
        c=self.queue.status(self.policy)['previews'][0]['batches'][0]
        return self.queue.create({'worker_id':'worker-a','epoch':'epoch-a','account_id':AID,'plan_id':PID,'candidate_key':c['key']},self.policy)
    def approve(self,d):return self.queue.approve({'id':d['id'],'intent_digest':d['intent_digest'],'confirmed':True},self.policy)
    def claim(self):return self.queue.claim({'worker_id':'worker-a','epoch':'epoch-a'},self.policy)['command']
    def begun(self):
        d=self.create();self.approve(d);c=self.claim();self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':self.raw},self.policy);return c
    def test_range_includes_today_and_crosses_month_boundary(self):
        self.assertEqual(date_range(NOW,5),('2026-10-22','2026-10-26'))
        self.assertEqual(date_range(NOW.replace(day=2),3),('2026-09-30','2026-10-02'))
        for bad in [2,6,True,3.0]:
            with self.assertRaises(ValueError):date_range(NOW,bad)
    def test_batches_are_sorted_exact_ids_and_whole_batch_budget(self):
        p=self.queue.status(self.policy)['previews'][0]
        self.assertEqual(len(p['batches']),2);c=p['batches'][0]
        self.assertEqual(c['material_ids'],[str(8204031517311756221+i) for i in range(3)])
        self.assertEqual(c['after']['daily_budget'],200);self.assertEqual(c['budget_scope'],'whole_batch')
        self.assertEqual(self.queue.status(self.policy)['actions'],[])
    def test_boundary_missing_roi_active_task_and_unapproved_material_skip(self):
        s=normalize_surface(surface(),self.policy['accounts'])
        s['materials'][0]['roi']=None;s['materials'][1]['chase_status']='调控中';s['materials'][2]['approved']=False
        s['materials'][3]['spend']='100.00';s['materials'][4]['roi']='3.00'
        r=batch_candidates(s,self.profile,NOW)
        self.assertEqual(r['batches'][0]['material_ids'],[s['materials'][i]['id'] for i in [5,6,7]])

    def test_running_materials_require_opt_in_through_prepare_approve_and_begin(self):
        for row in self.raw['materials'][:3]:row['chase_status']='调控中'
        self.observe();off=self.queue.status(self.policy)['previews'][0]['batches'][0]
        ids=[r['id'] for r in self.raw['materials'][:3]]
        self.assertFalse(set(off['material_ids'])&set(ids))
        self.profile['features']['f5']['allow_running_materials']=True;self.observe()
        d=self.create();self.assertEqual(d['intent']['material_ids'],ids);self.assertTrue(d['intent']['allow_running_materials'])
        self.approve(d);c=self.claim()
        self.assertTrue(self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':self.raw},self.policy)['submit_once'])

    def test_repeat_switch_change_invalidates_review_and_fresh_submission_still_checks_status(self):
        self.profile['features']['f5']['allow_running_materials']=True
        self.raw['materials'][0]['chase_status']='调控中';self.observe();d=self.create()
        self.profile['features']['f5']['allow_running_materials']=False
        with self.assertRaises(ValueError):self.approve(d)
        self.profile['features']['f5']['allow_running_materials']=True;self.approve(d);c=self.claim()
        fresh=copy.deepcopy(self.raw);fresh['materials'][0]['chase_status']='未知状态'
        with self.assertRaises(ValueError):self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':fresh},self.policy)

    def test_opt_in_allows_a_new_confirmed_batch_after_verified_creation_but_not_pending_or_unknown(self):
        self.profile['features']['f5']['allow_running_materials']=True;c=self.begun();v=c['intent']
        obs={**{k:v['surface'][k] for k in ['account_id','plan_id','tab_id','document_id']},'readback_document_id':'document-b',
             'task_id':'8029333841537831','task_name':v['task_name'],'material_ids':v['material_ids'],'after':v['after'],'captured_at':self.now.isoformat(),'reloaded':True}
        self.assertEqual(self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':obs,'message':'verified'})['state'],'verified')
        for row in self.raw['materials'][:3]:row['chase_status']='调控中'
        self.observe();payload=self.selected_payload(v['material_ids'])
        d=self.queue.create_selected(payload,self.policy);self.assertEqual(d['state'],'draft');self.assertIsNone(self.claim())
        with self.assertRaises(ValueError):self.queue.create_selected(payload,self.policy)
        self.approve(d);c=self.claim();self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':self.raw},self.policy)
        self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':None,'message':'unknown'})
        with self.assertRaises(ValueError):self.queue.create_selected(payload,self.policy)
        self.assertIsNone(self.claim())

    def test_opt_in_does_not_allow_unknown_status_unapproved_or_bad_metrics(self):
        from monitor.batch_chase import matches
        rule=self.profile['features']['f5']['rules'][0]
        for change in ({'chase_status':''},{'chase_status':'已追投'},{'chase_status':'未知状态'},{'approved':False},{'roi':2},{'spend':100}):
            row={**self.raw['materials'][0],'chase_status':'调控中',**change}
            self.assertFalse(matches(row,rule,allow_running=True))

    def test_loading_status_cells_wait_before_creating_candidates(self):
        for row in self.raw['materials']:row.update(chase_status='',approved=False)
        self.observe();preview=self.queue.status(self.policy)['previews'][0]
        self.assertFalse(preview['reading_complete']);self.assertEqual(preview['batches'],[])
        self.assertIn('保留本页',preview['notes'][0]);self.assertEqual(self.queue.status(self.policy)['actions'],[])
        for row in self.raw['materials']:row.update(chase_status='-',approved=True)
        self.observe();self.assertEqual(len(self.queue.status(self.policy)['previews'][0]['batches']),2)
    def test_wrong_account_duplicate_long_ids_wrong_page_size_and_range_rejected(self):
        for change in [lambda s:s.update(account_id='100'),lambda s:s['materials'].append(copy.deepcopy(s['materials'][0])),
                lambda s:s.update(page_size=10),lambda s:s.update(date_start='2026-10-19')]:
            v=surface();change(v)
            with self.assertRaises(ValueError):normalize_surface(v,self.policy['accounts'])
    def test_no_implicit_approve_and_draft_reserves_ids(self):
        d=self.create();self.assertIsNone(self.claim());self.assertEqual(d['state'],'draft')
        next_batch=self.queue.status(self.policy)['previews'][0]['batches'][0]
        self.assertFalse(set(d['intent']['material_ids'])&set(next_batch['material_ids']))
        with self.assertRaises(ValueError):self.queue.approve({'id':d['id'],'intent_digest':'wrong','confirmed':True},self.policy)
    def test_range_profile_mode_off_and_metrics_change_prevent_approval(self):
        for change in [lambda:setattr(self,'revision','changed'),lambda:self.raw.update(date_start='2026-10-23'),
                lambda:self.raw['materials'][0].update(roi=2)]:
            with self.subTest(change=change):
                saved=copy.deepcopy(self.raw);revision=self.revision;d=self.create();change();self.observe()
                with self.assertRaises(ValueError):self.approve(d)
                self.queue.cancel({'id':d['id']});self.raw=saved;self.revision=revision;self.observe()
    def test_unarmed_mode_cancels_unsubmitted_draft(self):
        d=self.create();self.observe(False)
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'],'cancelled')
        with self.assertRaises(ValueError):self.approve(d)
    def test_begin_uses_independent_fresh_page_and_single_permit(self):
        d=self.create();self.approve(d);c=self.claim();wrong=copy.deepcopy(self.raw);wrong['document_id']='other'
        with self.assertRaises(ValueError):self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':wrong},self.policy)
        self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':self.raw},self.policy)
        with self.assertRaises(ValueError):self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':self.raw},self.policy)
    def test_click_receipt_unknown_blocks_future_claim_and_no_retry(self):
        c=self.begun();r=self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':None,'message':'clicked'})
        self.assertEqual(r['state'],'unknown');self.assertIsNone(self.claim());self.assertTrue(self.queue.has_active_or_unknown())
    def test_full_new_document_task_readback_can_verify(self):
        c=self.begun();v=c['intent'];obs={**{k:v['surface'][k] for k in ['account_id','plan_id','tab_id','document_id']},
            'readback_document_id':'document-b','task_id':'8029333841537831','task_name':v['task_name'],'material_ids':v['material_ids'],
            'after':v['after'],'captured_at':NOW.isoformat(),'reloaded':True}
        r=self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':obs,'message':'new task read'})
        self.assertEqual(r['state'],'verified');self.assertFalse(self.queue.has_active_or_unknown())
    def test_same_document_or_wrong_material_set_cannot_verify(self):
        c=self.begun();v=c['intent'];obs={**{k:v['surface'][k] for k in ['account_id','plan_id','tab_id','document_id']},
            'readback_document_id':'document-a','task_id':'8029333841537831','task_name':v['task_name'],'material_ids':v['material_ids'][:-1],
            'after':v['after'],'captured_at':NOW.isoformat(),'reloaded':True}
        self.assertEqual(self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':obs,'message':'mismatch'})['state'],'unknown')
    def test_expiry_preserves_unknown_and_cancels_stale_draft(self):
        self.create();self.now+=timedelta(minutes=16)
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'],'expired')

    def test_review_lasts_fifteen_minutes_and_execution_has_a_separate_short_expiry(self):
        d=self.create();self.assertEqual(datetime.fromisoformat(d['expires_at'])-self.now,timedelta(minutes=15))
        self.now+=timedelta(minutes=5);self.raw['captured_at']=self.now.isoformat();self.observe()
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'],'draft')
        result=self.approve(d)
        self.assertEqual(datetime.fromisoformat(result['expires_at'])-self.now,timedelta(minutes=2))
        self.now+=timedelta(seconds=121)
        self.assertIsNone(self.claim());self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'],'expired')

    def test_longer_review_still_requires_fresh_data_and_matching_metrics(self):
        d=self.create();self.now+=timedelta(minutes=5)
        with self.assertRaises(ValueError):self.approve(d)
        self.raw['captured_at']=self.now.isoformat();self.raw['materials'][0]['roi']=2;self.observe()
        with self.assertRaises(ValueError):self.approve(d)
        self.assertIsNone(self.claim())

    def test_expired_batch_can_be_reprepared_from_a_fresh_document_without_approval(self):
        d=self.create();self.now+=timedelta(minutes=16)
        self.raw.update(captured_at=self.now.isoformat(),document_id='fresh-document');self.observe()
        fresh=self.queue.renew({'id':d['id']},self.policy)
        self.assertEqual(fresh['state'],'draft');self.assertNotEqual(fresh['id'],d['id'])
        self.assertEqual(fresh['intent']['material_ids'],d['intent']['material_ids'])
        self.assertEqual(fresh['intent']['after'],d['intent']['after'])
        self.assertEqual(fresh['intent']['surface']['document_id'],'fresh-document')
        self.assertEqual(datetime.fromisoformat(fresh['expires_at'])-self.now,timedelta(minutes=15))
        self.assertIsNone(self.claim())
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)

    def test_renew_never_substitutes_materials_or_accepts_changed_rules(self):
        d=self.create();self.now+=timedelta(minutes=16);self.raw['captured_at']=self.now.isoformat()
        self.raw['materials'][0]['roi']=2;self.observe()
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)
        self.raw['materials'][0]['roi']=4;self.revision='changed-rules';self.observe()
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)
        self.assertTrue(all(a['state']=='expired' for a in self.queue.status(self.policy)['actions']))

    def test_renew_keeps_reviewed_ids_when_another_eligible_row_moves_ahead(self):
        d=self.create();self.now+=timedelta(minutes=16)
        self.raw.update(captured_at=self.now.isoformat(),document_id='fresh-document')
        self.raw['materials'][3]['spend']=1000;self.raw['materials'][1]['spend']=900
        self.observe();new_top=self.queue.status(self.policy)['previews'][0]['batches'][0]['material_ids']
        self.assertNotEqual(set(new_top),set(d['intent']['material_ids']))
        fresh=self.queue.renew({'id':d['id']},self.policy)
        self.assertEqual(set(fresh['intent']['material_ids']),set(d['intent']['material_ids']))
        self.assertEqual(fresh['intent']['after'],d['intent']['after'])
        self.assertEqual(fresh['state'],'draft');self.assertIsNone(self.claim())

    def test_renew_requires_full_status_loading_and_every_original_id(self):
        d=self.create();self.now+=timedelta(minutes=16);self.raw['captured_at']=self.now.isoformat()
        self.raw['materials'][-1]['chase_status']='';self.observe()
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)
        self.raw['materials'][-1]['chase_status']='-';self.raw['materials'].pop(0);self.observe()
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)

    def test_renew_cannot_bypass_the_daily_action_cap_with_fixed_ids(self):
        self.profile['safety']['max_actions_per_day']=1
        d=self.create();original=copy.deepcopy(self.raw);self.now+=timedelta(minutes=16)
        for row in self.raw['materials']:row['id']=str(int(row['id'])+100)
        self.raw['captured_at']=self.now.isoformat();self.observe();c=self.begun();v=c['intent']
        obs={**{k:v['surface'][k] for k in ['account_id','plan_id','tab_id','document_id']},
             'readback_document_id':'document-b','task_id':'8029333841537831','task_name':v['task_name'],
             'material_ids':v['material_ids'],'after':v['after'],'captured_at':self.now.isoformat(),'reloaded':True}
        self.assertEqual(self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':obs,'message':'verified'})['state'],'verified')
        self.raw=original;self.raw['captured_at']=self.now.isoformat();self.observe()
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)

    def selected_payload(self,ids=None):
        return {'worker_id':'worker-a','epoch':'epoch-a','account_id':AID,'plan_id':PID,
                'material_ids':ids or [r['id'] for r in self.raw['materials'][:3]],'rule_index':0,'profile_revision':self.revision}

    def test_selected_ids_prepare_only_a_new_draft_under_the_current_profile(self):
        original=[r['id'] for r in self.raw['materials'][:3]]
        self.raw['materials'][3]['spend']=1000;self.revision='current-profile';self.observe()
        d=self.queue.create_selected(self.selected_payload(original),self.policy)
        self.assertEqual(set(d['intent']['material_ids']),set(original));self.assertEqual(d['state'],'draft')
        self.assertEqual(d['intent']['profile_revision'],'current-profile');self.assertIsNone(self.claim())
        self.assertEqual(d['intent']['after']['daily_budget'],200)

    def test_selected_ids_cannot_bypass_eligibility_identity_or_profile_checks(self):
        original=copy.deepcopy(self.raw)
        for change in (lambda:self.raw['materials'][0].update(chase_status='调控中'),lambda:self.raw['materials'][0].update(roi=2),
                       lambda:self.raw['materials'][0].update(approved=False),lambda:self.raw['materials'].pop(0)):
            self.raw=copy.deepcopy(original);payload=self.selected_payload();change();self.observe()
            with self.assertRaises(ValueError):self.queue.create_selected(payload,self.policy)
        self.raw=original;self.observe()
        for change in (lambda p:p.update(profile_revision='old-profile'),lambda p:p.update(plan_id='8342112891832239'),
                       lambda p:p.update(material_ids=[p['material_ids'][0]]*3),lambda p:p.update(rule_index=True)):
            payload=self.selected_payload();change(payload)
            with self.assertRaises(ValueError):self.queue.create_selected(payload,self.policy)
        self.assertEqual(self.queue.status(self.policy)['actions'],[])

    def test_selected_ids_reservations_and_daily_cap_are_shared_with_generated_batches(self):
        payload=self.selected_payload();d=self.queue.create_selected(payload,self.policy)
        with self.assertRaises(ValueError):self.queue.create_selected(payload,self.policy)
        self.profile['safety']['max_actions_per_day']=1
        with self.assertRaises(ValueError):self.queue.create_selected(self.selected_payload([r['id'] for r in self.raw['materials'][3:6]]),self.policy)
        self.assertEqual(len(self.queue.status(self.policy)['actions']),1)

    def test_active_and_unknown_batches_cannot_be_renewed(self):
        d=self.create()
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)
        self.approve(d);c=self.claim();self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':self.raw},self.policy)
        self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':None,'message':'uncertain'})
        with self.assertRaises(ValueError):self.queue.renew({'id':d['id']},self.policy)

    def test_claimed_execution_cannot_begin_after_its_separate_deadline(self):
        d=self.create();self.approve(d);c=self.claim();self.now+=timedelta(seconds=121)
        self.raw['captured_at']=self.now.isoformat()
        with self.assertRaises(ValueError):self.queue.begin({'id':c['id'],'lease':c['lease'],'observation':self.raw},self.policy)

    def test_unknown_can_be_rechecked_without_any_creation_or_second_claim(self):
        c=self.begun();v=c['intent']
        self.queue.receipt({'id':c['id'],'lease':c['lease'],'submitted':True,'observation':None,'message':'uncertain'})
        self.queue.request_readback({'id':c['id']},self.policy)
        self.assertEqual(self.queue.readback_jobs({'worker_id':'worker-a','epoch':'epoch-a'},self.policy)['commands'][0]['id'],c['id'])
        obs={**{k:v['surface'][k] for k in ['account_id','plan_id','tab_id','document_id']},'task_id':'8325113491300243',
             'task_name':v['task_name'],'material_ids':v['material_ids'],'after':v['after'],'captured_at':NOW.isoformat(),
             'readback_document_id':'new-task-document','reloaded':True}
        result=self.queue.readback_result({'id':c['id'],'worker_id':'worker-a','epoch':'epoch-a','observation':obs},self.policy)
        self.assertEqual(result,{'ok':True,'state':'verified','ad_changes':0});self.assertIsNone(self.claim())
        self.assertEqual(self.queue.readback_jobs({'worker_id':'worker-a','epoch':'epoch-a'},self.policy)['commands'],[])

if __name__=='__main__':unittest.main()
