"""Verified live-universal boost creation compiler, no network or storage.

Cost-controlled live material chase cannot express the requested two-hour
duration in the current API. Keep that branch blocked pending a reliable stop.
"""
from decimal import Decimal
from .schema import parse_time

CREATE_PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/create/'
CREATE_SOURCE='https://open.oceanengine.com/labels/12/docs/1825827435645963'
CONTRACT_VERSION='2026-10-23-live-boost-v1'
PURCHASE='AD_CONVERT_TYPE_LIVE_SUCCESSORDER_PAY'


def prepare_boost_body(proposal,snapshot,settings,now,snapshots,evaluation,*,synthetic=False):
    from .actuation import amount,exact_id
    if snapshots is None or not isinstance(evaluation,dict):raise ValueError('起量创建需当前完整评估及来源集合')
    if proposal.get('action')!='one_click_boost' or proposal.get('scope')!='plan':raise ValueError('当前创建合同仅覆盖计划级一键起量')
    if not any(r==proposal and r.get('eligible') is True for r in evaluation.get('recommendations',[])):
        raise ValueError('创建建议不是当前完整评估中的合格对象')
    current=[e for e in evaluation.get('entities',[]) if e['account_id']==proposal['account_id'] and e['entity_id']==proposal['entity_id']
        and e['scope']=='plan' and e['source_key']==proposal['source_key']]
    if len(current)!=1 or current[0]['latest_snapshot_id']!=snapshot.get('id'):raise ValueError('起量母计划不是当前独立来源快照')
    if snapshot.get('scene')!='live' or snapshot.get('promotion_type')!='universal':
        raise ValueError('当前名称及参数条件只核验了直播全域；商品或乘方保持阻断')
    if not any(a.get('id')==proposal['account_id'] and a.get('observe',True) is True for a in settings.get('accounts',[])):
        raise ValueError('母计划账户未启用观察')
    if snapshot.get('source',{}).get('kind')!=('demo' if synthetic else 'import'):
        raise ValueError('母计划配置须为明确报告来源，不能以页面字段猜测')
    required_kind='demo' if synthetic else 'import'
    account_ids={r.get('id') for r in snapshots if r.get('account_id')==proposal['account_id'] and r.get('scope')=='account'
        and r.get('scene')==snapshot.get('scene') and r.get('metric_basis')==snapshot.get('metric_basis')
        and r.get('source',{}).get('kind')==required_kind
        and r.get('source',{}).get('series_key')==snapshot.get('context',{}).get('account_source_key')}
    health=evaluation.get('collection_health',{}).get('sources',[])
    if not any(h.get('snapshot_id')==snapshot.get('id') and h.get('clock_current') is True for h in health):
        raise ValueError('母计划不是时间有效的当前来源')
    if len([h for h in health if h.get('snapshot_id') in account_ids and h.get('clock_current') is True])!=1:
        raise ValueError('起量账户基线须为唯一、时间有效且与母计划同类的报告来源')
    if snapshot.get('control',{}).get('running') is not True:raise ValueError('起量母计划须明确在投')
    ctx=snapshot.get('context',{})
    if ctx.get('manual_reviewed') is not True or ctx.get('plan_delivery_mode')!='cost_control' or ctx.get('plan_multi_aweme') is not False:
        raise ValueError('起量需核对人工声明的单号控成本母计划')
    goal={'overall_payment':'AD_CONVERT_TYPE_LIVE_PAY_ROI','net':'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI'}.get(snapshot.get('metric_basis'))
    if goal is None or ctx.get('plan_optimization_goal')!=goal or ctx.get('plan_roi_config_verified') is not True:
        raise ValueError('当前母计划优化目标及配置声明未核验')
    if abs((parse_time(ctx.get('plan_roi_observed_at'))-parse_time(snapshot['source']['captured_at'])).total_seconds())>1:
        raise ValueError('母计划配置截点与当前快照不一致')
    params=proposal.get('parameters',{})
    if not isinstance(params,dict) or set(params)!={'budget','duration_hours','optimization_goal','cost_guarantee'}:
        raise ValueError('起量参数缺失或包含尚未核验的附加设置')
    if params['optimization_goal']!='live_purchase' or params['cost_guarantee']!='not_supported_user_rule':
        raise ValueError('起量仅使用直播间购买，并保留用户策略的不支持保障提示')
    budget=amount(params['budget']);duration=amount(params['duration_hours'])
    if not Decimal(100)<=budget<=Decimal(300):raise ValueError('起量初始预算须为用户策略100至300元')
    if not Decimal(1)<=duration<=Decimal(24) or duration%Decimal('.5')!=0:
        raise ValueError('起量时长须至少1小时且符合官方0.5小时网格，上限24小时')
    if budget>amount(snapshot.get('metrics',{}).get('budget')):raise ValueError('起量预算不得超过母计划当前预算')
    if any(a.get('account_id')==proposal['account_id'] and a.get('priority')=='P0' for a in evaluation.get('alerts',[])):
        raise ValueError('账户存在P0风险，不创建常规起量任务')
    aid,pid=exact_id(proposal['account_id']),exact_id(proposal['entity_id'])
    if str(aid)!=proposal['account_id'] or str(pid)!=proposal['entity_id']:raise ValueError('起量账户及母计划ID须为规范十进制字符串')
    # Current documentation disallows name in the live-universal branch.
    # ROI/deep_external_action/smart_bid_type are not boost settings here.
    return ('{"advertiser_id":'+str(aid)+',"ad_id":'+str(pid)+',"scene":"SMART_BOOST","budget":'
        +format(budget,'.2f')+',"duration":'+format(duration,'.1f')+',"external_action":"'+PURCHASE+'"}')


def interpret_creation_response(value):
    from .plan_readback import _id
    result={'business_accepted':False,'created_task_id':None,'known_rejected':False,'platform_state_observed':False,'automatic_retry':False}
    if not isinstance(value,dict) or type(value.get('code')) is not int:return result
    data=value.get('data');native=data.get('id') if isinstance(data,dict) else None
    if value['code']!=0:
        if native is None:result['known_rejected']=True
        return result
    try:task=_id(native)
    except ValueError:return result
    result.update(business_accepted=True,created_task_id=task)
    return result
