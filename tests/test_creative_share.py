import copy
import json
import unittest
from datetime import timedelta

from monitor.analytics import analyze_monitoring
from monitor.evaluation import evaluate_all
from monitor.schema import normalize_context
from monitor.reporting import build_report, report_markdown
from monitor.ai import make_package
from monitor.catalog import coverage_report
from test_engine import NOW, series, settings


def fixture(account='123', ledger='overall'):
    cfg = settings()
    cfg['accounts'][0]['id'] = account
    parent = series(count=1)[0]
    parent.update(id='parent-'+account, account_id=account, entity_id='private-parent')
    parent['source']['series_key'] = 'private-parent-report'
    parent['metrics'].update(spend=1000, base_spend=600)
    parent['context'] = {'plan_id': 'private-parent', 'spend_ledger': ledger}
    creative = copy.deepcopy(parent)
    creative.update(id='creative-'+account, entity_id='private-creative', scope='creative')
    creative['source']['series_key'] = 'private-creative-report'
    creative['metrics'].update(spend=250, base_spend=120)
    creative['context'] = {'parent_plan_id': 'private-parent', 'parent_source_key': 'private-parent-report',
                           'creative_parent_verified': True, 'spend_ledger': ledger}
    return [parent, creative], cfg


def share(rows, cfg=None, now=NOW):
    return next(item['creative_spend_share'] for item in analyze_monitoring(rows, cfg or settings(), now)['entities']
                if item['scope'] == 'creative')


class CreativeSpendShareTests(unittest.TestCase):
    def test_explicit_same_point_overall_ratio_is_descriptive_and_independent_of_target(self):
        rows, cfg = fixture()
        cfg['accounts'][0]['target_roi'] = None
        value = share(rows, cfg)
        self.assertEqual(value['share_percent'], 25)
        self.assertTrue(value['is_current'])
        self.assertFalse(value['used_for_action'])
        self.assertFalse(value['platform_verified'])
        self.assertFalse(value['complete_creative_coverage_verified'])

    def test_base_ledger_does_not_borrow_total_spend(self):
        rows, cfg = fixture(ledger='base')
        self.assertEqual(share(rows, cfg)['share_percent'], 20)
        rows[1]['metrics']['base_spend'] = None
        self.assertIsNone(share(rows, cfg)['share_percent'])

    def test_overall_ledger_does_not_silently_use_base_or_comprehensive_cost(self):
        rows, cfg = fixture()
        rows[1]['metrics'].update(spend=None, comprehensive_cost=300)
        self.assertIsNone(share(rows, cfg)['share_percent'])

    def test_zero_creative_is_zero_but_zero_parent_is_undefined(self):
        rows, cfg = fixture()
        rows[1]['metrics']['spend'] = 0
        self.assertEqual(share(rows, cfg)['share_percent'], 0)
        rows[0]['metrics']['spend'] = 0
        self.assertIsNone(share(rows, cfg)['share_percent'])

    def test_numerator_above_parent_is_conflict_without_clamping(self):
        rows, cfg = fixture()
        rows[1]['metrics']['spend'] = 1001
        self.assertEqual(share(rows, cfg)['status'], 'conflict')
        self.assertIsNone(share(rows, cfg)['share_percent'])

    def test_relationship_unknown_source_or_ledger_cannot_be_inferred(self):
        for mutate in (lambda r: r[1]['context'].update(creative_parent_verified=None),
                       lambda r: r[1]['context'].update(parent_plan_id=None),
                       lambda r: r[1]['context'].update(parent_source_key='different'),
                       lambda r: r[1]['context'].update(spend_ledger='unknown'),
                       lambda r: r[0]['context'].update(spend_ledger='base')):
            rows, cfg = fixture()
            mutate(rows)
            self.assertIsNone(share(rows, cfg)['share_percent'])

    def test_parent_cannot_be_taken_from_another_account_even_with_same_id_and_series(self):
        rows, cfg = fixture()
        cfg['accounts'].append({'id': '456', 'target_roi': None, 'roi_basis': 'overall_payment'})
        rows[0]['account_id'] = '456'
        self.assertIsNone(share(rows, cfg)['share_percent'])
        other, _ = fixture('456')
        all_rows = rows[1:]+other
        evaluation = analyze_monitoring(all_rows, cfg, NOW)
        own = next(i for i in evaluation['entities'] if i['account_id'] == '123')
        self.assertIsNone(own['creative_spend_share']['share_percent'])
        self.assertEqual(next(i for i in evaluation['entities'] if i['account_id'] == '456' and i['scope'] == 'creative')
                         ['creative_spend_share']['share_percent'], 25)

    def test_product_scene_kind_and_query_range_must_match(self):
        for mutate in (lambda r: r[0].update(promotion_type='standard'),
                       lambda r: r[0].update(scene='product'),
                       lambda r: r[0]['source'].update(kind='demo'),
                       lambda r: r[1]['context'].update(report_start_at=(NOW-timedelta(hours=1)).isoformat()),
                       lambda r: r[0]['context'].update(report_end_at='invalid')):
            rows, cfg = fixture()
            mutate(rows)
            self.assertIsNone(share(rows, cfg)['share_percent'])

    def test_exact_metric_time_required_without_using_capture_as_data_time(self):
        for dt in ((NOW-timedelta(seconds=1)).isoformat(), None):
            rows, cfg = fixture()
            rows[0]['source']['data_at'] = dt
            self.assertIsNone(share(rows, cfg)['share_percent'])

    def test_day_or_session_identity_cannot_cross_and_overnight_session_is_supported(self):
        rows, cfg = fixture()
        rows[0]['source']['metric_date'] = (NOW-timedelta(days=1)).date().isoformat()
        self.assertIsNone(share(rows, cfg)['share_percent'])
        now = NOW.replace(day=22, hour=0, minute=10)
        start = (now-timedelta(hours=2)).isoformat()
        rows, cfg = fixture()
        for row in rows:
            row['source'].update(metric_date=start[:10], metric_period='live_session', period_start=start,
                                 data_at=now.isoformat(), captured_at=now.isoformat())
        self.assertEqual(share(rows, cfg, now)['share_percent'], 25)
        self.assertTrue(share(rows, cfg, now)['is_current'])
        rows[0]['source']['period_start'] = (now-timedelta(hours=3)).isoformat()
        self.assertIsNone(share(rows, cfg, now)['share_percent'])

    def test_stale_ratio_is_retained_as_history_and_midnight_does_not_make_it_current(self):
        rows, cfg = fixture()
        value = share(rows, cfg, NOW+timedelta(hours=1))
        self.assertEqual(value['share_percent'], 25)
        self.assertEqual(value['status'], 'historical')
        for row in rows:
            row['source'].update(data_at=NOW.replace(hour=23, minute=59).isoformat(),
                                 captured_at=NOW.replace(hour=23, minute=59).isoformat())
        value = share(rows, cfg, NOW.replace(day=22, hour=0, minute=1))
        self.assertEqual(value['share_percent'], 25)
        self.assertFalse(value['is_current'])

    def test_latest_missing_clock_or_new_quality_error_never_reuses_older_good_parent(self):
        for mutate in (lambda p: p['source'].update(data_at=None),
                       lambda p: p['quality'].update(issues=['未知质量错误'])):
            rows, cfg = fixture()
            newer = copy.deepcopy(rows[0])
            newer['id'] = 'latest-parent'
            newer['source']['captured_at'] = (NOW+timedelta(seconds=1)).isoformat()
            mutate(newer)
            rows.append(newer)
            self.assertIsNone(share(rows, cfg, NOW+timedelta(seconds=1))['share_percent'])

    def test_duplicate_clock_cost_conflicts_and_counter_regressions_remain_unknown(self):
        for offset, cost in ((0, 1100), (-60, 1100)):
            rows, cfg = fixture()
            another = copy.deepcopy(rows[0])
            another.update(id='other-parent')
            another['source'].update(data_at=(NOW+timedelta(seconds=offset)).isoformat(),
                                     captured_at=(NOW+timedelta(seconds=offset)).isoformat())
            another['metrics']['spend'] = cost
            rows.append(another)
            value = share(rows, cfg)
            self.assertIsNone(value['share_percent'])
            self.assertEqual(value['status'], 'conflict')

    def test_duplicate_parent_identity_or_scope_is_not_selected_by_input_order(self):
        rows, cfg = fixture()
        duplicate = copy.deepcopy(rows[0])
        duplicate['context']['spend_ledger'] = 'base'
        for combined in (rows+[duplicate], [duplicate]+rows):
            self.assertIsNone(share(combined, cfg)['share_percent'])
        duplicate=copy.deepcopy(rows[0])
        duplicate['quality']['issues']=['同次采集未知错误']
        for combined in (rows+[duplicate], [duplicate]+rows):
            self.assertIsNone(share(combined, cfg)['share_percent'])

    def test_catalog_identifies_derived_evidence_without_accepting_a_supplied_percentage(self):
        rows, cfg = fixture()
        rows[1]['metrics']['creative_spend_share']=99
        inventory=coverage_report(rows,cfg,NOW)['accounts'][0]['metrics']
        value=next(v for v in inventory if v['field']=='creative_spend_share')
        self.assertTrue(value['derived'])
        self.assertFalse(value['supported_input'])
        self.assertEqual(value['data_status'],'fresh')
        self.assertEqual(value['evidence'][0]['value'],25)
        empty=coverage_report([],cfg,NOW)['accounts'][0]['metrics']
        self.assertEqual(next(v for v in empty if v['field']=='creative_spend_share')['data_status'],'missing')

    def test_import_boundary_clears_browser_and_legacy_link_certification(self):
        context = {'creative_parent_verified': True, 'parent_plan_id': 'p1', 'spend_ledger': 'overall'}
        for kind in ('browser_dom', 'legacy_db'):
            self.assertIsNone(normalize_context(context, kind)['creative_parent_verified'])
        self.assertTrue(normalize_context(context, 'import')['creative_parent_verified'])
        for ledger in (['overall'], True, 'comprehensive_cost'):
            with self.assertRaises(ValueError):
                normalize_context({**context, 'spend_ledger': ledger}, 'import')

    def test_reports_are_account_scoped_and_ai_uses_aliases_and_controlled_fields(self):
        rows, cfg = fixture()
        other, _ = fixture('456')
        for row in other:
            row['entity_id'] = 'foreign-'+row['entity_id']
            row['source']['series_key'] = 'foreign-'+row['source']['series_key']
            if row['scope'] == 'plan': row['context']['plan_id'] = 'foreign-private-parent'
            else: row['context'].update(parent_plan_id='foreign-private-parent', parent_source_key='foreign-private-parent-report')
        cfg['accounts'].append({'id': '456', 'roi_basis': 'overall_payment', 'target_roi': None})
        all_rows = rows+other
        evaluation = evaluate_all(all_rows, cfg, NOW)
        report = build_report(all_rows, cfg, evaluation, '123', NOW)
        self.assertEqual(report['evidence_and_risks']['creative_spend_shares'][0]['share_percent'], 25)
        self.assertNotIn('foreign', json.dumps(report))
        self.assertIn('单素材消耗占比', report_markdown(report))
        package = make_package(all_rows, evaluation)
        self.assertEqual(len(package['creative_spend_shares']), 2)
        self.assertNotIn('private-', json.dumps(package))
        self.assertNotIn('foreign-', json.dumps(package))
        self.assertFalse(package['creative_spend_shares'][0]['used_for_action'])
        self.assertFalse(evaluation['coverage']['execution_enabled'])


if __name__ == '__main__':
    unittest.main()
