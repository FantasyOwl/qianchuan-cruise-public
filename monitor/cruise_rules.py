"""Independent replica of the original cruise's rule semantics.

Evidence: retained web_console_ui.html renderRules/collectRules and
src.rule_engine/RuleEngine._evaluate_rule-77.dis.txt. This module never performs
browser, network or advertising writes. Task payment ROI is distinct from the
account's user-defined overall-payment assessment target.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

CN = timezone(timedelta(hours=8))


def field(key, label, default=None, *, unit='', options=None, template_default=None, help=''):
    return {'key': key, 'label': label, 'type': 'select' if options else 'boolean' if type(default) is bool else 'number',
            'default': default, 'unit': unit, 'options': options or [], 'template_default': template_default, 'help':help}


FEATURE_CATALOG = [
    {'id': 'f1', 'name': '低支付ROI暂停', 'lane': 'main', 'description': '指定调控成本/消耗大于门槛且任务支付ROI小于门槛时暂停任务',
     'fields': [field('cost_metric','判断口径','control_cost',options=[{'value':'control_cost','label':'调控综合成本'},{'value':'control_spend','label':'调控消耗'}]),
                field('min_control_cost','成本或消耗大于',50,unit='元'),field('max_roi','任务支付ROI小于',template_default=2.25),field('cooldown_minutes','同动作冷却',15,unit='分钟')]},
    {'id':'f2','name':'控成本增加','lane':'main','description':'控成本追投任务命中成本区间和任务支付ROI门槛后，将调控预算增加到目标值',
     'fields':[field('budget_policy','预算执行方式','fifty_percent',options=[{'value':'fifty_percent','label':'单次增幅最多50%'},{'value':'saved_target','label':'按已保存的目标预算执行'}])], 'row_fields':[field('cost_metric','判断口径','control_cost',options=[{'value':'control_cost','label':'调控综合成本'},{'value':'control_spend','label':'调控消耗'}]),field('min_control_cost','所选成本/消耗大于'),field('max_control_cost','所选成本/消耗小于'),field('min_roi','任务支付ROI大于'),field('target_control_budget','目标调控预算',unit='元'),field('cooldown_minutes','同动作冷却',15,unit='分钟')]},
    {'id':'f3','name':'放量预算和时长增加','lane':'main','description':'放量追投命中规则后，分别生成预算与延长时长的确认单；预算是绝对目标，时长是小时增量',
     'fields':[], 'row_fields':[field('cost_metric','判断口径','control_cost',options=[{'value':'control_cost','label':'调控综合成本'},{'value':'control_spend','label':'调控消耗'}]),field('min_control_cost','所选成本/消耗大于'),field('max_control_cost','所选成本/消耗小于'),field('min_roi','任务支付ROI大于'),field('target_control_budget','目标调控预算',unit='元'),field('increase_control_duration','延长时长',unit='小时'),field('cooldown_minutes','同动作冷却',15,unit='分钟')]},
    {'id':'f4','name':'批量成本暂停','lane':'main','description':'按所选条件暂停追投任务。今日消耗＋ROI模式要求时长、消耗和ROI三项同时满足',
     'fields':[field('pause_rule','暂停条件','cost_equals',options=[{'value':'cost_equals','label':'成本等于指定值（原规则）'},{'value':'low_spend','label':'创建满指定小时，累计消耗仍偏低'},{'value':'either','label':'成本等值或累计低消耗任一命中'},{'value':'daily_spend_roi','label':'今日时长＋消耗＋ROI'}]),
        field('cost_metric','判断口径','control_cost',options=[{'value':'control_cost','label':'调控综合成本'},{'value':'control_spend','label':'调控消耗'}]),field('min_runtime_minutes','原规则：创建超过',30,unit='分钟'),field('control_cost_equals','原规则：所选成本/消耗等于',0,unit='元'),
        field('low_spend_hours','追投任务创建满',unit='小时'),field('low_spend_max','累计消耗低于',unit='元'),field('low_spend_scope','适用追投方式','both',options=[{'value':'both','label':'控成本和放量追投'},{'value':'cost_control','label':'仅控成本追投'},{'value':'volume','label':'仅放量追投'}]),
        field('daily_runtime_hours','今日经过时长达到',unit='小时'),field('daily_spend_max','今日调控消耗低于',unit='元'),
        field('daily_roi_max','今日调控支付ROI低于'),field('cooldown_minutes','同动作冷却',15,unit='分钟')]},
    {'id':'f5','name':'自动化追投','lane':'auto','description':'按近几天素材消耗及ROI筛选；API可在明确本户规则与额度授权后全自动执行',
     'fields':[field('lookback_days','素材统计最近天数（含今天）',5,unit='天'),field('batch_size','兼容旧配置：固定批量',3,unit='条',help='填写下方区间后按区间分批；本项保留兼容旧设置。'),
         field('batch_size_min','每批最少素材数',unit='条',help='最少2条；不足最少数量时等待下轮。'),field('batch_size_max','每批最多素材数',unit='条',help='最多20条。整批共用预算，不随素材数量相乘。'),
         field('allow_running_materials','允许调控中素材再次追投',False,help='默认关闭。开启后，调控中的素材也可按消耗和ROI条件入选；确认模式下逐批核对，开启全自动后按已授权整批预算执行，不会停止现有追投。')], 'row_fields':[field('material_spend_min','素材消耗大于'),field('material_spend_max','素材消耗小于（留空不限制）'),field('material_roi_min','素材ROI大于'),
         field('chase_type','追投方式','控成本追投',options=[{'value':'控成本追投','label':'控成本追投'},{'value':'放量追投','label':'放量追投'}]),
         field('optimization_target','优化目标','直播间成交',options=[{'value':'直播间成交','label':'直播间成交'},{'value':'综合ROI','label':'综合营销ROI（按API类型核对）'},{'value':'综合营销ROI','label':'综合营销ROI'},{'value':'支付ROI','label':'支付ROI'},{'value':'净成交ROI','label':'净成交ROI'}]),
         field('daily_budget','控成本日预算',unit='元'),field('total_budget','放量总预算',unit='元'),field('control_duration','放量时长',unit='小时'),field('bid_value','控成本出价/倍率')]},
    {'id':'f6','name':'ROI目标调整','lane':'main','description':'复刻原版配置与条件诊断；原程序静态分析未证实主计划ROI写入链',
     'implementation_status':'config_and_diagnostics_original_writer_not_found',
     'fields':[field('min_interval_minutes','最小间隔',15,unit='分钟'),field('min_overall_roi','综合ROI大于',template_default=2.8),field('max_online_users','在线人数小于',30),
         field('min_15m_roi','近15分ROI大于',template_default=2.6),field('min_30m_roi','近30分ROI大于',template_default=2.6),field('target_roi_delta','ROI调整幅度',-.1)]},
]
FIELD_CATALOG = {f['id']: f.get('fields', []) for f in FEATURE_CATALOG}


def default_profile(scene='live'):
    if scene not in {'live','product'}: raise ValueError('账户场景无效')
    features={}
    for item in FEATURE_CATALOG:
        values={x['key']:deepcopy(x['default']) for x in item['fields']}
        features[item['id']]={'enabled':False, **values}
        if 'row_fields' in item: features[item['id']]['rules']=[]
    return {'version':1,'enabled':False,'main_interval_seconds':300,'auto_interval_seconds':600,
        'operation_start':'07:00','operation_end':'01:00','snapshot_max_age_seconds':180,
        'broadcast_protection':{'enabled':False,'start':'01:00','end':'07:00'},
        'safety':{'max_actions_per_round':20,'max_actions_per_day':500,'high_spend_no_orders':500,'duplicate_guard_minutes':1440},'features':features}


def profile_for_entry(profile,account):
    """全域 task rules have spend, not the 乘方 comprehensive-cost field."""
    if not isinstance(profile,dict):return profile
    result=deepcopy(profile)
    if account.get('scene')=='live' and account.get('delivery_entry','uni_promotion')=='uni_promotion':
        for fid in ('f1','f2','f3','f4'):
            cfg=result.get('features',{}).get(fid,{})
            for rule in cfg.get('rules',[cfg]):
                if 'cost_metric' in rule:rule['cost_metric']='control_spend'
    return result


def _num(value, *, nullable=False, minimum=0, maximum=1e12, integer=False):
    if value is None and nullable:return None
    if type(value) is bool or not isinstance(value,(int,float,str,Decimal)):raise ValueError('数值格式无效')
    try: number=Decimal(str(value))
    except InvalidOperation:raise ValueError('数值格式无效') from None
    if not number.is_finite() or not Decimal(str(minimum))<=number<=Decimal(str(maximum)) or integer and number!=int(number):raise ValueError('数值超出允许范围')
    return int(number) if integer else float(number)


def _time(value):
    if isinstance(value,datetime):result=value
    elif isinstance(value,str):
        try:result=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:raise ValueError('时间格式无效') from None
    else:raise ValueError('时间格式无效')
    if result.tzinfo is None:raise ValueError('时间缺少时区')
    return result.astimezone(CN)


def _clock(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):raise ValueError('时段须为HH:MM')
    return value


def _boolean(value):
    if type(value) is not bool:raise ValueError('开关须为布尔值')
    return value


def _object(value, allowed):
    if not isinstance(value,dict) or set(value)-set(allowed):raise ValueError('巡航配置字段不匹配')
    return value


def validate_profile(value, scene='live'):
    out=default_profile(scene);_object(value,out)
    for k in value:
        if k not in {'features','safety','broadcast_protection'}:out[k]=deepcopy(value[k])
    if type(out['version']) is not int or out['version']!=1:raise ValueError('巡航配置版本无效')
    out['enabled']=_boolean(out['enabled'])
    for key in ('main_interval_seconds','auto_interval_seconds'):out[key]=_num(out[key],minimum=5,maximum=86400,integer=True)
    out['snapshot_max_age_seconds']=_num(out['snapshot_max_age_seconds'],minimum=10,maximum=3600,integer=True)
    for key in ('operation_start','operation_end'):out[key]=_clock(out[key])
    for section in ('safety','broadcast_protection'):
        supplied=value.get(section,{ });_object(supplied,out[section]);out[section].update(deepcopy(supplied))
    b=out['broadcast_protection'];b['enabled']=_boolean(b['enabled']);b['start']=_clock(b['start']);b['end']=_clock(b['end'])
    for key in out['safety']:
        out['safety'][key]=_num(out['safety'][key],minimum=0,maximum=1000000,integer=key!='high_spend_no_orders')
    supplied=value.get('features',{});_object(supplied,out['features'])
    for item in FEATURE_CATALOG:
        fid=item['id'];cfg=out['features'][fid];new=supplied.get(fid,{});_object(new,cfg);cfg.update(deepcopy(new));cfg['enabled']=_boolean(cfg['enabled'])
        _validate_fields(cfg,item['fields'])
        if fid=='f5':
            for key,lo,hi in [('lookback_days',3,5),('batch_size',1,20)]:
                cfg[key]=_num(cfg[key],minimum=lo,maximum=hi,integer=True)
            if cfg.get('batch_size_min') is not None or cfg.get('batch_size_max') is not None:
                for key in ('batch_size_min','batch_size_max'):cfg[key]=_num(cfg.get(key),minimum=2,maximum=20,integer=True)
                if cfg['batch_size_min']>cfg['batch_size_max']:raise ValueError('每批最少素材数不能大于最多素材数')
                cfg['batch_size']=cfg['batch_size_max']
        if 'row_fields' in item:
            if not isinstance(cfg['rules'],list) or len(cfg['rules'])>100:raise ValueError('每个功能最多100条规则')
            rows=[]
            for row in cfg['rules']:
                allowed={f['key'] for f in item['row_fields']}|{'enabled','name','cost_metric'}|({'audience'} if fid=='f5' else set());_object(row,allowed)
                clean={f['key']:deepcopy(f['default']) for f in item['row_fields']};clean.update(deepcopy(row))
                if 'enabled' in clean:clean['enabled']=_boolean(clean['enabled'])
                if 'name' in clean and (not isinstance(clean['name'],str) or len(clean['name'])>100):raise ValueError('规则名称无效')
                if clean.get('cost_metric','control_cost') not in {'control_cost','control_spend'}:raise ValueError('成本口径无效')
                _validate_fields(clean,item['row_fields'])
                if fid=='f5' and 'audience' in clean:
                    from .chase_audience import validate,DEFAULT
                    clean['audience']=validate(clean['audience'])
                    if clean['audience']==DEFAULT:clean.pop('audience')
                for lo,hi in (('min_control_cost','max_control_cost'),('material_spend_min','material_spend_max')):
                    if clean.get(lo) is not None and clean.get(hi) is not None and clean[lo]>=clean[hi]:raise ValueError('规则区间上下限无效')
                rows.append(clean)
            cfg['rules']=rows
    return out


def _validate_fields(value, fields):
    for f in fields:
        key=f['key']
        if f['type']=='boolean':value[key]=_boolean(value[key])
        elif f['type']=='select':
            if value[key] not in {o['value'] for o in f['options']}:raise ValueError('规则选项无效')
        else:
            raw=value[key]
            value[key]=_num(value[key],nullable=f['default'] is None,minimum=-100 if key=='target_roi_delta' else 0,
                maximum=1e12,integer=key in {'cooldown_minutes','min_runtime_minutes','min_interval_minutes','max_online_users'})
            if key in {'target_control_budget','increase_control_duration','daily_budget','total_budget','control_duration','bid_value'} and value[key] is not None and value[key]<=0:raise ValueError('预算、时长、出价必须为正数')
            if key in {'low_spend_hours','low_spend_max'} and value[key] is not None:
                if value[key]<=0 or key=='low_spend_hours' and value[key]>8760:raise ValueError('低消耗暂停的小时数和消耗门槛必须为正数，小时数不超过8760')
                if Decimal(str(raw))!=Decimal(str(raw)).quantize(Decimal('.01')):raise ValueError('低消耗暂停的小时数和金额最多两位小数')
            if key=='daily_runtime_hours' and value[key] is not None and not 0<value[key]<24:raise ValueError('今日经过时长须大于0且小于24小时')
            if key in {'target_control_budget','increase_control_duration','daily_budget','total_budget','control_duration'} and raw is not None:
                amount=Decimal(str(raw))
                if amount!=amount.quantize(Decimal('.01')):raise ValueError('预算及小时值最多两位小数，不自动舍入')


def _in_window(clock,start,end):return start<=clock<end if start<end else clock>=start or clock<end


def material_chase_allowed(status, allow_running=False):
    allowed={'-','未追投','已暂停','暂停中','已结束','调控暂停','调控结束'}
    if allow_running is True:allowed.update({'调控中','追投中'})
    return isinstance(status,str) and status.strip() in allowed


def _value(row,*keys):
    metrics=row.get('metrics',{})
    for key in keys:
        if metrics.get(key) is not None:
            try:return Decimal(str(_num(metrics[key])))
            except ValueError:return None
    return None


def _decimal(value):return format(Decimal(str(value)).quantize(Decimal('.01')),'f')


def _id(value):return isinstance(value,str) and bool(re.fullmatch(r'[0-9]{1,30}',value))


def _parent(s):return s.get('context',{}).get('parent_plan_id') or s.get('source',{}).get('parent_entity_id')


def _base_issues(s,now,profile,*,task=True):
    issues=[];src=s.get('source',{});ctx=s.get('context',{})
    if not _id(s.get('entity_id')) or not _id(_parent(s)):issues.append('缺少明确任务/素材ID或母计划ID')
    if ctx.get('parent_plan_id') and src.get('parent_entity_id') and ctx['parent_plan_id']!=src['parent_entity_id']:issues.append('母计划身份冲突')
    try:
        age=(now-_time(src.get('captured_at'))).total_seconds()
        if not 0<=age<=profile['snapshot_max_age_seconds']:issues.append('当前可见快照已过期或来自未来')
    except ValueError:issues.append('缺少明确采集时间')
    if src.get('metric_date') and src['metric_date']!=now.date().isoformat():issues.append('页面统计日期不是今天')
    if src.get('kind') not in {'browser_dom','demo','official_api'}:issues.append('不是原页面或官方API的任务/素材观察')
    if s.get('scene') not in {'live','推直播间'}:issues.append('原版此规则仅适用于直播追投任务')
    if task and s.get('control',{}).get('running') is not True:issues.append('任务未明确处于调控中')
    return issues


def _recent(history,account,parent,entity,actions,now,minutes):
    pending={'draft','pending','pending_confirmation','approved','claimed','submitting','unknown','submitted_pending_verification'}
    done={'verified','executed','submitted','reconciled_target','reconciled_before','reconciled_other'}
    for event in history:
        if not isinstance(event,dict) or event.get('account_id')!=account or event.get('entity_id',event.get('task_id'))!=entity:continue
        if event.get('parent_plan_id') is not None and event['parent_plan_id']!=parent:continue
        if event.get('action') not in actions:continue
        if event.get('state') in pending:return '已有待确认、执行中或结果不明的同类动作'
        if event.get('state') in done:
            try:
                at=_time(event.get('at') or event.get('occurred_at') or event.get('created_at'))
                if (now-at).total_seconds()<minutes*60:return '同动作仍在冷却期'
            except ValueError:return '已有操作记录缺少明确时间'
    return None


def _proposal(s,fid,action,before,after,reason,*,group_id=None):
    record={'account_id':s['account_id'],'parent_plan_id':_parent(s),'entity_id':s['entity_id'],
        'object_type':'control_task' if s['scope']=='control' else 'creative','feature_id':fid,'action':action,
        'before':before,'after':after,'reason':reason,'source_snapshot_id':s.get('id'),
        'source_captured_at':s.get('source',{}).get('captured_at'),'metric_basis':s.get('metric_basis'),
        'eligible':True,'blocked_reasons':[],'requires_confirmation':True,'synthetic':s.get('source',{}).get('kind')=='demo'}
    if group_id:record['group_id']=group_id
    record['id']=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    return record


def _f4_daily_condition(s,rule,profile,now):
    hours,maximum,ceiling=(rule.get(k) for k in ('daily_runtime_hours','daily_spend_max','daily_roi_max'))
    if None in (hours,maximum,ceiling):return None,['今日暂停须填齐时长、消耗和ROI门槛']
    ctx=s.get('context',{});src=s.get('source',{});delivery=ctx.get('control_delivery_mode');scope=rule.get('low_spend_scope','both')
    if ctx.get('control_type')!='material_chase' or delivery not in {'cost_control','volume'} or scope not in {'both',delivery}:
        return None,['本任务不在今日暂停的素材追投范围内']
    today_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    try:
        observed=_time(src.get('data_at') or src.get('captured_at'));created=_time(ctx.get('created_at'))
    except ValueError:return None,['缺少明确任务创建时间或今日统计时间']
    if src.get('metric_date')!=now.date().isoformat() or observed<today_start or not 0<=(now-observed).total_seconds()<=profile['snapshot_max_age_seconds']:
        return None,['缺少有效的今日调控指标']
    minutes=(observed-max(created,today_start)).total_seconds()/60
    spend=_value(s,'spend');roi=_value(s,'roi') if s.get('metric_basis')=='control_payment' else None
    misses=[]
    if Decimal(str(minutes))<Decimal(str(hours))*60:misses.append('今日经过时长尚未达到设置小时数')
    if spend is None:misses.append('缺少明确今日调控消耗')
    elif spend>=Decimal(str(maximum)):misses.append('今日调控消耗未严格低于设置门槛')
    if roi is None:misses.append('缺少明确今日调控支付ROI')
    elif roi>=Decimal(str(ceiling)):misses.append('今日调控支付ROI未严格低于设置门槛')
    if misses:return None,misses
    return f'今日经过时长已满{hours}小时，调控消耗{spend}元低于{maximum}元，调控支付ROI {roi}低于{ceiling}，暂停该追投任务',[]


def _f4_condition(s,rule,profile,now):
    mode=rule.get('pause_rule','cost_equals');ctx=s.get('context',{});failures=[]
    if mode=='daily_spend_roi':return _f4_daily_condition(s,rule,profile,now)
    if mode in {'cost_equals','either'}:
        misses=[];cost=_value(s,'spend') if rule.get('cost_metric')=='control_spend' else _value(s,'comprehensive_cost','control_cost')
        if ctx.get('control_delivery_mode')!='volume':misses.append('原等值规则只适用于明确放量追投')
        if cost is None:misses.append('缺少明确调控综合成本，不能把缺失当作0')
        elif abs(cost-Decimal(str(rule['control_cost_equals'])))>=Decimal('.0001'):misses.append('调控综合成本不等于指定值')
        try:
            age=(now-_time(ctx.get('created_at') or ctx.get('control_started_at'))).total_seconds()/60
            if age<rule['min_runtime_minutes']:misses.append('任务创建时长不足')
        except ValueError:misses.append('缺少明确任务创建时间')
        if not misses:return '命中原成本等值及创建时长暂停规则',[]
        failures.extend(misses)
    if mode in {'low_spend','either'}:
        misses=[];hours=rule.get('low_spend_hours');maximum=rule.get('low_spend_max');spend=_value(s,'lifetime_spend')
        if hours is None or maximum is None:misses.append('低消耗暂停的小时数或累计消耗门槛尚未填写')
        if ctx.get('control_type')!='material_chase':misses.append('未明确是素材追投任务；不处理一键起量或主计划')
        delivery=ctx.get('control_delivery_mode');scope=rule.get('low_spend_scope','both')
        if delivery not in {'cost_control','volume'} or scope!='both' and scope!=delivery:misses.append('追投方式不在低消耗暂停范围')
        try:
            created=_time(ctx.get('created_at'));age=(now-created).total_seconds()/3600
            if hours is not None and age<Decimal(str(hours)):misses.append('追投创建时长尚未达到设置小时数')
            start=_time(ctx.get('lifetime_spend_start'));end=_time(ctx.get('lifetime_spend_end'))
            if ctx.get('lifetime_spend_verified') is not True or start>created or end<created or not 0<=(now-end).total_seconds()<=profile['snapshot_max_age_seconds']:raise ValueError()
        except ValueError:misses.append('缺少任务创建时间或覆盖创建至今的官方累计消耗范围')
        if spend is None:misses.append('缺少明确任务累计消耗，不能使用今日消耗或把缺值当0')
        elif maximum is not None and spend>=Decimal(str(maximum)):misses.append('累计消耗未严格低于设置门槛')
        if not misses:return f'追投创建满{hours}小时，累计消耗{spend}元低于{maximum}元，暂停该追投任务',[]
        failures.extend(misses)
    return None,list(dict.fromkeys(failures))


def evaluate_account(account_id, profile, snapshots, *, now, history=None):
    if not _id(account_id):raise ValueError('账户ID须为字符串')
    p=validate_profile(profile);now=_time(now);history=[] if history is None else history
    if not isinstance(history,list):raise ValueError('操作历史须为数组')
    if not isinstance(snapshots,(list,tuple)):raise ValueError('观察快照须为数组')
    result={'account_id':account_id,'proposals':[],'skipped':[],'alerts':[],'diagnostics':[],
        'summary':'','mode':'per_action_confirmation','executed':False,'scanned':0}
    def skip(fid,s,reasons):result['skipped'].append({'feature_id':fid,'entity_id':s.get('entity_id') if s else None,'parent_plan_id':_parent(s) if s else None,'reasons':reasons})
    clock=now.strftime('%H:%M');b=p['broadcast_protection']
    if not p['enabled']:result['summary']='本户巡航配置未启用';return result
    if not _in_window(clock,p['operation_start'],p['operation_end']):result['summary']='不在本户操作时段';return result
    if b['enabled'] and _in_window(clock,b['start'],b['end']):result['summary']='本户停播保护生效，本轮不生成动作';return result
    if p['features']['f5']['enabled']:
        result['diagnostics'].append({'feature_id':'f5','status':'creation_not_connected',
            'reason':'旧单素材候选入口不下发；近几天批量素材追投请使用独立批次确认区'})
        rules = p['features']['f5']['rules']
        if not rules:
            result['diagnostics'].append({'feature_id':'f5','status':'incomplete_rule','reason':'尚未配置本户素材追投规则'})
        labels={'material_spend_min':'素材消耗下限','material_spend_max':'素材消耗上限','material_roi_min':'素材ROI下限',
                'daily_budget':'控成本日预算','optimization_target':'优化目标','bid_value':'控成本出价/倍率',
                'total_budget':'放量总预算','control_duration':'放量时长'}
        for index, rule in enumerate(rules, 1):
            if rule.get('enabled',True) is False:continue
            fields=('material_spend_min','material_roi_min') + (('total_budget','control_duration')
                if rule['chase_type']=='放量追投' else ('daily_budget','optimization_target','bid_value'))
            missing=[labels[k] for k in fields if rule.get(k) is None]
            if missing:result['diagnostics'].append({'feature_id':'f5','status':'incomplete_rule',
                'reason':f'规则{index}未设置：'+ '、'.join(missing)+'；当前区间规则不会命中'})
    # Latest observation is per account, parent, scope and entity. Conflicting
    # readings at the same capture time never resolve by source-order guessing.
    latest={};capture_times={};conflicts=set()
    for s in snapshots:
        if not isinstance(s,dict) or s.get('account_id')!=account_id or s.get('scope') not in {'control','creative'}:continue
        if not _id(s.get('entity_id')) or not _id(_parent(s)):
            skip('identity',s,['缺少明确任务/素材ID或母计划ID']);continue
        key=(s.get('scope'),_parent(s),s.get('entity_id'))
        try:at=_time(s.get('source',{}).get('captured_at'))
        except ValueError:skip('identity',s,['缺少明确采集时间']);continue
        old=latest.get(key)
        if old is None or at>capture_times[key]:latest[key]=s;capture_times[key]=at;conflicts.discard(key)
        elif at==capture_times[key] and (s.get('metrics')!=old.get('metrics') or s.get('control')!=old.get('control') or s.get('context')!=old.get('context')):conflicts.add(key)
    if p['features']['f5']['enabled'] and not any(s.get('scope')=='creative' and key not in conflicts
            and not _base_issues(s,now,p,task=False) for key,s in latest.items()):
        result['diagnostics'].append({'feature_id':'f5','status':'missing_current_creatives',
            'reason':'尚无身份有效且未过期的本户素材观察；任务数据不能替代素材消耗和ROI'})
    daily=0
    for e in history:
        if not isinstance(e,dict) or e.get('account_id')!=account_id or e.get('state') not in {'verified','executed','submitted','unknown','submitting'}:continue
        try:daily+=_time(e.get('at') or e.get('occurred_at') or e.get('created_at')).date()==now.date()
        except ValueError:daily+=1
    limit=min(p['safety']['max_actions_per_round'],max(0,p['safety']['max_actions_per_day']-daily))
    f4=p['features']['f4'];f4_mode=f4['pause_rule']
    main_features=('f1','f4','f2','f3') if f4['enabled'] and f4_mode=='daily_spend_roi' else ('f1','f2','f3','f4')
    def pause_priority(item):
        s=item[1]
        if s.get('scope')!='control':return 1
        for fid in ('f1','f4'):
            cfg=p['features'][fid]
            if not cfg['enabled']:continue
            for rule in cfg.get('rules',[cfg]):
                if rule.get('enabled',True) is False:continue
                if fid=='f4':
                    if not _f4_condition(s,rule,p,now)[1]:return 0
                else:
                    cost=_value(s,'spend') if rule.get('cost_metric')=='control_spend' else _value(s,'comprehensive_cost','control_cost');roi=_value(s,'roi')
                    if cost is not None and roi is not None and rule.get('min_control_cost') is not None and rule.get('max_roi') is not None and cost>Decimal(str(rule['min_control_cost'])) and roi<Decimal(str(rule['max_roi'])):return 0
        return 1
    # Select pauses before additions even when the per-round limit is small.
    # All normal freshness, running-state and rule checks still run below.
    for key,s in sorted(latest.items(),key=pause_priority):
        result['scanned']+=1
        if key in conflicts:skip('identity',s,['同一采集时刻的对象观察存在冲突']);continue
        task=s['scope']=='control';issues=_base_issues(s,now,p,task=task)
        if issues:skip('identity',s,issues);continue
        if task:
            spend=_value(s,'spend');orders=_value(s,'payment_orders','orders')
            alert_only=False
            if spend is not None and orders is not None and spend>Decimal(str(p['safety']['high_spend_no_orders'])) and orders==0:
                result['alerts'].append({'entity_id':s['entity_id'],'name':'高消耗无成交提醒','reason':'原规则只提醒；明确启用的新低消耗暂停条件仍独立检查','spend':float(spend)})
                alert_only=True
                if not f4['enabled'] or f4_mode not in {'low_spend','either','daily_spend_roi'}:continue
            matched=False
            for fid in main_features:
                if alert_only and fid!='f4':continue
                cfg=p['features'][fid]
                if not cfg['enabled']:continue
                for index,rule in enumerate(cfg.get('rules',[cfg])):
                    if rule.get('enabled',True) is False:continue
                    misses=[];ctx=s.get('context',{});cost=_value(s,'spend') if rule.get('cost_metric')=='control_spend' else _value(s,'comprehensive_cost','control_cost')
                    roi=_value(s,'roi');budget=_value(s,'budget')
                    if fid in {'f2','f3'} and ctx.get('control_delivery_mode')!=('cost_control' if fid=='f2' else 'volume'):misses.append('追投方式不匹配或未明确')
                    if fid!='f4':
                        required=['min_control_cost','max_roi'] if fid=='f1' else ['min_control_cost','max_control_cost','min_roi','target_control_budget']+(['increase_control_duration'] if fid=='f3' else [])
                        if any(rule.get(k) is None for k in required):misses.append('本户规则阈值尚未配置完整')
                        if cost is None:misses.append('缺少明确'+('调控消耗' if rule.get('cost_metric')=='control_spend' else '调控综合成本'))
                        if roi is None or s.get('metric_basis')!='control_payment':misses.append('缺少明确任务支付ROI口径')
                        if cost is not None and rule.get('min_control_cost') is not None and cost<=Decimal(str(rule['min_control_cost'])):misses.append('成本/消耗未严格超过下限')
                        if cost is not None and rule.get('max_control_cost') is not None and cost>=Decimal(str(rule['max_control_cost'])):misses.append('成本未严格低于上限')
                        if roi is not None and rule.get('max_roi') is not None and roi>=Decimal(str(rule['max_roi'])):misses.append('任务支付ROI未严格低于上限')
                        if roi is not None and rule.get('min_roi') is not None and roi<=Decimal(str(rule['min_roi'])):misses.append('任务支付ROI未严格超过下限')
                        if fid in {'f2','f3'} and (budget is None or fid=='f2' and rule.get('target_control_budget') is not None and budget>=Decimal(str(rule['target_control_budget']))):misses.append('预算缺失或已达到绝对目标预算')
                        if fid in {'f2','f3'} and budget is not None and budget!=budget.quantize(Decimal('.01')):misses.append('当前预算精度不支持，不自动舍入')
                    else:
                        f4_reason,misses=_f4_condition(s,rule,p,now)
                    actions={'f1':['control_pause'],'f2':['control_budget_up'],'f3':['control_budget_up','control_duration_up'],'f4':['control_pause']}[fid]
                    available=[]
                    for action in actions:
                        recent=_recent(history,account_id,_parent(s),s['entity_id'],{action},now,rule.get('cooldown_minutes',15))
                        if recent:
                            if fid!='f3':misses.append(recent)
                        else:available.append(action)
                    if fid=='f3' and not available:misses.append('同组预算与时长均已有待处理动作或处于冷却期')
                    duration=None
                    if fid=='f3':
                        try:
                            duration=Decimal(str(_num(ctx.get('observed_duration_hours')))) if ctx.get('observed_duration_mode')=='fixed' else None
                        except ValueError:duration=None
                        if duration is None:misses.append('缺少明确固定任务时长，不能推算延长后的值')
                        elif duration!=duration.quantize(Decimal('.01')):misses.append('当前时长精度不支持，不自动舍入')
                    if misses:skip(fid,s,list(dict.fromkeys(misses)));continue
                    group=hashlib.sha256(f'{account_id}:{_parent(s)}:{s["entity_id"]}:{fid}:{index}'.encode()).hexdigest()[:24]
                    additions=[]
                    if fid in {'f1','f4'}:additions.append(_proposal(s,fid,'control_pause',True,False,f4_reason if fid=='f4' else '命中原版'+next(f['name'] for f in FEATURE_CATALOG if f['id']==fid)))
                    else:
                        if 'control_budget_up' in available and budget < Decimal(str(rule['target_control_budget'])):
                            additions.append(_proposal(s,fid,'control_budget_up',_decimal(budget),_decimal(rule['target_control_budget']),'命中任务成本区间及支付ROI门槛，预算上调到绝对目标',group_id=group if fid=='f3' else None))
                        if fid=='f3' and 'control_duration_up' in available:
                            additions.append(_proposal(s,fid,'control_duration_up',_decimal(duration*60),_decimal((duration+Decimal(str(rule['increase_control_duration'])))*60),'同组预算与时长分别确认；此条设置任务总时长（分钟）',group_id=group))
                    if len(result['proposals'])+len(additions)>limit:skip(fid,s,['达到本户单轮或单日操作上限']);matched=True;break
                    result['proposals'].extend(additions);matched=True;break
                if matched:break
        else:
            cfg=p['features']['f5']
            if not cfg['enabled']:continue
            ctx=s.get('context',{});status=ctx.get('chase_status',ctx.get('material_chase_status'))
            allow_running=cfg.get('allow_running_materials',False) is True
            allowed=material_chase_allowed(status,allow_running)
            if not allowed:skip('f5',s,['素材追投状态缺失、未明确允许，或已有追投']);continue
            if not allow_running and any(t.get('context',{}).get('material_id')==s['entity_id'] and not _base_issues(t,now,p) for t in latest.values() if t.get('scope')=='control'):skip('f5',s,['同一素材已有在投调控任务，当前未允许重复追投']);continue
            spend=_value(s,'spend');roi=_value(s,'roi')
            for rule in cfg['rules']:
                if rule.get('enabled',True) is False:continue
                misses=[]
                if any(rule.get(k) is None for k in ('material_spend_min','material_roi_min')):misses.append('本户素材规则门槛尚未配置完整')
                if spend is None or roi is None or s.get('metric_basis')!='overall_payment':misses.append('缺少同口径素材消耗和整体支付ROI')
                if spend is not None and rule.get('material_spend_min') is not None and spend<=Decimal(str(rule['material_spend_min'])):misses.append('素材消耗未严格超过下限')
                if spend is not None and rule.get('material_spend_max') is not None and spend>=Decimal(str(rule['material_spend_max'])):misses.append('素材消耗未严格低于上限')
                if roi is not None and rule.get('material_roi_min') is not None and roi<=Decimal(str(rule['material_roi_min'])):misses.append('素材ROI未严格超过下限')
                fields=('total_budget','control_duration') if rule['chase_type']=='放量追投' else ('daily_budget','optimization_target','bid_value')
                if any(rule.get(k) is None for k in fields):misses.append('追投创建参数不完整')
                recent_history=[e for e in history if isinstance(e,dict) and e.get('state') in {'draft','pending','pending_confirmation','approved','claimed','submitting','unknown','submitted_pending_verification'}]
                recent=_recent(recent_history,account_id,_parent(s),s['entity_id'],{'auto_chase'},now,p['safety']['duplicate_guard_minutes'])
                if recent:misses.append(recent)
                if misses:skip('f5',s,list(dict.fromkeys(misses)));continue
                if len(result['proposals'])>=limit:skip('f5',s,['达到本户单轮或单日操作上限']);break
                after={k:rule[k] for k in ('chase_type',)+fields}
                proposal=_proposal(s,'f5','auto_chase',None,after,'素材状态、消耗区间和ROI命中本户配置；创建仍需独立确认和原生适配器')
                proposal['native_dispatch_required']='material_chase_creation';proposal['allow_running_materials']=allow_running;result['proposals'].append(proposal);break
    if p['features']['f6']['enabled']:
        result['diagnostics'].append({'feature_id':'f6','status':'config_only','reason':'原版只证实配置/AI数据包路径，未证实主计划ROI写入链；本轮不把配置保存当作实际ROI调整'})
    result['summary']=f'本户扫描{result["scanned"]}个任务/素材，生成{len(result["proposals"])}条待逐条确认候选，{len(result["skipped"])}条未命中/缺失诊断；未执行广告修改。'
    return result
