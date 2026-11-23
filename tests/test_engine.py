import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from monitor.engine import evaluate

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 21, 20, 0, tzinfo=CN)


def settings(basis="overall_payment"):
    return {"accounts": [{"id": "123", "scene": "live", "target_roi": 2, "roi_basis": basis}], "rules": {}}


def series(roi=1.0, *, scope="plan", basis="overall_payment", count=19, now=NOW,
           spend_step=100, orders_step=2, ctr_steps=None):
    rows = []
    spend = gmv = orders = clicks = impressions = 0
    for n in range(count):
        t = now - timedelta(minutes=10 * (count - 1 - n))
        if n:
            spend += spend_step
            gmv += spend_step * roi
            orders += orders_step
            impressions += 1000
            ctr = ctr_steps[min((n - 1) // 6, len(ctr_steps) - 1)] if ctr_steps else 4
            clicks += ctr * 10
        rows.append({"id": str(n), "account_id": "123", "entity_id": "plan1", "scope": scope,
                     "scene": "live", "promotion_type": "universal", "metric_basis": basis,
                     "source": {"kind": "import", "captured_at": t.isoformat(), "data_at": t.isoformat(),
                                "metric_date": t.date().isoformat(), "series_key": "report1", "coverage": "report"},
                     "metrics": {"spend": spend, "gmv": gmv, "net_gmv": gmv, "comprehensive_cost": spend,
                                 "control_spend": spend if scope == "control" else 0, "control_gmv": gmv,
                                 "orders": orders, "impressions": impressions, "clicks": clicks,
                                 "budget": 10000, "refund_rate_1h": 1},
                     "quality": {"issues": []},
                     "control": {"running": True, "history_known": True, "guarantee_verified": True,
                                 "lifecycle": "mature", "changes_today": 0, "last_change_at": None,
                                 "budget_original": 10000, "budget_increase_today": 0}})
    return rows


def recommendations(result, action=None):
    return [r for r in result["recommendations"] if action is None or r["action"] == action]


class EngineTests(unittest.TestCase):
    def test_two_full_hours_low_roi_produces_negative_ten_percent_simulation(self):
        result = evaluate(series(), settings(), NOW)
        rec = recommendations(result, "budget_down")[0]
        self.assertEqual(rec["change_pct"], -.1)
        self.assertEqual(rec["before"], 10000)
        self.assertEqual(rec["after"], 9000)
        self.assertTrue(rec["eligible"])
        self.assertEqual(rec["mode"], "simulation_only")
        self.assertFalse(result["coverage"]["execution_enabled"])
        json.dumps(result, allow_nan=False)

    def test_stable_three_hours_allows_small_budget_simulation(self):
        result = evaluate(series(roi=2.5), settings(), NOW)
        rec = recommendations(result, "budget_up")[0]
        self.assertEqual(rec["change_pct"], .2)
        self.assertTrue(rec["eligible"])
        self.assertEqual(rec["after"], 12000)

    def test_daily_growth_cap_blocks(self):
        rows = series(roi=2.5)
        rows[-1]["control"]["budget_increase_today"] = 4000
        rec = recommendations(evaluate(rows, settings(), NOW), "budget_up")[0]
        self.assertFalse(rec["eligible"])
        self.assertTrue(any("累计上调上限" in s for s in rec["blockers"]))

    def test_unknown_guarantee_and_history_block_proposal(self):
        rows = series()
        rows[-1]["control"] = {}
        rec = recommendations(evaluate(rows, settings(), NOW), "budget_down")[0]
        self.assertFalse(rec["eligible"])
        self.assertTrue(any("成本保障" in s for s in rec["blockers"]))
        self.assertTrue(any("历史未知" in s for s in rec["blockers"]))

    def test_cooldown_and_visible_only_block(self):
        rows = series()
        rows[-1]["control"]["last_change_at"] = (NOW - timedelta(minutes=30)).isoformat()
        rows[-1]["source"]["coverage"] = "visible_only"
        rec = recommendations(evaluate(rows, settings(), NOW), "budget_down")[0]
        self.assertFalse(rec["eligible"])
        self.assertTrue(any("冷却" in s for s in rec["blockers"]))
        self.assertTrue(any("可见页面" in s for s in rec["blockers"]))

    def test_stale_data_is_never_treated_as_realtime(self):
        result = evaluate(series(now=NOW - timedelta(days=1)), settings(), NOW)
        self.assertEqual(result["entities"][0]["quality"], "stale")
        self.assertFalse(result["recommendations"])

    def test_recent_capture_does_not_hide_stale_data_time(self):
        rows = series(now=NOW - timedelta(hours=3))
        rows[-1]["source"]["captured_at"] = NOW.isoformat()
        self.assertEqual(evaluate(rows, settings(), NOW)["entities"][0]["quality"], "stale")

    def test_missing_data_time_is_not_replaced_by_capture_time(self):
        rows = series()
        for row in rows:
            row["source"]["data_at"] = None
        result = evaluate(rows, settings(), NOW)
        self.assertFalse(result["recommendations"])
        self.assertEqual(result["entities"][0]["quality"], "insufficient")

    def test_cumulative_daily_roi_is_not_hourly_evidence(self):
        rows = series(count=1)
        rows[0]["metrics"].update(spend=9000, gmv=2000, roi=.2, orders=100)
        self.assertFalse(evaluate(rows, settings(), NOW)["recommendations"])

    def test_sampling_gap_blocks_hourly_evidence(self):
        rows = [r for i, r in enumerate(series()) if i not in {9, 10, 11}]
        self.assertFalse(recommendations(evaluate(rows, settings(), NOW), "budget_down"))

    def test_reset_rejects_window(self):
        rows = series()
        rows[13]["metrics"]["spend"] = 0
        self.assertFalse(recommendations(evaluate(rows, settings(), NOW), "budget_down"))

    def test_midnight_does_not_combine_statistics_dates(self):
        now = NOW.replace(hour=1, minute=0)
        result = evaluate(series(now=now), settings(), now)
        self.assertFalse(recommendations(result, "budget_down"))
        self.assertFalse(recommendations(result, "budget_up"))

    def test_different_sources_are_not_combined(self):
        rows = series()
        for i, row in enumerate(rows):
            row["source"]["series_key"] = str(i % 3)
        self.assertFalse(recommendations(evaluate(rows, settings(), NOW), "budget_down"))

    def test_different_bases_are_not_combined(self):
        rows = series()
        for i, row in enumerate(rows):
            row["metric_basis"] = "net" if i < 9 else "overall_payment"
        result = evaluate(rows, settings(), NOW)
        self.assertEqual(len(result["entities"]), 2)
        self.assertFalse(recommendations(result, "budget_down"))

    def test_net_roi_uses_net_numerator(self):
        rows = series(roi=3, basis="net")
        for row in rows:
            row["metrics"]["net_gmv"] = row["metrics"]["spend"]
        result = evaluate(rows, settings("net"), NOW)
        self.assertEqual(result["entities"][0]["hourly"][-1]["roi"], 1)
        self.assertTrue(recommendations(result, "budget_down"))

    def test_marketing_requires_comprehensive_cost(self):
        rows = series(basis="marketing")
        for row in rows:
            row["metrics"].pop("comprehensive_cost")
        result = evaluate(rows, settings("marketing"), NOW)
        self.assertFalse(recommendations(result, "budget_down"))
        self.assertIsNone(result["entities"][0]["hourly"][-1]["roi"])

    def test_control_uses_separate_spend_and_gmv(self):
        rows = series(scope="control", basis="control_payment")
        for row in rows:
            row["metrics"]["gmv"] = row["metrics"]["spend"] * 20
        result = evaluate(rows, settings("control_payment"), NOW)
        rec = recommendations(result, "pause_control")[0]
        self.assertTrue(rec["eligible"])
        self.assertEqual(rec["evidence"]["hourly"]["roi"], 1)

    def test_control_missing_separate_metrics_does_not_fall_back(self):
        rows = series(scope="control", basis="control_payment")
        for row in rows:
            row["metrics"].pop("control_spend")
        result = evaluate(rows, settings("control_payment"), NOW)
        self.assertFalse(recommendations(result, "pause_control"))

    def test_main_plan_additional_investment_requires_split(self):
        rows = series()
        for row in rows:
            row["metrics"]["control_spend"] = row["metrics"]["spend"] * .3
        result = evaluate(rows, settings(), NOW)
        self.assertFalse(recommendations(result, "budget_down"))

    def test_net_and_marketing_control_spend_require_basis_specific_base_data(self):
        for basis in ("net", "marketing"):
            with self.subTest(basis=basis):
                rows = series(basis=basis)
                for row in rows:
                    row["metrics"]["control_spend"] = row["metrics"]["spend"] * .1
                result = evaluate(rows, settings(basis), NOW)
                self.assertFalse(recommendations(result, "budget_down"))
                self.assertFalse(recommendations(result, "budget_up"))

    def test_explicit_base_ledger_is_used_when_control_spend_is_unknown(self):
        rows = series(roi=3)
        for row in rows:
            m = row["metrics"]
            m.pop("control_spend")
            m["base_spend"] = m["spend"]
            m["base_gmv"] = m["spend"]
            m["base_orders"] = m["orders"]
        result = evaluate(rows, settings(), NOW)
        rec = recommendations(result, "budget_down")[0]
        self.assertTrue(rec["eligible"])
        self.assertEqual(rec["evidence"]["hourly"][-1]["roi"], 1)
        self.assertEqual(rec["evidence"]["hourly"][-1]["ledger"], "base")

    def test_base_net_and_marketing_ledgers_use_exact_numerator_and_cost(self):
        for basis in ("net", "marketing"):
            with self.subTest(basis=basis):
                rows = series(roi=3, basis=basis)
                for row in rows:
                    m = row["metrics"]
                    m["control_spend"] = m["spend"] * .1
                    m["base_spend"] = m["spend"] * .9
                    m["base_net_gmv"] = m["spend"] * .9
                    m["base_comprehensive_cost"] = m["spend"] * 1.8
                    m["base_orders"] = m["orders"]
                result = evaluate(rows, settings(basis), NOW)
                rec = recommendations(result, "budget_down")[0]
                self.assertTrue(rec["eligible"])
                self.assertEqual(rec["evidence"]["hourly"][-1]["roi"], 1 if basis == "net" else .5)
                self.assertIsNone(rec["evidence"]["hourly"][-1]["ctr"])
                self.assertIsNone(rec["evidence"]["hourly"][-1]["cvr"])

    def test_total_orders_cannot_qualify_a_small_base_sample(self):
        rows = series(roi=3)
        for row in rows:
            m = row["metrics"]
            m["control_spend"] = m["spend"] * .1
            m["base_spend"] = m["spend"] * .9
            m["base_gmv"] = m["spend"] * .9
            m["base_orders"] = m["orders"] / 2
            m["orders"] *= 10
        result = evaluate(rows, settings(), NOW)
        self.assertFalse(recommendations(result, "budget_down"))
        self.assertFalse(recommendations(result, "budget_up"))

    def test_missing_base_orders_at_any_window_sample_blocks_base_roi_rule(self):
        rows = series(roi=3)
        for row in rows:
            m = row["metrics"]
            m["control_spend"] = m["spend"] * .1
            m["base_spend"] = m["spend"] * .9
            m["base_gmv"] = m["spend"] * .9
            m["base_orders"] = m["orders"]
        rows[13]["metrics"].pop("base_orders")
        result = evaluate(rows, settings(), NOW)
        self.assertFalse(recommendations(result, "budget_down"))

    def test_main_plan_control_ledger_reset_invalidates_hour(self):
        rows = series()
        for row in rows[:-1]:
            row["metrics"]["control_spend"] = 1
        result = evaluate(rows, settings(), NOW)
        self.assertFalse(recommendations(result, "budget_down"))

    def test_unknown_target_or_mismatched_basis_blocks_roi_rule(self):
        cfg = settings()
        cfg["accounts"][0]["target_roi"] = None
        self.assertFalse(recommendations(evaluate(series(), cfg, NOW), "budget_down"))
        self.assertFalse(recommendations(evaluate(series(basis="net"), settings(), NOW), "budget_down"))

    def test_zero_values_do_not_mean_missing(self):
        rows = series(spend_step=0, orders_step=0)
        result = evaluate(rows, settings(), NOW)
        self.assertTrue(any(a["rule"] == "no_spend_2h" for a in result["alerts"]))
        self.assertFalse(recommendations(result, "budget_down"))

    def test_small_order_sample_does_not_trigger_roi_decision(self):
        result = evaluate(series(orders_step=1), settings(), NOW)
        self.assertFalse(recommendations(result, "budget_down"))

    def test_creative_ctr_uses_click_and_impression_differences(self):
        result = evaluate(series(scope="creative", ctr_steps=[5, 4, 3]), settings(), NOW)
        self.assertTrue(any(a["rule"] == "creative_ctr_decay_3h" for a in result["alerts"]))

    def test_refund_spike_blocks_growth(self):
        rows = series(roi=3)
        rows[-1]["metrics"]["refund_rate_1h"] = 9
        result = evaluate(rows, settings(), NOW)
        self.assertTrue(recommendations(result, "hold_scaling"))
        self.assertFalse(recommendations(result, "budget_up")[0]["eligible"])

    def test_nonfinite_and_malformed_inputs_do_not_crash_or_escape(self):
        rows = series()
        rows[-1]["metrics"]["gmv"] = float("nan")
        rows += [None, [], {"account_id": []}, {"account_id": "other"}]
        result = evaluate(rows, settings(), NOW)
        self.assertFalse(result["recommendations"])
        self.assertGreaterEqual(result["coverage"]["invalid_count"], 4)
        json.dumps(result, allow_nan=False)
        json.dumps(evaluate(None, None, NOW), allow_nan=False)

    def test_malformed_group_keys_are_rejected(self):
        bad = copy.deepcopy(series()[-1])
        bad["scene"] = []
        result = evaluate([bad], settings(), NOW)
        self.assertEqual(result["coverage"]["invalid_count"], 1)
        self.assertFalse(result["entities"])

    def test_demo_can_qualify_only_for_offline_simulation(self):
        rows = series()
        for row in rows:
            row["source"]["kind"] = "demo"
        result = evaluate(rows, settings(), NOW)
        rec = recommendations(result, "budget_down")[0]
        self.assertTrue(rec["eligible"])
        self.assertEqual(rec["mode"], "simulation_only")
        self.assertFalse(result["coverage"]["execution_enabled"])

    def test_duplicate_data_timestamp_conflicts_fail_closed(self):
        rows = series()
        duplicate = copy.deepcopy(rows[-2])
        duplicate["metrics"]["spend"] += 1
        duplicate["source"]["captured_at"] = NOW.isoformat()
        rows.append(duplicate)
        self.assertFalse(recommendations(evaluate(rows, settings(), NOW), "budget_down"))

    def test_naive_times_use_china_timezone(self):
        rows = series()
        for row in rows:
            for field in ("captured_at", "data_at"):
                row["source"][field] = row["source"][field].replace("+08:00", "")
        self.assertTrue(recommendations(evaluate(rows, settings(), NOW), "budget_down"))

    def test_account_scope_never_receives_plan_budget_changes(self):
        result = evaluate(series(scope="account"), settings(), NOW)
        self.assertFalse(recommendations(result, "budget_down"))


if __name__ == "__main__":
    unittest.main()
