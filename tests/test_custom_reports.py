import copy
import json
import unittest
from datetime import timedelta

from monitor.custom_reports import (review_custom_reports, FORMAT, CONFIG_ENDPOINT, DATA_ENDPOINT)
from test_engine import NOW, settings


def fixture(account='123', scene='product', count=1, pages=1):
    topic = 'SITE_PROMOTION_PRODUCT_AD' if scene == 'product' else 'SITE_PROMOTION_POST_DATA_VIDEO'
    return {'format': FORMAT, 'captured_at': NOW.isoformat(), 'data_at': None,
        'configuration': {'endpoint': CONFIG_ENDPOINT,
            'query': {'advertiser_id': account, 'data_topics': [topic]},
            'response': {'code': 0, 'message': 'PRIVATE_MESSAGE', 'request_id': 'PRIVATE_REQUEST_ID', 'data': {
                'custom_config_datas': [{'data_topic': topic,
                    'dimensions': [{'field': 'object_id', 'name': '测试对象维度', 'is_required': True, 'sortable': True}],
                    'metrics': [{'field': 'test_amount', 'name': '测试金额', 'unit': 2}],
                    'query_limit': {'max_dimension_limit': '2', 'max_day': '180'}}]}}},
        'query': {'advertiser_id': account, 'data_topic': topic, 'dimensions': ['object_id'], 'metrics': ['test_amount'],
                  'filters': [], 'order_by': [{'field': 'object_id', 'type': 1}], 'page_size': 10,
                  'start_time': NOW.replace(hour=0, minute=0, second=0).isoformat(), 'end_time': NOW.isoformat()},
        'pages': [{'endpoint': DATA_ENDPOINT, 'page': p, 'response': {'code': 0, 'request_id': 'PRIVATE_REQUEST_ID', 'data': {
            'rows': [{'dimensions': {'object_id': {'opaque_identifier': str(8012367229158357+i)}},
                      'metrics': {'test_amount': {'opaque_amount': 0}}} for i in range((p-1)*10, min(count, p*10))],
            'page_info': {'page': p, 'page_size': 10, 'total_page': (count+9)//10, 'total_number': count}}}}
            for p in range(1, pages+1)]}


def cfg():
    c = settings()
    c['accounts'][0]['scene'] = 'product'
    return c


def review(p=None):
    return review_custom_reports(p if p is not None else fixture(), cfg(), NOW)


def config(p):
    return p['configuration']['response']['data']['custom_config_datas'][0]


class CustomReportTests(unittest.TestCase):
    def test_raw_nested_objects_units_and_large_ids_have_no_inferred_metrics(self):
        out = review()
        self.assertEqual(out['rows'][0]['cells']['object_id'], '{"opaque_identifier":"8012367229158357"}')
        self.assertEqual(out['rows'][0]['cells']['test_amount'], '{"opaque_amount":0}')
        self.assertEqual(out['columns'][1]['unit'], '分')
        self.assertEqual(out['normalized_snapshots'], 0)
        for key in ('persisted', 'executed', 'ai_sent', 'platform_verified', 'inventory_complete'):
            self.assertIs(out[key], False)
        self.assertNotIn('PRIVATE_', json.dumps(out))
        self.assertNotIn('snapshots', out)

    def test_json_text_preserves_integer_and_decimal_precision(self):
        text = json.dumps(fixture()).replace('"opaque_amount": 0', '"opaque_amount": 0.1234567890123456789012345')
        text = text.replace('"8012367229158357"', '8012367229158357')
        out = review({'document_text': text})
        self.assertIn('0.1234567890123456789012345', out['rows'][0]['cells']['test_amount'])
        self.assertIn('8012367229158357', out['rows'][0]['cells']['object_id'])

    def test_missing_metric_is_not_zero_and_zero_is_retained(self):
        p = fixture(count=2)
        p['pages'][0]['response']['data']['rows'][1]['metrics'] = {}
        out = review(p)
        self.assertEqual(out['missing_cells'], 1)
        self.assertIsNone(out['rows'][1]['cells']['test_amount'])
        self.assertIn(':0', out['rows'][0]['cells']['test_amount'])

    def test_partial_and_exhausted_pages_do_not_certify_inventory(self):
        partial = review(fixture(count=11))
        full = review(fixture(count=11, pages=2))
        self.assertFalse(partial['pagination_exhausted'])
        self.assertTrue(full['pagination_exhausted'])
        self.assertEqual(full['received_rows'], 11)
        self.assertFalse(full['inventory_complete'])

    def test_empty_result_supports_both_empty_page_conventions(self):
        for total in (0, 1):
            p = fixture(count=0)
            p['pages'][0]['response']['data']['page_info']['total_page'] = total
            out = review(p)
            self.assertEqual(out['received_rows'], 0)
            self.assertTrue(out['pagination_exhausted'])

    def test_duplicate_conflicting_or_reordered_pages_rejected(self):
        for mode in ('duplicate', 'drift', 'reorder', 'wrong_size', 'wrong_count'):
            with self.subTest(mode=mode):
                p = fixture(count=11, pages=2)
                data = p['pages'][1]['response']['data']
                if mode == 'duplicate': data['rows'][0] = copy.deepcopy(p['pages'][0]['response']['data']['rows'][0])
                if mode == 'drift': data['page_info']['total_number'] = 12
                if mode == 'reorder': p['pages'].reverse()
                if mode == 'wrong_size': data['page_info']['page_size'] = 20
                if mode == 'wrong_count': data['rows'] = []
                with self.assertRaises(ValueError): review(p)

    def test_account_topic_and_period_cannot_be_substituted(self):
        for mode in ('account', 'config_account', 'topic', 'scene', 'period', 'unhashable_period'):
            with self.subTest(mode=mode):
                p = fixture()
                if mode == 'account': p['query']['advertiser_id'] = '999'
                if mode == 'config_account': p['configuration']['query']['advertiser_id'] = '999'
                if mode == 'topic': p['query']['data_topic'] = 'unknown'
                if mode == 'scene': p = fixture(scene='live')
                if mode == 'period': p['query']['data_period'] = 'UNI_DATA'
                if mode == 'unhashable_period': p['query']['data_period'] = {}
                with self.assertRaises(ValueError): review(p)

    def test_overall_period_and_configuration_must_match(self):
        p = fixture(); topic = 'OVERALL_ROI_PRODUCT_PROJECT'
        p['query'].update(data_topic=topic, data_period='UNI_DATA')
        p['configuration']['query'].update(data_topics=[topic], data_period='UNI_DATA')
        config(p)['data_topic'] = topic
        self.assertEqual(review(p)['query']['data_period'], 'UNI_DATA')
        del p['configuration']['query']['data_period']
        with self.assertRaises(ValueError): review(p)

    def test_disabled_or_duplicate_registry_account_rejected(self):
        for mode in ('off', 'duplicate'):
            c = cfg()
            if mode == 'off': c['accounts'][0]['observe'] = False
            else: c['accounts'].append(copy.deepcopy(c['accounts'][0]))
            with self.assertRaises(ValueError): review_custom_reports(fixture(), c, NOW)

    def test_lossy_and_noncanonical_account_ids_rejected(self):
        for value in (123.0, True, '0123', '1e2', str(2**64), {}, None):
            p = fixture(); p['query']['advertiser_id'] = value
            with self.assertRaises(ValueError): review(p)

    def test_configuration_duplicate_required_filter_only_and_exclusion_rejected(self):
        for mode in ('duplicate', 'required', 'filter_only', 'exclusion', 'limit', 'bool'):
            with self.subTest(mode=mode):
                p = fixture(); c = config(p)
                if mode == 'duplicate': c['dimensions'].append(copy.deepcopy(c['dimensions'][0]))
                if mode == 'required': c['dimensions'].append({'field': 'required_id', 'name': '必选', 'is_required': True})
                if mode == 'filter_only': c['dimensions'][0]['filter_only'] = True
                if mode == 'exclusion': c['metrics'][0]['exclusion_dims'] = ['object_id']
                if mode == 'limit': c['query_limit']['max_dimension_limit'] = '0'
                if mode == 'bool': c['dimensions'][0]['is_required'] = 'false'
                with self.assertRaises(ValueError): review(p)

    def test_unknown_fields_units_flat_rows_and_scalar_cells_are_not_guessed(self):
        for mode in ('field', 'unit', 'flat', 'cell', 'extra', 'dimension'):
            with self.subTest(mode=mode):
                p = fixture(); row = p['pages'][0]['response']['data']['rows'][0]
                if mode == 'field': p['query']['metrics'] = ['unknown']
                if mode == 'unit': config(p)['metrics'][0]['unit'] = 999
                if mode == 'flat': p['pages'][0]['response']['data']['rows'][0] = {'spend': 100}
                if mode == 'cell': row['metrics']['test_amount'] = 100
                if mode == 'extra': row['metrics']['unrequested'] = {'value': 100}
                if mode == 'dimension': row['dimensions'] = {}
                with self.assertRaises(ValueError): review(p)

    def test_all_documented_units_are_retained_without_scaling(self):
        for unit, label in [(0, '无单位'), (1, '千分之一分'), (2, '分'), (3, '元'), (4, '个'), (10, '百分比')]:
            p = fixture(); config(p)['metrics'][0]['unit'] = unit
            self.assertEqual(review(p)['columns'][1]['unit'], label)
            self.assertIn(':0', review(p)['rows'][0]['cells']['test_amount'])

    def test_filters_and_sorting_require_explicit_configuration(self):
        p = fixture(); d = config(p)['dimensions'][0]
        d.update(filterable=True, filter_config={'operator': 7, 'filter_required': True, 'value_limit': 1})
        with self.assertRaises(ValueError): review(p)
        p['query']['filters'] = [{'field': 'object_id', 'operator': 7, 'values': ['123']}]
        self.assertEqual(len(review(p)['rows']), 1)
        for value in ([{}], ['a', 'b'], ['a', 'a']):
            bad = copy.deepcopy(p); bad['query']['filters'][0]['values'] = value
            with self.assertRaises(ValueError): review(bad)
        d['sortable'] = False
        with self.assertRaises(ValueError): review(p)

    def test_declared_clocks_are_separate_and_missing_clock_not_fabricated(self):
        out = review(); self.assertIsNone(out['data_at'])
        self.assertEqual(out['captured_at'], NOW.isoformat())
        p = fixture(); p['data_at'] = NOW.isoformat()
        self.assertEqual(review(p)['data_at'], NOW.isoformat())
        for value in ((NOW-timedelta(days=1)).isoformat(), {}, True):
            p['data_at'] = value
            with self.assertRaises(ValueError): review(p)

    def test_credentials_anywhere_duplicate_keys_and_nonfinite_json_rejected(self):
        p = fixture(); p['configuration']['response']['authorization'] = 'never echo this'
        with self.assertRaises(ValueError) as c: review(p)
        self.assertNotIn('never echo this', str(c.exception))
        raw = json.dumps(fixture())
        for text in (raw.replace('"code": 0', '"code": 0, "code": 0', 1),
                     raw.replace('"opaque_amount": 0', '"opaque_amount": NaN', 1)):
            with self.assertRaises(ValueError): review({'document_text': text})

    def test_business_errors_and_wrong_endpoints_rejected(self):
        for mode in ('config_code', 'data_code', 'config_endpoint', 'data_endpoint'):
            p = fixture()
            if mode == 'config_code': p['configuration']['response']['code'] = 1
            if mode == 'data_code': p['pages'][0]['response']['code'] = 1
            if mode == 'config_endpoint': p['configuration']['endpoint'] = DATA_ENDPOINT
            if mode == 'data_endpoint': p['pages'][0]['endpoint'] = '/list/'
            with self.assertRaises(ValueError): review(p)

    def test_review_is_deterministic_and_has_no_input_mutation(self):
        p = fixture(); before = copy.deepcopy(p)
        self.assertEqual(review(p), review(p))
        self.assertEqual(p, before)


if __name__ == '__main__':
    unittest.main()
