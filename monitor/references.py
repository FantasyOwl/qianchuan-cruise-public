"""Attach provenance without promoting user strategy into platform policy."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from .actuation import CONTRACTS
from .live_exclusion import MUTEX_RULES
from .runtime_files import read_asset

RULESET_VERSION = "2026-10-24-smart-control-v1"
_TITLES = {
    "ledger": "总体消耗和调控追加预算", "roi": "支付、净成交与综合 ROI",
    "standard_guarantee": "标准投放成本保障", "control_guarantee": "调控工具保障并不相同",
    "universal_guarantee": "全域成本保障完整条款", "live_targeting": "直播全域的定向边界",
    'live_exclusion':'直播全域与标准投放的互斥边界',
    'material_read':'计划素材查询范围与零消耗证据',
    'material_add':'计划新增素材与程序化配置',
    'material_delete':'素材删除范围与多号商品复用',
    'control_creation':'调控创建的时长、名称和目标条件',
}


def load_catalog():
    try:
        value = json.loads(read_asset(Path(__file__).resolve().parent.parent, 'docs/official-rules.json'))
    except (OSError, ValueError):
        return {}
    rows = {r['title']: r for r in value.get('rules', []) if isinstance(r, dict) and r.get('title')}
    result = {}
    for key, title in _TITLES.items():
        row = rows.get(title, {})
        url = row.get('url', '')
        if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in {
                'school.jinritemai.com', 'open.oceanengine.com', 'qianchuan.jinritemai.com'}:
            continue
        result[key] = {'id':key, 'title':title, 'url':url, 'checked_at':row.get('checked_at',value.get('checked_at')),
                       'verification':row.get('status'), 'source_label':row.get('source_label'),
                       'finding':row.get('finding'), 'limit':row.get('limit'),
                       'application':'资料参考；不证明该对象具备成本保障、权限或执行资格'}
    for key, title in (('budget','乘方/全域计划预算写合同'),('status','乘方/全域计划启停写合同'),
                       ('roi','乘方/全域控成本计划ROI目标写合同'),
                       ('control_budget','全域调控任务预算写合同'),('control_status','全域调控任务状态写合同'),
                       ('control_create','直播全域一键起量创建请求合同'),
                       ('smart_control_create','直播全域一键控量创建请求合同'),
                       ('smart_control_stop','按母计划停止一键控量请求合同')):
        result['contract_'+key] = {'id':'contract_'+key,'title':title,'url':CONTRACTS[key]['source'],
            'checked_at':'2026-10-24' if key.startswith('smart_control') else '2026-10-22' if key in {'budget','status'} else '2026-10-23','verification':'公开请求合同已核验；业务提交未验证',
            'application':'只证明请求定义；不证明当前账户权限、保障资格或已发生操作'}
    return result


CATALOG = load_catalog()


def _section(rule):
    if rule.startswith('product_'):return '五、商品承接、库存与讲解匹配；七、异常告警'
    if rule in MUTEX_RULES:return '四、计划启停与互斥；二、来源状态校验'
    if rule=='decline_maintenance':return '四、计划启停与生命周期；六、衰退期应对'
    if any(word in rule for word in ('chase','crowding','rechase','control_roi','control_spend','one_click')):
        return '三、追投专项监控与调控'
    if any(word in rule for word in ('creative','keyword','smart','cold','duplicate')):
        return '六、图文/短视频专项调控；七、异常告警'
    if any(word in rule for word in ('refund','stock','funnel','traffic')):
        return '五、大屏分析与实时决策'
    if any(word in rule for word in ('budget','roi','bid','cost','zero_orders','spend')):
        return '四、计划预算/出价调控；七、异常告警'
    return '二、监测与数据校验；七、异常告警'


def provenance(item, context=None):
    context = context or {}
    action = item.get('action','')
    rule = item.get('rule','')
    kind = item.get('rule_basis','user_strategy')
    ids = ['roi']
    if rule in MUTEX_RULES:ids.append('live_exclusion')
    risks = [item['risk']] if item.get('risk') else []
    if action in {'pause_creative','replace_creative','refresh_creatives','review_cold_creative','review_duplicate_creatives'} or rule=='creative_zero_24h':
        ids.extend(['material_read','material_add','material_delete'])
        risks.append('删除素材不等同暂停创意；自定义创意、程序化配置、自提/自选和智选范围分别核验。自动替换执行链尚未接通。')
    if context.get('scope') in {'plan','creative','control'}:
        ids.append('ledger')
    promotion = context.get('promotion_type')
    if promotion == 'standard':
        ids.append('standard_guarantee')
    elif promotion in {'universal','chengfang'}:
        ids.append('universal_guarantee')
    if context.get('scope') == 'control' or action in {'create_chase','control_budget_up','pause_control','one_click_boost','one_click_control'}:
        ids.append('control_guarantee')
        risks.append('调控工具的目标、预算和成本保障分别核验，不能将其他工具合同或保障条件套用到当前任务。')
    if context.get('scene') == 'live' and promotion == 'universal':
        ids.append('live_targeting')
        risks.append('直播全域定向能力需按当前产品核对，不能直接套用标准计划的人群包或达人定向方案。')
    if context.get('scope') == 'plan' and promotion in {'universal','chengfang'}:
        if action in {'budget_up','budget_down'}:
            ids.append('contract_budget')
        elif action in {'pause_plan','resume_plan'}:
            ids.append('contract_status')
        elif action in {'roi_target_up','roi_target_down'}:
            ids.append('contract_roi')
            risks.append('ROI写合同仅用于控成本计划并区分成交与净成交优化目标，最多两位小数；标准计划与综合营销口径不能直接套用。')
    if context.get('scope') == 'control' and promotion == 'universal':
        if action == 'control_budget_up':ids.append('contract_control_budget')
        elif action == 'pause_control':ids.append('contract_control_status')
    if action == 'one_click_boost':
        ids.extend(['control_creation','contract_control_create'])
        risks.append('用户策略提示：一键起量消耗不支持成本保障，盈亏自负；当前具体工具的适用官方条款仍需核验。')
        risks.append('当前只核验直播全域单号控成本母计划的请求预览；创建回执不证明可投，真实执行链路尚未接入。')
    if action=='create_chase':
        ids.extend(['control_creation','contract_control_create'])
        risks.append('直播控成本素材追投不支持设置调控时长、默认长期有效；可靠截止与独立读回未接通，不能静默丢弃2至4小时策略。')
    if action=='one_click_control':
        ids.extend(['contract_smart_control_create','contract_smart_control_stop'])
        risks.append('控量原生创建不接收时长；30至60分钟本地截止未接通。停止按母计划ad_id执行，回执不证明已停止或可释放占用。')
    if action in {'create_chase','roi_target_up','roi_target_down','bid_down','bid_restore','pause_control','control_budget_up','one_click_control'}:
        risks.append('该动作的适用写合同或执行链路尚未接入，不能由语义建议直接提交到账户。')
    official = [copy.deepcopy(CATALOG[k]) for k in dict.fromkeys(ids) if k in CATALOG]
    return {'ruleset_version':RULESET_VERSION,
            'strategy':{'authority':kind,'source':'用户目标文件 goal-objective.md' if kind=='user_strategy' else '本地观察/数据校验逻辑',
                        'section':_section(rule),'rule_id':rule,
                        'note':'数值阈值是用户策略或本地保护线，不能作为官方产品上限或赔付承诺'},
            'official_references':official,
            'unverified':['当前具体计划的成本保障资格和账户API权限没有由本程序认证'],
            'risk_notes':list(dict.fromkeys(risks))}


def attach_provenance(evaluation):
    entities = evaluation.get('entities',[])
    for item in evaluation.get('alerts',[])+evaluation.get('recommendations',[]):
        candidates = [e for e in entities if e['account_id']==item.get('account_id') and e['entity_id']==item.get('entity_id')
                      and (not item.get('scope') or e['scope']==item['scope'])
                      and (not item.get('source_key') or e['source_key']==item['source_key'])]
        contexts = {(e.get('scope'),e.get('scene'),e.get('promotion_type')) for e in candidates}
        context = dict(zip(('scope','scene','promotion_type'),next(iter(contexts)))) if len(contexts)==1 else {}
        item['policy_basis'] = provenance(item,context)
    return evaluation
