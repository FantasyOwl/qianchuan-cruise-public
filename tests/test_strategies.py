import copy
import json
import unittest
from datetime import timedelta

from monitor.engine import evaluate
from monitor.strategies import extend_evaluation
from test_engine import NOW, series, settings


def ready_rows(*, roi=2.5, scope="plan", basis="overall_payment", hours=4, now=NOW):
    rows = series(roi=roi, scope=scope, basis=basis, count=hours * 6 + 1, now=now)
    for row in rows:
        row["source"]["metric_period"] = "day"
        row["context"] = {"manual_reviewed": True, "model_stable": True, "compliance_ok": True,
            "anchor_ready": True, "carrying_ready": True, "promotion_peak": False,
            "plan_id": "plan1", "parent_plan_id": "parent1", "account_source_key": "account-report",
            "chase_history_known": True, "bid_change_history_known": True,
            "differentiated_targeting_verified": True, "creative_type": "custom",
            "created_at": (now - timedelta(days=4)).isoformat(), "is_primary_plan":False}
        row['context'].update(plan_roi_basis=basis,plan_roi_mode='roi_target',plan_roi_step=.01,
            plan_roi_min=.01,plan_roi_max=100,plan_roi_config_verified=True,plan_roi_observed_at=row['source']['captured_at'])
        row["metrics"].update(bid=100, target_cost=60, target_roi=2, parent_budget=1000)
    return rows


def account_row(*, roi=2.5, basis="overall_payment", now=NOW, account_id="123", ctr=3, cvr=3):
    row = ready_rows(roi=roi, scope="account", basis=basis, now=now)[-1]
    row["account_id"] = account_id
    row["entity_id"] = account_id
    row["source"]["series_key"] = "account-report"
    row["metrics"].update(spend=10000, comprehensive_cost=10000, gmv=10000 * roi,
        net_gmv=10000 * roi, ctr=ctr, cvr=cvr, control_spend=1000, control_share_pct=10)
    return row


def standard_rows():
    rows=ready_rows()
    for row in rows:
        row['promotion_type']='standard'
        row['context'].update(plan_bid_mode='conversion_cost',plan_roi_mode='conversion_cost',
            plan_bid_config_verified=True,plan_bid_observed_at=row['source']['captured_at'],
            target_cost_basis='overall_payment')
    return rows


def idle_rows(*, boundaries=True, prior_activity=False):
    rows=ready_rows(scope='creative',hours=24,roi=0)
    for row in rows:
        row['metrics'].update(spend=0,clicks=0,orders=0,gmv=0)
    if prior_activity:
        for row in rows:
            if row['source']['metric_date']!=NOW.date().isoformat():
                row['metrics'].update(spend=100,clicks=5)
    if boundaries:
        opening=next(r for r in rows if r['source']['data_at'].split('T')[1].startswith('00:00:00'))
        opening['context'].update(day_boundary_role='opening',day_boundary_verified=True)
        closing=copy.deepcopy(opening);closing['id']='prior-day-close'
        closing['source']['metric_date']=(NOW-timedelta(days=1)).date().isoformat()
        closing['context'].update(day_boundary_role='closing',day_boundary_verified=True)
        if prior_activity:closing['metrics'].update(spend=100,clicks=5)
        rows.append(closing)
    return rows


def run(rows, cfg=None, now=NOW, base=None):
    return extend_evaluation(rows, cfg or settings(), base or {}, now)


def recs(result, action):
    return [r for r in result["recommendations"] if r["action"] == action]


def rules(result):
    return {a["rule"] for a in result["alerts"]}


def roi_up_rows():
    rows=ready_rows(roi=1)
    for n,row in enumerate(rows):
        row['metrics']['target_cost']=40
        if n>18:
            row['metrics']['spend']+=(n-18)*40
            row['metrics']['gmv']+=(n-18)*40
    return rows


def creative_rows(*, roi=2.5, now=NOW):
    rows = ready_rows(roi=roi, scope="creative", now=now)
    for row in rows:
        m = row["metrics"]
        m.update(base_spend=m["spend"], base_gmv=m["gmv"], base_net_gmv=m["net_gmv"],
            base_comprehensive_cost=m["comprehensive_cost"], base_orders=m["orders"],
            base_clicks=m["clicks"], base_impressions=m["impressions"])
    return rows


class StrategiesTests(unittest.TestCase):
    def test_plan_30pct_surge_is_distinct_from_emergency_50pct(self):
        rows = ready_rows()
        for row in rows[-6:]:
            n = int(row["id"]) - 18
            row["metrics"]["spend"] += n * 40
            row["metrics"]["gmv"] += n * 100
        result = run(rows)
        self.assertIn("plan_spend_up_30pct", rules(result))
        self.assertNotIn("spend_surge_low_roi", rules(result))

    def test_cost_20pct_is_independent_anomaly(self):
        rows = ready_rows()
        for row in rows:
            row["metrics"]["target_cost"] = 40
        self.assertIn("plan_cost_up_20pct", rules(run(rows)))

    def test_four_complete_hours_zero_orders_creates_only_simulated_pause(self):
        rows = ready_rows(roi=0)
        for row in rows:
            row["metrics"]["orders"] = 0
        result = run(rows)
        r = recs(result, "pause_plan")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["mode"], "simulation_only")
        self.assertFalse(result["coverage"]["execution_enabled"])
        self.assertEqual(len(r["evidence"]["hourly"]), 4)

    def test_four_hours_does_not_substitute_daily_total_or_cross_midnight(self):
        rows = ready_rows(roi=0, hours=3)
        for row in rows:
            row["metrics"]["orders"] = 0
        self.assertFalse(recs(run(rows), "pause_plan"))
        now = NOW.replace(hour=2)
        rows = ready_rows(roi=0, hours=4, now=now)
        for row in rows:
            row["metrics"]["orders"] = 0
        self.assertFalse(recs(run(rows, now=now), "pause_plan"))

    def test_roi_target_up_uses_assessment_threshold_and_observed_setting(self):
        rows = roi_up_rows()
        r = recs(run(rows), "roi_target_up")[0]
        self.assertEqual(r["before"], 2)
        self.assertEqual(r["after"], 2.1)
        self.assertEqual(r["change_pct"], .05)
        self.assertTrue(r["eligible"])
        cfg = settings()
        cfg["accounts"][0]["target_roi"] = None
        self.assertFalse(recs(run(rows, cfg), "roi_target_up"))

    def test_observed_platform_goal_does_not_replace_user_assessment_threshold(self):
        rows = roi_up_rows()
        for row in rows:
            row["metrics"].update(target_cost=40, target_roi=10)
        r = recs(run(rows), "roi_target_up")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["before"], 10)
        self.assertEqual(r['after'],10.5)
        self.assertEqual(r['evidence']['strategy_target_roi'],2)

    def test_standard_bid_down_requires_two_hour_cooldown(self):
        rows = standard_rows()
        r = recs(run(rows), "bid_down")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["after"], 95)
        rows[-1]["context"]["last_bid_change_at"] = (NOW - timedelta(minutes=90)).isoformat()
        self.assertFalse(recs(run(rows), "bid_down")[0]["eligible"])

    def test_bid_restore_requires_real_post_change_hour(self):
        rows = standard_rows()
        change_at = NOW - timedelta(hours=1)
        for row in rows:
            row["promotion_type"] = "standard"
            row["context"]["last_bid_change_at"] = change_at.isoformat()
            row["metrics"]["previous_bid"] = 100
            if int(row["id"]) >= 18:
                row["metrics"]["bid"] = 95
                row["metrics"]["spend"] = 1800
                row["metrics"]["gmv"] = 4500
                row["metrics"]["orders"] = 36
                row["metrics"]["clicks"] = 720
                row["metrics"]["impressions"] = 18000
        r = recs(run(rows), "bid_restore")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["before"], 95)
        self.assertEqual(r["after"], 100)
        rows[-1]["context"]["last_bid_change_at"] = (NOW - timedelta(minutes=30)).isoformat()
        self.assertFalse(recs(run(rows), "bid_restore"))

    def test_material_chase_requires_four_hour_base_and_linked_account(self):
        rows = creative_rows()
        r = recs(run(rows + [account_row()]), "create_chase")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["parameters"]["budget"], 100)
        self.assertEqual(r["parameters"]["duration_hours"], 2)
        self.assertEqual(r["parameters"]["target_roi"], 2)
        self.assertFalse(recs(run(rows), "create_chase"))
        rows[-1]["context"]["last_chase_at"] = (NOW - timedelta(hours=23)).isoformat()
        self.assertFalse(recs(run(rows + [account_row()]), "create_chase"))

    def test_chase_rejects_small_parent_budget_account_share_and_wrong_account(self):
        for option in ("parent", "share", "account", "base_orders", "target"):
            with self.subTest(option=option):
                rows = creative_rows()
                baseline = account_row()
                cfg = settings()
                if option == "parent":
                    rows[-1]["metrics"]["parent_budget"] = 300
                elif option == "share":
                    baseline["metrics"]["control_share_pct"] = 21
                elif option == "account":
                    baseline["account_id"] = "other"
                elif option == "base_orders":
                    rows[13]["metrics"].pop("base_orders")
                else:
                    cfg["accounts"][0]["target_roi"] = None
                self.assertFalse(recs(run(rows + [baseline], cfg), "create_chase"))

    def test_unknown_booleans_never_become_ready(self):
        rows = creative_rows()
        rows[-1]["context"].pop("model_stable")
        result = run(rows + [account_row()])
        self.assertFalse(recs(result, "create_chase"))
        fields = str(result["coverage"]["strategies"]["material_chase_admission"]["missing"])
        self.assertIn("model_stable", fields)

    def test_one_click_boost_has_budget_duration_and_receiving_gate(self):
        rows = ready_rows()
        rows[-1]["context"]["promotion_peak"] = True
        r = recs(run(rows + [account_row()]), "one_click_boost")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["parameters"]["budget"], 100)
        self.assertEqual(r["parameters"]["duration_hours"], 1)
        rows[-1]["context"]["carrying_ready"] = None
        self.assertFalse(recs(run(rows + [account_row()]), "one_click_boost"))

    def test_peak_alone_does_not_override_bad_recent_conversion(self):
        rows = ready_rows(roi=1)
        rows[-1]["context"]["promotion_peak"] = True
        self.assertFalse(recs(run(rows + [account_row()]), "one_click_boost"))

    def test_chase_model_stable_flag_does_not_override_volatile_roi(self):
        rows = creative_rows()
        for row in rows[-6:]:
            n = int(row["id"]) - 18
            row["metrics"]["base_gmv"] += n * 200
        self.assertFalse(recs(run(rows + [account_row()]), "create_chase"))

    def test_one_plan_does_not_get_two_eligible_actions_in_one_round(self):
        rows = ready_rows()
        for row in rows:
            row["promotion_type"] = "standard"
        rows[-1]["context"]["promotion_peak"] = True
        result = run(rows + [account_row()])
        self.assertEqual(sum(r["eligible"] for r in result["recommendations"] if r["entity_id"] == "plan1"), 1)

    def test_one_click_control_needs_explicit_overload_forecast(self):
        rows = ready_rows()
        rows[-1]["context"]["carrying_ready"] = False
        rows[-1]["context"]["predicted_overload_at"] = (NOW + timedelta(minutes=4)).isoformat()
        r = recs(run(rows), "one_click_control")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["parameters"]["duration_hours"], .5)
        rows[-1]["context"]["predicted_overload_at"] = (NOW + timedelta(minutes=2)).isoformat()
        self.assertFalse(recs(run(rows), "one_click_control"))

    def test_governance_alerts_do_not_require_roi_target(self):
        row = ready_rows()[-1]
        row["context"].update(account_blocked=True, permission_restricted=True, violation_flag=True)
        row["metrics"].update(active_creatives=0, rejected_creatives=10, experience_score=4.1,
            experience_previous_score=4.5, experience_threshold=4.2)
        cfg = settings()
        cfg["accounts"][0]["target_roi"] = None
        result = run([row], cfg)
        self.assertTrue({"account_blocked", "permission_restricted", "violation_flag", "no_approved_creatives", "experience_score_drop"}.issubset(rules(result)))
        self.assertFalse(result["recommendations"])

    def test_two_live_universal_plans_same_aweme_alert_without_inferring_absence(self):
        first = ready_rows()[-1]
        first["context"]["aweme_id"] = "aweme1"
        first['context']['plan_marketing_goal']='LIVE_PROM_GOODS'
        second = copy.deepcopy(first)
        second["entity_id"] = "plan2"
        second["context"]["plan_id"] = "plan2"
        self.assertIn("aweme_universal_conflict", rules(run([first, second])))
        second["control"]["running"] = None
        self.assertNotIn("aweme_universal_conflict", rules(run([first, second])))
        second["control"]["running"] = True
        second["context"]["plan_id"] = "plan1"
        self.assertNotIn("aweme_universal_conflict", rules(run([first, second])))

    def test_creative_24h_checks_absolute_zeros_across_midnight(self):
        rows = idle_rows()
        result = run(rows)
        r = recs(result, "pause_creative")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["evidence"]["cross_day_method"], "separate_daily_deltas_with_declared_boundaries")
        rows[70]["metrics"]["clicks"] = 1
        self.assertFalse(recs(run(rows), "pause_creative"))

    def test_creative_24h_missing_samples_or_session_period_fail_closed(self):
        rows = idle_rows()
        self.assertFalse(recs(run(rows[:50] + rows[55:]), "pause_creative"))
        for row in rows:
            row["source"]["metric_period"] = "live_session"
        self.assertFalse(recs(run(rows), "pause_creative"))

    def test_prior_local_action_and_unknown_guarantee_block_eligibility(self):
        rows = roi_up_rows()
        base = evaluate(rows, settings(), NOW)
        r = recs(run(rows, base=base), "roi_target_up")[0]
        self.assertFalse(r["eligible"])
        self.assertIn("本轮已有", "".join(r["blockers"]))
        rows[-1]["control"]["guarantee_verified"] = False
        self.assertFalse(recs(run(rows), "roi_target_up")[0]["eligible"])

    def test_cross_source_or_gap_does_not_complete_four_hour_chase(self):
        rows = creative_rows()
        for i, row in enumerate(rows):
            row["source"]["series_key"] = "source-A" if i < 13 else "source-B"
        self.assertFalse(recs(run(rows + [account_row()]), "create_chase"))
        rows = creative_rows()
        self.assertFalse(recs(run(rows[:10] + rows[13:] + [account_row()]), "create_chase"))

    def test_no_input_or_malformed_metadata_produces_serializable_coverage(self):
        result = run([None, {}, {"account_id": []}])
        self.assertFalse(result["recommendations"])
        self.assertEqual(result["coverage"]["rejected_count"], 3)
        json.dumps(result, allow_nan=False)
        rows = ready_rows()
        rows[-1]["context"] = []
        json.dumps(run(rows), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
