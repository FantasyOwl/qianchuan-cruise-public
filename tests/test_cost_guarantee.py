from datetime import datetime, timedelta, timezone
import unittest

from monitor.cost_guarantee import evaluate_cost_guarantee

CN = timezone(timedelta(hours=8))


class CostGuaranteeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 26, 12, tzinfo=CN)
        self.action = {'account_id': '1999999999999002', 'plan_id': '8218616704149013',
                       'action': 'budget_up', 'before': '1000.00', 'after': '1200.00'}

    def run_check(self, context=None, action=None, history=None):
        return evaluate_cost_guarantee(context or {}, self.action if action is None else action,
                                       history=history, now=self.now)

    def operation(self, **kw):
        return {'id': 'a', 'account_id': self.action['account_id'], 'plan_id': self.action['plan_id'],
                'action': 'roi_target_up', 'state': 'verified',
                'at': (self.now-timedelta(hours=2)).isoformat(), **kw}

    def standard(self, **kw):
        return {'product_family': 'standard', 'scene': 'product', 'tool': 'main_plan',
                'delivery_mode': 'cost_control', 'optimization_goal': 'product_purchase', **kw}

    def roi_action(self, **kw):
        return {**self.action, 'action': 'roi_target_up', 'before': '3.00', 'after': '3.15', **kw}

    def qualification(self, **kw):
        return {'eligible_orders': 8, 'platform_filtered_statistics': True,
                'statistics_finalized': True, 'within_guarantee_period': True,
                'operation_conditions_met': True, 'no_disqualifying_events': True,
                'actual_to_target_cost_ratio': '1.21', **kw}

    def test_unknown_is_not_a_blanket_manual_budget_ban_or_payout_claim(self):
        out = self.run_check()
        self.assertTrue(out['allowed']); self.assertEqual(out['status'], 'unknown')
        self.assertFalse(out['eligible_for_payout'])
        self.assertTrue(any('初始预算' in s for s in out['warnings']))

    def test_url_name_or_marketing_roi_does_not_identify_product_family(self):
        out = self.run_check({'name': '乘方示例分组A', 'roi_basis': 'marketing',
            'source': {'url': 'https://qianchuan.jinritemai.com/overall-prom'}})
        self.assertEqual(out['rule_scope']['product_family'], 'unknown')

    def test_assessment_target_empty_does_not_replace_plan_setting(self):
        out = self.run_check({'target_roi': None, 'roi_basis': 'overall_payment'}, self.roi_action())
        self.assertTrue(out['allowed']); self.assertEqual(out['status'], 'unknown')

    def test_bad_identity_blocks_even_if_known_guarantee_status(self):
        out = self.run_check({'account_id': '1999999999999001', 'platform_guarantee_status': 'active'})
        self.assertFalse(out['allowed'])
        self.assertTrue(any('不一致' in s for s in out['blocking_reasons']))

    def test_main_plan_known_recent_change_is_a_user_policy_block(self):
        out = self.run_check(history=[self.operation(at=(self.now-timedelta(minutes=59)).isoformat())])
        self.assertFalse(out['allowed'])
        self.assertEqual(next(c for c in out['checks'] if c['id'] == 'main_plan_interval')['origin'], 'user_policy')

    def test_exact_one_hour_boundary_is_allowed(self):
        out = self.run_check(history=[self.operation(at=(self.now-timedelta(hours=1)).isoformat())])
        self.assertTrue(out['allowed'])

    def test_rejected_draft_and_other_account_changes_do_not_consume_interval(self):
        recent = (self.now-timedelta(minutes=1)).isoformat()
        rows = [self.operation(state='rejected', at=recent), self.operation(id='b', state='draft', at=recent),
                self.operation(id='c', account_id='1999999999999001', at=recent)]
        self.assertTrue(self.run_check(history=rows)['allowed'])

    def test_task_changes_do_not_use_parent_main_plan_one_hour_or_twenty_percent(self):
        task = {**self.action, 'target_kind': 'control_task', 'task_id': '99999999999999',
                'action': 'control_budget_up', 'before': '100', 'after': '150'}
        row = self.operation(entity_id=task['task_id'], parent_plan_id=self.action['plan_id'],
            task_id=task['task_id'], action='control_budget_up', at=(self.now-timedelta(minutes=1)).isoformat())
        out = self.run_check(action=task, history=[row])
        self.assertTrue(out['allowed'])
        self.assertFalse(any(c['id'] in {'main_plan_interval', 'daily_budget_cap'} for c in out['checks']))

    def test_chase_budget_above_fifty_percent_blocks_without_changing_absolute_target(self):
        task = {**self.action, 'target_kind': 'control_task', 'task_id': '99999999999999',
                'action': 'control_budget_up', 'before': '1500.00', 'after': '2250.01'}
        out = self.run_check(action=task)
        self.assertFalse(out['allowed']); self.assertEqual(task['after'], '2250.01')
        self.assertEqual(next(c for c in out['checks'] if c['id'] == 'chase_budget_step')['origin'], 'user_policy')

    def test_chase_budget_exact_fifty_percent_boundary_passes(self):
        task = {**self.action, 'task_id': '99999999999999', 'action': 'control_budget_up',
                'before': '1500', 'after': '2250'}
        self.assertTrue(self.run_check(action=task)['allowed'])

    def test_chase_zero_or_missing_budget_stays_unknown(self):
        for before in (None, '0.00'):
            task = {**self.action, 'task_id': '99999999999999', 'action': 'control_budget_up',
                    'before': before, 'after': '2000'}
            out = self.run_check(action=task)
            self.assertTrue(out['allowed'])
            self.assertEqual(next(c for c in out['checks'] if c['id'] == 'chase_budget_step')['status'], 'unknown')

    def test_chase_absolute_target_is_not_interpreted_as_budget_increment(self):
        task = {**self.action, 'task_id': '99999999999999', 'action': 'control_budget_up',
                'before': '1500', 'after': '2000'}
        self.assertTrue(self.run_check(action=task)['allowed'])

    def test_task_record_cannot_poison_main_plan_identity(self):
        row = self.operation(entity_id='99999999999999', parent_plan_id=self.action['plan_id'],
                             at=(self.now-timedelta(minutes=1)).isoformat())
        self.assertTrue(self.run_check(history=[row])['allowed'])

    def test_future_operation_blocks_until_clock_is_resolved(self):
        out = self.run_check(history=[self.operation(at=(self.now+timedelta(seconds=1)).isoformat())])
        self.assertFalse(out['allowed']); self.assertTrue(any('时钟' in s for s in out['blocking_reasons']))

    def test_missing_operation_time_is_unknown_not_a_fake_zero_count(self):
        out = self.run_check(history=[self.operation(at='yesterday')])
        self.assertTrue(out['allowed']); self.assertTrue(any('操作时间' in s for s in out['warnings']))

    def test_day_budget_cap_uses_verified_day_opening_budget(self):
        ctx = {'day_initial_budget': '700', 'day_initial_budget_verified': True,
               'day_initial_budget_date': '2026-10-26'}
        out = self.run_check(ctx)
        self.assertFalse(out['allowed'])
        self.assertIn('150%', out['blocking_reasons'][0])
        self.assertTrue(self.run_check(ctx, {**self.action, 'after': '1050'})['allowed'])

    def test_first_local_before_is_not_fabricated_day_opening_budget(self):
        row = self.operation(action='budget_up', before='700', after='800')
        out = self.run_check(history=[row])
        self.assertTrue(out['allowed'])
        self.assertEqual(next(c for c in out['checks'] if c['id'] == 'daily_budget_cap')['status'], 'unknown')

    def test_previous_day_or_unverified_baseline_is_not_current_day_proof(self):
        for ctx in ({'day_initial_budget': 700, 'day_initial_budget_verified': True,
                     'day_initial_budget_date': '2026-10-25'},
                    {'day_initial_budget': 700, 'day_initial_budget_verified': False,
                     'day_initial_budget_date': '2026-10-26'}):
            self.assertTrue(self.run_check(ctx)['allowed'])

    def test_browser_cannot_self_certify_baseline_complete_history_or_settled_statistics(self):
        ctx = self.standard(history_known=True, day_initial_budget=700,
                            day_initial_budget_verified=True, day_initial_budget_date='2026-10-26',
                            guarantee_conditions=self.qualification())
        ctx['source'] = {'kind': 'browser_dom'}
        out = self.run_check(ctx)
        self.assertTrue(out['allowed']); self.assertEqual(out['status'], 'unknown')
        self.assertFalse(out['coverage']['external_history_known'])

    def test_known_excluded_tool_can_be_manually_adjusted_without_payout_promise(self):
        for ctx in ({'product_family': 'all_domain', 'tool': 'smart_boost'},
                    {'product_family': 'chengfang', 'tool': 'material_chase_volume'},
                    {'product_family': 'standard', 'delivery_mode': 'volume'}):
            out = self.run_check(ctx)
            self.assertEqual(out['status'], 'not_applicable'); self.assertTrue(out['allowed'])
            self.assertFalse(out['eligible_for_payout'])

    def test_native_material_chase_mode_maps_only_when_type_is_explicit(self):
        out = self.run_check({'product_family': 'chengfang', 'control_type': 'material_chase',
                              'control_delivery_mode': 'volume'})
        self.assertEqual(out['status'], 'not_applicable')
        missing_type = self.run_check({'product_family': 'chengfang', 'control_delivery_mode': 'volume'})
        self.assertEqual(missing_type['status'], 'unknown')

    def test_cost_control_chase_is_not_marked_unprotected_from_old_blanket_text(self):
        out = self.run_check({'product_family': 'all_domain', 'tool': 'material_chase_cost'})
        self.assertEqual(out['status'], 'unknown'); self.assertTrue(out['allowed'])

    def test_platform_invalid_badge_does_not_prevent_manual_loss_control(self):
        out = self.run_check({'platform_guarantee_status': '成本保障已失效'},
                             {**self.action, 'action': 'pause_plan', 'before': True, 'after': False})
        self.assertEqual(out['status'], 'conditions_not_met'); self.assertTrue(out['allowed'])

    def test_active_or_paid_badge_never_proves_future_eligibility(self):
        for status in ('成本保障生效中', '成本保障已到账'):
            out = self.run_check({'platform_guarantee_status': status})
            self.assertEqual(out['status'], 'unknown'); self.assertFalse(out['eligible_for_payout'])

    def test_standard_nonroi_third_bid_change_is_protected_separately_from_targeting(self):
        rows = [self.operation(id=str(i), at=(self.now-timedelta(hours=2+i)).isoformat()) for i in range(2)]
        out = self.run_check(self.standard(), self.roi_action(), rows)
        self.assertFalse(out['allowed'])
        check = next(c for c in out['checks'] if c['id'] == 'official_bid_daily')
        self.assertEqual(check['origin'], 'official_condition_protection')
        targeting_only = [self.operation(action='targeting_change', id=str(i)) for i in range(2)]
        self.assertTrue(self.run_check(self.standard(), self.roi_action(), targeting_only)['allowed'])

    def test_standard_roi_goal_does_not_inherit_nonroi_daily_limit(self):
        ctx = self.standard(optimization_goal='payment_roi')
        rows = [self.operation(id=str(i)) for i in range(3)]
        out = self.run_check(ctx, self.roi_action(), rows)
        self.assertTrue(out['allowed'])
        self.assertFalse(any(c['id'] == 'official_bid_daily' for c in out['checks']))

    def test_unknown_family_does_not_apply_search_rule_based_on_tool_name_alone(self):
        rows = [self.operation(id=str(i)) for i in range(3)]
        out = self.run_check({'tool': 'search_first_screen'}, self.roi_action(), rows)
        self.assertTrue(out['allowed'])
        self.assertFalse(any(c['id'] == 'official_bid_daily' for c in out['checks']))

    def test_search_has_independent_two_bid_change_condition(self):
        ctx = {'product_family': 'all_domain', 'scene': 'live', 'tool': 'search_first_screen',
               'bid_changes_today': 2, 'changes_date': '2026-10-26', 'history_known': True}
        out = self.run_check(ctx, self.roi_action())
        self.assertFalse(out['allowed'])
        self.assertIn('search_first_screen', {s['id'] for s in out['sources']})

    def test_declared_total_and_local_events_are_not_double_counted(self):
        ctx = self.standard(bid_changes_today=1, changes_date='2026-10-26', history_known=True)
        out = self.run_check(ctx, self.roi_action(), [self.operation()])
        self.assertTrue(out['allowed'])
        self.assertEqual(next(c for c in out['checks'] if c['id'] == 'official_bid_daily')['status'], 'pass')

    def test_repeated_same_event_id_is_counted_once(self):
        out = self.run_check(self.standard(), self.roi_action(), [self.operation(), self.operation()])
        self.assertTrue(out['allowed'])

    def test_old_day_events_do_not_count_after_china_midnight(self):
        self.now = datetime(2026, 10, 26, 2, tzinfo=CN)
        rows = [self.operation(id=str(i), at='2026-10-25T15:59:59+00:00') for i in range(2)]
        self.assertTrue(self.run_check(self.standard(), self.roi_action(), rows)['allowed'])
        rows = [self.operation(id=str(i), at='2026-10-25T16:00:00+00:00') for i in range(2)]
        self.assertFalse(self.run_check(self.standard(), self.roi_action(), rows)['allowed'])

    def test_known_failed_condition_does_not_block_unrelated_budget_change(self):
        ctx = self.standard(bid_changes_today=3, changes_date='2026-10-26')
        out = self.run_check(ctx)
        self.assertTrue(out['allowed']); self.assertEqual(out['status'], 'conditions_not_met')

    def test_unknown_standard_operation_counts_remain_unknown(self):
        ctx = self.standard(bid_changes_today=0, changes_date='2026-10-26', history_known=False)
        out = self.run_check(ctx, self.roi_action())
        self.assertTrue(out['allowed'])
        self.assertEqual(next(c for c in out['checks'] if c['id'] == 'official_bid_daily')['status'], 'unknown')

    def test_qualified_conditions_produce_estimate_only(self):
        out = self.run_check(self.standard(history_known=True, guarantee_conditions=self.qualification()))
        self.assertEqual(out['status'], 'estimated_only'); self.assertFalse(out['eligible_for_payout'])
        self.assertFalse(out['coverage']['payout_calculated'])

    def test_unfiltered_order_count_does_not_meet_qualification(self):
        ctx = self.standard(history_known=True, guarantee_conditions=self.qualification(platform_filtered_statistics=False))
        self.assertEqual(self.run_check(ctx)['status'], 'unknown')

    def test_final_filtered_insufficient_orders_is_not_met(self):
        ctx = self.standard(history_known=True, guarantee_conditions=self.qualification(eligible_orders=5))
        self.assertEqual(self.run_check(ctx)['status'], 'conditions_not_met')

    def test_search_estimate_uses_eight_orders_and_strict_less_than_eighty_percent(self):
        ctx = {'product_family': 'all_domain', 'scene': 'live', 'tool': 'search_first_screen',
               'history_known': True, 'guarantee_conditions': self.qualification(actual_roi='1.6', weighted_target_roi='2')}
        self.assertEqual(self.run_check(ctx)['status'], 'conditions_not_met')
        ctx['guarantee_conditions']['actual_roi'] = '1.59'
        self.assertEqual(self.run_check(ctx)['status'], 'estimated_only')
        ctx['guarantee_conditions']['eligible_orders'] = 7
        self.assertEqual(self.run_check(ctx)['status'], 'conditions_not_met')

    def test_conflicting_explicit_conditions_cannot_overwrite_platform_invalid_or_known_failed_history(self):
        ctx = self.standard(history_known=True, guarantee_conditions=self.qualification(),
                            platform_guarantee_status='invalid')
        self.assertEqual(self.run_check(ctx)['status'], 'conditions_not_met')
        ctx.pop('platform_guarantee_status'); ctx.update(bid_changes_today=3, changes_date='2026-10-26')
        self.assertEqual(self.run_check(ctx)['status'], 'conditions_not_met')

    def test_chengfang_marketing_roi_is_distinct_from_overall_assessment(self):
        out = self.run_check({'product_family': 'chengfang', 'scene': 'product',
                              'target_roi': None, 'roi_basis': 'overall_payment'}, self.roi_action())
        self.assertTrue(any('整体支付ROI' in s and '综合营销ROI' in s for s in out['warnings']))

    def test_naive_clock_and_invalid_input_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_cost_guarantee({}, now=datetime(2026, 10, 26))
        with self.assertRaises(ValueError):
            evaluate_cost_guarantee([], now=self.now)
        with self.assertRaises(ValueError):
            evaluate_cost_guarantee({}, history={}, now=self.now)

    def test_sources_are_direct_official_links_and_no_api_or_browser_call_is_needed(self):
        out = self.run_check()
        self.assertTrue(all(s['url'].startswith(('https://school.jinritemai.com/',
                                                'https://qianchuan.jinritemai.com/')) for s in out['sources']))
        self.assertFalse(out['coverage']['complete_current_rules_verified'])


if __name__ == '__main__':
    unittest.main()
