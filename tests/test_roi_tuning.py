import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package,validate_proposals
from monitor.evaluation import evaluate_all
from monitor.reporting import build_report,report_markdown
from monitor.schema import normalize_snapshot
from test_engine import NOW,settings
from test_strategies import roi_up_rows,ready_rows,account_row,run,recs


def current_goal(rows,value):
    for row in rows:
        if row['scope']=='plan':row['metrics']['target_roi']=value
    return rows


def down_rows(goal=3.5):
    rows=current_goal(ready_rows(),goal)
    for row in rows[13:]:
        row['metrics'].update({key:rows[12]['metrics'][key] for key in ('spend','gmv','net_gmv','orders','clicks','impressions','comprehensive_cost')})
    account=account_row();account['id']='account-latest'
    return rows+[account]


def recommendation(rows,action='roi_target_up',cfg=None):return recs(run(rows,cfg),action)[0]


class RoiTuningTests(unittest.TestCase):
    def test_user_threshold_and_observed_setting_have_independent_values(self):
        rec=recommendation(current_goal(roi_up_rows(),3.5))
        self.assertTrue(rec['eligible']);self.assertEqual(rec['before'],3.5);self.assertEqual(rec['after'],3.68)
        self.assertAlmostEqual(rec['change_pct'],.18/3.5)
        self.assertEqual(rec['evidence']['strategy_target_roi'],2)
        self.assertEqual(rec['evidence']['plan_roi_configuration']['observed_plan_goal'],3.5)

    def test_observed_high_goal_cannot_turn_good_assessment_roi_into_failure(self):
        rows=current_goal(roi_up_rows(),10)
        for row in rows:row['metrics']['gmv']=row['metrics']['spend']*2.5
        self.assertFalse(recs(run(rows),'roi_target_up'))

    def test_user_empty_or_wrong_basis_never_borrows_plan_goal(self):
        for change in ({'target_roi':None},{'roi_basis':'net'}):
            cfg=settings();cfg['accounts'][0].update(change)
            self.assertFalse(recs(run(current_goal(roi_up_rows(),10),cfg),'roi_target_up'))

    def test_downward_quantization_uses_current_goal_and_actual_fraction(self):
        rec=recommendation(down_rows(),'roi_target_down')
        self.assertTrue(rec['eligible']);self.assertEqual(rec['before'],3.5);self.assertEqual(rec['after'],3.32)
        self.assertAlmostEqual(rec['change_pct'],-.18/3.5)
        self.assertEqual(rec['evidence']['strategy_target_roi'],2)

    def test_missing_current_goal_is_not_filled_from_assessment(self):
        for value in (None,0):
            rec=recommendation(current_goal(roi_up_rows(),value))
            self.assertFalse(rec['eligible']);self.assertIsNone(rec['before']);self.assertIsNone(rec['after'])
            self.assertIsNone(rec['change_pct'])

    def test_missing_each_configuration_declaration_holds_specific_value(self):
        for field in ('plan_roi_basis','plan_roi_mode','plan_roi_step','plan_roi_min','plan_roi_max','plan_roi_config_verified','plan_roi_observed_at'):
            rows=current_goal(roi_up_rows(),3.5)
            rows[-1]['context'].pop(field)
            rec=recommendation(rows)
            self.assertFalse(rec['eligible'],field);self.assertEqual(rec['before'],3.5);self.assertIsNone(rec['after'])
            self.assertTrue(rec['blockers'])

    def test_conversion_cost_mode_wrong_goal_basis_or_unverified_configuration_cannot_adjust(self):
        for change in ({'plan_roi_mode':'conversion_cost'},{'plan_roi_basis':'marketing'},{'plan_roi_config_verified':False}):
            rows=roi_up_rows();rows[-1]['context'].update(change)
            rec=recommendation(rows);self.assertFalse(rec['eligible']);self.assertIsNone(rec['after'])

    def test_configuration_time_is_capture_time_and_does_not_replace_data_time(self):
        rows=roi_up_rows();rows[-1]['source']['data_at']=(NOW-timedelta(minutes=1)).isoformat()
        self.assertIsNotNone(recommendation(rows)['after'])
        rows[-1]['context']['plan_roi_observed_at']=(NOW-timedelta(minutes=2)).isoformat()
        self.assertIsNone(recommendation(rows)['after'])

    def test_coarse_step_no_valid_small_change_is_held(self):
        rec=recommendation(current_goal(roi_up_rows(),.03))
        self.assertFalse(rec['eligible']);self.assertIsNone(rec['after'])
        self.assertIn('5%至10%',''.join(rec['blockers']))

    def test_exact_ten_percent_grid_boundary_is_permitted_without_exceeding_it(self):
        for rows,action,expected in ((current_goal(roi_up_rows(),.1),'roi_target_up',.11),(down_rows(.1),'roi_target_down',.09)):
            rec=recommendation(rows,action);self.assertEqual(rec['after'],expected)
            self.assertEqual(abs(rec['change_pct']),.1);self.assertTrue(rec['eligible'])

    def test_current_off_grid_or_out_of_range_is_not_rounded_into_a_new_old_value(self):
        for goal in (3.505,101):
            rec=recommendation(current_goal(roi_up_rows(),goal))
            self.assertFalse(rec['eligible']);self.assertEqual(rec['before'],goal);self.assertIsNone(rec['after'])

    def test_allowed_boundary_cannot_be_crossed_to_reach_nominal_change(self):
        for rows,action,change in ((current_goal(roi_up_rows(),3.5),'roi_target_up',{'plan_roi_max':3.67}),
                                  (down_rows(),'roi_target_down',{'plan_roi_min':3.33})):
            for row in rows:row['context'].update(change)
            rec=recommendation(rows,action);self.assertFalse(rec['eligible']);self.assertIsNone(rec['after'])

    def test_invalid_declared_step_is_held_for_direct_callers(self):
        for value in (None,0,-.01,True,'0.01',.0000001,float('nan'),10**1000):
            rows=roi_up_rows();rows[-1]['context']['plan_roi_step']=value
            self.assertIsNone(recommendation(rows)['after'])

    def test_target_change_after_declared_last_change_is_a_history_conflict(self):
        rows=roi_up_rows();rows[-1]['metrics']['target_roi']=3.5
        rec=recommendation(rows);self.assertFalse(rec['eligible']);self.assertIsNone(rec['after'])
        self.assertIn('最近变更时间',''.join(rec['blockers']))

    def test_properly_timed_prior_target_change_keeps_real_current_value(self):
        rows=roi_up_rows();changed=NOW-timedelta(minutes=70)
        for row in rows:
            row['control']['last_change_at']=changed.isoformat()
            if row['source']['captured_at']>changed.isoformat():row['metrics']['target_roi']=3.5
        rec=recommendation(rows);self.assertTrue(rec['eligible']);self.assertEqual(rec['after'],3.68)

    def test_same_capture_configuration_conflict_does_not_choose_one_goal(self):
        rows=roi_up_rows();duplicate=copy.deepcopy(rows[-1]);duplicate['id']='conflict';duplicate['metrics']['target_roi']=3.5
        self.assertFalse(recs(run(rows+[duplicate]),'roi_target_up'))
        duplicate['metrics']['target_roi']=2;duplicate['context']['plan_roi_step']=.1
        rec=recommendation(rows+[duplicate]);self.assertFalse(rec['eligible']);self.assertIsNone(rec['after'])

    def test_upward_trigger_requires_fast_spend_and_both_cost_hours(self):
        rows=ready_rows(roi=1)
        for row in rows:row['metrics']['target_cost']=40
        self.assertFalse(recs(run(rows),'roi_target_up'))
        rows=roi_up_rows()
        for row in rows:row['metrics']['target_cost']=100
        self.assertFalse(recs(run(rows),'roi_target_up'))
        rows=roi_up_rows()
        for n,row in enumerate(rows):row['metrics']['orders']=n*5 if n<=18 else 90+(n-18)*2
        self.assertFalse(recs(run(rows),'roi_target_up'))

    def test_cooldown_and_running_state_remain_required(self):
        for change in ({'running':False},{'running':None},{'last_change_at':(NOW-timedelta(minutes=59)).isoformat()}):
            rows=roi_up_rows();rows[-1]['control'].update(change)
            self.assertFalse(recommendation(rows)['eligible'])
        rows=roi_up_rows();rows[-1]['control']['last_change_at']=(NOW-timedelta(hours=1)).isoformat()
        self.assertTrue(recommendation(rows)['eligible'])

    def test_standard_conversion_bid_cannot_be_treated_as_roi_optimization(self):
        rows=roi_up_rows()
        for row in rows:row['promotion_type']='standard';row['context']['plan_roi_mode']='conversion_cost'
        rec=recommendation(rows);self.assertFalse(rec['eligible']);self.assertIsNone(rec['after'])

    def test_fresh_cross_source_alias_and_range_conflicts_hold_downward_adjustment(self):
        for field,value in (('target_roi',4),('plan_roi_step',.1)):
            rows=down_rows();baseline=evaluate_all(rows,settings(),NOW)
            self.assertTrue(recs(baseline,'roi_target_down')[0]['eligible'])
            other=copy.deepcopy(rows[-2]);other['id']='other';other['entity_id']='alias';other['source']['series_key']='other-report'
            if field=='target_roi':other['metrics'][field]=value
            else:other['context'][field]=value
            result=evaluate_all(rows+[other],settings(),NOW)
            rec=recs(result,'roi_target_down')[0]
            self.assertFalse(rec['eligible']);self.assertIn('其他新鲜来源',''.join(rec['blockers']))

    def test_stale_or_different_basis_config_cannot_override_current_before(self):
        for change in ('stale','basis'):
            rows=down_rows();other=copy.deepcopy(rows[-2]);other['id']='other';other['entity_id']='alias';other['source']['series_key']='other-report'
            other['metrics']['target_roi']=4
            if change=='stale':other['context']['plan_roi_observed_at']=(NOW-timedelta(hours=1)).isoformat()
            else:other['context']['plan_roi_basis']='net'
            self.assertTrue(recs(evaluate_all(rows+[other],settings(),NOW),'roi_target_down')[0]['eligible'])

    def test_schema_preserves_declarations_but_browser_cannot_certify_configuration(self):
        rows=roi_up_rows();raw=copy.deepcopy(rows[-1]);raw['source'].update(kind='import',dataset_id='declared',metric_period='day')
        parsed=normalize_snapshot(raw,settings(),NOW)
        self.assertTrue(parsed['context']['plan_roi_config_verified']);self.assertEqual(parsed['context']['plan_roi_step'],.01)
        raw['source'].update(kind='browser_dom',url='https://qianchuan.jinritemai.com/uni-prom?aavid=123')
        self.assertIsNone(normalize_snapshot(raw,settings(),NOW)['context']['plan_roi_config_verified'])
        raw['source']['kind']='legacy_db'
        self.assertIsNone(normalize_snapshot(raw,settings(),NOW)['context']['plan_roi_config_verified'])

    def test_schema_rejects_bad_numeric_mode_and_range_declarations(self):
        for change in ({'plan_roi_step':True},{'plan_roi_step':0},{'plan_roi_min':100,'plan_roi_max':1},{'plan_roi_mode':'automatic'}):
            raw=copy.deepcopy(roi_up_rows()[-1]);raw['context'].update(change)
            with self.assertRaises(ValueError):normalize_snapshot(raw,settings(),NOW)

    def test_report_and_anonymous_ai_preserve_both_meanings_without_changing_inputs(self):
        rows=down_rows();cfg=settings();before=copy.deepcopy((rows,cfg))
        result=evaluate_all(rows,cfg,NOW);report=build_report(rows,cfg,result,'123',NOW)
        action=next(a for a in report['prioritized_actions'] if a['action']['type']=='roi_target_down')
        self.assertEqual(action['action']['before'],3.5);self.assertEqual(action['action']['after'],3.32)
        self.assertEqual(action['evidence']['strategy_target_roi'],2)
        self.assertIn('plan_roi_configuration',report_markdown(report))
        package=make_package(rows,result);text=json.dumps(package)
        self.assertIn('observed_plan_goal',text);self.assertIn('strategy_target_roi',text)
        self.assertNotIn('plan1',text);self.assertNotIn('account-report',text)
        self.assertEqual((rows,cfg),before)

    def test_ai_cannot_shrink_a_quantized_change_into_an_off_grid_fraction(self):
        rec=recommendation(down_rows(),'roi_target_down')
        result=validate_proposals({'analysis':'review','proposals':[{'recommendation_id':rec['id'],'change_pct':-.05}]},{'recommendations':[rec]})
        self.assertEqual(result['accepted_for_simulation'],[]);self.assertEqual(len(result['rejected']),1)

    def test_ai_exact_reference_preserves_local_before_and_after_values(self):
        rec=recommendation(down_rows(),'roi_target_down')
        result=validate_proposals({'analysis':'review','proposals':[{'recommendation_id':rec['id'],'change_pct':rec['change_pct']}]},{'recommendations':[rec]})
        item=result['accepted_for_simulation'][0]
        self.assertEqual(item['before'],3.5);self.assertEqual(item['after'],3.32);self.assertFalse(result['executed'])


if __name__=='__main__':unittest.main()
