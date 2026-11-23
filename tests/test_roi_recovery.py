import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package
from monitor.demo import roi_recovery_scenario
from monitor.evaluation import evaluate_all
from monitor.events import normalize_event
from monitor.roi_recovery import review_roi_recovery
from monitor.reporting import build_report,report_markdown
from test_engine import settings


def fixture():return roi_recovery_scenario(settings())
def review(rows,cfg,at,events):return review_roi_recovery(rows,cfg,at,events=events)
def item(result):return result['observations'][0]


def completed_fixture(minutes=70):
    rows,cfg,at,events=fixture();before=at;after=at+timedelta(minutes=10)
    event=copy.deepcopy(events[-1]);event.update(id='final-return',before_at=before.isoformat(),after_at=after.isoformat(),
        before={'target_roi':3.31},after={'target_roi':3.5})
    last=rows[-1]
    for n in range(1,minutes//10+1):
        row=copy.deepcopy(last);stamp=at+timedelta(minutes=n*10)
        row['id']='post-'+str(n);row['source'].update(captured_at=stamp.isoformat(),data_at=stamp.isoformat())
        row['metrics'].update(spend=last['metrics']['spend']+30*n,gmv=last['metrics']['gmv']+96*n,
            orders=last['metrics']['orders']+3*n,target_roi=3.5)
        row['context']['plan_roi_observed_at']=stamp.isoformat();row['control'].update(last_change_at=after.isoformat(),changes_today=3)
        rows.append(row)
    return rows,cfg,at+timedelta(minutes=minutes),events+[event]


class RoiRecoveryTests(unittest.TestCase):
    def test_linked_recovery_returns_to_original_setting_not_user_threshold(self):
        rows,cfg,at,events=fixture();result=review(rows,cfg,at,events);rec=result['recommendations'][0]
        self.assertEqual(item(result)['state'],'ready_return_step');self.assertEqual(len(item(result)['step_history']),2)
        self.assertEqual((rec['before'],rec['after']),(3.31,3.5));self.assertTrue(rec['eligible'])
        self.assertEqual(rec['evidence']['strategy_target_roi'],2.5);self.assertAlmostEqual(rec['change_pct'],.19/3.31)

    def test_initial_return_leaves_a_legal_final_step(self):
        rows,cfg,at,events=fixture();events=events[:1];root_at=events[0]['after_at']
        for row in rows:
            if row['source']['data_at']>=root_at:
                row['metrics']['target_roi']=3.15;row['control'].update(last_change_at=root_at,changes_today=1)
        result=review(rows,cfg,at,events);self.assertEqual(result['recommendations'][0]['after'],3.31)
        self.assertTrue(.05<=(3.5/3.31-1)<=.1)

    def test_direct_final_return_uses_original_goal_within_five_to_ten_percent(self):
        rows,cfg,at,events=fixture();events=events[:1];events[0]['after']['target_roi']=3.32
        for row in rows:
            if row['source']['data_at']>=events[0]['after_at']:
                row['metrics']['target_roi']=3.32;row['control'].update(last_change_at=events[0]['after_at'],changes_today=1)
        rec=review(rows,cfg,at,events)['recommendations'][0]
        self.assertEqual(rec['after'],3.5);self.assertAlmostEqual(rec['change_pct'],.18/3.32)

    def test_legal_reported_step_cannot_force_an_illegal_subfive_percent_tail(self):
        rows,cfg,at,events=fixture();events[-1]['after']['target_roi']=3.46
        for row in rows:
            if row['source']['data_at']>=events[-1]['after_at']:row['metrics']['target_roi']=3.46
        result=review(rows,cfg,at,events)
        self.assertEqual(item(result)['state'],'verify_return_path');self.assertEqual(result['recommendations'],[])

    def test_complete_needs_a_report_and_full_post_window(self):
        rows,cfg,at,events=completed_fixture();result=review(rows,cfg,at,events)
        self.assertEqual(item(result)['state'],'complete');self.assertFalse(item(result)['active'])
        self.assertEqual(result['recommendations'],[])
        rows,cfg,at,events=completed_fixture(40);result=review(rows,cfg,at,events)
        self.assertEqual(item(result)['state'],'observing_after_change');self.assertEqual(result['recommendations'],[])

    def test_later_changes_do_not_reopen_an_archived_complete_episode(self):
        rows,cfg,at,events=completed_fixture();old_end=at
        row=copy.deepcopy(rows[-1]);stamp=at+timedelta(minutes=10);row['id']='later-market'
        row['source'].update(captured_at=stamp.isoformat(),data_at=stamp.isoformat());row['context']['plan_roi_observed_at']=stamp.isoformat()
        row['metrics'].update(target_roi=3.7,spend=row['metrics']['spend']+30,gmv=row['metrics']['gmv']+30)
        row['control'].update(last_change_at=stamp.isoformat(),changes_today=4);rows.append(row)
        later=copy.deepcopy(events[-1]);later.pop('roi_recovery_root_id');later.update(id='unrelated-later',
            before_at=at.isoformat(),after_at=stamp.isoformat(),before={'target_roi':3.5},after={'target_roi':3.7});events.append(later)
        result=review(rows,cfg,stamp,events)
        self.assertEqual(item(result)['state'],'complete');self.assertFalse(item(result)['active'])
        self.assertEqual(item(result)['completed_at'],old_end.isoformat());self.assertEqual(result['recommendations'],[])

    def test_returned_original_setting_with_bad_effect_is_reviewed_without_more_return_steps(self):
        rows,cfg,at,events=completed_fixture()
        for row in rows:
            if row['source']['data_at']>=events[-1]['after_at']:
                row['metrics']['gmv']=rows[27]['metrics']['gmv']+(row['metrics']['spend']-rows[27]['metrics']['spend'])
        result=review(rows,cfg,at,events)
        self.assertEqual(item(result)['state'],'returned_needs_review');self.assertFalse(item(result)['active'])
        self.assertEqual(result['recommendations'],[])

    def test_a_candidate_never_advances_the_event_chain(self):
        args=fixture();first=review(*args);second=review(*args)
        self.assertEqual(first,second);self.assertEqual(len(item(first)['step_history']),2)
        self.assertFalse(item(first)['executed']);self.assertFalse(item(first)['platform_verified'])

    def test_missing_link_does_not_guess_a_root_from_old_events(self):
        rows,cfg,at,events=fixture();rows[-1]['context'].pop('roi_recovery_event_id')
        self.assertEqual(review(rows,cfg,at,events)['observations'],[])

    def test_missing_duplicate_or_wrong_root_stays_unverified(self):
        for change in ('missing','duplicate','wrong_action','foreign'):
            rows,cfg,at,events=fixture()
            if change=='missing':events=events[1:]
            elif change=='duplicate':events.append(copy.deepcopy(events[0]))
            elif change=='wrong_action':events[0]['action']='budget_down'
            else:events[0]['account_id']='999'
            self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_unknown_report_status_or_unlinked_change_does_not_count_as_applied(self):
        for change in ('unknown','unlink'):
            rows,cfg,at,events=fixture()
            if change=='unknown':events[-1]['status']='unknown'
            else:events[-1].pop('roi_recovery_root_id')
            self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_report_order_step_size_and_root_ceiling_are_enforced(self):
        for field,value in (('after',{'target_roi':3.6}),('after',{'target_roi':3.2}),
                            ('before',{'target_roi':3.14}),('before_at',None)):
            rows,cfg,at,events=fixture()
            events[-1][field]=(at-timedelta(hours=2)).isoformat() if value is None else value
            self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_other_same_plan_report_blocks_even_when_from_another_source(self):
        rows,cfg,at,events=fixture();other=copy.deepcopy(events[-1]);other.update(id='budget-change',action='budget_down',
            source_key='other-report',before={'budget':2000},after={'budget':1800});other.pop('roi_recovery_root_id')
        events.append(other);self.assertIn('其他同计划', ''.join(item(review(rows,cfg,at,events))['missing']))

    def test_conflicting_duplicate_id_cannot_hide_an_unlinked_report(self):
        rows,cfg,at,events=fixture();other=copy.deepcopy(events[-1]);other.pop('roi_recovery_root_id')
        other.update(action='budget_down',before={'budget':2000},after={'budget':1800})
        events.append(other);self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_unreported_budget_bid_running_or_goal_change_blocks(self):
        for field,value in (('budget',1900),('bid',100),('running',False),('target_roi',3.2)):
            rows,cfg,at,events=fixture();row=rows[-3]
            (row['control'] if field=='running' else row['metrics'])[field]=value
            self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_missing_update_reset_gap_and_wrong_source_kind_do_not_form_recovery(self):
        for change in ('missing_time','reset','gap','kind'):
            rows,cfg,at,events=fixture()
            if change=='missing_time':rows[-3]['source']['data_at']=None
            elif change=='reset':rows[-3]['metrics']['gmv']=0
            elif change=='gap':del rows[-5:-2]
            else:rows[-3]['source']['kind']='import'
            self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_zero_pre_spend_is_verified_without_a_relative_growth_rate(self):
        rows,cfg,at,events=fixture();result=evaluate_all(rows,cfg,at,events=events)
        self.assertEqual(result['outcome_review']['items'][0]['review_status'],'roi_recovery_review')
        self.assertIsNone(result['outcome_review']['items'][0]['comparison']['ad_spend_change_percent'])
        for row in rows[:13]:row['metrics']['spend']+=int(row['id'].rsplit('-',1)[1])
        self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_current_good_hour_cannot_hide_an_unqualified_applied_step(self):
        rows,cfg,at,events=fixture();boundary=events[-1]['before_at']
        for row in rows:
            stamp=row['source']['data_at']
            if events[0]['after_at']<stamp<=boundary:row['metrics']['gmv']=320+(row['metrics']['spend']-100)
        self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_no_qualified_run_or_roi_does_not_generate_return(self):
        rows,cfg,at,events=fixture()
        for row in rows:row['metrics']['gmv']=row['metrics']['spend']
        result=review(rows,cfg,at,events);self.assertEqual(item(result)['state'],'recovery_not_qualified')
        self.assertEqual(result['recommendations'],[])

    def test_user_target_missing_or_wrong_basis_never_borrows_original_goal(self):
        for change in ({'target_roi':None},{'target_roi':float('nan')},{'target_roi':True},{'roi_basis':'net'}):
            rows,cfg,at,events=fixture();cfg['accounts'][0].update(change)
            self.assertEqual(item(review(rows,cfg,at,events))['state'],'target_missing')

    def test_cost_basis_and_cost_floor_are_independent_of_roi(self):
        for change in ('basis','cost'):
            rows,cfg,at,events=fixture()
            if change=='basis':rows[-1]['context']['target_cost_basis']='marketing'
            else:rows[-1]['metrics']['target_cost']=5
            self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_stale_recovery_is_not_an_eligible_current_step(self):
        rows,cfg,at,events=fixture();result=review(rows,cfg,at+timedelta(minutes=11),events)
        self.assertFalse(item(result)['is_current']);self.assertFalse(result['recommendations'][0]['eligible'])

    def test_current_configuration_history_and_count_still_gate_the_candidate(self):
        for change in ({'history_known':False},{'guarantee_verified':False},{'changes_today':0},
                       {'changes_today':True},{'changes_today':float('nan')},{'last_change_at':None}):
            rows,cfg,at,events=fixture();rows[-1]['control'].update(change)
            result=review(rows,cfg,at,events)
            self.assertFalse(any(r['eligible'] for r in result['recommendations']))

    def test_base_ledger_does_not_borrow_total_or_switch_within_episode(self):
        rows,cfg,at,events=fixture()
        for row in rows:
            m=row['metrics']
            for key in ('spend','gmv','orders'):m['base_'+key]=m[key]
            m['spend']*=20;m['gmv']*=20
        self.assertEqual(item(review(rows,cfg,at,events))['current_window']['ledger'],'base')
        rows[-1]['metrics'].pop('base_gmv');self.assertEqual(review(rows,cfg,at,events)['recommendations'],[])

    def test_schema_links_only_explicit_up_reports_to_another_root(self):
        rows,cfg,at,events=fixture();self.assertEqual(normalize_event(events[-1],cfg,at)['roi_recovery_root_id'],'demo-roi-root')
        bad=copy.deepcopy(events[-1]);bad['action']='budget_up'
        with self.assertRaises(ValueError):normalize_event(bad,cfg,at)
        bad=copy.deepcopy(events[-1]);bad['roi_recovery_root_id']=bad['id']
        with self.assertRaises(ValueError):normalize_event(bad,cfg,at)

    def test_unified_pipeline_reserves_recovery_but_keeps_budget_stop_loss(self):
        rows,cfg,at,events=fixture();result=evaluate_all(rows,cfg,at,events=events)
        self.assertTrue(next(r for r in result['recommendations'] if r['rule']=='roi_recovery_step')['eligible'])
        account=copy.deepcopy(rows[-1]);account.update(id='risk',scope='account',entity_id='123')
        account['source']['series_key']='risk';account['context']={'account_blocked':True}
        result=evaluate_all(rows+[account],cfg,at,events=events)
        self.assertFalse(next(r for r in result['recommendations'] if r['rule']=='roi_recovery_step')['eligible'])
        rows,cfg,at,events=fixture()
        for row in rows:row['metrics']['gmv']=row['metrics']['spend']
        result=evaluate_all(rows,cfg,at,events=events)
        self.assertTrue(any(r['action']=='budget_down' and r['eligible'] for r in result['recommendations']))

    def test_active_budget_handover_reserves_the_plan_from_roi_return(self):
        from unittest.mock import patch
        rows,cfg,at,events=fixture()
        active={'state':'held','account_id':'123','source':{'entity_id':'demo-roi-recovery'},'target':{'entity_id':'another'}}
        with patch('monitor.evaluation.review_migration',return_value=active):
            result=evaluate_all(rows,cfg,at,events=events,migrations=[{}])
        rec=next(r for r in result['recommendations'] if r['rule']=='roi_recovery_step')
        self.assertFalse(rec['eligible']);self.assertIn('活动预算交接',''.join(rec['blockers']))

    def test_report_and_ai_anonymize_episode_ids_and_preserve_goal_meanings(self):
        rows,cfg,at,events=fixture();before=copy.deepcopy((rows,cfg,events));result=evaluate_all(rows,cfg,at,events=events)
        report=build_report(rows,cfg,result,'123',at)
        self.assertEqual(report['evidence_and_risks']['roi_recovery_reviews'][0]['next_goal'],3.5)
        self.assertIn('ROI下调后的恢复复核',report_markdown(report))
        text=json.dumps(make_package(rows,result));self.assertNotIn('demo-roi-root',text);self.assertNotIn('demo-roi-step-1',text)
        self.assertIn('roi_recovery_observations',text);self.assertEqual((rows,cfg,events),before)


if __name__=='__main__':unittest.main()
