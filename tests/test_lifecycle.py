import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package
from monitor.engine import evaluate
from monitor.evaluation import evaluate_all
from monitor.reporting import build_report,report_markdown
from monitor.schema import normalize_snapshot
from monitor.schema import validate_settings
from test_engine import NOW,series,settings


def config():
    value=settings();value['accounts'][0]['scene']='product'
    return value


class ConfirmedColdBidCapTests(unittest.TestCase):
    def review(self,bid=14,*,promotion='standard',target_cost=10):
        rows=cold_rows();cfg=config();cfg['rules']['cold_bid_max_target_ratio']=1.4
        for row in rows:
            row['promotion_type']=promotion;row['metrics'].update(bid=bid,target_cost=target_cost)
            row['context']['plan_bid_mode']='conversion_cost'
        return rows,cfg,evaluate_all(rows,cfg,NOW)

    def test_exact_1_4_cap_is_within_and_does_not_require_a_bid_mutation(self):
        rows,cfg,e=self.review();r=e['lifecycle_reviews'][0]
        self.assertEqual(r['bid_cap_status'],'user_confirmed_1_4')
        self.assertEqual(r['cold_bid_review']['ceiling'],14);self.assertEqual(r['cold_bid_review']['state'],'within_user_cap')
        self.assertTrue(any(x['action']=='budget_up' and x['eligible'] for x in e['recommendations']))
        self.assertFalse(any(x['action']=='bid_up' for x in e['recommendations']))

    def test_exceeding_the_user_ceiling_warns_and_holds_cold_growth(self):
        _,_,e=self.review(14.01)
        self.assertEqual(e['lifecycle_reviews'][0]['cold_bid_review']['state'],'above_user_cap')
        self.assertTrue(any(a['rule']=='cold_bid_above_user_cap' and a['priority']=='P1' for a in e['alerts']))
        rec=next(x for x in e['recommendations'] if x['action']=='budget_up');self.assertFalse(rec['eligible'])
        self.assertTrue(any('1.4' in x for x in rec['blockers']))

    def test_policy_ceiling_is_not_rounded_to_an_unverified_platform_precision(self):
        _,_,e=self.review(14.042,target_cost=10.03)
        self.assertEqual(e['lifecycle_reviews'][0]['cold_bid_review']['ceiling'],14.042)
        self.assertEqual(e['lifecycle_reviews'][0]['cold_bid_review']['state'],'within_user_cap')

    def test_universal_roi_plan_is_not_treated_as_a_standard_cost_bid(self):
        _,_,e=self.review(100,promotion='universal')
        self.assertEqual(e['lifecycle_reviews'][0]['cold_bid_review']['state'],'not_standard_cost_bid')
        self.assertIsNone(e['lifecycle_reviews'][0]['cold_bid_review']['ceiling'])

    def test_standard_plan_roi_or_unknown_bid_mode_has_no_currency_ceiling(self):
        for mode in ('roi','unknown',None):
            rows,cfg,_=self.review()
            for row in rows:row['context']['plan_bid_mode']=mode
            r=evaluate_all(rows,cfg,NOW)['lifecycle_reviews'][0]['cold_bid_review']
            self.assertEqual(r['state'],'not_standard_cost_bid');self.assertIsNone(r['ceiling'])

    def test_cost_bid_mode_survives_import_but_invalid_mode_is_rejected(self):
        rows,cfg,_=self.review();row=rows[-1]
        self.assertEqual(normalize_snapshot(row,cfg,NOW)['context']['plan_bid_mode'],'conversion_cost')
        row['context']['plan_bid_mode']='unsupported'
        with self.assertRaises(ValueError):normalize_snapshot(row,cfg,NOW)

    def test_missing_cost_basis_or_observed_bid_never_becomes_a_new_bid(self):
        rows,cfg,_=self.review(None)
        self.assertEqual(evaluate_all(rows,cfg,NOW)['lifecycle_reviews'][0]['cold_bid_review']['state'],'bid_missing')
        rows[-1]['context'].pop('target_cost_basis')
        r=evaluate_all(rows,cfg,NOW)['lifecycle_reviews'][0]['cold_bid_review']
        self.assertEqual(r['state'],'target_cost_unverified');self.assertIsNone(r['ceiling'])

    def test_unconfirmed_or_invalid_setting_cannot_silently_choose_a_ratio(self):
        for value in (1.5,.4,True):
            with self.assertRaises(ValueError):validate_settings({'rules':{'cold_bid_max_target_ratio':value}})
        self.assertEqual(validate_settings({'rules':{'cold_bid_max_target_ratio':1.4}})['rules']['cold_bid_max_target_ratio'],1.4)
        rows=cold_rows();e=evaluate_all(rows,config(),NOW)
        self.assertEqual(e['lifecycle_reviews'][0]['cold_bid_review']['state'],'interpretation_pending')


def cold_rows():
    rows=series(roi=2.5,count=25,spend_step=10)
    for row in rows:
        row['scene']='product';row['source']['metric_period']='day'
        row['metrics'].update(budget=300,target_cost=10,industry_ctr=3)
        row['control'].update(lifecycle='cold',budget_original=300)
        row['context']={'created_at':(NOW-timedelta(days=1)).isoformat(),'plan_id':'plan1','content_format':'image_text',
            'plan_budget_period':'day','plan_budget_period_verified':True,'budget_mode':'specified',
            'cold_min_test_spend':200,'cold_test_threshold_verified':True,'target_cost_basis':'overall_payment',
            'manual_reviewed':True,'model_stable':True,'compliance_ok':True,'industry_baseline_verified':True,
            'industry_baseline_at':(NOW-timedelta(days=2)).isoformat(),'industry_baseline_source':'PRIVATE_BENCHMARK_SOURCE',
            'industry_id':'women_top','baseline_industry_id':'women_top'}
    return rows


def run(rows=None,cfg=None,now=NOW):return evaluate_all(cold_rows() if rows is None else rows,cfg or config(),now)
def review(result):return result['lifecycle_reviews'][0]
def ups(result):return [r for r in result['recommendations'] if r['action']=='budget_up']


class LifecycleTests(unittest.TestCase):
    def test_cold_positive_test_can_raise_gradually_using_existing_caps(self):
        result=run();item=review(result);rec=ups(result)[0]
        self.assertEqual(item['state'],'cold_ready');self.assertEqual(item['daily_test_spend'],240)
        self.assertTrue(rec['eligible']);self.assertEqual(rec['before'],300);self.assertEqual(rec['after'],360)
        self.assertEqual(rec['change_pct'],.2);self.assertEqual(rec['rule'],'cold_budget_stable_3h')
        self.assertFalse(item['execution_eligible']);self.assertFalse(item['platform_verified'])

    def test_spend_threshold_is_explicit_and_equality_is_enough(self):
        for threshold,ready in ((240,True),(241,False)):
            rows=cold_rows()
            for row in rows:row['context']['cold_min_test_spend']=threshold
            self.assertEqual(review(run(rows))['cold_budget_ready'],ready)
            self.assertEqual(ups(run(rows))[0]['eligible'],ready)

    def test_missing_test_threshold_and_verification_are_not_defaulted(self):
        for field in ('cold_min_test_spend','cold_test_threshold_verified'):
            rows=cold_rows();rows[-1]['context'].pop(field)
            self.assertFalse(review(run(rows))['cold_budget_ready']);self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_daily_budget_period_mode_and_confirmation_are_required(self):
        for change in ({'plan_budget_period':'total'},{'plan_budget_period_verified':False},{'budget_mode':'suggested'}):
            rows=cold_rows();rows[-1]['context'].update(change)
            self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_low_budget_is_reviewed_without_a_jump_to_300(self):
        rows=cold_rows()
        for row in rows:row['metrics']['budget']=100;row['control']['budget_original']=100
        result=run(rows);rec=next(r for r in result['recommendations'] if r['action']=='review_cold_budget')
        self.assertFalse(rec['eligible']);self.assertIsNone(rec['after']);self.assertIsNone(rec['change_pct'])
        self.assertEqual(rec['parameters']['strategy_initial_daily_budget'],300)
        self.assertTrue(all(not r['eligible'] and r['after']<=120 for r in ups(result)))

    def test_missing_user_goal_preserves_the_review_without_numeric_growth(self):
        cfg=config();cfg['accounts'][0]['target_roi']=None
        result=run(cfg=cfg);self.assertEqual(ups(result),[])
        self.assertFalse(review(result)['cold_budget_ready']);self.assertTrue(review(result)['is_current'])

    def test_wrong_target_cost_basis_or_missing_cost_holds_growth(self):
        for change in ('basis','missing'):
            rows=cold_rows()
            if change=='basis':rows[-1]['context']['target_cost_basis']='marketing'
            else:rows[-1]['metrics']['target_cost']=None
            self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_low_industry_ctr_and_unknown_baseline_hold_growth(self):
        for field,value in (('industry_ctr',5),('industry_ctr',None)):
            rows=cold_rows();rows[-1]['metrics'][field]=value
            self.assertFalse(ups(run(rows))[0]['eligible'])
        rows=cold_rows();rows[-1]['context']['baseline_industry_id']='other'
        self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_baseline_age_source_and_industry_proof_are_required(self):
        for change in ({'industry_baseline_at':(NOW-timedelta(days=31)).isoformat()},
                       {'industry_baseline_at':(NOW+timedelta(minutes=1)).isoformat()},
                       {'industry_baseline_source':''},{'industry_baseline_verified':False}):
            rows=cold_rows();rows[-1]['context'].update(change)
            self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_clicks_must_use_the_same_ledger_and_sufficient_sample(self):
        rows=cold_rows();rows[-1]['metrics'].update(clicks=10,impressions=100)
        self.assertFalse(any(r['eligible'] for r in ups(run(rows))))
        rows=cold_rows();rows[-1]['metrics'].update(clicks=25000,impressions=24000)
        self.assertFalse(any(r['eligible'] for r in ups(run(rows))))

    def test_video_requires_three_second_observation_while_image_text_does_not(self):
        self.assertTrue(review(run())['cold_budget_ready'])
        rows=cold_rows()
        for row in rows:row['context']['content_format']='short_video'
        self.assertFalse(review(run(rows))['cold_budget_ready'])
        rows[-1]['metrics']['completion_3s']=0
        self.assertTrue(review(run(rows))['cold_budget_ready']);self.assertEqual(review(run(rows))['completion_3s'],0)

    def test_expansion_requires_explicit_model_and_compliance_evidence(self):
        for field in ('manual_reviewed','model_stable','compliance_ok'):
            rows=cold_rows();rows[-1]['context'][field]=False
            self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_existing_budget_daily_cap_cooldown_and_guarantee_still_apply(self):
        for change in ({'budget_increase_today':140},{'last_change_at':(NOW-timedelta(minutes=59)).isoformat()},
                       {'history_known':False},{'guarantee_verified':False},{'running':False}):
            rows=cold_rows();rows[-1]['control'].update(change)
            self.assertTrue(review(run(rows))['cold_budget_ready']);self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_two_hours_low_samples_gaps_and_resets_do_not_create_ready_state(self):
        for change in ('short','gap','reset','low_roi'):
            rows=cold_rows()
            if change=='short':rows=rows[-13:]
            elif change=='gap':del rows[-5:-2]
            elif change=='reset':rows[-2]['metrics']['gmv']=0
            else:
                for row in rows:row['metrics']['gmv']=row['metrics']['spend']
            result=run(rows);self.assertFalse(review(result)['cold_budget_ready'])
            self.assertFalse(any(r['eligible'] for r in ups(result)))

    def test_age_72_boundary_is_cold_and_older_cold_label_conflicts(self):
        for age,ready in ((timedelta(hours=72),True),(timedelta(hours=72,seconds=1),False)):
            rows=cold_rows()
            for row in rows:row['context']['created_at']=(NOW-age).isoformat()
            self.assertEqual(review(run(rows))['cold_budget_ready'],ready)
            self.assertEqual(ups(run(rows))[0]['eligible'],ready)

    def test_mature_or_growing_label_cannot_skip_the_declared_cold_age_range(self):
        for stage in ('growing','mature'):
            rows=cold_rows()
            for row in rows:row['control']['lifecycle']=stage
            result=run(rows);self.assertEqual(review(result)['state'],'stage_conflict');self.assertFalse(ups(result)[0]['eligible'])

    def test_growth_and_maturity_use_existing_policy_after_cold_period(self):
        for stage in ('growing','mature'):
            rows=cold_rows()
            for row in rows:row['control']['lifecycle']=stage;row['context']['created_at']=(NOW-timedelta(days=4)).isoformat()
            result=run(rows);self.assertEqual(review(result)['state'],stage);self.assertTrue(ups(result)[0]['eligible'])
            self.assertEqual(ups(result)[0]['rule'],'roi_stable_3h')

    def test_missing_future_or_inconsistent_creation_time_is_not_inferred(self):
        for change in ('missing','future','inconsistent'):
            rows=cold_rows()
            if change=='missing':rows[-1]['context'].pop('created_at')
            elif change=='future':rows[-1]['context']['created_at']=(NOW+timedelta(hours=1)).isoformat()
            else:rows[-2]['context']['created_at']=(NOW-timedelta(days=2)).isoformat()
            self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_test_window_cannot_predate_the_plan(self):
        rows=cold_rows()
        for row in rows:row['context']['created_at']=(NOW-timedelta(hours=2)).isoformat()
        item=review(run(rows));self.assertFalse(item['cold_budget_ready']);self.assertIn('创建时间',''.join(item['missing']))

    def test_live_or_unknown_content_cannot_borrow_product_cold_budget_policy(self):
        for change in ('live','unknown'):
            rows=cold_rows()
            for row in rows:
                if change=='live':row['scene']='live'
                else:row['context']['content_format']='unknown'
            self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_base_test_spend_and_counts_never_fall_back_to_total(self):
        rows=cold_rows()
        for row in rows:
            m=row['metrics']
            for key in ('spend','gmv','orders','clicks','impressions'):m['base_'+key]=m[key]
            m.update(spend=m['spend']*10,gmv=m['gmv']*10)
        self.assertTrue(review(run(rows))['cold_budget_ready']);self.assertEqual(review(run(rows))['daily_test_spend'],240)
        rows[-1]['metrics'].pop('base_clicks');self.assertFalse(ups(run(rows))[0]['eligible'])

    def test_stale_data_and_unknown_update_time_do_not_authorize_cold_growth(self):
        rows=cold_rows();rows[-1]['source']['data_at']=None
        self.assertFalse(any(r['eligible'] for r in ups(run(rows))))
        rows=cold_rows();result=run(rows,now=NOW+timedelta(hours=1))
        self.assertFalse(review(result)['is_current']);self.assertFalse(any(r['eligible'] for r in ups(result)))

    def test_creation_and_lifecycle_conflict_across_fresh_alias_sources_blocks_growth(self):
        rows=cold_rows();other=copy.deepcopy(rows[-1]);other['id']='other';other['entity_id']='alias'
        other['source']['series_key']='other-report';other['control']['lifecycle']='mature'
        other['context']['created_at']=(NOW-timedelta(days=4)).isoformat()
        rec=ups(run(rows+[other]))[0];self.assertFalse(rec['eligible']);self.assertIn('其他新鲜来源',''.join(rec['blockers']))

    def test_stale_other_stage_does_not_override_current_cold_source(self):
        rows=cold_rows();other=copy.deepcopy(rows[-1]);other['id']='other';other['entity_id']='alias'
        other['source'].update(series_key='other-report',data_at=(NOW-timedelta(hours=1)).isoformat())
        other['control']['lifecycle']='mature';other['context']['created_at']=(NOW-timedelta(days=4)).isoformat()
        self.assertTrue(ups(run(rows+[other]))[0]['eligible'])

    def test_same_capture_stage_or_content_conflict_does_not_choose_a_label(self):
        rows=cold_rows();other=copy.deepcopy(rows[-1]);other['id']='other';other['context']['content_format']='short_video'
        result=run(rows+[other]);self.assertEqual(review(result)['state'],'stage_conflict');self.assertFalse(ups(result)[0]['eligible'])

    def test_browser_and_legacy_cannot_certify_new_cold_context(self):
        raw=copy.deepcopy(cold_rows()[-1]);raw['source']['dataset_id']='declared'
        self.assertTrue(normalize_snapshot(raw,config(),NOW)['context']['cold_test_threshold_verified'])
        for kind in ('browser_dom','legacy_db'):
            raw['source'].update(kind=kind,url='https://qianchuan.jinritemai.com/uni-prom?aavid=123')
            row=normalize_snapshot(raw,config(),NOW)
            self.assertIsNone(row['context']['cold_test_threshold_verified']);self.assertIsNone(row['context']['plan_budget_period_verified'])

    def test_schema_rejects_invalid_cold_threshold_or_budget_period(self):
        for change in ({'cold_min_test_spend':0},{'cold_min_test_spend':True},{'plan_budget_period':'session'}):
            raw=copy.deepcopy(cold_rows()[-1]);raw['context'].update(change)
            with self.assertRaises(ValueError):normalize_snapshot(raw,config(),NOW)

    def test_account_report_and_ai_keep_declared_stage_without_private_baseline_source(self):
        rows=cold_rows();cfg=config();before=copy.deepcopy((rows,cfg));result=run(rows,cfg)
        report=build_report(rows,cfg,result,'123',NOW)
        self.assertEqual(report['evidence_and_risks']['lifecycle_reviews'][0]['state'],'cold_ready')
        self.assertIn('生命周期分层复核',report_markdown(report))
        text=json.dumps(make_package(rows,result));self.assertIn('lifecycle_observations',text)
        self.assertNotIn('PRIVATE_BENCHMARK_SOURCE',text);self.assertNotIn('plan1',text)
        self.assertEqual((rows,cfg),before)

    def test_declining_stage_keeps_maintenance_priority_and_does_not_promote(self):
        rows=cold_rows()
        for row in rows:row['control']['lifecycle']='declining';row['context']['created_at']=(NOW-timedelta(days=4)).isoformat()
        result=run(rows);self.assertEqual(review(result)['state'],'declining');self.assertFalse(ups(result)[0]['eligible'])

    def test_other_account_stage_does_not_veto_the_cold_plan(self):
        rows=cold_rows();other=copy.deepcopy(rows[-1]);other.update(id='other',account_id='456',entity_id='alias')
        other['control']['lifecycle']='mature';other['context']['created_at']=(NOW-timedelta(days=4)).isoformat()
        cfg=config();cfg['accounts'].append({'id':'456','scene':'product','target_roi':2,'roi_basis':'overall_payment'})
        result=run(rows+[other],cfg)
        self.assertTrue(next(r for r in ups(result) if r['account_id']=='123')['eligible'])
        report=build_report(rows+[other],cfg,result,'123',NOW)
        self.assertTrue(all(r['account_id']=='123' for r in report['evidence_and_risks']['lifecycle_reviews']))

    def test_cold_parent_plan_vetoes_creative_chase_admission(self):
        from test_strategies import creative_rows,account_row
        parent=cold_rows()
        for row in parent:
            row['id']='parent-'+row['id'];row['entity_id']='parent1';row['context']['plan_id']='parent1'
            row['source']['series_key']='parent-report';row['metrics']['budget']=1000;row['control']['budget_original']=1000
        creatives=creative_rows()
        for row in creatives:row['id']='creative-'+row['id'];row['scene']='product'
        account=account_row();account['id']='account-latest';account['scene']='product'
        result=run(parent+creatives+[account])
        rec=next(r for r in result['recommendations'] if r['action']=='create_chase')
        self.assertFalse(rec['eligible']);self.assertIn('冷启动阶段',''.join(rec['blockers']))

    def test_conflicting_mature_label_cannot_unlock_standard_bid_reduction(self):
        rows=cold_rows()
        for row in rows:
            row['control']['lifecycle']='mature';row['promotion_type']='standard';row['metrics']['bid']=100
            row['context']['bid_change_history_known']=True
        result=run(rows);rec=next(r for r in result['recommendations'] if r['action']=='bid_down')
        self.assertFalse(rec['eligible']);self.assertIn('生命周期',''.join(rec['blockers']))


if __name__=='__main__':unittest.main()
