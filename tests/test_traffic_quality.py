import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package, validate_proposals
from monitor.analytics import analyze_monitoring
from monitor.demo import quality_scenario
from monitor.evaluation import evaluate_all
from monitor.reporting import build_report, report_markdown
from monitor.schema import normalize_snapshot
from test_engine import NOW, series, settings


def report(now=NOW):
    rows,_,_=quality_scenario(settings())
    row=rows[-1]
    # The synthetic scenario uses today's local calendar, independent of NOW.
    from monitor.schema import parse_time
    delta=now-parse_time(row['source']['data_at'])
    row['source'].update(data_at=now.isoformat(),captured_at=now.isoformat(),metric_date=now.date().isoformat())
    row['source']['period_start']=(now-timedelta(hours=4)).isoformat()
    row['source']['metric_date']=row['source']['period_start'][:10]
    for k in ('quality_baseline_start','quality_baseline_end','quality_window_start','quality_window_end'):
        row['context'][k]=(parse_time(row['context'][k])+delta).isoformat()
    row.update(account_id='123',entity_id='ROOM_PRIVATE',id='quality-now')
    row['source']['kind']='import';row['source']['dataset_id']='quality-report'
    row['context'].update(quality_baseline_source='PRIVATE_SOURCE_NOTE',quality_definition_id='PRIVATE_DEFINITION',
                          baseline_quality_definition_id='PRIVATE_DEFINITION',quality_segment_id='PRIVATE_SEGMENT',
                          baseline_quality_segment_id='PRIVATE_SEGMENT')
    return row


def analyze(rows,now=NOW,cfg=None):
    return analyze_monitoring(rows,cfg or settings(),now)


def quality(rows,now=NOW):
    return analyze(rows,now)['entities'][-1]['traffic']['quality']


class TrafficQualityTests(unittest.TestCase):
    def test_joint_decline_records_explicit_windows_and_local_threshold(self):
        row=report();result=analyze([row]);q=quality([row])
        self.assertEqual(q['state'],'suspected_broadening');self.assertTrue(q['is_current'])
        self.assertAlmostEqual(q['comparisons'][0]['relative_change'],-.5)
        self.assertEqual(result['alerts'][0]['priority'],'P1')
        self.assertFalse(result['recommendations'][0]['eligible']);self.assertFalse(q['platform_verified'])
        self.assertEqual(q['thresholds']['relative_drop'],.2)
        self.assertEqual(row['metrics']['watch_duration'],30)

    def test_order_decline_needs_one_engagement_decline(self):
        for field in ('watch_duration','interaction_rate'):
            row=report();row['metrics'].update(watch_duration=60,interaction_rate=12)
            row['metrics'][field]=0
            self.assertEqual(quality([row])['state'],'suspected_broadening')
        row=report();row['metrics'].update(watch_duration=60,interaction_rate=12)
        self.assertEqual(quality([row])['state'],'mixed_decline');self.assertFalse(analyze([row])['alerts'])

    def test_engagement_decline_without_order_decline_does_not_claim_broadening(self):
        row=report();row['metrics']['order_rate']=8
        self.assertEqual(quality([row])['state'],'mixed_decline');self.assertFalse(analyze([row])['alerts'])

    def test_improved_or_unchanged_does_not_mean_account_healthy(self):
        row=report();row['metrics'].update(watch_duration=60,interaction_rate=15,order_rate=8)
        q=quality([row]);self.assertEqual(q['state'],'no_material_decline')
        self.assertFalse(analyze([row])['alerts']);self.assertIn('不证明',q['note'])

    def test_current_zero_is_real_decline_baseline_zero_is_not_comparable(self):
        row=report();row['metrics'].update(watch_duration=0,interaction_rate=0,order_rate=0)
        self.assertEqual(quality([row])['comparisons'][0]['relative_change'],-1)
        row['metrics']['baseline_order_rate']=0;q=quality([row])
        self.assertEqual(q['state'],'insufficient');self.assertTrue(all(i['relative_change'] is None for i in q['comparisons']))

    def test_missing_each_dimension_blocks_whole_diagnosis(self):
        for field in ('watch_duration','interaction_rate','order_rate','baseline_watch_duration','baseline_interaction_rate','baseline_order_rate'):
            row=report();del row['metrics'][field]
            self.assertEqual(quality([row])['state'],'insufficient');self.assertFalse(analyze([row])['alerts'])

    def test_small_zero_fractional_or_unknown_user_sample_is_blocked(self):
        for field in ('quality_sample_users','baseline_quality_sample_users'):
            for value in (0,99,100.5,None):
                row=report();row['metrics'][field]=value
                self.assertEqual(quality([row])['state'],'insufficient')
        row=report();row['metrics'].update(quality_sample_users=100,baseline_quality_sample_users=100)
        self.assertTrue(analyze([row])['alerts'])

    def test_verification_and_baseline_source_are_required(self):
        for field in ('quality_metrics_verified','quality_baseline_verified','quality_baseline_source'):
            row=report();del row['context'][field]
            self.assertEqual(quality([row])['state'],'insufficient')

    def test_units_fraction_bound_and_window_duration_are_checked(self):
        for field,value in (('interaction_rate',101),('baseline_order_rate',101),('watch_duration',1801),('baseline_watch_duration',1801),('order_rate',float('nan'))):
            row=report();row['metrics'][field]=value
            self.assertEqual(quality([row])['state'],'insufficient');self.assertFalse(analyze([row])['alerts'])

    def test_definition_and_segment_ids_must_match(self):
        for field in ('quality_definition_id','baseline_quality_definition_id','quality_segment_id','baseline_quality_segment_id'):
            for value in (None,'OTHER',{},' '):
                row=report();row['context'][field]=value
                self.assertEqual(quality([row])['state'],'insufficient')

    def test_two_windows_must_be_adjacent_equal_length_and_recent_to_report(self):
        for field,delta in (('quality_window_start',-2),('quality_baseline_start',-2),('quality_baseline_end',-2),('quality_window_end',-2)):
            row=report();from monitor.schema import parse_time
            row['context'][field]=(parse_time(row['context'][field])+timedelta(minutes=delta)).isoformat()
            self.assertEqual(quality([row])['state'],'insufficient')

    def test_15_and_60_minute_windows_allowed_others_blocked(self):
        for minutes,allowed in ((14,False),(15,True),(60,True),(61,False)):
            row=report();row['context'].update(quality_window_start=(NOW-timedelta(minutes=minutes)).isoformat(),
                quality_baseline_start=(NOW-timedelta(minutes=minutes*2)).isoformat(),quality_baseline_end=(NOW-timedelta(minutes=minutes)).isoformat())
            self.assertEqual(bool(analyze([row])['alerts']),allowed)

    def test_day_cannot_borrow_previous_day_but_session_can_cross_midnight(self):
        now=NOW.replace(hour=0,minute=30);row=report(now)
        self.assertEqual(quality([row],now)['state'],'suspected_broadening')
        row['source'].update(metric_period='day',period_start=None)
        self.assertEqual(quality([row],now)['state'],'insufficient')

    def test_session_start_cannot_be_after_baseline(self):
        row=report();row['source']['period_start']=(NOW-timedelta(minutes=45)).isoformat()
        self.assertEqual(quality([row])['state'],'insufficient')

    def test_stale_report_remains_historical_without_current_alert_or_veto(self):
        row=report();q=quality([row],NOW+timedelta(hours=1))
        self.assertEqual(q['state'],'suspected_broadening');self.assertFalse(q['is_current'])
        self.assertFalse(analyze([row],NOW+timedelta(hours=1))['alerts'])

    def test_missing_data_time_never_replaced_by_capture(self):
        row=report();row['source']['data_at']=None
        self.assertEqual(quality([row])['state'],'insufficient');self.assertFalse(analyze([row])['alerts'])

    def test_not_in_live_or_unknown_ongoing_cannot_raise_current_alarm(self):
        for value in (False,None):
            row=report();row['context']['live_ongoing']=value
            self.assertFalse(quality([row])['is_current']);self.assertFalse(analyze([row])['alerts'])
        row=report();row['scope']='plan'
        self.assertNotIn('traffic',analyze([row])['entities'][0])

    def test_same_time_metric_metadata_and_quality_conflicts_block(self):
        for mutate in (lambda r:r['metrics'].update(order_rate=2),lambda r:r['context'].update(quality_baseline_verified=False),
                       lambda r:r['context'].update(live_ongoing=False),lambda r:r['quality'].update(issues=['来源冲突'])):
            a=report();b=copy.deepcopy(a);mutate(b)
            self.assertEqual(quality([a,b])['state'],'insufficient');self.assertFalse(analyze([a,b])['alerts'])
        a=report();self.assertEqual(len(analyze([a,copy.deepcopy(a)])['alerts']),1)

    def test_other_sources_or_sessions_do_not_supply_missing_quality(self):
        a=report();b=copy.deepcopy(a);b['source']['series_key']='other'
        a['metrics']['baseline_order_rate']=None
        q=next(e['traffic']['quality'] for e in analyze([a,b])['entities'] if e['source_key']==a['source']['series_key'])
        self.assertEqual(q['state'],'insufficient')

    def test_browser_and_legacy_cannot_certify_even_direct_caller(self):
        for kind in ('browser_dom','legacy_db'):
            row=report();row['source']['kind']=kind
            self.assertEqual(quality([row])['state'],'insufficient');self.assertFalse(analyze([row])['alerts'])

    def test_ingestion_keeps_explicit_metadata_and_rejects_fractional_users(self):
        row=report();normalized=normalize_snapshot(row,settings(),NOW)
        self.assertEqual(normalized['metrics']['quality_sample_users'],450)
        self.assertEqual(normalized['context']['quality_definition_id'],'PRIVATE_DEFINITION')
        self.assertTrue(analyze([normalized])['alerts'])
        row['metrics']['quality_sample_users']=450.5
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_ingestion_browser_strips_quality_verification_flags(self):
        row=report();row['source'].update(kind='browser_dom',url='https://qianchuan.jinritemai.com/board-next?aavid=123')
        normalized=normalize_snapshot(row,settings(),NOW)
        self.assertIsNone(normalized['context']['quality_metrics_verified']);self.assertIsNone(normalized['context']['quality_baseline_verified'])

    def test_account_veto_blocks_growth_but_not_other_account_or_existing_reduction(self):
        rows=series(roi=3);q=report();cfg=settings();other=copy.deepcopy(rows)
        cfg['accounts'].append({'id':'456','scene':'live','roi_basis':'overall_payment','target_roi':2})
        for r in other:r.update(account_id='456',entity_id='other-plan',id='other'+r['id'])
        e=evaluate_all(rows+[q]+other,cfg,NOW)
        growth=[r for r in e['recommendations'] if r['action']=='budget_up']
        self.assertFalse(next(r for r in growth if r['account_id']=='123')['eligible'])
        self.assertTrue(next(r for r in growth if r['account_id']=='456')['eligible'])
        down=evaluate_all(series(roi=1)+[q],settings(),NOW)
        self.assertTrue(next(r for r in down['recommendations'] if r['action']=='budget_down')['eligible'])

    def test_user_roi_target_not_needed_for_quality_and_not_filled_from_platform(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        e=evaluate_all([report()],cfg,NOW)
        self.assertTrue(any(a['rule']=='traffic_quality_decline' for a in e['alerts']))
        self.assertIsNone(cfg['accounts'][0]['target_roi']);self.assertFalse(any(r['eligible'] for r in e['recommendations']))

    def test_report_resolves_exact_source_and_exports_account_scoped_quality(self):
        row=report();cfg=settings();e=evaluate_all([row],cfg,NOW);r=build_report([row],cfg,e,'123',NOW)
        action=next(a for a in r['prioritized_actions'] if a['rule']=='traffic_quality_decline')
        self.assertEqual(action['source_resolution'],'exact');self.assertEqual(action['source_snapshot_ids'],['quality-now'])
        self.assertEqual(len(r['evidence_and_risks']['traffic_quality_observations']),1)
        self.assertIn('流量质量配对窗口',report_markdown(r));self.assertFalse(action['execution_eligible'])
        cfg['accounts'].append({'id':'456','scene':'product','roi_basis':'overall_payment','target_roi':None})
        empty=build_report([row],cfg,e,'456',NOW)
        self.assertEqual(empty['evidence_and_risks']['traffic_quality_observations'],[])

    def test_ai_receives_numeric_diagnosis_without_private_context_and_cannot_enable_action(self):
        row=report();e=evaluate_all([row],settings(),NOW);package=make_package([row],e)
        text=json.dumps(package,ensure_ascii=False,allow_nan=False)
        self.assertNotIn('PRIVATE',text);self.assertNotIn('ROOM_PRIVATE',text)
        self.assertEqual(package['traffic_quality_observations'][0]['quality']['state'],'suspected_broadening')
        rec=next(r for r in e['recommendations'] if r['rule']=='traffic_quality_decline')
        out=validate_proposals({'analysis':'test','proposals':[{'recommendation_id':rec['id'],'change_pct':.05}]},e)
        self.assertEqual(out['accepted_for_simulation'],[])

    def test_simulation_does_not_mutate_config_or_inputs(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None;before=copy.deepcopy(cfg)
        rows,sim,when=quality_scenario(cfg);saved=copy.deepcopy(rows);e=evaluate_all(rows,sim,when)
        self.assertEqual(cfg,before);self.assertEqual(rows,saved)
        self.assertTrue(any(a['rule']=='traffic_quality_decline' for a in e['alerts']))
        self.assertFalse(any(r['eligible'] and r['action']=='budget_up' for r in e['recommendations']))

    def test_existing_budget_handover_is_held_on_current_quality_risk(self):
        from monitor.demo import migration_scenario
        rows,cfg,when,events,plans=migration_scenario(settings())
        row=report(when)
        result=evaluate_all(rows+[row],cfg,when,events=events,migrations=plans)
        review=result['budget_migrations'][0]
        self.assertEqual(review['state'],'held');self.assertIsNone(review['next_step'])
        self.assertTrue(any('流量质量' in b for b in review['blockers']))


if __name__=='__main__':unittest.main()
