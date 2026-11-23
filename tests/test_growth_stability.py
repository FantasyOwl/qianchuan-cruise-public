import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

from monitor.engine import evaluate, _settings
from monitor.evaluation import evaluate_all
from monitor.schema import validate_settings
from monitor.ai import make_package
from monitor.reporting import build_report, report_markdown
from monitor.submission import AttemptLedger, BudgetClient
from test_engine import NOW, series, settings
from test_submission import fixture as submission_fixture


def varying(roi_steps=(3, 3, 3), order_steps=(2, 2, 2), cost_steps=(100, 100, 100), basis='overall_payment'):
    rows=series(roi=3,basis=basis)
    spend=cost=gmv=orders=0
    for n,row in enumerate(rows):
        if n:
            h=(n-1)//6
            spend+=100;cost+=cost_steps[h];gmv+=cost_steps[h]*roi_steps[h];orders+=order_steps[h]
        row['metrics'].update(spend=spend,comprehensive_cost=cost,gmv=gmv,net_gmv=gmv,orders=orders)
    return rows


def up(rows,cfg=None):
    return next(r for r in evaluate(rows,cfg or settings(),NOW)['recommendations'] if r['action']=='budget_up')


class GrowthStabilityTests(unittest.TestCase):
    def test_stable_samples_pass_and_include_actual_boundaries_and_local_policy(self):
        rows=varying();cfg=settings();before=copy.deepcopy(cfg)
        rec=up(rows,cfg);value=rec['evidence']['growth_stability']
        self.assertTrue(rec['eligible']);self.assertTrue(value['passed'])
        self.assertEqual(value['policy_basis'],'local_strategy_guardrail')
        self.assertFalse(value['platform_policy_verified'])
        self.assertEqual(value['ratios'],{'roi':1,'conversion_cost':1,'spend_rate':1})
        self.assertEqual([w['observed_seconds'] for w in value['windows']],[3600]*3)
        self.assertEqual(cfg,before)

    def test_roi_above_target_each_hour_can_still_be_unstable(self):
        rec=up(varying(roi_steps=(3,6,3)))
        self.assertFalse(rec['eligible'])
        self.assertEqual(rec['evidence']['growth_stability']['ratios']['roi'],2)
        self.assertTrue(any('ROI最高/最低' in text for text in rec['blockers']))

    def test_cost_volatility_is_independent_of_stable_roi_and_raw_spend(self):
        rec=up(varying(order_steps=(2,4,2)))
        value=rec['evidence']['growth_stability']
        self.assertFalse(rec['eligible'])
        self.assertEqual(value['ratios']['roi'],1)
        self.assertEqual(value['ratios']['spend_rate'],1)
        self.assertEqual(value['ratios']['conversion_cost'],2)

    def test_marketing_cost_does_not_borrow_ad_spend_for_cost_stability(self):
        rows=varying(cost_steps=(100,100,200),basis='marketing')
        rec=up(rows,settings('marketing'))
        value=rec['evidence']['growth_stability']
        self.assertFalse(rec['eligible'])
        self.assertEqual(value['ratios']['spend_rate'],1)
        self.assertEqual(value['ratios']['roi'],1)
        self.assertEqual(value['ratios']['conversion_cost'],2)

    def test_base_ledger_ignores_inclusive_totals_but_not_base_cost_waves(self):
        for order_steps,eligible in (((2,2,2),True),((2,4,2),False)):
            rows=varying(order_steps=order_steps)
            for row in rows:
                m=row['metrics'];m.update(base_spend=m['spend'],base_gmv=m['gmv'],base_orders=m['orders'])
                m.update(spend=999999,gmv=999999,orders=999999)
            rec=up(rows)
            self.assertEqual(rec['eligible'],eligible)
            self.assertTrue(all(w['ledger']=='base' for w in rec['evidence']['growth_stability']['windows']))

    def test_zero_order_stop_loss_samples_cannot_qualify_growth(self):
        rows=varying(order_steps=(0,0,0))
        for row in rows:row['metrics']['target_cost']=1
        rec=up(rows)
        self.assertFalse(rec['eligible'])
        self.assertEqual(rec['evidence']['growth_stability']['state'],'insufficient')
        self.assertTrue(any('零订单' in reason for reason in rec['blockers']))

    def test_time_normalization_accepts_equal_actual_rates_at_stricter_limit(self):
        rows=varying()
        start=NOW-timedelta(hours=3)
        for n,row in enumerate(rows):
            at=NOW-timedelta(minutes=10*(18-n)+(5 if n==6 else 0))
            spend=(at-start).total_seconds()/60*10
            row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat())
            row['metrics'].update(spend=spend,comprehensive_cost=spend,gmv=spend*3,net_gmv=spend*3,orders=spend/50)
        cfg=settings();cfg['rules']['growth_spend_max_ratio']=1
        rec=up(rows,cfg)
        self.assertTrue(rec['eligible'])
        windows=rec['evidence']['growth_stability']['windows']
        self.assertEqual([w['observed_seconds'] for w in windows],[3300,3900,3600])
        self.assertEqual([w['spend_rate_per_hour'] for w in windows],[600]*3)

    def test_equal_raw_amounts_do_not_hide_unequal_actual_rates(self):
        rows=varying()
        at=NOW-timedelta(minutes=125)
        rows[6]['source'].update(data_at=at.isoformat(),captured_at=at.isoformat())
        cfg=settings();cfg['rules']['growth_spend_max_ratio']=1.1
        rec=up(rows,cfg)
        self.assertFalse(rec['eligible'])
        self.assertGreater(rec['evidence']['growth_stability']['ratios']['spend_rate'],1.1)

    def test_boundary_tolerance_cannot_turn_175_minutes_into_three_hours(self):
        rows=varying()
        at=NOW-timedelta(minutes=175)
        rows[0]['source'].update(data_at=at.isoformat(),captured_at=at.isoformat())
        rec=up(rows)
        self.assertFalse(rec['eligible'])
        self.assertEqual(rec['evidence']['growth_stability']['total_observed_seconds'],175*60)
        self.assertTrue(any('未满三小时' in reason for reason in rec['blockers']))

    def test_equality_passes_without_rounding_and_stricter_local_policy_is_respected(self):
        rows=varying(roi_steps=(3,3.3,3.6))
        self.assertTrue(up(rows)['eligible'])
        cfg=settings();cfg['rules']['growth_roi_max_ratio']=1.1
        self.assertFalse(up(rows,cfg)['eligible'])
        self.assertFalse(up(varying(roi_steps=(3,3.3,3.6001)))['eligible'])

    def test_local_configuration_rejects_invalid_values_and_direct_calls_cannot_loosen_caps(self):
        for key,maximum in (('growth_roi_max_ratio',1.2),('growth_cost_max_ratio',1.2),('growth_spend_max_ratio',1.3)):
            for value in (0,maximum+.01,None,True,float('nan')):
                with self.assertRaises(ValueError):validate_settings({'rules':{key:value}})
            rules,_=_settings({'rules':{key:99}})
            self.assertEqual(rules[key],maximum)

    def test_cold_positive_test_still_requires_cost_stability(self):
        from test_lifecycle import cold_rows,config
        rows=cold_rows();orders=0
        for n,row in enumerate(rows):
            if n:orders+=4 if (n-1)//6==2 else 2
            row['metrics']['orders']=orders
        evaluation=evaluate_all(rows,config(),NOW)
        self.assertTrue(evaluation['lifecycle_reviews'][0]['cold_budget_ready'])
        rec=next(r for r in evaluation['recommendations'] if r['action']=='budget_up')
        self.assertFalse(rec['eligible'])
        self.assertFalse(rec['evidence']['growth_stability']['passed'])

    def test_report_and_ai_keep_stability_evidence_without_authorizing_execution(self):
        rows=varying(order_steps=(2,4,2));cfg=settings()
        evaluation=evaluate_all(rows,cfg,NOW)
        report=build_report(rows,cfg,evaluation,'123',NOW)
        self.assertIn('growth_stability',json.dumps(report))
        self.assertIn('三小时转化成本',report_markdown(report))
        package=make_package(rows,evaluation)
        rec=next(r for r in package['local_recommendations'] if r['action']=='budget_up')
        self.assertFalse(rec['evidence']['growth_stability']['passed'])
        self.assertFalse(rec['eligible'])
        self.assertFalse(report['executed'])

    def test_future_budget_client_recomputes_cost_volatility_before_reading_credentials(self):
        rows,cfg,proposal,permit=submission_fixture()
        orders=0
        for n,row in enumerate(rows):
            if n:orders+=4 if (n-1)//6==1 else 2
            row['metrics']['orders']=orders
        token,transport=Mock(),Mock()
        with tempfile.TemporaryDirectory() as folder:
            client=BudgetClient(AttemptLedger(Path(folder)/'attempt.db'),enable_network=True,transport=transport,clock=lambda:NOW)
            with self.assertRaises(ValueError):
                client.dispatch('stale-review',proposal['id'],rows,cfg,permit,token,NOW)
        token.assert_not_called();transport.assert_not_called()


if __name__=='__main__':unittest.main()
