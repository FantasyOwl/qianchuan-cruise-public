import copy
import json
import unittest
from datetime import timedelta

from monitor.specialized import analyze_specialized
from test_engine import NOW, settings
from test_strategies import ready_rows


def analyze(rows, cfg=None, now=NOW):
    return analyze_specialized(rows, cfg or settings(), now)


def actions(result, action):
    return [r for r in result["recommendations"] if r["action"] == action]


def alert_rules(result):
    return {r["rule"] for r in result["alerts"]}


def subledger(rows, prefix, roi):
    for row in rows:
        m = row["metrics"]
        m.update({prefix + "spend": m["spend"], prefix + "gmv": m["spend"] * roi,
            prefix + "net_gmv": m["spend"] * roi, prefix + "comprehensive_cost": m["spend"],
            prefix + "orders": m["orders"], prefix + "clicks": m["clicks"], prefix + "impressions": m["impressions"]})
    return rows


def search_rows(roi=1, overall_roi=5):
    rows = subledger(ready_rows(roi=overall_roi, scope="keyword", hours=3), "search_", roi)
    for row in rows:
        row["promotion_type"] = "search"
        row["context"].update(search_ledger_verified=True, keyword_id="keyword1")
    return rows


def smart_rows(roi=1, overall_roi=5):
    rows = subledger(ready_rows(roi=overall_roi, hours=3), "smart_", roi)
    for row in rows:
        row["context"].update(smart_ledger_verified=True, smart_enabled=True)
    return rows


def cold_rows():
    rows = ready_rows(scope="creative", hours=3)
    for row in rows:
        row["scene"] = "product"
        row["context"].update(content_format="image_text", industry_baseline_verified=True,
            created_at=(NOW-timedelta(days=2)).isoformat(),
            industry_baseline_source="PRIVATE_REPORT_SOURCE_DO_NOT_DISCLOSE",
            industry_baseline_at=(NOW - timedelta(days=2)).isoformat(), industry_id="women_top", baseline_industry_id="women_top")
        row["metrics"]["industry_ctr"] = 5
    return rows


def declining_rows(base=False, scope="creative"):
    rows = ready_rows(scope=scope, hours=3)
    cumulative = {k: 0 for k in ("spend", "gmv", "orders", "clicks", "impressions")}
    values = {"spend": [600, 500, 400], "gmv": [1500, 1000, 600], "orders": [72, 48, 24],
              "clicks": [600, 360, 180], "impressions": [12000, 9000, 6000]}
    for i, row in enumerate(rows):
        if i:
            hour = (i - 1) // 6
            for k in cumulative:
                cumulative[k] += values[k][hour] / 6
        row["metrics"].update(cumulative)
        row["metrics"].update(net_gmv=cumulative["gmv"], comprehensive_cost=cumulative["spend"], cpm=999999)
        if base:
            for field in ("spend", "gmv", "orders", "clicks", "impressions"):
                row["metrics"]["base_" + field] = cumulative[field]
            row["metrics"].update(base_net_gmv=cumulative["gmv"], base_comprehensive_cost=cumulative["spend"])
            # Inclusive totals tell a different story and must be ignored.
            row["metrics"].update(spend=i * 100, gmv=i * 1000, orders=i * 20, clicks=i * 100, impressions=i * 10000)
    return rows


class SpecializedTests(unittest.TestCase):
    def test_search_uses_only_search_ledger_not_overall_roi(self):
        result = analyze(search_rows(roi=1, overall_roi=5))
        r = actions(result, "review_keyword_reduce")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["evidence"]["ledger"], "search_only")
        self.assertEqual(r["evidence"]["hourly"][-1]["roi"], 1)
        self.assertEqual(r["mode"], "simulation_only")

    def test_high_search_roi_candidate_can_coexist_with_bad_overall_roi(self):
        result = analyze(search_rows(roi=3, overall_roi=1))
        self.assertTrue(actions(result, "review_keyword_expand")[0]["eligible"])
        self.assertFalse(actions(result, "review_keyword_reduce"))

    def test_search_roi_label_alone_is_not_a_subledger(self):
        rows = ready_rows(scope="keyword", hours=3)
        for row in rows:
            row["promotion_type"] = "search"
            row["context"]["search_ledger_verified"] = True
            row["metrics"]["search_roi"] = 3
        result = analyze(rows)
        self.assertFalse(result["recommendations"])
        self.assertEqual(result["coverage"]["specialized"]["search_keywords"]["state"], "insufficient")

    def test_search_requires_correct_basis_and_user_goal(self):
        cfg = settings()
        cfg["accounts"][0]["target_roi"] = None
        self.assertFalse(actions(analyze(search_rows(), cfg), "review_keyword_reduce"))
        rows = search_rows()
        for row in rows:
            row["metric_basis"] = "net"
        self.assertFalse(actions(analyze(rows), "review_keyword_reduce"))

    def test_search_net_roi_requires_search_net_gmv(self):
        rows = search_rows()
        for row in rows:
            row["metric_basis"] = "net"
            row["metrics"]["search_gmv"] *= 10
        cfg = settings("net")
        r = actions(analyze(rows, cfg), "review_keyword_reduce")[0]
        self.assertEqual(r["evidence"]["hourly"][-1]["roi"], 1)
        for row in rows:
            row["metrics"].pop("search_net_gmv")
        self.assertFalse(actions(analyze(rows, cfg), "review_keyword_reduce"))

    def test_smart_uses_smart_roi_for_close_and_keep(self):
        low = analyze(smart_rows(roi=1, overall_roi=5))
        high = analyze(smart_rows(roi=3, overall_roi=1))
        self.assertTrue(actions(low, "review_disable_smart")[0]["eligible"])
        self.assertTrue(actions(high, "retain_smart")[0]["eligible"])
        self.assertEqual(actions(low, "review_disable_smart")[0]["evidence"]["ledger"], "smart_only")

    def test_overall_and_smart_share_never_replace_smart_amounts(self):
        rows = ready_rows(roi=1, hours=3)
        for row in rows:
            row["context"].update(smart_ledger_verified=True, smart_enabled=True)
            row["metrics"]["smart_share"] = 40
        self.assertFalse(actions(analyze(rows), "review_disable_smart"))

    def test_disabled_or_unknown_smart_setting_is_not_closed_again(self):
        for enabled in (False, None):
            rows = smart_rows()
            rows[-1]["context"]["smart_enabled"] = enabled
            self.assertFalse(actions(analyze(rows), "review_disable_smart"))

    def test_cold_ctr_requires_explicit_benchmark_and_adequate_sample(self):
        result = analyze(cold_rows())
        r = actions(result, "review_cold_creative")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["evidence"]["industry_ctr"], 5)
        self.assertEqual(r["evidence"]["ctr"], 4)
        self.assertNotIn("PRIVATE_REPORT_SOURCE_DO_NOT_DISCLOSE", json.dumps(result))
        rows = cold_rows()
        rows[-1]["metrics"]["impressions"] = 999
        rows[-1]["metrics"]["clicks"] = 1
        self.assertFalse(actions(analyze(rows), "review_cold_creative"))

    def test_missing_or_mismatched_industry_baseline_is_never_invented(self):
        for mutation in ("value", "source", "industry", "date", "verify"):
            with self.subTest(mutation=mutation):
                rows = cold_rows()
                last = rows[-1]
                if mutation == "value":
                    last["metrics"].pop("industry_ctr")
                elif mutation == "source":
                    last["context"].pop("industry_baseline_source")
                elif mutation == "industry":
                    last["context"]["baseline_industry_id"] = "other"
                elif mutation == "date":
                    last["context"]["industry_baseline_at"] = (NOW - timedelta(days=31)).isoformat()
                else:
                    last["context"]["industry_baseline_verified"] = None
                self.assertFalse(actions(analyze(rows), "review_cold_creative"))

    def test_cold_ctr_is_independent_of_a_missing_roi_target(self):
        cfg = settings()
        cfg["accounts"][0]["target_roi"] = None
        result = analyze(cold_rows(), cfg)
        self.assertTrue(actions(result, "review_cold_creative"))
        self.assertFalse(actions(result, "review_keyword_expand"))

    def test_mature_asset_is_not_mislabeled_cold(self):
        rows = cold_rows()
        rows[-1]["context"]["created_at"] = (NOW - timedelta(days=4)).isoformat()
        self.assertFalse(actions(analyze(rows), "review_cold_creative"))

    def test_composite_decline_uses_hourly_cpm_not_daily_label(self):
        result = analyze(declining_rows())
        r = actions(result, "replace_creative")[0]
        self.assertTrue(r["eligible"])
        windows = r["evidence"]["hourly"]
        self.assertAlmostEqual(windows[0]["cpm"], 50)
        self.assertAlmostEqual(windows[-1]["cpm"], 400 / 6000 * 1000)
        self.assertTrue(all(w["cpm_period"] == "hour" for w in windows))

    def test_existing_base_ledger_wins_over_inclusive_material_totals(self):
        result = analyze(declining_rows(base=True))
        r = actions(result, "replace_creative")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["evidence"]["hourly"][-1]["ledger"], "base")
        self.assertAlmostEqual(r["evidence"]["hourly"][-1]["roi"], 1.5)
        rows = declining_rows(base=True)
        rows[13]["metrics"].pop("base_orders")
        self.assertFalse(actions(analyze(rows), "replace_creative"))

    def test_partial_base_label_never_falls_back_to_totals(self):
        rows = declining_rows()
        for row in rows:
            row["metrics"]["base_roi"] = 3
        self.assertFalse(actions(analyze(rows), "replace_creative"))

    def test_three_signal_decline_is_not_invented_from_missing_fourth(self):
        rows = declining_rows()
        for row in rows:
            # Constant hourly CPM removes the rising CPM signal.
            row["metrics"]["impressions"] = row["metrics"]["spend"] * 20
        self.assertFalse(actions(analyze(rows), "replace_creative"))

    def test_primary_plan_decline_prioritizes_creatives_without_pause(self):
        rows = declining_rows(scope="plan")
        for row in rows:
            row["context"].update(is_primary_plan=True, creative_replacement_failed=True)
        result = analyze(rows)
        r = actions(result, "refresh_creatives")[0]
        self.assertTrue(r["eligible"])
        self.assertEqual(r["target_id"], "plan1")
        self.assertFalse(any("pause" in x["action"] for x in result["recommendations"]))

    def test_primary_creative_advice_targets_parent_plan_not_material_id(self):
        rows = declining_rows()
        rows[-1]["context"]["is_primary_plan"] = True
        r = actions(analyze(rows), "refresh_creatives")[0]
        self.assertEqual(r["target_id"], "parent1")
        rows = cold_rows()
        rows[-1]["context"]["is_primary_plan"] = True
        r = actions(analyze(rows), "refresh_creatives")[0]
        self.assertEqual(r["target_id"], "parent1")

    def test_duplicate_count_requires_verified_group_and_warns_only_above_three(self):
        row = ready_rows(scope="creative")[-1]
        row["metrics"]["duplicate_count"] = 4
        row["context"].update(duplicate_group_verified=True, duplicate_group_id="group1")
        self.assertTrue(actions(analyze([row]), "review_duplicate_creatives")[0]["eligible"])
        row["metrics"]["duplicate_count"] = 3
        self.assertFalse(actions(analyze([row]), "review_duplicate_creatives"))
        row["metrics"]["duplicate_count"] = 4
        row["context"]["duplicate_group_verified"] = None
        self.assertFalse(actions(analyze([row]), "review_duplicate_creatives"))

    def test_heating_cap_is_user_policy_and_does_not_claim_chengfang_official_limit(self):
        row = ready_rows()[-1]
        row["promotion_type"] = "chengfang"
        row["metrics"]["active_heated_creatives"] = 20
        cfg = settings()
        cfg["accounts"][0]["target_roi"] = None
        r = actions(analyze([row], cfg), "hold_new_chase")[0]
        self.assertTrue(r["eligible"])
        self.assertFalse(r["evidence"]["official_product_quota_verified"])
        row["scope"] = "account"
        self.assertFalse(actions(analyze([row], cfg), "hold_new_chase"))

    def test_visible_only_can_warn_but_never_qualifies_adjustment(self):
        rows = smart_rows()
        rows[-1]["source"]["coverage"] = "visible_only"
        r = actions(analyze(rows), "review_disable_smart")[0]
        self.assertFalse(r["eligible"])
        self.assertIn("可见页面", "".join(r["blockers"]))

    def test_midnight_gap_source_change_and_stale_do_not_create_search_window(self):
        for mutation in ("midnight", "gap", "source", "stale"):
            with self.subTest(mutation=mutation):
                now = NOW.replace(hour=1) if mutation == "midnight" else NOW
                rows = search_rows()
                if mutation == "midnight":
                    shift = NOW - now
                    for row in rows:
                        for k in ("captured_at", "data_at"):
                            from datetime import datetime
                            t = datetime.fromisoformat(row["source"][k]) - shift
                            row["source"][k] = t.isoformat()
                        row["source"]["metric_date"] = t.date().isoformat()
                elif mutation == "gap":
                    rows = rows[:10] + rows[13:]
                elif mutation == "source":
                    for i, row in enumerate(rows):
                        row["source"]["series_key"] = "source-A" if i < 10 else "source-B"
                else:
                    now += timedelta(hours=1)
                self.assertFalse(actions(analyze(rows, now=now), "review_keyword_reduce"))

    def test_malformed_input_and_missing_metadata_are_serializable_and_fail_closed(self):
        result = analyze([None, {}, {"account_id": []}])
        self.assertEqual(result["coverage"]["rejected_count"], 3)
        self.assertFalse(result["recommendations"])
        json.dumps(result, allow_nan=False)
        rows = smart_rows()
        rows[-1]["metrics"]["smart_gmv"] = float("nan")
        self.assertFalse(actions(analyze(rows), "review_disable_smart"))
        json.dumps(analyze(rows), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
