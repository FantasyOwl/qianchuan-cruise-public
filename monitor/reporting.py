"""Account-scoped review reports. No AI, network, credentials or account writes."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections import Counter

from .catalog import coverage_report
from .references import RULESET_VERSION, provenance
from .schema import now_cn
from .official_reports import official_evidence
from .live_exclusion import MUTEX_RULES

BASIS_NAMES = {'overall_payment':'整体支付ROI','net':'净成交ROI','marketing':'综合营销ROI',
               'settled_7d':'7日结算ROI','control_payment':'调控支付ROI','unknown':'口径未确认'}
PRIORITIES = {'P0':0,'P1':1,'P2':2}


def _scoped_evidence(value,account_id):
    value=copy.deepcopy(value)
    if isinstance(value,dict) and isinstance(value.get('observed_plans'),list):
        plans=value['observed_plans'];own=[p for p in plans if p.get('account_id')==account_id]
        value['observed_plans']=own;value['other_account_conflicting_plan_count']=len(plans)-len(own)
    return value


def build_report(snapshots, settings, evaluation, account_id, now=None):
    now = now or now_cn()
    account = next((a for a in settings.get('accounts',[]) if a.get('id')==account_id),None)
    if account is None:
        raise ValueError('报告账户不在观察白名单内')
    entities = [e for e in evaluation.get('entities',[]) if e.get('account_id')==account_id]
    by_id = {s['id']:s for s in snapshots if s.get('account_id')==account_id}
    sources = []
    for e in entities:
        snapshot = by_id.get(e.get('latest_snapshot_id'),{})
        src = snapshot.get('source',e.get('source',{}))
        sources.append({'snapshot_id':e.get('latest_snapshot_id'),'account_id':account_id,'entity_id':e['entity_id'],
            'scope':e['scope'],'display_name':snapshot.get('display_name'),'metric_basis':e['metric_basis'],
            'scene':e.get('scene'),'promotion_type':e.get('promotion_type'),'evaluation_source_key':e['source_key'],
            'series_key':src.get('series_key'),'source_kind':src.get('kind'),'dataset_id':src.get('dataset_id'),
            'data_at':src.get('data_at'),'captured_at':src.get('captured_at'),'metric_date':src.get('metric_date'),
            'metric_period':src.get('metric_period','day'),'period_start':src.get('period_start'),'coverage':src.get('coverage'),
            'official_report':official_evidence(snapshot),
            'strategy_evidence_status':e.get('quality'),'issues':copy.deepcopy(e.get('issues',[])),
            'complete_hour_windows':sum(w.get('complete') is True for w in e.get('hourly',[]))})
    alerts = []
    for original in evaluation.get('alerts',[]):
        related = original.get('evidence',{}).get('observed_plans',[]) if original.get('rule') in MUTEX_RULES else []
        own_plans = [p for p in related if p.get('account_id')==account_id]
        if original.get('account_id')!=account_id and not own_plans:
            continue
        alert = copy.deepcopy(original)
        if related:
            if original.get('account_id')!=account_id:
                alert.pop('source_key',None)
            alert['account_id']=account_id
            alert['entity_id']=own_plans[0]['plan_id'] if own_plans else alert.get('entity_id')
            alert['evidence']['observed_plans']=copy.deepcopy(own_plans)
            alert['evidence']['other_account_conflicting_plan_count']=len(related)-len(own_plans)
            alert['scope_note']='关联互斥告警；其他账户仅保留冲突数量，不导出其计划和账户标识'
        alerts.append(alert)
    # Unassigned validation failures cannot be attributed to this account.
    notices = [a.get('title') for a in evaluation.get('alerts',[]) if not a.get('account_id')]
    alerts.sort(key=lambda a:(PRIORITIES.get(a.get('priority'),9),a.get('entity_id',''),a.get('rule','')))
    actions = []
    for r in evaluation.get('recommendations',[]):
        if r.get('account_id')!=account_id:
            continue
        evidence_sources = [s for s in sources if s['entity_id']==r['entity_id'] and s['scope']==r['scope']
                            and s['evaluation_source_key']==r.get('source_key')]
        basis = copy.deepcopy(r.get('policy_basis') or provenance(r,evidence_sources[0] if len(evidence_sources)==1 else {}))
        actions.append({'id':r['id'],'priority':r['priority'],'rule':r['rule'],
            'object':{'account_id':account_id,'evidence_entity_id':r['entity_id'],'scope':r['scope'],
                      'target_type':r.get('target_type',r['scope']),'target_id':r.get('target_id',r['entity_id']),
                      'source_key':r.get('source_key'),'metric_basis':r.get('metric_basis')},
            'action':{'type':r['action'],'label':r.get('action_label',r['action']),
                      'before':r.get('before'),'after':r.get('after'),'change_pct':r.get('change_pct'),
                      'parameters':copy.deepcopy(r.get('parameters',{}))},
            'reason':r.get('reason'),'expected_goal':r.get('expected_goal'),
            'offline_eligible':r.get('eligible') is True,'execution_eligible':False,'executed':False,
            'blockers':copy.deepcopy(r.get('blockers',[])),'evidence':_scoped_evidence(r.get('evidence',{}),account_id),
            'source_snapshot_ids':[s['snapshot_id'] for s in evidence_sources],
            'source_resolution':'exact' if len(evidence_sources)==1 else 'missing_or_ambiguous',
            'policy_basis':basis,'risks':basis['risk_notes']})
    actions.sort(key=lambda a:(PRIORITIES.get(a['priority'],9),a['object']['target_id'],a['rule']))
    status_counts = Counter(e.get('quality','unknown') for e in entities)
    priorities = Counter(a.get('priority') for a in alerts)
    conclusions = [f'观察到{len(entities)}个独立来源序列；具备基本策略证据{status_counts["fresh"]}个，过期{status_counts["stale"]}个，证据不足{status_counts["insufficient"]}个。']
    if account.get('target_roi') is None:
        conclusions.append(f'{BASIS_NAMES.get(account.get("roi_basis"),"口径未确认")}目标仍为空，依赖ROI目标的数值调控保持阻断。')
    if priorities['P0']:
        conclusions.append(f'存在{priorities["P0"]}条P0告警，需先核验紧急风险；离线增量放量建议受统一否决。')
    elif not entities or not status_counts['fresh']:
        conclusions.append('缺少足够的当前策略证据，不能由没有投放告警判定账户健康。')
    else:
        conclusions.append(f'生成{len(alerts)}条告警和{len(actions)}条离线建议；当前程序不提交账户操作。')
    coverage = coverage_report(snapshots,settings,now,analysis=evaluation.get('monitoring_analysis',{}))
    inventory = next((a for a in coverage['accounts'] if a['account_id']==account_id),{})
    missing = [{'group':m['group'],'label':m['label'],'status':m['data_status']} for m in inventory.get('metrics',[]) if m['data_status']!='fresh']
    reviews = [copy.deepcopy(i) for i in evaluation.get('outcome_review',{}).get('items',[]) if i.get('account_id')==account_id]
    migrations = [copy.deepcopy(p) for p in evaluation.get('budget_migrations',[]) if p.get('account_id')==account_id]
    report = {'schema_version':1,'generated_at':now.isoformat(),'ruleset_version':RULESET_VERSION,'mode':'offline_review',
        'account':{k:account.get(k) for k in ('id','name','scene','roi_basis','target_roi')},
        'core_conclusions':conclusions,'prioritized_actions':actions,
        'evidence_and_risks':{'alerts':alerts,'sources':sources,'metric_coverage_counts':inventory.get('counts',{}),
            'product_carrying':{key:[copy.deepcopy(o) for o in evaluation.get('product_carrying',{}).get(key,[]) if o['account_id']==account_id]
                for key in ('inventory','plans')},
            'collection_health': {**{k:v for k,v in evaluation.get('collection_health',{}).items() if k not in {'sources','accounts'}},
                'accounts':[copy.deepcopy(o) for o in evaluation.get('collection_health',{}).get('accounts',[]) if o['account_id']==account_id],
                'sources':[copy.deepcopy(o) for o in evaluation.get('collection_health',{}).get('sources',[]) if o['account_id']==account_id]},
            'lifecycle_reviews':[copy.deepcopy(o) for o in evaluation.get('lifecycle_reviews',[]) if o.get('account_id')==account_id],
            'roi_recovery_reviews':[copy.deepcopy(o) for o in evaluation.get('roi_recovery_reviews',[]) if o.get('account_id')==account_id],
            'live_exclusion_reviews':[copy.deepcopy(o) for o in evaluation.get('live_exclusion_reviews',[]) if o.get('account_id')==account_id],
            'emergency_observations':[copy.deepcopy(o) for o in evaluation.get('emergency_analysis',[]) if o.get('account_id')==account_id],
            'maintenance_reviews':[copy.deepcopy(o) for o in evaluation.get('maintenance_review',[]) if o.get('account_id')==account_id],
            'observed_plan_operations':[copy.deepcopy(o) for o in evaluation.get('observed_plan_operations',[]) if o.get('account_id')==account_id],
            'spend_observations':[{**{k:copy.deepcopy(o.get(k)) for k in ('entity_id','scope','snapshot_id','source_key','metric_basis','metric_period','data_at','captured_at','data_status','data_status_reason')},
                                   'trend':copy.deepcopy(o['spend_trend'])}
                for o in evaluation.get('monitoring_analysis',{}).get('entities',[]) if o.get('account_id')==account_id and o.get('spend_trend')],
            'creative_spend_shares':[{'entity_id':o['entity_id'],'snapshot_id':o['snapshot_id'],'source_key':o['source_key'],
                                      **copy.deepcopy(o['creative_spend_share'])}
                for o in evaluation.get('monitoring_analysis',{}).get('entities',[]) if o.get('account_id')==account_id and o.get('creative_spend_share')],
            'traffic_quality_observations':[{**{k:copy.deepcopy(o.get(k)) for k in ('entity_id','scope','snapshot_id','source_key','metric_period','data_at','data_status')},
                                            'quality':copy.deepcopy(o['traffic']['quality'])}
                for o in evaluation.get('monitoring_analysis',{}).get('entities',[]) if o.get('account_id')==account_id and o.get('traffic',{}).get('quality')],
            'metrics_not_currently_verified':missing,'outcome_reviews':reviews,'budget_migrations':migrations,
            'unassigned_data_notices':notices,
            'risks':['所有动作仅供离线复核；eligible不是账户执行许可。','采集时间不能替代数据更新时间，日累计与本场累计不能混拼。',
                     '人工报告不等于平台认证；观察变化不能证明因果或利润。','缺少全量连续输入，不能证明独立7×24监测或完整目标已经完成。']},
        'execution_available':False,'executed':False,
        'synthetic':bool(sources) and all(s['source_kind']=='demo' for s in sources),
        'data_mode':'empty' if not sources else 'synthetic_only' if all(s['source_kind']=='demo' for s in sources)
                    else 'mixed_evidence' if any(s['source_kind']=='demo' for s in sources) else 'observations',
        'evidence_summary':{'source_count':len(sources),'source_states':dict(status_counts),
                            'alerts_by_priority':dict(priorities),'offline_eligible_actions':sum(a['offline_eligible'] for a in actions),
                            'real_executions_by_this_program':0}}
    report['id'] = hashlib.sha256(json.dumps(report,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()[:24]
    return report


def _inline(value):
    value = ' '.join(str(value if value is not None else '未采集').splitlines())
    return re.sub(r'([\\`*_{}\[\]()#+.!|\-])',r'\\\1',html.escape(value,quote=False))


def _json_block(value):
    text = json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    longest = max((len(m.group(0)) for m in re.finditer(r'`+',text)),default=0)
    fence = '`'*max(3,longest+1)
    return f'{fence}json\n{text}\n{fence}'


def report_markdown(report):
    a = report['account']
    lines = ['# 千川账户复核报告',f'账户：{_inline(a.get("name") or a["id"])}（{_inline(a["id"])}）',
             f'生成时间：{_inline(report["generated_at"])}；报告编号：{report["id"]}',
             '模式：'+{'empty':'暂无来源','synthetic_only':'合成数据回放','mixed_evidence':'合成与观察混合证据','observations':'只读离线复核'}[report['data_mode']]+'，未提交账户操作。',
             '','## 核心结论','']
    lines += ['- '+_inline(c) for c in report['core_conclusions']]
    lines += ['','## 分级行动指令','']
    if not report['prioritized_actions']:
        lines.append('没有足够证据生成行动建议；不表示账户健康。')
    for item in report['prioritized_actions']:
        obj, action = item['object'],item['action']
        percent = f'{action["change_pct"]*100:g}%' if action['change_pct'] is not None else '无数值幅度'
        status=action['type'] in {'pause_plan','resume_plan','pause_control','pause_creative'}
        value=lambda v: ('开启' if v is True else '关闭' if v is False else '未确认') if status else _inline(v)
        lines += [f'### {_inline(item["priority"])} · {_inline(action["label"])}','',
                  f'操作对象：{_inline(obj["target_type"])} / {_inline(obj["target_id"])}；证据对象：{_inline(obj["scope"])} / {_inline(obj["evidence_entity_id"])}。',
                  f'口径：{_inline(BASIS_NAMES.get(obj["metric_basis"],obj["metric_basis"]))}。',
                  f'原因：{_inline(item["reason"])}。预期目标：{_inline(item["expected_goal"])}。',
                  f'前后值：{value(action["before"])} → {value(action["after"])}；调整幅度：{_inline(percent)}。',
                  '操作参数：',_json_block(action['parameters']),
                  '离线条件：'+('满足已声明的模拟条件；真实执行不可用。' if item['offline_eligible'] else '阻断：'+_inline('；'.join(item['blockers']))),
                  f'策略依据：{_inline(item["policy_basis"]["strategy"]["source"])} / {_inline(item["policy_basis"]["strategy"]["section"])}。']
        for ref in item['policy_basis']['official_references']:
            lines.append(f'- 资料参考：[{_inline(ref["title"])}]({ref["url"]})；来源 {_inline(ref.get("source_label") or ref["title"])}；核验日期 {_inline(ref["checked_at"])}；{_inline(ref["verification"])}。{_inline(ref["application"])}。{_inline(ref.get("limit") or "按适用产品与当前权限另行核对")}。')
        lines += ['风险：'+_inline('；'.join(item['risks'])),'来源快照：'+_inline('、'.join(item['source_snapshot_ids']) or '缺失或不唯一'),'']
    evidence = report['evidence_and_risks']
    lines += ['## 数据佐证与风险提示','',f'资料/规则版本：{_inline(report["ruleset_version"])}（本地版本，不是平台政策版本）。','']
    lines += ['- '+_inline(r) for r in evidence['risks']]
    for title, value in (('分级告警',evidence['alerts']),('独立来源与数据时间',evidence['sources']),
                         ('消耗分时独立观察',evidence.get('spend_observations',[])),
                         ('单素材消耗占比',evidence.get('creative_spend_shares',[])),
                         ('商品库存与讲解承接',evidence.get('product_carrying',{})),
                         ('持续采集与指标时效',evidence.get('collection_health',{})),
                         ('后台监测轮次记录',evidence.get('monitor_runtime',{})),
                         ('生命周期分层复核',evidence.get('lifecycle_reviews',[])),
                         ('ROI下调后的恢复复核',evidence.get('roi_recovery_reviews',[])),
                         ('直播全域与标准互斥复核',evidence.get('live_exclusion_reviews',[])),
                         ('应急观测与评分',evidence.get('emergency_observations',[])),
                         ('流量质量配对窗口',evidence.get('traffic_quality_observations',[])),
                         ('衰退维护顺序复核',evidence.get('maintenance_reviews',[])),
                         ('已观察的计划操作日志',evidence.get('observed_plan_operations',[])),
                         ('逐条建议的数据佐证',[{'id':i['id'],'evidence':i['evidence']} for i in report['prioritized_actions']]),
                         ('尚未取得当前有效证据的指标',evidence['metrics_not_currently_verified']),
                         ('调控后的观察复核',evidence['outcome_reviews']),('预算分步交接',evidence['budget_migrations']),
                         ('未能归属账户的数据问题',evidence['unassigned_data_notices'])):
        lines += ['',f'### {title}','',_json_block(value)]
    return '\n'.join(lines)+'\n'
