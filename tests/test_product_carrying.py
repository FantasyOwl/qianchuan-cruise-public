import copy
import json
import unittest
from datetime import timedelta

from monitor.analytics import analyze_monitoring
from monitor.evaluation import evaluate_all
from monitor.product_carrying import review_product_carrying
from monitor.schema import normalize_snapshot
from monitor.reporting import build_report,report_markdown
from test_engine import NOW,series,settings


def fixture(stock=12):
    plans=series(roi=3);products=copy.deepcopy(plans)
    for row in plans:
        row['context']={'plan_id':'plan1','promoted_product_id':'prod1','product_plan_mapping_verified':True,
            'promotion_product_mode':'single_product','product_mapping_at':row['source']['captured_at'],
            'aweme_id':'aw1','live_room_id':'room1'}
    for row in products:
        n=int(row['id']);row.update(id='product-'+str(n),scope='product',entity_id='prod1')
        row['source']['series_key']='product-report'
        row['metrics'].update(stock=100,available_stock=stock,product_units_sold=n*4,product_orders=n*2)
        row['context']={'product_id':'prod1','inventory_scope':'product','inventory_units_verified':True,
            'inventory_at':row['source']['captured_at'],'product_sales_units_verified':True,
            'product_sales_scope':'total_product','minimum_stock_hours':1,'stock_policy_verified':True,
            'aweme_id':'aw1','live_room_id':'room1','product_metrics_verified':True}
    return plans+products,settings(),NOW


def review(args):return review_product_carrying(*args)


class ProductCarryingTests(unittest.TestCase):
    def test_item_pace_does_not_treat_orders_as_units(self):
        rows,cfg,at=fixture();r=review((rows,cfg,at))
        inventory=r['inventory'][0]
        self.assertEqual(inventory['units_per_hour'],24);self.assertEqual(inventory['stock_coverage_hours'],.5)
        legacy=next(i for i in analyze_monitoring(rows,cfg,at)['entities'] if i['scope']=='product')
        self.assertIsNone(legacy['product']['stock_coverage_hours'])

    def test_low_stock_holds_only_explicitly_related_growth(self):
        rows,cfg,at=fixture();other=copy.deepcopy(rows[:19])
        for row in other:row.update(id='other-'+row['id'],entity_id='other-plan');row['context']={}
        e=evaluate_all(rows+other,cfg,at)
        own=next(r for r in e['recommendations'] if r['action']=='budget_up' and r['entity_id']=='plan1')
        peer=next(r for r in e['recommendations'] if r['action']=='budget_up' and r['entity_id']=='other-plan')
        self.assertFalse(own['eligible']);self.assertTrue(peer['eligible'])

    def test_stock_zero_is_known_without_inventing_a_sales_rate(self):
        rows,cfg,at=fixture(0)
        for row in rows[19:]:row['metrics'].pop('product_units_sold')
        r=review((rows,cfg,at));self.assertEqual(r['inventory'][0]['state'],'stock_empty')
        self.assertIsNone(r['inventory'][0]['units_per_hour']);self.assertTrue(r['plans'][0]['hold_growth'])

    def test_no_user_threshold_means_description_instead_of_inventory_shortage(self):
        rows,cfg,at=fixture()
        for row in rows[19:]:row['context'].pop('minimum_stock_hours')
        r=review((rows,cfg,at));self.assertEqual(r['inventory'][0]['state'],'coverage_known_policy_missing')
        self.assertFalse(r['plans'][0]['hold_growth'])

    def test_ad_attributed_units_and_order_counts_cannot_predict_total_product_stock(self):
        for mode in ('ad_attributed','unknown',None):
            rows,cfg,at=fixture()
            for row in rows[19:]:row['context']['product_sales_scope']=mode
            self.assertIsNone(review((rows,cfg,at))['inventory'][0]['stock_coverage_hours'])

    def test_zero_sales_rate_does_not_become_infinite_coverage(self):
        rows,cfg,at=fixture()
        for row in rows[19:]:row['metrics']['product_units_sold']=0
        r=review((rows,cfg,at))['inventory'][0];self.assertEqual(r['units_per_hour'],0);self.assertIsNone(r['stock_coverage_hours'])

    def test_sku_stock_does_not_prove_entire_product_unavailable(self):
        rows,cfg,at=fixture(0)
        for row in rows[19:]:row['context']['inventory_scope']='sku'
        self.assertFalse(review((rows,cfg,at))['plans'][0]['hold_growth'])

    def test_old_inventory_or_unverified_mapping_does_not_veto_a_current_plan(self):
        for mode in ('stale','mapping','catalog'):
            rows,cfg,at=fixture(0)
            if mode=='stale':rows[-1]['context']['inventory_at']=(at-timedelta(hours=1)).isoformat()
            elif mode=='mapping':rows[18]['context']['product_plan_mapping_verified']=False
            else:rows[18]['context']['promotion_product_mode']='catalog'
            self.assertFalse(review((rows,cfg,at))['plans'][0]['hold_growth'])

    def test_inventory_and_mapping_times_must_align_and_live_identity_must_match(self):
        for field,value in (('inventory_at',(NOW-timedelta(minutes=3)).isoformat()),('live_room_id','other-room'),('aweme_id','other-aweme')):
            rows,cfg,at=fixture(0);rows[-1]['context'][field]=value
            self.assertFalse(review((rows,cfg,at))['plans'][0]['hold_growth'])

    def test_inventory_timestamp_is_separate_from_performance_timestamp(self):
        rows,cfg,at=fixture(0);rows[-1]['source']['data_at']=None
        r=review((rows,cfg,at));self.assertTrue(r['plans'][0]['hold_growth'])
        self.assertIsNone(r['inventory'][0]['performance_data_at']);self.assertIsNone(r['inventory'][0]['units_per_hour'])

    def test_gap_reset_or_missing_sales_updates_block_only_rate_estimation(self):
        for mode in ('gap','reset','missing'):
            rows,cfg,at=fixture()
            if mode=='gap':del rows[-5:-2]
            elif mode=='reset':rows[-3]['metrics']['product_units_sold']=0
            else:rows[-3]['source']['data_at']=None
            r=review((rows,cfg,at));self.assertIsNone(r['inventory'][0]['stock_coverage_hours'])

    def test_unknown_quality_does_not_certify_an_inventory_clock(self):
        rows,cfg,at=fixture(0);rows[-1]['quality']['issues']=['unknown metric definition']
        self.assertFalse(review((rows,cfg,at))['plans'][0]['hold_growth'])

    def test_invalid_quality_shapes_do_not_crash_or_supply_a_sales_window(self):
        for quality in (None,{'issues':'bad'},{'issues':[{}]}):
            rows,cfg,at=fixture();rows[-3]['quality']=quality
            self.assertIsNone(review((rows,cfg,at))['inventory'][0]['stock_coverage_hours'])

    def test_conflicting_current_inventory_sources_cannot_select_positive_stock(self):
        rows,cfg,at=fixture(0);peer=copy.deepcopy(rows[-1]);peer['id']='peer-stock';peer['source']['series_key']='other-stock';peer['metrics']['available_stock']=100
        r=review((rows+[peer],cfg,at));self.assertEqual(r['plans'][0]['inventory_state'],'conflict');self.assertTrue(r['plans'][0]['hold_growth'])

    def test_coincident_missing_quantity_does_not_cancel_known_zero_quantity(self):
        rows,cfg,at=fixture(0);peer=copy.deepcopy(rows[-1]);peer['id']='missing-peer';peer['source']['series_key']='missing-peer';peer['metrics']['available_stock']=None
        r=review((rows+[peer],cfg,at));self.assertTrue(r['plans'][0]['hold_growth']);self.assertEqual(r['plans'][0]['available_stock'],0)

    def test_alternate_sales_window_cannot_hide_a_current_low_coverage_source(self):
        rows,cfg,at=fixture();fast=copy.deepcopy(rows[19:])
        for n,row in enumerate(rows[19:]):row['metrics']['product_units_sold']=n
        for row in fast:row['id']='fast-'+row['id'];row['source']['series_key']='fast-source'
        r=review((rows+fast,cfg,at));self.assertTrue(r['plans'][0]['hold_growth'])
        self.assertEqual(r['plans'][0]['stock_coverage_hours'],.5);self.assertIn('fast-source',r['plans'][0]['inventory_source_key'])

    def test_latest_mapping_supersedes_old_product_instead_of_holding_old_stock(self):
        rows,cfg,at=fixture(0);old=copy.deepcopy(rows[18]);old['id']='old-map';old['source']['series_key']='old-map'
        old['context'].update(promoted_product_id='prod-old',product_mapping_at=(at-timedelta(minutes=1)).isoformat())
        r=review((rows+[old],cfg,at));self.assertFalse(next(p for p in r['plans'] if p['source_key'].find('old-map')>=0)['mapping_verified'])

    def test_simultaneous_single_product_mapping_conflict_holds_growth(self):
        rows,cfg,at=fixture(100);other=copy.deepcopy(rows[18]);other['id']='other-mapping';other['source']['series_key']='another-map';other['context']['promoted_product_id']='prod2'
        self.assertTrue(all(p['hold_growth'] for p in review((rows+[other],cfg,at))['plans']))

    def explain(self,same_category=False):
        rows,cfg,at=fixture(100)
        rows[18]['context'].update(explanation_verified=True,explanation_active=True,explained_product_id='prod2',
            promoted_category_id='cat1',explained_category_id='cat1' if same_category else 'cat2',
            explanation_observed_at=at.isoformat(),explanation_started_at=(at-timedelta(minutes=10)).isoformat())
        return rows,cfg,at

    def test_declared_active_category_mismatch_is_a_review_never_a_category_write(self):
        r=review(self.explain());self.assertEqual(r['plans'][0]['alignment_state'],'category_mismatch')
        self.assertTrue(r['plans'][0]['hold_growth']);self.assertTrue(r['recommendations'])
        self.assertFalse(any(v['eligible'] for v in r['recommendations']));self.assertTrue(all(v['after'] is None for v in r['recommendations']))

    def test_different_products_in_same_category_do_not_prove_category_mismatch(self):
        r=review(self.explain(True));self.assertEqual(r['plans'][0]['alignment_state'],'product_mismatch_review')
        self.assertFalse(r['plans'][0]['hold_growth'])

    def test_stopped_future_or_stale_explanation_does_not_invent_current_mismatch(self):
        for mode in ('stopped','future','stale','ended'):
            args=self.explain();ctx=args[0][18]['context']
            if mode=='stopped':ctx['explanation_active']=False
            elif mode=='future':ctx['explanation_started_at']=(NOW+timedelta(minutes=1)).isoformat()
            elif mode=='stale':ctx['explanation_observed_at']=(NOW-timedelta(hours=1)).isoformat()
            else:ctx['explanation_ends_at']=(NOW-timedelta(minutes=1)).isoformat()
            self.assertEqual(review(args)['plans'][0]['alignment_state'],'unverified')

    def test_image_text_does_not_inherit_live_explanation_rules(self):
        args=self.explain()
        for row in args[0]:row['scene']='product'
        self.assertEqual(review(args)['plans'][0]['alignment_state'],'unverified')

    def test_risk_does_not_disable_budget_stop_loss(self):
        rows,cfg,at=fixture(0)
        for row in rows[:19]:row['metrics']['gmv']=row['metrics']['spend']
        self.assertTrue(any(r['action']=='budget_down' and r['eligible'] for r in evaluate_all(rows,cfg,at)['recommendations']))

    def test_missing_user_roi_target_does_not_hide_inventory_risk(self):
        rows,cfg,at=fixture(0);cfg['accounts'][0]['target_roi']=None
        self.assertTrue(review((rows,cfg,at))['plans'][0]['hold_growth'])

    def test_inventory_quantity_does_not_require_a_financial_roi_basis(self):
        rows,cfg,at=fixture()
        for row in rows[19:]:row['metric_basis']='unknown';row['quality']['issues']=['ROI 口径未确认']
        r=review((rows,cfg,at));self.assertEqual(r['inventory'][0]['stock_coverage_hours'],.5)
        self.assertTrue(r['plans'][0]['hold_growth'])

    def test_ai_anonymizes_product_category_plan_and_room_identifiers(self):
        from monitor.ai import make_package
        rows,cfg,at=self.explain();e=evaluate_all(rows,cfg,at);text=json.dumps(make_package(rows,e))
        for identifier in ('prod1','prod2','cat1','cat2','room1','aw1','plan1','product-report'):
            self.assertNotIn(identifier,text)

    def test_report_is_account_scoped_and_inputs_are_immutable(self):
        rows,cfg,at=fixture(0);before=copy.deepcopy(rows)
        cfg['accounts'].append({'id':'other','scene':'live','target_roi':2,'roi_basis':'overall_payment'})
        other=copy.deepcopy(rows[-1]);other.update(account_id='other',entity_id='PRIVATE_OTHER');other['context']['product_id']='PRIVATE_OTHER'
        e=evaluate_all(rows+[other],cfg,at);r=build_report(rows+[other],cfg,e,'123',at)
        self.assertNotIn('PRIVATE_OTHER',json.dumps(r));self.assertIn('商品库存与讲解承接',report_markdown(r));self.assertEqual(rows,before)

    def test_schema_checks_counts_and_prevents_browser_verification(self):
        rows,cfg,at=fixture();row=rows[-1]
        self.assertEqual(normalize_snapshot(row,cfg,at)['metrics']['product_units_sold'],72)
        row['metrics']['product_units_sold']=1.5
        with self.assertRaises(ValueError):normalize_snapshot(row,cfg,at)
        row['metrics']['product_units_sold']=72;row['source'].update(kind='browser_dom',url='https://qianchuan.jinritemai.com/overall-prom?aavid=123')
        normalized=normalize_snapshot(row,cfg,at);self.assertIsNone(normalized['context']['inventory_units_verified'])
        self.assertIsNone(normalized['context']['product_sales_units_verified'])

    def test_malformed_input_returns_no_inventory_or_plan_certification(self):
        r=review_product_carrying([None,{}, {'scope':'product','source':None}],settings(),NOW)
        self.assertEqual(r['inventory'],[]);self.assertEqual(r['plans'],[])
