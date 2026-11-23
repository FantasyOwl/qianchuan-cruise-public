import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from monitor.plan_readback import compare_supplied_readback
from monitor.submission import AttemptLedger
from test_engine import NOW


PLAN = '8012367229158357'


def inputs(action='budget_up'):
    query = {'account_id': '123', 'marketing_goal': 'LIVE_PROM_GOODS',
             'request_started_at': (NOW-timedelta(minutes=2)).isoformat(),
             'received_at': (NOW-timedelta(minutes=1)).isoformat()}
    attempt = {'created_at': (NOW-timedelta(minutes=3)).isoformat(),
               'context': {'account_id': '123', 'entity_id': PLAN, 'action': action,
                           'marketing_goal': 'LIVE_PROM_GOODS', 'before': 1000, 'after': 1200}}
    info = {'id': int(PLAN), 'marketing_goal': 'LIVE_PROM_GOODS', 'budget': 1200,
            'budget_mode': 'BUDGET_MODE_DAY', 'opt_status': 'ENABLE', 'status': 'DELIVERY_OK'}
    if action in {'pause_plan', 'resume_plan'}:
        attempt['context'].update(before=action == 'pause_plan', after=action == 'resume_plan')
        info['opt_status'] = 'DISABLE' if action == 'pause_plan' else 'ENABLE'
    response = {'code': 0, 'data': {'ad_list': [{'ad_info': info}],
                                 'page_info': {'page': 1, 'page_size': 10, 'total_page': 3}}}
    return response, query, attempt


def compare(response, query=None, attempt=None):
    _, default_query, default_attempt = inputs()
    return compare_supplied_readback(json.dumps(response), query or default_query, attempt or default_attempt, NOW)


class PlanReadbackTests(unittest.TestCase):
    def test_exact_large_id_and_decimal_budget_match_without_claiming_execution(self):
        raw, query, attempt = inputs()
        raw['data']['ad_list'][0]['ad_info']['budget'] = '1200.00'
        result = compare(raw, query, attempt)
        self.assertEqual(result['status'], 'matches_requested_value')
        self.assertEqual(result['entity_id'], PLAN)
        self.assertEqual(result['observed'], '1200.00')
        for key in ('provenance_verified', 'platform_state_observed', 'actual_delivery_confirmed',
                    'execution_confirmed', 'can_release_hold', 'automatic_retry'):
            self.assertIs(result[key], False)

    def test_mismatching_budget_is_difference_not_failed_submission(self):
        raw, query, attempt = inputs()
        raw['data']['ad_list'][0]['ad_info']['budget'] = 1000
        result = compare(raw, query, attempt)
        self.assertEqual(result['status'], 'differs_requested_value')
        self.assertNotIn('business_success', result)

    def test_switch_is_not_actual_delivery_and_pause_direction_is_strict(self):
        for action in ('pause_plan', 'resume_plan'):
            raw, query, attempt = inputs(action)
            raw['data']['ad_list'][0]['ad_info']['status'] = 'OFFLINE_BALANCE'
            result = compare(raw, query, attempt)
            self.assertEqual(result['status'], 'matches_requested_value')
            self.assertFalse(result['actual_delivery_confirmed'])
            attempt['context']['after'] = int(attempt['context']['after'])
            self.assertEqual(compare(raw, query, attempt)['status'], 'inconclusive')

    def test_special_disable_is_not_silently_normalized_to_requested_disable(self):
        raw, query, attempt = inputs('pause_plan')
        raw['data']['ad_list'][0]['ad_info']['opt_status'] = 'ROI2_DISABLE'
        result = compare(raw, query, attempt)
        self.assertEqual(result['status'], 'differs_requested_value')
        self.assertEqual(result['observed'], 'ROI2_DISABLE')

    def test_missing_plan_on_partial_page_does_not_establish_absence(self):
        raw, query, attempt = inputs()
        raw['data']['ad_list'] = []
        result = compare(raw, query, attempt)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertIn('不能推断', result['blockers'][0])

    def test_account_goal_and_old_missing_goal_are_not_borrowed(self):
        raw, query, attempt = inputs()
        other = {**query, 'account_id': '456'}
        with self.assertRaises(ValueError): compare(raw, other, attempt)
        for modify in (lambda q,a,r: a['context'].pop('marketing_goal'),
                       lambda q,a,r: q.update(marketing_goal='VIDEO_PROM_GOODS'),
                       lambda q,a,r: r['data']['ad_list'][0]['ad_info'].update(marketing_goal='VIDEO_PROM_GOODS')):
            r,q,a = copy.deepcopy((raw,query,attempt));modify(q,a,r)
            self.assertEqual(compare(r,q,a)['status'], 'inconclusive')

    def test_duplicate_or_lossy_ids_are_rejected_not_selected(self):
        raw, query, attempt = inputs()
        bad = copy.deepcopy(raw);bad['data']['ad_list'] *= 2
        with self.assertRaises(ValueError): compare(bad,query,attempt)
        for identity in (float(PLAN), True, '83213982241676983', '0', None):
            bad = copy.deepcopy(raw);bad['data']['ad_list'][0]['ad_info']['id'] = identity
            with self.assertRaises(ValueError): compare(bad,query,attempt)

    def test_duplicate_json_keys_and_credentials_anywhere_are_rejected(self):
        raw, query, attempt = inputs()
        texts = ['{"code":0,"code":1,"data":{"ad_list":[]}}',
                 '{"code":0,"data":{"ad_list":[],"nested":{"cookie":"private-value"}}}',
                 json.dumps({**raw, 'access_token':'private-value'}),
                 '{"code":0,"data":{"ad_list":[],"x":NaN}}']
        for text in texts:
            with self.assertRaises(ValueError) as caught:
                compare_supplied_readback(text,query,attempt,NOW)
            self.assertNotIn('private-value', str(caught.exception))

    def test_failed_or_incomplete_envelopes_are_rejected(self):
        for response in ({'code':False,'data':{'ad_list':[]}}, {'code':1}, {'code':0,'data':{}},
                         {'code':0,'data':{'ad_list':[{}]}}, []):
            with self.assertRaises(ValueError): compare(response)

    def test_budget_modes_and_missing_or_overprecise_amounts_are_inconclusive(self):
        for fields in ({'budget_mode':'BUDGET_MODE_TOTAL'}, {'budget_mode':'BUDGET_MODE_INFINITE'},
                       {'budget':None}, {'budget':True}, {'budget':'1200.001'}, {'budget':'NaN'},
                       {'budget':0}, {'budget':-1}):
            raw, query, attempt = inputs();raw['data']['ad_list'][0]['ad_info'].update(fields)
            self.assertEqual(compare(raw,query,attempt)['status'], 'inconclusive')

    def test_old_sdk_stats_and_names_are_never_retained_or_used(self):
        raw, query, attempt = inputs()
        raw['data']['ad_list'][0].update(stats_info={'stat_cost':99999})
        raw['data']['ad_list'][0]['ad_info'].update(name='private-name', modify_time='2026-01-01 00:00:00')
        result = compare(raw,query,attempt)
        text = json.dumps(result)
        self.assertNotIn('private-name',text);self.assertNotIn('99999',text)
        self.assertNotIn('modify_time',text)
        self.assertEqual(result['status'],'matches_requested_value')

    def test_clock_order_age_and_query_metadata_cannot_be_ambiguous(self):
        raw, query, attempt = inputs()
        for fields in ({'request_started_at':(NOW-timedelta(minutes=4)).isoformat()},
                       {'received_at':(NOW+timedelta(seconds=1)).isoformat()},
                       {'received_at':'2026-10-21T19:59:00'},
                       {'marketing_goal':[]}, {'fields':['stats_info']}):
            with self.assertRaises(ValueError): compare(raw,{**query,**fields},attempt)
        old = copy.deepcopy(query)
        old.update(request_started_at=(NOW-timedelta(hours=1,minutes=2)).isoformat(),
                   received_at=(NOW-timedelta(hours=1)).isoformat())
        attempt['created_at']=(NOW-timedelta(hours=2)).isoformat()
        with self.assertRaises(ValueError): compare(raw,old,attempt)

    def test_oversize_and_unknown_switch_do_not_produce_confirmation(self):
        raw, query, attempt = inputs('pause_plan')
        with self.assertRaises(ValueError):compare_supplied_readback(' '*1048577,query,attempt,NOW)
        raw['data']['ad_list'][0]['ad_info']['opt_status']=['DISABLE']
        self.assertEqual(compare(raw,query,attempt)['status'],'inconclusive')


class ReadbackLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'attempts.db'
        self.ledger=AttemptLedger(self.path)

    def test_matching_review_is_durable_idempotent_and_never_unlocks_unknown(self):
        raw, query, attempt = inputs()
        self.ledger.claim('one','digest',NOW-timedelta(minutes=3),attempt['context'])
        self.ledger.finish('one','unknown',{'state':'unknown','platform_state_observed':False})
        with self.ledger.connect() as db:
            before=db.execute('SELECT * FROM submission_attempts').fetchall()
        result=self.ledger.review_supplied_readback('one',json.dumps(raw),query,NOW)
        self.assertEqual(result['status'],'matches_requested_value')
        restarted=AttemptLedger(self.path)
        self.assertEqual(restarted.review_supplied_readback('one',json.dumps(raw),query,NOW+timedelta(minutes=1)),result)
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT * FROM submission_attempts').fetchall(),before)
            self.assertEqual(db.execute('SELECT count(*) FROM supplied_readback_reviews').fetchone()[0],1)
        with self.assertRaisesRegex(ValueError,'未明确'):
            restarted.claim('next','next-digest',NOW+timedelta(hours=2),attempt['context'])

    def test_rejected_review_rolls_back_without_raw_secrets_or_missing_attempt(self):
        raw, query, attempt = inputs()
        self.ledger.claim('one','digest',NOW-timedelta(minutes=3),attempt['context'])
        for attempt_id,response in (('missing',json.dumps(raw)),('one',json.dumps({**raw,'token':'private-value'}))):
            with self.assertRaises(ValueError):self.ledger.review_supplied_readback(attempt_id,response,query,NOW)
        with self.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM supplied_readback_reviews').fetchone()[0],0)
        self.assertNotIn(b'private-value',self.path.read_bytes())


if __name__=='__main__':unittest.main()
