import copy
import unittest
from datetime import timedelta

from monitor.schema import normalize_snapshot
from monitor.evaluation import evaluate_all
from test_engine import NOW, series, settings


def logged_row():
    row=series()[-1]
    row['source'].update(kind='browser_dom',url='https://qianchuan.jinritemai.com/uni-prom/detail?aavid=123',
        data_at=None,captured_at=NOW.isoformat(),segment='plan_operation_log')
    row['metrics']={}
    row['metric_basis']='unknown'
    row['context']={'plan_id':row['entity_id'],'observed_plan_operations':[
        {'occurred_at':(NOW-timedelta(minutes=20)).isoformat(),'kind':'control_budget_change',
         'control_id':'8344933447404222','before_budget':1200,'after_budget':1000}]}
    row['control'].update(history_known=True,last_change_at=None)
    return row


class PlanOperationObservationTests(unittest.TestCase):
    def test_native_log_preserves_values_and_only_supplies_partial_history_time(self):
        value=normalize_snapshot(logged_row(),settings(),NOW)
        self.assertFalse(value['control']['history_known'])
        self.assertIsNone(value['source']['data_at'])
        self.assertEqual(value['control']['last_change_at'],(NOW-timedelta(minutes=20)).isoformat())
        self.assertEqual(value['context']['observed_plan_operations'][0]['after_budget'],1000)
        self.assertEqual(value['metrics'],{})

    def test_only_explicit_valid_log_segment_accepts_a_metricless_snapshot(self):
        row=logged_row();row['context'].pop('observed_plan_operations')
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)
        row=logged_row();row['source'].pop('segment')
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)
        row=logged_row();row['metric_basis']='overall_payment'
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)
        row=logged_row();row['metrics']={'budget':10000}
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)
        row=logged_row();row['context']['plan_roi_basis']='net'
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_logs_require_live_detail_and_matching_parent_account(self):
        for mutate in [lambda x:x.update(scope='account'),lambda x:x['context'].update(plan_id='other'),
                lambda x:x['source'].update(kind='import'),
                lambda x:x['source'].update(url='https://qianchuan.jinritemai.com/overall-prom?aavid=123'),
                lambda x:x['source'].update(url='https://qianchuan.jinritemai.com/uni-prom/detail?aavid=456')]:
            row=logged_row();mutate(row)
            with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_raw_text_operator_extra_fields_bad_ids_and_unsupported_types_rejected(self):
        for update in [{'operator':'private'},{'content':'raw text'},{'control_id':1234567},
                {'kind':'pause'},{'before_budget':'1200'},{'after_budget':True},
                {'before_budget':-1},{'after_budget':float('nan')}]:
            row=logged_row();row['context']['observed_plan_operations'][0].update(update)
            with self.subTest(update=update),self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_log_clock_cannot_be_future_naive_or_disagree_with_recent_change(self):
        for at in [(NOW+timedelta(seconds=1)).isoformat(),NOW.replace(tzinfo=None).isoformat(),'2026-02-30T00:00:00+08:00']:
            row=logged_row();row['context']['observed_plan_operations'][0]['occurred_at']=at
            with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)
        row=logged_row();row['control']['last_change_at']=NOW.isoformat()
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_duplicates_normalize_once_conflicts_reject_and_zero_is_preserved(self):
        row=logged_row();operation=row['context']['observed_plan_operations'][0];operation['after_budget']=0
        row['context']['observed_plan_operations'].append(copy.deepcopy(operation))
        value=normalize_snapshot(row,settings(),NOW)
        self.assertEqual(len(value['context']['observed_plan_operations']),1)
        self.assertEqual(value['context']['observed_plan_operations'][0]['after_budget'],0)
        row['context']['observed_plan_operations'][1]['after_budget']=900
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_empty_or_excessive_rows_are_not_complete_history(self):
        for count in (0,201):
            row=logged_row();row['context']['observed_plan_operations']*=count
            with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_same_observation_survives_new_empty_source_and_blocks_tuning(self):
        observed=normalize_snapshot(logged_row(),settings(),NOW)
        rows=series(roi=2.5);rows.extend([observed,copy.deepcopy(rows[-1])])
        result=evaluate_all(rows,settings(),NOW)
        self.assertEqual(len(result['observed_plan_operations']),1)
        operation=result['observed_plan_operations'][0]
        self.assertFalse(operation['history_complete']);self.assertFalse(operation['execution_verified'])
        self.assertEqual(operation['occurred_at'],(NOW-timedelta(minutes=20)).isoformat())
        self.assertTrue(any('共同操作间隔' in b for r in result['recommendations'] for b in r['blockers']))

    def test_repeated_capture_deduplicates_without_refreshing_operation_time(self):
        first=normalize_snapshot(logged_row(),settings(),NOW)
        second=copy.deepcopy(first);second['id']='later';second['source']['captured_at']=(NOW+timedelta(minutes=1)).isoformat()
        result=evaluate_all([first,second],settings(),NOW+timedelta(minutes=1))
        self.assertEqual(len(result['observed_plan_operations']),1)
        self.assertEqual(result['observed_plan_operations'][0]['observed_at'],second['source']['captured_at'])
        self.assertEqual(result['observed_plan_operations'][0]['occurred_at'],first['control']['last_change_at'])

    def test_conflicting_history_values_are_not_arbitrarily_displayed(self):
        first=normalize_snapshot(logged_row(),settings(),NOW);second=copy.deepcopy(first);second['id']='conflict'
        second['context']['observed_plan_operations'][0]['after_budget']=900
        result=evaluate_all([first,second],settings(),NOW)
        self.assertEqual(result['observed_plan_operations'],[])
        self.assertEqual(result['conflicting_plan_operation_observations'],1)
        self.assertEqual(len(result['recent_plan_operations']),1)

    def test_local_report_keeps_only_the_selected_account_log_evidence(self):
        from monitor.reporting import build_report,report_markdown
        rows=[normalize_snapshot(logged_row(),settings(),NOW)]
        result=evaluate_all(rows,settings(),NOW)
        foreign=copy.deepcopy(result['observed_plan_operations'][0]);foreign['account_id']='foreign-account'
        result['observed_plan_operations'].append(foreign)
        report=build_report(rows,settings(),result,'123',NOW)
        self.assertEqual(len(report['evidence_and_risks']['observed_plan_operations']),1)
        self.assertFalse(report['evidence_and_risks']['observed_plan_operations'][0]['execution_verified'])
        markdown=report_markdown(report)
        self.assertIn('已观察的计划操作日志',markdown)
        self.assertIn('8344933447404222',markdown)
        self.assertNotIn('foreign-account',markdown)

    def test_ai_package_excludes_operation_rows_and_native_task_ids(self):
        import json
        from monitor.ai import make_package
        rows=[normalize_snapshot(logged_row(),settings(),NOW)]
        result=evaluate_all(rows,settings(),NOW)
        package=make_package(rows,result)
        serialized=json.dumps(package)
        self.assertNotIn('8344933447404222',serialized)
        self.assertNotIn('observed_plan_operations',serialized)
        self.assertNotIn('before_budget',serialized)


if __name__=='__main__':unittest.main()
