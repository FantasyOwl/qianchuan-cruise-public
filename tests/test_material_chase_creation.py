import copy
import json
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from monitor.evaluation import evaluate_all
from monitor.material_chase_creation import preview_material_chase
from monitor.material_chase_demo import chase_creation_scenario


class MaterialChaseCreationTests(unittest.TestCase):
    def setUp(self):
        settings=json.loads(Path('config.json').read_text('utf-8'))
        self.rows,self.cfg,self.at,self.payload=chase_creation_scenario(settings)
        for row in self.rows:row['source']['kind']='import'
        self.evaluation=evaluate_all(self.rows,self.cfg,self.at)
        self.payload['proposal_id']=next(p['id'] for p in self.evaluation['recommendations'] if p['action']=='create_chase' and p['eligible'])

    def review(self,payload=None,evaluation=None):
        return preview_material_chase(payload or self.payload,self.rows,self.cfg,evaluation or self.evaluation,self.at)

    def test_prepared_body_keeps_native_ids_cost_bid_audience_and_deadline_separate(self):
        out=self.review();self.assertTrue(out['prepared']);draft=out['draft'];body=json.loads(draft['body_json'])
        self.assertEqual(body['advertiser_id'],9990001);self.assertEqual(body['ad_id'],8012367229158357)
        self.assertEqual(body['material_ids'],[8753264005940210]);self.assertEqual(body['audience']['aweme_fan_accounts'],[8321107035636407])
        self.assertEqual(body['bid'],10);self.assertEqual(body['smart_bid_type'],'SMART_BID_CUSTOM')
        self.assertEqual(body['external_action'],'AD_CONVERT_TYPE_LIVE_SUCCESSORDER_PAY')
        for field in ('duration','name','roi2_goal','deep_external_action'):self.assertNotIn(field,body)
        self.assertEqual(draft['deadline_reservation']['request_digest'],draft['body_digest'])
        self.assertEqual(draft['deadline_reservation']['duration_hours'],2)
        for key in ('live_execution_allowed','native_submission_ready','deadline_registered','executed','persisted','ai_sent'):self.assertFalse(out[key])
        self.assertIsNone(out['native_url'])

    def test_blank_user_target_does_not_inherit_a_plan_goal_or_imported_advice(self):
        self.cfg['accounts'][0]['target_roi']=None
        self.assertFalse(self.review()['prepared']);self.assertIsNone(self.cfg['accounts'][0]['target_roi'])

    def test_invented_or_ineligible_current_proposal_does_not_prepare(self):
        self.payload['proposal_id']='invented';self.assertFalse(self.review()['prepared'])

    def test_current_creative_and_parent_snapshot_ids_cannot_be_swapped_or_stale(self):
        for field in ('creative_snapshot_id','parent_snapshot_id'):
            p=copy.deepcopy(self.payload);p[field]='old-or-unknown';self.assertFalse(self.review(p)['prepared'])

    def test_material_id_is_not_inferred_from_plan_or_video_id(self):
        self.payload['material']['material_id']='8313320123514312'
        self.assertFalse(self.review()['prepared'])

    def test_video_status_audit_mapping_and_deleted_state_must_be_explicit(self):
        for field,value in (('material_type','IMAGE'),('material_status','DELIVERY_NOT'),('audit_status','IN_PROGRESS'),('is_delete',None),('mapping_verified',False)):
            p=copy.deepcopy(self.payload);p['material'][field]=value;self.assertFalse(self.review(p)['prepared'])

    def test_material_native_identity_and_observation_clock_are_required(self):
        for native in ({},{'video_id':'v','aweme_item_id':'123'},{'aweme_item_id':True},{'video_id':''}):
            p=copy.deepcopy(self.payload);p['material']['native']=native;self.assertFalse(self.review(p)['prepared'])
        self.payload['material']['observed_at']=(self.at-timedelta(minutes=10)).isoformat()
        self.assertFalse(self.review()['prepared'])

    def test_non_video_delivery_product_multi_aweme_or_wrong_goal_is_not_extrapolated(self):
        parent=self.rows[-3]
        for field,value in (('plan_multi_aweme',True),('plan_delivery_mode','volume'),('plan_optimization_goal','AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI')):
            saved=parent['context'][field];parent['context'][field]=value
            self.assertFalse(self.review()['prepared']);parent['context'][field]=saved
        parent['scene']='product';self.assertFalse(self.review()['prepared'])

    def test_missing_parent_chase_total_never_defaults_to_zero(self):
        parent=self.rows[-3]
        for total in (None,True,-1,Decimal('NaN'),Decimal('Infinity')):
            parent['metrics']['parent_chase_budget_total']=total;self.assertFalse(self.review()['prepared'])

    def test_total_budget_after_creation_must_fit_the_parent_30_percent_cap(self):
        self.rows[-3]['metrics']['parent_chase_budget_total']=2950
        self.assertFalse(self.review()['prepared'])

    def test_user_20_concurrent_material_strategy_is_not_the_api_20_per_request_rule(self):
        parent=self.rows[-3]
        for count in (None,20,21,0.5,True):
            parent['metrics']['active_heated_creatives']=count;self.assertFalse(self.review()['prepared'])

    def test_bid_is_not_the_assessment_roi_and_has_a_separate_budget_constraint(self):
        for bid in (None,True,0,20,10001,'10.001'):
            self.payload['conversion_bid']=bid;self.assertFalse(self.review()['prepared'])

    def test_targeting_review_must_match_current_parent_and_not_just_a_boolean(self):
        for field,value in (('reviewed',False),('parent_snapshot_id','other'),('method','unreviewed')):
            p=copy.deepcopy(self.payload);p['targeting_review'][field]=value;self.assertFalse(self.review(p)['prepared'])

    def test_follow_only_targeting_disallows_days_but_remains_a_valid_documented_combination(self):
        p=copy.deepcopy(self.payload);p['audience']['aweme_fan_behaviors']=['FOLLOWED_USER']
        self.assertFalse(self.review(p)['prepared']);del p['audience']['aweme_fan_behaviors_days']
        self.assertTrue(self.review(p)['prepared'])

    def test_targeting_unknown_keys_duplicate_ids_and_unknown_behavior_are_rejected(self):
        for change in ({'custom_audience':['unknown']},{'aweme_fan_accounts':['123','123']},
                       {'aweme_fan_behaviors':['unknown']},{'aweme_fan_behaviors_days':'DAYS_7'}):
            p=copy.deepcopy(self.payload);p['audience'].update(change);self.assertFalse(self.review(p)['prepared'])

    def test_budget_and_heating_inventory_need_current_reviewed_scope(self):
        p=self.rows[-3];p['context']['parent_control_inventory_verified']=False
        self.assertFalse(self.review()['prepared']);p['context']['parent_control_inventory_verified']=True
        p['context']['creative_inventory_at']=(self.at-timedelta(hours=1)).isoformat();self.assertFalse(self.review()['prepared'])

    def test_conflicting_parent_action_or_p0_risk_prevents_stacking(self):
        ev=copy.deepcopy(self.evaluation);ev['recommendations'].append({'eligible':True,'account_id':'9990001','entity_id':'8012367229158357','action':'budget_up'})
        self.assertFalse(self.review(evaluation=ev)['prepared'])
        ev=copy.deepcopy(self.evaluation);ev['alerts'].append({'priority':'P0','account_id':'9990001'})
        self.assertFalse(self.review(evaluation=ev)['prepared'])

    def test_stale_clock_cannot_reuse_the_previous_eligible_advice(self):
        ev=copy.deepcopy(self.evaluation)
        for item in ev['collection_health']['sources']:
            if item['snapshot_id']==self.payload['creative_snapshot_id']:item['clock_current']=False
        self.assertFalse(self.review(evaluation=ev)['prepared'])

    def test_unknown_credentials_and_duplicate_document_keys_are_rejected(self):
        p=copy.deepcopy(self.payload);p['access_token']='PRIVATE_SECRET'
        with self.assertRaises(ValueError) as error:self.review(p)
        self.assertNotIn('PRIVATE_SECRET',str(error.exception))
        with self.assertRaises(ValueError):self.review({'document_text':'{"format":"x","format":"y"}'})

    def test_file_round_trip_preserves_long_native_ids_without_javascript_rewriting(self):
        raw=json.dumps(self.payload);self.assertTrue(self.review({'document_text':raw})['prepared'])
        self.assertIn('8321107035636407',self.review({'document_text':raw})['draft']['body_json'])

    def test_mixed_demo_sources_do_not_gain_normal_preparation(self):
        self.rows[-2]['source']['kind']='demo';self.assertFalse(self.review()['prepared'])

    def test_preview_does_not_mutate_sources_settings_or_register_a_deadline(self):
        before=copy.deepcopy((self.rows,self.cfg,self.evaluation,self.payload))
        out=self.review();self.assertTrue(out['prepared']);self.assertEqual((self.rows,self.cfg,self.evaluation,self.payload),before)
        self.assertFalse(out['draft']['deadline_reservation']['registered'])

    def test_assessment_roi_precision_is_preserved_without_applying_currency_grid(self):
        self.cfg['accounts'][0]['target_roi']=2.000001
        self.evaluation=evaluate_all(self.rows,self.cfg,self.at)
        out=self.review();self.assertTrue(out['prepared']);self.assertEqual(out['draft']['account_assessment_target'],'2.000001')
        self.assertNotIn('roi2_goal',json.loads(out['draft']['body_json']))

    def test_net_assessment_is_separate_from_the_purchase_conversion_cost_task(self):
        self.cfg['accounts'][0]['roi_basis']='net'
        for row in self.rows:
            row['metric_basis']='net';row['metrics']['net_gmv']=row['metrics']['gmv']
            if row['scope']=='creative':row['metrics']['base_net_gmv']=row['metrics']['base_gmv']
            row['context']['plan_optimization_goal']='AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI'
        self.evaluation=evaluate_all(self.rows,self.cfg,self.at)
        self.payload['proposal_id']=next(p['id'] for p in self.evaluation['recommendations'] if p['action']=='create_chase' and p['eligible'])
        out=self.review();self.assertTrue(out['prepared']);self.assertNotIn('deep_external_action',json.loads(out['draft']['body_json']))


if __name__=='__main__':unittest.main()
