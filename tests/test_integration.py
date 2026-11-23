import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package
from monitor.catalog import coverage_report
from monitor.evaluation import evaluate_all
from monitor.schema import normalize_context, normalize_snapshot
from monitor.demo import scenario
from test_engine import NOW, series, settings
from test_boundaries import config


class IntegratedStrategiesTests(unittest.TestCase):
    def test_all_consumers_receive_expanded_coverage_and_consistent_summary(self):
        result=evaluate_all(series(roi=2.5),settings(),NOW)
        self.assertIn('material_chase_admission',result['coverage']['strategies'])
        self.assertIn(str(len(result['recommendations']))+' 条离线建议',result['summary'])
        self.assertFalse(result['coverage']['execution_enabled'])
        json.dumps(result,allow_nan=False)

    def test_separate_reports_cannot_double_change_same_plan(self):
        first=series(roi=2.5);second=copy.deepcopy(first)
        for row in second:row['source']['series_key']='other-report'
        result=evaluate_all(first+second,settings(),NOW)
        eligible=[r for r in result['recommendations'] if r['eligible'] and r['action']=='budget_up']
        self.assertEqual(len(eligible),1)
        self.assertEqual(len(result['entities']),2)

    def test_empty_user_target_never_uses_platform_target(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        rows=series(roi=2.5)
        for row in rows:row['metrics']['target_roi']=2
        result=evaluate_all(rows,cfg,NOW)
        self.assertFalse(any(r['eligible'] for r in result['recommendations']))

    def test_new_strategy_evidence_is_anonymized_before_ai(self):
        rows=series();rows[0]['context']={'parent_plan_id':'private-parent-123','account_source_key':'private-report'}
        evaluation=evaluate_all(rows,settings(),NOW)
        evaluation['recommendations'][0]['evidence']={'parent_plan_id':'private-parent-123','account_source_key':'private-report','spend':100}
        package=make_package(rows,evaluation)
        text=json.dumps(package)
        self.assertNotIn('private-parent-123',text);self.assertNotIn('private-report',text)
        self.assertIn('"spend": 100',text)

    def test_outcome_candidates_never_gain_local_action_eligibility_or_leak_report_notes_to_ai(self):
        from test_outcomes import fixture,NOW as at
        event,rows,cfg=fixture('bid_down',post_rate=40)
        event.update(id='private-event-42',note='secret-raw-note')
        result=evaluate_all(rows,cfg,at,events=[event])
        self.assertEqual(len(result['outcome_review']['candidates']),1)
        candidate=result['outcome_review']['candidates'][0]
        self.assertFalse(candidate['execution_eligible'])
        self.assertNotIn(candidate['id'],{r['id'] for r in result['recommendations']})
        package=make_package(rows,result);raw=json.dumps(package)
        self.assertEqual(len(package['outcome_observations']),1)
        self.assertNotIn('private-event-42',raw);self.assertNotIn('secret-raw-note',raw);self.assertNotIn('exact-report',raw)


class CoverageAndContextTests(unittest.TestCase):
    def row(self,report,field):
        return next(r for r in report['accounts'][0]['metrics'] if r['id']==field)

    def test_marketing_roi_cannot_fill_payment_roi_inventory(self):
        row=series(basis='marketing',scope='account')[-1]
        row['metrics'].update(roi=9,marketing_roi=3)
        result=coverage_report([row],settings(),NOW)
        self.assertEqual(self.row(result,'账户效果:roi')['data_status'],'missing')
        self.assertEqual(self.row(result,'账户效果:marketing_roi')['objects_observed'],1)

    def test_inventory_keeps_missing_stale_and_demo_distinct(self):
        row=series(scope='account')[-1];row['metrics']['balance']=0
        row['source']['data_at']=(NOW-timedelta(hours=2)).isoformat()
        demo=copy.deepcopy(row);demo['source']['kind']='demo';demo['metrics']['gpm']=123
        result=coverage_report([row,demo],settings(),NOW)
        self.assertEqual(self.row(result,'账户消耗:balance')['data_status'],'stale')
        self.assertEqual(self.row(result,'账户效果:gpm')['data_status'],'missing')
        self.assertEqual(self.row(result,'账户消耗:balance')['evidence'][0]['value'],0)

    def test_context_requires_real_booleans_and_browser_cannot_certify_review(self):
        with self.assertRaises(ValueError):normalize_context({'model_stable':'false'},'import')
        value=normalize_context({'model_stable':None,'manual_reviewed':True,'chase_history_known':True},'browser_dom')
        self.assertIsNone(value['model_stable']);self.assertIsNone(value['manual_reviewed']);self.assertIsNone(value['chase_history_known'])

    def test_count_metrics_are_integer_and_fractional_counts_rejected(self):
        cfg=config();rows,_,now=scenario(cfg);row=copy.deepcopy(rows[-1])
        row['metrics']['base_orders']=10.0
        self.assertIs(type(normalize_snapshot(row,cfg,now)['metrics']['base_orders']),int)
        row['metrics']['base_orders']=10.5
        with self.assertRaises(ValueError):normalize_snapshot(row,cfg,now)

    def test_unknown_context_fields_never_survive_import(self):
        value=normalize_context({'plan_id':'exact-id','cookie':'secret','budget_mode':'specified'},'import')
        self.assertNotIn('cookie',value);self.assertEqual(value['plan_id'],'exact-id')


if __name__=='__main__':unittest.main()
