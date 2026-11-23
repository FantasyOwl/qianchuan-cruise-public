"""Positive nearby status observations, never proof of account-wide clearance."""
from collections import defaultdict
from itertools import combinations

from .engine import _time,_uid

MUTEX_RULES={'aweme_universal_conflict','live_universal_standard_conflict','live_plan_status_conflict'}
STATUS_NEUTRAL={
    'ROI 口径未确认','统计日期不是今日，仅供历史观察','数据时间与统计日期不一致',
    '缺少页面数据更新时间，不能用采集时间代替','缺少页面数据更新时间，采集时间不能替代数据时间',
    '统计日期与采集日期不一致','非日累计统计周期，仅作独立观察',
    '非日累计统计周期，仅作独立观察，不参与日级小时调控',
    '仅采集可见汇总，未覆盖分页计划、素材、退款及库存全量数据',
    '仅当前渲染计划行，未翻页且未核验全量及操作历史',
    '基础与总体台账分别保存，未核验范围前不通过相减推算调控台账',
}


def _id(value):return value if isinstance(value,str) and 0<len(value)<=150 else None


def inspect_live_exclusion(groups,rules,now):
    heads=defaultdict(list)
    for key,records in groups.items():
        if key[2]!='plan':continue
        last=max(records,key=lambda r:r['captured']);raw=last['raw'];ctx=raw.get('context',{})
        ctx=ctx if isinstance(ctx,dict) else {}
        goal=ctx.get('plan_marketing_goal')
        goal=goal if isinstance(goal,str) and goal in {'LIVE_PROM_GOODS','VIDEO_PROM_GOODS','unknown'} else 'unknown'
        if raw.get('scene')!='live' and goal!='LIVE_PROM_GOODS':continue
        enabled=last['control'].get('running');enabled=enabled if type(enabled) is bool else None
        explicit='plan_status_at' in ctx
        at=_time(ctx.get('plan_status_at')) if explicit else last['captured']
        # Legacy snapshots require fresh performance evidence as well. An
        # explicit status clock never changes or fills the performance clock.
        clock_valid=at is not None and abs((at-last['captured']).total_seconds())<=1
        if not explicit:
            clock_valid=clock_valid and last['data'] is not None and last['date']==now.date() and not last['issues']
            if clock_valid:clock_valid=-120<=(now-last['data']).total_seconds()<=rules['max_age_seconds']
        fresh=bool(clock_valid and -120<=(now-at).total_seconds()<=rules['max_age_seconds']
                   and not any(v not in STATUS_NEUTRAL for v in last['issues']))
        plan=_id(ctx.get('plan_id')) or raw['entity_id'];aweme=_id(ctx.get('aweme_id'))
        delivery=ctx.get('plan_delivery_state','unknown')
        delivery=delivery if isinstance(delivery,str) and delivery in {'delivering','system_paused','not_delivering','unknown'} else 'unknown'
        missing=[]
        if not fresh:missing.append('缺少当前有效的独立状态观测时间或新鲜快照')
        if not aweme:missing.append('明确抖音号ID，不能按账户名或计划名匹配')
        if goal!='LIVE_PROM_GOODS' or raw.get('scene')!='live':
            missing.append('明确直播营销目标及场景，不能套用到推商品')
        if raw.get('promotion_type') not in {'universal','standard'}:missing.append('未明确为直播全域或标准产品；不外推乘方/搜索/调控工具')
        if enabled is None:missing.append('当前计划开关状态未明确')
        if enabled is False and delivery=='delivering':missing.append('开关关闭与实际投放声明冲突')
        heads[(raw['account_id'],plan)].append({'account_id':raw['account_id'],'entity_id':raw['entity_id'],'plan_id':plan,
            'source_key':':'.join(map(str,key[3:])),'snapshot_id':raw.get('id'),'aweme_id':aweme,
            'scene':raw.get('scene'),
            'promotion_type':raw.get('promotion_type'),'plan_marketing_goal':goal,
            'enabled':enabled,'delivery_state':delivery,'status_at':at.isoformat() if at else None,
            'performance_data_at':last['data'].isoformat() if last['data'] else None,
            'status_clock_basis':'explicit_status_observation' if explicit else 'fresh_snapshot_capture',
            'captured_at':last['captured'].isoformat(),'is_current':fresh,'missing':missing,'state':'insufficient',
            'overlap':'none_observed','aligned_peer_count':0,'platform_verified':False,
            'note':'状态与业绩时间分别保存；近时点声明不证明实际同时投放，没有冲突也不认证账户全量互斥已满足。',
            '_row':last,'_status_supplied':explicit or enabled is not None})
    observations=[];alerts=[];recommendations=[]
    def listed(plans):
        return [{k:p[k] for k in ('account_id','plan_id','promotion_type','enabled','delivery_state','status_at')} for p in plans]
    def alert(rule,title,plans,aweme=None,pairs=0):
        plans=sorted(plans,key=lambda p:(p['account_id'],p['plan_id']));first=plans[0]
        value={'id':_uid(rule,aweme,*[(p['account_id'],p['plan_id']) for p in plans]),'priority':'P1','rule':rule,
            'account_id':first['account_id'],'entity_id':first['entity_id'],'scope':'plan','title':title,
            'source_key':first['source_key'],'source_basis':first['_row']['basis'],'rule_basis':'user_strategy',
            'evidence':{'observation_kind':'plan_status','aweme_id':aweme,'observed_plans':listed(plans),'aligned_pair_count':pairs,
                'alignment_method':'每一冲突对的状态观测相差不超过120秒（本地观察窗口）','platform_verified':False},
            'risk':'官方课程为有限直播场景资料，当前产品版本和实际互斥状态未认证；不开启、暂停或恢复任何账户计划。'}
        alerts.append(value);return value
    for identity,values in sorted(heads.items()):
        supplied=[v for v in values if v['_status_supplied']]
        current=[v for v in supplied if v['is_current']]
        pool=current or supplied or values
        latest=max(pool,key=lambda v:v['captured_at'])
        peers=[v for v in current if abs((_time(v['status_at'])-_time(latest['status_at'])).total_seconds())<=1]
        fields=('aweme_id','scene','promotion_type','plan_marketing_goal','enabled','delivery_state')
        def known(value):return value is not None and value!='unknown'
        # Missing fields are not contrary observations. Among coincident
        # partial reads retain the most informative actual source, while any
        # two different known values still block the whole plan.
        conflict=any(len({p[k] for p in peers if known(p[k])})>1 for k in fields)
        if peers:latest=max(peers,key=lambda p:(sum(known(p[k]) for k in fields),p['captured_at']))
        conflict=conflict or (latest['enabled'] is False and latest['delivery_state']=='delivering')
        if conflict:
            latest['missing'].append('同一状态时点存在来源冲突，不能只选开启或关闭的一份')
            latest['state']='status_conflict'
            evidence=[{k:p.get(k) for k in ('account_id','plan_id','snapshot_id','source_key','aweme_id','promotion_type',
                'plan_marketing_goal','enabled','delivery_state','status_at')} for p in (peers or [latest])]
            latest['conflicting_sources']=evidence
            notice=alert('live_plan_status_conflict','同计划当前开关、产品或抖音号信息冲突',[latest],latest['aweme_id'])
            notice['evidence']['conflicting_sources']=evidence
        elif not latest['missing']:
            latest['state']='enabled' if latest['enabled'] else 'disabled'
            if latest['enabled'] and latest['delivery_state'] in {'system_paused','not_delivering'}:latest['state']=latest['delivery_state']
        observations.append(latest)
    by_aweme=defaultdict(list)
    for p in observations:
        if p['state'] in {'enabled','system_paused','not_delivering'}:by_aweme[p['aweme_id']].append(p)
    for aweme,plans in by_aweme.items():
        universal={};mixed={};universals_pairs=0;mixed_pairs=0
        for a,b in combinations(plans,2):
            products={a['promotion_type'],b['promotion_type']}
            if products not in ({'universal'},{'universal','standard'}):continue
            if abs((_time(a['status_at'])-_time(b['status_at'])).total_seconds())>120:
                for p in (a,b):p['missing'].append('同抖音号另有状态观测但相差超过120秒，不拼成同时状态')
                continue
            for p in (a,b):p['aligned_peer_count']+=1
            if products=={'universal'}:
                universals_pairs+=1
                for p in (a,b):
                    universal[(p['account_id'],p['plan_id'])]=p
                    if p['overlap']=='none_observed':p['overlap']='multiple_universal_switches'
            else:
                standard=a if a['promotion_type']=='standard' else b
                if standard['delivery_state'] in {'system_paused','not_delivering'}:
                    standard['overlap']='system_pause_observed' if standard['delivery_state']=='system_paused' else 'delivery_not_active'
                    continue
                mixed_pairs+=1
                for p in (a,b):mixed[(p['account_id'],p['plan_id'])]=p
                overlap='delivery_overlap_declared' if a['delivery_state']==b['delivery_state']=='delivering' else 'enabled_overlap_unverified'
                for p in (a,b):
                    if p['overlap']!='delivery_overlap_declared':p['overlap']=overlap
        if universal:alert('aweme_universal_conflict','同抖音号近时点观察到多个开启的直播全域计划',list(universal.values()),aweme,universals_pairs)
        if mixed:
            plans=list(mixed.values());notice=alert('live_universal_standard_conflict','同抖音号近时点观察到全域与直播标准开启状态重叠',plans,aweme,mixed_pairs)
            for p in plans:
                if p['promotion_type']!='standard':continue
                declared=p['overlap']=='delivery_overlap_declared'
                recommendations.append({'id':_uid('live-exclusion-review',aweme,p['account_id'],p['plan_id']),
                    'priority':'P1','account_id':p['account_id'],'entity_id':p['entity_id'],'scope':'plan','source_key':p['source_key'],
                    'metric_basis':p['_row']['basis'],'target_type':'plan_status','target_id':p['plan_id'],
                    'action':'pause_plan' if declared else 'review_live_exclusion','action_label':'复核暂停冲突的直播标准计划' if declared else '核对直播标准计划的全域互斥状态',
                    'before':True if declared else None,'after':False if declared else None,'change_pct':None,
                    'parameters':{'aweme_id':aweme,'observed_standard_switch':True,'observed_delivery_state':p['delivery_state']},
                    'evidence':notice['evidence'],'reason':'附近观察窗口有全域与直播标准开关重叠，先核对平台系统暂停及实际投放状态',
                    'expected_goal':'消除同直播间重叠投放风险，保留明确的互斥复核记录','risk':notice['risk'],
                    'eligible':False,'blockers':['仅观察；当前平台自动互斥状态及直播标准写合同未认证，不能把开关重叠直接当作已证明违规']+
                        ([] if p['_row']['raw'].get('context',{}).get('is_primary_plan') is False else ['主力角色未知或为主力计划，须人工核对，不直接获得暂停资格']),
                    'mode':'simulation_only','rule_basis':'user_strategy','rule':'live_universal_standard_conflict'})
    for value in observations:value.pop('_row');value.pop('_status_supplied')
    return {'observations':observations,'alerts':alerts,'recommendations':recommendations}
