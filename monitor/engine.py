"""Deterministic advice from complete, comparable observation windows.

No network or account execution lives in this module. ``eligible`` only means
that an offline strategy has enough evidence; every proposal stays simulated.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from .lifecycle import inspect_lifecycle
from .growth import assess_growth_stability
from .observation_time import review_observation_time

CN = timezone(timedelta(hours=8), "Asia/Shanghai")
DEFAULTS = {
    "max_age_seconds": 600, "min_spend": 50, "min_orders": 10,
    "max_gap_minutes": 20, "roi_low_ratio": .8, "chase_roi_low_ratio": .7,
    "budget_step_up": .2, "budget_step_down": .1, "daily_budget_up_cap": .5,
    "cooldown_minutes": 60, "spend_surge_ratio": 1.5,
    "refund_spike_pp": 5, "creative_ctr_decay": .15, "control_share_cap": .2,
    "cold_bid_max_target_ratio": None,
    "growth_roi_max_ratio": 1.2, "growth_cost_max_ratio": 1.2, "growth_spend_max_ratio": 1.3,
}
BASES = {"overall_payment", "net", "marketing", "control_payment", "settled_7d"}
SCOPES = {"account", "plan", "creative", "live", "control", "product", "keyword"}
KINDS = {"browser_dom", "legacy_db", "import", "demo"}
_evaluation_records = ContextVar('evaluation_records', default=None)
ACTION_NAMES = {
    "budget_down": "下调预算", "budget_up": "上调预算",
    "pause_control": "暂停追加投入", "hold_scaling": "暂停放量",
    "inspect_recovery": "检查断流原因", "replace_creative": "补充新素材",
    "review_cold_budget":"复核冷启动起步预算",
}


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        return float(value) if math.isfinite(value) and 0 <= value <= 1e15 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _time(value):
    if not isinstance(value, str) or len(value) > 50:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=CN) if dt.tzinfo is None else dt.astimezone(CN)
    except (ValueError, TypeError, OverflowError):
        return None


def _uid(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:20]


def _settings(settings):
    settings = settings if isinstance(settings, dict) else {}
    raw = settings.get("rules") if isinstance(settings.get("rules"), dict) else {}
    rules = {}
    for key, default in DEFAULTS.items():
        value = _number(raw.get(key))
        rules[key] = default if value is None else value
    # A malformed direct caller cannot remove the minimal evidence guardrails.
    rules["max_age_seconds"] = min(3600, max(60, rules["max_age_seconds"]))
    rules["max_gap_minutes"] = min(30, max(1, rules["max_gap_minutes"]))
    rules["min_orders"] = max(1, rules["min_orders"])
    rules["cooldown_minutes"] = max(60, rules["cooldown_minutes"])
    rules["budget_step_up"] = min(.2, max(.01, rules["budget_step_up"]))
    rules["budget_step_down"] = min(.3, max(.1, rules["budget_step_down"]))
    rules["daily_budget_up_cap"] = min(.5, rules["daily_budget_up_cap"])
    for key, ceiling in (('growth_roi_max_ratio',1.2),('growth_cost_max_ratio',1.2),('growth_spend_max_ratio',1.3)):
        rules[key]=min(ceiling,max(1,rules[key]))
    if rules['cold_bid_max_target_ratio'] != 1.4:
        rules['cold_bid_max_target_ratio'] = None
    raw_accounts = settings.get("accounts")
    accounts = {}
    if isinstance(raw_accounts, list):
        for account in raw_accounts:
            if isinstance(account, dict) and isinstance(account.get("id"), str):
                accounts[account["id"]] = account
    return rules, accounts


def _assessment_matches(record, account):
    if account.get('roi_basis') == record['basis']:return True
    ctx = record['raw'].get('context',{})
    return (record['raw'].get('scope')=='control' and record['basis']=='control_payment'
        and account.get('roi_basis')=='overall_payment' and isinstance(ctx,dict)
        and ctx.get('target_metric_basis')=='overall_payment'
        and isinstance(ctx.get('control_type'),str) and ctx['control_type'] in {'material_chase','one_click_boost'})


@contextmanager
def _record_cache_scope():
    """Reuse parsing only within one immutable-input evaluation and thread."""
    token = _evaluation_records.set({})
    try:
        yield
    finally:
        _evaluation_records.reset(token)


def _record(raw, accounts, now):
    cache = _evaluation_records.get()
    if cache is None or not isinstance(raw, dict):
        return _parse_record(raw, accounts, now)
    # Parsing depends on account membership, not that account's ROI policy.
    # Retain the input object so Python cannot reuse its id during this call.
    key = (id(raw), now, raw.get('account_id') in accounts)
    if key not in cache:
        cache[key] = (raw, _parse_record(raw, accounts, now))
    record = cache[key][1]
    if record is None:
        return None
    # Each consumer still owns its working values and diagnostics.
    return {**record, 'metrics': dict(record['metrics']), 'issues': list(record['issues']),
            'source': dict(record['source']), 'control': dict(record['control'])}


def _parse_record(raw, accounts, now):
    raw = review_observation_time(raw)
    if not isinstance(raw, dict):
        return None
    account_id, entity_id, scope = raw.get("account_id"), raw.get("entity_id"), raw.get("scope")
    if account_id not in accounts or not isinstance(entity_id, str) or not entity_id or scope not in SCOPES:
        return None
    source = raw.get("source")
    if not isinstance(source, dict) or source.get("kind") not in KINDS:
        return None
    captured = _time(source.get("captured_at"))
    data = _time(source.get("data_at"))
    if captured is None or captured > now + timedelta(minutes=2):
        return None
    metric_date = source.get("metric_date")
    try:
        date = datetime.strptime(metric_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        return None
    metrics = {str(key): _number(val) for key, val in metrics.items() if isinstance(key, str)}
    issues = []
    if any(val is not None and metrics[key] is None for key, val in raw["metrics"].items() if isinstance(key, str)):
        issues.append("含非有限、负数或非数字指标，不能用于调控")
    supplied = raw.get("quality")
    if isinstance(supplied, dict) and isinstance(supplied.get("issues"), list):
        issues.extend(str(v)[:200] for v in supplied["issues"][:30])
    if data is None:
        issues.append("缺少页面数据更新时间，不能用采集时间代替")
    elif data > captured + timedelta(minutes=2) or data > now + timedelta(minutes=2):
        issues.append("页面数据时间在未来")
    elif data.date() != date:
        issues.append("数据时间与统计日期不一致")
    if date != now.date():
        issues.append("统计日期不是今日，仅供历史观察")
    if source.get("metric_period", "day") != "day":
        issues.append("非日累计统计周期，仅作独立观察")
    basis = raw.get("metric_basis", "unknown")
    if basis not in BASES:
        basis = "unknown"
        issues.append("ROI 口径未确认")
    for key in ("ctr", "cvr", "refund_rate_1h"):
        if metrics.get(key) is not None and metrics[key] > 100:
            issues.append(f"{key} 百分比超过 100")
    control = raw.get("control") if isinstance(raw.get("control"), dict) else {}
    kind = source["kind"]
    series_key = source.get("series_key")
    if not isinstance(series_key, str):
        series_key = ""
    scene = raw.get("scene", "unknown")
    promotion_type = raw.get("promotion_type", "unknown")
    if not isinstance(scene, str) or not isinstance(promotion_type, str):
        return None
    key = (account_id, entity_id, scope, scene,
           promotion_type, basis, kind, series_key, metric_date)
    return {"raw": raw, "key": key, "metrics": metrics, "source": source,
            "captured": captured, "data": data, "date": date, "basis": basis,
            "issues": list(dict.fromkeys(issues)), "control": control}


def _uses_base(record):
    """A declared base ledger always wins over an inclusive plan total."""
    if record["raw"].get("scope") != "plan":
        return False
    m = record["metrics"]
    return bool(m.get("control_spend", 0)) or any(m.get(k) is not None for k in (
        "base_spend", "base_gmv", "base_net_gmv", "base_comprehensive_cost", "base_orders"))


def _amount_fields(record):
    basis, m = record["basis"], record["metrics"]
    base = _uses_base(record)
    if basis == "overall_payment":
        return ("base_spend", "base_gmv") if base else ("spend", "gmv")
    if basis == "net":
        return ("base_spend", "base_net_gmv") if base else ("spend", "net_gmv")
    if basis == "marketing":
        return ("base_comprehensive_cost", "base_net_gmv") if base else ("comprehensive_cost", "net_gmv")
    if basis == "control_payment":
        return "control_spend", "control_gmv"
    # Seven-day settlement is an after-the-fact measure, not an hourly control ROI.
    return "spend", None


def _hour_windows(records, rules):
    if not records or records[-1]["data"] is None:
        return [], ["没有可核验的数据时间，无法构造小时窗口"]
    dated = [r for r in records if r["data"] is not None]
    dated.sort(key=lambda r: (r["data"], r["captured"]))
    by_time = {}
    conflicts = set()
    for rec in dated:
        previous = by_time.get(rec["data"])
        if previous and previous["metrics"] != rec["metrics"]:
            conflicts.add(rec["data"])
        by_time[rec["data"]] = rec
    dated = sorted(by_time.values(), key=lambda r: r["data"])
    latest = dated[-1]["data"]
    cost_key, numerator = _amount_fields(dated[-1])
    base = _uses_base(dated[-1])
    orders_key = "base_orders" if base else "orders"
    windows = []
    reasons = []
    tolerance = timedelta(minutes=5)
    for offset in (2, 1, 0):
        end = latest - timedelta(hours=offset)
        start = end - timedelta(hours=1)
        result = {"start": start.isoformat(), "end": end.isoformat(),
                  "complete": False, "spend": None, "cost": None,
                  "revenue": None, "roi": None, "orders": None,
                  "ctr": None, "cvr": None, "conversion_cost": None,
                  "ledger": "base" if base else "control" if dated[-1]["basis"] == "control_payment" else "overall",
                  "reason": "缺少完整小时边界样本"}
        left = min(dated, key=lambda r: abs(r["data"] - start))
        right = min(dated, key=lambda r: abs(r["data"] - end))
        if abs(left["data"] - start) > tolerance or abs(right["data"] - end) > tolerance:
            windows.append(result)
            continue
        span = (right["data"] - left["data"]).total_seconds()
        if not 55 * 60 <= span <= 65 * 60:
            result["reason"] = "小时边界间隔偏差过大"
            windows.append(result)
            continue
        selected = [r for r in dated if left["data"] <= r["data"] <= right["data"]]
        if any(r["issues"] for r in selected):
            result["reason"] = "窗口内存在数据质量问题"
            windows.append(result)
            continue
        if any(r["data"] in conflicts for r in selected):
            result["reason"] = "同一数据时间出现不一致指标，可能为回补或口径变化"
            windows.append(result)
            continue
        if any((b["data"] - a["data"]).total_seconds() > rules["max_gap_minutes"] * 60
               for a, b in zip(selected, selected[1:])):
            result["reason"] = "小时窗口采集断档"
            windows.append(result)
            continue
        keys = {cost_key, orders_key, "control_spend"}
        if not base:
            keys.update({"clicks", "impressions"})
        if numerator:
            keys.add(numerator)
        reset = any(a["metrics"].get(k) is not None and b["metrics"].get(k) is not None
                    and b["metrics"][k] + 1e-8 < a["metrics"][k]
                    for a, b in zip(selected, selected[1:]) for k in keys)
        if reset:
            result["reason"] = "累计指标回退或重置，停止计算差值"
            windows.append(result)
            continue

        def delta(key):
            if not key or any(r["metrics"].get(key) is None for r in selected):
                return None
            return right["metrics"][key] - left["metrics"][key]

        spend_key = "control_spend" if dated[-1]["basis"] == "control_payment" else "spend"
        if base:
            spend_key = "base_spend"
        spend, cost, revenue = delta(spend_key), delta(cost_key), delta(numerator)
        orders = delta(orders_key)
        # Inclusive clicks/impressions are not a base-ledger conversion denominator.
        clicks, impressions = (None, None) if base else (delta("clicks"), delta("impressions"))
        result.update({"complete": spend is not None, "spend": spend, "cost": cost,
                       "observed_start": left['data'].isoformat(), "observed_end": right['data'].isoformat(), "observed_seconds": span,
                       "revenue": revenue, "orders": orders,
                       "roi": revenue / cost if revenue is not None and cost is not None and cost > 0 else None,
                       "ctr": clicks / impressions * 100 if clicks is not None and impressions is not None and impressions > 0 else None,
                       "cvr": orders / clicks * 100 if orders is not None and clicks is not None and clicks > 0 else None,
                       "conversion_cost": cost / orders if cost is not None and orders is not None and orders > 0 else None,
                       "reason": "" if spend is not None else "缺少累计消耗，无法计算完整小时"})
        windows.append(result)
    reasons.extend(dict.fromkeys(w["reason"] for w in windows if not w["complete"]))
    return windows, reasons


def evaluate(snapshots: list[dict], settings: dict, now: datetime | None = None) -> dict:
    """Return alerts and simulated proposals. Invalid observations fail closed."""
    if not isinstance(now, datetime):
        now = datetime.now(CN)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=CN)
    else:
        now = now.astimezone(CN)
    rules, accounts = _settings(settings)
    raw_snapshots = snapshots if isinstance(snapshots, list) else []
    groups = defaultdict(list)
    invalid_count = 0
    for raw in raw_snapshots:
        try:
            record = _record(raw, accounts, now)
        except (TypeError, ValueError, OverflowError, AttributeError):
            record = None
        if record is None:
            invalid_count += 1
        else:
            groups[record["key"]].append(record)
    alerts, recommendations, entities, lifecycle_reviews = [], [], [], []
    counts = {"fresh": 0, "stale": 0, "insufficient": 0}

    for key in sorted(groups, key=lambda k: tuple(map(str, k))):
        records = sorted(groups[key], key=lambda r: (r["data"] or r["captured"], r["captured"]))
        last = records[-1]
        raw, m, ctl = last["raw"], last["metrics"], last["control"]
        account_id, entity_id, scope = key[:3]
        source_key = ":".join(map(str, key[3:]))
        identity = [account_id, entity_id, scope, source_key]
        issues = list(last["issues"])
        data_time = last["data"]
        age = (now - data_time).total_seconds() if data_time else None
        stale = data_time is not None and (age > rules["max_age_seconds"] or last["date"] != now.date())
        if stale:
            issues.append("数据已过期，禁止据此生成有资格的调控建议")
        account = accounts[account_id]
        target = _number(account.get("target_roi"))
        if target is None or target <= 0:
            issues.append("尚未配置账户目标 ROI")
            target = None
        if not _assessment_matches(last,account) or last["basis"] == "unknown":
            issues.append("账户目标 ROI 与快照口径不一致或未确认")
        if last["basis"] == "settled_7d":
            issues.append("7 日结算 ROI 仅作投后观察，不用于小时调控")
        cost_key, revenue_key = _amount_fields(last)
        if last["basis"] in BASES - {"settled_7d"} and (m.get(cost_key) is None or m.get(revenue_key) is None):
            issues.append("缺少当前 ROI 口径对应的累计金额分子或成本分母")
        orders_key = "base_orders" if _uses_base(last) else "orders"
        if m.get(orders_key) is None:
            issues.append("缺少基础计划订单样本量，不能用总订单替代" if orders_key == "base_orders" else "缺少订单样本量，无法验证 ROI 调控样本")
        windows, window_issues = _hour_windows(records, rules)
        completed = [w for w in windows if w["complete"]]
        quality = "stale" if stale else "insufficient" if issues or len(completed) < 2 else "fresh"
        all_issues = list(dict.fromkeys(issues + window_issues))
        counts[quality] += 1
        entities.append({"account_id": account_id, "entity_id": entity_id, "scope": scope,
                         "scene": raw.get("scene"), "promotion_type": raw.get("promotion_type"),
                         "metric_basis": last["basis"], "source_key": source_key,
                         "source": last["source"], "quality": quality, "issues": all_issues,
                         "hourly": windows, "age_seconds": round(age, 1) if age is not None else None,
                         "latest_snapshot_id": str(raw.get("id", ""))})

        def alert(rule, priority, title, evidence, risk=""):
            alerts.append({"id": _uid(*identity, rule), "priority": priority, "title": title,
                           "account_id": account_id, "entity_id": entity_id, "scope": scope,
                           "rule": rule, "evidence": evidence, "risk": risk,
                           "source_basis": last["basis"], "rule_basis": "user_strategy"})

        if stale:
            alert("data_stale", "P1", "监测数据过期", {"data_at": last["source"].get("data_at"),
                  "age_seconds": round(age, 1), "metric_date": last["source"].get("metric_date")},
                  "历史数据不能支持当前账户操作")
        elif issues:
            alert("data_insufficient", "P2", "数据条件不足，调控建议保持阻断", {"issues": issues})

        def sample_ok(window):
            if not window or not window["complete"] or window.get("spend") is None or window["spend"] < rules["min_spend"]:
                return False
            orders = window.get("orders")
            if orders is not None and orders >= rules["min_orders"]:
                return True
            # Zero orders is only a meaningful stop-loss sample with an explicit cost target.
            target_cost = m.get("target_cost")
            return orders == 0 and target_cost is not None and target_cost > 0 and window["spend"] >= target_cost * rules["min_orders"]

        fresh_data = not stale and not last["issues"] and data_time is not None and age >= -120
        context = raw.get('context', {}) if isinstance(raw.get('context'), dict) else {}
        assessment_matches = _assessment_matches(last,account)
        roi_usable = fresh_data and target is not None and assessment_matches and last["basis"] not in {"unknown", "settled_7d"}
        budget = m.get("budget")
        recent_change = _time(ctl.get("last_change_at"))
        phase=None
        if scope=='plan':
            min_impressions=_number(settings.get('rules',{}).get('specialized_min_impressions')) if isinstance(settings,dict) and isinstance(settings.get('rules',{}),dict) else None
            min_impressions=min(1_000_000,max(100,min_impressions)) if min_impressions is not None else 1000
            phase=inspect_lifecycle(last,records,windows,rules,target if roi_usable else None,now,_uses_base(last),min_impressions)
            lifecycle_reviews.append(phase)
        common_blockers = []
        if not fresh_data:
            common_blockers.append("数据质量或时效不满足要求")
        if issues:
            common_blockers.append("当前快照证据不完整，需先处理数据质量提示")
        if not roi_usable:
            common_blockers.append("目标 ROI 与已确认指标口径不完整")
        if ctl.get("history_known") is not True:
            common_blockers.append("账户调控历史未知，无法验证操作间隔")
        elif recent_change is None and _number(ctl.get("changes_today")) != 0:
            common_blockers.append("缺少最近一次调控时间")
        if recent_change is not None and (now - recent_change).total_seconds() < rules["cooldown_minutes"] * 60:
            common_blockers.append("距离最近一次调控不足冷却时间")
        if ctl.get("guarantee_verified") is not True:
            common_blockers.append("当前产品成本保障及调控限制未核验")
        if raw.get("promotion_type") not in {"universal", "chengfang", "standard", "search"}:
            common_blockers.append("推广产品类型未知")
        if last["source"].get("coverage") != "report":
            common_blockers.append("只采到可见页面，账户与计划覆盖范围未完整核验")

        def propose(rule, priority, action, reason, evidence, change=None, blockers=()):
            block = list(dict.fromkeys(common_blockers + list(blockers)))
            before = budget if action in {"budget_down", "budget_up"} else None
            after = round(before * (1 + change), 2) if before is not None and change is not None else None
            if action == 'pause_control':
                before, after = ctl.get('running'), False
                if before is not True:block.append('当前追加投入未核验为开启，不能获得暂停资格')
            recommendations.append({"id": _uid(*identity, rule, action), "priority": priority,
                "account_id": account_id, "entity_id": entity_id, "scope": scope,
                "source_key": source_key, "metric_basis": last["basis"],
                "action": action, "action_label": ACTION_NAMES[action], "change_pct": change,
                **({'target_id':phase['plan_id'],'target_type':'plan'} if phase else {}),
                **({'target_id':entity_id,'target_type':'control_task','parameters':{
                    'parent_plan_id':context.get('parent_plan_id'),'material_id':context.get('material_id')}}
                    if action=='pause_control' and scope=='control' else {}),
                "before": before, "after": after, "reason": reason,
                "expected_goal": {"budget_down": "降低消耗速度并观察效果", "budget_up": "小幅验证跑量能力",
                    "pause_control": "隔离追加投入风险", "hold_scaling": "控制无效增量消耗",
                    "inspect_recovery": "核实断流与承接状态", "replace_creative": "恢复创意点击与转化能力",
                    "review_cold_budget":"核对指定日预算与初始测试安排，不跳涨或提交预算"}[action],
                "risk": ("演示数据假定条件，仅用于测试。" if last["source"].get("kind") == "demo" else "") + "调整可能影响投放模型与成本保障；本条仅供离线模拟，不执行账户操作。",
                "evidence": evidence, "eligible": not block, "blockers": block,
                "mode": "simulation_only", "rule_basis": "user_strategy", "rule": rule})

        # No trend or account action is inferred from an old or unverified page.
        if not fresh_data:
            continue
        if phase and phase['state']=='stage_conflict':
            alert('lifecycle_conflict','P1','计划生命周期证据冲突，先核对阶段',{'lifecycle':phase})
        if phase and phase['cold_bid_review']['state'] == 'above_user_cap':
            alert('cold_bid_above_user_cap','P1','冷启动成本出价超过用户确认的目标成本1.4倍上限',{'lifecycle':phase})
        if phase and phase['initial_budget_review']:
            alert('cold_initial_budget','P2','冷启动指定日预算低于用户300元起步策略',{'lifecycle':phase})
            propose('cold_initial_budget','P2','review_cold_budget','当前预算低于起步策略，仅复核初始测试安排，不直接跳涨补齐',
                    {'lifecycle':phase},blockers=['只核对初始预算安排；不能把300元起步策略转换为现有计划的跳涨动作'])
            recommendations[-1].update(target_type='plan_budget_review',target_id=phase['plan_id'],
                parameters={'observed_daily_budget':budget,'strategy_initial_daily_budget':300})
        balance, expected = m.get("balance"), m.get("expected_day_spend")
        if balance is not None and expected is not None and expected > 0 and balance <= expected * 1.2:
            alert("balance_120pct", "P1", "余额低于预计当日消耗的 120%",
                  {"balance": balance, "expected_day_spend": expected, "threshold": expected * 1.2})
        current = windows[-1] if windows else None
        previous = windows[-2] if len(windows) > 1 else None
        last_two = windows[-2:]
        last_three = windows[-3:]

        # Compare reported 1-hour refund percentages without turning missing into zero.
        refund_spike = False
        refund = m.get("refund_rate_1h")
        prior = [r for r in records if r["data"] and data_time - timedelta(minutes=65) <= r["data"] <= data_time - timedelta(minutes=55)
                 and not r["issues"] and r["metrics"].get("refund_rate_1h") is not None]
        if refund is not None and prior:
            old_refund = prior[-1]["metrics"]["refund_rate_1h"]
            if refund - old_refund >= rules["refund_spike_pp"]:
                refund_spike = True
                evidence = {"previous_refund_rate_1h": old_refund, "refund_rate_1h": refund,
                            "increase_percentage_points": round(refund - old_refund, 4)}
                alert("refund_spike", "P1", "1 小时退款率上升，暂停放量验证", evidence,
                      "退款窗口可能回补，需核验商品与履约原因")
                propose("refund_spike", "P1", "hold_scaling", "退款率较一小时前上升达到策略阈值", evidence)

        if current and current["complete"] and current["spend"] is not None and current["spend"] > 0 and balance is not None and balance < current["spend"]:
            alert("balance_one_hour", "P0", "按最近完整小时速度，余额不足一小时",
                  {"balance": balance, "last_hour_spend": current["spend"],
                   "estimated_hours": round(balance / current["spend"], 3)}, "估算基于历史流速，不能保证未来消耗速度")

        if len(last_two) == 2 and all(w["complete"] and w["spend"] == 0 for w in last_two) and ctl.get("running") is True:
            evidence = {"hourly": last_two, "running": True}
            alert("no_spend_2h", "P1", "投放中对象连续两个完整小时无消耗", evidence)
            propose("no_spend_2h", "P1", "inspect_recovery", "投放状态为开启但连续两小时消耗差值为零", evidence)

        if roi_usable and current and previous and all(sample_ok(w) for w in (previous, current)) and previous["spend"] > 0 and current["roi"] is not None:
            if current["spend"] > previous["spend"] * rules["spend_surge_ratio"] and current["roi"] < target * rules["roi_low_ratio"]:
                evidence = {"previous_hour": previous, "current_hour": current, "target_roi": target}
                alert("spend_surge_low_roi", "P0", "单小时消耗突增且 ROI 低于策略底线", evidence)
                propose("spend_surge_low_roi", "P0", "hold_scaling", "完整小时消耗突增且同口径小时 ROI 不达标", evidence)

        budget_blockers = []
        if budget is None or budget <= 0:
            budget_blockers.append("缺少有效当前预算")
        if scope != "plan":
            budget_blockers.append("预算调节建议仅对明确计划对象生成")
        if scope == "plan" and m.get("control_spend") is None and not _uses_base(last):
            budget_blockers.append("主计划与追加投入核算范围未确认")
        if scope == "plan" and _uses_base(last):
            if any(m.get(k) is None for k in (cost_key, revenue_key, "base_spend", "base_orders")):
                budget_blockers.append("含追加投入的主计划缺少对应口径基础金额、基础成本或基础订单样本")

        low_two = roi_usable and len(last_two) == 2 and all(sample_ok(w) and w["roi"] is not None and w["roi"] < target * rules["roi_low_ratio"] for w in last_two)
        if low_two:
            evidence = {"hourly": last_two, "target_roi": target, "low_ratio": rules["roi_low_ratio"]}
            alert("roi_low_2h", "P1", "连续两个完整小时 ROI 低于目标的策略比例", evidence)
            if scope == "plan":
                propose("roi_low_2h", "P1", "budget_down", "同口径 ROI 连续两小时低于目标，先小幅降低消耗", evidence,
                        change=-rules["budget_step_down"], blockers=budget_blockers)

        stable_three = roi_usable and len(last_three) == 3 and all(sample_ok(w) and w["roi"] is not None and w["roi"] >= target for w in last_three)
        if stable_three and scope == "plan":
            extra = list(budget_blockers)
            cold=ctl.get('lifecycle')=='cold'
            if cold and phase:
                if not phase['cold_budget_ready']:extra+=phase['missing']+phase['conflicts']
            elif ctl.get("lifecycle") not in {"growing", "mature"}:
                extra.append("计划生命周期不是已确认成长期或成熟期")
            if phase and phase['state']=='stage_conflict':extra+=phase['conflicts']
            if ctl.get("running") is not True:
                extra.append("计划运行状态未确认开启")
            original = _number(ctl.get("budget_original"))
            increase = _number(ctl.get("budget_increase_today"))
            if original is None or original <= 0 or increase is None:
                extra.append("缺少当日起始预算或累计上调金额")
            elif budget is not None and (increase + budget * rules["budget_step_up"] > original * rules["daily_budget_up_cap"] + 1e-8
                                        or budget * (1 + rules["budget_step_up"]) > original * (1 + rules["daily_budget_up_cap"]) + 1e-8):
                extra.append("本次上调会超过当日起始预算累计上调上限")
            stability=assess_growth_stability(last_three,rules)
            extra.extend(stability['blockers'])
            if refund_spike:
                extra.append("退款率突增，先暂停放量")
            if m.get("control_spend") is not None and m.get("spend") is not None and m["spend"] > 0 and m["control_spend"] / m["spend"] > rules["control_share_cap"]:
                extra.append("追加投入消耗占比超过策略上限")
            for metric in ("ctr", "cvr"):
                known = [w[metric] for w in last_three]
                if all(v is not None for v in known) and known[0] > 0 and known[-1] < known[0] * .85:
                    extra.append("最近三小时流量转化质量明显下降")
            propose('cold_budget_stable_3h' if cold else "roi_stable_3h", "P2", "budget_up",
                    ('冷启动测试门槛和三个小时同口径数据正向，复核小幅追加预算' if phase and phase['cold_budget_ready'] else
                     '三个小时ROI达标，冷启动测试门槛或前置证据仍待复核') if cold else "连续三小时同口径 ROI 达标，验证小幅追加预算",
                    {"hourly": last_three, "target_roi": target,"growth_stability":stability,**({'lifecycle':phase} if phase else {})},
                    change=rules["budget_step_up"], blockers=extra)

        # Control ROI is a separate ledger; never use the parent-plan ROI here.
        if scope == "control" and last["basis"] == "control_payment" and roi_usable and sample_ok(current):
            if current["roi"] is not None and current["roi"] < target * rules["chase_roi_low_ratio"]:
                start,end=_time(current["start"]),_time(current["end"])
                points=[r for r in records if r["data"] and start<=r["data"]<=end]
                rois=[r["metrics"]["control_gmv"]/r["metrics"]["control_spend"]
                      if r["metrics"].get("control_gmv") is not None and (r["metrics"].get("control_spend") or 0)>0 else None for r in points]
                no_improvement=(len(points)>=4 and all(v is not None and v<target*rules["chase_roi_low_ratio"] for v in rois)
                                and all(b<=a+1e-8 for a,b in zip(rois,rois[1:])))
                evidence = {"hourly": current, "target_roi": target, "low_ratio": rules["chase_roi_low_ratio"],
                            "sampled_task_roi":rois,"no_improvement_1h":no_improvement}
                alert("control_roi_low_1h", "P1", f"追加投入完整小时 ROI 低于目标的 {rules['chase_roi_low_ratio']*100:g}% 策略线", evidence)
                propose("control_roi_low_1h", "P1", "pause_control", "追投完整小时低于底线，并要求小时内采样持续低位且未好转", evidence,
                        blockers=[] if no_improvement else ["尚未证明连续一小时低于策略线且无好转，不能仅凭小时聚合值关停"])
        if scope == "control" and last["basis"] == "control_payment" and current and current["complete"] and budget is not None and budget > 0 and current["spend"] > budget * .5:
            evidence = {"last_hour_control_spend": current["spend"], "control_budget": budget}
            alert("control_spend_fast", "P0", "追加投入一小时消耗超过预算 50%", evidence)
            propose("control_spend_fast", "P0", "pause_control", "追加投入消耗速度超过用户策略上限", evidence)

        target_cost = m.get("target_cost")
        if scope == "plan" and target_cost is not None and target_cost > 0 and len(last_two) == 2 and all(sample_ok(w) and w["conversion_cost"] is not None and w["conversion_cost"] > target_cost * 1.3 for w in last_two):
            evidence = {"hourly": last_two, "target_cost": target_cost}
            alert("cost_high_2h", "P1", "连续两小时转化成本超过目标 30%", evidence)
            if not low_two:
                propose("cost_high_2h", "P1", "budget_down", "完整小时转化成本连续超出用户策略目标", evidence,
                        change=-rules["budget_step_down"], blockers=budget_blockers)

        if scope == "creative" and len(last_three) == 3 and all(w["complete"] and w["ctr"] is not None for w in last_three):
            ctrs = [w["ctr"] for w in last_three]
            if ctrs[0] > 0 and ctrs[0] > ctrs[1] > ctrs[2] and ctrs[2] <= ctrs[0] * (1 - rules["creative_ctr_decay"]):
                evidence = {"hourly_ctr_percent": ctrs, "relative_decline": round(1 - ctrs[-1] / ctrs[0], 4)}
                priority = "P1" if isinstance(raw.get('context'),dict) and raw['context'].get('is_core_creative') is True else "P2"
                alert("creative_ctr_decay_3h", priority, "核心素材连续三小时点击率下降达到策略阈值" if priority=='P1' else "素材连续三小时点击率下降达到策略阈值", evidence)
                propose("creative_ctr_decay_3h", priority, "replace_creative", "小时点击与展示差值计算的 CTR 连续下降", evidence)

    if invalid_count:
        alerts.append({"id": "invalid_observations", "priority": "P1", "title": "部分快照被拒绝，未参与规则计算",
                       "account_id": "", "entity_id": "", "rule": "invalid_observations",
                       "evidence": {"invalid_count": invalid_count}, "risk": "检查账户白名单、时间与字段格式",
                       "source_basis": "unknown", "rule_basis": "data_validation"})
    alerts.sort(key=lambda x: (x["priority"], x["account_id"], x["entity_id"], x["rule"]))
    recommendations.sort(key=lambda x: (x["priority"], x["account_id"], x["entity_id"], x["rule"]))
    eligible = sum(r["eligible"] for r in recommendations)
    summary = f"观察 {len(entities)} 个来源序列；新鲜且具备基本小时证据 {counts['fresh']} 个，数据过期 {counts['stale']} 个，证据不足 {counts['insufficient']} 个。生成 {len(alerts)} 条告警、{len(recommendations)} 条离线建议，其中 {eligible} 条满足已声明的模拟条件。"
    if not entities:
        summary = "暂无可用账户快照。请接通已配置账户页面或导入带明确时间与指标口径的数据。"
    return {"summary": summary, "alerts": alerts, "recommendations": recommendations, "entities": entities,'lifecycle_reviews':lifecycle_reviews,
            "coverage": {"input_count": len(raw_snapshots), "accepted_count": len(raw_snapshots) - invalid_count,
                         "invalid_count": invalid_count, "series_count": len(entities), "quality_counts": counts,
                         "execution_enabled": False, "rule_basis": "user_strategy",
                         "hour_window_minutes": 60, "boundary_tolerance_minutes": 5,
                         "unsupported": ["无认证开放平台 API 执行器", "未自动识别官方成本保障资格", "可见页面不代表完整计划或创意覆盖", "未实现关键词排名/同质化素材语义识别/主播承接判断"]}}
