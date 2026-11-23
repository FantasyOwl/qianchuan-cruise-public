import copy
import unittest
from datetime import timedelta

from monitor.evaluation import evaluate_all
from monitor.schema import normalize_context
from test_engine import NOW, settings
from test_strategies import standard_rows, run


def reduction_rows():
    rows=standard_rows();changed=NOW-timedelta(hours=1)
    for row in rows:
        row['context']['last_bid_change_at']=changed.isoformat()
        row['metrics']['previous_bid']=100
        if row['source']['data_at']>=changed.isoformat():
            row['metrics'].update(bid=95,spend=1800,gmv=4500,orders=36,clicks=720,impressions=18000)
    return rows


def review(rows,now=NOW):
    result=evaluate_all(rows,settings(),now)
    return result,result['standard_bid_reviews'][-1]


class StandardBidEvidenceTests(unittest.TestCase):
    def test_complete_cost_bid_configuration_and_two_hour_series_allow_semantic_reduction(self):
        result,out=review(standard_rows())
        self.assertTrue(out['stable_two_hours'],out['tuning_missing'])
        proposal=next(p for p in result['recommendations'] if p['action']=='bid_down')
        self.assertTrue(next(p for p in run(standard_rows())['recommendations'] if p['action']=='bid_down')['eligible'])
        self.assertFalse(proposal['eligible'])
        self.assertIn('同对象',''.join(proposal['blockers']))
        self.assertEqual((proposal['before'],proposal['after']),(100,95))
        self.assertFalse(out['native_bid_submission_available'])
        self.assertFalse(out['platform_configuration_authenticated'])

    def test_observed_reduction_and_stall_reference_both_sides_of_configuration_history(self):
        result,out=review(reduction_rows())
        self.assertTrue(out['last_reduction_observed'],out['recovery_missing'])
        proposal=next(p for p in result['recommendations'] if p['action']=='bid_restore')
        self.assertTrue(proposal['eligible'],proposal['blockers'])
        self.assertEqual((proposal['before'],proposal['after']),(95,100))
        self.assertTrue(out['before_observations']);self.assertTrue(out['after_observations'])
        self.assertFalse(out['causality_proven'])
        self.assertIn('bid_review',proposal['evidence'])

    def test_numeric_previous_bid_cannot_replace_observed_prior_bid(self):
        rows=reduction_rows();changed=NOW-timedelta(hours=1)
        for row in rows:
            if row['source']['data_at']<changed.isoformat():row['metrics']['bid']=90
        result,out=review(rows)
        self.assertFalse(out['last_reduction_observed'])
        self.assertFalse(any(p['eligible'] for p in result['recommendations'] if p['action']=='bid_restore'))
        self.assertIn('降价前',''.join(out['recovery_missing']))

    def test_missing_or_stale_independent_configuration_clock_blocks(self):
        for value in (None,(NOW-timedelta(hours=1)).isoformat(),(NOW+timedelta(minutes=3)).isoformat()):
            with self.subTest(value=value):
                rows=standard_rows();rows[-1]['context']['plan_bid_observed_at']=value
                _,out=review(rows);self.assertFalse(out['stable_two_hours'])
                self.assertIn('配置观察时间',''.join(out['tuning_missing']))

    def test_roi_mode_and_wrong_target_cost_basis_do_not_authorize_cost_bid_tuning(self):
        for change in ({'plan_bid_mode':'roi'},{'target_cost_basis':'net'},{'plan_bid_config_verified':False}):
            with self.subTest(change=change):
                rows=standard_rows();rows[-1]['context'].update(change)
                result,out=review(rows)
                self.assertFalse(out['stable_two_hours'])
                self.assertFalse(any(p['eligible'] for p in result['recommendations'] if p['action']=='bid_down'))

    def test_a_second_unreported_bid_change_in_the_observation_window_blocks_reduction(self):
        rows=standard_rows();rows[-4]['metrics']['bid']=101
        _,out=review(rows)
        self.assertFalse(out['stable_two_hours'])
        self.assertIn('其他变更',''.join(out['tuning_missing']))

    def test_unknown_historical_configuration_is_not_carried_forward(self):
        rows=reduction_rows();rows[-3]['context']['plan_bid_config_verified']=False
        _,out=review(rows)
        self.assertFalse(out['last_reduction_observed'])
        self.assertIn('未知',''.join(out['recovery_missing']))

    def test_conflicting_previous_bid_fields_are_rejected(self):
        rows=reduction_rows();rows[-1]['metrics']['bid_before_drop']=99
        _,out=review(rows)
        self.assertFalse(out['last_reduction_observed'])
        self.assertIn('相互冲突',''.join(out['recovery_missing']))

    def test_same_configuration_clock_with_conflicting_values_cannot_be_deduplicated_away(self):
        rows=reduction_rows();conflict=copy.deepcopy(rows[-3])
        conflict['id']='conflicting-configuration';conflict['metrics']['bid']=94
        rows.append(conflict)
        _,out=review(rows)
        self.assertFalse(out['last_reduction_observed'])
        self.assertIn('冲突',''.join(out['recovery_missing']))

    def test_duplicate_identical_configuration_observations_keep_audit_ids(self):
        rows=reduction_rows();duplicate=copy.deepcopy(rows[-3]);duplicate['id']='repeated-configuration'
        rows.append(duplicate);_,out=review(rows)
        self.assertTrue(out['last_reduction_observed'],out['recovery_missing'])
        self.assertTrue(any('repeated-configuration' in p['snapshot_ids'] for p in out['after_observations']))

    def test_report_clock_gap_is_not_filled_from_statistics_sampling(self):
        rows=reduction_rows()
        for row in rows[-5:-2]:row['context']['plan_bid_observed_at']=(NOW-timedelta(hours=3)).isoformat()
        _,out=review(rows)
        self.assertFalse(out['last_reduction_observed'])

    def test_future_change_or_incomplete_post_change_hour_does_not_prove_recovery(self):
        for changed in (NOW+timedelta(seconds=1),NOW-timedelta(minutes=59)):
            rows=reduction_rows();rows[-1]['context']['last_bid_change_at']=changed.isoformat()
            _,out=review(rows);self.assertFalse(out['last_reduction_observed'])

    def test_changed_source_cannot_borrow_another_series_prior_bid_history(self):
        rows=reduction_rows();changed=NOW-timedelta(hours=1)
        for row in rows:
            if row['source']['data_at']>=changed.isoformat():row['source']['series_key']='new-report-series'
        result,_=review(rows)
        newer=next(r for r in result['standard_bid_reviews'] if r['source_key']=='new-report-series')
        self.assertFalse(newer['last_reduction_observed'])
        self.assertIn('降价前',''.join(newer['recovery_missing']))

    def test_page_visibility_does_not_authenticate_configuration_history(self):
        rows=standard_rows()
        for row in rows:row['source'].update(kind='browser_dom',coverage='visible_only')
        _,out=review(rows);self.assertFalse(out['stable_two_hours'])
        self.assertIn('页面可见值',''.join(out['tuning_missing']))

    def test_schema_preserves_explicit_configuration_time_and_strict_boolean_flag(self):
        value=normalize_context({'plan_bid_mode':'conversion_cost','plan_bid_observed_at':NOW.isoformat(),
            'plan_bid_config_verified':True},'import')
        self.assertIs(value['plan_bid_config_verified'],True)
        self.assertEqual(value['plan_bid_observed_at'],NOW.isoformat())
        with self.assertRaises(ValueError):normalize_context({'plan_bid_config_verified':'true'},'import')

    def test_configuration_declaration_and_cold_cost_cap_are_different_requirements(self):
        rows=standard_rows();rows[-1]['context']['plan_bid_mode']='roi'
        result,out=review(rows)
        self.assertFalse(out['stable_two_hours'])
        self.assertFalse(out['native_bid_submission_available'])
        self.assertEqual(result['coverage']['strategies']['standard_bid_tuning']['state'],'insufficient')


if __name__=='__main__':unittest.main()
