"""Additional offline strategies with explicit evidence and missing-data coverage.

This module has no account writer, browser control, or network client. All
numeric thresholds below are the user's strategy choices, not platform rules.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .engine import (CN, _amount_fields, _hour_windows, _number, _record,
                     _settings, _time, _uid, _uses_base)
from .roi_tuning import roi_adjustment
from .live_exclusion import inspect_live_exclusion
from .creative_idle import inspect_creative_idle
from .standard_bid import inspect_standard_bid

REQUIRED_METRICS = (
    "bid", "previous_bid", "bid_before_drop", "account_roi", "account_ctr", "account_cvr",
    "parent_budget", "control_share_pct", "experience_score", "experience_previous_score",
    "experience_threshold", "base_clicks", "base_impressions",
)
CONTEXT_FIELDS = {
    "ids": ["plan_id", "parent_plan_id", "aweme_id", "account_source_key"],
    "times": ["created_at", "last_pause_at", "last_chase_at", "last_bid_change_at", "account_metrics_at", "predicted_overload_at"],
    "enums": {"account_metric_basis": ["overall_payment", "net", "marketing", "control_payment", "settled_7d", "unknown"],
              "creative_type": ["custom", "programmatic", "unknown"]},
    "booleans": ["model_stable", "compliance_ok", "anchor_ready", "promotion_peak", "carrying_ready",
                 "manual_reviewed", "bid_change_history_known", "chase_history_known", "account_metrics_verified",
                 "account_blocked", "permission_restricted", "violation_flag", "differentiated_targeting_verified"],
}
CONTEXT_FIELDS['times'].append('plan_roi_observed_at')
CONTEXT_FIELDS['booleans'].append('plan_roi_config_verified')
CONTEXT_FIELDS['times'].append('plan_bid_observed_at')
CONTEXT_FIELDS['booleans'].append('plan_bid_config_verified')
CONTEXT_FIELDS['enums']['plan_bid_mode']=['conversion_cost','roi','unknown']
CONTEXT_FIELDS['enums'].update(plan_roi_basis=['overall_payment','net','marketing','unknown'],plan_roi_mode=['roi_target','conversion_cost','unknown'])
CONTEXT_FIELDS['numbers']=['plan_roi_step','plan_roi_min','plan_roi_max']
FEATURES = (
    "plan_spend_up_30pct", "plan_cost_up_20pct", "plan_zero_orders_4h", "creative_zero_24h",
    "roi_target_tuning", "standard_bid_tuning", "standard_bid_recovery", "material_chase_admission",
    "one_click_boost", "one_click_control", "aweme_universal_conflict", "compliance_and_experience",
    "live_universal_standard_conflict",
)


def _context(rec):
    value = rec["raw"].get("context")
    return value if isinstance(value, dict) else {}


def _str(value):
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def _fresh(rec, rules, now, *, ignore_roi=False):
    if rec["data"] is None or not -120 <= (now - rec["data"]).total_seconds() <= rules["max_age_seconds"]:
        return False
    if rec["date"] != now.date() or rec["source"].get("metric_period", "day") != "day":
        return False
    if ignore_roi:
        # Explicit governance flags do not require a configured ROI target.
        return not any(x for x in rec["issues"] if x != "ROI 口径未确认")
    return not rec["issues"]


def _hours(records, rules, count=4, *, creative_base=False):
    """Reuse the core window checker at successive hour endpoints."""
    rows = sorted([r for r in records if r["data"]], key=lambda r: (r["data"], r["captured"]))
    if not rows:
        return []
    if creative_base:
        # Only the scope projection changes; all amounts must be explicit base fields.
        rows = [dict(r, raw={**r["raw"], "scope": "plan"}) for r in rows]
    end = rows[-1]["data"]
    result = []
    for offset in reversed(range(count)):
        boundary = end - timedelta(hours=offset)
        candidates = [r for r in rows if r["data"] <= boundary + timedelta(minutes=5)]
        if not candidates:
            result.append({"complete": False, "reason": "缺少小时结束边界"})
            continue
        anchor = min(candidates, key=lambda r: abs(r["data"] - boundary))
        if abs(anchor["data"] - boundary) > timedelta(minutes=5):
            result.append({"complete": False, "reason": "缺少小时结束边界"})
            continue
        selected = [r for r in rows if r["data"] <= anchor["data"]]
        windows, _ = _hour_windows(selected, rules)
        result.append(windows[-1] if windows else {"complete": False, "reason": "无法构造小时窗口"})
    return result


def _sufficient(w, rules, *, zero_orders=False):
    return bool(w and w.get("complete") and w.get("spend") is not None
                and w["spend"] >= rules["min_spend"] and w.get("orders") is not None
                and (w["orders"] == 0 if zero_orders else w["orders"] >= rules["min_orders"]))


def _full_delta(records, hours, fields, rules):
    """A same-day same-series interval, with no counter resets or missing samples."""
    rows = sorted([r for r in records if r["data"]], key=lambda r: (r["data"], r["captured"]))
    if not rows:
        return None
    end = rows[-1]["data"]
    start = end - timedelta(hours=hours)
    left = min(rows, key=lambda r: abs(r["data"] - start))
    if abs(left["data"] - start) > timedelta(minutes=5):
        return None
    selected = [r for r in rows if left["data"] <= r["data"] <= end]
    if any(r["issues"] for r in selected):
        return None
    if len({r["date"] for r in selected}) != 1:
        return None
    if any((b["data"] - a["data"]).total_seconds() > rules["max_gap_minutes"] * 60
           for a, b in zip(selected, selected[1:])):
        return None
    by_time = {}
    for r in selected:
        if r["data"] in by_time and any(r["metrics"].get(k) != by_time[r["data"]]["metrics"].get(k) for k in fields):
            return None
        by_time[r["data"]] = r
    selected = sorted(by_time.values(), key=lambda r: r["data"])
    if any(r["metrics"].get(k) is None for r in selected for k in fields):
        return None
    if any(b["metrics"][k] < a["metrics"][k] for a, b in zip(selected, selected[1:]) for k in fields):
        return None
    return {k: selected[-1]["metrics"][k] - selected[0]["metrics"][k] for k in fields}


def _account_fact(last, groups, rules, now):
    """Only an explicit source link can select an account baseline."""
    ctx = _context(last)
    source_key = _str(ctx.get("account_source_key"))
    if not source_key:
        return None, ["context.account_source_key（账户比较来源）"]
    matches = []
    for key, rows in groups.items():
        if key[0] != last["key"][0] or key[2] != "account" or key[5] != last["basis"]:
            continue
        candidate = max(rows, key=lambda r: (r["data"] or r["captured"], r["captured"]))
        if candidate["source"].get("series_key") != source_key:
            continue
        if not _fresh(candidate, rules, now) or candidate["source"].get("coverage") != "report":
            continue
        if abs((candidate["data"] - last["data"]).total_seconds()) > 300:
            continue
        matches.append(candidate)
    if len(matches) != 1:
        return None, ["唯一、同账户同口径且相差不超过5分钟的账户报告"]
    rec = matches[0]
    m = rec["metrics"]
    cost_key, revenue_key = _amount_fields(rec)
    cost, revenue = m.get(cost_key), m.get(revenue_key)
    roi = revenue / cost if cost is not None and cost > 0 and revenue is not None else None
    ctr, cvr = m.get("ctr"), m.get("cvr")
    if ctr is None and m.get("clicks") is not None and m.get("impressions") is not None and m["impressions"] > 0:
        ctr = m["clicks"] / m["impressions"] * 100
    if cvr is None and m.get("orders") is not None and m.get("clicks") is not None and m["clicks"] > 0:
        cvr = m["orders"] / m["clicks"] * 100
    share = m.get("control_share_pct")
    if share is None and m.get("control_spend") is not None and m.get("spend") is not None and m["spend"] > 0:
        share = m["control_spend"] / m["spend"] * 100
    return {"roi": roi, "ctr": ctr, "cvr": cvr, "control_share_pct": share,
            "balance": m.get("balance"), "source_key": source_key, "data_at": rec["data"].isoformat()}, []


def extend_evaluation(snapshots, settings, evaluation, now=None):
    if not isinstance(now, datetime):
        now = datetime.now(CN)
    else:
        now = now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)
    rules, accounts = _settings(settings)
    evaluation = evaluation if isinstance(evaluation, dict) else {}
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
    bid_reviews = []
    feature_coverage = {name: {"evaluated_entities": 0, "triggered": 0, "missing": []} for name in FEATURES}
    existing = evaluation.get("recommendations", [])
    existing = existing if isinstance(existing, list) else []
    risk_alerts = evaluation.get("alerts", [])
    risk_alerts = risk_alerts if isinstance(risk_alerts, list) else []

    for key, records in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        records = sorted(records, key=lambda r: (r["data"] or r["captured"], r["captured"]))
        last = records[-1]
        raw, m, ctl, ctx = last["raw"], last["metrics"], last["control"], _context(last)
        account_id, entity_id, scope = key[:3]
        source_key = ":".join(map(str, key[3:]))
        identity = (account_id, entity_id, scope, source_key)
        target = _number(accounts[account_id].get("target_roi"))
        target = target if target is not None and target > 0 and accounts[account_id].get("roi_basis") == last["basis"] and last["basis"] not in {"unknown", "settled_7d"} else None
        fresh = _fresh(last, rules, now)
        ws = _hours(records, rules, 4)
        current = ws[-1] if ws else None
        previous = ws[-2] if len(ws) >= 2 else None

        def coverage(feature, missing=(), triggered=False, note=None):
            item = feature_coverage[feature]
            item["evaluated_entities"] += 1
            item["triggered"] += int(bool(triggered))
            if missing and len(item["missing"]) < 100:
                item["missing"].append({"account_id": account_id, "entity_id": entity_id,
                    "source_key": source_key, "fields": list(dict.fromkeys(missing)), "note": note or "证据不完整，未推断未知值"})

        def alert(rule, priority, title, evidence, risk=""):
            alerts.append({"id": _uid(*identity, rule), "priority": priority, "title": title,
                "account_id": account_id, "entity_id": entity_id, "scope": scope, "rule": rule,
                "evidence": evidence, "risk": risk, "source_basis": last["basis"],
                "source_key": source_key, "rule_basis": "user_strategy"})

        def blockers(roi_required=True, cooldown=None):
            out = []
            if not fresh:
                out.append("来源时间、日期、统计周期或数据质量不满足要求")
            if roi_required and target is None:
                out.append("账户策略 ROI 目标缺失或口径不一致")
            if last["source"].get("coverage") != "report":
                out.append("只采到可见页面，覆盖范围未完整核验")
            if raw.get("promotion_type") not in {"universal", "chengfang", "standard", "search"}:
                out.append("推广产品类型未知")
            if ctl.get("history_known") is not True:
                out.append("账户调控历史未知")
            changed = _time(ctl.get("last_change_at"))
            if ctl.get("history_known") is True and changed is None and _number(ctl.get("changes_today")) != 0:
                out.append("缺少最近调控时间")
            if changed and (now - changed).total_seconds() < max(rules["cooldown_minutes"], cooldown or 60) * 60:
                out.append("尚未满足策略冷却时间")
            if ctl.get("guarantee_verified") is not True:
                out.append("当前产品成本保障及调控限制未核验")
            if ctx.get("manual_reviewed") is not True:
                out.append("场景与操作对象尚未人工核验")
            if scope == "plan" and m.get("control_spend") is None and not _uses_base(last):
                out.append("主计划与追加投入核算范围未知")
            if any(isinstance(a, dict) and a.get("priority") == "P0" and a.get("account_id") == account_id for a in risk_alerts):
                out.append("账户已有 P0 风险，先人工处理")
            if any(isinstance(r, dict) and r.get("account_id") == account_id and r.get("entity_id") == entity_id
                   and r.get("eligible") is True and r.get("action") in {"budget_up", "budget_down", "pause_control"} for r in existing):
                out.append("本轮已有同对象调控建议，需人工择一，禁止叠加操作")
            return out

        def propose(rule, priority, action, label, reason, evidence, *, before=None, after=None,
                    change=None, target_type=None, target_id=None, extra=(), parameters=None, roi_required=True, cooldown=None):
            target_type = target_type or scope
            target_id = target_id or _str(ctx.get("plan_id")) or entity_id
            block = list(dict.fromkeys(blockers(roi_required, cooldown) + list(extra)))
            intent = _uid(account_id, target_type, target_id, action, now.date().isoformat())
            recommendations.append({"id": _uid(*identity, rule, action), "intent_id": intent,
                "priority": priority, "account_id": account_id, "entity_id": entity_id, "scope": scope,
                "source_key": source_key, "metric_basis": last["basis"], "target_type": target_type, "target_id": target_id,
                "action": action, "action_label": label, "change_pct": change, "before": before, "after": after,
                "reason": reason, "expected_goal": label + "后验证消耗、转化和模型稳定性",
                "risk": "用户策略阈值，不代表官方成本保障资格；建议会影响模型，仅作离线模拟，不执行到账户。",
                "evidence": evidence, "parameters": parameters or {}, "eligible": not block,
                "blockers": block, "mode": "simulation_only", "rule_basis": "user_strategy", "rule": rule})

        # Governance signals remain independent of ROI availability.
        gov_missing, gov_triggered = [], False
        if _fresh(last, rules, now, ignore_roi=True):
            for flag, title in (("account_blocked", "账户来源显示封禁"), ("permission_restricted", "账户投放权限受限"),
                                ("violation_flag", "账户出现明确违规提醒")):
                if ctx.get(flag) is True:
                    gov_triggered = True
                    alert(flag, "P0", title, {"flag": flag, "observed": True}, "需在官方账户页面人工核验并处理")
                elif type(ctx.get(flag)) is not bool:
                    gov_missing.append("context." + flag)
            if m.get("active_creatives") == 0 and m.get("rejected_creatives") is not None and m["rejected_creatives"] > 0:
                gov_triggered = True
                alert("no_approved_creatives", "P0", "存在审核驳回且没有可用创意", {"active_creatives": 0, "rejected_creatives": m["rejected_creatives"]})
            elif m.get("active_creatives") is None or m.get("rejected_creatives") is None:
                gov_missing += ["metrics.active_creatives", "metrics.rejected_creatives"]
            score, old_score, floor = m.get("experience_score"), m.get("experience_previous_score"), m.get("experience_threshold")
            if score is not None and old_score is not None and floor is not None and score < old_score and score <= floor:
                gov_triggered = True
                alert("experience_score_drop", "P1", "体验分下降至用户设置的预警阈值", {"experience_score": score, "previous_score": old_score, "user_threshold": floor})
            elif any(v is None for v in (score, old_score, floor)):
                gov_missing += ["metrics.experience_score", "metrics.experience_previous_score", "metrics.experience_threshold"]
        else:
            gov_missing.append("可核验的新鲜数据时间与日期")
        coverage("compliance_and_experience", gov_missing, gov_triggered)

        if scope not in {"plan", "creative"}:
            continue
        if not fresh:
            for name in FEATURES:
                if name not in {"compliance_and_experience", "aweme_universal_conflict", "live_universal_standard_conflict", "creative_zero_24h"}:
                    coverage(name, ["新鲜、同日、日累计且质量完整的来源"])
            continue

        if scope == "plan":
            surge = bool(current and previous and current.get("complete") and previous.get("complete")
                         and current.get("spend") is not None and previous.get("spend") is not None and previous["spend"] > 0
                         and current["spend"] > previous["spend"] * 1.3)
            if surge:
                alert("plan_spend_up_30pct", "P1", "计划小时消耗增幅超过 30%", {"previous_hour": previous, "current_hour": current})
            coverage("plan_spend_up_30pct", [] if current and previous and all(w.get("complete") for w in (current, previous)) else ["两个完整小时消耗窗口"], surge)
            target_cost = m.get("target_cost")
            high_cost = bool(current and _sufficient(current, rules) and target_cost is not None and target_cost > 0
                             and current.get("conversion_cost") is not None and current["conversion_cost"] > target_cost * 1.2)
            if high_cost:
                alert("plan_cost_up_20pct", "P1", "计划小时转化成本超过目标 20%", {"hourly": current, "target_cost": target_cost})
            coverage("plan_cost_up_20pct", [] if current and _sufficient(current, rules) and target_cost is not None else ["完整小时订单样本及明确目标成本"], high_cost)

            zero_four = len(ws) == 4 and all(_sufficient(w, rules, zero_orders=True) and w.get("roi") is not None for w in ws)
            severe_zero = bool(zero_four and target is not None and all(w['roi']<target*.8 for w in ws))
            if severe_zero:
                alert("plan_zero_orders_4h", "P1", "计划连续四个完整小时持续消耗且无转化", {"hourly": ws, "target_roi": target})
                propose("plan_zero_orders_4h", "P1", "pause_plan", "评估暂停计划", "基础计划四小时持续消耗、订单差值为零且 ROI 偏离目标",
                        {"hourly": ws, "target_roi": target}, target_type="plan_status", before=ctl.get('running'), after=False,
                        extra=([] if ctx.get('is_primary_plan') is False else ['主力角色未知或为主力计划，先按素材维护与止损复核流程处理，不直接获得暂停资格'])
                              + ([] if ctl.get('running') is True else ['计划当前开启状态未确认或已经关闭，不能重复暂停']))
            coverage("plan_zero_orders_4h", [] if severe_zero else ["四个完整同日小时基础台账、有效目标ROI、持续消耗且各小时ROI低于目标80%"], severe_zero)

            # Assessment threshold and the observed optimization setting have
            # separate meanings. Never substitute one for the other.
            roi_missing = []
            if target is None:
                roi_missing.append("与快照口径一致的 settings.accounts.target_roi")
            if current is None or previous is None or not all(w.get("complete") for w in (current, previous)):
                roi_missing.append("两个完整小时窗口")
            roi_changed = False
            if not roi_missing:
                def propose_roi(direction,rule,priority,reason,evidence):
                    adjustment=roi_adjustment(last,records,direction)
                    roi_missing.extend(adjustment['blockers'])
                    evidence.update(strategy_target_roi=target,plan_roi_configuration=adjustment['evidence'])
                    propose(rule,priority,'roi_target_up' if direction==1 else 'roi_target_down',
                            '小幅上调计划ROI目标' if direction==1 else '小幅下调计划ROI目标',reason,evidence,
                            before=adjustment['before'],after=adjustment['after'],change=adjustment['change_pct'],
                            target_type='plan_roi',extra=adjustment['blockers']+
                            ([] if ctl.get('running') is True else ['计划未明确处于投放中，先核对运行状态']))
                low_two = all(_sufficient(w, rules) and w.get("roi") is not None and w["roi"] < target * .8 for w in (previous, current))
                sustained_cost=target_cost is not None and target_cost>0 and all(
                    _sufficient(w,rules) and w.get('conversion_cost') is not None and w['conversion_cost']>target_cost*1.2
                    for w in (previous,current))
                if low_two and surge and sustained_cost:
                    roi_changed = True
                    propose_roi(1,'roi_target_up_cost','P1','两小时ROI低于考核线80%、小时消耗增长超过30%且两小时成本均超目标20%，模拟小幅收紧计划目标',
                                {'hourly':[previous,current],'target_cost':target_cost})
                stalled = previous.get("spend") == 0 and current.get("spend") == 0
                prior = ws[-3] if len(ws) >= 3 else None
                if stalled and prior and _sufficient(prior, rules) and prior.get("roi") is not None and prior["roi"] >= target:
                    account_fact, missing = _account_fact(last, groups, rules, now)
                    if missing:
                        roi_missing += missing
                    elif account_fact["roi"] is None or account_fact["roi"] < target:
                        roi_missing.append("账户当前同口径 ROI 达标的证据")
                    elif ctx.get("model_stable") is not True:
                        roi_missing.append("context.model_stable=true")
                    else:
                        roi_changed = True
                        propose_roi(-1,'roi_target_down_stall','P2','此前转化达标、当前两小时停滞且账户ROI达标，模拟小幅放量',
                                    {'hourly':ws[-3:],'account':account_fact})
                if target_cost is None or target_cost<=0:roi_missing.append('明确目标成本以核对ROI收紧条件')
            coverage("roi_target_tuning", roi_missing, roi_changed)

            if raw.get("promotion_type") == "standard":
                bid_review = inspect_standard_bid(records, rules, now)
                bid_reviews.append(bid_review)
                bid_missing = []
                bid = m.get("bid")
                if bid is None or bid <= 0:
                    bid_missing.append("metrics.bid")
                if target is None or target_cost is None or target_cost <= 0:
                    bid_missing += ["有效且同口径的账户ROI目标", "metrics.target_cost"]
                if ctx.get("bid_change_history_known") is not True:
                    bid_missing.append("context.bid_change_history_known=true")
                if ctl.get("lifecycle") != "mature" or ctx.get("model_stable") is not True:
                    bid_missing.append("成熟期且模型稳定的明确证据")
                stable = previous and current and all(_sufficient(w, rules) and w.get("roi") is not None and target is not None and w["roi"] >= target
                    and w.get("conversion_cost") is not None and target_cost is not None and w["conversion_cost"] <= target_cost for w in (previous, current))
                bid_at = _time(ctx.get("last_bid_change_at"))
                extra = []
                if bid_at is not None and (now - bid_at).total_seconds() < 7200:
                    extra.append("距离最近一次出价修改不足 2 小时")
                if not stable:
                    bid_missing.append("连续两小时同口径ROI及成本达标样本")
                if not bid_missing:
                    propose("standard_bid_down", "P2", "bid_down", "转化出价下调 5%", "成熟标准计划两小时转化稳定，模拟小幅压价",
                            {"hourly": [previous, current], "target_cost": target_cost, "strategy_target_roi": target, 'bid_review':bid_review},
                            before=bid, after=round(bid * .95, 6), change=-.05, target_type="plan_bid", extra=extra+bid_review['tuning_missing'], cooldown=120)
                coverage("standard_bid_tuning", bid_missing+bid_review['tuning_missing'], not bid_missing and not bid_review['tuning_missing'])

                old_bid = m.get("bid_before_drop") if m.get("bid_before_drop") is not None else m.get("previous_bid")
                recovery_missing = []
                if bid is None or bid <= 0 or old_bid is None or old_bid <= bid or not .05 - 1e-8 <= 1 - bid / old_bid <= .1 + 1e-8:
                    recovery_missing.append("可核验的上次5%-10%降价前后出价")
                if bid_at is None or ctx.get("bid_change_history_known") is not True:
                    recovery_missing.append("明确的降价时间及完整出价历史")
                if not (current and previous and current.get("complete") and previous.get("complete") and current.get("spend") == 0
                        and previous.get("spend") is not None and previous["spend"] >= rules["min_spend"]):
                    recovery_missing.append("降价前完整小时消耗及降价后完整小时零消耗")
                if bid_at and current and current.get("start") and previous and previous.get("end"):
                    if abs((_time(current["start"]) - bid_at).total_seconds()) > 300 or abs((_time(previous["end"]) - bid_at).total_seconds()) > 300:
                        recovery_missing.append("降价时点须与断流前后窗口边界吻合")
                if target is None:
                    recovery_missing.append("有效同口径账户ROI目标")
                if not recovery_missing:
                    propose("standard_bid_recovery", "P1", "bid_restore", "回调至上一出价", "明确降价后出现完整小时断流，模拟回调到上次已核验出价",
                            {"before_hour": previous, "after_hour": current, "bid_changed_at": bid_at.isoformat(), 'bid_review':bid_review},
                            before=bid, after=old_bid, change=round(old_bid / bid - 1, 8), target_type="plan_bid",extra=bid_review['recovery_missing'])
                coverage("standard_bid_recovery", recovery_missing+bid_review['recovery_missing'], not recovery_missing and not bid_review['recovery_missing'])

            if raw.get("scene") == "live":
                account_fact, account_missing = _account_fact(last, groups, rules, now)
                baseline_missing = list(account_missing)
                if target is None:
                    baseline_missing.append("有效同口径账户 ROI 目标")
                if account_fact and (account_fact["roi"] is None or target is None or account_fact["roi"] < target):
                    baseline_missing.append("账户同口径 ROI 达标")
                for flag in ("anchor_ready", "carrying_ready", "model_stable", "compliance_ok"):
                    if ctx.get(flag) is not True:
                        baseline_missing.append("context." + flag + "=true")
                if ctl.get("lifecycle") not in {"growing", "mature"}:
                    baseline_missing.append("明确成长期或成熟期")
                peak = ctx.get("promotion_peak") is True
                stalled_stable = bool(current and previous and current.get("complete") and current.get("spend") == 0
                                      and _sufficient(previous, rules) and previous.get("roi") is not None and target is not None and previous["roi"] >= target)
                if not peak and not stalled_stable:
                    baseline_missing.append("明确大促高峰，或停滞前完整小时转化稳定证据")
                conversion_ready = stalled_stable or bool(current and _sufficient(current, rules) and current.get("roi") is not None
                    and target is not None and current["roi"] >= target)
                if not conversion_ready:
                    baseline_missing.append("最近完整小时达标，或停滞前有完整小时转化稳定证据")
                if not baseline_missing:
                    propose("one_click_boost", "P2", "one_click_boost", "一键起量测试 100 元 1 小时", "主播与承接能力已核验，账户 ROI 达标且符合起量场景",
                            {"account": account_fact, "previous_hour": previous, "promotion_peak": peak}, target_type="boost_task",
                            parameters={"budget": 100, "duration_hours": 1, "optimization_goal": "live_purchase", "cost_guarantee": "not_supported_user_rule"})
                coverage("one_click_boost", baseline_missing, not baseline_missing)
                forecast = _time(ctx.get("predicted_overload_at"))
                forecast_ok = forecast is not None and 180 <= (forecast - now).total_seconds() <= 300
                emergency = bool(surge and current and current.get("roi") is not None and target is not None and current["roi"] < target)
                control_missing = []
                if target is None:
                    control_missing.append("有效同口径账户 ROI 目标")
                if ctx.get("carrying_ready") is not False:
                    control_missing.append("明确承接不足 carrying_ready=false")
                if not forecast_ok and not emergency:
                    control_missing.append("3到5分钟后的明确承接预警，或已发生消耗突增且ROI偏低")
                if not control_missing:
                    propose("one_click_control", "P1", "one_click_control", "一键控量测试 0.5 小时", "已明确承接不足，并有预警时点或完整小时流量异常证据",
                            {"trigger":"forecast" if forecast_ok else "observed_surge",
                             "predicted_overload_at": forecast.isoformat() if forecast else None, "current_hour": current,"previous_hour":previous,
                             "strategy_target_roi": target}, target_type="flow_control_task", parameters={"duration_hours": .5})
                coverage("one_click_control", control_missing, not control_missing)

        if scope == "creative":
            missing = []
            for field in ("parent_plan_id", "account_source_key"):
                if not _str(ctx.get(field)):
                    missing.append("context." + field)
            for flag in ("model_stable", "compliance_ok", "chase_history_known", "differentiated_targeting_verified"):
                if ctx.get(flag) is not True:
                    missing.append("context." + flag + "=true")
            if ctl.get("lifecycle") not in {"growing", "mature"}:
                missing.append("成长期或成熟期的明确分层")
            if target is None:
                missing.append("有效同口径账户 ROI 目标")
            base_ws = _hours(records, rules, 4, creative_base=True)
            if not (len(base_ws) == 4 and all(w.get("ledger") == "base" and _sufficient(w, rules)
                    and w.get("roi") is not None and target is not None and w["roi"] >= target for w in base_ws)):
                missing.append("4个完整小时的对应口径基础金额、基础成本及base_orders达标台账")
            elif max(w["roi"] for w in base_ws) > min(w["roi"] for w in base_ws) * 1.2:
                missing.append("4小时基础ROI波动超过20%，未满足稳定条件")
            traffic = _full_delta(records, 4, ("base_clicks", "base_impressions", "base_orders"), rules)
            material_ctr = traffic["base_clicks"] / traffic["base_impressions"] * 100 if traffic and traffic["base_impressions"] > 0 else None
            material_cvr = traffic["base_orders"] / traffic["base_clicks"] * 100 if traffic and traffic["base_clicks"] > 0 else None
            account_fact, account_missing = _account_fact(last, groups, rules, now)
            missing += account_missing
            if material_ctr is None or material_cvr is None:
                missing.append("4小时基础点击、展示及订单差值")
            if account_fact:
                if account_fact["roi"] is None or target is None or account_fact["roi"] < target:
                    missing.append("账户同口径ROI达标")
                if account_fact["ctr"] is None or account_fact["cvr"] is None or material_ctr is None or material_cvr is None or material_ctr <= account_fact["ctr"] or material_cvr <= account_fact["cvr"]:
                    missing.append("素材基础CTR及CVR均高于已关联账户平均值")
                if account_fact["control_share_pct"] is None or account_fact["control_share_pct"] > rules["control_share_cap"] * 100:
                    missing.append("账户追投消耗占比已确认且不超过20%策略上限")
            parent_budget = m.get("parent_budget")
            if parent_budget is None or parent_budget * .3 < 100:
                missing.append("母计划预算及至少100元且不超过母预算30%的空间")
            chased = _time(ctx.get("last_chase_at"))
            paused = _time(ctx.get("last_pause_at"))
            if ctx.get("last_chase_at") is not None and chased is None or ctx.get("last_pause_at") is not None and paused is None:
                missing.append("追投与关停历史时间格式无效")
            if chased and (now - chased).total_seconds() < 86400 or paused and (now - paused).total_seconds() < 86400:
                missing.append("同素材距最近追投或关停须满24小时")
            if not missing:
                propose("material_chase_admission", "P2", "create_chase", "素材追投测试 100 元 2 小时", "基础素材稳定4小时、效果高于账户平均且预算占比符合策略",
                        {"hourly_base": base_ws, "base_ctr": material_ctr, "base_cvr": material_cvr, "account": account_fact,
                         "parent_budget": parent_budget, "strategy_target_roi": target}, target_type="material_chase", target_id=entity_id,
                        parameters={"budget": 100, "duration_hours": 2, "mode": "cost_control", "target_roi": target,
                                    "parent_plan_id": ctx["parent_plan_id"], "differentiated_targeting_verified": True})
            coverage("material_chase_admission", missing, not missing)

    exclusion=inspect_live_exclusion(groups,rules,now)
    alerts.extend(exclusion['alerts']);recommendations.extend(exclusion['recommendations'])
    for name in ('aweme_universal_conflict','live_universal_standard_conflict'):
        feature_coverage[name]['evaluated_entities']=len(exclusion['observations'])
        feature_coverage[name]['triggered']=sum(a['rule']==name for a in exclusion['alerts'])
        feature_coverage[name]['missing']=[{'account_id':o['account_id'],'entity_id':o['entity_id'],'fields':o['missing']}
            for o in exclusion['observations'] if o['missing']][:100]

    # Independent daily segments require an explicitly declared close/open pair.
    # Never subtract counters across midnight or choose an older favorable clock.
    creative_history = defaultdict(list)
    for key, rows in groups.items():
        if key[2] == "creative":
            creative_history[key[:-1]].extend(rows)
    for identity, rows in creative_history.items():
        idle=inspect_creative_idle(rows,rules,now)
        last=idle['latest']
        if last is None:continue
        ctx = _context(last)
        missing=idle['missing']
        zero=idle['zero_activity']
        feature_coverage["creative_zero_24h"]["evaluated_entities"] += 1
        if missing:
            feature_coverage["creative_zero_24h"]["missing"].append({"account_id": identity[0], "entity_id": identity[1], "fields": missing})
        if zero:
            feature_coverage["creative_zero_24h"]["triggered"] += 1
            aid = _uid(*identity, "creative_zero_24h")
            alerts.append({"id": aid, "priority": "P2", "title": "自定义创意完整24小时新增消耗与点击均为零",
                "account_id": identity[0], "entity_id": identity[1], "scope": "creative", "rule": "creative_zero_24h",
                "rule_basis": "user_strategy", "source_basis": last["basis"], "evidence": idle['evidence'],
                "risk": "暂停前需确认其他投放用途；删除素材不等同暂停，不能调用删除接口代替"})
            block = []
            if last["source"].get("coverage") != "report":
                block.append("覆盖范围不完整")
            if last["control"].get("history_known") is not True or last["control"].get("guarantee_verified") is not True or ctx.get("manual_reviewed") is not True:
                block.append("变更历史、成本保障或对象人工核验不完整")
            changed = _time(last["control"].get("last_change_at"))
            if changed is None and _number(last["control"].get("changes_today")) != 0:
                block.append("缺少最近调控时间及当日操作计数")
            if changed and (now - changed).total_seconds() < rules["cooldown_minutes"] * 60:
                block.append("冷却时间不足")
            if last["raw"].get("promotion_type") not in {"universal", "standard", "search"}:
                block.append("推广产品类型未知或保障适用范围未在此策略中实现")
            recommendations.append({"id": aid + "_advice", "intent_id": _uid(identity[0], identity[1], "pause_creative", now.date()),
                "priority": "P2", "account_id": identity[0], "entity_id": identity[1], "scope": "creative", "source_key": ":".join(map(str, last['key'][3:])),
                "metric_basis": last["basis"], "target_type": "creative_status", "target_id": identity[1], "action": "pause_creative", "action_label": "评估暂停无效自定义创意",
                "change_pct": None, "before": True, "after": False,
                "parameters": {"parent_plan_id": idle['evidence']['parent_plan_id']},
                "reason": "实际时长不少于24小时，逐日台账增量为零且午夜封账/起点明确，不跨日直接相减",
                "expected_goal": "减少无效创意占用并人工确认替换素材", "risk": "用户策略，仅作离线建议，不执行到账户", "evidence": alerts[-1]["evidence"],
                "eligible": not block, "blockers": block, "mode": "simulation_only", "rule_basis": "user_strategy", "rule": "creative_zero_24h"})

    recommendations.sort(key=lambda r: (r["priority"], r["account_id"], r["entity_id"], r["rule"]))
    # An AI consumer must not turn independent options into several changes to
    # the same plan in one evaluation. Keep one eligible option per operation
    # object; all other proposals stay visible as blocked alternatives.
    selected_objects = set()
    for prior in existing:
        if isinstance(prior, dict) and prior.get("eligible") is True and prior.get("action") in {"budget_up", "budget_down", "pause_control"}:
            selected_objects.add((prior.get("account_id"), prior.get("target_id") or prior.get("entity_id")))
    for rec in recommendations:
        if not rec["eligible"]:
            continue
        operation_id = rec.get("parameters", {}).get("parent_plan_id") or rec["target_id"]
        operation_key = (rec["account_id"], operation_id)
        if operation_key in selected_objects:
            rec["eligible"] = False
            rec["blockers"].append("同一计划本轮已有优先建议，禁止叠加调控")
        else:
            selected_objects.add(operation_key)
    alerts.sort(key=lambda r: (r["priority"], r["account_id"], r["entity_id"], r["rule"]))
    for value in feature_coverage.values():
        value["state"] = "triggered" if value["triggered"] else "insufficient" if value["missing"] else "observed" if value["evaluated_entities"] else "not_applicable"
    return {"alerts": alerts, "recommendations": recommendations,'live_exclusion_reviews':exclusion['observations'], 'standard_bid_reviews':bid_reviews,
            "coverage": {"execution_enabled": False, "rule_basis": "user_strategy", "strategies": feature_coverage,
                "rejected_count": rejected, "required_metrics": list(REQUIRED_METRICS), "required_context": CONTEXT_FIELDS,
                "limits": ["所有场景标签与调控历史需明确提供，未从页面文字猜测", "仅明确关联的账户报告可作账户均值比较", "24小时零活动按独立日台账增量核对，跨日必须有明确封账和零累计起点，不把23小时55分视为24小时"]}}
