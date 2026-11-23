import copy
import json
import unittest
from datetime import timedelta

from monitor.actuation import prepare_action
from monitor.evaluation import evaluate_all
from monitor.schema import normalize_context
from monitor.smart_control import CREATE_PATH,STOP_PATH,prepare_smart_control,interpret_smart_control_response
from monitor.smart_control_demo import smart_control_scenario,smart_control_preview
from test_engine import settings


def fixture():
    rows,cfg,at=smart_control_scenario(settings())
    for row in rows:row['source']['kind']='import'
    evaluation=evaluate_all(rows,cfg,at)
    proposal=next(p for p in evaluation['recommendations'] if p['action']=='one_click_control')
    return rows,cfg,at,proposal,evaluation


class SmartControlTests(unittest.TestCase):
    def preview(self,rows,cfg,at,p,e):
        return prepare_action(p,rows[-2],cfg,at,snapshots=rows,evaluation=e)

    def test_exact_create_and_parent_stop_keep_local_duration_separate(self):
        rows,cfg,at,p,e=fixture();before=copy.deepcopy((rows,cfg,p,e))
        out=self.preview(rows,cfg,at,p,e)
        self.assertTrue(out['supported_for_preview'],out['blockers'])
        body=out['request']['body_json'];self.assertIn('8012367229158357',body)
        self.assertEqual(json.loads(body),{'advertiser_id':9990001,'ad_id':8012367229158357,'budget':100})
        self.assertTrue(out['request']['url'].endswith(CREATE_PATH))
        stop=out['required_stop_request'];self.assertTrue(stop['url'].endswith(STOP_PATH))
        self.assertEqual(json.loads(stop['body_json']),{'advertiser_id':9990001,'ad_id':8012367229158357,'opt_type':'DISABLE'})
        self.assertEqual(out['local_duration_policy']['duration_minutes'],30)
        self.assertIsNone(out['local_duration_policy']['due_at'])
        self.assertFalse(out['local_duration_policy']['deadline_registered'])
        self.assertFalse(stop['submitted']);self.assertFalse(stop['parent_hold_released'])
        self.assertFalse(out['live_execution_allowed']);self.assertTrue(out['execution_blockers'])
        self.assertEqual((rows,cfg,p,e),before)

    def test_budget_is_explicit_not_copied_from_boost_or_entire_parent(self):
        for key,val in [('smart_control_budget',None),('smart_control_budget',True),('smart_control_budget',0),
            ('smart_control_budget','100.001'),('smart_control_budget',100000),('smart_control_budget_reviewed',False)]:
            rows,cfg,at,p,e=fixture();rows[-2]['context'][key]=val
            self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'],(key,val))

    def test_parent_budget_mode_status_and_human_review_are_required(self):
        for key,val in [('latest_budget_verified',False),('manual_reviewed',False),('plan_delivery_mode','volume')]:
            rows,cfg,at,p,e=fixture();rows[-2]['context'][key]=val
            self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])
        rows,cfg,at,p,e=fixture();rows[-2]['control']['running']=False
        self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])

    def test_full_evaluation_and_exact_current_snapshot_are_required(self):
        rows,cfg,at,p,e=fixture()
        self.assertFalse(prepare_action(p,rows[-2],cfg,at)['supported_for_preview'])
        for change in ('proposal','snapshot','health'):
            other=copy.deepcopy(e)
            if change=='proposal':other['recommendations']=[]
            elif change=='snapshot':other['entities'][0]['latest_snapshot_id']='other'
            else:
                for s in other['collection_health']['sources']:s['clock_current']=False
            self.assertFalse(self.preview(rows,cfg,at,p,other)['supported_for_preview'],change)
        altered=copy.deepcopy(rows[-2]);altered['metrics']['budget']=99999
        self.assertFalse(prepare_action(p,altered,cfg,at,snapshots=rows,evaluation=e)['supported_for_preview'])

    def test_local_minutes_do_not_borrow_boost_native_half_hour_grid(self):
        for hours,minutes in [(.5,30),(.75,45),(1,60),(.9,54)]:
            rows,cfg,at,p,e=fixture();p['parameters']['duration_hours']=hours
            out=self.preview(rows,cfg,at,p,e)
            self.assertTrue(out['supported_for_preview'],out['blockers'])
            self.assertEqual(out['local_duration_policy']['duration_minutes'],minutes)
            self.assertNotIn('duration',json.loads(out['request']['body_json']))

    def test_invalid_local_duration_and_unverified_native_settings_are_rejected(self):
        for hours in [True,.25,1.5,'NaN','Infinity',{},.5001]:
            rows,cfg,at,p,e=fixture();p['parameters']['duration_hours']=hours
            self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'],hours)
        rows,cfg,at,p,e=fixture();p['parameters']['scene']='SMART_BOOST'
        self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])

    def test_non_live_non_universal_and_dom_do_not_borrow_this_contract(self):
        for field,val in [('scene','product'),('promotion_type','chengfang')]:
            rows,cfg,at,p,e=fixture();rows[-2][field]=val
            self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])
        rows,cfg,at,p,e=fixture();rows[-2]['source']['kind']='browser_dom'
        self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])

    def test_null_user_target_stale_data_and_changed_identity_remain_blocked(self):
        for change in ('target','stale','id'):
            rows,cfg,at,p,e=fixture()
            if change=='target':cfg['accounts'][0]['target_roi']=None
            elif change=='stale':rows[-2]['source']['data_at']=(at-timedelta(hours=2)).isoformat()
            else:p['target_id']='another-plan'
            self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])

    def test_response_acknowledgement_never_invents_task_id_stop_or_unlock(self):
        for data in [{},{'id':8753264005940210}]:
            out=interpret_smart_control_response({'code':0,'data':data})
            self.assertTrue(out['business_accepted']);self.assertIsNone(out['created_task_id'])
            self.assertFalse(out['platform_state_observed']);self.assertFalse(out['parent_hold_released'])
            self.assertFalse(out['automatic_retry'])
        for value in [None,{}, {'code':False,'data':{}},{'code':0,'data':None},{'code':0,'data':[]}]:
            self.assertFalse(interpret_smart_control_response(value)['business_accepted'])
        self.assertTrue(interpret_smart_control_response({'code':1})['known_rejected'])

    def test_delayed_forecast_and_recovered_carrying_state_cannot_reuse_prior_advice(self):
        rows,cfg,at,p,e=fixture()
        self.assertFalse(self.preview(rows,cfg,at+timedelta(minutes=2),p,e)['supported_for_preview'])
        rows[-2]['context']['carrying_ready']=True
        self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])

    def test_observed_surge_requires_its_own_complete_low_roi_windows(self):
        rows,cfg,at,p,e=fixture();p['evidence'].update(trigger='observed_surge',
            current_hour={'complete':True,'spend':150,'roi':1},previous_hour={'complete':True,'spend':100})
        self.assertTrue(self.preview(rows,cfg,at,p,e)['supported_for_preview'])
        for key,value in [('complete',False),('spend',130),('roi',3)]:
            evidence=copy.deepcopy(p['evidence']);p['evidence']['current_hour'][key]=value
            self.assertFalse(self.preview(rows,cfg,at,p,e)['supported_for_preview'])
            p['evidence']=evidence

    def test_import_budget_review_cannot_be_certified_by_dom_or_legacy(self):
        raw={'smart_control_budget':123.45,'smart_control_budget_reviewed':True}
        self.assertEqual(normalize_context(raw,'import'),raw)
        for kind in ('browser_dom','legacy_db'):
            self.assertIsNone(normalize_context(raw,kind)['smart_control_budget_reviewed'])
        with self.assertRaises(ValueError):normalize_context({'smart_control_budget_reviewed':'true'},'import')
        with self.assertRaises(ValueError):normalize_context({'smart_control_budget':-1},'import')

    def test_demo_is_fictitious_without_executable_url_persistence_or_real_target_changes(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None;before=copy.deepcopy(cfg)
        rows,synthetic,at=smart_control_scenario(cfg);e=evaluate_all(rows,synthetic,at)
        out=smart_control_preview(rows,synthetic,at,e)
        self.assertEqual(cfg,before);self.assertTrue(out['synthetic'])
        self.assertEqual(out['account_id'],'9990001');self.assertTrue(out['budget_example_only'])
        self.assertIsNone(out['create_request']['url']);self.assertIsNone(out['stop_request']['url'])
        for flag in ('persisted','executed','native_client_enabled','deadline_registered','state_readback_verified'):
            self.assertIs(out[flag],False)

    def test_recommendation_cites_smart_control_contracts_not_boost_as_its_write_basis(self):
        _,_,_,p,_=fixture();refs=p['policy_basis']['official_references']
        urls={r['url'] for r in refs}
        self.assertIn('https://open.oceanengine.com/labels/12/docs/8752793111424956',urls)
        self.assertIn('https://open.oceanengine.com/labels/12/docs/8008572109101042',urls)


if __name__=='__main__':unittest.main()
