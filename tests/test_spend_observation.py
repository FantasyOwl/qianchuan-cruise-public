import copy
import json
import unittest
from datetime import timedelta

from monitor.analytics import analyze_monitoring
from monitor.evaluation import evaluate_all
from monitor.reporting import build_report, report_markdown
from monitor.ai import make_package
from test_engine import NOW, series, settings


def observe(rows, cfg=None, now=NOW):
    return analyze_monitoring(rows, cfg or settings(), now)['entities'][0]


class SpendObservationTests(unittest.TestCase):
    def test_net_amount_decrease_does_not_hide_independent_cost_or_relax_roi(self):
        rows=series(scope='account',basis='marketing')
        for i,r in enumerate(rows):r['metrics']['net_gmv']=10000-i*10
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        result=evaluate_all(rows,cfg,NOW)
        item=result['monitoring_analysis']['entities'][0]
        trend=item['spend_trend']
        self.assertEqual([w['cost'] for w in trend['windows']],[600]*3)
        self.assertEqual(trend['change_percent'],0)
        self.assertTrue(trend['is_current'])
        self.assertFalse(any(w['complete'] for w in item['hourly']))
        self.assertFalse(result['recommendations'])
        self.assertIsNone(cfg['accounts'][0]['target_roi'])

    def test_cost_reset_and_missing_base_cost_never_borrow_inclusive_total(self):
        rows=series();rows[-3]['metrics']['spend']=0
        self.assertFalse(observe(rows)['spend_trend']['windows'][-1]['complete'])
        rows=series()
        for row in rows:row['metrics'].update(base_spend=None,base_gmv=100,control_spend=50)
        trend=observe(rows)['spend_trend']
        self.assertEqual((trend['field'],trend['ledger']),('base_spend','base'))
        self.assertFalse(any(w['complete'] for w in trend['windows']))

    def test_explicit_ledger_and_basis_select_the_cost_without_cross_account_sum(self):
        cases=[('overall_payment','plan','base_spend','base'),('marketing','plan','base_comprehensive_cost','base'),
               ('control_payment','control','control_spend','control'),('marketing','account','comprehensive_cost','overall')]
        for basis,scope,field,ledger in cases:
            with self.subTest(basis=basis,scope=scope):
                rows=series(basis=basis,scope=scope)
                for i,r in enumerate(rows):r['metrics'][field]=i*20
                t=observe(rows)['spend_trend']
                self.assertEqual((t['field'],t['ledger']),(field,ledger))
                self.assertEqual(t['windows'][-1]['cost'],120)

    def test_zero_previous_rate_has_no_infinite_growth_and_last_zero_is_retained(self):
        rows=series()
        for i,r in enumerate(rows):r['metrics']['spend']=max(0,i-12)*10
        t=observe(rows)['spend_trend']
        self.assertIsNone(t['change_percent']);self.assertIn('前窗消耗为零',t['comparison_reason'])
        for i,r in enumerate(rows):r['metrics']['spend']=min(i,12)*10
        t=observe(rows)['spend_trend']
        self.assertEqual(t['windows'][-1]['cost'],0)
        self.assertEqual(t['windows'][-1]['average_cost_per_hour'],0)
        self.assertEqual(t['change_percent'],-100)
        json.dumps(t,allow_nan=False)

    def test_growth_uses_actual_durations_when_nearest_boundary_is_four_minutes_late(self):
        rows=series()
        t=NOW-timedelta(minutes=56)
        rows[12]['source'].update(data_at=t.isoformat(),captured_at=t.isoformat())
        trend=observe(rows)['spend_trend']
        before,after=trend['windows'][-2:]
        self.assertEqual(before['observed_seconds'],64*60)
        self.assertEqual(after['observed_seconds'],56*60)
        self.assertAlmostEqual(trend['change_percent'],100/7)
        self.assertEqual(after['observed_start'],t.isoformat())

    def test_missing_interior_cost_gap_quality_and_duplicate_conflict_block_only_valid_windows(self):
        def conflict(rows):
            other=copy.deepcopy(rows[-3]);other['metrics']['spend']+=1;rows.append(other)
        for mutate in [lambda r:r[-3]['metrics'].update(spend=None),lambda r:r.__delitem__(slice(-5,-2)),
                       lambda r:r[-3]['quality'].update(issues=['计数范围冲突']),conflict]:
            with self.subTest(mutate=mutate):
                rows=series();mutate(rows)
                trend=observe(rows)['spend_trend']
                self.assertFalse(trend['windows'][-1]['complete'])
                self.assertIsNone(trend['change_percent'])

    def test_no_timestamp_unknown_basis_and_midnight_do_not_fill_a_window(self):
        for kind in ['missing_time','unknown_basis','day_boundary']:
            rows=series()
            if kind=='missing_time':
                for row in rows:row['source']['data_at']=None
            elif kind=='unknown_basis':
                for row in rows:row['metric_basis']='unknown'
            else:
                rows=series(now=NOW.replace(hour=0,minute=30))
            items=analyze_monitoring(rows,settings(),NOW if kind!='day_boundary' else NOW.replace(hour=0,minute=30))['entities']
            for item in items:
                if kind=='day_boundary' and not item['data_at'].startswith(NOW.date().isoformat()):
                    self.assertFalse(item['spend_trend']['is_current'])
                    continue
                self.assertFalse(any(w['complete'] for w in item['spend_trend']['windows']))

    def test_different_source_or_session_never_completes_each_other(self):
        for change in ['source','session']:
            rows=series()
            for i,row in enumerate(rows):
                if change=='source':row['source']['series_key']='first' if i<9 else 'second'
                else:row['source'].update(metric_period='live_session',period_start=(NOW-timedelta(hours=4 if i<9 else 2)).isoformat())
            items=analyze_monitoring(rows,settings(),NOW)['entities']
            self.assertEqual(len(items),2)
            self.assertTrue(all(len([w for w in x['spend_trend']['windows'] if w['complete']])<3 for x in items))

    def test_later_capture_with_missing_or_regressed_time_cannot_claim_current_cost(self):
        for data_at in [None,(NOW-timedelta(minutes=20)).isoformat()]:
            rows=series()
            latest=copy.deepcopy(rows[-1]);latest['id']='later'
            latest['source'].update(captured_at=(NOW+timedelta(seconds=10)).isoformat(),data_at=data_at)
            rows.append(latest)
            self.assertFalse(observe(rows,now=NOW+timedelta(seconds=10))['spend_trend']['is_current'])
        rows=series()
        self.assertFalse(observe(rows,now=NOW+timedelta(hours=2))['spend_trend']['is_current'])

    def test_report_preserves_exact_windows_and_account_scope_and_ai_anonymizes_ids(self):
        rows=series(scope='account');cfg=settings();other=copy.deepcopy(rows)
        cfg['accounts'].append({'id':'PRIVATE_ACCOUNT','roi_basis':'overall_payment','target_roi':None})
        for r in other:
            r.update(id='other-'+r['id'],account_id='PRIVATE_ACCOUNT',entity_id='PRIVATE_PLAN')
            r['source']['series_key']='PRIVATE_SERIES'
        all_rows=rows+other;e=evaluate_all(all_rows,cfg,NOW)
        report=build_report(all_rows,cfg,e,'123',NOW)
        observations=report['evidence_and_risks']['spend_observations']
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0]['trend']['windows'][-1]['cost'],600)
        self.assertNotIn('PRIVATE',json.dumps(report))
        self.assertIn('消耗分时独立观察',report_markdown(report))
        package=make_package(all_rows,e)
        self.assertNotIn('PRIVATE',json.dumps(package))
        self.assertTrue(package['spend_observations'][0]['trend']['observation_only'])


if __name__=='__main__':unittest.main()
