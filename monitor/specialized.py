"""Search and creative advice from explicitly scoped, offline evidence only."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .engine import CN, _hour_windows, _number, _record, _settings, _time, _uid

FEATURES = ("search_keywords", "smart_traffic", "cold_start_ctr", "creative_composite_decline",
            "duplicate_creatives", "heating_quota", "primary_plan_maintenance")
REQUIRED_METRICS = [prefix + field for prefix in ("search_", "smart_") for field in
                    ("spend", "gmv", "net_gmv", "comprehensive_cost", "orders", "clicks", "impressions")] + ["industry_ctr"]
REQUIRED_CONTEXT = {
    "flags": ["search_ledger_verified", "smart_ledger_verified", "smart_enabled", "industry_baseline_verified",
              "duplicate_group_verified", "is_primary_plan", "creative_replacement_failed"],
    "ids": ["industry_id", "baseline_industry_id", "duplicate_group_id"],
    "times": ["industry_baseline_at"],
    "enums": {"content_format": ["image_text", "short_video", "unknown"]},
    "local_only_text": ["industry_baseline_source"],
}


def _ctx(record):
    value = record["raw"].get("context")
    return value if isinstance(value, dict) else {}


def _id(value):
    return value if isinstance(value, str) and 0 < len(value) <= 150 else None


def _fresh(record, rules, now, *, roi_independent=False):
    if record["data"] is None or not -120 <= (now - record["data"]).total_seconds() <= rules["max_age_seconds"]:
        return False
    if record["date"] != now.date() or record["source"].get("metric_period", "day") != "day":
        return False
    ignored = {"ROI 口径未确认"} if roi_independent else set()
    return not [issue for issue in record["issues"] if issue not in ignored]


def _project_subledger(records, prefix):
    """No fallback from a missing subledger amount to the overall amount."""
    fields = ("spend", "gmv", "net_gmv", "comprehensive_cost", "orders", "clicks", "impressions")
    projected = []
    for record in records:
        m = {field: record["metrics"].get(prefix + field) for field in fields}
        m["control_spend"] = 0.0
        projected.append(dict(record, raw={**record["raw"], "scope": "keyword"}, metrics=m))
    return projected


def _base_mode(record):
    m = record["metrics"]
    return any(m.get(key) is not None for key in ("base_spend", "base_gmv", "base_net_gmv",
        "base_comprehensive_cost", "base_orders", "base_clicks", "base_impressions", "base_roi", "base_net_roi", "base_marketing_roi")) or bool(m.get("control_spend", 0))


def _window_delta(records, window, fields):
    """The core engine validates gaps/resets for its own amounts; validate any
    additional traffic counters over exactly the same checked hour here."""
    if not window.get("complete"):
        return None
    start, end = _time(window.get("start")), _time(window.get("end"))
    if start is None or end is None:
        return None
    dated = sorted([r for r in records if r["data"]], key=lambda r: (r["data"], r["captured"]))
    if not dated:
        return None
    left = min(dated, key=lambda r: abs(r["data"] - start))
    right = min(dated, key=lambda r: abs(r["data"] - end))
    if abs(left["data"] - start) > timedelta(minutes=5) or abs(right["data"] - end) > timedelta(minutes=5):
        return None
    selected = [r for r in dated if left["data"] <= r["data"] <= right["data"]]
    by_time = {}
    for row in selected:
        previous = by_time.get(row["data"])
        if previous and any(previous["metrics"].get(field) != row["metrics"].get(field) for field in fields):
            return None
        by_time[row["data"]] = row
    selected = sorted(by_time.values(), key=lambda r: r["data"])
    if any(r["metrics"].get(field) is None for r in selected for field in fields):
        return None
    if any(b["metrics"][field] < a["metrics"][field] for a, b in zip(selected, selected[1:]) for field in fields):
        return None
    return {field: right["metrics"][field] - left["metrics"][field] for field in fields}


def _creative_windows(records, rules):
    base = _base_mode(records[-1])
    projected = records
    if base:
        projected = []
        for r in records:
            # Map explicit base fields to a dedicated virtual creative ledger;
            # even an isolated base ROI label cannot trigger a total-amount fallback.
            m = {field: r["metrics"].get("base_" + field) for field in
                 ("spend", "gmv", "net_gmv", "comprehensive_cost", "orders", "clicks", "impressions")}
            m["control_spend"] = 0.0
            projected.append(dict(r, raw={**r["raw"], "scope": "creative"}, metrics=m))
    windows, _ = _hour_windows(projected, rules)
    click_key, impression_key = ("base_clicks", "base_impressions") if base else ("clicks", "impressions")
    for window in windows:
        traffic = _window_delta(records, window, (click_key, impression_key))
        impressions = traffic[impression_key] if traffic else None
        clicks = traffic[click_key] if traffic else None
        window["impressions"] = impressions
        window["clicks"] = clicks
        window["ctr"] = clicks / impressions * 100 if clicks is not None and impressions is not None and impressions > 0 else None
        # CPM uses advertising spend, not marketing comprehensive costs or a day CPM label.
        window["cpm"] = window["spend"] / impressions * 1000 if window.get("spend") is not None and impressions is not None and impressions > 0 else None
        window["cpm_period"] = "hour"
        window["ledger"] = "base" if base else "overall"
    return windows


def analyze_specialized(snapshots, settings, now=None):
    if not isinstance(now, datetime):
        now = datetime.now(CN)
    else:
        now = now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)
    rules, accounts = _settings(settings)
    raw_rules = settings.get("rules", {}) if isinstance(settings, dict) and isinstance(settings.get("rules"), dict) else {}
    # This is an explicit local sample guard, never an invented industry mean.
    min_impressions = _number(raw_rules.get("specialized_min_impressions"))
    min_impressions = max(100, min(1_000_000, min_impressions)) if min_impressions is not None else 1000
    groups, rejected = defaultdict(list), 0
    for raw in snapshots if isinstance(snapshots, list) else []:
        try:
            rec = _record(raw, accounts, now)
        except (TypeError, ValueError, OverflowError, AttributeError):
            rec = None
        if rec is None:
            rejected += 1
        else:
            groups[rec["key"]].append(rec)
    alerts, recommendations = [], []
    features = {name: {"evaluated_entities": 0, "triggered": 0, "missing": []} for name in FEATURES}

    for key, records in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        records = sorted(records, key=lambda r: (r["data"] or r["captured"], r["captured"]))
        last = records[-1]
        raw, m, ctl, ctx = last["raw"], last["metrics"], last["control"], _ctx(last)
        account_id, entity_id, scope = key[:3]
        source_key = ":".join(map(str, key[3:]))
        identity = (account_id, entity_id, scope, source_key)
        fresh = _fresh(last, rules, now)
        independent_fresh = _fresh(last, rules, now, roi_independent=True)
        target = _number(accounts[account_id].get("target_roi"))
        if target is None or target <= 0 or accounts[account_id].get("roi_basis") != last["basis"] or last["basis"] in {"unknown", "settled_7d", "control_payment"}:
            target = None

        def cover(feature, missing=(), triggered=False):
            item = features[feature]
            item["evaluated_entities"] += 1
            item["triggered"] += int(bool(triggered))
            if missing and len(item["missing"]) < 100:
                item["missing"].append({"account_id": account_id, "entity_id": entity_id,
                    "source_key": source_key, "fields": list(dict.fromkeys(missing))})

        def alert(rule, priority, title, evidence, risk=""):
            alerts.append({"id": _uid(*identity, rule), "priority": priority, "title": title,
                "account_id": account_id, "entity_id": entity_id, "scope": scope, "source_key": source_key,
                "rule": rule, "source_basis": last["basis"], "evidence": evidence, "risk": risk,
                "rule_basis": "user_strategy"})

        def advice(rule, priority, action, label, evidence, *, target_type=None, target_id=None,
                   roi_required=True, extra=()):
            block = list(extra)
            if not (fresh if roi_required else independent_fresh):
                block.append("数据时间、日期、统计周期或质量不满足要求")
            if roi_required and target is None:
                block.append("没有匹配口径的用户ROI目标")
            if last["source"].get("coverage") != "report":
                block.append("只采到可见页面，未核验完整覆盖范围")
            if ctl.get("history_known") is not True:
                block.append("调控历史未知")
            if ctl.get("guarantee_verified") is not True:
                block.append("当前产品保障及操作限制未核验")
            if ctx.get("manual_reviewed") is not True:
                block.append("对象与场景尚未人工核验")
            if raw.get("promotion_type") not in {"universal", "standard", "search", "chengfang"}:
                block.append("推广产品类型未知")
            changed = _time(ctl.get("last_change_at"))
            if changed is None and _number(ctl.get("changes_today")) != 0:
                block.append("缺少最近操作时间或明确的零操作计数")
            if changed is not None and (now - changed).total_seconds() < rules["cooldown_minutes"] * 60:
                block.append("尚未满足至少一小时的操作间隔")
            target_type = target_type or scope
            target_id = target_id or entity_id
            recommendations.append({"id": _uid(*identity, rule, action),
                "intent_id": _uid(account_id, target_type, target_id, action, now.date()), "priority": priority,
                "account_id": account_id, "entity_id": entity_id, "scope": scope, "source_key": source_key,
                "metric_basis": last["basis"], "target_type": target_type, "target_id": target_id,
                "action": action, "action_label": label, "change_pct": None, "before": None, "after": None,
                "reason": label + "，依据明确分账与样本进行人工复核", "expected_goal": "验证专项流量或创意效果并保留审计记录",
                "parameters": {"parent_plan_id":ctx.get("parent_plan_id") or ctx.get("plan_id") or (entity_id if scope=="plan" else None)},
                "risk": "本地用户策略，不代表官方政策；仅作离线建议，不执行任何账户操作。",
                "evidence": evidence, "eligible": not block, "blockers": list(dict.fromkeys(block)),
                "mode": "simulation_only", "rule_basis": "user_strategy", "rule": rule})

        def sample(window):
            if not window or not window.get("complete") or window.get("spend") is None or window["spend"] < rules["min_spend"]:
                return False
            if window.get("orders") is not None and window["orders"] >= rules["min_orders"]:
                return True
            cost = m.get("target_cost")
            return window.get("orders") == 0 and cost is not None and cost > 0 and window["spend"] >= cost * rules["min_orders"]

        if scope == "keyword":
            missing = []
            if not fresh:
                missing.append("新鲜、同日、日累计且质量完整的关键词快照")
            if ctx.get("search_ledger_verified") is not True:
                missing.append("context.search_ledger_verified=true")
            if target is None:
                missing.append("匹配口径的 settings.accounts.target_roi")
            projected = _project_subledger(records, "search_")
            windows, _ = _hour_windows(projected, rules)
            recent = windows[-2:]
            if len(recent) != 2 or not all(sample(w) and w.get("roi") is not None for w in recent):
                missing.append("两个完整小时的search_*金额、成本及匹配口径订单子台账")
            triggered = False
            if not missing:
                evidence = {"ledger": "search_only", "hourly": recent, "strategy_target_roi": target,
                            "keyword_id": _id(ctx.get("keyword_id")) or entity_id}
                if all(w["roi"] < target * .8 for w in recent):
                    triggered = True
                    alert("keyword_high_cost_low_return", "P1", "搜索关键词连续两小时高消耗低回报", evidence)
                    advice("keyword_high_cost_low_return", "P1", "review_keyword_reduce", "复核并压缩低效搜索关键词", evidence,
                           target_type="keyword", target_id=evidence["keyword_id"])
                elif all(w["roi"] >= target and w.get("orders") is not None and w["orders"] >= rules["min_orders"] for w in recent):
                    triggered = True
                    advice("keyword_high_conversion_candidate", "P2", "review_keyword_expand", "将该关键词列为高转化拓展候选", evidence,
                           target_type="keyword", target_id=evidence["keyword_id"])
            cover("search_keywords", missing, triggered)

        if scope in {"plan", "account", "product"}:
            missing = []
            if not fresh:
                missing.append("新鲜、同日、日累计且质量完整的智选子台账")
            if ctx.get("smart_ledger_verified") is not True:
                missing.append("context.smart_ledger_verified=true")
            if type(ctx.get("smart_enabled")) is not bool:
                missing.append("context.smart_enabled（明确当前开关状态）")
            if target is None:
                missing.append("匹配口径的用户ROI目标")
            projected = _project_subledger(records, "smart_")
            windows, _ = _hour_windows(projected, rules)
            recent = windows[-2:]
            if len(recent) != 2 or not all(sample(w) and w.get("roi") is not None for w in recent):
                missing.append("两个完整小时smart_*独立金额、成本及订单子台账")
            plan_id = _id(ctx.get("plan_id")) or (entity_id if scope == "plan" else None)
            if not plan_id:
                missing.append("对应智选开关的明确plan_id")
            triggered = False
            if not missing and ctx["smart_enabled"] is True:
                evidence = {"ledger": "smart_only", "hourly": recent, "strategy_target_roi": target, "smart_enabled": True}
                if all(w["roi"] >= target for w in recent):
                    triggered = True
                    advice("smart_roi_pass", "P2", "retain_smart", "智选流量单独核算达标，建议保留", evidence,
                           target_type="smart_traffic_setting", target_id=plan_id)
                elif all(w["roi"] < target for w in recent):
                    triggered = True
                    alert("smart_roi_fail", "P1", "智选流量独立台账连续两小时未达目标", evidence)
                    advice("smart_roi_fail", "P1", "review_disable_smart", "建议人工复核关闭智选流量", evidence,
                           target_type="smart_traffic_setting", target_id=plan_id)
            cover("smart_traffic", missing, triggered)

        if scope in {"creative", "plan"} and raw.get("scene") == "product":
            missing = []
            created = _time(ctx.get("created_at"))
            age_hours = (now - created).total_seconds() / 3600 if created else None
            cold = age_hours is not None and 0 <= age_hours <= 72
            if created is None:
                missing.append("context.created_at（确认0-3天冷启动）")
            if ctx.get("content_format") not in {"image_text", "short_video"}:
                missing.append("明确图文或短视频体裁")
            if not independent_fresh:
                missing.append("新鲜且明确统计日期的CTR快照")
            benchmark = m.get("industry_ctr")
            benchmark_at = _time(ctx.get("industry_baseline_at"))
            baseline_source = ctx.get("industry_baseline_source")
            source_present = isinstance(baseline_source, str) and 0 < len(baseline_source.strip()) <= 500
            if benchmark is None or not 0 <= benchmark <= 100 or ctx.get("industry_baseline_verified") is not True or not source_present:
                missing.append("明确行业CTR均值、来源存在及人工核验标记")
            if benchmark_at is None or not 0 <= (now - benchmark_at).total_seconds() <= 30 * 86400:
                missing.append("30天内且时间明确的行业CTR基线")
            industry, baseline_industry = _id(ctx.get("industry_id")), _id(ctx.get("baseline_industry_id"))
            if not industry or industry != baseline_industry:
                missing.append("与本素材一致的行业基线分类ID")
            base = _base_mode(last)
            clicks, impressions = m.get("base_clicks" if base else "clicks"), m.get("base_impressions" if base else "impressions")
            if impressions is None or clicks is None or impressions < min_impressions or clicks > impressions:
                missing.append("达到本地样本门槛的同口径点击与展示数")
            triggered = False
            if cold and not missing:
                ctr = clicks / impressions * 100
                if ctr < benchmark:
                    triggered = True
                    evidence = {"period": "day", "ledger": "base" if base else "overall", "age_hours": round(age_hours, 3),
                        "clicks": clicks, "impressions": impressions, "ctr": ctr, "industry_ctr": benchmark,
                        "minimum_impressions_local_guard": min_impressions, "baseline_at": benchmark_at.isoformat(),
                        "baseline_source_present": True, "industry_id": industry, "baseline_industry_id": baseline_industry}
                    alert("cold_ctr_below_industry", "P2", "冷启动素材CTR低于已核验的行业基线", evidence,
                          "行业基线由用户报告提供；样本门槛为本地保护参数，不是官方淘汰标准")
                    primary = ctx.get("is_primary_plan") is True
                    label = "主力计划优先补充新素材" if primary else "复核替换低CTR冷启动素材"
                    primary_id = (_id(ctx.get("plan_id")) or entity_id) if scope == "plan" else (_id(ctx.get("parent_plan_id")) or _id(ctx.get("plan_id")))
                    advice("cold_ctr_below_industry", "P2", "refresh_creatives" if primary else "review_cold_creative", label,
                           evidence, roi_required=False, target_type="plan_creatives" if primary else "creative",
                           target_id=primary_id if primary else entity_id,
                           extra=["缺少明确的主力计划ID"] if primary and primary_id is None else [])
            # Mature assets are outside this feature rather than falsely missing age.
            if cold or created is None:
                cover("cold_start_ctr", missing, triggered)

        if scope == "creative" or scope == "plan" and ctx.get("is_primary_plan") is True:
            missing = []
            if not fresh:
                missing.append("新鲜、同日、日累计且质量完整的创意来源")
            windows = _creative_windows(records, rules)
            complete = len(windows) == 3 and all(w.get("complete") and w.get("impressions") is not None and w["impressions"] >= min_impressions
                and w.get("ctr") is not None and w.get("roi") is not None and w.get("cpm") is not None
                and w.get("spend") is not None and w["spend"] >= rules["min_spend"]
                and w.get("orders") is not None and w["orders"] >= rules["min_orders"] for w in windows)
            if not complete:
                missing.append("三个完整小时的同口径CTR、ROI、消耗、展示与订单样本")
            triggered = False
            if not missing:
                ctrs, rois, cpms, spends = [[w[field] for w in windows] for field in ("ctr", "roi", "cpm", "spend")]
                decline = (ctrs[0] > ctrs[1] > ctrs[2] and ctrs[2] <= ctrs[0] * (1 - rules["creative_ctr_decay"])
                    and rois[0] > rois[1] > rois[2] and cpms[0] < cpms[1] < cpms[2]
                    and spends[0] > spends[1] > spends[2] and spends[2] <= spends[0] * .85)
                if decline:
                    triggered = True
                    evidence = {"hourly": windows, "ctr_relative_decline": round(1 - ctrs[2] / ctrs[0], 6),
                        "spend_relative_slowdown": round(1 - spends[2] / spends[0], 6), "cpm_source": "hourly_ad_spend_divided_by_hourly_impressions"}
                    primary = ctx.get("is_primary_plan") is True
                    alert("creative_composite_decline", "P1", "创意综合衰退：CTR及ROI下降、CPM上涨且消耗放缓", evidence)
                    extra = []
                    if not _base_mode(last) and m.get("control_spend") is None:
                        extra.append("基础台账与追加投入的范围未确认")
                    plan_id = (_id(ctx.get("plan_id")) or entity_id) if scope == "plan" else (_id(ctx.get("parent_plan_id")) or _id(ctx.get("plan_id")))
                    if primary and not plan_id:
                        extra.append("缺少主力计划ID，不能把素材ID当成计划ID")
                    advice("creative_composite_decline", "P1", "refresh_creatives" if primary else "replace_creative",
                        "主力计划优先补充新素材" if primary else "复核替换衰退素材", evidence, roi_required=False,
                        target_type="plan_creatives" if primary else "creative", target_id=plan_id if primary else entity_id, extra=extra)
                    if primary:
                        cover("primary_plan_maintenance", triggered=True)
            cover("creative_composite_decline", missing, triggered)

        if scope == "creative":
            missing = []
            duplicate_count, group_id = m.get("duplicate_count"), _id(ctx.get("duplicate_group_id"))
            if not independent_fresh:
                missing.append("新鲜且日期明确的复制分组观察")
            if duplicate_count is None or not float(duplicate_count).is_integer() or not group_id or ctx.get("duplicate_group_verified") is not True:
                missing.append("已核验复制分组ID及明确整数复制数")
            triggered = not missing and duplicate_count > 3
            if triggered:
                evidence = {"duplicate_count": duplicate_count, "user_strategy_max": 3, "duplicate_group_id": group_id}
                alert("duplicate_creatives_over_3", "P2", "同组复制素材数量超过用户策略的3条", evidence,
                      "复制分组来自已核验报告；未进行图像相似度猜测")
                advice("duplicate_creatives_over_3", "P2", "review_duplicate_creatives", "复核精简同组复制素材", evidence,
                       target_type="creative_group", target_id=group_id, roi_required=False)
            cover("duplicate_creatives", missing, triggered)

        if scope == "plan":
            count = m.get("active_heated_creatives")
            missing = []
            if not independent_fresh:
                missing.append("新鲜且日期明确的计划加热配额观察")
            if count is None or not float(count).is_integer():
                missing.append("该计划当前唯一加热素材整数数量")
            triggered = not missing and count >= 20
            if triggered:
                evidence = {"active_heated_creatives": count, "user_strategy_limit": 20,
                            "official_product_quota_verified": False, "promotion_type": raw.get("promotion_type")}
                alert("heating_user_quota_20", "P1", "计划加热素材数量已达到用户策略配额20", evidence,
                      "20是用户策略阈值；不推定全域、乘方等不同产品的官方上限一致")
                advice("heating_user_quota_20", "P1", "hold_new_chase", "暂停新增素材加热并核验当前产品配额", evidence,
                       target_type="plan_control_tasks", target_id=_id(ctx.get("plan_id")) or entity_id, roi_required=False)
            cover("heating_quota", missing, triggered)

    alerts.sort(key=lambda r: (r["priority"], r["account_id"], r["entity_id"], r["rule"]))
    recommendations.sort(key=lambda r: (r["priority"], r["account_id"], r["entity_id"], r["rule"]))
    for item in features.values():
        item["state"] = "triggered" if item["triggered"] else "insufficient" if item["missing"] else "observed" if item["evaluated_entities"] else "not_applicable"
    return {"alerts": alerts, "recommendations": recommendations,
        "coverage": {"execution_enabled": False, "rule_basis": "user_strategy", "specialized": features,
            "rejected_count": rejected, "required_metrics": REQUIRED_METRICS, "required_context": REQUIRED_CONTEXT,
            "sample_guard_min_impressions": min_impressions,
            "limits": ["搜索与智选分别使用显式子台账，不回退总体ROI", "行业均值由带来源的人工核验报告提供，不自动生成", "CPM只用同来源小时分段计算", "主力计划衰退只建议补素材，不自动暂停主计划"]}}
