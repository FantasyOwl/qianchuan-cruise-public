"""Cross-check reported or simulated changes against local observation windows.

An operator report is not platform certification. This module has no network,
account writer, or authorization path. Its candidates are review material only.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta

from .engine import CN, _amount_fields, _record, _settings, _time, _uid, _uses_base
from .schema import now_cn

SOURCES = {"manual_verified_report", "simulation"}
STATUSES = {"reported_applied", "simulated", "rejected", "failed", "unknown"}
SCOPES = {"plan", "control", "creative"}
BASES = {"overall_payment", "net", "marketing", "control_payment"}
EXPIRING_ACTIONS = {"create_chase", "one_click_boost", "one_click_control"}
ACTION_FIELDS = {
    "budget_up": "budget", "budget_down": "budget", "control_budget_up": "budget",
    "roi_target_up": "target_roi", "roi_target_down": "target_roi",
    "bid_down": "bid", "bid_restore": "bid",
    "pause_plan": "running", "resume_plan": "running", "pause_control": "running",
    "pause_creative": "running",
}
ACTIONS = set(ACTION_FIELDS) | EXPIRING_ACTIONS | {'refresh_creatives'}
DECREASING = {"budget_down", "roi_target_down", "bid_down"}
INCREASING = {"budget_up", "control_budget_up", "roi_target_up", "bid_restore"}
# These exact messages limit coverage or daily actions, rather than invalidate
# the observed counters of this one object. They remain visible as limitations.
OBSERVATION_NOTES = {
    "统计日期与采集日期不一致",
    "非日累计统计周期，仅作独立观察，不参与日级小时调控",
    "仅采集可见汇总，未覆盖分页计划、素材、退款及库存全量数据",
    "仅当前渲染计划行，未翻页且未核验全量及操作历史",
    "基础与总体台账分别保存，未核验范围前不通过相减推算调控台账",
    "直播大屏为本场累计，不能当作日累计或直接拼接账户小时序列",
}
DERIVED_OBSERVATION_NOTES = {
    "统计日期不是今日，仅供历史观察", "非日累计统计周期，仅作独立观察",
}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e15:
        return None
    return float(value)


def _text(value, maximum=150):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


def _base_item(event, index):
    value = event if isinstance(event, dict) else {}
    def enum_value(field,allowed):
        return value.get(field) if isinstance(value.get(field),str) and value[field] in allowed else None
    return {
        "event_id": value.get("id") if _text(value.get("id")) else f"invalid_{index}",
        "account_id": value.get("account_id") if _text(value.get("account_id")) else None,
        "entity_id": value.get("entity_id") if _text(value.get("entity_id")) else None,
        "source": enum_value("source",SOURCES),
        "reported_status": enum_value("status",STATUSES),
        "action": enum_value("action",ACTIONS),
        "review_status": "invalid", "executed": False, "execution_eligible": False,
        "platform_verified": False, "blockers": [], "evidence": {}, "candidates": [],
        "note": "人工已核验报告仍是人工声明；本地观察与模拟均不能证明平台已应用操作。",
    }


def _validate(event, accounts):
    errors = []
    if not isinstance(event, dict):
        return ["事件必须为对象"]
    for field in ("id", "account_id", "entity_id"):
        if not _text(event.get(field)):
            errors.append(f"缺少有效字符串 {field}")
    if event.get("account_id") not in accounts:
        errors.append("事件账户不在本次白名单")
    if event.get("scope") not in SCOPES:
        errors.append("事件对象层级无效")
    if event.get("source") not in SOURCES:
        errors.append("来源只能是 manual_verified_report 或 simulation")
    if event.get("status") not in STATUSES:
        errors.append("事件应用状态无效")
    if event.get("action") not in ACTIONS:
        errors.append("事件动作未支持")
    if event.get("metric_basis") not in BASES and not (event.get("action") in EXPIRING_ACTIONS and event.get("metric_basis")=="unknown"):
        errors.append("缺少可用于前后窗口的明确 ROI 口径")
    if not _text(event.get("source_key"), 1000):
        errors.append("缺少精确快照来源键 source_key")
    if event.get("status") == "reported_applied" and event.get("source") != "manual_verified_report":
        errors.append("reported_applied 只能来自人工核验报告")
    if event.get("status") == "simulated" and event.get("source") != "simulation":
        errors.append("simulated 只能来自 simulation")
    return errors


def _observed_value(row, field):
    if field == "running":
        value = row["control"].get("running")
        return value if type(value) is bool else None
    return row["metrics"].get(field)


def _valid_row(row):
    if row["data"] is None or row["data"] > row["captured"] + timedelta(minutes=2):
        return False
    src = row["source"]
    period = src.get("metric_period", "day")
    if period == "day":
        if row["data"].date() != row["date"]:
            return False
    elif period == "live_session":
        start = _time(src.get("period_start"))
        if start is None or start > row["data"] or start.date() != row["date"]:
            return False
    else:
        return False
    allowed = OBSERVATION_NOTES | DERIVED_OBSERVATION_NOTES
    if period == "live_session":
        allowed = allowed | {"数据时间与统计日期不一致"}
    return not any(issue not in allowed for issue in row["issues"])


def _window(rows, start, end, fields, changed_field, declared_value, rules, *, validator=None, minimum_points=None):
    result = {"complete": False, "start": start.isoformat(), "end": end.isoformat(),
              "sample_count": 0, "delta": {}, "reason": "缺少完整时间边界"}
    if not rows:
        return result
    left = min(rows, key=lambda r: abs(r["data"] - start))
    right = min(rows, key=lambda r: abs(r["data"] - end))
    if abs(left["data"] - start) > timedelta(minutes=5) or abs(right["data"] - end) > timedelta(minutes=5):
        return result
    expected = (end - start).total_seconds()
    if abs((right["data"] - left["data"]).total_seconds() - expected) > 300:
        result["reason"] = "时间窗口长度不完整"
        return result
    selected = [r for r in rows if left["data"] <= r["data"] <= right["data"]]
    by_time = {}
    for row in selected:
        if not (validator or _valid_row)(row):
            result["reason"] = "窗口存在数据质量或统计周期问题"
            return result
        signature = tuple(row["metrics"].get(k) for k in fields) + (_observed_value(row, changed_field),)
        if row["data"] in by_time and by_time[row["data"]][0] != signature:
            result["reason"] = "同一数据时间存在冲突值"
            return result
        by_time[row["data"]] = (signature, row)
    selected = [by_time[t][1] for t in sorted(by_time)]
    result["sample_count"] = len(selected)
    minimum_points = minimum_points if minimum_points is not None else max(3, math.ceil(expected / (rules["max_gap_minutes"] * 60)) + 1)
    if len(selected) < minimum_points:
        result["reason"] = "独立时间样本不足"
        return result
    if any((b["data"] - a["data"]).total_seconds() > rules["max_gap_minutes"] * 60
           for a, b in zip(selected, selected[1:])):
        result["reason"] = "前后观察窗口存在采集断档"
        return result
    if any(row["metrics"].get(k) is None for row in selected for k in fields):
        result["reason"] = "缺少完整同口径金额、成本、订单或广告消耗"
        return result
    if any(_observed_value(row, changed_field) is None or _observed_value(row, changed_field) != declared_value
           for row in selected):
        result["reason"] = "报告变更值与连续快照不符，或观察期间发生再次变更"
        return result
    if any(b["metrics"][k] + 1e-8 < a["metrics"][k]
           for a, b in zip(selected, selected[1:]) for k in fields):
        result["reason"] = "累计指标回退或重置，不能计算前后差值"
        return result
    result.update(complete=True, reason="", start=left["data"].isoformat(), end=right["data"].isoformat(),
                  delta={k: right["metrics"][k] - left["metrics"][k] for k in fields})
    return result


def _financial_fields(row):
    projected = row
    if row["raw"]["scope"] == "creative" and any(row["metrics"].get(k) is not None for k in
            ("base_spend", "base_gmv", "base_net_gmv", "base_comprehensive_cost", "base_orders")):
        projected = {**row, "raw": {**row["raw"], "scope": "plan"}}
    cost, revenue = _amount_fields(projected)
    base = _uses_base(projected)
    spend = "base_spend" if base else "control_spend" if row["basis"] == "control_payment" else "spend"
    return cost, revenue, spend, "base_orders" if base else "orders"


def _summarize_window(window, fields, rules):
    cost, revenue, spend, orders = fields
    d = window["delta"]
    elapsed_hours = (_time(window["end"]) - _time(window["start"])).total_seconds() / 3600
    result = {
        "ad_spend": d[spend], "cost": d[cost], "revenue": d[revenue], "orders": d[orders],
        "hours": elapsed_hours, "ad_spend_per_hour": d[spend] / elapsed_hours,
        "roi": d[revenue] / d[cost] if d[cost] > 0 else None,
        "conversion_cost": d[cost] / d[orders] if d[orders] > 0 else None,
        "roi_sample_sufficient": d[spend] >= rules["min_spend"] and d[orders] >= rules["min_orders"],
        "ledger": "base" if spend == "base_spend" else "control" if spend == "control_spend" else "overall",
    }
    if window.get('segments'):
        result['segments'] = window['segments']
    return result


def _review(event, item, snapshots, rules, accounts, now):
    errors = item["blockers"]
    if event["status"] in {"rejected", "failed", "unknown"}:
        item["review_status"] = "not_applied"
        item["note"] = "该事件未声明已应用，不计算前后效果，也不将本地建议或 HTTP 成功当作实际变更。"
        return
    before_at, after_at = _time(event.get("before_at")), _time(event.get("after_at"))
    if before_at is None or after_at is None or before_at > after_at or after_at > now:
        errors.append("缺少有效前后时间，时间倒置或变更报告时间在未来")
        return
    if (after_at - before_at).total_seconds() > rules["max_gap_minutes"] * 60:
        errors.append("前后值报告间隔过长，无法界定变更边界")
        return
    item["evidence_authority"] = "manual_report_crosschecked_locally" if event["source"] == "manual_verified_report" else "synthetic_simulation_only"
    item["evidence"]["before_at"] = before_at.isoformat()
    item["evidence"]["after_at"] = after_at.isoformat()
    if event['action']=='refresh_creatives':
        item['review_status']='maintenance_review'
        item['note']='素材补充报告转入衰退维护复核；本条不证明已补充或效果恢复，需核对完整素材集合与后续窗口。'
        return
    if event['action']=='roi_target_up' and event.get('roi_recovery_root_id'):
        item['review_status']='roi_recovery_review'
        item['note']='关联ROI回调报告转入完整恢复链复核，不使用截短的通用前后窗口重复判断。'
        return
    if event["action"] in EXPIRING_ACTIONS:
        duration = _number(event.get("duration_minutes"))
        if duration is None or not 0 < duration <= 1440:
            errors.append("工具到期复核需要明确的1天以内duration_minutes")
            return
        due_at = after_at + timedelta(minutes=duration)
        item["review_status"] = "expired_pending_verification" if now >= due_at else "duration_pending"
        item["expiry"] = {"due_at": due_at.isoformat(), "expired": now >= due_at,
                          "basis": "reported_event_time", "task_stop_confirmed": False, "executed": False}
        warnings=[]
        if event["action"]=="one_click_control" and not 30<=duration<=60:warnings.append("报告控量时长超出用户0.5—1小时策略，需复核是否长期开启")
        if event["action"]=="one_click_boost" and duration<60:warnings.append("报告起量时长不足用户至少1小时策略")
        if event["action"]=="create_chase" and not 120<=duration<=240:warnings.append("报告追投时长不在用户2—4小时测试建议范围")
        item["expiry"]["policy_warnings"]=warnings
        item["note"] = "仅依据所报告开始时间和时长计算到期；到期不等于任务已经停止，需人工核验，不自动提交停止请求。"
        return

    field = ACTION_FIELDS[event["action"]]
    before, after = event.get("before"), event.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        errors.append("需要明确的before/after字段对象")
        return
    old, new = before.get(field), after.get(field)
    if field == "running":
        if type(old) is not bool or type(new) is not bool or old == new:
            errors.append("启停报告需要明确且发生改变的布尔running值")
            return
        expected = event["action"] == "resume_plan"
        if new != expected:
            errors.append("running前后方向与报告动作不一致")
            return
    else:
        old, new = _number(old), _number(new)
        if old is None or new is None or old <= 0 or new <= 0:
            errors.append(f"缺少明确且为正的{field}前后值")
            return
        if (event["action"] in DECREASING and new >= old) or (event["action"] in INCREASING and new <= old):
            errors.append("前后数值方向与报告动作不一致")
            return
    expected_scope = "control" if event["action"] in {"control_budget_up", "pause_control"} else "creative" if event["action"] == "pause_creative" else "plan"
    if event["scope"] != expected_scope:
        errors.append("报告动作与对象层级不符")
        return
    hours = 2 if field == "bid" else 1
    start, end = before_at - timedelta(hours=hours), after_at + timedelta(hours=hours)
    if len({start.date(), before_at.date(), after_at.date(), end.date()}) != 1:
        linked_roi = (event['action'] == 'roi_target_up' and event.get('roi_recovery_root_id')) or (
            event['action'] == 'roi_target_down' and any(isinstance(s,dict)
                and s.get('account_id') == event['account_id'] and s.get('entity_id') == event['entity_id']
                and isinstance(s.get('context'),dict) and s['context'].get('roi_recovery_event_id') == event['id'] for s in snapshots))
        if linked_roi:
            item['review_status']='roi_recovery_review'
            item['note']='关联ROI恢复报告跨自然日，转入专门日台账复核；需明确封账和次日起点，不直接相减日累计。'
            return
        errors.append("前后效果窗口跨自然日，不能相减日累计计数")
        item["review_status"] = "insufficient"
        return
    if end > now:
        errors.append(f"尚未积累变更后{hours}小时完整观察")
        item["review_status"] = "pending_window"
        item["observation_due_at"] = end.isoformat()
        return
    matches, invalid_matching = [], False
    for raw in snapshots:
        if not isinstance(raw, dict):
            continue
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        if not (raw.get("account_id") == event["account_id"] and raw.get("entity_id") == event["entity_id"]
                and raw.get("scope") == event["scope"] and raw.get("metric_basis") == event["metric_basis"]
                and source.get("series_key") == event["source_key"]):
            continue
        try:
            row = _record(raw, accounts, now)
        except (ValueError, TypeError, OverflowError, AttributeError):
            row = None
        if row is None or row["data"] is None:
            invalid_matching = True
            continue
        if start - timedelta(minutes=5) <= row["data"] <= end + timedelta(minutes=5):
            matches.append(row)
    if invalid_matching:
        errors.append("匹配来源存在无有效数据时间或无效格式的快照，不能用采集时间替代")
    if not matches:
        errors.append("没有同账户、对象、口径与精确来源的前后快照")
    expected_kinds = {"demo"} if event["source"] == "simulation" else {"import", "browser_dom", "legacy_db"}
    if any(row["source"]["kind"] not in expected_kinds for row in matches):
        errors.append("模拟与人工报告数据来源不匹配，禁止借用彼此的样本")
    identities = {(r["raw"].get("scene"), r["raw"].get("promotion_type"), r["source"]["kind"],
                   r["source"].get("metric_period", "day"), r["source"].get("period_start"), r["date"])
                  for r in matches}
    if len(identities) != 1:
        errors.append("前后来源包含混合周期、场次、产品或来源类型")
    if errors:
        item["review_status"] = "insufficient"
        return
    fields = _financial_fields(matches[-1])
    if not fields[1]:
        errors.append("当前口径不支持同周期金额效果复核")
        item["review_status"] = "insufficient"
        return
    pre = _window([r for r in matches if r["data"] <= before_at], start, before_at, set(fields), field, old, rules)
    post = _window([r for r in matches if r["data"] >= after_at], after_at, end, set(fields), field, new, rules)
    item["evidence"].update(before_window=pre, after_window=post,
                            value_check={"field": field, "reported_before": old, "reported_after": new})
    if not pre["complete"] or not post["complete"]:
        errors.extend(w["reason"] for w in (pre, post) if not w["complete"])
        item["review_status"] = "insufficient"
        return
    a, b = _summarize_window(pre, fields, rules), _summarize_window(post, fields, rules)
    item["evidence"].update(before_observation=a, after_observation=b)
    drop = 1 - b["ad_spend_per_hour"] / a["ad_spend_per_hour"] if a["ad_spend_per_hour"] > 0 else None
    item["comparison"] = {"ad_spend_change_percent": None if drop is None else -drop * 100,
                          "roi_before": a["roi"], "roi_after": b["roi"],
                          "roi_difference": b["roi"] - a["roi"] if a["roi"] is not None and b["roi"] is not None else None,
                          "causal_effect_proven": False, "profit_effect_proven": False}
    if event['action']=='roi_target_down' and a['ad_spend']==0:
        item['review_status']='roi_recovery_review'
        item['note']='根报告前为零消耗，不能计算相对消耗增幅；前后设置及窗口转入专门ROI恢复链复核，不把零基线当作恢复已达标。'
        return
    if a["ad_spend"] < rules["min_spend"]:
        errors.append("变更前广告消耗样本低于用户最小观察门槛")
        item["review_status"] = "insufficient"
        return
    item["review_status"] = "observed"
    item["note"] = "前后值和完整窗口已与本地同源数据交叉检查；只报告观察变化，不能证明变化由该操作造成，也不能证明利润效果或平台认证。"
    if (event["action"] == "bid_down" and event["scope"] == "plan"
            and matches[-1]["raw"].get("promotion_type") == "standard" and drop is not None and drop >= .5 - 1e-8):
        candidate = {"id": _uid("outcome", event["id"], "bid_restore"), "event_id": event["id"],
                     "account_id": event["account_id"], "entity_id": event["entity_id"], "scope": "plan",
                     "action": "bid_restore", "action_label": "人工复核是否回调上一出价",
                     "before": new, "after": old, "observed_spend_drop_percent": drop * 100,
                     "rule_basis": "local_observation_threshold", "threshold_percent": 50,
                     "mode": "simulation_only", "executed": False, "execution_eligible": False,
                     "source": event["source"], "platform_verified": False,
                     "observation_end_at":end.isoformat(),"historical":(now-end).total_seconds()>rules["max_age_seconds"],
                     "reason": "报告中的实际降价前后值在连续快照中吻合，降价后2小时广告消耗速度下降至少50%。这只是同窗相关，需排查开播、预算和流量变化。"}
        item["candidates"].append(candidate)


def review_outcomes(events, snapshots, settings, now=None):
    """Review explicit operation reports, without applying or authorizing actions.

    Event fields: id/account_id/entity_id/scope/action/metric_basis/source_key,
    source/status, before_at/after_at, before/after dictionaries, and optional
    duration_minutes. source_key is exactly snapshot.source.series_key.
    """
    if not isinstance(now, datetime):
        now = now_cn()
    else:
        now = now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)
    rules, accounts = _settings(settings)
    raw_events = events if isinstance(events, list) else []
    rows = snapshots if isinstance(snapshots, list) else []
    items, seen = [], set()
    for index, event in enumerate(raw_events):
        item = _base_item(event, index)
        try:
            item["blockers"] = _validate(event, accounts)
            if not item["blockers"]:
                if event["id"] in seen:
                    item["blockers"].append("重复事件ID不能重复复核或生成候选")
                else:
                    seen.add(event["id"])
                    _review(event, item, rows, rules, accounts, now)
        except (ValueError, TypeError, OverflowError, AttributeError, KeyError, ZeroDivisionError):
            item["review_status"] = "invalid"
            item["blockers"] = ["事件或来源格式无效，未进行效果推断"]
            item["candidates"] = []
        items.append(item)
    counts = Counter(item["review_status"] for item in items)
    return {"mode": "offline_outcome_review", "executed": False, "execution_eligible": False,
            "reviewed_at": now.isoformat(), "items": items,
            "candidates": [c for item in items for c in item["candidates"]],
            "summary": {"events": len(items), "review_states": dict(counts),
                        "manual_reported_applied": sum(i["source"] == "manual_verified_report" and i["reported_status"] == "reported_applied" and i["review_status"] != "invalid" for i in items),
                        "simulated": sum(i["source"] == "simulation" and i["reported_status"] == "simulated" and i["review_status"] != "invalid" for i in items),
                        "platform_confirmed_applied": 0, "real_executions": 0},
            "input_errors": [] if isinstance(events, list) else ["events必须为数组"],
            "limitations": ["人工应用报告与合成事件分别统计，不是平台认证或执行回执。",
                            "只比较同对象同源完整前后窗口，历史数据不伪装成实时结果。",
                            "回调候选只供模拟审阅，禁止自动并入真实执行资格。",
                            "预算变化不能证明因果或利润；工具到期不能证明任务已停止。"]}
