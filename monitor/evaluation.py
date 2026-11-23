"""One offline evaluation pipeline for monitoring, replay, AI and request preview."""
from datetime import datetime
from .engine import evaluate,CN,_settings,_record_cache_scope
from .roi_tuning import conflicting_roi_sources
from .lifecycle import lifecycle_blockers
from .roi_recovery import review_roi_recovery
from .live_exclusion import MUTEX_RULES
from .analytics import analyze_monitoring
from .strategies import extend_evaluation
from .specialized import analyze_specialized
from .chase import evaluate_chase
from .outcomes import review_outcomes
from .migration import review_migration, ACTIVE_STATES
from .references import attach_provenance
from .emergency import analyze_emergency
from .maintenance import review_maintenance
from .health import source_health
from .product_carrying import review_product_carrying
from .observation_time import review_observation_time
from .recent_operations import apply_recent_operation_guard

MUTATING_ACTIONS = {"budget_up", "budget_down", "pause_control", "pause_plan", "resume_plan",
                    "pause_creative", "roi_target_up", "roi_target_down", "bid_down", "bid_restore",
                    "create_chase", "control_budget_up", "one_click_boost", "one_click_control", "review_keyword_reduce", "review_keyword_expand",
                    "review_disable_smart", "review_cold_creative", "refresh_creatives", "replace_creative", "review_duplicate_creatives"}


@_record_cache_scope()
def evaluate_all(snapshots, settings, now=None, events=None, migrations=None, *, recovery_snapshots=None, recovery_events=None):
    snapshots = [review_observation_time(row) for row in snapshots] if isinstance(snapshots, list) else snapshots
    if isinstance(recovery_snapshots, list):
        recovery_snapshots = [review_observation_time(row) for row in recovery_snapshots]
    now=datetime.now(CN) if not isinstance(now,datetime) else now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)
    result = evaluate(snapshots, settings, now)
    result['collection_health'] = source_health(snapshots, settings, now)
    clock_issues = {(s['account_id'], s['entity_id'], s['scope'], s['source_key']): s
                    for s in result['collection_health']['sources'] if not s['clock_current']}
    for entity in result['entities']:
        if (entity['account_id'], entity['entity_id'], entity['scope'], entity['source_key']) in clock_issues:
            if entity['quality'] == 'fresh':
                result['coverage']['quality_counts']['fresh'] -= 1
                result['coverage']['quality_counts']['insufficient'] += 1
                entity['quality'] = 'insufficient'
            entity['issues'].append('最新采集的指标时刻缺失、过期、回退或矛盾，不能选择较旧的有利记录绕过')
    result["monitoring_analysis"] = analyze_monitoring(snapshots,settings,now)
    carrying=review_product_carrying(snapshots,settings,now)
    result['product_carrying']=carrying
    result['alerts'].extend(carrying['alerts']);result['recommendations'].extend(carrying['recommendations'])
    result["alerts"].extend(result["monitoring_analysis"]["alerts"])
    result["recommendations"].extend(result["monitoring_analysis"]["recommendations"])
    emergency=analyze_emergency(snapshots,settings,now)
    result['emergency_analysis']=emergency['observations']
    result['coverage']['emergency']=emergency['coverage']['emergency']
    result['coverage']['emergency_limits']=emergency['coverage']['limits']
    result['alerts'].extend(emergency['alerts'])
    result['recommendations'].extend(emergency['recommendations'])
    specialized = analyze_specialized(snapshots,settings,now)
    result["alerts"].extend(specialized["alerts"])
    result["recommendations"].extend(specialized["recommendations"])
    result["coverage"]["specialized"] = specialized["coverage"]["specialized"]
    result["coverage"]["specialized_limits"] = specialized["coverage"]["limits"]
    chase=evaluate_chase(snapshots,settings,now,events=events)
    result["alerts"].extend(chase["alerts"])
    result["recommendations"].extend(chase["recommendations"])
    result["coverage"]["chase"]=chase["coverage"]["chase"]
    result["coverage"]["chase_limits"]=chase["coverage"]["limits"]
    result["outcome_review"]=review_outcomes(events or [],snapshots,settings,now)
    additional = extend_evaluation(snapshots, settings, result, now)
    recovery_rows=snapshots if recovery_snapshots is None else recovery_snapshots
    recovery_reports=events if recovery_events is None else recovery_events
    recovery_rows=recovery_rows if isinstance(recovery_rows,list) else []
    recovery_reports=recovery_reports if isinstance(recovery_reports,list) else []
    recovery=review_roi_recovery(recovery_rows,settings,now,events=recovery_reports)
    result['coverage']['recovery_history']={'input_snapshots':len(recovery_rows),
        'input_reports':len(recovery_reports or []),'separate_from_regular_28h_window':True}
    result['roi_recovery_reviews']=recovery['observations']
    result['live_exclusion_reviews']=additional['live_exclusion_reviews']
    result['standard_bid_reviews']=additional['standard_bid_reviews']
    additional['recommendations'].extend(recovery['recommendations'])
    result["coverage"]["strategies"] = additional["coverage"]["strategies"]
    result["coverage"]["strategy_limits"] = additional["coverage"]["limits"]
    for field in ("alerts", "recommendations"):
        result[field].extend(additional[field])
        result[field].sort(key=lambda r: (r["priority"], r["account_id"], r["entity_id"], r["rule"]))
    maintenance=review_maintenance(snapshots,settings,now,events=events)
    result['maintenance_review']=maintenance['observations']
    result['recommendations'].extend(maintenance['recommendations'])
    latest_by_id={s.get('id'):s for s in (snapshots if isinstance(snapshots,list) else []) if isinstance(s,dict) and isinstance(s.get('id'),str)}
    def operation_plan_id(entity):
        context=latest_by_id.get(entity.get('latest_snapshot_id'),{}).get('context',{})
        value=context.get('plan_id') if isinstance(context,dict) else None
        return value if isinstance(value,str) and value else entity['entity_id']
    for rec in result['recommendations']:
        if ((rec.get('account_id'), rec.get('entity_id'), rec.get('scope'), rec.get('source_key')) in clock_issues
                and rec.get('action') in MUTATING_ACTIONS):
            rec['eligible'] = False
            rec['blockers'].append('最新采集的指标时刻缺失、过期、回退或矛盾，先核对来源，不沿用旧采集的策略资格')
        plan_id=rec.get('parameters',{}).get('parent_plan_id') or rec.get('target_id',rec.get('entity_id'))
        if any(p['account_id']==rec.get('account_id') and p['plan_id']==plan_id and p['hold_growth'] for p in carrying['plans']):
            if rec.get('action') in {'budget_up','control_budget_up','create_chase','one_click_boost','roi_target_down','resume_plan'} or rec.get('rule')=='roi_recovery_step':
                rec['eligible']=False;rec['blockers'].append('已核对关联的商品库存或当前讲解/映射声明存在风险，先复核承接，不追加放量')
        suppressed=[o for o in result['live_exclusion_reviews'] if o['account_id']==rec.get('account_id')
                    and o['plan_id']==plan_id and o['is_current']
                    and (o['state'] in {'system_paused','not_delivering','status_conflict'} or o['enabled'] is None)]
        if suppressed and rec.get('action') in {'budget_up','control_budget_up','create_chase','one_click_boost','roi_target_down','resume_plan'}:
            rec['eligible']=False
            rec['blockers'].append('当前状态声明为系统暂停、不在投或存在冲突，不能据历史效果放量或自动恢复')
        episodes=[o for o in result['roi_recovery_reviews'] if o['account_id']==rec.get('account_id')
                  and o['entity_id']==plan_id and o['is_current'] and o['active']]
        if episodes and rec.get('rule')!='roi_recovery_step' and rec.get('action') in {
                'roi_target_up','roi_target_down','bid_down','bid_restore','budget_up','create_chase','one_click_boost'}:
            rec['eligible']=False
            rec['blockers'].append('计划正在ROI下调后的恢复复核，先按回调链观察，不能叠加普通放量或调价')
        phase_blockers=lifecycle_blockers(rec.get('action'),rec.get('account_id'),plan_id,result.get('lifecycle_reviews',[]))
        if phase_blockers:
            rec['eligible']=False;rec['blockers'].extend(phase_blockers)
        if rec.get('action') in {'roi_target_up','roi_target_down'}:
            related=[latest_by_id.get(e.get('latest_snapshot_id'),{}) for e in result['entities']
                     if e['account_id']==rec.get('account_id') and e['scope']=='plan'
                     and operation_plan_id(e)==rec.get('target_id',rec.get('entity_id'))]
            if conflicting_roi_sources(rec,related,now,_settings(settings)[0]['max_age_seconds']):
                rec['eligible']=False
                rec['blockers'].append('同计划其他新鲜来源的当前ROI设置或输入范围存在冲突，先核对旧值')
        if rec.get('action')=='pause_plan':
            related=[latest_by_id.get(e.get('latest_snapshot_id'),{}).get('context',{}) for e in result['entities']
                     if e['account_id']==rec.get('account_id') and e['scope']=='plan'
                     and operation_plan_id(e)==rec.get('target_id',rec.get('entity_id'))]
            if any(isinstance(c,dict) and c.get('is_primary_plan') is True for c in related):
                rec['eligible']=False
                rec['blockers'].append('同对象其他来源标为主力计划，先消除角色冲突，不直接获得暂停资格')
        match=[m for m in result['maintenance_review'] if m['account_id']==rec.get('account_id') and m['entity_id']==rec.get('target_id',rec.get('entity_id'))]
        if rec.get('action')=='refresh_creatives' and match and not all(m['state']=='refresh_required' and m['is_current'] for m in match):
            rec['eligible']=False
            rec['blockers'].append('本轮衰退已有补素材报告或前置证据待核对，先按维护阶段复核，不能重复补充')
    result['recommendations'].sort(key=lambda r:(r['priority'],r['account_id'],r['entity_id'],r['rule']))
    # Risk vetoes apply across all sources of the account, before any allocation.
    risk_accounts = {a["account_id"] for a in result["alerts"] if a.get("priority") == "P0"
                     or a.get("rule") in {"refund_spike", "pre_ship_refund_spike", "traffic_quality_decline"}}
    for alert in result['alerts']:
        if alert.get('rule') in MUTEX_RULES:
            risk_accounts.add(alert['account_id'])
            risk_accounts.update(p['account_id'] for p in alert.get('evidence',{}).get('observed_plans',[]) if p.get('account_id'))
    for rec in result["recommendations"]:
        if rec['account_id'] in risk_accounts and rec.get('rule')=='roi_recovery_step':
            rec['eligible']=False;rec['blockers'].append('同账户存在紧急、退款、质量或互斥风险，先处理风险再复核恢复回调')
        if rec["account_id"] in risk_accounts and rec["action"] in {
                "budget_up", "control_budget_up", "create_chase", "one_click_boost", "roi_target_down", "resume_plan"}:
            rec["eligible"] = False
            rec["blockers"].append("同账户存在紧急风险、退款突增、流量质量联合下降或全域互斥冲突，暂停增量放量")
    apply_recent_operation_guard(result, snapshots, settings, now)
    result["budget_migrations"] = [review_migration(p, snapshots, settings, result, events or [], now)
                                   for p in migrations or []]
    reserved = {(p["account_id"], p[leg]["entity_id"]) for p in result["budget_migrations"]
                if p["state"] in ACTIVE_STATES for leg in ("source", "target")}
    for rec in result["recommendations"]:
        if ((rec['account_id'],rec.get('target_id',rec['entity_id'])) in reserved and rec.get('rule')=='roi_recovery_step'):
            rec['eligible']=False;rec['blockers'].append('该计划已有活动预算交接，不能同时回调ROI干扰阶段观察')
        if ((rec["account_id"], rec["entity_id"]) in reserved and rec["action"] in {"budget_up", "budget_down"}
                and not (rec["action"] == "budget_down" and rec["account_id"] in risk_accounts)):
            rec["eligible"] = False
            rec["blockers"].append("对象已有分步预算交接计划，须按阶段复核，不能同时独立调预算")
    # Separate sources can describe the same operation object. They remain
    # separate evidence, but must not create two eligible changes in one round.
    selected = set()
    for rec in result["recommendations"]:
        if not rec["eligible"] or rec["action"] not in MUTATING_ACTIONS:
            continue
        object_id = rec.get("parameters", {}).get("parent_plan_id") or rec.get("target_id") or rec["entity_id"]
        key = (rec["account_id"], object_id)
        if key in selected:
            rec["eligible"] = False
            rec["blockers"].append("同一对象本轮已有优先建议，来源序列不能叠加调控")
        else:
            selected.add(key)
    c = result["coverage"]["quality_counts"]
    eligible = sum(r["eligible"] for r in result["recommendations"])
    if result["entities"]:
        result["summary"] = (f"观察 {len(result['entities'])} 个来源序列；新鲜且具备基本小时证据 {c['fresh']} 个，"
                             f"数据过期 {c['stale']} 个，证据不足 {c['insufficient']} 个。"
                             f"生成 {len(result['alerts'])} 条告警、{len(result['recommendations'])} 条离线建议，"
                             f"其中 {eligible} 条满足已声明的模拟条件。真实执行不可用。")
    return attach_provenance(result)
