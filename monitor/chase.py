"""Auditable material-chase safeguards. No account executor or network access."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .engine import CN, _hour_windows, _number, _record, _settings, _time, _uid

FEATURES = ("chase_budget_topup", "parent_crowding_overlap", "account_roi_overlap", "rechase_24h")
REQUIRED_CONTEXT = {
    "enums": {"control_type": ["material_chase", "one_click_boost", "other", "unknown"],
              "target_metric_basis": ["overall_payment", "control_payment", "net", "marketing", "settled_7d", "unknown"]},
    "source_keys": ["parent_source_key", "account_source_key"],
    "ids": ["parent_plan_id", "material_id", "causal_review_control_id", "last_chase_id", "previous_chase_id", "last_pause_control_id"],
    "times": ["control_started_at", "causal_review_at", "last_chase_at", "last_pause_at"],
    "flags": ["latest_budget_verified", "parent_control_inventory_verified", "crowding_causality_verified",
              "account_roi_causality_verified", "chase_history_known"],
}


def _ctx(rec):
    value = rec["raw"].get("context")
    return value if isinstance(value, dict) else {}


def _id(value):
    return value if isinstance(value, str) and 0 < len(value) <= 150 else None


def _source_key(value):
    return value if isinstance(value, str) and 0 < len(value) <= 1000 else None


def _fresh(rec, rules, now):
    return bool(rec["data"] and not rec["issues"] and rec["date"] == now.date()
        and rec["source"].get("metric_period", "day") == "day"
        and -120 <= (now - rec["data"]).total_seconds() <= rules["max_age_seconds"])


def _target(last, account):
    """A paid control ledger may use an overall-payment account target only
    through an explicit, matching assessment-basis declaration."""
    value = _number(account.get("target_roi"))
    basis = account.get("roi_basis")
    declaration = _ctx(last).get("target_metric_basis")
    if value is None or value <= 0 or last["basis"] != "control_payment":
        return None
    if basis == declaration and basis in {"overall_payment", "control_payment"}:
        return value
    return None


def _link(last, groups, kind, account_basis, rules, now):
    ctx = _ctx(last)
    field = "parent_source_key" if kind == "plan" else "account_source_key"
    declared_key = _source_key(ctx.get(field))
    if not declared_key:
        return None, ["context." + field]
    parent_id = _id(ctx.get("parent_plan_id"))
    if kind == "plan" and not parent_id:
        return None, ["context.parent_plan_id"]
    matches = []
    for key, rows in groups.items():
        if key[0] != last["key"][0] or key[2] != kind or key[5] != account_basis:
            continue
        latest = max(rows, key=lambda r: (r["data"] or r["captured"], r["captured"]))
        if latest["source"].get("series_key") != declared_key:
            continue
        if kind == "plan" and latest["key"][1] != parent_id:
            continue
        if not _fresh(latest, rules, now) or latest["source"].get("coverage") != "report":
            continue
        if last["data"] is None or abs((latest["data"] - last["data"]).total_seconds()) > 300:
            continue
        matches.append((latest, sorted(rows, key=lambda r: (r["data"] or r["captured"], r["captured"]))))
    if len(matches) != 1:
        return None, [f"唯一、同账户同考核口径、报告完整且相差不超过5分钟的{kind}来源"]
    return matches[0], []


def _base_projection(rows):
    result = []
    for r in rows:
        metrics = {field: r["metrics"].get("base_" + field) for field in
            ("spend", "gmv", "net_gmv", "comprehensive_cost", "orders", "clicks", "impressions")}
        metrics["control_spend"] = 0.0
        result.append(dict(r, raw={**r["raw"], "scope": "creative"}, metrics=metrics))
    return result


def _event_windows(rows, started, rules):
    """One full pre-start hour and the latest full hour wholly after start."""
    dated = sorted([r for r in rows if r["data"]], key=lambda r: (r["data"], r["captured"]))
    if not dated or started is None:
        return None, ["明确追投开始时间及前后观察"]
    boundary = min(dated, key=lambda r: abs(r["data"] - started))
    if abs(boundary["data"] - started) > timedelta(minutes=5):
        return None, ["追投开始时点附近5分钟内的边界快照"]
    before = [r for r in dated if r["data"] <= boundary["data"]]
    pre_windows, _ = _hour_windows(before, rules)
    post_windows, _ = _hour_windows(dated, rules)
    pre = pre_windows[-1] if pre_windows else None
    post = post_windows[-1] if post_windows else None
    if not pre or not post or not pre.get("complete") or not post.get("complete"):
        return None, ["追投开始前及运行期间各一个完整同日小时"]
    if _time(post.get("start")) < started - timedelta(minutes=5):
        return None, ["最新完整小时必须全部位于追投开始之后"]
    return {"before": pre, "during": post}, []


def _causal_review(last, flag, during_end, now):
    ctx = _ctx(last)
    review_at = _time(ctx.get("causal_review_at"))
    started = _time(ctx.get("control_started_at"))
    if ctx.get(flag) is not True:
        return False, "只有时段重合，未有明确人工因果复核"
    if ctx.get("causal_review_control_id") != last["key"][1]:
        return False, "人工因果复核未关联当前追投任务ID"
    if review_at is None or started is None or review_at < started or not -120 <= (now - review_at).total_seconds() <= 600:
        return False, "人工因果复核时间缺失、过期或早于本次追投"
    if during_end is None or review_at < during_end - timedelta(minutes=5):
        return False, "人工因果复核未覆盖当前异常小时"
    return True, "人工因果复核已明确关联当前任务与异常时段"


def _verified_pauses(events,records,now):
    """Cross-check imported pause reports; never grant complete history from one report."""
    pauses=[]
    for event in events if isinstance(events,list) else []:
        if not isinstance(event,dict) or event.get("action")!="pause_control" or event.get("scope")!="control":continue
        kind="demo" if event.get("source")=="simulation" and event.get("status")=="simulated" else "real" if event.get("source")=="manual_verified_report" and event.get("status")=="reported_applied" else None
        before,after=_time(event.get("before_at")),_time(event.get("after_at"))
        if kind is None or before is None or after is None or before>after or after>now:continue
        if not isinstance(event.get("before"),dict) or not isinstance(event.get("after"),dict):continue
        if event["before"].get("running") is not True or event["after"].get("running") is not False:continue
        matching=[r for r in records if r["key"][0]==event.get("account_id") and r["key"][1]==event.get("entity_id")
            and r["key"][2]=="control" and r["basis"]==event.get("metric_basis") and r["source"].get("series_key")==event.get("source_key")
            and (r["source"]["kind"]=="demo" if kind=="demo" else r["source"]["kind"] in {"import","browser_dom","legacy_db"})]
        matching=[r for r in matching if r["data"] and r["data"]<=now and r["data"]<=r["captured"]+timedelta(minutes=2)
                  and r["source"].get("metric_period","day")=="day" and r["data"].date()==r["date"]]
        pre=[r for r in matching if r["data"] and abs((r["data"]-before).total_seconds())<=300]
        post=[r for r in matching if r["data"] and abs((r["data"]-after).total_seconds())<=300]
        if not pre or not post or any(r["control"].get("running") is not True for r in pre) or any(r["control"].get("running") is not False for r in post):continue
        materials={_id(_ctx(r).get("material_id")) for r in pre+post}
        if len(materials)!=1 or None in materials:continue
        pauses.append({"account_id":event["account_id"],"control_id":event["entity_id"],"material_id":next(iter(materials)),
                       "paused_at":after,"event_id":event.get("id"),"source":event["source"]})
    return pauses


def _rechase(last, all_control_latest, now, rules, pauses):
    ctx, task_id = _ctx(last), last["key"][1]
    started = _time(ctx.get("control_started_at"))
    last_at = _time(ctx.get("last_chase_at"))
    pause_at = _time(ctx.get("last_pause_at"))
    last_id, previous_id, pause_id = [_id(ctx.get(k)) for k in ("last_chase_id", "previous_chase_id", "last_pause_control_id")]
    missing, concerns = [], []
    if ctx.get("chase_history_known") is not True:
        missing.append("同素材完整追投历史尚未核验")
    if started is None:
        missing.append("control_started_at")
    if not last_id or last_at is None:
        missing.append("last_chase_id与last_chase_at须明确关联当前/上次任务")
    elif started and last_id == task_id:
        if abs((last_at - started).total_seconds()) > 300:
            missing.append("当前任务的最近追投时间与实际开始时间不一致")
    elif started and last_id != task_id and 0 <= (started - last_at).total_seconds() < 86400:
        concerns.append({"kind": "other_task_within_24h", "other_control_id": last_id, "other_started_at": last_at.isoformat()})
    if ctx.get("last_pause_at") is not None and pause_at is None:
        missing.append("关停历史时间无效")
    if pause_at:
        if not pause_id:
            missing.append("关停时间缺少last_pause_control_id关联")
        elif pause_id != task_id:
            if previous_id is None or previous_id != pause_id:
                missing.append("上次关停任务与previous_chase_id不一致")
            if started and 0 <= (started - pause_at).total_seconds() < 86400:
                concerns.append({"kind": "restarted_within_24h_after_other_pause", "other_control_id": pause_id, "paused_at": pause_at.isoformat()})
    material_id = _id(ctx.get("material_id"))
    if not material_id:
        missing.append("明确material_id，不能根据任务名判断同素材")
    for pause in pauses:
        if (started and pause["account_id"]==last["key"][0] and pause["control_id"]!=task_id and pause["material_id"]==material_id
                and 0<=(started-pause["paused_at"]).total_seconds()<86400):
            concerns.append({"kind":"locally_crosschecked_pause_report_within_24h","other_control_id":pause["control_id"],
                             "paused_at":pause["paused_at"].isoformat(),"event_id":pause["event_id"],"report_source":pause["source"],"platform_verified":False})
    for other in all_control_latest:
        if other["key"][0] != last["key"][0] or other["key"][1] == task_id or _ctx(other).get("material_id") != material_id or material_id is None:
            continue
        if _fresh(other,rules,now) and other["control"].get("running") is True:
            concerns.append({"kind": "same_material_other_active_task", "other_control_id": other["key"][1],
                "parent_plan_id": _id(_ctx(other).get("parent_plan_id"))})
        other_pause = _time(_ctx(other).get("last_pause_at"))
        if (started and other_pause and other_pause<=other["captured"]+timedelta(minutes=2)
                and _ctx(other).get("chase_history_known") is True and _ctx(other).get("last_pause_control_id") == other["key"][1]
                and 0 <= (started - other_pause).total_seconds() < 86400):
            concerns.append({"kind": "other_report_pause_within_24h", "other_control_id": other["key"][1], "paused_at": other_pause.isoformat()})
    return missing, concerns


def evaluate_chase(snapshots, settings, now=None,events=None):
    if not isinstance(now, datetime):
        now = datetime.now(CN)
    else:
        now = now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)
    rules, accounts = _settings(settings)
    groups, rejected = defaultdict(list), 0
    for raw in snapshots if isinstance(snapshots, list) else []:
        try:
            record = _record(raw, accounts, now)
        except (TypeError, ValueError, OverflowError, AttributeError):
            record = None
        if record is None:
            rejected += 1
        else:
            groups[record["key"]].append(record)
    latest_controls = []
    pauses=_verified_pauses(events,[r for rows in groups.values() for r in rows],now)
    for key, records in groups.items():
        latest = max(records, key=lambda r: (r["data"] or r["captured"], r["captured"]))
        if key[2] == "control" and _ctx(latest).get("control_type") == "material_chase":
            latest_controls.append(latest)
    alerts, recommendations = [], []
    coverage = {feature: {"evaluated_entities": 0, "triggered": 0, "missing": []} for feature in FEATURES}

    for key, records in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        if key[2] != "control":
            continue
        records = sorted(records, key=lambda r: (r["data"] or r["captured"], r["captured"]))
        last = records[-1]
        raw, m, ctl, ctx = last["raw"], last["metrics"], last["control"], _ctx(last)
        account_id, task_id = key[:2]
        source_key = ":".join(map(str, key[3:]))
        identity = (account_id, task_id, "control", source_key)
        parent_id, material_id = _id(ctx.get("parent_plan_id")), _id(ctx.get("material_id"))

        def cover(feature, missing=(), triggered=False):
            entry = coverage[feature]
            entry["evaluated_entities"] += 1
            entry["triggered"] += int(bool(triggered))
            if missing and len(entry["missing"]) < 100:
                entry["missing"].append({"account_id": account_id, "entity_id": task_id, "source_key": source_key,
                    "fields": list(dict.fromkeys(missing))})

        def alert(rule, priority, title, evidence, risk=""):
            alerts.append({"id": _uid(*identity, rule), "priority": priority, "title": title,
                "account_id": account_id, "entity_id": task_id, "scope": "control", "source_key": source_key,
                "source_basis": last["basis"], "rule": rule, "rule_basis": "user_strategy", "evidence": evidence, "risk": risk})

        if ctx.get("control_type") != "material_chase":
            for feature in FEATURES:
                cover(feature, ["context.control_type须明确material_chase；任务名不能证明种类"])
            continue
        if not _fresh(last, rules, now) or last["basis"] != "control_payment":
            for feature in FEATURES:
                cover(feature, ["新鲜、同日、日累计且完整的control_payment独立台账"])
            continue
        target = _target(last, accounts[account_id])
        account_basis = accounts[account_id].get("roi_basis")
        parent, parent_missing = _link(last, groups, "plan", account_basis, rules, now)
        account, account_missing = _link(last, groups, "account", account_basis, rules, now)
        windows, _ = _hour_windows(records, rules)
        recent = windows[-2:]
        current = windows[-1] if windows else None
        started = _time(ctx.get("control_started_at"))
        history_missing, rechase_concerns = _rechase(last, latest_controls, now, rules,pauses)
        if rechase_concerns:
            alert("material_rechase_24h", "P1", "同素材追投存在24小时保护或同时运行关系，需复核",
                  {"material_id": material_id, "current_control_id": task_id, "relationships": rechase_concerns},
                  "这是用户策略的同素材保护检查，不是平台违规判定；当前任务追加预算不等同于新建追投")
        cover("rechase_24h", history_missing, bool(rechase_concerns))

        def blockers():
            out = list(parent_missing + account_missing)
            if target is None:
                out.append("用户ROI目标缺失，或target_metric_basis未明确匹配独立任务考核语义")
            if not material_id or not parent_id:
                out.append("素材与母计划关系不明确")
            if last["source"].get("coverage") != "report":
                out.append("只采到可见页面，任务覆盖范围未完整核验")
            if ctl.get("running") is not True:
                out.append("当前追投运行状态未核验为开启")
            if ctl.get("history_known") is not True:
                out.append("调控历史未知")
            if ctl.get("guarantee_verified") is not True:
                out.append("当前产品成本保障与调控限制未核验")
            if ctx.get("manual_reviewed") is not True:
                out.append("当前任务与关系尚未人工核验")
            if raw.get("promotion_type") not in {"universal", "standard", "search", "chengfang"}:
                out.append("推广产品类型未知")
            changed = _time(ctl.get("last_change_at"))
            if changed is None and _number(ctl.get("changes_today")) != 0:
                out.append("缺少最近调控时间或明确的零次调控计数")
            if changed and (now - changed).total_seconds() < rules["cooldown_minutes"] * 60:
                out.append("尚未满足至少1小时调控冷却")
            return out

        def advice(rule, priority, action, label, evidence, extra=(), before=None, after=None, change=None):
            block = list(dict.fromkeys(blockers() + list(extra)))
            if action == 'pause_control':before,after = ctl.get('running'),False
            recommendations.append({"id": _uid(*identity, rule, action), "intent_id": _uid(account_id, task_id, action, now.date()),
                "priority": priority, "account_id": account_id, "entity_id": task_id, "scope": "control",
                "source_key": source_key, "metric_basis": last["basis"], "target_type": "control_task", "target_id": task_id,
                "action": action, "action_label": label, "before": before, "after": after, "change_pct": change,
                "reason": label + "；依据明确任务台账与关联报告，保留人工复核边界",
                "expected_goal": "控制追加投入风险并验证对母计划与账户的影响",
                "risk": "时段重合不证明因果；策略阈值由用户设置，官方保障资格需独立核验；仅离线模拟，不执行到账户。",
                "evidence": evidence, "parameters": {"parent_plan_id": parent_id, "material_id": material_id},
                "eligible": not block, "blockers": block, "mode": "simulation_only", "rule_basis": "user_strategy", "rule": rule})

        # Identity declarations must be stable throughout the assessed task history.
        relation_fields = ("parent_plan_id", "material_id", "parent_source_key", "account_source_key", "control_type")
        relation_missing = []
        if any(_ctx(r).get(field) != ctx.get(field) for r in records for field in relation_fields):
            relation_missing.append("任务关系在观察序列中发生变化，不能拼接不同母计划/素材/来源")

        budget_missing = list(parent_missing + account_missing + history_missing + relation_missing)
        if target is None:
            budget_missing.append("用户ROI目标及target_metric_basis的明确匹配")
        if ctx.get("latest_budget_verified") is not True:
            budget_missing.append("context.latest_budget_verified=true")
        if ctx.get("parent_control_inventory_verified") is not True:
            budget_missing.append("context.parent_control_inventory_verified=true")
        if ctx.get("model_stable") is not True:
            budget_missing.append("已核验测试模型稳定")
        if ctx.get("compliance_ok") is not True:
            budget_missing.append("当前素材合规状态尚未核验")
        if ctl.get("running") is not True:
            budget_missing.append("当前追投运行状态为开启")
        if rechase_concerns:
            budget_missing.append("同素材24小时或并行追投保护尚未解决")
        budget = m.get("budget")
        if budget is None or budget <= 0:
            budget_missing.append("最新有效任务预算")
        if m.get("control_spend") is None or budget is not None and m.get("control_spend") is not None and m["control_spend"] > budget + .01:
            budget_missing.append("当前独立任务消耗与预算关系不完整或矛盾")
        complete_test = len(recent) == 2 and all(w.get("complete") and w.get("roi") is not None
            and w.get("spend") is not None and w["spend"] >= rules["min_spend"] and w.get("orders") is not None
            and w["orders"] >= rules["min_orders"] and target is not None and w["roi"] >= target for w in recent)
        if not complete_test:
            budget_missing.append("连续两个完整小时独立任务ROI达标及足够转化样本")
        test_start=_time(recent[0].get("start")) if len(recent)==2 else None
        if started is None or test_start is None or started>test_start:
            budget_missing.append("完整测试窗口必须位于明确的任务开始时间之后")
        for linked,label,project in ((parent,"母计划基础",True),(account,"账户",False)):
            if not linked:continue
            linked_windows,_=_hour_windows(_base_projection(linked[1]) if project else linked[1],rules)
            paired=linked_windows[-2:]
            if (len(paired)!=2 or len(recent)!=2 or target is None or not all(w.get("complete") and w.get("roi") is not None
                    and w["roi"]>=target and w.get("spend") is not None and w["spend"]>=rules["min_spend"]
                    and w.get("orders") is not None and w["orders"]>=rules["min_orders"] for w in paired)
                    or any(abs((_time(a["end"])-_time(b["end"])).total_seconds())>300 for a,b in zip(paired,recent))):
                budget_missing.append(label+"同窗完整小时效果不满足继续追加条件")
        new_budget = round(budget * 1.25, 2) if budget is not None and budget > 0 else None
        increase = new_budget - budget if new_budget is not None else None
        parent_budget, parent_total, share, projected_share = None, m.get("parent_chase_budget_total"), None, None
        if parent:
            parent_budget = parent[0]["metrics"].get("budget")
        if parent_budget is None or parent_budget <= 0 or parent_total is None or budget is None or parent_total + .01 < budget:
            budget_missing.append("关联母计划当前预算与含当前任务的追投预算合计")
        elif new_budget is not None and (new_budget > parent_budget * .3 + 1e-8 or parent_total + increase > parent_budget * .3 + 1e-8):
            budget_missing.append("本次追加会超过母计划预算30%的用户策略空间")
        if account:
            am = account[0]["metrics"]
            spend, control_spend = am.get("spend"), am.get("control_spend")
            if spend is None or spend <= 0 or control_spend is None or control_spend > spend:
                budget_missing.append("关联账户整体消耗与追投总消耗的完整同日台账")
            else:
                share = control_spend / spend
                projected_share = (control_spend + increase) / (spend + increase) if increase is not None else None
                if share > rules["control_share_cap"] or projected_share is not None and projected_share > rules["control_share_cap"]:
                    budget_missing.append("当前或新增预算全消耗后的账户追投占比将超过20%用户策略")
                if am.get("control_share_pct") is not None and abs(am["control_share_pct"] - share * 100) > .05:
                    budget_missing.append("账户声明的追投占比与金额台账不一致")
                if m.get("control_spend") is not None and control_spend + .01 < m["control_spend"]:
                    budget_missing.append("账户追投总消耗小于当前任务消耗，关联时点或范围不一致")
        topup_triggered = not budget_missing
        if topup_triggered:
            advice("chase_budget_topup", "P2", "control_budget_up", "素材追投预算追加25%测试",
                {"hourly_control": recent, "strategy_target_roi": target, "target_metric_basis": ctx.get("target_metric_basis"),
                 "current_budget": budget, "new_budget": new_budget, "single_step_max_user_rule": .5,
                 "parent_budget": parent_budget, "parent_chase_budget_total": parent_total, "increment": increase,
                 "account_control_share": share, "projected_control_share_if_increment_spent": projected_share},
                before=budget, after=new_budget, change=.25)
        cover("chase_budget_topup", budget_missing, topup_triggered)

        # Observed decline and an overlapping task provide a suspicion only.
        own_during_ok = bool(current and current.get("complete") and current.get("spend") is not None and current["spend"] >= rules["min_spend"])
        if current and current.get("start"):
            during_start = _time(current["start"])
            own_during_ok = own_during_ok and all(r["control"].get("running") is True for r in records if r["data"] and r["data"] >= during_start)
        parent_overlap_missing = list(parent_missing + relation_missing)
        parent_event = None
        if parent:
            parent_event, missing = _event_windows(_base_projection(parent[1]), started, rules)
            parent_overlap_missing += missing
        if not own_during_ok:
            parent_overlap_missing.append("完整追投运行小时与正向独立消耗")
        parent_triggered = False
        if parent_event and not parent_overlap_missing:
            pre, post = parent_event["before"], parent_event["during"]
            if current is None or abs((_time(post["start"]) - _time(current["start"])).total_seconds()) > 300:
                parent_overlap_missing.append("母计划异常小时与当前追投小时边界不相容")
            elif pre.get("roi") is None or post.get("roi") is None or pre.get("spend") is None or pre["spend"] < rules["min_spend"]:
                parent_overlap_missing.append("母计划开始前及当前基础金额/成本/ROI样本")
            else:
                base_traffic = _base_projection(parent[1])
                # These fields were independently mapped and validated by the core windows.
                pre_flow = pre.get("ctr")
                # Absolute impressions are calculated from the same hour endpoints.
                def impression_delta(window):
                    a, b = _time(window["start"]), _time(window["end"])
                    dated = [r for r in base_traffic if r["data"]]
                    left = min(dated, key=lambda r: abs(r["data"] - a))
                    right = min(dated, key=lambda r: abs(r["data"] - b))
                    if left["metrics"].get("impressions") is None or right["metrics"].get("impressions") is None:
                        return None
                    return right["metrics"]["impressions"] - left["metrics"]["impressions"]
                pre_impressions, post_impressions = impression_delta(pre), impression_delta(post)
                flow_down = pre_impressions is not None and pre_impressions > 0 and post_impressions is not None and post_impressions <= pre_impressions * .7
                roi_down = pre["roi"] > 0 and post["roi"] <= pre["roi"] * .8
                if flow_down or roi_down:
                    parent_triggered = True
                    causal, causal_note = _causal_review(last, "crowding_causality_verified", _time(post["end"]), now)
                    evidence = {"parent_plan_id": parent_id, "control_started_at": started.isoformat(),
                        "before_base_hour": pre, "during_base_hour": post, "before_base_impressions": pre_impressions,
                        "during_base_impressions": post_impressions, "flow_down": flow_down, "roi_down": roi_down,
                        "manual_causality_verified": causal, "causal_review_control_id": _id(ctx.get("causal_review_control_id"))}
                    alert("suspected_parent_crowding", "P1", "疑似挤压：母计划基础流量或ROI下降与追投时段重合", evidence,
                          "重合不等于追投导致；直播承接、竞争及自然流量变化仍需人工排查")
                    advice("suspected_parent_crowding", "P1", "pause_control", "人工复核暂停该素材追投候选", evidence,
                           extra=[] if causal else [causal_note])
        cover("parent_crowding_overlap", parent_overlap_missing, parent_triggered)

        account_overlap_missing = list(account_missing + relation_missing)
        if target is None:
            account_overlap_missing.append("有效用户ROI目标及明确target_metric_basis匹配")
        if not own_during_ok:
            account_overlap_missing.append("追投运行中的完整小时台账")
        account_event = None
        if account:
            account_event, missing = _event_windows(account[1], started, rules)
            account_overlap_missing += missing
        account_triggered = False
        if account_event and not account_overlap_missing:
            pre, post = account_event["before"], account_event["during"]
            if current is None or abs((_time(post["start"]) - _time(current["start"])).total_seconds()) > 300:
                account_overlap_missing.append("账户异常小时与当前追投小时不相容")
            elif pre.get("roi") is None or post.get("roi") is None or post.get("spend") is None or post["spend"] < rules["min_spend"]:
                account_overlap_missing.append("账户时段独立差值ROI与消耗证据")
            elif post["roi"] < target:
                account_triggered = True
                causal, causal_note = _causal_review(last, "account_roi_causality_verified", _time(post["end"]), now)
                evidence = {"control_started_at": started.isoformat(), "before_account_hour": pre, "during_account_hour": post,
                    "strategy_target_roi": target, "crossed_from_target_or_above": pre["roi"] >= target,
                    "manual_causality_verified": causal, "target_metric_basis": ctx.get("target_metric_basis")}
                alert("suspected_account_roi_impact", "P1", "账户时段ROI低于配置目标，与当前追投时段重合", evidence,
                      "仅报告时段关联；日累计ROI与本时段ROI分开核算，不能据此自动归因于追投")
                advice("suspected_account_roi_impact", "P1", "pause_control", "人工复核账户ROI保护下的暂停追投候选", evidence,
                       extra=[] if causal else [causal_note])
        cover("account_roi_overlap", account_overlap_missing, account_triggered)

    alerts.sort(key=lambda r: (r["priority"], r["account_id"], r["entity_id"], r["rule"]))
    recommendations.sort(key=lambda r: (r["priority"], r["account_id"], r["entity_id"], r["rule"]))
    selected = set()
    for r in recommendations:
        if not r["eligible"]:
            continue
        key = (r["account_id"], r["parameters"].get("parent_plan_id") or r["target_id"])
        if key in selected:
            r["eligible"] = False
            r["blockers"].append("同一母计划本轮已有优先建议，禁止叠加调控")
        else:
            selected.add(key)
    for item in coverage.values():
        item["state"] = "triggered" if item["triggered"] else "insufficient" if item["missing"] else "observed" if item["evaluated_entities"] else "not_applicable"
    return {"alerts": alerts, "recommendations": recommendations,
        "coverage": {"execution_enabled": False, "rule_basis": "user_strategy", "chase": coverage,
            "rejected_count": rejected, "required_metrics": ["parent_chase_budget_total"], "required_context": REQUIRED_CONTEXT,
            "limits": ["预算追加仅对明确material_chase任务，默认25%且上限50%", "时段重合只报疑似；暂停候选资格需明确人工因果复核", "账户目标为null时不从页面target_roi补值", "当前任务预算追加不视为新建追投"]}}
