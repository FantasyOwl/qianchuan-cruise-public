import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package,validate_proposals
from monitor.demo import maintenance_scenario
from monitor.events import normalize_event
from monitor.evaluation import evaluate_all
from monitor.maintenance import review_maintenance
from monitor.reporting import build_report,report_markdown
from monitor.schema import normalize_snapshot,parse_time
from monitor.strategies import extend_evaluation
from test_engine import NOW,settings
from test_strategies import ready_rows


def fixture():
    rows,cfg,at,events=maintenance_scenario(settings());delta=NOW-at
    for row in rows:
        for field in ('captured_at','data_at'):
            row['source'][field]=(parse_time(row['source'][field])+delta).isoformat()
        row['source']['metric_date']=row['source']['data_at'][:10]
        for field in ('creative_inventory_at','decline_started_at','bid_maintenance_review_at'):
            if row['context'].get(field):row['context'][field]=(parse_time(row['context'][field])+delta).isoformat()
        if row['control'].get('last_change_at'):row['control']['last_change_at']=(parse_time(row['control']['last_change_at'])+delta).isoformat()
    for event in events:
        for field in ('before_at','after_at'):event[field]=(parse_time(event[field])+delta).isoformat()
    return rows,cfg,events


def review(rows,cfg,events,now=NOW):
    return review_maintenance(rows,cfg,now,events=events)


def item(rows,cfg,events,now=NOW):return review(rows,cfg,events,now)['observations'][0]


class MaintenanceTests(unittest.TestCase):
    def test_complete_sequence_uses_inventory_report_and_two_hours_before_bid_review(self):
        rows,cfg,events=fixture();out=item(rows,cfg,events)
        self.assertEqual(out['state'],'handover_ready',out);self.assertTrue(out['refresh_verified_locally'])
        self.assertEqual(out['added_material_count'],1);self.assertEqual(len(out['post_refresh_windows']),2)
        self.assertTrue(all(w['roi']==1.1 for w in out['post_refresh_windows']))
        self.assertFalse(out['platform_verified']);self.assertFalse(out['executed']);self.assertFalse(out['execution_eligible'])

    def test_initial_decline_requests_materials_before_any_other_maintenance(self):
        rows,cfg,events=fixture()
        for row in rows:row['context'].pop('creative_refresh_event_id',None)
        result=review(rows,cfg,[])
        self.assertEqual(result['observations'][0]['state'],'refresh_required')
        self.assertEqual([r['action'] for r in result['recommendations']],['refresh_creatives'])
        self.assertFalse(result['recommendations'][0]['eligible'])

    def test_report_without_explicit_pointer_does_not_request_duplicate_refresh(self):
        rows,cfg,events=fixture();rows[-1]['context'].pop('creative_refresh_event_id')
        result=review(rows,cfg,events)
        self.assertEqual(result['observations'][0]['state'],'verify_refresh_report');self.assertEqual(result['recommendations'],[])

    def test_wait_for_full_observation_after_verified_material_change(self):
        rows,cfg,events=fixture();end=parse_time(events[0]['after_at'])+timedelta(minutes=30)
        rows=[r for r in rows if parse_time(r['source']['data_at'])<=end]
        out=item(rows,cfg,events,end)
        self.assertEqual(out['state'],'observing_refresh');self.assertTrue(out['refresh_verified_locally'])

    def test_failure_observation_requires_bid_review_after_window_and_does_not_claim_bid_changed(self):
        for mutate in (lambda c:c.update(bid_maintenance_reviewed=False),lambda c:c.pop('bid_maintenance_review_at'),
                       lambda c:c.update(bid_maintenance_review_at=(NOW-timedelta(hours=3)).isoformat()),
                       lambda c:c.update(bid_maintenance_review_at=(NOW+timedelta(minutes=10)).isoformat())):
            rows,cfg,events=fixture();mutate(rows[-1]['context'])
            out=item(rows,cfg,events)
            self.assertEqual(out['state'],'bid_review_required');self.assertFalse(out['execution_eligible'])

    def test_old_failure_flag_without_report_or_actual_inventory_cannot_qualify(self):
        rows,cfg,events=fixture()
        out=item(rows,cfg,[]);self.assertEqual(out['state'],'verify_refresh_report')
        rows[-1]['context'].pop('creative_ids')
        self.assertEqual(item(rows,cfg,events)['state'],'verify_decline')

    def test_refresh_before_new_episode_is_not_reused(self):
        rows,cfg,events=fixture();rows[-1]['context']['decline_started_at']=events[0]['after_at']
        out=item(rows,cfg,events)
        self.assertEqual(out['state'],'verify_refresh_report');self.assertTrue(out['missing'])

    def test_missing_or_cross_day_decline_start_is_not_inferred(self):
        for value in (None,(NOW-timedelta(days=1)).isoformat(),(NOW+timedelta(minutes=1)).isoformat()):
            rows,cfg,events=fixture();rows[-1]['context']['decline_started_at']=value
            self.assertEqual(item(rows,cfg,events)['state'],'verify_decline')

    def test_role_history_running_and_source_coverage_are_required(self):
        for field,value in (('is_primary_plan',None),('creative_inventory_verified',False),('creative_inventory_at',None)):
            rows,cfg,events=fixture();rows[-1]['context'][field]=value
            self.assertEqual(item(rows,cfg,events)['state'],'verify_decline')
        for field,value in (('running',False),('history_known',False),('last_change_at',(NOW+timedelta(minutes=1)).isoformat())):
            rows,cfg,events=fixture();rows[-1]['control'][field]=value
            self.assertEqual(item(rows,cfg,events)['state'],'verify_decline')
        rows,cfg,events=fixture();rows[-1]['source']['coverage']='visible_only'
        self.assertEqual(item(rows,cfg,events)['state'],'verify_decline')

    def test_primary_role_is_preserved_and_never_inferred_from_budget(self):
        rows,cfg,events=fixture()
        for row in rows:row['context']['is_primary_plan']=True
        out=item(rows,cfg,events);self.assertTrue(out['is_primary_plan']);self.assertEqual(out['state'],'handover_ready')
        self.assertFalse(any(r['action']=='pause_plan' for r in review(rows,cfg,events)['recommendations']))

    def test_stale_source_is_displayed_without_advancing(self):
        rows,cfg,events=fixture();out=item(rows,cfg,events,NOW+timedelta(hours=1))
        self.assertFalse(out['is_current']);self.assertEqual(out['state'],'verify_decline')

    def test_only_declared_declining_plan_enters_workflow(self):
        rows,cfg,events=fixture()
        for row in rows:row['control']['lifecycle']='mature'
        self.assertEqual(review(rows,cfg,events)['observations'],[])

    def test_wrong_report_identity_basis_or_authority_cannot_advance(self):
        for change in ({'account_id':'456'},{'entity_id':'wrong'},{'source_key':'other'},{'metric_basis':'net'},
                       {'source':'manual_verified_report','status':'reported_applied'},{'status':'unknown'}):
            rows,cfg,events=fixture();events[0].update(change)
            self.assertEqual(item(rows,cfg,events)['state'],'verify_refresh_report')

    def test_duplicate_reports_and_same_time_inventory_conflicts_are_held(self):
        rows,cfg,events=fixture();self.assertEqual(item(rows,cfg,events*2)['state'],'verify_refresh_report')
        after=events[0]['after_at'];duplicate=copy.deepcopy(next(r for r in rows if r['source']['data_at']==after))
        duplicate['id']='conflict';duplicate['context']['creative_ids']=['other-material'];rows.append(duplicate)
        self.assertTrue(item(rows,cfg,events)['missing'])

    def test_missing_boundary_or_unverified_inventory_blocks_report_crosscheck(self):
        for mutate in (lambda rs:rs.pop(0),lambda rs:rs[1]['context'].update(creative_inventory_verified=False),
                       lambda rs:rs[0]['context'].update(creative_ids=['unexpected'])):
            rows,cfg,events=fixture();mutate(rows)
            self.assertEqual(item(rows,cfg,events)['state'],'verify_refresh_report')

    def test_changed_inventory_or_budget_bid_target_or_status_during_window_is_confounded(self):
        for mutate in (lambda r:r['context'].update(creative_ids=['old-material','new-material','another']),
                       lambda r:r['metrics'].update(budget=1),lambda r:r['metrics'].update(bid=10),
                       lambda r:r['metrics'].update(target_roi=3),lambda r:r['control'].update(running=False)):
            rows,cfg,events=fixture();mutate(rows[5])
            out=item(rows,cfg,events);self.assertEqual(out['state'],'verify_refresh_report');self.assertTrue(out['missing'])

    def test_other_unknown_change_report_inside_observation_blocks_attribution(self):
        rows,cfg,events=fixture();other=copy.deepcopy(events[0]);other.update(id='uncertain-bid',action='bid_down',status='unknown')
        other['after_at']=rows[5]['source']['data_at'];events.append(other)
        self.assertTrue(item(rows,cfg,events)['missing'])

    def test_simultaneous_change_at_refresh_boundary_is_not_hidden(self):
        for mutate in (lambda r:r['metrics'].update(budget=1),lambda r:r['metrics'].update(bid=50),
                       lambda r:r['control'].update(running=False)):
            rows,cfg,events=fixture();mutate(rows[0])
            self.assertNotEqual(item(rows,cfg,events)['state'],'handover_ready')
        rows,cfg,events=fixture();events[0]['after']['budget']=1000
        with self.assertRaises(ValueError):normalize_event(events[0],cfg,NOW)

    def test_gap_missing_amount_reset_and_low_sample_do_not_claim_failure(self):
        for mutate in (lambda rs:rs[5]['metrics'].update(gmv=None),lambda rs:rs[5]['metrics'].update(gmv=0),
                       lambda rs:rs.__delitem__(slice(3,7))):
            rows,cfg,events=fixture();mutate(rows)
            out=item(rows,cfg,events);self.assertNotEqual(out['state'],'handover_ready');self.assertTrue(out['missing'])

    def test_target_empty_unknown_or_mismatched_never_uses_platform_goal(self):
        for target,basis in ((None,'overall_payment'),(2.5,'net'),(float('nan'),'overall_payment')):
            rows,cfg,events=fixture();cfg['accounts'][0].update(target_roi=target,roi_basis=basis)
            rows[-1]['metrics']['target_roi']=3
            self.assertEqual(item(rows,cfg,events)['state'],'target_missing')

    def test_recovered_sample_or_borderline_result_stops_transfer(self):
        for roi,state in ((3,'recovered_observation'),(2.3,'inconclusive')):
            rows,cfg,events=fixture()
            for row in rows:row['metrics']['gmv']=row['metrics']['spend']*roi
            out=item(rows,cfg,events);self.assertEqual(out['state'],state)
            self.assertFalse(review(rows,cfg,events)['recommendations'])

    def test_base_ledger_does_not_borrow_overall_amounts(self):
        rows,cfg,events=fixture()
        for row in rows:
            m=row['metrics'];m.update(base_spend=m['spend'],base_gmv=m['spend']*1.1,base_orders=m['orders'],gmv=m['spend']*10)
        self.assertEqual(item(rows,cfg,events)['state'],'handover_ready')
        rows[5]['metrics'].pop('base_gmv')
        self.assertNotEqual(item(rows,cfg,events)['state'],'handover_ready')

    def test_material_event_requires_addition_without_replacing_old_set(self):
        rows,cfg,events=fixture();one=events[0]
        for before,after in ((['old'],['old']),(['old'],['new']),(['old'],['old','old']),(['old'],[1]),(['old'],None)):
            event=copy.deepcopy(one);event.update(before={'material_ids':before},after={'material_ids':after})
            with self.assertRaises(ValueError):normalize_event(event,cfg,NOW)
        event=copy.deepcopy(one);event.update(scope='creative')
        with self.assertRaises(ValueError):normalize_event(event,cfg,NOW)

    def test_ingestion_preserves_typed_inventory_but_browser_cannot_certify(self):
        rows,cfg,events=fixture();row=rows[-1]
        clean=normalize_snapshot(row,cfg,NOW)
        self.assertEqual(clean['context']['creative_ids'],['old-material','new-material'])
        row['source'].update(kind='browser_dom',url='https://qianchuan.jinritemai.com/uni-prom?aavid=123')
        self.assertIsNone(normalize_snapshot(row,cfg,NOW)['context']['creative_inventory_verified'])
        row['context']['creative_ids']=['repeat','repeat']
        with self.assertRaises(ValueError):normalize_snapshot(row,cfg,NOW)

    def test_four_hours_zero_orders_but_good_revenue_cannot_pause(self):
        rows=ready_rows(roi=3)
        for row in rows:row['metrics']['orders']=0
        out=extend_evaluation(rows,settings(),{},NOW)
        self.assertFalse(any(r['action']=='pause_plan' for r in out['recommendations']))

    def test_four_hour_pause_requires_each_hour_severe_and_explicit_nonprimary(self):
        for role in (True,None):
            rows=ready_rows(roi=0)
            for row in rows:row['metrics']['orders']=0;row['context']['is_primary_plan']=role
            rec=next(r for r in extend_evaluation(rows,settings(),{},NOW)['recommendations'] if r['action']=='pause_plan')
            self.assertFalse(rec['eligible']);self.assertTrue(any('主力' in b for b in rec['blockers']))
        rows=ready_rows(roi=1.6)
        for row in rows:row['metrics']['orders']=0
        self.assertFalse(any(r['action']=='pause_plan' for r in extend_evaluation(rows,settings(),{},NOW)['recommendations']))

    def test_other_source_primary_role_cannot_be_bypassed_by_nonprimary_source(self):
        rows=ready_rows(roi=0)
        for row in rows:row['metrics']['orders']=0
        other=copy.deepcopy(rows)
        for row in rows:row['entity_id']='nonprimary-evidence-alias'
        for row in other:
            row['id']='other-'+row['id'];row['source']['series_key']='other-primary-report';row['context']['is_primary_plan']=True
        out=evaluate_all(rows+other,settings(),NOW)
        recs=[r for r in out['recommendations'] if r['action']=='pause_plan']
        self.assertTrue(recs);self.assertFalse(any(r['eligible'] for r in recs))
        self.assertTrue(all(any('主力' in b for b in r['blockers']) for r in recs))

    def test_report_and_ai_keep_sequence_without_material_ids_or_raw_notes(self):
        rows,cfg,events=fixture();out=evaluate_all(rows,cfg,NOW,events=events)
        report=build_report(rows,cfg,out,'123',NOW)
        self.assertEqual(report['evidence_and_risks']['maintenance_reviews'][0]['state'],'handover_ready')
        self.assertIn('衰退维护顺序复核',report_markdown(report))
        events[0]['note']='PRIVATE_NOTE';pack=make_package(rows,out)
        text=json.dumps(pack,allow_nan=False)
        self.assertNotIn('old-material',text);self.assertNotIn('new-material',text);self.assertNotIn('PRIVATE_NOTE',text)
        self.assertNotIn(events[0]['id'],text);self.assertEqual(pack['maintenance_observations'][0]['state'],'handover_ready')
        candidate=next(r for r in out['recommendations'] if r['rule']=='decline_maintenance')
        verdict=validate_proposals({'analysis':'test','proposals':[{'recommendation_id':candidate['id'],'change_pct':.1}]},out)
        self.assertEqual(verdict['accepted_for_simulation'],[])

    def test_inputs_config_and_reports_remain_unchanged(self):
        rows,cfg,events=fixture();before=copy.deepcopy((rows,cfg,events));out=review(rows,cfg,events)
        self.assertEqual((rows,cfg,events),before);json.dumps(out,allow_nan=False)

    def test_malformed_inputs_are_skipped_without_fabricating_maintenance(self):
        for rows in (None,{},[None,{}, {'scope':[]} ]):
            result=review_maintenance(rows,settings(),NOW,events=[])
            self.assertEqual(result['observations'],[]);json.dumps(result,allow_nan=False)
        self.assertEqual(evaluate_all(None,settings(),NOW)['maintenance_review'],[])


if __name__=='__main__':unittest.main()
