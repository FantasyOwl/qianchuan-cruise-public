"""Live-universal single-video cost-chase contract preview. No IO or ledger.

This compiles reviewed inputs after current full evaluation; it never enables
creation. Local deadline and native material provenance remain execution gates.
"""
import hashlib
import json
from decimal import Decimal

from .actuation import amount
from .control_creation import CREATE_SOURCE,PURCHASE
from .official_reports import _document,_guard
from .plan_readback import _clock,_id
from .schema import now_cn

FORMAT='qianchuan_material_chase_creation_v1'
VERSION='2026-10-23-live-single-video-cost-chase-v1'
BEHAVIORS={'COMMENTED_USER','FOLLOWED_USER','GOODS_CARTS_CLICK','GOODS_CARTS_ORDER','LIKED_USER','LIVE_COMMENT',
    'LIVE_EFFECTIVE_WATCH','LIVE_EXCEPTIONAL','LIVE_GOODS_CLICK','LIVE_GOODS_ORDER','LIVE_WATCH','SHARED_USER'}


def _exact(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys):raise ValueError('追投创建登记缺字段或包含未知设置')


def _text(value):
    if not isinstance(value,str) or not 1<=len(value)<=500 or any(ord(c)<32 for c in value):raise ValueError('视频原生标识无效')
    return value


def _ratio(value):
    if type(value) not in {int,float,Decimal}:raise ValueError('账户考核ROI须为明确的正数，不能借任务出价填充')
    number=Decimal(str(value))
    if not number.is_finite() or number<=0:raise ValueError('账户考核ROI须为明确的正数')
    return number


def _audience(value):
    if not isinstance(value,dict):raise ValueError('达人定向须为明确对象')
    fields={'aweme_fan_accounts','aweme_fan_behaviors','aweme_fan_behaviors_days'}
    if set(value)-fields or not {'aweme_fan_accounts','aweme_fan_behaviors'}<=set(value):
        raise ValueError('当前只核验明确达人ID及互动行为的定向组合，不接收未知人群字段')
    accounts,behaviors=value['aweme_fan_accounts'],value['aweme_fan_behaviors']
    if not isinstance(accounts,list) or not 1<=len(accounts)<=20:raise ValueError('本地预览只接收1至20个明确达人ID，此上限非平台配额')
    ids=[_id(v) for v in accounts]
    if len(ids)!=len(set(ids)):raise ValueError('达人定向ID不能重复')
    if not isinstance(behaviors,list) or not behaviors or any(not isinstance(v,str) or v not in BEHAVIORS for v in behaviors) or len(behaviors)!=len(set(behaviors)):
        raise ValueError('达人互动行为缺失、重复或不在当前已核验枚举内')
    if behaviors==['FOLLOWED_USER']:
        if 'aweme_fan_behaviors_days' in value:raise ValueError('仅关注行为时，官方不支持设置互动天数')
    elif value.get('aweme_fan_behaviors_days') not in {'DAYS_15','DAYS_30','DAYS_60'}:
        raise ValueError('达人互动天数须为当前已核验的15、30或60天枚举')
    return {**value,'aweme_fan_accounts':[int(v) for v in ids]}


def _latest(evaluation,rows,account,entity,scope,series):
    matches=[e for e in evaluation.get('entities',[]) if e.get('account_id')==account and e.get('entity_id')==entity
        and e.get('scope')==scope and e.get('source',{}).get('series_key')==series]
    if len(matches)!=1:raise ValueError('素材、母计划或账户必须有明确且唯一的当前来源关系')
    selected=[r for r in rows if r.get('id')==matches[0].get('latest_snapshot_id')]
    if len(selected)!=1:raise ValueError('当前来源快照ID缺失或重复')
    clocks=[h for h in evaluation.get('collection_health',{}).get('sources',[]) if h.get('snapshot_id')==selected[0]['id']]
    if len(clocks)!=1 or clocks[0].get('clock_current') is not True:raise ValueError('最新来源时间不足，不能挑选较旧有利快照')
    return selected[0]


def _compile(payload,rows,cfg,evaluation,now,synthetic):
    aid,pid,mid=map(_id,(payload['account_id'],payload['parent_plan_id'],payload['material']['material_id']))
    candidates=[p for p in evaluation.get('recommendations',[]) if p.get('id')==payload['proposal_id']
        and p.get('account_id')==aid and p.get('action')=='create_chase' and p.get('scope')=='creative' and p.get('eligible') is True]
    if len(candidates)!=1:raise ValueError('当前完整评估没有相同的合格素材追投建议')
    proposal=candidates[0]
    latest=[r for r in rows if r.get('id')==payload['creative_snapshot_id']]
    if len(latest)!=1:raise ValueError('没有登记对应的唯一素材快照')
    creative=latest[0];ctx=creative.get('context',{})
    selected=_latest(evaluation,rows,aid,proposal['entity_id'],'creative',creative.get('source',{}).get('series_key'))
    if selected!=creative or creative.get('entity_id')!=proposal['entity_id'] or proposal.get('target_id')!=creative['entity_id']:
        raise ValueError('追投建议、目标与当前素材来源不一致')
    if ctx.get('parent_plan_id')!=pid or ctx.get('material_id')!=mid or ctx.get('creative_parent_verified') is not True:
        raise ValueError('素材原生ID及母计划映射尚未明确核对，不能用计划或视频ID代替素材ID')
    parent=_latest(evaluation,rows,aid,pid,'plan',ctx.get('parent_source_key'))
    if parent['id']!=payload['parent_snapshot_id']:raise ValueError('登记的母计划不是当前最新快照')
    account=_latest(evaluation,rows,aid,aid,'account',ctx.get('account_source_key'))
    kind='demo' if synthetic else 'import'
    for row in (creative,parent,account):
        source=row.get('source',{})
        if (row.get('scene')!='live' or row.get('promotion_type')!='universal' or source.get('kind')!=kind
            or source.get('coverage')!='report' or source.get('metric_period')!='day' or source.get('metric_date')!=now.date().isoformat()
            or row.get('quality',{}).get('issues') or row.get('scope') in {'plan','creative'} and row.get('control',{}).get('running') is not True):
            raise ValueError('当前仅核验同类报告来源、在投直播全域的明确素材／母计划／账户')
    if len({r['metric_basis'] for r in (creative,parent,account)})!=1:raise ValueError('素材、账户和母计划考核口径不能混用')
    basis=creative['metric_basis'];accounts=[a for a in cfg.get('accounts',[]) if a.get('id')==aid and a.get('observe',True)]
    if len(accounts)!=1 or accounts[0].get('roi_basis')!=basis or basis not in {'overall_payment','net'}:
        raise ValueError('当前账户考核口径未明确匹配')
    target=_ratio(accounts[0].get('target_roi'));params=proposal['parameters']
    _exact(params,{'budget','duration_hours','mode','target_roi','parent_plan_id','differentiated_targeting_verified'})
    if (params['parent_plan_id']!=pid or params['mode']!='cost_control' or params['differentiated_targeting_verified'] is not True
        or _ratio(params['target_roi'])!=target):raise ValueError('追投意图、账户目标或控成本模式不一致')
    budget,duration,bid=map(amount,(params['budget'],params['duration_hours'],payload['conversion_bid']))
    if not Decimal(100)<=budget<=Decimal(500) or not Decimal(2)<=duration<=Decimal(4) or duration%Decimal('.5'):
        raise ValueError('本地素材追投测试须保持100至500元、2至4小时策略')
    if not Decimal('.01')<=bid<=Decimal(10000) or budget<=bid*5:
        raise ValueError('直播间成交出价须0.01至10000元，本地测试预算还需高于其5倍')
    pctx=parent.get('context',{});pm=parent.get('metrics',{})
    goal={'overall_payment':'AD_CONVERT_TYPE_LIVE_PAY_ROI','net':'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI'}[basis]
    if (pctx.get('manual_reviewed') is not True or pctx.get('plan_delivery_mode')!='cost_control'
        or pctx.get('plan_multi_aweme') is not False or pctx.get('plan_optimization_goal')!=goal
        or pctx.get('plan_roi_config_verified') is not True):raise ValueError('母计划须为已核对的单号控成本ROI目标')
    if abs((_clock(pctx.get('plan_roi_observed_at'))-_clock(parent['source']['captured_at'])).total_seconds())>1:
        raise ValueError('母计划设置声明与当前快照截点不一致')
    if pctx.get('parent_control_inventory_verified') is not True or pctx.get('creative_inventory_verified') is not True:
        raise ValueError('母计划追投清单与加热素材清单尚未明确核对')
    if abs((_clock(pctx.get('creative_inventory_at'))-_clock(parent['source']['captured_at'])).total_seconds())>1:
        raise ValueError('母计划追投／素材清单截点不一致')
    total=pm.get('parent_chase_budget_total');heated=pm.get('active_heated_creatives')
    if type(total) not in {int,float,Decimal} or not Decimal(str(total)).is_finite() or Decimal(str(total))<0:raise ValueError('缺少当前追投预算合计，不能默认0')
    if type(heated) is not int or not 0<=heated<20:raise ValueError('用户同时加热20个素材的策略空间不足或未知')
    parent_budget=amount(pm.get('budget'))
    if (budget>parent_budget or budget+Decimal(str(total))>parent_budget*Decimal('.3')
        or amount(creative.get('metrics',{}).get('parent_budget'))!=parent_budget
        or amount(proposal.get('evidence',{}).get('parent_budget'))!=parent_budget):
        raise ValueError('当前母计划预算及追投预算合计不符合30%用户策略')
    for row in (parent,account):
        if abs((_clock(row['source']['data_at'])-_clock(creative['source']['data_at'])).total_seconds())>300:
            raise ValueError('关联素材、母计划和账户数据时点须相差不超过5分钟')
    material=payload['material'];_exact(material,{'material_id','native','snapshot_id','material_type','material_status','audit_status','is_delete','mapping_verified','observed_at'})
    if (material['snapshot_id']!=creative['id'] or material['material_type']!='VIDEO' or material['material_status']!='DELIVERY_OK'
        or material['audit_status']!='PASS' or material['is_delete'] is not False or material['mapping_verified'] is not True):
        raise ValueError('当前只预览明确映射、审核通过且未删除的可投视频素材')
    native=material['native']
    if not isinstance(native,dict) or len(native)!=1 or not set(native)<={'video_id','aweme_item_id'}:raise ValueError('须明确单个素材库视频或抖音视频标识')
    if 'aweme_item_id' in native:_id(native['aweme_item_id'])
    else:_text(native['video_id'])
    if not _clock(material['observed_at'])<=now or abs((_clock(material['observed_at'])-_clock(creative['source']['captured_at'])).total_seconds())>1:
        raise ValueError('素材登记与当前快照截点不一致')
    review=payload['targeting_review'];_exact(review,{'reviewed','observed_at','parent_snapshot_id','method'})
    if (review['reviewed'] is not True or review['parent_snapshot_id']!=parent['id'] or review['method']!='reviewed_fan_audience'
        or ctx.get('differentiated_targeting_verified') is not True
        or abs((_clock(review['observed_at'])-_clock(creative['source']['captured_at'])).total_seconds())>1):
        raise ValueError('实际达人定向与母计划差异化复核尚未对应当前来源')
    audience=_audience(payload['audience'])
    if any(a.get('priority')=='P0' and a.get('account_id')==aid for a in evaluation.get('alerts',[])):raise ValueError('账户存在P0风险，不准备常规追投')
    if any(p.get('eligible') is True and p.get('account_id')==aid and p.get('entity_id')==pid
        and p.get('action') in {'budget_up','budget_down','pause_plan','roi_target_up','roi_target_down','one_click_boost'} for p in evaluation.get('recommendations',[])):
        raise ValueError('当前母计划另有合格调控建议，不能叠加追投')
    body=('{"advertiser_id":'+aid+',"ad_id":'+pid+',"scene":"MATERIAL_ADD_BUDGET","smart_bid_type":"SMART_BID_CUSTOM",'
        '"budget":'+format(budget,'.2f')+',"material_ids":['+mid+'],"external_action":"'+PURCHASE+'","bid":'+format(bid,'.2f')+
        ',"material_type":"VIDEO","audience":'+json.dumps(audience,separators=(',',':'),ensure_ascii=False)+'}')
    body_digest=hashlib.sha256(body.encode()).hexdigest()
    qualification={'payload':payload,'account':accounts[0],'rules':cfg.get('rules',{}),
        'sources':[r for r in rows if r.get('account_id')==aid and (r.get('scope'),r.get('entity_id')) in
            {('creative',creative['entity_id']),('plan',pid),('account',aid)}]}
    qualification_digest=hashlib.sha256(json.dumps(qualification,ensure_ascii=True,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()
    return {'account_id':aid,'parent_plan_id':pid,'material_id':mid,'creative_snapshot_id':creative['id'],
        'qualification_digest':qualification_digest,
        'parent_snapshot_id':parent['id'],'proposal_id':proposal['id'],'body_json':body,'body_digest':body_digest,
        'budget':format(budget,'.2f'),'conversion_bid':format(bid,'.2f'),'requested_local_duration_hours':float(duration),
        'account_assessment_target':format(target,'f'),'native_optimization':'live_purchase_conversion_cost',
        'duration_sent_to_api':False,'roi_target_sent_to_api':False,
        'deadline_reservation':{'account_id':aid,'parent_plan_id':pid,'material_id':mid,'control_type':'material_chase',
            'duration_hours':float(duration),'request_digest':body_digest,'reserve_before_creation':True,
            'binding_requires_native_receipt':True,'registered':False}}


def preview_material_chase(payload,rows,settings,evaluation,now=None,*,synthetic=False):
    payload=_document(payload);_guard(payload)
    _exact(payload,{'format','account_id','proposal_id','creative_snapshot_id','parent_plan_id','parent_snapshot_id','conversion_bid','material','audience','targeting_review'})
    if payload['format']!=FORMAT:raise ValueError('追投创建登记格式不符')
    now=_clock(now or now_cn());result={'format':FORMAT,'contract_version':VERSION,'prepared':False,'draft':None,'blockers':[],
        'synthetic':synthetic,'executed':False,'persisted':False,'ai_sent':False,'native_url':None,
        'native_submission_ready':False,'live_execution_allowed':False,'deadline_registered':False,'official_source':CREATE_SOURCE,
        'execution_blockers':['原生素材来源和用途尚未认证；登记文件不产生执行资格。',
            '连续截止调度与完整追投创建客户端未接通；创建前必须持久预留并独立核对任务。'],
        'limits':['只准备当前直播全域单号、单视频、控成本直播间成交及已复核达人定向分支。',
            '账户支付ROI是准入考核，不被偷偷复制成任务ROI或出价；实际转化出价须另行复核。',
            '2至4小时保留为本地截止要求，不向不支持时长的接口发送duration，也不保证真实到期停止。',
            '20个并发加热上限为用户策略；当前接口单次最多20素材不是同一个限制。']}
    try:result['draft']=_compile(payload,rows,settings,evaluation,now,synthetic)
    except (ValueError,TypeError,KeyError) as error:
        result['blockers'].append(str(error) if isinstance(error,ValueError) else '当前追投来源或配置结构不足')
    else:result['prepared']=True
    return result
