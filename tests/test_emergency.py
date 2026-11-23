import copy
import json
import unittest
from datetime import timedelta

from monitor.emergency import analyze_emergency
from monitor.evaluation import evaluate_all
from monitor.schema import normalize_context
from test_engine import NOW,series,settings


def minute_rows():
    template=series()[-1];rows=[]
    for n in range(21):
        row=copy.deepcopy(template);at=NOW-timedelta(minutes=20-n)
        row.update(id='minute-'+str(n),entity_id='primary')
        row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat(),series_key='minute-primary')
        spend=100+min(n,10)*10
        row['metrics'].update(spend=spend,gmv=spend*3,orders=n,impressions=100+min(n,10)*100)
        row['context']={'is_primary_plan':True,'live_ongoing':True,'maintenance_active':False,
                        'low_latency_verified':True,'report_granularity':'minute'}
        rows.append(row)
    return rows


def risk(rows,cfg=None,now=NOW):return analyze_emergency(rows,cfg or settings(),now)
def alerts(result,rule):return [a for a in result['alerts'] if a['rule']==rule]


def declining_account(rois=(2.2,1.5,1)):
    rows=series(scope='account');gmv=0
    for n,row in enumerate(rows):
        if n:gmv+=100*rois[(n-1)//6]
        row['metrics'].update(gmv=gmv,net_gmv=gmv,roi=99)
    return rows


def score_rows():
    rows=series(scope='account')[-2:]
    for n,row in enumerate(rows):
        row['metrics'].update(account_score=80 if n==0 else 55,account_score_threshold=60,
                              reputation_score=4.7 if n==0 else 4.3,reputation_threshold=4.4)
        row['context']={'score_thresholds_verified':True}
    return rows


class EmergencyTests(unittest.TestCase):
    def test_active_minute_baseline_then_ten_minute_flat_spend_is_p0_review_only(self):
        result=risk(minute_rows());a=alerts(result,'primary_spend_stop')[0]
        self.assertEqual(a['priority'],'P0');self.assertEqual(a['evidence']['flat_minutes'],10)
        self.assertEqual(a['evidence']['baseline_spend'],100)
        self.assertFalse(a['evidence']['cause_verified'])
        self.assertEqual(result['recommendations'][0]['action'],'inspect_recovery')
        self.assertFalse(result['coverage']['execution_enabled']);json.dumps(result,allow_nan=False)

    def test_target_roi_is_not_required_for_investigating_zero_spend(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        self.assertTrue(alerts(risk(minute_rows(),cfg),'primary_spend_stop'))

    def test_recent_growth_is_observed_without_a_false_emergency(self):
        rows=minute_rows()
        for n,row in enumerate(rows):row['metrics'].update(spend=100+n*10,impressions=100+n*100)
        result=risk(rows);self.assertFalse(result['alerts'])
        self.assertEqual(result['coverage']['emergency']['primary_spend_stop']['state'],'observed')

    def test_no_prior_activity_or_single_zero_snapshot_never_proves_sudden_stop(self):
        for rows in ([minute_rows()[-1]],minute_rows()):
            for r in rows:r['metrics']['spend']=0
            self.assertFalse(alerts(risk(rows),'primary_spend_stop'))

    def test_required_state_and_latency_claims_cannot_be_inferred(self):
        for field,value in [('live_ongoing',False),('maintenance_active',True),('maintenance_active',None),
                            ('low_latency_verified',False),('report_granularity','hour')]:
            rows=minute_rows();rows[8]['context'][field]=value
            self.assertFalse(alerts(risk(rows),'primary_spend_stop'),field)
        rows=minute_rows();rows[-1]['control']['running']=None
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))

    def test_declaring_only_latest_state_does_not_certify_earlier_window(self):
        rows=minute_rows();rows[5]['context']={}
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))

    def test_delayed_import_recent_capture_and_old_data_do_not_look_realtime(self):
        rows=minute_rows()
        for r in rows:r['source']['captured_at']=NOW.isoformat()
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))
        self.assertFalse(alerts(risk(minute_rows(),now=NOW+timedelta(minutes=3)),'primary_spend_stop'))

    def test_missing_time_gap_conflict_and_reset_reject_p0(self):
        for mutate in (lambda rs:rs[14]['source'].update(data_at=None),
                       lambda rs:rs[14]['metrics'].update(spend=None),
                       lambda rs:rs[14]['metrics'].update(spend=0)):
            rows=minute_rows();mutate(rows)
            self.assertFalse(alerts(risk(rows),'primary_spend_stop'))
        rows=minute_rows();del rows[12:17]
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))
        rows=minute_rows();other=copy.deepcopy(rows[-1]);other['metrics']['spend']=199;rows.append(other)
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))

    def test_primary_role_and_source_boundaries_are_explicit(self):
        rows=minute_rows();rows[-1]['context'].pop('is_primary_plan')
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))

    def test_product_primary_uses_expected_delivery_instead_of_live_session(self):
        rows=minute_rows();cfg=settings();cfg['accounts'][0]['scene']='product'
        for row in rows:
            row['scene']='product';row['context'].update(live_ongoing=False,delivery_expected=True)
        self.assertTrue(alerts(risk(rows,cfg),'primary_spend_stop'))
        rows[-1]['context']['delivery_expected']=None
        self.assertFalse(alerts(risk(rows,cfg),'primary_spend_stop'))
        rows=minute_rows()
        for n,row in enumerate(rows):row['source']['series_key']='first' if n<11 else 'second'
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))

    def test_unknown_control_ledger_cannot_substitute_for_base(self):
        rows=minute_rows();rows[8]['metrics']['control_spend']=None
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))
        rows=minute_rows()
        for n,row in enumerate(rows):
            row['metrics'].update(base_spend=row['metrics']['spend'],base_impressions=row['metrics']['impressions'],
                                  spend=100+n*20,control_spend=20*n)
        result=risk(rows);self.assertTrue(alerts(result,'primary_spend_stop'))
        self.assertEqual(result['observations'][0]['primary_spend_stop']['ledger'],'base')
        rows[15]['metrics']['base_spend']=None
        self.assertFalse(alerts(risk(rows),'primary_spend_stop'))

    def test_incident_stays_open_while_flat_sampling_continues(self):
        rows=minute_rows();last=rows[-1]
        for n in range(1,21):
            row=copy.deepcopy(last);at=NOW+timedelta(minutes=n);row['id']='later-'+str(n)
            row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat());rows.append(row)
        result=risk(rows,now=NOW+timedelta(minutes=20))
        self.assertEqual(alerts(result,'primary_spend_stop')[0]['evidence']['flat_minutes'],30)

    def test_p0_vetoes_other_plan_growth_without_forcing_shutdown(self):
        result=evaluate_all(minute_rows()+series(roi=3),settings(),NOW)
        growth=[r for r in result['recommendations'] if r['action']=='budget_up']
        self.assertTrue(growth);self.assertFalse(any(r['eligible'] for r in growth))
        self.assertFalse(any(r['action']=='pause_plan' and r['eligible'] for r in result['recommendations']))

    def test_spend_stagnation_does_not_claim_impressions_stopped_when_they_continue(self):
        rows=minute_rows()
        for n,row in enumerate(rows):row['metrics']['impressions']=100+n*100
        self.assertEqual(alerts(risk(rows),'primary_spend_stop')[0]['evidence']['flow_observation'],'not_proven_flat')

    def test_unknown_or_non_day_period_and_cross_midnight_are_not_joined(self):
        for period in ('unknown','live_session'):
            rows=minute_rows()
            for row in rows:row['source'].update(metric_period=period,period_start=rows[0]['source']['data_at'])
            self.assertFalse(alerts(risk(rows),'primary_spend_stop'))
        rows=minute_rows();now=NOW.replace(day=22,hour=0,minute=10)
        for n,row in enumerate(rows):
            at=now-timedelta(minutes=20-n);row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat(),metric_date=at.date().isoformat())
            if n>=10:row['metrics']['spend']=0
        self.assertFalse(alerts(risk(rows,now=now),'primary_spend_stop'))

    def test_declining_three_hour_roi_and_low_latest_hour_trigger_separate_p1(self):
        result=risk(declining_account());a=alerts(result,'roi_sustained_decline')[0]
        self.assertEqual(a['priority'],'P1')
        for window,expected in zip(a['evidence']['hourly'],[2.2,1.5,1]):self.assertAlmostEqual(window['roi'],expected)

    def test_low_constant_recovery_missing_target_or_wrong_basis_is_not_sustained_decline(self):
        for rois in ((1,1,1),(1.2,1,1.4),(3,2.8,2.5)):
            self.assertFalse(alerts(risk(declining_account(rois)),'roi_sustained_decline'))
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        self.assertFalse(alerts(risk(declining_account(),cfg),'roi_sustained_decline'))
        self.assertFalse(alerts(risk(declining_account(),settings('net')),'roi_sustained_decline'))

    def test_roi_interior_missing_and_source_split_do_not_supply_three_complete_hours(self):
        rows=declining_account();rows[9]['metrics']['gmv']=None
        self.assertFalse(alerts(risk(rows),'roi_sustained_decline'))
        rows=declining_account()
        for n,row in enumerate(rows):row['source']['series_key']='a' if n<9 else 'b'
        self.assertFalse(alerts(risk(rows),'roi_sustained_decline'))

    def test_score_thresholds_are_user_verified_and_do_not_require_roi_target(self):
        rows=score_rows();cfg=settings();cfg['accounts'][0]['target_roi']=None
        result=risk(rows,cfg)
        self.assertEqual(len(alerts(result,'account_score_threshold')),1)
        self.assertEqual(len(alerts(result,'reputation_score_threshold')),1)
        self.assertEqual(result['observations'][0]['scores'][0]['change'],-25)
        self.assertFalse(result['observations'][0]['scores'][0]['platform_policy_verified'])

    def test_unknown_threshold_units_or_stale_score_do_not_raise_score_alarm(self):
        for mutate in (lambda rs:rs[-1]['context'].update(score_thresholds_verified=False),
                       lambda rs:rs[-1]['metrics'].update(account_score_threshold=None,reputation_threshold=None),
                       lambda rs:rs[-1]['source'].update(data_at=None)):
            rows=score_rows();mutate(rows)
            self.assertFalse([a for a in risk(rows)['alerts'] if a['rule'].endswith('_threshold')])
        self.assertFalse([a for a in risk(score_rows(),now=NOW+timedelta(hours=1))['alerts'] if a['rule'].endswith('_threshold')])

    def test_score_zero_is_observed_and_conflict_does_not_choose_arbitrary_value(self):
        rows=score_rows();rows[-1]['metrics'].update(account_score=0,account_score_threshold=0)
        self.assertEqual(alerts(risk(rows),'account_score_threshold')[0]['evidence']['value'],0)
        other=copy.deepcopy(rows[-1]);other['metrics']['account_score']=90;rows.append(other)
        self.assertFalse(alerts(risk(rows),'account_score_threshold'))

    def test_scores_can_compare_known_levels_across_midnight_without_subtracting_spend(self):
        rows=score_rows();now=NOW.replace(day=22,hour=0,minute=5)
        for n,row in enumerate(rows):
            at=now-timedelta(minutes=10*(1-n));row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat(),metric_date=at.date().isoformat())
        latest=[o for o in risk(rows,now=now)['observations'] if o['is_current'] and o['data_at']==now.isoformat()][0]
        self.assertEqual(latest['scores'][0]['change'],-25)

    def test_prior_score_conflict_keeps_level_alert_without_claiming_decline(self):
        rows=score_rows();other=copy.deepcopy(rows[0]);other['metrics']['account_score']=50;rows.insert(0,other)
        a=alerts(risk(rows),'account_score_threshold')[0]
        self.assertIsNone(a['evidence']['change']);self.assertNotIn('下降',a['title'])

    def test_core_creative_priority_requires_explicit_role(self):
        rows=series(scope='creative',ctr_steps=[4,3.5,3]);cfg=settings()
        result=evaluate_all(rows,cfg,NOW)
        self.assertEqual(alerts(result,'creative_ctr_decay_3h')[0]['priority'],'P2')
        rows[-1]['context']={'is_core_creative':True};result=evaluate_all(rows,cfg,NOW)
        self.assertEqual(alerts(result,'creative_ctr_decay_3h')[0]['priority'],'P1')
        self.assertEqual(next(r for r in result['recommendations'] if r['rule']=='creative_ctr_decay_3h')['priority'],'P1')

    def test_browser_cannot_certify_latency_nonmaintenance_or_threshold_units(self):
        result=normalize_context({'low_latency_verified':True,'maintenance_active':False,'score_thresholds_verified':True,'report_granularity':'minute'},'browser_dom')
        self.assertIsNone(result['low_latency_verified']);self.assertIsNone(result['maintenance_active']);self.assertIsNone(result['score_thresholds_verified'])
        with self.assertRaises(ValueError):normalize_context({'report_granularity':'guessed'},'import')

    def test_malformed_inputs_are_counted_without_generating_alerts(self):
        result=risk([None,{}, {'account_id':[]}])
        self.assertEqual(result['coverage']['rejected_count'],3);self.assertFalse(result['alerts'])

    def test_new_simulation_is_synthetic_with_review_and_growth_veto(self):
        from monitor.demo import emergency_scenario
        rows,cfg,now=emergency_scenario(settings());result=evaluate_all(rows,cfg,now)
        self.assertTrue(alerts(result,'primary_spend_stop'))
        self.assertTrue(alerts(result,'account_score_threshold'))
        self.assertFalse(any(r['eligible'] and r['action']=='budget_up' for r in result['recommendations']))
        self.assertTrue(all(r['source']['kind']=='demo' for r in rows))

    def test_emergency_ai_package_anonymizes_ids_and_does_not_export_context_text(self):
        from monitor.ai import make_package
        rows=minute_rows();rows[-1]['context']['raw_note']='PRIVATE_NOTE'
        result=evaluate_all(rows,settings(),NOW);package=make_package(rows,result)
        text=json.dumps(package['emergency_observations'])
        self.assertNotIn('minute-primary',text);self.assertNotIn('PRIVATE_NOTE',text)
        self.assertTrue(package['emergency_observations'][0]['primary_spend_stop']['triggered'])

    def test_impressions_stop_can_alert_while_cost_billing_continues(self):
        rows=minute_rows()
        for n,row in enumerate(rows):row['metrics']['spend']=100+n*10
        result=risk(rows)
        self.assertFalse(alerts(result,'primary_spend_stop'));self.assertTrue(alerts(result,'primary_flow_stop'))
        self.assertEqual(result['recommendations'][0]['rule'],'primary_flow_stop')
        self.assertEqual(len(result['recommendations']),1)

    def test_flow_needs_large_enough_explicit_base_and_never_borrows_total(self):
        rows=minute_rows()
        for n,row in enumerate(rows):
            row['metrics'].update(base_impressions=row['metrics']['impressions'],impressions=100+n*500,control_spend=n*5)
        self.assertTrue(alerts(risk(rows),'primary_flow_stop'))
        rows[15]['metrics']['base_impressions']=None
        self.assertFalse(alerts(risk(rows),'primary_flow_stop'))
        rows=minute_rows()
        for row in rows:row['metrics']['impressions']/=10
        self.assertFalse(alerts(risk(rows),'primary_flow_stop'))

    def test_two_p0_dimensions_still_create_only_one_investigation(self):
        result=risk(minute_rows())
        self.assertTrue(alerts(result,'primary_spend_stop'));self.assertTrue(alerts(result,'primary_flow_stop'))
        self.assertEqual(len(result['recommendations']),1)

    def test_float_roundoff_does_not_create_sustained_roi_decline(self):
        self.assertFalse(alerts(risk(declining_account((1.4+2e-12,1.4+1e-12,1.4))),'roi_sustained_decline'))


if __name__=='__main__':unittest.main()
