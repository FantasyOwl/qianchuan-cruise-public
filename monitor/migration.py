"""Account-local, budget-neutral handover plans. Never executes account actions.

The frozen plan is a local intention. Manual reports and comparable observations
advance its review state; neither constitutes platform execution certification.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN

from .actuation import amount
from .engine import CN, _record, _settings, _time
from .outcomes import review_outcomes
from .schema import now_cn

ACTIVE_STATES = {"ready_reduce", "pending_source_review", "ready_raise", "pending_target_review", "held"}


def _source(row):
    return ":".join(map(str, row["key"][3:]))


def _proposal(evaluation, row, action):
    matches = [p for p in evaluation.get("recommendations", [])
               if p.get("account_id") == row["raw"]["account_id"] and p.get("entity_id") == row["raw"]["entity_id"]
               and p.get("scope") == "plan" and p.get("source_key") == _source(row) and p.get("action") == action
               and p.get("eligible") is True and p.get("mode") == "simulation_only"]
    return matches[0] if len(matches) == 1 else None


def _latest(snapshots, leg, accounts, now):
    rows = []
    for raw in snapshots:
        if (raw.get("account_id") != leg["account_id"] or raw.get("entity_id") != leg["entity_id"]
                or raw.get("scope") != "plan" or raw.get("source", {}).get("series_key") != leg["series_key"]
                or raw.get("metric_basis") != leg["metric_basis"]):
            continue
        row = _record(raw, accounts, now)
        if row and row["data"] is not None:
            rows.append(row)
    if not rows:
        raise ValueError("迁移对象缺少精确来源和数据时间")
    rows.sort(key=lambda r: (r["data"], r["captured"]))
    latest = rows[-1]
    if any(r["metrics"] != latest["metrics"] for r in rows if r["data"] == latest["data"]):
        raise ValueError("迁移对象同一数据时间存在冲突")
    return latest


def _safe_pair(source, target, settings, evaluation, now):
    rules, accounts = _settings(settings)
    errors = []
    s, t = source["raw"], target["raw"]
    if s["account_id"] != t["account_id"] or s["entity_id"] == t["entity_id"]:
        errors.append("预算只能在同一账户的两个不同计划之间迁移")
    if any(s.get(k) != t.get(k) for k in ("scene", "promotion_type", "metric_basis")):
        errors.append("迁移计划的场景、推广产品和ROI口径必须一致")
    if source["basis"] not in {"overall_payment", "net", "marketing"}:
        errors.append("该ROI口径不能用于小时预算迁移")
    account = accounts[s["account_id"]]
    maintenance=[m for m in evaluation.get('maintenance_review',[]) if m.get('account_id')==s['account_id']
                 and m.get('entity_id')==s['entity_id'] and m.get('source_key')==_source(source)
                 and m.get('snapshot_id')==s.get('id')]
    if len(maintenance)!=1 or maintenance[0].get('state')!='handover_ready' or maintenance[0].get('is_current') is not True:
        errors.append('旧计划补素材报告、后续两小时观察与出价方案复核尚未按顺序通过')
    if not account.get("target_roi") or account.get("roi_basis") != source["basis"]:
        errors.append("用户目标ROI未设置或口径不一致")
    if source["source"]["kind"] != target["source"]["kind"]:
        errors.append("两侧来源类型不同，不能混合真实数据与模拟数据")
    for row in (source, target):
        if (row["issues"] or row["source"].get("coverage") != "report" or row["date"] != now.date()
                or not -120 <= (now-row["data"]).total_seconds() <= rules["max_age_seconds"]):
            errors.append("两侧必须是同日、新鲜且完整的独立报告")
        ctl = row["control"]
        if any(ctl.get(k) is not True for k in ("running", "history_known", "guarantee_verified")):
            errors.append("两侧运行状态、调控历史和保障限制尚未核验")
        if row["raw"].get("context", {}).get("model_stable") is not True:
            errors.append("两侧模型稳定性尚未明确核验")
    if abs((source["data"]-target["data"]).total_seconds()) > 300:
        errors.append("两侧报告更新时间相差超过5分钟")
    if s.get("scene") == "live" and s.get("promotion_type") == "universal":
        aweme_s, aweme_t = s.get("context", {}).get("aweme_id"), t.get("context", {}).get("aweme_id")
        if not aweme_s or not aweme_t or aweme_s == aweme_t:
            errors.append("直播全域计划须明确属于不同抖音号，禁止重叠投放")
    if any(a.get("account_id") == s["account_id"] and
           (a.get("priority") == "P0" or a.get("rule") in {"refund_spike", "pre_ship_refund_spike", "traffic_quality_decline"})
           for a in evaluation.get("alerts", [])):
        errors.append("账户存在紧急风险、退款突增或流量质量联合下降，暂停预算交接放量")
    return list(dict.fromkeys(errors))


def create_migration(source_id, target_id, snapshots, settings, evaluation, now=None):
    """Freeze an explicit pair; never infer consent or a recipient from ROI ranks."""
    now = now or now_cn()
    rules, accounts = _settings(settings)
    rows = []
    for snapshot_id in (source_id, target_id):
        matches = [s for s in snapshots if s.get("id") == snapshot_id and s.get("scope") == "plan"]
        if len(matches) != 1:
            raise ValueError("请选择两个明确的计划快照")
        row = _record(matches[0], accounts, now)
        if row is None or row["data"] is None:
            raise ValueError("计划快照格式或数据时间无效")
        leg = {"account_id": row["raw"]["account_id"], "entity_id": row["raw"]["entity_id"],
               "series_key": row["source"].get("series_key"), "metric_basis": row["basis"]}
        latest = _latest(snapshots, leg, accounts, now)
        if latest["raw"]["id"] != snapshot_id:
            raise ValueError("所选快照已不是该来源的最新数据")
        rows.append(row)
    source, target = rows
    errors = _safe_pair(source, target, settings, evaluation, now)
    context = source["raw"].get("context", {})
    if (source["control"].get("lifecycle") != "declining" or context.get("creative_replacement_failed") is not True
            or context.get("bid_maintenance_reviewed") is not True):
        errors.append("旧计划需确认衰退、已补充素材仍无恢复且已复核微调出价方案")
    down, up = _proposal(evaluation, source, "budget_down"), _proposal(evaluation, target, "budget_up")
    if not down or not up:
        errors.append("需同时满足旧计划小幅缩减与新计划连续3小时稳定放量的离线条件")
    transfer = None
    try:
        old_s, old_t = amount(source["metrics"].get("budget")), amount(target["metrics"].get("budget"))
        if down and up:
            transfer = min(old_s-amount(down["after"]), amount(up["after"])-old_t).quantize(Decimal(".01"), rounding=ROUND_DOWN)
            if not old_s*Decimal(".1") <= transfer <= old_s*Decimal(str(rules["budget_step_down"])):
                errors.append("新计划可接收额度不足以满足旧计划至少10%的小步下调")
            if transfer <= 0 or transfer > old_t*Decimal(str(rules["budget_step_up"])):
                errors.append("接收预算超出单次上调限制")
    except (ValueError, KeyError, TypeError):
        errors.append("缺少明确的两位小数预算及建议额度")
    result = {"mode": "simulation_only", "executed": False, "execution_eligible": False,
              "state": "blocked" if errors else "ready_reduce", "blockers": list(dict.fromkeys(errors)),
              "created_at": now.isoformat(), "account_id": source["raw"]["account_id"],
              "evidence_kind": "synthetic" if source["source"]["kind"] == "demo" else "manual_report",
              "metric_basis": source["basis"], "observe_minutes": 60,
              "note": "预算上限交接不保证流量迁移；每阶段需报告与完整观察，人工报告不是平台回执。"}
    if errors:
        return result
    for name, row, before, after in (("source", source, old_s, old_s-transfer), ("target", target, old_t, old_t+transfer)):
        result[name] = {"account_id": row["raw"]["account_id"], "entity_id": row["raw"]["entity_id"],
                        "snapshot_id": row["raw"]["id"], "series_key": row["source"]["series_key"],
                        "metric_basis": row["basis"], "kind": row["source"]["kind"],
                        "scene": row["raw"].get("scene"), "promotion_type": row["raw"].get("promotion_type"),
                        "before": float(before), "after": float(after)}
    result["amount"] = float(transfer)
    result["total_before"] = result["total_after"] = float(old_s+old_t)
    result["id"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return result


def review_migration(plan, snapshots, settings, evaluation, events, now=None):
    now = now or now_cn()
    result = copy.deepcopy(plan)
    result.update(executed=False, execution_eligible=False, next_step=None, reviewed_at=now.isoformat())
    errors = result["blockers"] = []
    if plan.get("cancelled"):
        result.update(state="cancelled", note="本地交接计划已取消；不撤销或证明任何账户操作。")
        return result
    if plan.get("final_review"):
        result = copy.deepcopy(plan['final_review'])
        result.update(historical=True, reviewed_at=now.isoformat(), next_step=None, executed=False, execution_eligible=False)
        return result
    if _time(plan["created_at"]).date() != now.date():
        result.update(state="expired", blockers=["交接跨自然日，需重新核对预算、历史和当日上调累计额度"])
        return result
    rules, accounts = _settings(settings)
    try:
        source, target = [_latest(snapshots, plan[name], accounts, now) for name in ("source", "target")]
        errors.extend(_safe_pair(source, target, settings, evaluation, now))
        for name, row in (("source", source), ("target", target)):
            if (row["source"]["kind"] != plan[name]["kind"] or row["raw"].get("scene") != plan[name]["scene"]
                    or row["raw"].get("promotion_type") != plan[name]["promotion_type"]):
                errors.append("交接来源类型、场景或推广产品已改变，不能沿用旧计划")
    except (ValueError, KeyError):
        result.update(state="held", blockers=["缺少交接计划对应的有效白名单来源"])
        return result
    created = _time(plan["created_at"])
    matched = {}
    relevant = []
    for name, action in (("source", "budget_down"), ("target", "budget_up")):
        leg = plan[name]
        reports = [e for e in events if e.get("account_id") == plan["account_id"] and e.get("entity_id") == leg["entity_id"]
                   and e.get("scope") == "plan" and e.get("status") in {"reported_applied", "simulated"}
                   and _time(e.get("after_at")) is not None and _time(e["after_at"]) >= created]
        relevant.extend(reports)
        matches = [e for e in reports if e.get("action") == action and e.get("metric_basis") == plan["metric_basis"]
                   and e.get("source_key") == leg["series_key"]
                   and e.get("source") == ("simulation" if leg["kind"] == "demo" else "manual_verified_report")
                   and _time(e.get("before_at")) is not None and _time(e["before_at"]) >= created
                   and e.get("before", {}).get("budget") == leg["before"] and e.get("after", {}).get("budget") == leg["after"]]
        if len(matches) > 1:
            errors.append("同一交接阶段存在多条匹配报告，需人工消除歧义")
        matched[name] = matches[0] if len(matches) == 1 else None
        if any(e not in matches for e in reports):
            errors.append("交接期间对象出现其他变更报告，需要重新核对预算与模型状态")
    reviews = {i["event_id"]: i for i in review_outcomes(relevant, snapshots, settings, now)["items"]}
    s_event, t_event = matched["source"], matched["target"]
    if t_event and (not s_event or _time(t_event.get("before_at")) < _time(s_event["after_at"])+timedelta(minutes=60)):
        errors.append("接收预算早于旧计划缩减及至少1小时观察，违反分步交接顺序")
    for name, row, event in (("source", source, s_event), ("target", target, t_event)):
        expected = plan[name]["after" if event else "before"]
        if row["metrics"].get("budget") != expected:
            errors.append("当前预算与已报告交接阶段不一致，不能把预览当作完成")
    if errors:
        result["state"] = "held"
        return result
    if not s_event:
        down, up = _proposal(evaluation, source, "budget_down"), _proposal(evaluation, target, "budget_up")
        if not down or not up:
            errors.append("两侧当前离线策略条件已不再同时满足")
        result["state"] = "held" if errors else "ready_reduce"
        if not errors:
            result["next_step"] = _step(plan, "source", "budget_down", down)
        return result
    source_review = reviews.get(s_event["id"], {})
    result["source_review"] = {k: source_review.get(k) for k in ("review_status", "comparison", "blockers")}
    if source_review.get("review_status") != "observed":
        result["state"] = "pending_source_review"
        result["blockers"] = source_review.get("blockers") or ["旧计划缩减后尚无完整前后1小时观察"]
        return result
    comparison = source_review.get("comparison", {})
    if (comparison.get("ad_spend_change_percent") is None or comparison["ad_spend_change_percent"] <= -50
            or not comparison.get("roi_before") or comparison.get("roi_after") is None
            or comparison["roi_after"] < comparison["roi_before"]*.8):
        result.update(state="held", blockers=["旧计划交接后消耗速度下降至少50%或ROI恶化20%，先复核流量断层；这是本地保护阈值"])
        return result
    if t_event:
        target_review = reviews.get(t_event["id"], {})
        result["target_review"] = {k: target_review.get(k) for k in ("review_status", "comparison", "blockers")}
        result["state"] = "completed_reported" if target_review.get("review_status") == "observed" else "pending_target_review"
        authority = "合成" if plan.get("evidence_kind") == "synthetic" else "人工"
        result["note"] = f"两侧{authority}报告及观察窗口已对照；不代表平台确认、因果验证或投放盈利。"
        return result
    up = _proposal(evaluation, target, "budget_up")
    if not up or amount(up["after"])-amount(up["before"]) < amount(plan["amount"]):
        result.update(state="held", blockers=["新计划当前稳定放量条件或当日剩余上调额度不足"])
        return result
    result.update(state="ready_raise", next_step=_step(plan, "target", "budget_up", up))
    return result


def _step(plan, name, action, proposal):
    leg = plan[name]
    return {"action": action, "entity_id": leg["entity_id"], "before": leg["before"], "after": leg["after"],
            "evidence_proposal_id": proposal["id"], "execution_eligible": False,
            "note": "仅交接阶段预览；当前程序没有账户执行入口"}
