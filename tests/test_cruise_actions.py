import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monitor.cruise_actions import CruiseActions, normalize_task_surface, task_binding

NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
POLICY = {'revision': 'scope-a', 'accounts': [
    {'id': '123', 'scene': 'live', 'tab_group': '组A'}, {'id': '456', 'scene': 'live', 'tab_group': '组B'}]}


def task(at=NOW, **updates):
    value = {'target_kind': 'control_task', 'account_id': '123', 'plan_id': '789', 'task_id': '900',
        'path': '/uni-prom/detail', 'group': '组A', 'name': '明确追投任务', 'budget': '1500.00',
        'duration_minutes': '120.00', 'running': True, 'status': '调控中',
        'actions': ['control_budget_up', 'control_duration_up', 'control_pause', 'control_stop'],
        'captured_at': at.isoformat(), 'tab_id': 7, 'document_id': 'doc_original',
        'metrics': {'spend': 123.45, 'comprehensive_cost': 120, 'roi': 3.1, 'orders': 2},
        'context': {'native_status': '调控中', 'data_at': None, 'control_delivery_mode': '控成本追投'},
        'metric_basis': 'control_payment'}
    value.update(updates); return value


class CruiseActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / 'tasks.db'
        self.now = NOW; self.policy = copy.deepcopy(POLICY)
        self.queue = CruiseActions(self.path, lambda: self.now); self.observe()

    def tearDown(self): self.tmp.cleanup()

    def observe(self, *, rows=None, epoch='epoch-a', armed=True):
        return self.queue.observe({'protocol': 1, 'worker_id': 'worker-a', 'epoch': epoch,
            'armed': armed, 'revision': self.policy['revision'], 'surfaces': [task(self.now)] if rows is None else rows}, self.policy)

    def create(self, **updates):
        payload = {'worker_id': 'worker-a', 'epoch': 'epoch-a', 'account_id': '123', 'plan_id': '789',
            'task_id': '900', 'action': 'control_budget_up', 'after': '6000.00', 'reason': '已核对这条任务'}
        payload.update(updates); return self.queue.create(payload, self.policy)

    def approve(self, draft):
        return self.queue.approve({'id': draft['id'], 'intent_digest': draft['intent_digest'], 'confirmed': True}, self.policy)

    def claim(self): return self.queue.claim({'worker_id': 'worker-a', 'epoch': 'epoch-a'}, self.policy)['command']

    def begin(self, command, observation=None):
        return self.queue.begin({'id': command['id'], 'lease': command['lease'], 'observation': observation or task(self.now)}, self.policy)

    def ready(self, **changes):
        draft = self.create(**changes); self.approve(draft); command = self.claim(); self.begin(command); return command

    def receipt(self, command, *, observation=True, submitted=True, **changes):
        intent = command['intent']; old = intent['surface']
        row = {k: old[k] for k in ('account_id', 'plan_id', 'task_id', 'tab_id', 'document_id')}
        row.update(readback_document_id='doc_refreshed', field=intent['field'], value=intent['after'],
                   captured_at=self.now.isoformat(), reloaded=True)
        row.update(changes)
        return {'id': command['id'], 'lease': command['lease'], 'submitted': submitted,
            'observation': row if observation else None, 'message': '隔离任务回读，未接触平台'}

    def test_no_command_without_explicit_per_action_approval(self):
        d = self.create(); self.assertIsNone(self.claim())
        self.assertEqual(d['intent']['target_kind'], 'control_task')
        self.assertEqual(d['intent']['plan_id'], '789'); self.assertEqual(d['intent']['task_id'], '900')
        self.assertNotIn('plan_name', d['intent'])
        self.assertEqual(d['intent']['before'], '1500.00'); self.assertEqual(d['intent']['after'], '6000.00')

    def test_task_budget_is_absolute_and_does_not_inherit_main_plan_percentage_limit(self):
        d = self.create(after='9000'); self.approve(d)
        self.assertEqual(self.claim()['intent']['after'], '9000.00')

    def test_explicit_zero_budget_is_not_missing_and_may_be_increased(self):
        self.observe(rows=[task(budget='0.00')])
        self.assertEqual(self.create(after='500')['intent']['before'], '0.00')
        with self.assertRaises(ValueError): normalize_task_surface(task(budget=None), POLICY['accounts'])

    def test_duration_targets_are_positive_increasing_thirty_minute_totals(self):
        for after in ('120', '90', '135', '150.001', '1e3', '+150', '0'):
            with self.subTest(after=after), self.assertRaises(ValueError):
                self.create(action='control_duration_up', after=after)
        d = self.create(action='control_duration_up', after='150')
        self.assertEqual(d['intent']['field'], 'duration_minutes'); self.assertEqual(d['intent']['after'], '150.00')

    def test_pause_and_stop_are_distinct_and_require_exact_types_and_status(self):
        for after in (0, 'false', None):
            with self.assertRaises(ValueError): self.create(action='control_pause', after=after)
        d = self.create(action='control_pause', after=False); self.assertIs(d['intent']['after'], False)
        self.queue.cancel({'id': d['id']})
        with self.assertRaises(ValueError): self.create(action='control_stop', after='已结束')
        d = self.create(action='control_stop', after='调控结束')
        self.assertEqual(d['intent']['field'], 'status')

    def test_observation_values_change_without_mutating_approved_configuration_binding(self):
        d = self.create()
        revised = task(metrics={'spend': 200, 'roi': None}, context={'data_at': 'unknown'}, metric_basis='unknown')
        self.observe(rows=[revised]); self.approve(d); c = self.claim(); self.begin(c, revised)
        self.assertEqual(task_binding(task()), task_binding(revised))
        self.assertNotIn('metrics', d['intent']['surface']); self.assertNotIn('context', d['intent']['surface'])

    def test_wrong_parent_task_document_or_group_never_consumes_original_approval(self):
        d = self.create()
        for updates in ({'plan_id': '790'}, {'task_id': '901'}, {'document_id': 'other_doc'}, {'budget': '1501.00'}):
            self.observe(rows=[task(**updates)])
            with self.subTest(updates=updates), self.assertRaises(ValueError): self.approve(d)
        with self.assertRaises(ValueError): self.observe(rows=[task(group='其他组')])

    def test_same_task_number_under_another_parent_does_not_replace_original_task(self):
        self.observe(rows=[task(), task(plan_id='790')])
        d = self.create(plan_id='790'); self.approve(d); command = self.claim()
        self.assertEqual(command['intent']['plan_id'], '790')
        with self.assertRaises(ValueError): self.begin(command, task())

    def test_duplicate_identity_unadvertised_action_and_main_plan_shapes_rejected(self):
        with self.assertRaises(ValueError): self.observe(rows=[task(), task()])
        self.observe(rows=[task(actions=['control_pause'])])
        with self.assertRaises(ValueError): self.create()
        with self.assertRaises(ValueError): self.observe(rows=[task(target_kind='plan')])
        with self.assertRaises(ValueError): self.observe(rows=[task(status='调控结束', running=True)])

    def test_freshness_protocol_and_readonly_metrics_validation(self):
        for row in (task(NOW-timedelta(seconds=91)), task(NOW+timedelta(seconds=1)),
                    task(metrics={'roi': float('nan')}), task(metrics={'orders': 1.5}), task(metric_basis='overall_payment')):
            with self.subTest(row=row), self.assertRaises(ValueError): self.observe(rows=[row])
        self.now += timedelta(seconds=91)
        with self.assertRaises(ValueError): self.create()

    def test_success_needs_refreshed_original_parent_and_task_readback(self):
        c = self.ready(); result = self.queue.receipt(self.receipt(c))
        self.assertEqual(result['state'], 'verified'); self.assertFalse(self.queue.has_active_or_unknown())
        self.assertEqual(self.queue.receipt(self.receipt(c))['state'], 'verified')
        conflict = self.receipt(c); conflict['message'] = '不同回执'
        with self.assertRaises(ValueError): self.queue.receipt(conflict)

    def test_wrong_readback_stays_unknown_and_does_not_replay(self):
        changes = [{'account_id': '456'}, {'plan_id': '790'}, {'task_id': '901'}, {'tab_id': 8},
            {'document_id': 'other_original'}, {'readback_document_id': 'doc_original'}, {'value': '6001.00'},
            {'value': 6000}, {'field': 'duration_minutes'}, {'reloaded': False},
            {'captured_at': (NOW-timedelta(seconds=1)).isoformat()}]
        for index, update in enumerate(changes):
            with self.subTest(update=update):
                self.queue = CruiseActions(self.path.parent / ('wrong-'+str(index)+'.db'), lambda: self.now); self.observe()
                c = self.ready(); self.assertEqual(self.queue.receipt(self.receipt(c, **update))['state'], 'unknown')
                self.assertTrue(self.queue.has_active_or_unknown()); self.assertIsNone(self.claim())

    def test_click_receipt_without_independent_observation_is_unknown(self):
        c = self.ready()
        self.assertEqual(self.queue.receipt(self.receipt(c, observation=False))['state'], 'unknown')
        self.assertIsNone(self.claim())

    def test_cannot_claim_or_submit_twice(self):
        d = self.create(); self.approve(d)
        with ThreadPoolExecutor(max_workers=5) as pool: commands = list(pool.map(lambda _: self.claim(), range(5)))
        self.assertEqual(sum(c is not None for c in commands), 1)
        c = next(c for c in commands if c); self.begin(c)
        with self.assertRaises(ValueError): self.begin(c)

    def test_submission_without_begin_is_rejected_even_after_claim_timeout(self):
        d = self.create(); self.approve(d); c = self.claim(); self.now += timedelta(seconds=91)
        with self.assertRaisesRegex(ValueError, '提交许可'): self.queue.receipt(self.receipt(c))

    def test_stop_and_worker_restart_invalidate_unclaimed_and_hold_claimed_work(self):
        d = self.create(); self.approve(d); self.observe(armed=False)
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'cancelled')
        self.observe(); d = self.create(); self.approve(d); self.claim(); self.observe(epoch='new-epoch')
        self.assertTrue(self.queue.has_active_or_unknown())
        self.assertEqual(next(a for a in self.queue.status(self.policy)['actions'] if a['id']==d['id'])['state'], 'unknown')

    def test_restart_does_not_requeue_or_release_unknown(self):
        self.ready(); self.queue = CruiseActions(self.path, lambda: self.now)
        self.assertIsNone(self.claim()); self.now += timedelta(seconds=91)
        self.assertTrue(self.queue.has_active_or_unknown()); self.assertIsNone(self.claim())

    def test_task_same_action_cooldown_allows_separately_confirmed_duration_leg(self):
        c = self.ready(); self.queue.receipt(self.receipt(c))
        self.observe(rows=[task(budget='6000.00')])
        with self.assertRaisesRegex(ValueError, '15分钟'): self.create(after='6500.00')
        duration=self.create(action='control_duration_up',after='180.00')
        self.assertEqual(duration['state'],'draft')
        self.queue.cancel({'id':duration['id']})
        self.now += timedelta(seconds=900); self.observe(rows=[task(self.now, budget='6000.00')])
        self.assertEqual(self.create(after='6500.00')['state'], 'draft')

    def test_independent_task_and_account_identity_and_worker_serialization(self):
        other = task(account_id='456', plan_id='790', task_id='901', tab_id=8, group='组B')
        self.observe(rows=[task(), other])
        first = self.create(); second = self.create(account_id='456', plan_id='790', task_id='901')
        self.approve(first); self.approve(second)
        self.assertIsNotNone(self.claim()); self.assertIsNone(self.claim())
        state = self.queue.status(self.policy)
        self.assertNotIn('lease', json.dumps(state)); self.assertEqual(len(state['actions']), 2)

    def test_validator_runs_at_approval_claim_and_begin_and_can_read_history_without_deadlock(self):
        calls = []
        def validate(action_id, intent):
            history = self.queue.history(intent['account_id']); calls.append((action_id, history))
        self.queue.action_validator = validate
        d = self.create(); self.approve(d); c = self.claim(); self.begin(c)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[0] == d['id'] for call in calls))
        history = self.queue.history('123')
        self.assertEqual(history[0]['entity_id'], '900'); self.assertEqual(history[0]['parent_plan_id'], '789')
        self.assertEqual(history[0]['state'], 'submitting'); self.assertEqual(self.queue.history('456'), [])

    def test_changed_rule_validator_blocks_old_approval_and_claim(self):
        d = self.create()
        def rejected(*_): raise ValueError('规则资格已变化')
        self.queue.action_validator = rejected
        with self.assertRaisesRegex(ValueError, '规则资格'): self.approve(d)
        self.queue.action_validator = None; self.approve(d); self.queue.action_validator = rejected
        self.assertIsNone(self.claim()); self.assertEqual(self.queue.status(self.policy)['actions'][0]['state'], 'rejected')


if __name__ == '__main__': unittest.main()
