"""Urgent observations, with explicit latency/state and independent score units.

No account writer. Short-window alerts request investigation; they cannot infer
the cause of a traffic interruption or justify a direct main-plan shutdown.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .analytics import _valid_observation
from .engine import CN, _hour_windows, _number, _record, _settings, _uid, _uses_base
from .schema import now_cn

FEATURES = ('primary_spend_stop','primary_flow_stop','roi_sustained_decline','account_score_watch','reputation_score_watch')


def _ctx(row):
    value=row['raw'].get('context')
    return value if isinstance(value,dict) else {}


def _point_valid(row):
    # Scores/spend observations do not depend on a configured ROI target.
    raw=row['raw'];quality=raw.get('quality',{})
    if not isinstance(quality,dict) or not isinstance(quality.get('issues',[]),list):return False
    projected={**row,'raw':{**raw,'quality':{'issues':[v for v in quality.get('issues',[]) if v!='ROI 口径未确认']}}}
    return _valid_observation(projected)


def _source(row):
    return ':'.join(map(str,row['key'][3:]))


def _conflicts(rows):
    seen={}
    for row in rows:
        if row['data'] in seen and row['metrics']!=seen[row['data']]['metrics']:return True
        seen[row['data']]=row
    return False


def _signal_base(row,signal):
    return _uses_base(row) or signal=='impressions' and row['metrics'].get('base_impressions') is not None


def _stop_observation(records,rules,now,signal='spend'):
    rows=sorted((r for r in records if r['data']),key=lambda r:(r['data'],r['captured']))
    last=rows[-1] if rows else None
    result={'state':'insufficient','missing':[],'triggered':False,'window_minutes':10,
            'signal':signal,'max_gap_minutes':4,'boundary_tolerance_seconds':60,'rule_basis':'local_observation_threshold'}
    missing=result['missing']
    if last is None:
        missing.append('明确数据更新时间');return result
    if (last['date']!=now.date() or last['source'].get('metric_period','day')!='day'
            or not 0<=(now-last['data']).total_seconds()<=min(120,rules['max_age_seconds'])):
        missing.append('当前日累计且更新距今不超过2分钟的数据');return result
    base=_signal_base(last,signal);field=('base_' if base else '')+signal
    minimum=1000 if signal=='impressions' else max(1,rules['min_spend'])
    result['ledger']='base' if base else 'overall_without_control'
    result['metric_field']=field
    result['minimum_baseline_delta']=minimum
    if signal=='spend':result['spend_field']=field
    unique={r['data']:r for r in rows};ordered=[unique[t] for t in sorted(unique)]
    # The last positive increment anchors the last active interval. A flat
    # incident remains open while continuous zero-growth evidence continues.
    movements=[b for a,b in zip(ordered,ordered[1:]) if a['metrics'].get(field) is not None
               and b['metrics'].get(field) is not None and b['metrics'][field]>a['metrics'][field]]
    if not movements:
        missing.append('停滞前可核验的正向基线，不能由一直为零推断突然停止');return result
    anchor=movements[-1]['data'];start=anchor-timedelta(minutes=10)
    left=min(ordered,key=lambda r:abs(r['data']-start))
    if abs(left['data']-start)>timedelta(seconds=60):
        missing.append('停滞前10分钟完整基线边界');return result
    selected=[r for r in rows if left['data']<=r['data']<=last['data']]
    current=[r for r in ordered if anchor<=r['data']<=last['data']]
    before=[r for r in ordered if left['data']<=r['data']<=anchor]
    flat_minutes=(last['data']-anchor).total_seconds()/60
    if len(before)<4 or (flat_minutes>=10 and len(current)<4):
        missing.append('基线和停耗段各至少4个独立数据时间');return result
    if any(r['data'] is None and r['captured']>=left['captured'] for r in records):
        missing.append('相关时段存在无数据时间的记录')
    if _conflicts(selected):missing.append('同一数据时间存在冲突值')
    unique_selected=[r for r in ordered if left['data']<=r['data']<=last['data']]
    if any((b['data']-a['data']).total_seconds()>240 for a,b in zip(unique_selected,unique_selected[1:])):
        missing.append('基线至当前存在超过4分钟采集断档')
    if any(not _point_valid(r) for r in selected):missing.append('窗口包含数据时间、质量或周期问题')
    if any(_signal_base(r,signal)!=base or r['metrics'].get(field) is None for r in selected):
        missing.append('基础账与总体账混用或缺少明确指标')
    if not base and any(r['metrics'].get('control_spend')!=0 for r in selected):
        missing.append('总体消耗中追加投入范围未知，需基础消耗或明确追加投入为零')
    for row in selected:
        ctx=_ctx(row)
        active=ctx.get('live_ongoing') if row['raw'].get('scene')=='live' else ctx.get('delivery_expected') if row['raw'].get('scene')=='product' else None
        if (row['control'].get('running') is not True or active is not True
                or ctx.get('maintenance_active') is not False or ctx.get('low_latency_verified') is not True
                or ctx.get('report_granularity')!='minute'):
            missing.append('全窗口明确开启、在播或预期投放时段、非维护及已核验分钟低延迟来源')
            break
        if not 0<=(row['captured']-row['data']).total_seconds()<=120:
            missing.append('窗口包含延迟超过2分钟或数据时间在采集之后的记录');break
    if missing:return result
    if any(b['metrics'][field]<a['metrics'][field] for a,b in zip(unique_selected,unique_selected[1:])):
        missing.append('累计指标回退或重置');return result
    baseline=before[-1]['metrics'][field]-before[0]['metrics'][field]
    positive=sum(b['metrics'][field]>a['metrics'][field] for a,b in zip(before,before[1:]))
    if baseline<minimum or positive<3:
        missing.append('停滞前指标总量或正向增量次数不足以构成稳定活跃基线');return result
    result.update(state='observed',baseline_start=left['data'].isoformat(),
                  flat_from=anchor.isoformat(),flat_minutes=flat_minutes,data_at=last['data'].isoformat())
    result['baseline_'+signal]=baseline
    if flat_minutes<10:
        result['note']='近期仍有指标增长，不满足持续至少10分钟停滞条件';return result
    if any(r['metrics'][field]!=current[0]['metrics'][field] for r in current):return result
    impressions='base_impressions' if base else 'impressions'
    values=[r['metrics'].get(impressions) for r in current]
    flow='also_flat' if all(v is not None for v in values) and len(set(values))==1 else 'not_proven_flat'
    result.update(triggered=True,state='triggered',flow_observation=flow,
                  sample_count=len(current),cause_verified=False)
    result['flat_'+signal+'_delta']=0
    return result


def analyze_emergency(snapshots,settings,now=None):
    if not isinstance(now,datetime):now=now_cn()
    now=now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)
    rules,accounts=_settings(settings);groups=defaultdict(list);rejected=0
    for raw in snapshots if isinstance(snapshots,list) else []:
        try:row=_record(raw,accounts,now)
        except (TypeError,ValueError,OverflowError,AttributeError):row=None
        if row is None:rejected+=1
        elif (not isinstance(row['source'].get('metric_period','day'),str)
              or row['source'].get('metric_period','day') not in {'day','live_session','unknown'}
              or not (row['source'].get('period_start') is None or isinstance(row['source']['period_start'],str))):
            rejected+=1
        else:groups[row['key']].append(row)
    coverage={name:{'evaluated_entities':0,'triggered':0,'missing':[]} for name in FEATURES}
    score_history=defaultdict(list)
    for key,rs in groups.items():
        if key[2]=='account':
            for row in rs:
                score_history[(key[:-1],row['source'].get('metric_period','day'),row['source'].get('period_start'))].append(row)
    observations=[];alerts=[];recommendations=[]
    for key,records in sorted(groups.items(),key=lambda p:tuple(map(str,p[0]))):
        rows=sorted(records,key=lambda r:(r['data'] or r['captured'],r['captured']));last=rows[-1]
        raw=last['raw'];scope=raw['scope'];ctx=_ctx(last);source_key=_source(last)
        identity=(raw['account_id'],raw['entity_id'],scope,source_key)
        fresh=bool(last['data'] and 0<=(now-last['data']).total_seconds()<=rules['max_age_seconds']
                   and _point_valid(last))
        item={'account_id':raw['account_id'],'entity_id':raw['entity_id'],'scope':scope,'source_key':source_key,
              'snapshot_id':raw.get('id'),'metric_basis':last['basis'],'data_at':last['source'].get('data_at'),
              'is_current':fresh,'scores':[]}
        def cover(name,missing=(),triggered=False):
            c=coverage[name];c['evaluated_entities']+=1;c['triggered']+=int(triggered)
            if missing:c['missing'].append({'account_id':raw['account_id'],'entity_id':raw['entity_id'],'source_key':source_key,'fields':list(missing)})
        def alert(rule,priority,title,evidence,risk):
            value={'id':_uid(*identity,rule),'account_id':raw['account_id'],'entity_id':raw['entity_id'],'scope':scope,
                   'source_key':source_key,'source_basis':last['basis'],'rule':rule,'priority':priority,'title':title,
                   'evidence':evidence,'risk':risk,'rule_basis':'user_strategy'}
            alerts.append(value);return value
        if scope=='plan' and ctx.get('is_primary_plan') is True:
            stop=_stop_observation(rows,rules,now);item['primary_spend_stop']=stop
            flow=_stop_observation(rows,rules,now,'impressions');item['primary_flow_stop']=flow
            cover('primary_spend_stop',stop['missing'],stop['triggered'])
            cover('primary_flow_stop',flow['missing'],flow['triggered'])
            risk='10分钟窗口、4分钟最大断档、2分钟时效及展示基线至少1000次为本地观测保护线；人工声明非平台认证，需排查开播/投放时段、预算、审核与数据延迟，不直接启停主力计划。'
            if stop['triggered']:
                alert('primary_spend_stop','P0','主力计划累计消耗持续不增长，需立即核验',stop,risk)
            if flow['triggered']:
                alert('primary_flow_stop','P0','主力计划展示累计量持续不增长，需立即核验流量',flow,risk)
            if stop['triggered'] or flow['triggered']:
                rule='primary_spend_stop' if stop['triggered'] else 'primary_flow_stop'
                evidence={'spend':stop,'impressions':flow}
                recommendations.append({'id':_uid(*identity,rule,'inspect_recovery'),'account_id':raw['account_id'],
                    'entity_id':raw['entity_id'],'scope':scope,'source_key':source_key,'metric_basis':last['basis'],
                    'target_id':raw['entity_id'],'target_type':'plan','priority':'P0','rule':rule,
                    'action':'inspect_recovery','action_label':'立即核验主力计划消耗/展示停滞原因','reason':'分钟级连续观测满足活跃基线后持续停滞条件',
                    'expected_goal':'核对开播/投放时段、预算、审核与数据延迟，恢复连续观测和正常消耗',
                    'change_pct':None,'before':None,'after':None,'parameters':{},'evidence':evidence,'risk':risk,
                    'eligible':True,'blockers':[],'mode':'simulation_only','rule_basis':'local_observation_threshold'})
        elif scope=='plan' and ctx.get('is_primary_plan') is not False:
            item['primary_spend_stop']={'state':'insufficient','triggered':False,'missing':['明确主力计划标签，不能由最大预算或计划名称推断']}
            item['primary_flow_stop']=dict(item['primary_spend_stop'])
            cover('primary_spend_stop',item['primary_spend_stop']['missing'])
            cover('primary_flow_stop',item['primary_flow_stop']['missing'])
        if scope=='account':
            target=_number(accounts[raw['account_id']].get('target_roi'));missing=[]
            if not fresh or last['date']!=now.date() or last['source'].get('metric_period','day')!='day':missing.append('同日新鲜日累计账户数据')
            if target is None or target<=0 or accounts[raw['account_id']].get('roi_basis')!=last['basis'] or last['basis'] in {'unknown','settled_7d','control_payment'}:
                missing.append('明确同口径账户目标ROI')
            windows,_=_hour_windows(rows,rules)
            if not (len(windows)==3 and all(w['complete'] and w.get('roi') is not None and w.get('spend') is not None
                    and w['spend']>=rules['min_spend'] and w.get('orders') is not None and w['orders']>=rules['min_orders'] for w in windows)):
                missing.append('三个完整小时的同口径金额、成本和订单样本')
            drops=all(a['roi']-b['roi']>max(1e-8,abs(a['roi'])*1e-8) for a,b in zip(windows,windows[1:])) if not missing else False
            decline=not missing and drops and windows[2]['roi']<target*rules['roi_low_ratio']
            item['roi_trend']={'state':'triggered' if decline else 'insufficient' if missing else 'observed',
                               'hourly':windows,'missing':missing,'sustained_decline':bool(decline)}
            cover('roi_sustained_decline',missing,bool(decline))
            if decline:alert('roi_sustained_decline','P1','账户小时ROI连续下滑且跌破用户目标策略线',
                {'hourly':windows,'target_roi':target,'low_ratio':rules['roi_low_ratio']},'小时趋势需核验归因和延迟，不能由累计日ROI或仅连续低值推断持续下降。')
            for field,floor_field,name in (('account_score','account_score_threshold','账户分值'),('reputation_score','reputation_threshold','口碑分')):
                value=last['metrics'].get(field);floor=last['metrics'].get(floor_field);missing=[]
                if not fresh:missing.append('明确当前数据时间和可用质量')
                if value is None:missing.append('metrics.'+field)
                if floor is None or ctx.get('score_thresholds_verified') is not True:missing.append('用户阈值及分值/阈值同单位核验')
                if _conflicts([r for r in rows if r['data']==last['data']]):missing.append('当前同一数据时间存在冲突值')
                score_rows=score_history[(key[:-1],last['source'].get('metric_period','day'),last['source'].get('period_start'))]
                previous=[r for r in score_rows if r['data'] and last['data'] and last['data']-timedelta(hours=24)<=r['data']<last['data']
                          and _point_valid(r) and r['metrics'].get(field) is not None]
                prior=max(previous,key=lambda r:(r['data'],r['captured'])) if previous else None
                if prior and _conflicts([r for r in previous if r['data']==prior['data']]):prior=None
                change=value-prior['metrics'][field] if value is not None and prior else None
                if change is not None and abs(change)<1e-8:change=0.0
                triggered=not missing and value<=floor
                score={'field':field,'label':name,'value':value,'user_threshold':floor,'previous_value':prior['metrics'][field] if prior else None,
                       'previous_at':prior['data'].isoformat() if prior else None,'change':change,
                       'state':'triggered' if triggered else 'insufficient' if missing else 'observed','missing':missing,
                       'threshold_authority':'user_declared_same_unit','platform_policy_verified':False}
                item['scores'].append(score);cover('account_score_watch' if field=='account_score' else 'reputation_score_watch',missing,triggered)
                if triggered:
                    title=f'{name}'+('下降至' if change is not None and change<0 else '处于')+'用户预警阈值或以下'
                    alert(field+'_threshold','P1',title,score,'阈值和单位由用户报告声明，不代表官方封禁/赔付线；需核对来源及评分周期。')
        if item.get('primary_spend_stop') or scope=='account':observations.append(item)
    for entry in coverage.values():
        entry['state']='triggered' if entry['triggered'] else 'insufficient' if entry['missing'] else 'observed' if entry['evaluated_entities'] else 'not_applicable'
    return {'observations':observations,'alerts':alerts,'recommendations':recommendations,
            'coverage':{'emergency':coverage,'rejected_count':rejected,'execution_enabled':False,
                        'limits':['主力停耗只要求人工核验和阻断增量放量，不依据短窗口直接启停。',
                                  '分钟/在播/非维护/低延迟与评分阈值由明确元数据声明，不能从页面标题推断。']}}
