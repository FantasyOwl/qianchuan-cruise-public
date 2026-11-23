import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock,patch

from monitor.actuation import prepare_action
from monitor.control_creation import CREATE_PATH,PURCHASE,prepare_boost_body,interpret_creation_response
from monitor.control_creation_client import ControlCreationClient,ControlCreationPermit
from monitor.demo import control_creation_scenario
from monitor.evaluation import evaluate_all
from monitor.schema import normalize_context
from monitor.submission import AttemptLedger
from monitor.creative_workflow import WorkflowLedger
from test_engine import settings


def fixture():
    rows,cfg,at=control_creation_scenario(settings())
    for r in rows:r['source']['kind']='import'
    evaluation=evaluate_all(rows,cfg,at)
    proposal=next(p for p in evaluation['recommendations'] if p['action']=='one_click_boost')
    parent=rows[-2];preview=prepare_action(proposal,parent,cfg,at,snapshots=rows,evaluation=evaluation)
    return rows,cfg,at,proposal,preview


class CreationCompilerTests(unittest.TestCase):
    def test_complete_strategy_and_current_single_parent_produce_exact_native_request(self):
        rows,cfg,at,p,out=fixture();self.assertTrue(out['supported_for_preview'],out['blockers'])
        body=json.loads(out['request']['body_json']);self.assertEqual(body['ad_id'],8012367229158357)
        self.assertEqual(body['scene'],'SMART_BOOST');self.assertEqual(body['external_action'],PURCHASE)
        self.assertEqual(body['budget'],100);self.assertEqual(body['duration'],1)
        self.assertEqual(set(body),{'advertiser_id','ad_id','scene','external_action','budget','duration'})
        self.assertTrue(out['request']['url'].endswith(CREATE_PATH));self.assertFalse(out['live_execution_allowed'])

    def test_returned_new_task_id_is_business_acknowledgement_not_platform_state(self):
        out=interpret_creation_response({'code':0,'data':{'id':8753264005940210}})
        self.assertTrue(out['business_accepted']);self.assertEqual(out['created_task_id'],'8753264005940210')
        self.assertFalse(out['platform_state_observed']);self.assertFalse(out['automatic_retry'])

    def test_missing_rounded_boolean_alias_and_conflicting_failure_ids_stay_unknown(self):
        for value in (None,True,1.0,'00900','0',{},-1):
            out=interpret_creation_response({'code':0,'data':{'id':value}});self.assertFalse(out['business_accepted']);self.assertFalse(out['known_rejected'])
        self.assertFalse(interpret_creation_response({'code':False,'data':{'id':123}})['business_accepted'])
        self.assertFalse(interpret_creation_response({'code':1,'data':{'id':123}})['known_rejected'])
        self.assertTrue(interpret_creation_response({'code':1,'data':{}})['known_rejected'])

    def test_name_bid_roi_and_chase_only_fields_are_never_silently_added(self):
        rows,cfg,at,p,_=fixture();p=copy.deepcopy(p);p['parameters']['name']='not-supported'
        evaluation=evaluate_all(rows,cfg,at);evaluation['recommendations']=[p]
        out=prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=evaluation)
        self.assertFalse(out['supported_for_preview'])

    def test_creation_requires_current_full_evaluation_not_a_supplied_eligible_flag(self):
        rows,cfg,at,p,_=fixture()
        self.assertFalse(prepare_action(p,rows[-2],cfg,at)['supported_for_preview'])
        e=evaluate_all(rows,cfg,at);e['recommendations']=[]
        self.assertFalse(prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'])

    def test_budget_and_duration_must_satisfy_both_user_limits_and_native_grid(self):
        for key,value in [('budget',True),('budget',99),('budget',301),('duration_hours',.5),('duration_hours',1.1),('duration_hours',25)]:
            rows,cfg,at,p,_=fixture();p=copy.deepcopy(p);p['parameters'][key]=value
            e=evaluate_all(rows,cfg,at);e['recommendations']=[p]
            self.assertFalse(prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'])

    def test_parent_mode_multi_scope_config_timestamp_and_delivery_state_gate_request(self):
        for key,value in [('plan_multi_aweme',True),('plan_multi_aweme',None),('plan_delivery_mode','volume'),
            ('plan_roi_config_verified',False),('plan_optimization_goal','unknown'),('plan_roi_observed_at','2020-01-01T00:00:00+08:00')]:
            rows,cfg,at,_,_=fixture();rows[-2]['context'][key]=value;e=evaluate_all(rows,cfg,at)
            p=next(p for p in e['recommendations'] if p['action']=='one_click_boost')
            self.assertFalse(prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'],key)

    def test_other_product_families_and_scene_cannot_borrow_the_live_universal_contract(self):
        for field,value in [('promotion_type','chengfang'),('scene','product')]:
            rows,cfg,at,p,_=fixture();snapshot=copy.deepcopy(rows[-2]);snapshot[field]=value;e=evaluate_all(rows,cfg,at)
            out=prepare_action(p,snapshot,cfg,at,snapshots=rows,evaluation=e)
            self.assertFalse(out['supported_for_preview'])

    def test_user_target_null_or_competing_growth_proposal_never_grants_creation(self):
        rows,cfg,at,p,_=fixture();cfg['accounts'][0]['target_roi']=None;e=evaluate_all(rows,cfg,at)
        self.assertFalse(prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'])
        rows,cfg,at,_,_=fixture()
        for n,r in enumerate(rows[:-1]):
            for field,step in [('spend',100),('gmv',250),('net_gmv',250),('comprehensive_cost',100),('orders',2),('clicks',40),('impressions',1000)]:r['metrics'][field]=n*step
            r['context']['promotion_peak']=True
            r['control'].update(budget_original=10000,budget_increase_today=0)
        e=evaluate_all(rows,cfg,at);p=next(p for p in e['recommendations'] if p['action']=='one_click_boost')
        self.assertFalse(p['eligible']);self.assertFalse(prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'])

    def test_demo_plan_or_mixed_demo_account_never_prepares_a_native_submission(self):
        rows,cfg,at=control_creation_scenario(settings());e=evaluate_all(rows,cfg,at)
        p=next(p for p in e['recommendations'] if p['action']=='one_click_boost')
        self.assertFalse(prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'])
        body=prepare_boost_body(p,rows[-2],cfg,at,rows,e,synthetic=True);self.assertIn('8012367229158357',body)
        rows,cfg,at,_,_=fixture();rows[-1]['source']['kind']='demo';e=evaluate_all(rows,cfg,at)
        p=next(p for p in e['recommendations'] if p['action']=='one_click_boost')
        self.assertFalse(prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'])

    def test_live_cost_control_chase_explicitly_blocks_long_lived_native_duration(self):
        rows,cfg,at,p,_=fixture();p=copy.deepcopy(p);p['action']='create_chase'
        out=prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=evaluate_all(rows,cfg,at))
        self.assertFalse(out['supported_for_preview']);self.assertIsNone(out['request'])
        self.assertIn('长期有效','；'.join(out['blockers']));self.assertTrue(out['official_references'])

    def test_native_multi_aweme_field_is_typed_and_retained_for_import_only_as_declaration(self):
        self.assertIs(normalize_context({'plan_multi_aweme':False},'import')['plan_multi_aweme'],False)
        with self.assertRaises(ValueError):normalize_context({'plan_multi_aweme':'false'},'import')


class CreationClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'attempts.db';self.ledger=AttemptLedger(self.path)
        self.rows,self.cfg,self.at,self.p,self.preview=fixture()
        self.assertTrue(self.preview['supported_for_preview'],self.preview['blockers'])
        self.permit=ControlCreationPermit('9990001',self.preview['digest'],self.at,self.at+timedelta(minutes=2),'future-test-only-reference')
        self.transport=Mock(return_value={'code':0,'data':{'id':8753264005940210}});self.token=Mock(return_value='PRIVATE_FAKE')

    def client(self,**kw):
        return ControlCreationClient(self.ledger,enable_network=True,transport=kw.pop('transport',self.transport),clock=kw.pop('clock',lambda:self.at),**kw)

    def dispatch(self,client=None,permit=None,token=None,attempt='one',**kw):
        return (client or self.client()).dispatch(attempt,self.p['id'],self.rows,self.cfg,permit or self.permit,token or self.token,**kw)

    def test_default_off_rejects_before_clock_token_ledger_or_transport(self):
        client=ControlCreationClient(None,transport=self.transport,clock=lambda:(_ for _ in ()).throw(AssertionError()))
        with self.assertRaises(PermissionError):self.dispatch(client)
        self.token.assert_not_called();self.transport.assert_not_called()

    def test_mock_ack_records_native_task_id_but_retains_unknown_parent_hold_without_secret(self):
        out=self.dispatch();self.assertEqual(out['state'],'accepted_pending_readback');self.assertEqual(out['created_task_id'],'8753264005940210')
        self.assertTrue(out['parent_hold_active']);self.assertFalse(out['platform_state_observed']);self.assertFalse(out['automatic_retry'])
        with self.ledger.connect() as db:
            row=db.execute('SELECT state,context_json,result_json FROM submission_attempts').fetchone()
        self.assertEqual(row[0],'unknown');ctx=json.loads(row[1]);self.assertEqual(ctx['scope'],'control_create')
        self.assertIsNone(ctx['entity_id']);self.assertEqual(ctx['parent_plan_id'],'8012367229158357')
        self.assertNotIn('PRIVATE_FAKE',str(row));self.assertNotIn('future-test-only-reference',str(row))
        body=json.loads(self.transport.call_args.args[0]);self.assertEqual(body['ad_id'],8012367229158357)

    def test_accepted_or_unknown_creation_blocks_further_parent_and_child_actions(self):
        self.dispatch()
        for ctx in ({'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'},
            {'scope':'control','account_id':'9990001','entity_id':'77','parent_plan_id':'8012367229158357'}):
            with self.assertRaises(ValueError):self.ledger.claim('other','other',self.at+timedelta(hours=4),ctx)
        with self.assertRaises(ValueError):self.dispatch(attempt='again')
        self.assertEqual(self.transport.call_count,1)

    def test_default_permit_expired_wrong_account_digest_or_config_flags_do_not_read_token(self):
        bad=[{'enabled':True},ControlCreationPermit('other',self.preview['digest'],self.at,self.at+timedelta(minutes=2),'r'),
            ControlCreationPermit('9990001','bad',self.at,self.at+timedelta(minutes=2),'r'),
            ControlCreationPermit('9990001',self.preview['digest'],self.at,self.at+timedelta(minutes=6),'r')]
        for permit in bad:
            with self.assertRaises(PermissionError):self.dispatch(permit=permit)
        self.token.assert_not_called();self.transport.assert_not_called()

    def test_state_changed_by_token_provider_is_rechecked_before_claim_and_io(self):
        def token():self.rows[-2]['context']['plan_multi_aweme']=True;return 'PRIVATE_FAKE'
        with self.assertRaises(ValueError):self.dispatch(token=token)
        self.transport.assert_not_called()
        with self.ledger.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM submission_attempts').fetchone()[0],0)

    def test_expiry_after_claim_finishes_without_sending_or_holding_parent(self):
        times=iter([self.at,self.at,self.at+timedelta(minutes=3)])
        out=self.dispatch(self.client(clock=lambda:next(times)))
        self.assertEqual(out['state'],'failed_before_io');self.assertFalse(out['submitted']);self.transport.assert_not_called()
        with self.ledger.connect() as db:self.assertEqual(db.execute('SELECT state FROM submission_attempts').fetchone()[0],'failed')

    def test_missing_id_or_timeout_is_unknown_and_not_automatically_retried(self):
        self.transport.return_value={'code':0,'data':{}};out=self.dispatch()
        self.assertEqual(out['state'],'unknown');self.assertTrue(out['parent_hold_active'])
        with self.assertRaises(ValueError):self.dispatch(attempt='retry')
        self.assertEqual(self.transport.call_count,1)

    def test_transport_exception_text_is_not_saved_and_current_claim_survives(self):
        self.transport.side_effect=TimeoutError('PRIVATE_FAKE')
        out=self.dispatch();self.assertEqual(out['state'],'unknown')
        with self.ledger.connect() as db:rows=str(db.execute('SELECT * FROM submission_attempts').fetchall())
        self.assertNotIn('PRIVATE_FAKE',rows);self.assertEqual(self.transport.call_count,1)

    def test_explicit_rejection_is_recorded_without_inventing_a_new_task(self):
        self.transport.return_value={'code':400,'message':'PRIVATE_FAKE','data':{}}
        out=self.dispatch();self.assertEqual(out['state'],'failed');self.assertIsNone(out['created_task_id'])
        self.assertFalse(out['parent_hold_active']);self.assertFalse(out['automatic_retry'])

    def test_process_exit_during_post_keeps_claimed_attempt_after_reopening(self):
        self.transport.side_effect=SystemExit(75)
        with self.assertRaises(SystemExit):self.dispatch()
        self.ledger=AttemptLedger(self.path)
        with self.assertRaises(ValueError):self.dispatch(attempt='again')
        self.assertEqual(self.transport.call_count,1)

    def test_existing_material_parent_reservation_blocks_new_creation(self):
        workflow=WorkflowLedger(self.path)
        with workflow.connect() as db:db.execute('INSERT INTO action_reservations VALUES(?,?,?,?,NULL)',('material-owner','9990001','8012367229158357',self.at.isoformat()))
        with self.assertRaises(ValueError):self.dispatch()
        self.transport.assert_not_called()

    def test_parent_and_child_scope_history_block_creation_in_reverse_direction(self):
        self.ledger.claim('parent','d',self.at,{'scope':'control','account_id':'9990001','entity_id':'77','parent_plan_id':'8012367229158357'})
        with self.assertRaises(ValueError):self.dispatch()
        self.transport.assert_not_called()

    def test_snapshot_generator_survives_all_three_full_evaluations(self):
        out=self.client().dispatch('one',self.p['id'],(r for r in self.rows),self.cfg,self.permit,self.token,
            events=(e for e in []),migrations=(m for m in []))
        self.assertEqual(out['state'],'accepted_pending_readback')

    def test_configuration_revoked_after_sqlite_claim_is_read_from_a_fresh_frame(self):
        claim=self.ledger.claim
        def mutate(*args,**kwargs):
            claim(*args,**kwargs)
            self.rows[-2]['context']['plan_multi_aweme']=True
        with patch.object(self.ledger,'claim',side_effect=mutate):out=self.dispatch()
        self.assertEqual(out['state'],'failed_before_io');self.assertFalse(out['submitted'])
        self.transport.assert_not_called()
        with self.ledger.connect() as db:self.assertEqual(db.execute('SELECT state FROM submission_attempts').fetchone()[0],'failed')

    def test_parent_appended_during_token_lookup_is_not_hidden_by_a_list_copy(self):
        def token():
            row=copy.deepcopy(self.rows[-2]);row['id']='appended-parent'
            row['source']['data_at']=(self.at+timedelta(seconds=1)).isoformat()
            row['source']['captured_at']=row['source']['data_at'];row['context']['plan_multi_aweme']=True
            self.rows.append(row);return 'PRIVATE_FAKE'
        with self.assertRaises(ValueError):self.dispatch(token=token)
        self.transport.assert_not_called()
        with self.ledger.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM submission_attempts').fetchone()[0],0)

    def test_default_transport_selects_the_fixed_creation_contract(self):
        with patch('monitor.control_creation_client.create_task',return_value={'code':0,'data':{'id':999}}) as post:
            out=self.dispatch(ControlCreationClient(self.ledger,enable_network=True,clock=lambda:self.at))
        self.assertEqual(out['created_task_id'],'999');self.assertEqual(post.call_count,1)
        self.assertEqual(json.loads(post.call_args.args[0])['scene'],'SMART_BOOST');self.assertEqual(post.call_args.args[2],10)
        self.assertFalse(out['creation_receipt']['origin_verified'])


if __name__=='__main__':unittest.main()
