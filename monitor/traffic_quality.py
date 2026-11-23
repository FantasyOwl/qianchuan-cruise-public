"""Paired-window live quality observations, never a targeting or ROI executor.

Window means/rates are compared directly, never subtracted as counters. Imported
verification describes the reporter's claim, not platform certification.
"""
from datetime import timedelta
import re

from .engine import _time, _uid

DIMENSIONS = (("watch_duration", "baseline_watch_duration", "平均停留", "秒"),
              ("interaction_rate", "baseline_interaction_rate", "互动用户占比", "%"),
              ("order_rate", "baseline_order_rate", "下单用户占比", "%"))
METADATA = ("quality_metrics_verified", "quality_baseline_verified", "quality_baseline_source",
            "quality_definition_id", "baseline_quality_definition_id", "quality_segment_id", "baseline_quality_segment_id",
            "quality_window_start", "quality_window_end", "quality_baseline_start", "quality_baseline_end", "live_ongoing")
FIELDS = tuple(k for pair in DIMENSIONS for k in pair[:2]) + ("quality_sample_users", "baseline_quality_sample_users")
LIMITS = {"relative_drop": .2, "min_users_per_window": 100, "min_window_minutes": 15,
          "max_window_minutes": 60, "boundary_tolerance_seconds": 1}


def _context(row):
    ctx = row['raw'].get('context')
    return ctx if isinstance(ctx, dict) else {}


def observe_quality(last, records, data_status):
    """Accept one source's explicit current/baseline window report only."""
    ctx = _context(last);m = last['metrics'];src = last['source'];raw = last['raw'];dt = last['data']
    missing = [];current_blockers = []
    starts = [_time(ctx.get(k)) for k in ('quality_baseline_start','quality_baseline_end','quality_window_start','quality_window_end')]
    observation = {'state':'insufficient','is_current':False,'data_at':src.get('data_at'),
        'window':{'start':starts[2].isoformat() if starts[2] else None,'end':starts[3].isoformat() if starts[3] else None},
        'baseline_window':{'start':starts[0].isoformat() if starts[0] else None,'end':starts[1].isoformat() if starts[1] else None},
        'sample_users':m.get('quality_sample_users'),'baseline_sample_users':m.get('baseline_quality_sample_users'),
        'comparisons':[],'missing':missing,'current_blockers':current_blockers,'thresholds':dict(LIMITS),
        'rule_basis':'local_observation','platform_verified':False,
        'note':'比较阈值和样本门槛是本地观察条件。联合下降仅提示疑似流量泛化，不证明原因、显著性或可执行调控。'}
    if src.get('kind') not in {'import','demo'}:
        missing.append('页面或旧库观察不能认证质量口径与基线')
    if data_status == 'unverified':missing.append('来源时间或质量尚未核验')
    if ctx.get('quality_metrics_verified') is not True:
        missing.append('当前单位、去重用户分母和归因窗口未核对')
    if ctx.get('quality_baseline_verified') is not True:
        missing.append('基线与当前窗口的可比性未核对')
    description = ctx.get('quality_baseline_source')
    if not isinstance(description,str) or not description.strip() or len(description)>500 or any(ord(c)<32 for c in description):
        missing.append('缺少明确基线来源说明')
    for field, baseline, label in (('quality_definition_id','baseline_quality_definition_id','指标定义'),
                                   ('quality_segment_id','baseline_quality_segment_id','流量分群')):
        value = ctx.get(field)
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',value) or value!=ctx.get(baseline):
            missing.append(label+'标识缺失或不一致')
    duration = baseline_duration = None
    if any(t is None for t in starts) or dt is None:
        missing.append('需要两个明确且相邻的统计窗口，不能用采集时间补齐')
    else:
        bs,be,cs,ce = starts;duration = (ce-cs).total_seconds();baseline_duration = (be-bs).total_seconds()
        if not (900<=duration<=3600 and 900<=baseline_duration<=3600):
            missing.append('两窗口时长均须为15至60分钟')
        if abs(duration-baseline_duration)>1 or abs((cs-be).total_seconds())>1:
            missing.append('基线必须是紧邻当前窗口且等长的上一窗口')
        if abs((ce-dt).total_seconds())>1:
            missing.append('当前质量窗口截止时间与数据时间不一致')
        period = src.get('metric_period','day')
        lower = dt.replace(hour=0,minute=0,second=0,microsecond=0) if period=='day' else _time(src.get('period_start')) if period=='live_session' else None
        if lower is None or bs<lower or bs>=be or cs>=ce or be>cs+timedelta(seconds=1):
            missing.append('质量窗口跨越来源日或场次边界，或时间顺序无效')
    for field in ('quality_sample_users','baseline_quality_sample_users'):
        value = m.get(field)
        if value is None or value!=int(value) or value<100:
            missing.append('两个窗口各需至少100个明确去重用户样本');break
    signature = ([m.get(k) for k in FIELDS],[ctx.get(k) for k in METADATA])
    for other in records:
        if other['data']==dt and (([other['metrics'].get(k) for k in FIELDS],[_context(other).get(k) for k in METADATA])!=signature or other['issues']!=last['issues']):
            missing.append('同一数据时间存在质量指标或基线元数据冲突');break
    for field,base,label,unit in DIMENSIONS:
        value,baseline = m.get(field),m.get(base);reason = ''
        if value is None or baseline is None:reason='指标或基线缺失'
        elif unit=='%' and (value>100 or baseline>100):reason='去重用户百分比超出0至100范围'
        elif unit=='秒' and duration is not None and baseline_duration is not None and (value>duration or baseline>baseline_duration):reason='停留均值超过对应观察窗口，需核对定义'
        elif baseline==0:reason='基线为零，不能计算相对下降'
        if reason:missing.append(label+'：'+reason)
        observation['comparisons'].append({'field':field,'label':label,'unit':unit,'value':value,'baseline':baseline,
                                          'relative_change':None,'declined':None,'reason':reason})
    if data_status!='fresh':current_blockers.append('数据不是当前有效观测')
    if ctx.get('live_ongoing') is not True:current_blockers.append('当前在播状态未确认')
    # A simultaneous unknown baseline never produces partial numerical diagnosis.
    if missing:return observation,[],[]
    for item in observation['comparisons']:
        item['relative_change']=(item['value']-item['baseline'])/item['baseline']
        item['declined']=item['relative_change']<=-.2+1e-10
    declines = {i['field'] for i in observation['comparisons'] if i['declined']}
    broadening = 'order_rate' in declines and bool(declines & {'watch_duration','interaction_rate'})
    observation.update(state='suspected_broadening' if broadening else 'mixed_decline' if declines else 'no_material_decline',
                       is_current=not current_blockers)
    if not broadening or not observation['is_current']:return observation,[],[]
    rule='traffic_quality_decline';source_key=':'.join(str(x) for x in last['key'][3:])
    identity={'account_id':raw['account_id'],'entity_id':raw['entity_id'],'scope':'live','source_key':source_key}
    evidence={k:observation[k] for k in ('data_at','window','baseline_window','sample_users','baseline_sample_users','comparisons','thresholds')}
    risk='可能同时受主播、商品、价格、履约与流量结构影响；先核实原因，不直接收紧定向、修改ROI或停止主力计划。'
    alert={**identity,'id':_uid(*last['key'],rule,dt),'priority':'P1','source_basis':last['basis'],'rule':rule,
        'title':'下单用户占比与停留或互动联合下降，需排查流量泛化','evidence':evidence,'risk':risk,'rule_basis':'local_observation'}
    rec={**identity,'id':_uid(*last['key'],rule,'hold'),'priority':'P1','metric_basis':last['basis'],'rule':rule,
        'action':'hold_scaling','action_label':'暂停增量放量并复核流量质量','target_type':'live','target_id':raw['entity_id'],
        'before':None,'after':None,'change_pct':None,'reason':alert['title'],
        'parameters':{'review_checks':['主播与商品承接变化','付费与自然流量结构变化','指标去重、归因及窗口定义'],
                      'conditional_review':'原因确认后，再分别核对当前产品的定向能力或计划ROI微调条件'},
        'expected_goal':'核实质量下降原因并避免继续增加低效流量','risk':risk,'evidence':evidence,
        'eligible':False,'blockers':['仅为联合质量观察，尚未核实原因与具体计划调控条件'],
        'mode':'simulation_only','rule_basis':'local_observation'}
    return observation,[alert],[rec]
