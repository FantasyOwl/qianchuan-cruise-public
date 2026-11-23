import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from monitor.creative_rotation import preview_creative_rotations
from monitor.creative_retirement import review_retirement
from monitor.creative_workflow import WorkflowLedger,run_synthetic_step
from monitor.creative_workflow_demo import SyntheticMaterialProvider,creative_workflow_replay
from monitor.evaluation import evaluate_all
from monitor.submission import AttemptLedger
from test_engine import settings


def preview(adapter,at=None):
    at=at or adapter.at;rows,cfg,payload=adapter.frame(at)
    return preview_creative_rotations(payload,rows,cfg,evaluate_all(rows,cfg,at),at)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'workflow.db';self.adapter=SyntheticMaterialProvider(settings())
        self.at=self.adapter.at;self.ledger=WorkflowLedger(self.path);self.p=preview(self.adapter)
        self.d=self.p['drafts'][0];self.assertTrue(self.d['local_eligible'],self.d['blockers'])

    def create(self,p=None):
        p=p or self.p;d=p['drafts'][0]
        return self.ledger.create(p,d['account_id'],d['plan_id'],d['intent_digest'],self.at)

    def step(self,w,seconds):
        return run_synthetic_step(self.ledger,w['id'],self.adapter,self.at+timedelta(seconds=seconds))

    def ready_retire(self):
        w=self.create();w=self.step(w,0);w=self.step(w,2);return self.step(w,3602)

    def retirement(self,w,mutate=None):
        now=self.at+timedelta(seconds=3603);rows,cfg,payload=self.adapter.frame(now)
        if mutate:mutate(rows,cfg,payload)
        return review_retirement(w['draft'],payload,rows,cfg,evaluate_all(rows,cfg,now),now)

    def test_full_replay_reopens_durable_stages_without_real_actions_or_live_urls(self):
        out=creative_workflow_replay(settings())
        self.assertEqual(out['workflow']['state'],'completed_synthetic',out)
        self.assertEqual(out['synthetic_calls'],['add','retire']);self.assertFalse(out['holds_remain'])
        self.assertEqual([e['seq'] for e in out['journal']],list(range(1,10)))
        self.assertEqual(out['workflow']['simulation_action_count'],2)
        self.assertTrue(out['reopened_before_every_step']);self.assertFalse(out['persisted'])
        self.assertFalse(out['executed']);self.assertEqual(out['real_ad_actions'],0)
        self.assertFalse(out['workflow']['platform_verified'])

    def test_intent_is_stable_across_preview_clock_but_not_configuration_or_inventory_changes(self):
        rows,cfg,payload=self.adapter.frame(self.at)
        again=preview_creative_rotations(payload,rows,cfg,evaluate_all(rows,cfg,self.at+timedelta(seconds=1)),self.at+timedelta(seconds=1))
        self.assertEqual(self.d['intent_digest'],again['drafts'][0]['intent_digest'])
        payload['plans'][0]['capacity']['maximum']=4
        changed=preview_creative_rotations(payload,rows,cfg,evaluate_all(rows,cfg,self.at),self.at)
        self.assertNotEqual(self.d['intent_digest'],changed['drafts'][0]['intent_digest'])

    def test_same_intent_creation_is_idempotent_and_journaled_once(self):
        a=self.create();b=self.create();self.assertEqual(a,b)
        self.assertEqual(len(self.ledger.events(a['id'])),1)
        self.assertEqual(WorkflowLedger.existing(self.path),[a])

    def test_reading_absent_workflow_database_does_not_create_a_file(self):
        path=Path(self.temp.name)/'never.db';self.assertEqual(WorkflowLedger.existing(path),[])
        self.assertFalse(path.exists())

    def test_create_requires_current_qualified_intent_and_fresh_full_preview(self):
        for p,intent,at in [(self.p,'0'*64,self.at),(self.p,self.d['intent_digest'],self.at+timedelta(seconds=6))]:
            with self.assertRaises(ValueError):self.ledger.create(p,self.d['account_id'],self.d['plan_id'],intent,at)
        bad=copy.deepcopy(self.p);bad['drafts'][0]['local_eligible']=False
        with self.assertRaises(ValueError):self.create(bad)
        self.assertEqual(WorkflowLedger.existing(self.path),[])

    def test_distinct_draft_cannot_overlap_same_parent_after_reopening(self):
        self.create();other=copy.deepcopy(self.p);other['drafts'][0]['intent_digest']='a'*64
        with self.assertRaisesRegex(ValueError,'母计划'):WorkflowLedger(self.path).create(other,self.d['account_id'],self.d['plan_id'],'a'*64,self.at)

    def test_persistent_group_reservations_span_plans_and_unknown_is_not_zero(self):
        for n in range(3):
            p=copy.deepcopy(self.p);d=p['drafts'][0];d['plan_id']=str(2001+n);d['intent_digest']=str(n)*64
            self.ledger.create(p,d['account_id'],d['plan_id'],d['intent_digest'],self.at)
        p=copy.deepcopy(self.p);d=p['drafts'][0];d['plan_id']='2004';d['intent_digest']='4'*64
        with self.assertRaisesRegex(ValueError,'超过3'):self.create(p)
        self.assertEqual(len(WorkflowLedger.existing(self.path)),3)

    def test_plan_and_child_submission_attempts_respect_workflow_parent_reservation(self):
        self.create();attempts=AttemptLedger(self.path)
        for context in ({'account_id':self.d['account_id'],'entity_id':'2001','scope':'plan'},
                {'account_id':'000'+self.d['account_id'],'entity_id':'02001','scope':'plan'},
                {'account_id':self.d['account_id'],'entity_id':'777','scope':'control','parent_plan_id':'2001'}):
            with self.assertRaisesRegex(ValueError,'母计划'):attempts.claim('a','d',self.at,context)
        attempts.claim('other','other',self.at,{'account_id':self.d['account_id'],'entity_id':'2002','scope':'plan'})

    def test_unknown_existing_task_submission_prevents_rotation_creation(self):
        a=AttemptLedger(self.path);a.claim('task','task-digest',self.at,{'account_id':self.d['account_id'],'entity_id':'777','scope':'control','parent_plan_id':'2001'})
        a.finish('task','unknown',{})
        with self.assertRaisesRegex(ValueError,'未知'):self.create()

    def test_known_recent_submission_and_corrupt_prior_context_block_create(self):
        a=AttemptLedger(self.path);a.claim('x','x',self.at,{'account_id':self.d['account_id'],'entity_id':'2001','scope':'plan'})
        a.finish('x','confirmed',{})
        with self.assertRaisesRegex(ValueError,'冷却'):self.create()
        with a.connect() as db:db.execute("UPDATE submission_attempts SET context_json='{}'")
        with self.assertRaisesRegex(ValueError,'证据'):self.create()

    def test_cancel_before_add_releases_both_reservations_and_is_recorded(self):
        w=self.create();c=self.ledger.cancel_before_add(w['id'],self.at)
        self.assertFalse(c['parent_reservation_held']);self.assertFalse(c['group_reservation_held'])
        self.assertEqual(self.ledger.events(w['id'])[-1]['event'],'cancelled_before_any_attempt')
        AttemptLedger(self.path).claim('after','after',self.at,{'account_id':self.d['account_id'],'entity_id':'2001','scope':'plan'})

    def test_non_synthetic_draft_can_be_saved_but_runner_refuses_every_real_action(self):
        rows,cfg,payload=self.adapter.frame(self.at)
        for r in rows:r['source']['kind']='import'
        p=preview_creative_rotations(payload,rows,cfg,evaluate_all(rows,cfg,self.at),self.at)
        w=self.create(p);self.assertEqual(w['state'],'awaiting_native_integration')
        with self.assertRaises(PermissionError):self.step(w,0)
        self.assertEqual(self.adapter.calls,[])

    def test_add_claim_survives_process_loss_and_never_repeats_io(self):
        w=self.create();claimed=self.ledger.change(w,'add_claimed','add_claimed_before_io',self.at,{'add_attempt_at':self.at.isoformat()})
        self.ledger=WorkflowLedger(self.path)
        for seconds in (0,1,36000):self.assertEqual(self.step(claimed,seconds)['state'],'add_claimed')
        self.assertEqual(self.adapter.calls,[])
        with self.assertRaises(ValueError):self.ledger.cancel_before_add(w['id'],self.at)

    def test_timeout_after_synthetic_remote_change_holds_forever_without_retry_or_unlock(self):
        w=self.create();original=self.adapter.mutate
        def timeout(*args):original(*args);raise TimeoutError('PRIVATE_PROVIDER_TEXT')
        self.adapter.mutate=timeout;w=self.step(w,0);self.assertEqual(w['state'],'add_unknown')
        self.assertTrue(self.adapter.added);self.ledger=WorkflowLedger(self.path)
        self.step(w,100000);self.assertEqual(self.adapter.calls,['add'])
        self.assertNotIn('PRIVATE_PROVIDER_TEXT',json.dumps(self.ledger.events(w['id'])))
        with self.assertRaises(ValueError):self.ledger.change(w,'completed_synthetic','fake',self.at,release=True)
        with self.assertRaises(ValueError):self.ledger.cancel_before_add(w['id'],self.at)

    def test_only_one_concurrent_driver_can_claim_add_before_io(self):
        w=self.create();gate=threading.Barrier(2);orig=self.adapter.frame;results=[]
        def frame(now):gate.wait(timeout=10);return orig(now)
        self.adapter.frame=frame
        def drive():
            try:results.append(self.step(w,0)['state'])
            except ValueError:results.append('conflict')
        threads=[threading.Thread(target=drive) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=15);self.assertFalse(t.is_alive())
        self.assertEqual(self.adapter.calls,['add']);self.assertCountEqual(results,['verify_add','conflict'])

    def test_acknowledgement_does_not_start_cooldown_without_fresh_bound_readback(self):
        w=self.step(self.create(),0);self.assertEqual(w['state'],'verify_add')
        original=self.adapter.readback
        for key,value in [('complete_inventory',False),('matched',False),('usable',False),('plan_id','999'),('workflow_id','other'),('product_id','other'),('aweme_uid','999'),('present',False),('replacement_native',{'video_id':'other'}),('observed_at',(self.at-timedelta(seconds=1)).isoformat()),('synthetic_only',False)]:
            def bad(w,stage,now,key=key,value=value):r=original(w,stage,now);r[key]=value;return r
            self.adapter.readback=bad
            self.assertEqual(self.step(w,2)['state'],'verify_add')
        self.adapter.readback=original;self.assertEqual(self.step(w,2)['state'],'cooldown')

    def test_one_hour_cooldown_begins_at_verified_add_and_retire_needs_a_new_step(self):
        w=self.step(self.create(),0);w=self.step(w,2)
        self.assertEqual(self.step(w,3601)['state'],'cooldown')
        w=self.step(w,3602);self.assertEqual(w['state'],'ready_retire');self.assertEqual(self.adapter.calls,['add'])
        self.assertEqual(self.step(w,3603)['state'],'verify_retire');self.assertEqual(self.adapter.calls,['add','retire'])

    def test_stale_add_or_changed_candidate_is_rejected_before_claim(self):
        w=self.create();original=self.adapter.frame
        def changed(now):
            rows,cfg,p=original(now);p['plans'][0]['pool'][0]['native']['video_id']='different';return rows,cfg,p
        self.adapter.frame=changed;w=self.step(w,0)
        self.assertEqual(w['state'],'ready_add');self.assertTrue(w['last_blockers']);self.assertEqual(self.adapter.calls,[])

    def test_fresh_retirement_keeps_add_first_capacity_and_requires_new_usable_material(self):
        w=self.ready_retire();out=self.retirement(w);self.assertTrue(out['local_eligible'],out['blockers'])
        def remove_status(rows,cfg,p):p['plans'][0]['slots'][1].pop('status_verified')
        out=self.retirement(w,remove_status);self.assertFalse(out['local_eligible']);self.assertIn('可用','；'.join(out['blockers']))

    def test_retirement_rejects_incomplete_inventory_source_mismatch_and_shared_old_uses(self):
        w=self.ready_retire()
        def incomplete(rows,cfg,p):p['plans'][0]['inventory_complete']=False
        def mixed(rows,cfg,p):rows[-1]['source']['kind']='import'
        def shared(rows,cfg,p):
            old=copy.deepcopy(p['plans'][0]['slots'][0]);old['snapshot_id']=None;old['product_id']='3002'
            p['plans'][0]['slots'].append(old);p['plans'][0]['capacity']['active_count']=3
        for mutation in (incomplete,mixed,shared):
            out=self.retirement(w,mutation);self.assertFalse(out['local_eligible'],out)

    def test_retirement_rechecks_current_weakness_creation_and_programmatic_history(self):
        w=self.ready_retire()
        def healthy(rows,cfg,p):
            for i,r in enumerate(rows):r['metrics'].update(impressions=i*1000,clicks=i*100)
        def created(rows,cfg,p):
            for r in rows:r['context']['created_at']=(self.at-timedelta(days=8)).isoformat()
        def history(rows,cfg,p):rows[0]['context']['creative_type']='custom'
        for mutation in (healthy,created,history):self.assertFalse(self.retirement(w,mutation)['local_eligible'])

    def test_retirement_requires_current_same_use_replacement_and_non_smart_old_asset(self):
        w=self.ready_retire()
        def wrong_product(rows,cfg,p):p['plans'][0]['slots'][1]['product_id']='3002'
        def smart(rows,cfg,p):p['plans'][0]['slots'][0]['delete_class']='SMART'
        def stale(rows,cfg,p):p['plans'][0]['slots'][1]['status_at']=(self.at-timedelta(days=1)).isoformat()
        for mutation in (wrong_product,smart,stale):self.assertFalse(self.retirement(w,mutation)['local_eligible'])

    def test_unknown_retire_keeps_added_material_and_reservations_without_retry(self):
        w=self.ready_retire();original=self.adapter.mutate
        def timeout(*args):original(*args);raise TimeoutError()
        self.adapter.mutate=timeout;w=self.step(w,3603);self.assertEqual(w['state'],'retire_unknown')
        self.assertTrue(w['parent_reservation_held']);self.step(w,10000);self.assertEqual(self.adapter.calls,['add','retire'])

    def test_timer_alone_cannot_complete_observation_or_release_parent(self):
        w=self.ready_retire();w=self.step(w,3603);w=self.step(w,3604)
        original=self.adapter.observations
        self.adapter.observations=lambda w,now:[]
        w=self.step(w,10804);self.assertEqual(w['state'],'observe');self.assertTrue(w['parent_reservation_held'])
        self.adapter.observations=lambda w,now:[original(w,now)[0],original(w,now)[-1]]
        self.assertEqual(self.step(w,10804)['state'],'observe')
        self.adapter.observations=original;self.assertEqual(self.step(w,10804)['state'],'completed_synthetic')

    def test_gaps_wrong_identity_or_unavailable_observation_samples_do_not_complete(self):
        w=self.ready_retire();w=self.step(w,3603);w=self.step(w,3604);original=self.adapter.observations
        for field,value in [('plan_id','other'),('workflow_id','other'),('product_id','other'),('aweme_uid','999'),('available',False),('native',{'video_id':'other'}),('synthetic_only',False)]:
            def bad(w,now,field=field,value=value):points=original(w,now);points[5][field]=value;return points
            self.adapter.observations=bad;self.assertEqual(self.step(w,10804)['state'],'observe')

    def test_process_exit_during_provider_call_retains_claim_without_repeating_action(self):
        w=self.create()
        def crash(*args):raise SystemExit(75)
        self.adapter.mutate=crash
        with self.assertRaises(SystemExit):self.step(w,0)
        self.ledger=WorkflowLedger(self.path)
        self.assertEqual(self.step(w,3600)['state'],'add_claimed')
        self.assertEqual(self.ledger.events(w['id'])[-1]['event'],'add_claimed_before_io')

    def test_retirement_readback_must_explicitly_confirm_absence_in_the_same_use(self):
        w=self.ready_retire();w=self.step(w,3603);original=self.adapter.readback
        for field,value in [('present',True),('present',None),('product_id','other'),('aweme_uid','other')]:
            def bad(w,stage,now,field=field,value=value):r=original(w,stage,now);r[field]=value;return r
            self.adapter.readback=bad;self.assertEqual(self.step(w,3604)['state'],'verify_retire')
        self.adapter.readback=original;self.assertEqual(self.step(w,3604)['state'],'observe')

    def test_stale_version_rollback_and_unknown_state_cannot_rewrite_durable_history(self):
        w=self.create();v=self.step(w,0)
        with self.assertRaises(ValueError):self.ledger.cancel_before_add(w['id'],self.at)
        with self.assertRaises(ValueError):self.ledger.change(w,'add_claimed','duplicate',self.at)
        with self.assertRaises(ValueError):self.step(v,-1)
        with self.assertRaises(ValueError):self.ledger.change(v,'completed_synthetic','skip',self.at,release=True)
        self.assertEqual(len(self.ledger.events(w['id'])),3)


if __name__=='__main__':unittest.main()
