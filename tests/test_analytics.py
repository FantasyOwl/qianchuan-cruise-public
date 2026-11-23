import copy
import json
import unittest
from datetime import timedelta

from monitor.analytics import analyze_monitoring
from test_engine import CN,NOW,series,settings


def analyze(rows,cfg=None,now=NOW):
    return analyze_monitoring(rows,cfg or settings(),now)


class MonitoringAnalysisTests(unittest.TestCase):
    def test_hourly_roi_uses_differences_instead_of_daily_ratio(self):
        rows=series(roi=2.5)
        for row in rows:row['metrics']['roi']=99
        result=analyze(rows)['entities'][0]
        self.assertEqual(len(result['hourly']),3)
        self.assertTrue(all(w['roi']==2.5 for w in result['hourly']))

    def test_session_can_cross_midnight_without_becoming_daily(self):
        now=NOW.replace(day=22,hour=0,minute=30)
        rows=series(scope='live',now=now)
        start=rows[0]['source']['data_at']
        for row in rows:
            row['source'].update(metric_date=start[:10],metric_period='live_session',period_start=start)
            row['metrics']['online_users']=100+int(row['id'])
        result=analyze(rows,now=now)['entities'][0]
        self.assertEqual(result['metric_period'],'live_session')
        self.assertEqual(sum(w['complete'] for w in result['hourly']),3)
        self.assertEqual(result['data_status'],'fresh')

    def test_daily_midnight_reset_is_not_a_session_interval(self):
        now=NOW.replace(day=22,hour=0,minute=30)
        rows=series(scope='live',now=now)
        entities=analyze(rows,now=now)['entities']
        today=[e for e in entities if e['data_at'].startswith('2026-10-22')][0]
        self.assertFalse(any(w['complete'] for w in today['hourly']))

    def test_different_live_sessions_and_sources_never_join(self):
        rows=series(scope='live')
        for n,row in enumerate(rows):
            row['source']['series_key']='first' if n<9 else 'second'
        entities=analyze(rows)['entities']
        self.assertEqual(len(entities),2)
        self.assertTrue(all(sum(w['complete'] for w in e['hourly'])<3 for e in entities))

    def test_missing_interior_value_gap_and_counter_reset_block_hour(self):
        for mutate in (lambda rs:rs[-3]['metrics'].update(gmv=None),lambda rs:rs[-3]['metrics'].update(gmv=0)):
            rows=series();mutate(rows)
            self.assertFalse(analyze(rows)['entities'][0]['hourly'][-1]['complete'])
        rows=series();del rows[-5:-2]
        self.assertFalse(analyze(rows)['entities'][0]['hourly'][-1]['complete'])

    def test_creative_base_roi_never_borrows_total_revenue(self):
        rows=series(scope='creative',roi=9)
        for row in rows:
            row['metrics'].update(base_spend=row['metrics']['spend'],base_gmv=row['metrics']['spend']*2,base_orders=row['metrics']['orders'])
        item=analyze(rows)['entities'][0]
        self.assertTrue(all(w['roi']==2 and w['ledger']=='base' for w in item['hourly']))
        rows[-1]['metrics']['base_gmv']=None
        self.assertFalse(analyze(rows)['entities'][0]['hourly'][-1]['complete'])

    def test_missing_data_update_time_does_not_become_fresh_on_capture(self):
        row=series(scope='live')[-1];row['source']['data_at']=None
        result=analyze([row])['entities'][0]
        self.assertEqual(result['data_status'],'unverified');self.assertEqual(result['hourly'],[])

    def test_visible_scope_warning_allows_observation_but_not_action_eligibility(self):
        from monitor.evaluation import evaluate_all
        rows=series(roi=2.5)
        for row in rows:
            row['source']['kind']='browser_dom';row['source']['coverage']='visible_only'
            row['quality']['issues']=['仅当前渲染计划行，未翻页且未核验全量及操作历史']
        result=evaluate_all(rows,settings(),NOW)
        analysis=result['monitoring_analysis']['entities'][0]
        self.assertEqual(sum(w['complete'] for w in analysis['hourly']),3)
        self.assertEqual(analysis['data_status'],'fresh')
        self.assertFalse(any(r['eligible'] for r in result['recommendations']))
        rows[-1]['quality']['issues'].append('计数范围发生冲突')
        self.assertFalse(analyze(rows)['entities'][0]['hourly'][-1]['complete'])

    def test_funnel_exposes_counts_but_requires_verified_cohort_for_rates(self):
        row=series(scope='live')[-1]
        row['metrics'].update(funnel_exposures=1000,funnel_entries=100,funnel_product_clicks=50,funnel_created_orders=10,funnel_payments=8)
        result=analyze([row])['entities'][0]['funnel']
        self.assertEqual(result['stages'][0]['value'],1000)
        self.assertTrue(all(r['percent'] is None for r in result['rates']))
        row['context']={'funnel_same_cohort_verified':True,'funnel_layout':'live'}
        result=analyze([row])['entities'][0]['funnel']
        self.assertEqual([r['percent'] for r in result['rates']],[10,50,20,80])
        self.assertEqual(result['largest_observed_dropoff']['from'],'funnel_exposures')

    def test_funnel_missing_stage_zero_and_invalid_nested_counts_stay_missing(self):
        row=series(scope='product')[-1];row['context']={'funnel_same_cohort_verified':True,'funnel_layout':'product'}
        row['metrics'].update(funnel_exposures=1000,funnel_entries=100,funnel_created_orders=0,funnel_payments=2,funnel_orders=50)
        rates=analyze([row])['entities'][0]['funnel']['rates']
        self.assertIsNone(rates[0]['percent']);self.assertIsNone(rates[1]['percent']);self.assertIsNone(rates[-1]['percent'])
        row['metrics']['funnel_created_orders']=101
        self.assertIsNone(analyze([row])['entities'][0]['funnel']['rates'][2]['percent'])
        self.assertEqual(analyze([row])['entities'][0]['funnel']['additional_observations'][0]['value'],50)

    def test_lifecycle_candidate_is_separate_from_manual_label(self):
        rows=series();rows[-1]['context']={'created_at':(NOW-timedelta(days=1)).isoformat()}
        result=analyze(rows)['entities'][0]['lifecycle']
        self.assertEqual(result['candidate'],'cold');self.assertEqual(result['declared'],'mature');self.assertFalse(result['used_for_action'])
        rows[-1]['context']['created_at']=(NOW+timedelta(days=1)).isoformat()
        self.assertEqual(analyze(rows)['entities'][0]['lifecycle']['candidate'],'unknown')

    def test_low_sample_does_not_certify_mature_lifecycle(self):
        rows=series(roi=2.5,orders_step=0)
        rows[-1]['context']={'model_stable':True}
        self.assertEqual(analyze(rows)['entities'][0]['lifecycle']['candidate'],'unknown')

    def test_product_profit_role_is_never_inferred_from_gmv(self):
        row=series(scope='product',roi=3)[-1]
        product=analyze([row])['entities'][0]['product']
        self.assertEqual(product['role'],'unknown');self.assertIsNone(product['account_gmv_contribution_percent'])

    def test_new_product_needs_explicit_metrics_and_user_target(self):
        row=series(scope='product',roi=3)[-1];row['context']={'product_role':'new','product_metrics_verified':True}
        self.assertEqual(analyze([row])['entities'][0]['product']['new_product_test'],'sample_met_roi_qualified')
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        self.assertEqual(analyze([row],cfg)['entities'][0]['product']['new_product_test'],'target_missing')

    def test_product_contribution_needs_exact_same_account_source(self):
        product=series(scope='product',roi=3)[-1];product['context']={'product_metrics_verified':True,'product_account_coverage_verified':True,'account_source_key':'account-report'}
        account=copy.deepcopy(product);account['scope']='account';account['entity_id']='123';account['source']['series_key']='account-report';account['metrics']['gmv']*=2
        result=next(e for e in analyze([product,account])['entities'] if e['scope']=='product')
        self.assertEqual(result['product']['account_gmv_contribution_percent'],50)
        account['source']['series_key']='other-report'
        result=next(e for e in analyze([product,account])['entities'] if e['scope']=='product')
        self.assertIsNone(result['product']['account_gmv_contribution_percent'])

    def test_product_contribution_cannot_link_two_sessions_on_same_day(self):
        product=series(scope='product',roi=3)[-1];product['context']={'product_metrics_verified':True,'product_account_coverage_verified':True,'account_source_key':'account-report'}
        account=copy.deepcopy(product);account['scope']='account';account['entity_id']='123';account['source']['series_key']='account-report';account['metrics']['gmv']*=2
        for row in (product,account):row['source'].update(metric_period='live_session',period_start=(NOW-timedelta(hours=3)).isoformat())
        item=next(e for e in analyze([product,account])['entities'] if e['scope']=='product')
        self.assertEqual(item['product']['account_gmv_contribution_percent'],50)
        account['source']['period_start']=(NOW-timedelta(hours=2)).isoformat()
        item=next(e for e in analyze([product,account])['entities'] if e['scope']=='product')
        self.assertIsNone(item['product']['account_gmv_contribution_percent'])

    def test_primary_budget_progress_excludes_control_spend_and_never_falls_back(self):
        row=series()[-1];row['metrics'].update(spend=900,budget=1000,base_spend=400,control_spend=500)
        budget=analyze([row])['entities'][0]['budget']
        self.assertEqual(budget['progress_percent'],40);self.assertEqual(budget['ledger'],'base')
        del row['metrics']['base_spend']
        self.assertIsNone(analyze([row])['entities'][0]['budget']['progress_percent'])

    def test_session_total_does_not_fill_daily_budget_progress(self):
        row=series()[-1];row['source'].update(metric_period='live_session',period_start=(NOW-timedelta(hours=3)).isoformat())
        self.assertIsNone(analyze([row])['entities'][0]['budget']['progress_percent'])

    def test_pre_ship_refund_increase_and_stock_zero_create_observational_alerts(self):
        rows=series(scope='product')
        for row in rows:row['metrics']['refund_rate_pre_ship']=int(row['id'])/6*6
        rows[-1]['metrics']['stock']=0
        result=analyze(rows)
        self.assertEqual({a['rule'] for a in result['alerts']},{'product_stock_zero','pre_ship_refund_spike'})
        self.assertFalse(result['execution_enabled'])

    def test_stale_stock_data_does_not_create_current_emergency(self):
        row=series(scope='product')[-1];row['metrics']['stock']=0
        result=analyze([row],now=NOW+timedelta(hours=2))
        self.assertEqual(result['alerts'],[])
        self.assertEqual(result['entities'][0]['data_status'],'stale')

    def test_malformed_inputs_are_skipped_and_all_outputs_are_json_safe(self):
        result=analyze([None,{}, {'source':{'metric_period':{}}}])
        self.assertEqual(result['entities'],[]);json.dumps(result,allow_nan=False)
        row=series()[-1];row['source']['metric_period']={}
        self.assertEqual(analyze([row])['entities'],[])
        row=series()[-1];row['quality']['issues']=[{}]
        self.assertEqual(analyze([row])['entities'][0]['data_status'],'unverified')


if __name__=='__main__':unittest.main()
