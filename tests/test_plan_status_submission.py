import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from monitor.actuation import interpret_status_response, prepare_action
from monitor.evaluation import evaluate_all
from monitor.submission import (AttemptLedger, BudgetClient, BudgetPermit,
                                PlanStatusClient, PlanStatusPermit, _post_status)
from test_engine import NOW, settings
from test_strategies import ready_rows
from test_submission import fixture as budget_fixture


PLAN = '8012367229158357'


def pause_fixture(account='123'):
    cfg = settings()
    cfg['accounts'][0]['id'] = account
    rows = ready_rows(roi=0)
    for row in rows:
        row.update(account_id=account, entity_id=PLAN, id=f"{account}-{row['id']}")
        row['context']['plan_id'] = PLAN
        # A missing budget blocks budget adjustment but does not invent a
        # dependency for an otherwise eligible, explicitly reviewed pause.
        row['metrics'].update(orders=0, budget=None)
    proposal = next(p for p in evaluate_all(rows, cfg, NOW)['recommendations'] if p['action'] == 'pause_plan')
    assert proposal['eligible'], proposal['blockers']
    preview = prepare_action(proposal, rows[-1], cfg, NOW)
    permit = PlanStatusPermit(account, preview['digest'], NOW, NOW+timedelta(minutes=2), 'isolated-test-review')
    return rows, cfg, proposal, permit


def status_ok(body, token, timeout):
    return {'code': 0, 'data': {'results': [{'ad_id': json.loads(body)['ad_ids'][0], 'flag': True}]}}


class StatusResponseTests(unittest.TestCase):
    def test_boolean_results_preserve_partial_and_missing_objects(self):
        value = {'code': 0, 'data': {'results': [{'ad_id': 1, 'flag': True}, {'ad_id': 2, 'flag': False}]}}
        result = interpret_status_response(value, ['1', '2', '3'])
        self.assertEqual((result['confirmed'], result['failed'], result['unknown']), (['1'], ['2'], ['3']))
        self.assertFalse(result['success'])
        self.assertFalse(result['executed'])

    def test_empty_code_zero_and_budget_result_do_not_confirm_status(self):
        for value in ({'code': 0, 'data': {}}, {'code': 0, 'data': {'results': []}},
                      {'code': 0, 'data': {'results': [{'ad_id': 1, 'status': 'SUCCESS'}]}}):
            self.assertEqual(interpret_status_response(value, ['1'])['unknown'], ['1'])

    def test_truthy_values_duplicate_rows_and_error_conflicts_remain_unknown(self):
        cases = [[{'ad_id': 1, 'flag': flag}] for flag in (1, 'true', None)]
        cases += [[{'ad_id': 1, 'flag': True}, {'ad_id': 1, 'flag': True}],
                  [{'ad_id': 1, 'flag': True, 'error': {'error_code': 123}}]]
        for rows in cases:
            self.assertEqual(interpret_status_response({'code': 0, 'data': {'results': rows}}, ['1'])['unknown'], ['1'])

    def test_unexpected_malformed_or_float_ids_cannot_certify_another_request(self):
        for row in ({'ad_id': 2, 'flag': True}, {'ad_id': 1.0, 'flag': True},
                    {'ad_id': True, 'flag': True}, {'flag': True}, None):
            value = {'code': 0, 'data': {'results': [{'ad_id': 1, 'flag': True}, row]}}
            self.assertEqual(interpret_status_response(value, ['1'])['unknown'], ['1'])

    def test_exact_large_id_and_business_failure_are_distinct(self):
        result = interpret_status_response({'code': 0, 'data': {'results': [{'ad_id': int(PLAN), 'flag': True}]}}, [PLAN])
        self.assertTrue(result['success'])
        self.assertEqual(result['confirmed'], [PLAN])
        self.assertEqual(interpret_status_response({'code': 100, 'message': 'raw-secret'}, [PLAN])['failed'], [PLAN])
        self.assertEqual(interpret_status_response({'code': True}, [PLAN])['unknown'], [PLAN])


class PlanStatusSubmissionTests(unittest.TestCase):
    def setUp(self):
        clock = patch('monitor.submission.now_cn', return_value=NOW)
        clock.start();self.addCleanup(clock.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'attempts.db'
        self.ledger = AttemptLedger(self.path)

    def test_default_adapter_never_reads_credentials_or_sends(self):
        rows, cfg, proposal, permit = pause_fixture()
        token, transport = Mock(), Mock()
        with self.assertRaises(PermissionError):
            PlanStatusClient(self.ledger, transport=transport).dispatch('disabled', proposal['id'], rows, cfg, permit, token, NOW)
        token.assert_not_called()
        transport.assert_not_called()
        with self.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM submission_attempts').fetchone()[0], 0)

    def test_pause_is_recomputed_and_audited_without_asserting_observed_state(self):
        rows, cfg, proposal, permit = pause_fixture()
        calls = []
        def transport(body, token, timeout):
            calls.append(json.loads(body))
            return status_ok(body, token, timeout)
        result = PlanStatusClient(self.ledger, enable_network=True, transport=transport).dispatch(
            'reviewed-pause', proposal['id'], rows, cfg, permit, lambda: 'test-secret', NOW)
        self.assertEqual(calls, [{'advertiser_id': 123, 'ad_ids': [int(PLAN)], 'opt_status': 'DISABLE'}])
        self.assertEqual(result['state'], 'confirmed')
        self.assertFalse(result['platform_state_observed'])
        with self.ledger.connect() as db:
            context, result_json = db.execute('SELECT context_json,result_json FROM submission_attempts').fetchone()
        evidence = json.loads(context)
        self.assertEqual((evidence['account_id'], evidence['entity_id'], evidence['before'], evidence['after']),
                         ('123', PLAN, True, False))
        self.assertNotIn('test-secret', context+result_json)
        self.assertNotIn('isolated-test-review', context+result_json)

    def test_account_digest_permit_type_and_expiry_reject_before_token(self):
        rows, cfg, proposal, permit = pause_fixture()
        invalid = [BudgetPermit(permit.account_id, permit.request_digest, permit.issued_at, permit.expires_at, 'test'),
                   PlanStatusPermit('456', permit.request_digest, NOW, permit.expires_at, 'test'),
                   PlanStatusPermit('123', 'other-request', NOW, permit.expires_at, 'test'),
                   PlanStatusPermit('123', permit.request_digest, NOW-timedelta(minutes=6), NOW, 'test'),
                   {'approved': True}]
        token, transport = Mock(), Mock()
        for value in invalid:
            with self.assertRaises(PermissionError):
                PlanStatusClient(self.ledger, enable_network=True, transport=transport).dispatch(
                    'invalid-review', proposal['id'], rows, cfg, value, token, NOW)
        token.assert_not_called()
        transport.assert_not_called()

    def test_paused_unknown_primary_or_changed_target_invalidates_review(self):
        rows, cfg, proposal, permit = pause_fixture()
        mutations = [lambda r, c: r[-1]['control'].update(running=False),
                     lambda r, c: r[-1]['control'].update(running=None),
                     lambda r, c: r[-1]['context'].update(is_primary_plan=True),
                     lambda r, c: c['accounts'][0].update(target_roi=None)]
        token, transport = Mock(), Mock()
        for mutate in mutations:
            r, c = copy.deepcopy((rows, cfg))
            mutate(r, c)
            with self.assertRaises(ValueError):
                PlanStatusClient(self.ledger, enable_network=True, transport=transport).dispatch(
                    'changed-evidence', proposal['id'], r, c, permit, token, NOW)
        token.assert_not_called()
        transport.assert_not_called()

    def test_timeout_blocks_later_different_requests_until_reconciled(self):
        rows, cfg, proposal, permit = pause_fixture()
        transport = Mock(side_effect=TimeoutError('test-secret'))
        client = PlanStatusClient(self.ledger, enable_network=True, transport=transport)
        result = client.dispatch('uncertain-pause', proposal['id'], rows, cfg, permit, lambda: 'test-secret', NOW)
        self.assertEqual(result['state'], 'unknown')
        self.assertNotIn('test-secret', json.dumps(result))
        # Even another kind of request, after the cooldown, is blocked for this
        # same object when no independent reconciliation has resolved the IO.
        with self.assertRaisesRegex(ValueError, '未明确'):
            AttemptLedger(self.path).claim('other-action', 'other-digest', NOW+timedelta(hours=2),
                                           {'account_id': '123', 'entity_id': PLAN})
        self.assertEqual(transport.call_count, 1)

    def test_budget_and_pause_share_same_object_cooldown(self):
        rows, cfg, proposal, permit = budget_fixture()
        BudgetClient(self.ledger, enable_network=True, transport=lambda *a: {
            'code': 0, 'data': {'results': [{'ad_id': int(PLAN), 'status': 'SUCCESS'}]}}).dispatch(
                'budget-first', proposal['id'], rows, cfg, permit, lambda: 'test', NOW)
        rows, cfg, proposal, permit = pause_fixture(permit.account_id)
        transport = Mock()
        with self.assertRaisesRegex(ValueError, '一小时'):
            PlanStatusClient(self.ledger, enable_network=True, transport=transport).dispatch(
                'pause-too-soon', proposal['id'], rows, cfg, permit, lambda: 'test', NOW)
        transport.assert_not_called()

    def test_same_plan_id_in_two_accounts_remains_independent(self):
        client = PlanStatusClient(self.ledger, enable_network=True, transport=status_ok)
        for account in ('123', '456'):
            rows, cfg, proposal, permit = pause_fixture(account)
            result = client.dispatch('account-'+account, proposal['id'], rows, cfg, permit, lambda: 'test', NOW)
            self.assertEqual(result['state'], 'confirmed')
        with self.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM submission_attempts').fetchone()[0], 2)

    def test_competing_digests_cannot_both_claim_same_plan(self):
        barrier = threading.Barrier(2)
        def claim(number):
            ledger = AttemptLedger(self.path)
            barrier.wait(timeout=5)
            try:
                ledger.claim('attempt-'+str(number), 'digest-'+str(number), NOW,
                             {'account_id': '123', 'entity_id': PLAN})
                return 'claimed'
            except ValueError:
                return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(claim, [1, 2])), ['blocked', 'claimed'])

    def test_stricter_recorded_cooldown_and_exact_boundary_survive_restart(self):
        context = {'account_id': '123', 'entity_id': PLAN, 'cooldown_seconds': 7200}
        self.ledger.claim('first', 'first-digest', NOW, context)
        self.ledger.finish('first', 'confirmed', {'business_success': True})
        restarted = AttemptLedger(self.path)
        current = {'account_id': '123', 'entity_id': PLAN, 'cooldown_seconds': 3600}
        with self.assertRaisesRegex(ValueError, '一小时'):
            restarted.claim('too-soon', 'second-digest', NOW+timedelta(seconds=7199), current)
        restarted.claim('at-boundary', 'second-digest', NOW+timedelta(seconds=7200), current)
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM submission_attempts').fetchone()[0], 2)

    def test_status_transport_uses_fixed_official_endpoint_and_no_redirect(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"code":0}'
        opener = Mock()
        opener.open.return_value = response
        with patch('monitor.submission.urllib.request.build_opener', return_value=opener) as build:
            _post_status('{"advertiser_id":123}', 'test', 10)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.oceanengine.com/open_api/v1.0/qianchuan/uni_promotion/ad/status/update/')
        self.assertEqual(request.method, 'POST')
        self.assertIsNone(build.call_args.args[1].redirect_request(None, None, None, None, None, None))


class PlanStatusPreviewTests(unittest.TestCase):
    def test_direction_and_current_boolean_state_must_match(self):
        rows, cfg, proposal, _ = pause_fixture()
        for before, after, running in ((None, False, True), (True, True, True), (1, False, True),
                                       (True, False, False), (True, False, None)):
            p, row = copy.deepcopy((proposal, rows[-1]))
            p.update(before=before, after=after)
            row['control']['running'] = running
            self.assertFalse(prepare_action(p, row, cfg, NOW)['supported_for_preview'])

    def test_enable_contract_does_not_create_a_resume_strategy(self):
        rows, cfg, proposal, _ = pause_fixture()
        p, row = copy.deepcopy((proposal, rows[-1]))
        p.update(action='resume_plan', before=False, after=True)
        row['control']['running'] = False
        preview = prepare_action(p, row, cfg, NOW)
        self.assertEqual(json.loads(preview['request']['body_json'])['opt_status'], 'ENABLE')
        self.assertFalse(preview['live_execution_allowed'])
        self.assertFalse(any(p['action'] == 'resume_plan' and p['eligible']
                             for p in evaluate_all(rows, cfg, NOW)['recommendations']))


if __name__ == '__main__':
    unittest.main()
