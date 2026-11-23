"""A progressing overview clock cannot make cached plan rows current."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from monitor.analytics import analyze_monitoring
from monitor.catalog import coverage_report
from monitor.engine import evaluate
from monitor.evaluation import evaluate_all
from monitor.observation_time import PLAN_CLOCK_ISSUE, DETAIL_CLOCK_ISSUE, review_observation_time
from monitor.schema import normalize_snapshot
from monitor.store import Store
from test_engine import NOW, series, settings


def browser_rows(path='/overall-prom', scope='plan'):
    rows = series(roi=2.5, scope=scope)
    for row in rows:
        row['source'].update(kind='browser_dom', url='https://qianchuan.jinritemai.com'+path+'?aavid=123', coverage='visible_only')
    return rows


class PlanObservationTimeTests(unittest.TestCase):
    def test_both_overview_paths_preserve_values_but_do_not_lend_plan_clocks(self):
        for path in ('/overall-prom', '/uni-prom'):
            row=browser_rows(path)[-1];before=copy.deepcopy(row)
            reviewed=review_observation_time(row)
            self.assertIsNone(reviewed['source']['data_at'])
            self.assertEqual(reviewed['source']['reported_overview_data_at'],before['source']['data_at'])
            self.assertEqual(reviewed['metrics'],before['metrics'])
            self.assertEqual(reviewed['id'],before['id'])
            self.assertIn(PLAN_CLOCK_ISSUE,reviewed['quality']['issues'])
            self.assertEqual(row,before)
            self.assertEqual(review_observation_time(reviewed),reviewed)

    def test_ingestion_discards_legacy_collector_inherited_clock_before_hashing(self):
        row=browser_rows()[-1]
        normalized=normalize_snapshot(row,settings(),NOW)
        self.assertIsNone(normalized['source']['data_at'])
        self.assertEqual(normalized['source']['reported_overview_data_at'],row['source']['data_at'])
        self.assertEqual(normalized['metrics']['spend'],row['metrics']['spend'])
        self.assertIn(PLAN_CLOCK_ISSUE,normalized['quality']['issues'])

    def test_stored_legacy_rows_are_reviewed_on_read_without_rewriting_evidence(self):
        row=browser_rows()[-1]
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db');store.add([row])
            with store.connect() as c:
                original=c.execute('SELECT data_json FROM snapshots').fetchone()[0]
            views=[store.snapshots(),store.evaluation_snapshots(now=NOW),store.recovery_history([row],[])[0]]
            for view in views:
                self.assertIsNone(view[0]['source']['data_at'])
                self.assertEqual(view[0]['id'],row['id'])
            with store.connect() as c:
                self.assertEqual(c.execute('SELECT data_json FROM snapshots').fetchone()[0],original)
            self.assertEqual(json.loads(original),row)

    def test_old_rows_cannot_produce_hourly_roi_or_fresh_plan_health(self):
        rows=browser_rows();before=copy.deepcopy(rows)
        for row in rows:
            row['metrics']['spend']=100
            row['metrics']['gmv']=250
        result=evaluate(rows,settings(),NOW)
        self.assertTrue(all(e['quality']!='fresh' for e in result['entities']))
        analysis=analyze_monitoring(rows,settings(),NOW)['entities'][0]
        self.assertEqual(analysis['data_status'],'unverified')
        self.assertIsNone(analysis['data_at'])
        self.assertEqual(analysis['hourly'],[])
        self.assertEqual(analysis['spend_trend']['windows'],[])
        full=evaluate_all(rows,settings(),NOW)
        self.assertTrue(all(not s['clock_current'] for s in full['collection_health']['sources']))
        self.assertFalse(any(r['eligible'] for r in full['recommendations']))
        self.assertEqual(rows[0]['source'],before[0]['source'])

    def test_coverage_does_not_call_plan_values_fresh(self):
        coverage=coverage_report(browser_rows(),settings(),NOW)['accounts'][0]
        plan_spend=next(r for r in coverage['metrics'] if r['id']=='计划与基础台账:spend')
        self.assertEqual(plan_spend['data_status'],'unverified')
        self.assertIsNone(plan_spend['evidence'][0]['data_at'])

    def test_overview_account_board_import_and_other_hosts_are_unchanged(self):
        cases=[browser_rows(scope='account')[-1],browser_rows('/board-next','live')[-1],
               series()[-1]]
        other=browser_rows('/uni-prom/detail','control')[-1]
        other['source']['url']='https://example.test/uni-prom/detail?aavid=123'
        cases.append(other)
        for row in cases:
            with self.subTest(scope=row['scope'],source=row['source']):
                self.assertEqual(review_observation_time(row),row)

    def test_detail_header_cannot_certify_any_of_the_three_emitted_scopes(self):
        for scope in ('account','plan','control'):
            row=browser_rows('/uni-prom/detail',scope)[-1];before=copy.deepcopy(row)
            reviewed=review_observation_time(row)
            self.assertIsNone(reviewed['source']['data_at'])
            self.assertEqual(reviewed['source']['reported_detail_header_data_at'],row['source']['data_at'])
            self.assertIn(DETAIL_CLOCK_ISSUE,reviewed['quality']['issues'])
            self.assertEqual(reviewed['metrics'],before['metrics']);self.assertEqual(row,before)
            self.assertEqual(review_observation_time(reviewed),reviewed)

    def test_current_collector_header_metadata_survives_normalization_without_lending_clock(self):
        row=browser_rows('/uni-prom/detail')[-1];header=row['source']['data_at']
        row['source'].update(data_at=None,reported_detail_header_data_at=header)
        row['context']={'plan_roi_observed_at':row['source']['captured_at'],'plan_roi_basis':'net'}
        normalized=normalize_snapshot(row,settings(),NOW)
        self.assertIsNone(normalized['source']['data_at'])
        self.assertEqual(normalized['source']['reported_detail_header_data_at'],header)
        self.assertEqual(normalized['context']['plan_roi_observed_at'],row['source']['captured_at'])
        self.assertEqual(normalized['context']['plan_roi_basis'],'net')
        self.assertIn(DETAIL_CLOCK_ISSUE,normalized['quality']['issues'])

    def test_legacy_detail_clock_is_removed_before_hashing_and_bad_metadata_is_rejected(self):
        row=browser_rows('/uni-prom/detail','control')[-1]
        normalized=normalize_snapshot(row,settings(),NOW)
        self.assertIsNone(normalized['source']['data_at'])
        self.assertEqual(normalized['source']['reported_detail_header_data_at'],row['source']['data_at'])
        for value in ('invalid','2030-01-01T00:00:00+08:00'):
            row['source'].update(data_at=None,reported_detail_header_data_at=value)
            with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_detail_tasks_cannot_form_hourly_roi_health_or_fresh_coverage(self):
        rows=browser_rows('/uni-prom/detail','control')
        analysis=analyze_monitoring(rows,settings(),NOW)['entities'][0]
        self.assertIsNone(analysis['data_at']);self.assertEqual(analysis['hourly'],[])
        self.assertEqual(analysis['spend_trend']['windows'],[])
        full=evaluate_all(rows,settings(),NOW)
        self.assertTrue(all(not s['clock_current'] for s in full['collection_health']['sources']))
        self.assertFalse(any(r['eligible'] for r in full['recommendations']))

    def test_detail_history_read_projection_preserves_raw_original_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            for scope in ('account','plan','control'):
                row=browser_rows('/uni-prom/detail',scope)[-1]
                store=Store(Path(folder)/(scope+'.db'));store.add([row])
                with store.connect() as db:before=db.execute('SELECT data_json FROM snapshots').fetchone()[0]
                for view in (store.snapshots(),store.evaluation_snapshots(now=NOW),store.recovery_history([row],[])[0]):
                    self.assertIsNone(view[0]['source']['data_at']);self.assertEqual(view[0]['id'],row['id'])
                with store.connect() as db:self.assertEqual(db.execute('SELECT data_json FROM snapshots').fetchone()[0],before)


if __name__=='__main__':unittest.main()
