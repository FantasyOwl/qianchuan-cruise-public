"""Exercise the same temporal/data gates for every disconnected writer."""
import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from monitor.actuation import prepare_action
from monitor.evaluation import evaluate_all
from monitor.submission import (AttemptLedger, BudgetClient, PlanStatusClient,
    RoiTargetClient, ControlBudgetClient, ControlStatusClient)
from test_submission import fixture as budget_fixture
from test_plan_status_submission import pause_fixture
from test_roi_submission import dispatch_fixture as roi_fixture
from test_control_submission import fixture as control_fixture, task_response


def case(kind):
    if kind == 'budget':
        rows, cfg, proposal, permit = budget_fixture()
        client = BudgetClient
        response = {'code': 0, 'data': {'results': [{'ad_id': int(proposal['entity_id']), 'status': 'SUCCESS'}]}}
    elif kind == 'status':
        rows, cfg, proposal, permit = pause_fixture()
        client = PlanStatusClient
        response = {'code': 0, 'data': {'results': [{'ad_id': int(proposal['entity_id']), 'flag': True}]}}
    elif kind == 'roi':
        rows, cfg, proposal, permit = roi_fixture()
        client = RoiTargetClient
        response = {'code': 0, 'data': {'results': [{'ad_id': int(proposal['entity_id']), 'status': 'SUCCESS'}]}}
    else:
        pause = kind == 'control_status'
        rows, cfg, _, proposal, _, permit = control_fixture(pause)
        client = ControlStatusClient if pause else ControlBudgetClient
        response = task_response(pause)
    return rows, cfg, proposal, permit, client, response


KINDS = ('budget', 'status', 'roi', 'control_budget', 'control_status')


class SubmissionRecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.number = 0

    def setup_case(self, kind):
        self.number += 1
        self.path = Path(self.temp.name)/f'{self.number}.db'
        self.ledger = AttemptLedger(self.path)
        self.rows, self.cfg, self.proposal, self.permit, self.client_type, response = case(kind)
        self.at = self.permit.issued_at
        self.transport = Mock(return_value=response)
        self.token = Mock(return_value='FAKE_PRIVATE_TOKEN')
        return self

    def client(self, clock=None):
        return self.client_type(self.ledger, enable_network=True, transport=self.transport,
            clock=(lambda: self.at) if clock is None else clock)

    def dispatch(self, client=None, token=None, **kwargs):
        return (client or self.client()).dispatch('one', self.proposal['id'], self.rows, self.cfg,
            self.permit, self.token if token is None else token, **kwargs)

    def stored(self):
        with self.ledger.connect() as db:
            return db.execute('SELECT state,context_json,result_json FROM submission_attempts').fetchall()

    def assert_unsent(self, result):
        self.assertEqual(result['state'], 'failed_before_io')
        self.assertFalse(result['submitted'])
        self.assertFalse(result['automatic_retry'])
        self.assertFalse(result['platform_state_observed'])
        self.transport.assert_not_called()
        records = self.stored()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0], 'failed')
        self.assertNotIn('FAKE_PRIVATE_TOKEN', str(records))

    def test_default_off_never_reads_clock_inputs_ledger_or_credentials(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                constructor = case(kind)[4]
                clock, token, transport = Mock(side_effect=AssertionError), Mock(), Mock()
                def forbidden():
                    raise AssertionError('disabled writer consumed inputs')
                    yield None
                with self.assertRaises(PermissionError):
                    constructor(None, clock=clock, transport=transport).dispatch('one', 'p', forbidden(), {}, None, token)
                clock.assert_not_called();token.assert_not_called();transport.assert_not_called()

    def test_all_five_writers_recompute_three_times_and_consume_generators_once(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setup_case(kind)
                consumed = []
                def once():
                    for row in self.rows:
                        consumed.append(row['id'])
                        yield row
                with patch('monitor.submission.evaluate_all', wraps=evaluate_all) as evaluate:
                    result = self.client().dispatch('one', self.proposal['id'], once(), self.cfg, self.permit, self.token,
                        events=iter(()), migrations=iter(()), recovery_snapshots=iter(self.rows), recovery_events=iter(()))
                self.assertEqual(result['state'], 'confirmed')
                self.assertEqual(evaluate.call_count, 3)
                self.assertEqual(consumed, [r['id'] for r in self.rows])
                self.assertEqual(self.transport.call_count, 1)
                context = json.loads(self.stored()[0][1])
                self.assertEqual(len(context['qualification_digest']), 64)
                self.assertEqual(context['account_id'], self.permit.account_id)
                self.assertNotIn('FAKE_PRIVATE_TOKEN', str(self.stored()))

    def test_target_withdrawal_inside_token_provider_blocks_all_five_before_claim(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setup_case(kind)
                def token():
                    self.cfg['accounts'][0]['target_roi'] = None
                    return 'FAKE_PRIVATE_TOKEN'
                with self.assertRaises((ValueError, PermissionError)):
                    self.dispatch(token=token)
                self.assertEqual(self.stored(), [])
                self.transport.assert_not_called()

    def test_account_scope_can_be_revoked_while_credentials_are_read(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setup_case(kind)
                def token():
                    self.cfg['accounts'][0]['observe'] = False
                    return 'FAKE_PRIVATE_TOKEN'
                with self.assertRaises(ValueError):
                    self.dispatch(token=token)
                self.assertEqual(self.stored(), [])
                self.transport.assert_not_called()

    def test_appended_current_source_is_seen_instead_of_a_frozen_list(self):
        self.setup_case('budget')
        def token():
            row = copy.deepcopy(self.rows[-1])
            row['id'] = 'new-current-source'
            row['source']['data_at'] = (self.at+timedelta(seconds=1)).isoformat()
            row['source']['captured_at'] = row['source']['data_at']
            row['source']['coverage'] = 'visible_only'
            self.rows.append(row)
            return 'FAKE_PRIVATE_TOKEN'
        with self.assertRaises(ValueError):
            self.dispatch(token=token)
        self.assertEqual(self.stored(), [])
        self.transport.assert_not_called()

    def test_unchanged_body_does_not_mask_a_changed_review_basis(self):
        self.setup_case('budget')
        def token():
            self.cfg['accounts'][0]['target_roi'] *= .99
            current = next(p for p in evaluate_all(self.rows, self.cfg, self.at)['recommendations'] if p['id'] == self.proposal['id'])
            self.assertTrue(current['eligible'])
            self.assertEqual(prepare_action(current, self.rows[-1], self.cfg, self.at)['digest'], self.permit.request_digest)
            return 'FAKE_PRIVATE_TOKEN'
        with self.assertRaisesRegex(ValueError, '依据已变化'):
            self.dispatch(token=token)
        self.assertEqual(self.stored(), [])
        self.transport.assert_not_called()

    def test_all_five_writers_refresh_original_inputs_after_claim(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setup_case(kind)
                claim = self.ledger.claim
                def mutate_after_claim(*args, **kwargs):
                    claim(*args, **kwargs)
                    self.cfg['accounts'][0]['target_roi'] = None
                with patch.object(self.ledger, 'claim', side_effect=mutate_after_claim):
                    self.assert_unsent(self.dispatch())

    def test_actual_sqlite_writer_wait_does_not_freeze_source_or_configuration(self):
        self.setup_case('budget')
        entered = threading.Event()
        claim = self.ledger.claim
        def competing_claim(*args, **kwargs):
            entered.set()
            return claim(*args, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.ledger.connect() as blocker:
                blocker.execute('BEGIN IMMEDIATE')
                with patch.object(self.ledger, 'claim', side_effect=competing_claim):
                    future = pool.submit(self.dispatch)
                    try:
                        self.assertTrue(entered.wait(3))
                        self.cfg['accounts'][0]['target_roi'] = None
                    finally:
                        blocker.commit()
                    self.assert_unsent(future.result(timeout=5))

    def test_expiry_during_token_provider_blocks_before_claim(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setup_case(kind)
                times = iter((self.at, self.permit.expires_at))
                with self.assertRaises(PermissionError):
                    self.dispatch(self.client(clock=lambda: next(times)))
                self.token.assert_called_once()
                self.transport.assert_not_called()
                self.assertEqual(self.stored(), [])

    def test_expiry_after_claim_is_unsent_failed_and_audited(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.setup_case(kind)
                times = iter((self.at, self.at, self.permit.expires_at))
                self.assert_unsent(self.dispatch(self.client(clock=lambda: next(times))))

    def test_expiry_during_final_evaluation_is_checked_before_transport(self):
        self.setup_case('budget')
        times = iter((self.at, self.at, self.at, self.permit.expires_at))
        self.assert_unsent(self.dispatch(self.client(clock=lambda: next(times))))

    def test_wall_clock_rollback_before_claim_is_not_treated_as_fresh(self):
        self.setup_case('budget')
        times = iter((self.at, self.at-timedelta(microseconds=1)))
        with self.assertRaisesRegex(PermissionError, '倒退'):
            self.dispatch(self.client(clock=lambda: next(times)))
        self.assertEqual(self.stored(), [])
        self.transport.assert_not_called()

    def test_wall_clock_rollback_after_claim_is_unsent(self):
        self.setup_case('budget')
        times = iter((self.at, self.at, self.at-timedelta(microseconds=1)))
        self.assert_unsent(self.dispatch(self.client(clock=lambda: next(times))))

    def test_monotonic_elapsed_time_expires_a_frozen_wall_clock(self):
        self.setup_case('budget')
        with patch('monitor.submission.time.monotonic', side_effect=(10, 130)):
            with self.assertRaises(PermissionError):
                self.dispatch()
        self.assertEqual(self.stored(), [])
        self.transport.assert_not_called()

    def test_monotonic_elapsed_after_sqlite_claim_prevents_io(self):
        self.setup_case('budget')
        with patch('monitor.submission.time.monotonic', side_effect=(10, 10, 130)):
            self.assert_unsent(self.dispatch())

    def test_legacy_explicit_now_cannot_replace_the_live_clock_or_revive_an_expired_permit(self):
        self.setup_case('budget')
        wall = self.at+timedelta(days=30)
        times = iter((wall, wall+timedelta(minutes=2)))
        with self.assertRaises(PermissionError):
            self.dispatch(self.client(clock=lambda: next(times)), now=self.at)
        self.token.assert_not_called()
        self.assertEqual(self.stored(), [])
        self.transport.assert_not_called()

    def test_token_provider_error_is_redacted_without_claim(self):
        self.setup_case('budget')
        with self.assertRaises(ValueError) as error:
            self.dispatch(token=Mock(side_effect=RuntimeError('FAKE_PRIVATE_TOKEN')))
        self.assertNotIn('FAKE_PRIVATE_TOKEN', str(error.exception))
        self.assertEqual(self.stored(), [])
        self.transport.assert_not_called()

    def test_pre_io_failures_keep_digest_audit_but_do_not_hold_the_parent(self):
        self.setup_case('budget')
        claim = self.ledger.claim
        def mutate(*args, **kwargs):
            claim(*args, **kwargs)
            self.cfg['accounts'][0]['observe'] = False
        with patch.object(self.ledger, 'claim', side_effect=mutate):
            self.assert_unsent(self.dispatch())
        restarted = AttemptLedger(self.path)
        with self.assertRaises(ValueError):
            restarted.claim('same-body', self.permit.request_digest, self.at, {
                'account_id': self.proposal['account_id'], 'entity_id': self.proposal['entity_id']})
        restarted.claim('separate-review', 'different-digest', self.at, {
            'account_id': self.proposal['account_id'], 'entity_id': self.proposal['entity_id']})


if __name__ == '__main__':
    unittest.main()
