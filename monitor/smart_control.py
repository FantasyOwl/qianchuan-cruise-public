"""Live universal flow-control request planning, with no IO or timers.

The native create contract has budget but no duration and returns no documented
task ID. A native stop addresses the parent plan. A preview never registers a
deadline, proves absence of another control, or certifies its eventual stop.
"""
from decimal import Decimal, InvalidOperation

CREATE_PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/create/'
STOP_PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/status/update/'
CREATE_SOURCE='https://open.oceanengine.com/labels/12/docs/1848756884121668'
STOP_SOURCE='https://open.oceanengine.com/labels/12/docs/1848757011743171'
EXECUTION_LIMITS=[
    '用户当前只允许观察，控量原生客户端未接入运行服务',
    '30至60分钟是本地策略期限，当前接口不接收duration；可靠截止工作线程未接通',
    '停止以母计划ad_id为对象，需核验现有控量及共享母计划占用，不能借用起量任务ID',
    '创建或停止回执不证明实际状态；控量独立状态读回合同尚未核验，不能自动释放占用',
]


def prepare_smart_control(proposal,snapshot,settings,now,snapshots,evaluation,*,synthetic=False):
    from .actuation import amount, exact_id
    from .schema import parse_time
    if not isinstance(snapshots,list) or not isinstance(evaluation,dict):
        raise ValueError('控量预览需要当前完整评估和来源集合')
    if proposal.get('action')!='one_click_control' or proposal.get('scope')!='plan':
        raise ValueError('控量合同只用于明确计划级一键控量')
    if any(proposal.get(k)!=snapshot.get(k) for k in ('account_id','entity_id','scope','metric_basis')):
        raise ValueError('控量建议和母计划身份或口径不一致')
    if not any(r==proposal and r.get('eligible') is True for r in evaluation.get('recommendations',[])):
        raise ValueError('控量建议不是当前完整评估中的合格对象')
    if not any(r==snapshot for r in snapshots):
        raise ValueError('控量母计划配置不在当前评估输入中')
    current=[e for e in evaluation.get('entities',[]) if e.get('account_id')==proposal.get('account_id')
        and e.get('entity_id')==proposal.get('entity_id') and e.get('scope')=='plan'
        and e.get('source_key')==proposal.get('source_key')]
    if len(current)!=1 or current[0].get('latest_snapshot_id')!=snapshot.get('id'):
        raise ValueError('控量母计划不是当前来源的最新快照')
    accounts=[a for a in settings.get('accounts',[]) if a.get('id')==snapshot.get('account_id')]
    if len(accounts)!=1 or accounts[0].get('observe',True) is not True:
        raise ValueError('控量账户须唯一且已启用观察')
    if (snapshot.get('scene')!='live' or snapshot.get('promotion_type')!='universal'
        or snapshot.get('source',{}).get('kind')!=('demo' if synthetic else 'import')):
        raise ValueError('当前控量请求只核验直播全域的明确报告来源，不能套用乘方或商品')
    health=evaluation.get('collection_health',{}).get('sources',[])
    if not any(h.get('snapshot_id')==snapshot.get('id') and h.get('clock_current') is True for h in health):
        raise ValueError('控量母计划缺少当前有效的指标时钟')
    ctx=snapshot.get('context',{})
    if ctx.get('carrying_ready') is not False:
        raise ValueError('控量须重新确认当前明确承接不足')
    evidence=proposal.get('evidence',{})
    trigger=evidence.get('trigger') if isinstance(evidence,dict) else None
    if trigger=='forecast':
        predicted=parse_time(ctx.get('predicted_overload_at'))
        if (predicted!=parse_time(evidence.get('predicted_overload_at'))
            or not 180<=(predicted-now).total_seconds()<=300):
            raise ValueError('控量承接预警已不在当前3至5分钟窗口')
    elif trigger=='observed_surge':
        current,previous=evidence.get('current_hour'),evidence.get('previous_hour')
        target=accounts[0].get('target_roi')
        def number(value):
            return type(value) in {int,float} and Decimal(str(value)).is_finite() and value>=0
        if (not isinstance(current,dict) or not isinstance(previous,dict)
            or current.get('complete') is not True or previous.get('complete') is not True
            or not all(number(v) for v in (current.get('spend'),previous.get('spend'),current.get('roi'),target))
            or previous['spend']<=0 or current['spend']<=previous['spend']*1.3 or current['roi']>=target):
            raise ValueError('控量应急条件须为两个完整小时的消耗突增且ROI偏低')
    else:raise ValueError('控量触发须明确区分承接预判与已经发生的消耗突增')
    if (snapshot.get('control',{}).get('running') is not True or ctx.get('manual_reviewed') is not True
        or ctx.get('plan_delivery_mode')!='cost_control' or ctx.get('latest_budget_verified') is not True):
        raise ValueError('控量须明确母计划在投、控成本模式及当前预算已复核')
    if ctx.get('smart_control_budget_reviewed') is not True:
        raise ValueError('控量调控预算须明确选择并复核，不能借用起量预算或母计划全额')
    budget=amount(ctx.get('smart_control_budget'))
    if budget>amount(snapshot.get('metrics',{}).get('budget')):
        raise ValueError('控量调控预算不得超过母计划当前预算')
    parameters=proposal.get('parameters')
    if not isinstance(parameters,dict) or set(parameters)!={'duration_hours'}:
        raise ValueError('控量时长须单独保留，不能夹带未核验的原生设置')
    raw=parameters['duration_hours']
    if isinstance(raw,bool) or not isinstance(raw,(int,float,str,Decimal)):
        raise ValueError('控量时长必须为明确的0.5至1小时')
    try:duration=Decimal(str(raw))
    except InvalidOperation:raise ValueError('控量时长无效') from None
    if (not duration.is_finite() or not Decimal('.5')<=duration<=Decimal(1)
        or duration*60!=(duration*60).to_integral_value()):
        raise ValueError('控量本地时长须为30至60整分钟，不套用起量接口的时长网格')
    aid,pid=exact_id(proposal.get('account_id')),exact_id(proposal.get('entity_id'))
    if str(aid)!=proposal['account_id'] or str(pid)!=proposal['entity_id']:
        raise ValueError('控量账户和母计划须为规范十进制字符串')
    create='{"advertiser_id":'+str(aid)+',"ad_id":'+str(pid)+',"budget":'+format(budget,'.2f')+'}'
    stop='{"advertiser_id":'+str(aid)+',"ad_id":'+str(pid)+',"opt_type":"DISABLE"}'
    return {'body_json':create,'stop_body_json':stop,
        'local_duration_policy':{'duration_minutes':int(duration*60),'native_duration_supported':False,
            'deadline_registered':False,'due_at':None,'anchor_required':'first_create_request_sent_at',
            'deadline_extension_allowed':False,'automatic_retry':False},
        'execution_blockers':list(EXECUTION_LIMITS)}


def interpret_smart_control_response(value):
    result={'business_accepted':False,'known_rejected':False,'created_task_id':None,
            'platform_state_observed':False,'parent_hold_released':False,'automatic_retry':False}
    if not isinstance(value,dict) or type(value.get('code')) is not int:
        return result
    if value['code']!=0:
        result['known_rejected']=True
    elif isinstance(value.get('data'),dict):
        result['business_accepted']=True
    return result
