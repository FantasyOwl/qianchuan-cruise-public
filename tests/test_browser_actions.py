import copy
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monitor.browser_actions import BrowserActions, normalize_surface

NOW = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
POLICY = {'revision': 'revision-a', 'accounts': [
    {'id': '123', 'scene': 'product', 'tab_group': '组A'},
    {'id': '456', 'scene': 'live', 'tab_group': '组B'}]}


def surface(at=NOW, **updates):
    row = {'account_id': '123', 'plan_id': '789', 'tab_id': 7, 'document_id': 'doc_a',
           'path': '/overall-prom', 'group': '组A', 'name': '明确计划', 'budget': '1000.00',
           'target_roi': '3.00', 'roi_basis': 'marketing', 'running': True,
           'actions': ['budget_up', 'budget_down', 'roi_target_up', 'roi_target_down', 'pause_plan'],
           'captured_at': at.isoformat()}
    row.update(updates)
    return row


class BrowserQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / 'actions.db'
        self.now = NOW
        self.queue = BrowserActions(self.path, lambda: self.now)
        self.policy = copy.deepcopy(POLICY); self.observe()

    def tearDown(self):
        self.tmp.cleanup()

    def observe(self, *, armed=True, epoch='epoch_a', rows=None):
        return self.queue.observe({'protocol': 1, 'worker_id': 'worker_a', 'epoch': epoch,
            'armed': armed, 'revision': self.policy['revision'],
            'surfaces': [surface(self.now)] if rows is None else rows}, self.policy)

    def draft(self, **updates):
        payload = {'worker_id': 'worker_a', 'epoch': 'epoch_a', 'account_id': '123', 'plan_id': '789',
                   'action': 'budget_up', 'after': '1100.00', 'reason': '人工核对后小步调整'}
        payload.update(updates)
        return self.queue.create(payload, self.policy)

    def approve(self, draft):
        return self.queue.approve({'id': draft['id'], 'intent_digest': draft['intent_digest'], 'confirmed': True}, self.policy)

    def claim(self):
        return self.queue.claim({'worker_id': 'worker_a', 'epoch': 'epoch_a'}, self.policy)['command']

    def begin(self, command, **updates):
        observation = surface(self.now); observation.update(updates)
        return self.queue.begin({'id': command['id'], 'lease': command['lease'], 'observation': observation}, self.policy)

    def receipt(self, command, *, observed=True, **updates):
        observation = {'account_id': '123', 'plan_id': '789', 'tab_id': 7, 'document_id': 'doc_a',
                       'readback_document_id': 'doc_b', 'field': 'budget', 'value': '1100.00',
                       'captured_at': self.now.isoformat(), 'reloaded': True} if observed else None
        if observation: observation.update(updates)
        return {'id': command['id'], 'lease': command['lease'], 'submitted': True,
                'observation': observation, 'message': '刷新后页面读取'}

    def ready(self):
        d = self.draft(); self.approve(d); c = self.claim(); self.begin(c); return c

    def test_no_command_without_per_action_confirmation(self):
        self.draft(); self.assertIsNone(self.claim())

    def test_unarmed_worker_cannot_prepare_or_execute(self):
        self.observe(armed=False)
        self.assertFalse(self.queue.status(self.policy)['execution_available'])
        with self.assertRaisesRegex(ValueError, '未开启'): self.draft()

    def test_current_surface_and_setting_basis_are_bound(self):
        draft = self.draft(action='roi_target_up', after='3.15')
        self.assertEqual(draft['intent']['before'], '3.00')
        self.assertEqual(draft['intent']['setting_roi_basis'], 'marketing')
        self.assertEqual(draft['intent']['origin'], 'manual_review')

    def test_unverified_action_not_advertised_cannot_prepare(self):
        self.observe(rows=[surface(actions=['pause_plan'])])
        with self.assertRaisesRegex(ValueError, '未识别'): self.draft()

    def test_payload_cannot_import_or_override_old_value(self):
        with self.assertRaises(ValueError): self.draft(before='1.00')
        with self.assertRaises(ValueError): self.draft(source='import')

    def test_unknown_account_group_path_and_duplicate_objects_rejected(self):
        for change in ({'account_id': '999'}, {'group': '其他组'}, {'path': '/board-next'}, {'budget': None}, {'roi_basis': 'unknown'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.observe(rows=[surface(**change)])
        with self.assertRaises(ValueError): self.observe(rows=[surface(), surface()])

    def test_surface_future_or_old_clock_is_rejected(self):
        for offset in (-91, 1):
            with self.assertRaises(ValueError): self.observe(rows=[surface(NOW + timedelta(seconds=offset))])

    def test_stale_inventory_cannot_create_or_approve(self):
        draft = self.draft(); self.now += timedelta(seconds=91)
        with self.assertRaises(ValueError): self.approve(draft)

    def test_exact_amount_and_limits_no_silent_rounding(self):
        for action, value in [('budget_up', '1200.01'), ('budget_up', '1000'), ('budget_down', '950'),
                              ('budget_down', '699.99'), ('roi_target_up', '3.001'),
                              ('roi_target_up', '2.85'), ('roi_target_up', '3.31'), ('roi_target_down', '3.15')]:
            with self.subTest(action=action, value=value), self.assertRaises(ValueError): self.draft(action=action, after=value)

    def test_pause_requires_boolean_false_and_current_true(self):
        with self.assertRaises(ValueError): self.draft(action='pause_plan', after=0)
        draft = self.draft(action='pause_plan', after=False)
        self.assertIs(draft['intent']['before'], True); self.assertIs(draft['intent']['after'], False)

    def test_changed_settings_or_document_reject_old_approval(self):
        draft = self.draft()
        for updates in ({'budget': '1001.00'}, {'document_id': 'new_doc'}, {'tab_id': 8}):
            self.observe(rows=[surface(**updates)])
            with self.subTest(updates=updates), self.assertRaises(ValueError): self.approve(draft)

    def test_wrong_digest_or_unchecked_confirmation_rejected(self):
        draft = self.draft()
        for updates in ({'confirmed': False}, {'intent_digest': 'other'}):
            payload = {'id': draft['id'], 'intent_digest': draft['intent_digest'], 'confirmed': True}; payload.update(updates)
            with self.assertRaises(ValueError): self.queue.approve(payload, self.policy)

    def test_approval_cannot_be_consumed_twice(self):
        draft = self.draft(); self.approve(draft)
        with self.assertRaises(ValueError): self.approve(draft)
        self.assertIsNotNone(self.claim()); self.assertIsNone(self.claim())

    def test_parallel_claims_only_one_wins(self):
        self.approve(self.draft())
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.claim(), range(6)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_restart_does_not_requeue_claimed_command(self):
        self.approve(self.draft()); self.claim()
        self.queue = BrowserActions(self.path, lambda: self.now)
        self.assertIsNone(self.claim())
        self.now += timedelta(seconds=91); self.observe()
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'unknown')
        self.assertIsNone(self.claim())

    def test_stop_or_epoch_change_invalidates_unclaimed_work(self):
        draft = self.draft(); self.approve(draft); self.observe(epoch='epoch_b')
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'cancelled')

    def test_stop_during_execution_is_unknown_not_cancelled(self):
        self.approve(self.draft()); self.claim(); self.observe(armed=False)
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'unknown')

    def test_policy_change_rejects_pending_approval(self):
        self.approve(self.draft()); self.policy['revision'] = 'new-revision'; self.observe()
        self.assertIsNone(self.claim())
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'rejected')

    def test_expired_approval_not_claimed_after_worker_refresh(self):
        self.approve(self.draft()); self.now += timedelta(seconds=121); self.observe()
        self.assertIsNone(self.claim())
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'expired')

    def test_begin_revalidates_before_value_and_short_clock(self):
        self.approve(self.draft()); command = self.claim()
        with self.assertRaises(ValueError): self.begin(command, budget='1100.00')
        with self.assertRaises(ValueError): self.begin(command, captured_at=(NOW - timedelta(seconds=11)).isoformat())
        self.begin(command)
        with self.assertRaises(ValueError): self.begin(command)

    def test_submitted_receipt_without_begin_rejected(self):
        self.approve(self.draft()); command = self.claim()
        with self.assertRaises(ValueError): self.queue.receipt(self.receipt(command))

    def test_click_ack_is_unknown_not_verified(self):
        command = self.ready(); out = self.queue.receipt(self.receipt(command, observed=False))
        self.assertEqual(out['state'], 'unknown'); self.assertIsNone(self.claim())

    def test_wrong_account_plan_value_tab_or_reload_never_verifies(self):
        for updates in ({'account_id': '456'}, {'plan_id': '111'}, {'tab_id': 99},
                        {'document_id': 'wrong'}, {'readback_document_id': 'doc_a'},
                        {'value': '1200.00'}, {'value': 1100}, {'field': 'target_roi'}, {'reloaded': False},
                        {'captured_at': (NOW - timedelta(seconds=1)).isoformat()}):
            with self.subTest(updates=updates):
                with tempfile.TemporaryDirectory() as tmp:
                    old_queue = self.queue; self.queue = BrowserActions(Path(tmp) / 'isolated.db', lambda: self.now)
                    try:
                        self.observe(); command = self.ready()
                        self.assertEqual(self.queue.receipt(self.receipt(command, **updates))['state'], 'unknown')
                    finally: self.queue = old_queue

    def test_refresh_readback_success_and_idempotent_identical_receipt(self):
        command = self.ready(); receipt = self.receipt(command)
        self.assertEqual(self.queue.receipt(receipt)['state'], 'verified')
        self.assertEqual(self.queue.receipt(receipt)['state'], 'verified')
        receipt['message'] = 'different'
        with self.assertRaises(ValueError): self.queue.receipt(receipt)
        public = json.dumps(self.queue.status(self.policy))
        self.assertNotIn(command['lease'], public)

    def test_cooldown_is_shared_between_different_actions(self):
        command = self.ready(); self.queue.receipt(self.receipt(command))
        self.observe(rows=[surface(budget='1100.00')])
        with self.assertRaisesRegex(ValueError, '不足1小时'): self.draft(action='roi_target_up', after='3.15')

    def test_cancel_only_before_worker_claim(self):
        draft = self.draft(); self.approve(draft); self.queue.cancel({'id': draft['id']})
        self.assertIsNone(self.claim())
        command = self.ready()
        with self.assertRaises(ValueError): self.queue.cancel({'id': command['id']})

    def test_rule_approval_claim_and_begin_recompute_eligibility(self):
        proposal = {'id': 'rule-one', 'account_id': '123', 'entity_id': '789', 'scope': 'plan',
                    'action': 'budget_up', 'before': 1000, 'after': 1100, 'metric_basis': 'overall_payment',
                    'source_key': 'real-plan-series', 'mode': 'simulation_only', 'eligible': True}
        calls = []
        self.queue.recommendation_validator = lambda expected: calls.append(expected['id'])
        payload = {'worker_id': 'worker_a', 'epoch': 'epoch_a', 'account_id': '123', 'plan_id': '789',
                   'action': 'budget_up', 'after': '1100', 'reason': '当前规则建议'}
        draft = self.queue.create(payload, self.policy, recommendation=proposal)
        self.assertEqual(draft['intent']['origin'], 'rule_recommendation')
        self.approve(draft); command = self.claim(); self.begin(command)
        self.assertEqual(calls, ['rule-one'] * 3)

    def test_changed_or_missing_rule_validator_blocks_approval(self):
        proposal = {'id': 'rule-one', 'account_id': '123', 'entity_id': '789', 'scope': 'plan',
                    'action': 'budget_up', 'before': 1000, 'after': 1100, 'mode': 'simulation_only', 'eligible': True}
        payload = {'worker_id': 'worker_a', 'epoch': 'epoch_a', 'account_id': '123', 'plan_id': '789',
                   'action': 'budget_up', 'after': '1100', 'reason': '当前规则建议'}
        draft = self.queue.create(payload, self.policy, recommendation=proposal)
        with self.assertRaisesRegex(ValueError, '复核器尚未连接'): self.approve(draft)
        def invalid(expected): raise ValueError('建议已失效')
        self.queue.recommendation_validator = invalid
        with self.assertRaisesRegex(ValueError, '已失效'): self.approve(draft)

    def test_rule_roi_cannot_change_a_different_setting_basis(self):
        proposal = {'id': 'roi-rule', 'account_id': '123', 'entity_id': '789', 'scope': 'plan',
            'action': 'roi_target_up', 'before': 3, 'after': 3.15, 'metric_basis': 'overall_payment',
            'mode': 'simulation_only', 'eligible': True}
        payload = {'worker_id': 'worker_a', 'epoch': 'epoch_a', 'account_id': '123', 'plan_id': '789',
            'action': 'roi_target_up', 'after': '3.15', 'reason': '规则建议'}
        with self.assertRaisesRegex(ValueError, '口径不一致'):
            self.queue.create(payload, self.policy, recommendation=proposal)

    def test_two_accounts_are_bound_and_execute_serially(self):
        self.observe(rows=[surface(), surface(account_id='456', plan_id='987', tab_id=8, path='/uni-prom/detail', group='组B')])
        first = self.draft(); second = self.draft(account_id='456', plan_id='987')
        self.approve(first); self.approve(second)
        commands = [self.claim(), self.claim()]
        self.assertEqual(sum(c is not None for c in commands), 1)
        self.assertEqual(len(self.queue.status(self.policy)['actions']), 2)


class BrowserQueueHTTPTests(unittest.TestCase):
    def setUp(self):
        from monitor.server import App, Handler, ROOT, Server
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        shutil.copyfile(ROOT / 'config.json', root / 'config.json')
        self.app = App(root)
        self.server = Server(('127.0.0.1', 0), Handler); self.app.port = self.server.server_address[1]; self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.app.port}{path}', data=data,
            headers={'Content-Type': 'application/json', **(headers or {})})
        try: response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc: response = exc
        with response: return response.status, json.loads(response.read())

    def test_default_read_only_without_worker_and_no_changes_in_monitor_series(self):
        code, value = self.request('/api/browser-actions')
        self.assertEqual(code, 200); self.assertFalse(value['execution_available']); self.assertEqual(value['actions'], [])
        code, value = self.request('/api/status')
        self.assertEqual(value['mode'], 'read_only'); self.assertFalse(value['execution_available'])
        self.assertEqual(self.app.store.snapshots(), [])

    def test_status_summary_follows_real_execution_connection_without_changing_rule_eligibility(self):
        from monitor.accounts import collection_policy
        policy = collection_policy(self.app.cfg()); account = next(a for a in policy['accounts'] if a['scene'] == 'product')
        observed = surface(datetime.now(timezone.utc), account_id=account['id'], group=account['tab_group'])
        baseline_summary = self.app.current()[1]['summary']
        for armed in (True, False):
            self.app.browser_actions.observe({'protocol': 1, 'worker_id': 'worker_status', 'epoch': 'epoch_status',
                'armed': armed, 'revision': policy['revision'], 'surfaces': [observed]}, policy)
            code, result = self.request('/api/status')
            self.assertEqual(code, 200); self.assertEqual(result['execution_available'], armed)
            self.assertEqual('逐条确认执行已连接' in result['evaluation']['summary'], armed)
            if armed: self.assertNotIn('真实执行不可用', result['evaluation']['summary'])
            else: self.assertEqual(result['evaluation']['summary'], baseline_summary)
            self.assertEqual(result['evaluation']['recommendations'], [])
            self.assertEqual(self.app.current()[1]['summary'], baseline_summary)
        self.assertEqual(self.app.browser_actions.status(policy)['actions'], [])

    def test_extension_version_heartbeat_does_not_enable_execution_or_create_a_worker(self):
        from monitor.accounts import collection_policy
        policy=collection_policy(self.app.cfg())
        code,_=self.request('/api/heartbeat',{'tab_count':2,'issues':[],'collector_protocol':2,'policy_revision':policy['revision'],
            'extension_version':'1.3.0','browser_control_protocol':1},{'X-Monitor-Token':self.app.token})
        self.assertEqual(code,200)
        self.assertEqual(self.app.store.diagnostics()['extension']['extension_version'],'1.3.0')
        self.assertEqual(self.app.browser_actions.status(policy)['workers'],[])
        for value in ('token-private',1.3,'1.3.0/../../'):
            with self.assertRaises(ValueError):self.app.heartbeat({'extension_version':value})

    def test_pairing_token_cannot_approve_and_csrf_cannot_claim(self):
        self.assertEqual(self.request('/api/browser-actions/approve', {}, {'X-Monitor-Token': self.app.token})[0], 403)
        self.assertEqual(self.request('/api/browser-actions/worker-claim', {}, {'X-Monitor-CSRF': self.app.csrf})[0], 403)

    def test_foreign_browser_origins_are_rejected(self):
        self.assertEqual(self.request('/api/browser-actions/create', {}, {'X-Monitor-CSRF': self.app.csrf, 'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.request('/api/browser-actions/worker-claim', {}, {'X-Monitor-Token': self.app.token, 'Origin': 'https://other.example'})[0], 403)

    def test_protocol_validation_after_correct_local_auth(self):
        for suffix in ('create', 'create-from-recommendation', 'approve', 'cancel'):
            self.assertEqual(self.request('/api/browser-actions/' + suffix, {}, {'X-Monitor-CSRF': self.app.csrf})[0], 400)
        for suffix in ('worker-observe', 'worker-claim', 'worker-begin', 'worker-receipt'):
            self.assertEqual(self.request('/api/browser-actions/' + suffix, {}, {'X-Monitor-Token': self.app.token})[0], 400)

    def test_no_rule_draft_from_missing_suggestion_or_blank_targets(self):
        code, result = self.request('/api/browser-actions/create-from-recommendation',
            {'recommendation_id': 'not-current', 'worker_id': 'worker_a', 'epoch': 'epoch_a'}, {'X-Monitor-CSRF': self.app.csrf})
        self.assertEqual(code, 400)
        self.assertEqual(self.app.browser_actions.status(__import__('monitor.accounts', fromlist=['collection_policy']).collection_policy(self.app.cfg()))['actions'], [])

    def test_http_end_to_end_approval_and_browser_readback_in_temporary_app(self):
        from monitor.accounts import collection_policy
        policy = collection_policy(self.app.cfg()); account = next(a for a in policy['accounts'] if a['scene'] == 'product')
        now = datetime.now(timezone.utc)
        observed = surface(now, account_id=account['id'], group=account['tab_group'])
        worker_headers = {'X-Monitor-Token': self.app.token, 'Origin': 'chrome-extension://test-executor'}
        ui_headers = {'X-Monitor-CSRF': self.app.csrf}
        code, _ = self.request('/api/browser-actions/worker-observe', {'protocol': 1, 'worker_id': 'worker_http',
            'epoch': 'epoch_http', 'armed': True, 'revision': policy['revision'], 'surfaces': [observed]}, worker_headers)
        self.assertEqual(code, 200)
        code, draft = self.request('/api/browser-actions/create', {'worker_id': 'worker_http', 'epoch': 'epoch_http',
            'account_id': account['id'], 'plan_id': '789', 'action': 'budget_up', 'after': '1100', 'reason': '隔离HTTP回放'}, ui_headers)
        self.assertEqual(code, 200)
        self.assertEqual(self.request('/api/browser-actions/approve', {'id': draft['id'], 'intent_digest': draft['intent_digest'], 'confirmed': True}, ui_headers)[0], 200)
        code, value = self.request('/api/browser-actions/worker-claim', {'worker_id': 'worker_http', 'epoch': 'epoch_http'}, worker_headers)
        self.assertEqual(code, 200); command = value['command']
        self.assertEqual(self.request('/api/browser-actions/worker-begin', {'id': command['id'], 'lease': command['lease'], 'observation': observed}, worker_headers)[0], 200)
        receipt = {'id': command['id'], 'lease': command['lease'], 'submitted': True, 'message': '隔离回放，未接触平台',
            'observation': {'account_id': account['id'], 'plan_id': '789', 'tab_id': 7, 'document_id': 'doc_a',
            'readback_document_id': 'doc_http_new', 'field': 'budget', 'value': '1100.00',
            'captured_at': datetime.now(timezone.utc).isoformat(), 'reloaded': True}}
        code, value = self.request('/api/browser-actions/worker-receipt', receipt, worker_headers)
        self.assertEqual(code, 200); self.assertEqual(value['state'], 'verified')
        self.assertEqual(self.app.store.snapshots(), []); self.assertEqual(self.app.store.events(), [])


if __name__ == '__main__':
    unittest.main()
