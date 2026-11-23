import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from monitor.outcomes import review_outcomes

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 22, 18, 0, tzinfo=CN)


def fixture(action="budget_up", *, source="manual_verified_report", post_rate=100, basis="overall_payment", scope="plan"):
    cfg = {"accounts": [{"id": "123", "scene": "live", "target_roi": 2, "roi_basis": basis}], "rules": {}}
    before_at, after_at = NOW - timedelta(hours=2, minutes=10), NOW - timedelta(hours=2)
    field = "bid" if action in {"bid_down", "bid_restore"} else "target_roi" if action.startswith("roi_target_") else "running" if action.startswith(("pause_", "resume_")) else "budget"
    old, new = (100, 95) if field == "bid" else (2, 1.9) if field == "target_roi" else (True, False) if field == "running" else (1000, 1200)
    if action in {"budget_down"}:new = 900
    if action == "roi_target_up":new = 2.1
    if action == "bid_restore":old, new = 95, 100
    if action == "resume_plan":old, new = False, True
    event = {"id": "reported-change-1", "account_id": "123", "entity_id": "plan1", "scope": scope,
             "action": action, "metric_basis": basis, "source_key": "exact-report", "source": source,
             "status": "simulated" if source == "simulation" else "reported_applied",
             "before_at": before_at.isoformat(), "after_at": after_at.isoformat(),
             "before": {field: old}, "after": {field: new}}
    rows = []
    spend = revenue = orders = 0
    for n in range(26):
        stamp = before_at - timedelta(hours=2) + timedelta(minutes=n * 10)
        if n:
            inc = post_rate if stamp > after_at else 100
            spend += inc; revenue += inc * 3; orders += 2 if inc else 0
        value = old if stamp <= before_at else new
        metrics = {"spend": spend, "gmv": revenue, "net_gmv": revenue, "comprehensive_cost": spend,
                   "orders": orders, "budget": 1000, "bid": 100, "target_roi": 2,
                   "control_spend": spend if basis == "control_payment" else 0, "control_gmv": revenue}
        control = {"running": True}
        if field == "running":control["running"] = value
        else:metrics[field] = value
        rows.append({"id": f"row-{n}", "account_id": "123", "entity_id": "plan1", "scope": scope,
                     "scene": "live", "promotion_type": "standard" if field == "bid" else "universal",
                     "metric_basis": basis, "source": {"kind": "demo" if source == "simulation" else "import",
                     "series_key": "exact-report", "captured_at": stamp.isoformat(), "data_at": stamp.isoformat(),
                     "metric_date": stamp.date().isoformat(), "metric_period": "day", "coverage": "report"},
                     "quality": {"issues": []}, "metrics": metrics, "control": control})
    return event, rows, cfg


def review(event, rows, cfg, now=NOW):
    return review_outcomes([event], rows, cfg, now)

def post_middle(event,rows):
    # Change an observation inside the actual one-hour post-change window.
    at=datetime.fromisoformat(event['after_at'])+timedelta(minutes=30)
    return next(row for row in rows if datetime.fromisoformat(row['source']['data_at'])==at)


class OutcomeReviewTests(unittest.TestCase):
    def test_budget_change_reports_observed_values_without_claiming_causal_profit_or_execution(self):
        event, rows, cfg = fixture()
        result = review(event, rows, cfg)
        item = result["items"][0]
        self.assertEqual(item["review_status"], "observed")
        self.assertFalse(item["platform_verified"])
        self.assertFalse(item["comparison"]["causal_effect_proven"])
        self.assertFalse(item["comparison"]["profit_effect_proven"])
        self.assertEqual(item["evidence"]["before_observation"]["roi"], 3)
        self.assertEqual(result["summary"]["manual_reported_applied"], 1)
        self.assertEqual(result["summary"]["platform_confirmed_applied"], 0)
        self.assertFalse(result["executed"]); self.assertEqual(result["candidates"], [])

    def test_bid_drop_and_two_hour_spend_collapse_produces_only_review_candidate(self):
        event, rows, cfg = fixture("bid_down", post_rate=40)
        result = review(event, rows, cfg)
        self.assertEqual(result["items"][0]["review_status"], "observed")
        candidate = result["candidates"][0]
        self.assertEqual((candidate["before"], candidate["after"]), (95, 100))
        self.assertAlmostEqual(candidate["observed_spend_drop_percent"], 60)
        self.assertEqual(candidate["threshold_percent"], 50)
        self.assertEqual(candidate["mode"], "simulation_only")
        self.assertFalse(candidate["execution_eligible"]); self.assertFalse(candidate["executed"])

    def test_fifty_percent_boundary_and_smaller_drop(self):
        for rate, expected in ((50, 1), (51, 0), (0, 1)):
            with self.subTest(rate=rate):
                event, rows, cfg = fixture("bid_down", post_rate=rate)
                self.assertEqual(len(review(event, rows, cfg)["candidates"]), expected)

    def test_one_hour_is_not_two_hour_bid_observation(self):
        event, rows, cfg = fixture("bid_down", post_rate=40)
        result = review(event, rows, cfg, NOW - timedelta(minutes=30))
        self.assertEqual(result["items"][0]["review_status"], "pending_window")
        self.assertFalse(result["candidates"])

    def test_simulation_is_separate_and_cannot_claim_actual_application(self):
        event, rows, cfg = fixture("bid_down", source="simulation", post_rate=40)
        result = review(event, rows, cfg)
        self.assertEqual(result["summary"]["manual_reported_applied"], 0)
        self.assertEqual(result["summary"]["simulated"], 1)
        self.assertEqual(result["candidates"][0]["source"], "simulation")
        event["status"] = "reported_applied"
        self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "invalid")

    def test_manual_and_synthetic_snapshots_cannot_substitute_for_one_another(self):
        for source, other_kind in (("simulation", "import"), ("manual_verified_report", "demo")):
            event, rows, cfg = fixture(source=source)
            for row in rows:row["source"]["kind"] = other_kind
            result = review(event, rows, cfg)
            self.assertEqual(result["items"][0]["review_status"], "insufficient")

    def test_rejected_failed_unknown_never_become_applied_on_http_or_local_flags(self):
        event, rows, cfg = fixture()
        for status in ("rejected", "failed", "unknown"):
            event["status"] = status
            event.update(http_status=200, eligible=True, platform_verified=True)
            event.pop("before_at", None);event.pop("after_at", None)
            result = review(event, rows, cfg)
            self.assertEqual(result["items"][0]["review_status"], "not_applied")
            self.assertFalse(result["candidates"])
            self.assertEqual(result["summary"]["manual_reported_applied"], 0)

    def test_source_action_scope_and_account_are_explicit(self):
        mutations = ({"source": "platform_certified"}, {"scope": "account"}, {"action": "execute"},
                     {"account_id": "other"}, {"metric_basis": "unknown"}, {"source_key": ""})
        for mutation in mutations:
            event, rows, cfg = fixture();event.update(mutation)
            self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "invalid")

    def test_matching_is_exact_for_entity_basis_and_source(self):
        for field, value in (("entity_id", "another"), ("metric_basis", "net"), ("source_key", "another-report")):
            event, rows, cfg = fixture();event[field] = value
            result = review(event, rows, cfg)
            self.assertEqual(result["items"][0]["review_status"], "insufficient")

    def test_declared_values_must_match_all_pre_and_post_samples(self):
        for mutate in (lambda e, rs:e["before"].update(budget=999),
                       lambda e, rs:e["after"].update(budget=1300),
                       lambda e, rs:post_middle(e,rs)["metrics"].update(budget=1250)):
            event, rows, cfg = fixture();mutate(event, rows)
            result = review(event, rows, cfg)
            self.assertEqual(result["items"][0]["review_status"], "insufficient")

    def test_missing_timestamp_never_uses_capture_time(self):
        event, rows, cfg = fixture();rows[-2]["source"]["data_at"] = None
        self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "insufficient")

    def test_missing_interior_amount_or_counter_reset_invalidates_window(self):
        for bad in (None, 0):
            event, rows, cfg = fixture();post_middle(event,rows)["metrics"]["gmv"] = bad
            self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "insufficient")

    def test_sampling_gap_is_not_hidden_by_boundary_samples(self):
        event, rows, cfg = fixture();rows = rows[:14] + rows[17:]
        self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "insufficient")

    def test_conflicting_same_timestamp_values_are_not_arbitrarily_chosen(self):
        event, rows, cfg = fixture();duplicate = copy.deepcopy(post_middle(event,rows))
        duplicate["metrics"]["gmv"] += 1;duplicate["id"] = "conflict";rows.append(duplicate)
        self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "insufficient")

    def test_mixed_periods_and_live_session_starts_are_rejected(self):
        for variant in ("period", "start"):
            event, rows, cfg = fixture()
            for row in rows:row["source"].update(metric_period="live_session", period_start=NOW.replace(hour=10).isoformat())
            if variant == "period":post_middle(event,rows)["source"]["metric_period"] = "day"
            else:post_middle(event,rows)["source"]["period_start"] = NOW.replace(hour=11).isoformat()
            self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "insufficient")

    def test_cross_midnight_window_fails_closed(self):
        event, rows, cfg = fixture()
        event["before_at"] = NOW.replace(hour=23, minute=20, day=21).isoformat()
        event["after_at"] = NOW.replace(hour=23, minute=30, day=21).isoformat()
        result = review(event, rows, cfg, NOW.replace(hour=0, minute=30))
        self.assertEqual(result["items"][0]["review_status"], "insufficient")
        self.assertFalse(result["candidates"])

    def test_historical_same_day_outcome_is_labelled_as_history_not_current_execution(self):
        event, rows, cfg = fixture()
        result = review(event, rows, cfg, NOW + timedelta(days=2))
        self.assertEqual(result["items"][0]["review_status"], "observed")
        self.assertEqual(result["items"][0]["evidence"]["after_at"], event["after_at"])
        self.assertFalse(result["executed"])

    def test_base_plan_and_marketing_cost_use_their_own_ledgers(self):
        event, rows, cfg = fixture(basis="marketing")
        for row in rows:
            m = row["metrics"];m.update(base_spend=m["spend"], base_net_gmv=m["net_gmv"],
                                       base_comprehensive_cost=m["spend"]*2, base_orders=m["orders"])
            m["gmv"] *= 99;m["comprehensive_cost"] *= 10
        item = review(event, rows, cfg)["items"][0]
        self.assertEqual(item["review_status"], "observed")
        self.assertEqual(item["evidence"]["before_observation"]["roi"], 1.5)
        self.assertEqual(item["evidence"]["before_observation"]["ledger"], "base")

    def test_control_budget_up_and_roi_target_down_are_supported(self):
        event, rows, cfg = fixture("control_budget_up", scope="control", basis="control_payment")
        for row in rows:row["metrics"]["spend"] *= 10
        item = review(event, rows, cfg)["items"][0]
        self.assertEqual(item["review_status"], "observed")
        self.assertEqual(item["evidence"]["before_observation"]["ledger"], "control")
        event, rows, cfg = fixture("roi_target_down")
        self.assertEqual(review(event, rows, cfg)["items"][0]["review_status"], "observed")

    def test_wrong_bid_product_never_generates_restore_candidate(self):
        event, rows, cfg = fixture("bid_down", post_rate=40)
        for row in rows:row["promotion_type"] = "unknown"
        self.assertEqual(review(event, rows, cfg)["candidates"], [])

    def test_zero_or_tiny_prechange_spend_does_not_qualify_a_collapse(self):
        event, rows, cfg = fixture("bid_down", post_rate=0)
        for row in rows:
            row["metrics"]["spend"] /= 1000
            row["metrics"]["gmv"] /= 1000
        result = review(event, rows, cfg)
        self.assertEqual(result["items"][0]["review_status"], "insufficient")
        self.assertFalse(result["candidates"])

    def test_expiry_only_reports_due_and_never_confirms_task_stop(self):
        event, rows, cfg = fixture()
        for action in ("one_click_boost", "one_click_control", "create_chase"):
            event.update(action=action, scope="control", duration_minutes=60)
            result = review(event, [], cfg)
            item = result["items"][0]
            self.assertEqual(item["review_status"], "expired_pending_verification")
            self.assertFalse(item["expiry"]["task_stop_confirmed"])
            self.assertFalse(result["candidates"]);self.assertFalse(result["executed"])

    def test_expiry_has_no_default_duration_and_future_tool_stays_pending(self):
        event, rows, cfg = fixture();event.update(action="one_click_control", scope="control")
        self.assertEqual(review(event, [], cfg)["items"][0]["review_status"], "invalid")
        event["duration_minutes"] = 180
        self.assertEqual(review(event, [], cfg)["items"][0]["review_status"], "duration_pending")

    def test_duplicate_event_id_cannot_repeat_a_restore_candidate(self):
        event, rows, cfg = fixture("bid_down", post_rate=40)
        result = review_outcomes([event, copy.deepcopy(event)], rows, cfg, NOW)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["items"][1]["review_status"], "invalid")

    def test_malformed_events_are_json_safe_and_do_not_escape(self):
        event, rows, cfg = fixture()
        for bad in (None, [], {}, {"account_id": []}, {**event, "source": []},
                    {**event, "before": {"budget": float("nan")}}):
            result = review_outcomes([bad], rows, cfg, NOW)
            self.assertEqual(result["items"][0]["review_status"], "invalid")
            json.dumps(result, allow_nan=False)
        self.assertTrue(review_outcomes(None, None, None, NOW)["input_errors"])


if __name__ == "__main__":
    unittest.main()
