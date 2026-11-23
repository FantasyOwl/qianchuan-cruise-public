import copy
import unittest
from datetime import timedelta

from monitor.evaluation import evaluate_all
from monitor.engine import _record, _settings
from monitor.recent_operations import apply_recent_operation_guard
from test_engine import NOW, series, settings


def observed_change(rows, *, age=20, kind='browser_dom', plan='plan1', account='123'):
    row = copy.deepcopy(rows[-1]); row.update(id='observed-change', account_id=account, entity_id=plan)
    row['source'].update(kind=kind, series_key='visible-config', data_at=None,
        url='https://qianchuan.jinritemai.com/uni-prom/detail?aavid=' + account)
    row['metrics'] = {'budget': 10000}
    row['control'].update(history_known=False, guarantee_verified=False,
        last_change_at=(NOW-timedelta(minutes=age)).isoformat())
    return row


class RecentOperationTests(unittest.TestCase):
    def test_partial_dom_history_blocks_other_source_without_certifying_history(self):
        rows = series(roi=2.5); rows.append(observed_change(rows))
        result = evaluate_all(rows, settings(), NOW)
        recommendations = [r for r in result['recommendations'] if r['action']=='budget_up']
        self.assertTrue(recommendations)
        self.assertTrue(all(not r['eligible'] for r in recommendations))
        self.assertTrue(any('共同操作间隔' in b for r in recommendations for b in r['blockers']))
        self.assertFalse(result['recent_plan_operations'][0]['history_complete'])
        self.assertFalse(rows[-1]['control']['history_known'])

    def test_other_plan_account_or_demo_cannot_hold_a_real_plan(self):
        for kwargs in ({'plan':'another-plan'}, {'account':'456'}, {'kind':'demo'}):
            rows=series(roi=2.5); rows.append(observed_change(rows, **kwargs))
            with self.subTest(kwargs=kwargs):
                result=evaluate_all(rows, settings(), NOW)
                self.assertTrue(any(r['eligible'] for r in result['recommendations'] if r['action']=='budget_up'))

    def test_midnight_does_not_reset_observed_operation_interval(self):
        now=NOW.replace(hour=0,minute=10);rows=series(roi=2.5,now=now)
        row=observed_change(rows);row['control']['last_change_at']=(now-timedelta(minutes=20)).isoformat()
        row['source']['captured_at']=(now-timedelta(minutes=5)).isoformat()
        row['source']['metric_date']=(now-timedelta(days=1)).date().isoformat();rows.append(row)
        result=evaluate_all(rows,settings(),now)
        self.assertEqual(result['recent_plan_operations'][0]['general_tuning_after'],
            (now+timedelta(minutes=40)).isoformat())

    def test_newer_empty_history_cannot_clear_an_observed_change(self):
        rows=series(roi=2.5);change=observed_change(rows)
        change['source']['captured_at']=(NOW-timedelta(minutes=1)).isoformat()
        newer=copy.deepcopy(change);newer['id']='new-empty';newer['source']['captured_at']=NOW.isoformat()
        newer['control']['last_change_at']=None;rows.extend([change,newer])
        result=evaluate_all(rows,settings(),NOW)
        self.assertFalse(any(r['eligible'] for r in result['recommendations'] if r['action']=='budget_up'))

    def test_invalid_or_elapsed_observations_do_not_create_a_hold(self):
        for age in (-10, 120, 200):
            rows=series(roi=2.5);rows.append(observed_change(rows,age=age))
            with self.subTest(age=age):
                result=evaluate_all(rows,settings(),NOW)
                self.assertEqual(result['recent_plan_operations'],[])

    def test_expiry_only_removes_this_guard_and_does_not_grant_eligibility(self):
        rows=series(roi=2.5);rows.append(observed_change(rows,age=70));cfg=settings()
        cfg['accounts'][0]['target_roi']=None
        result=evaluate_all(rows,cfg,NOW)
        self.assertFalse(any(r['eligible'] for r in result['recommendations']))
        self.assertEqual(len(result['recent_plan_operations']),1)

    def test_child_operation_needs_explicit_matching_parent_source(self):
        for linked in (True, False):
            rows=series(roi=2.5);row=observed_change(rows)
            row.update(scope='control',entity_id='child-task')
            row['context']={'parent_plan_id':'plan1'}
            row['source']['parent_entity_id']='plan1' if linked else 'other-plan'
            rows.append(row);result=evaluate_all(rows,settings(),NOW)
            growth=[r for r in result['recommendations'] if r['action']=='budget_up']
            self.assertEqual(any(r['eligible'] for r in growth),not linked)

    def test_bid_keeps_two_hour_interval_after_general_tuning_interval_elapsed(self):
        rows=series(roi=2.5);rows.append(observed_change(rows,age=90));cfg=settings()
        record=_record(rows[0],_settings(cfg)[1],NOW)
        source_key=':'.join(map(str,record['key'][3:]))
        result={'recommendations':[{'account_id':'123','entity_id':'plan1','scope':'plan',
            'source_key':source_key,'action':action,'eligible':True,'blockers':[],'evidence':{}}
            for action in ('budget_up','bid_down')]}
        apply_recent_operation_guard(result,rows,cfg,NOW)
        self.assertTrue(result['recommendations'][0]['eligible'])
        self.assertFalse(result['recommendations'][1]['eligible'])
        self.assertEqual(result['recommendations'][1]['evidence']['observed_plan_cooldown']['not_before'],
            (NOW+timedelta(minutes=30)).isoformat())

    def test_budget_handover_cannot_prepare_its_first_step_before_shared_guard(self):
        from test_migration import fixture, evaluate_pair
        rows,cfg,_,plan=fixture()
        baseline=evaluate_pair(rows,cfg,NOW,migrations=[plan])
        self.assertEqual(baseline['budget_migrations'][0]['state'],'ready_reduce')
        row=observed_change(rows,plan='new');row['metrics']['budget']=5000;rows.append(row)
        result=evaluate_pair(rows,cfg,NOW,migrations=[plan])
        self.assertEqual(result['budget_migrations'][0]['state'],'held')
        self.assertIsNone(result['budget_migrations'][0]['next_step'])


if __name__=='__main__':
    unittest.main()
