"""Recovery never repeats an ad change or invents a successful submission."""
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from monitor.browser_actions import BrowserActions
from tests import test_browser_actions as fixtures

surface = fixtures.surface


class BrowserReconciliationTests(unittest.TestCase):
    setUp = fixtures.BrowserQueueTests.setUp
    tearDown = fixtures.BrowserQueueTests.tearDown
    observe = fixtures.BrowserQueueTests.observe
    draft = fixtures.BrowserQueueTests.draft
    approve = fixtures.BrowserQueueTests.approve
    claim = fixtures.BrowserQueueTests.claim
    begin = fixtures.BrowserQueueTests.begin
    receipt = fixtures.BrowserQueueTests.receipt
    ready = fixtures.BrowserQueueTests.ready

    def unknown(self, *, receipt=True):
        command = self.ready()
        if receipt:
            self.queue.receipt(self.receipt(command, observed=False))
        else:
            self.now += timedelta(seconds=91)
            self.queue.status(self.policy)
        self.now += timedelta(seconds=1)
        return command

    def refresh(self, **updates):
        self.observe(rows=[surface(self.now, document_id='doc_refreshed', budget='1100.00', **updates)])

    def preview(self, command, **updates):
        payload = {'id': command['id'], 'worker_id': 'worker_a', 'epoch': 'epoch_a'}
        payload.update(updates)
        return self.queue.reconcile_preview(payload, self.policy)

    def confirm(self, preview, **updates):
        payload = {'id': preview['id'], 'reconciliation_digest': preview['reconciliation_digest'], 'confirmed': True}
        payload.update(updates)
        return self.queue.reconcile_confirm(payload, self.policy)

    def test_target_match_records_current_setting_without_manufacturing_submission(self):
        c = self.unknown(); self.refresh(); p = self.preview(c)
        self.assertEqual(p['evidence']['match'], 'target')
        self.assertIsNone(self.claim())  # Preview alone leaves the global hold in place.
        result = self.confirm(p)
        self.assertEqual(result['state'], 'reconciled_target'); self.assertIs(result['executed'], False)
        a = self.queue.status(self.policy)['actions'][0]
        self.assertIsNone(a['receipt']['observation'])
        self.assertEqual(a['resolution']['evidence']['value'], '1100.00')
        self.assertIsNone(self.claim())
        with self.queue._tx() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM browser_action_journal WHERE event='claimed'").fetchone()[0], 1)

    def test_before_and_other_current_values_remain_distinct_and_share_cooldown(self):
        for value, match in [('1000.00', 'before'), ('1050.00', 'other')]:
            with self.subTest(value=value):
                # Each branch gets its own queue so one result cannot hide another.
                self.queue = BrowserActions(self.path.parent / (match + '.db'), lambda: self.now)
                self.observe(); c = self.unknown()
                self.observe(rows=[surface(self.now, document_id='new_document', budget=value)])
                result = self.confirm(self.preview(c))
                self.assertEqual(result['state'], 'reconciled_' + match)
                with self.assertRaisesRegex(ValueError, '核对不足1小时'):
                    self.draft(action='pause_plan', after=False)

    def test_refresh_is_required_and_preview_does_not_cause_browser_io(self):
        c = self.unknown(); self.observe()
        with self.assertRaisesRegex(ValueError, '先刷新'): self.preview(c)
        self.refresh(); self.preview(c)
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'unknown')

    def test_current_observation_must_follow_claim(self):
        c = self.unknown()
        self.observe(rows=[surface(document_id='different_but_old', budget='1100.00')])
        with self.assertRaisesRegex(ValueError, '先刷新'): self.preview(c)

    def test_wrong_tab_worker_and_missing_current_value_cannot_release_hold(self):
        c = self.unknown()
        for changes in ({'tab_id': 8}, {'budget': None, 'actions': []}):
            self.observe(rows=[surface(self.now, document_id='new_document', **changes)])
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.preview(c)
        self.refresh()
        with self.assertRaisesRegex(ValueError, '原执行器'): self.preview(c, worker_id='other_worker')

    def test_no_fresh_armed_inventory_cannot_release_hold(self):
        c = self.unknown(); self.refresh(); self.now += timedelta(seconds=91)
        with self.assertRaises(ValueError): self.preview(c)
        self.observe(armed=False, rows=[surface(self.now, document_id='new_doc')])
        with self.assertRaises(ValueError): self.preview(c)

    def test_new_epoch_of_original_worker_can_reconcile_without_replaying_old_approval(self):
        c = self.unknown()
        self.observe(epoch='epoch_b', rows=[surface(self.now, document_id='new_doc', budget='1100.00')])
        self.assertEqual(self.confirm(self.preview(c, epoch='epoch_b'))['state'], 'reconciled_target')
        self.assertIsNone(self.queue.claim({'worker_id': 'worker_a', 'epoch': 'epoch_b'}, self.policy)['command'])

    def test_changed_value_document_or_epoch_after_preview_requires_new_review(self):
        c = self.unknown(); self.refresh(); p = self.preview(c)
        for changes in ({'budget': '1150.00'}, {'document_id': 'another_doc'}, {'name': '改名'}):
            current = surface(self.now, document_id='doc_refreshed', budget='1100.00'); current.update(changes)
            self.observe(rows=[current])
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '已变化'): self.confirm(p)
        self.observe(epoch='epoch_b', rows=[surface(self.now, document_id='doc_refreshed', budget='1100.00')])
        with self.assertRaises(ValueError): self.confirm(p)

    def test_recapture_of_identical_settings_is_accepted_and_latest_time_is_retained(self):
        c = self.unknown(); self.refresh(); p = self.preview(c)
        self.now += timedelta(seconds=5); self.refresh(); self.confirm(p)
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['resolution']['evidence']['surface']['captured_at'], self.now.isoformat())

    def test_unchecked_wrong_digest_expired_and_replaced_previews_are_rejected(self):
        c = self.unknown(); self.refresh(); p = self.preview(c)
        for updates in ({'confirmed': False}, {'confirmed': 1}, {'reconciliation_digest': 'other'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError): self.confirm(p, **updates)
        newer = self.preview(c)
        with self.assertRaises(ValueError): self.confirm(p)
        self.now += timedelta(seconds=60)
        with self.assertRaises(ValueError): self.confirm(newer)

    def test_confirmation_is_consumed_once_under_concurrent_requests(self):
        c = self.unknown(); self.refresh(); p = self.preview(c)
        def attempt(_):
            try: return self.confirm(p)['state']
            except ValueError: return None
        with ThreadPoolExecutor(max_workers=5) as pool: results = list(pool.map(attempt, range(5)))
        self.assertEqual(results.count('reconciled_target'), 1)

    def test_restart_preserves_review_and_resolution_and_cooldown_expires_from_resolution(self):
        c = self.unknown(); self.refresh(); p = self.preview(c)
        self.queue = BrowserActions(self.path, lambda: self.now); self.confirm(p)
        self.queue = BrowserActions(self.path, lambda: self.now)
        self.now += timedelta(seconds=3599); self.refresh()
        with self.assertRaisesRegex(ValueError, '核对不足1小时'): self.draft(action='pause_plan', after=False)
        self.now += timedelta(seconds=1); self.refresh()
        self.assertEqual(self.draft(action='pause_plan', after=False)['state'], 'draft')

    def test_late_exact_receipt_is_idempotent_without_undoing_resolution(self):
        c = self.unknown(); original_receipt = self.receipt(c, observed=False)
        self.refresh(); self.confirm(self.preview(c))
        self.assertEqual(self.queue.receipt(original_receipt)['state'], 'reconciled_target')
        conflict = copy.deepcopy(original_receipt); conflict['message'] = '不同回执'
        with self.assertRaises(ValueError): self.queue.receipt(conflict)

    def test_late_first_receipt_cannot_replace_independent_resolution(self):
        c = self.unknown(receipt=False); self.refresh(); self.confirm(self.preview(c))
        late = self.receipt(c, observed=False)
        result = self.queue.receipt(late)
        self.assertTrue(result['late']); self.assertEqual(result['state'], 'reconciled_target')
        self.assertEqual(self.queue.receipt(late)['state'], 'reconciled_target')
        a = self.queue.status(self.policy)['actions'][0]
        self.assertIsNone(a['receipt']['observation']); self.assertEqual(a['state'], 'reconciled_target')
        with self.queue._tx() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM browser_action_journal WHERE event='late_receipt'").fetchone()[0], 1)

    def test_removed_account_or_changed_roi_basis_cannot_be_reconciled(self):
        d = self.draft(action='roi_target_up', after='3.15'); self.approve(d); c = self.claim(); self.begin(c)
        self.queue.receipt(self.receipt(c, observed=False)); self.now += timedelta(seconds=1)
        self.observe(rows=[surface(self.now, document_id='new_doc', target_roi='3.15', roi_basis='overall_payment')])
        with self.assertRaisesRegex(ValueError, '口径'): self.preview(c)
        self.policy['accounts'] = []
        with self.assertRaises(ValueError): self.preview(c)

    def test_non_unknown_action_cannot_be_reconciled(self):
        d = self.draft(); self.now += timedelta(seconds=1); self.refresh()
        with self.assertRaises(ValueError): self.preview(d)

    def test_global_hold_releases_only_other_approved_work_and_never_replays_original(self):
        c = self.unknown()
        self.observe(rows=[surface(self.now, document_id='doc_refreshed', budget='1100.00'),
            surface(self.now, account_id='456', plan_id='987', tab_id=8, path='/uni-prom/detail', group='组B')])
        other = self.draft(account_id='456', plan_id='987'); self.approve(other)
        self.assertIsNone(self.claim())
        self.confirm(self.preview(c))
        claimed = self.claim()
        self.assertEqual(claimed['id'], other['id']); self.assertNotEqual(claimed['id'], c['id'])
        self.assertIsNone(self.claim())


class BrowserReconciliationHTTPTests(unittest.TestCase):
    setUp = fixtures.BrowserQueueHTTPTests.setUp
    tearDown = fixtures.BrowserQueueHTTPTests.tearDown
    request = fixtures.BrowserQueueHTTPTests.request

    def test_pairing_credentials_cannot_preview_or_confirm_recovery(self):
        for path in ('reconcile-preview', 'reconcile-confirm'):
            with self.subTest(path=path):
                url = '/api/browser-actions/' + path
                self.assertEqual(self.request(url, {}, {'X-Monitor-Token': self.app.token})[0], 403)
                self.assertEqual(self.request(url, {}, {'X-Monitor-CSRF': self.app.csrf, 'Origin': 'https://other.example'})[0], 403)
                self.assertEqual(self.request(url, {}, {'X-Monitor-CSRF': self.app.csrf})[0], 400)

    def test_two_step_http_recovery_uses_current_config_and_does_not_write_metric_history(self):
        from monitor.accounts import collection_policy
        policy = collection_policy(self.app.cfg()); account = next(a for a in policy['accounts'] if a['scene'] == 'product')
        now = datetime.now(timezone.utc); self.app.browser_actions.clock = lambda: now
        q = self.app.browser_actions
        observed = surface(now, account_id=account['id'], group=account['tab_group'])
        def inventory():
            q.observe({'protocol': 1, 'worker_id': 'worker_http', 'epoch': 'epoch_http', 'armed': True,
                       'revision': policy['revision'], 'surfaces': [observed]}, policy)
        inventory()
        d = q.create({'worker_id': 'worker_http', 'epoch': 'epoch_http', 'account_id': account['id'],
            'plan_id': '789', 'action': 'budget_up', 'after': '1100', 'reason': '隔离恢复测试'}, policy)
        q.approve({'id': d['id'], 'intent_digest': d['intent_digest'], 'confirmed': True}, policy)
        c = q.claim({'worker_id': 'worker_http', 'epoch': 'epoch_http'}, policy)['command']
        q.begin({'id': c['id'], 'lease': c['lease'], 'observation': observed}, policy)
        q.receipt({'id': c['id'], 'lease': c['lease'], 'submitted': True, 'observation': None, 'message': '连接中断'})
        now += timedelta(seconds=1)
        observed.update(document_id='new_http_doc', budget='1100.00', captured_at=now.isoformat()); inventory()
        auth = {'X-Monitor-CSRF': self.app.csrf}
        code, p = self.request('/api/browser-actions/reconcile-preview', {'id': c['id'], 'worker_id': 'worker_http', 'epoch': 'epoch_http'}, auth)
        self.assertEqual(code, 200); self.assertEqual(q.status(policy)['actions'][0]['state'], 'unknown')
        code, result = self.request('/api/browser-actions/reconcile-confirm',
            {'id': c['id'], 'reconciliation_digest': p['reconciliation_digest'], 'confirmed': True}, auth)
        self.assertEqual(code, 200); self.assertEqual(result['state'], 'reconciled_target')
        self.assertIsNone(q.claim({'worker_id': 'worker_http', 'epoch': 'epoch_http'}, policy)['command'])
        self.assertEqual(self.app.store.snapshots(), []); self.assertEqual(self.app.store.events(), [])


if __name__ == '__main__': unittest.main()
