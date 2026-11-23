import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package
from monitor.evaluation import evaluate_all
from monitor.reporting import build_report,report_markdown
from monitor.schema import normalize_snapshot
from test_engine import NOW,settings
from test_strategies import ready_rows,run


def plan(identifier,product='universal',account='123',source=None,enabled=True,delivery='unknown',age_seconds=0):
    row=copy.deepcopy(ready_rows()[-1]);stamp=NOW-timedelta(seconds=age_seconds)
    row.update(id=account+'-'+identifier+'-'+str(source),account_id=account,entity_id=identifier,promotion_type=product)
    row['source'].update(series_key=source or identifier,captured_at=stamp.isoformat(),data_at=stamp.isoformat())
    row['control']['running']=enabled
    row['context'].update(plan_id=identifier,aweme_id='shared-aweme',plan_marketing_goal='LIVE_PROM_GOODS',
        plan_status_at=stamp.isoformat(),plan_delivery_state=delivery)
    return row


def analyze(rows,cfg=None):return evaluate_all(rows,cfg or settings(),NOW)
def rules(result):return {a['rule'] for a in result['alerts']}
def mixed(result):return [r for r in result['recommendations'] if r['rule']=='live_universal_standard_conflict']


class LiveExclusionTests(unittest.TestCase):
    def test_universal_and_standard_switch_overlap_is_a_review_not_automatic_pause(self):
        result=analyze([plan('u'),plan('s','standard')])
        self.assertIn('live_universal_standard_conflict',rules(result));rec=mixed(result)[0]
        self.assertEqual(rec['action'],'review_live_exclusion');self.assertFalse(rec['eligible']);self.assertIsNone(rec['after'])

    def test_declared_delivery_overlap_can_name_a_pause_candidate_but_never_authorize_it(self):
        result=analyze([plan('u',delivery='delivering'),plan('s','standard',delivery='delivering')]);rec=mixed(result)[0]
        self.assertEqual(rec['action'],'pause_plan');self.assertEqual((rec['before'],rec['after']),(True,False))
        self.assertFalse(rec['eligible']);self.assertEqual(rec['target_id'],'s')

    def test_system_paused_standard_is_not_paused_again_or_resumed(self):
        result=analyze([plan('u'),plan('s','standard',delivery='system_paused')])
        self.assertNotIn('live_universal_standard_conflict',rules(result));self.assertFalse(mixed(result))
        state=next(o for o in result['live_exclusion_reviews'] if o['plan_id']=='s')
        self.assertEqual(state['overlap'],'system_pause_observed')
        result=analyze([plan('u',enabled=False),plan('s','standard',delivery='system_paused')])
        self.assertFalse(any(r['action']=='resume_plan' for r in result['recommendations']))
        rows=ready_rows(roi=3)
        for row in rows:row['context'].update(aweme_id='shared-aweme',plan_marketing_goal='LIVE_PROM_GOODS',plan_status_at=row['source']['captured_at'])
        result=analyze(rows+[plan('s','standard',delivery='system_paused')])
        self.assertTrue(any(r['action']=='budget_up' and r['target_id']=='plan1' and r['eligible'] for r in result['recommendations']))

    def test_coincident_missing_metadata_is_not_an_opposite_status(self):
        full=plan('s','standard',source='full',delivery='system_paused')
        partial=plan('s','standard',source='partial');partial['context'].pop('plan_marketing_goal')
        result=analyze([plan('u'),full,partial])
        self.assertNotIn('live_plan_status_conflict',rules(result));self.assertNotIn('live_universal_standard_conflict',rules(result))
        state=next(o for o in result['live_exclusion_reviews'] if o['plan_id']=='s')
        self.assertEqual(state['state'],'system_paused')

    def test_not_delivering_standard_does_not_prove_actual_overlap(self):
        result=analyze([plan('u'),plan('s','standard',delivery='not_delivering')])
        self.assertNotIn('live_universal_standard_conflict',rules(result))

    def test_missing_aweme_or_goal_does_not_guess_from_account_or_plan_name(self):
        for key in ('aweme_id','plan_marketing_goal'):
            first=plan('u');first['context'].pop(key)
            self.assertNotIn('live_universal_standard_conflict',rules(analyze([first,plan('s','standard')])))
        first=plan('u');first['context']['plan_marketing_goal']={}
        self.assertNotIn('live_universal_standard_conflict',rules(analyze([first,plan('s','standard')])))

    def test_product_chengfang_search_and_control_tasks_are_not_swept_into_live_rule(self):
        for change in ('product','chengfang','search','control'):
            other=plan('s','standard')
            if change=='product':other['scene']='product';other['context']['plan_marketing_goal']='VIDEO_PROM_GOODS'
            elif change=='control':other['scope']='control'
            else:other['promotion_type']=change
            self.assertNotIn('live_universal_standard_conflict',rules(analyze([plan('u'),other])))

    def test_disabled_or_explicit_unknown_switch_is_not_counted_as_enabled(self):
        for enabled in (False,None):
            self.assertNotIn('live_universal_standard_conflict',rules(analyze([plan('u'),plan('s','standard',enabled=enabled)])))

    def test_newer_off_source_suppresses_older_on_alias_instead_of_cherry_picking(self):
        old=plan('u',source='A',age_seconds=60);new=plan('u',source='B',enabled=False)
        new['entity_id']='alias'
        result=analyze([old,new,plan('u2')])
        self.assertNotIn('aweme_universal_conflict',rules(result))
        self.assertFalse(next(o for o in result['live_exclusion_reviews'] if o['plan_id']=='u')['enabled'])

    def test_same_time_status_conflict_is_reported_without_counting_two_plans(self):
        first=plan('u',source='A');other=plan('u',source='B',enabled=False)
        result=analyze([first,other,plan('u2')])
        self.assertIn('live_plan_status_conflict',rules(result));self.assertNotIn('aweme_universal_conflict',rules(result))
        alert=next(a for a in result['alerts'] if a['rule']=='live_plan_status_conflict')
        self.assertEqual({s['enabled'] for s in alert['evidence']['conflicting_sources']},{True,False})

    def test_aliases_of_one_enabled_plan_are_not_two_universal_plans(self):
        first=plan('u',source='A');other=plan('u',source='B');other['entity_id']='alias'
        self.assertNotIn('aweme_universal_conflict',rules(analyze([first,other])))

    def test_declared_delivering_with_disabled_switch_is_an_input_conflict(self):
        result=analyze([plan('u',enabled=False,delivery='delivering')])
        self.assertIn('live_plan_status_conflict',rules(result))

    def test_status_pair_alignment_uses_local_two_minute_window(self):
        for seconds,conflict in ((120,True),(121,False)):
            result=analyze([plan('u'),plan('s','standard',age_seconds=seconds)])
            self.assertEqual('live_universal_standard_conflict' in rules(result),conflict)

    def test_roi_target_missing_does_not_suppress_a_status_warning(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        self.assertIn('live_universal_standard_conflict',rules(analyze([plan('u'),plan('s','standard')],cfg)))

    def test_explicit_status_clock_does_not_replace_missing_or_historical_performance_time(self):
        rows=[plan('u'),plan('s','standard')]
        for row in rows:row['source']['data_at']=None;row['metric_basis']='unknown'
        result=analyze(rows);self.assertIn('live_universal_standard_conflict',rules(result))
        self.assertTrue(all(o['performance_data_at'] is None for o in result['live_exclusion_reviews']))
        self.assertTrue(all(o['is_current'] for o in result['live_exclusion_reviews']))
        self.assertTrue(all(row['source']['data_at'] is None for row in rows))

    def test_missing_status_clock_requires_fresh_legacy_snapshot(self):
        first=plan('u');first['context'].pop('plan_status_at');first['source']['data_at']=None
        self.assertNotIn('live_universal_standard_conflict',rules(analyze([first,plan('s','standard')])))

    def test_old_or_misaligned_status_clock_cannot_borrow_new_capture(self):
        first=plan('u');first['context']['plan_status_at']=(NOW-timedelta(minutes=2)).isoformat()
        self.assertNotIn('live_universal_standard_conflict',rules(analyze([first,plan('s','standard')])))

    def test_multiple_universal_detection_remains_supported(self):
        result=analyze([plan('u'),plan('u2')]);self.assertIn('aweme_universal_conflict',rules(result))
        self.assertNotIn('live_universal_standard_conflict',rules(result))

    def test_cross_account_growth_is_held_and_report_redacts_foreign_ids_from_actions_too(self):
        rows=ready_rows(roi=3)
        for row in rows:row['context'].update(aweme_id='shared-aweme',plan_marketing_goal='LIVE_PROM_GOODS',plan_status_at=row['source']['captured_at'])
        other=plan('FOREIGN_STANDARD','standard',account='456');cfg=settings()
        cfg['accounts'].append({'id':'456','scene':'live','target_roi':2,'roi_basis':'overall_payment'})
        result=analyze(rows+[other],cfg)
        self.assertFalse(any(r['action']=='budget_up' and r['eligible'] for r in result['recommendations']))
        own=build_report(rows+[other],cfg,result,'123',NOW);foreign=build_report(rows+[other],cfg,result,'456',NOW)
        self.assertNotIn('FOREIGN_STANDARD',json.dumps(own));self.assertNotIn('plan1',json.dumps(foreign))
        self.assertTrue(any(a['rule']=='live_universal_standard_conflict' for a in foreign['evidence_and_risks']['alerts']))
        self.assertIn('直播全域与标准互斥复核',report_markdown(own))

    def test_system_pause_blocks_own_growth_even_if_old_performance_was_good(self):
        rows=ready_rows(roi=3)
        for row in rows:
            row['promotion_type']='standard';row['context'].update(aweme_id='shared-aweme',plan_marketing_goal='LIVE_PROM_GOODS',
                plan_status_at=row['source']['captured_at'],plan_delivery_state='system_paused')
        result=analyze(rows)
        self.assertFalse(any(r['action']=='budget_up' and r['eligible'] for r in result['recommendations']))

    def test_unrelated_accounts_aweme_ids_and_unallowed_accounts_do_not_join(self):
        first=plan('u');second=plan('s','standard');second['context']['aweme_id']='different'
        self.assertNotIn('live_universal_standard_conflict',rules(analyze([first,second])))
        self.assertNotIn('live_universal_standard_conflict',rules(analyze([first,plan('s','standard',account='999')])))

    def test_scope_does_not_claim_clearance_when_only_one_visible_plan_is_seen(self):
        result=analyze([plan('u')]);self.assertFalse(mixed(result))
        self.assertIn('没有冲突也不认证',result['live_exclusion_reviews'][0]['note'])

    def test_schema_preserves_explicit_status_context_and_rejects_bad_enums(self):
        raw=plan('u');raw['source']['dataset_id']='status-test'
        self.assertEqual(normalize_snapshot(raw,settings(),NOW)['context']['plan_marketing_goal'],'LIVE_PROM_GOODS')
        for field,value in (('plan_delivery_state','paused?'),('plan_marketing_goal','直播'),('plan_status_at',True)):
            bad=copy.deepcopy(raw);bad['context'][field]=value
            with self.assertRaises(ValueError):normalize_snapshot(bad,settings(),NOW)

    def test_official_reference_scope_and_anonymous_ai_keep_status_separate(self):
        rows=[plan('u'),plan('s','standard')];before=copy.deepcopy(rows);result=analyze(rows)
        alert=next(a for a in result['alerts'] if a['rule']=='live_universal_standard_conflict')
        refs=alert['policy_basis']['official_references'];self.assertTrue(any(r['id']=='live_exclusion' and r['checked_at']=='2026-10-22' for r in refs))
        package=json.dumps(make_package(rows,result));self.assertNotIn('shared-aweme',package);self.assertIn('live_exclusion_observations',package)
        self.assertEqual(rows,before)


if __name__=='__main__':unittest.main()
