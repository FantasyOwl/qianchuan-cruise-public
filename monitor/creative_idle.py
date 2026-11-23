"""Verify 24h of zero creative activity using separate daily counter ledgers.

Only supplied report evidence is checked. A local finding is not a platform
pause, deletion, credential grant or certification of complete inventory.
"""
import math
import re
from collections import defaultdict
from datetime import timedelta

from .daily_windows import inspect_daily_ledger
from .engine import _time

MATERIAL_GET = '/open_api/v1.0/qianchuan/uni_promotion/ad/material/get/'


def _context(row):
    value=row['raw'].get('context',{})
    return value if isinstance(value,dict) else {}


def inspect_creative_idle(records,rules,now):
    missing=[]
    out={'zero_activity':False,'missing':missing,'latest':None,'evidence':None}
    current=[r for r in records if r['date']==now.date()]
    if not current:
        missing.append('缺少当前统计日的素材观测');return out
    captured=max(r['captured'] for r in current)
    peers=[r for r in current if r['captured']==captured]
    last=peers[-1];out['latest']=last
    clocks={r['data'] for r in peers}
    if None in clocks or len(clocks)!=1:
        missing.append('最新采集的指标时刻缺失或同次矛盾，不能沿用旧记录');return out
    end=last['data']
    if any(r['captured']<captured and r['data'] is not None and r['data']>end for r in current):
        missing.append('最新采集的指标时间回退，不能沿用较旧有利记录');return out
    ages=[(now-end).total_seconds(),(now-captured).total_seconds()]
    if min(ages)<-120 or max(ages)>rules['max_age_seconds']:
        missing.append('最新采集与指标时间均须新鲜且不在未来');return out
    nominal=end-timedelta(hours=24)
    left=[r for r in records if r['data'] is not None and nominal-timedelta(minutes=5)<=r['data']<=nominal]
    if not left:
        missing.append('缺少不晚于24小时起点的实际边界，不将23小时55分算作24小时');return out
    start=max(r['data'] for r in left)
    selected=[r for r in records if r['data'] is not None and start<=r['data']<=end]
    days={r['date'] for r in selected}
    if any(r['data'] is None and r['date'] in days and start<=r['captured']<=captured for r in records):
        missing.append('观察窗口内存在缺失指标时间的采集');return out
    if any(r['source'].get('kind') not in {'import','demo'} or r['source'].get('coverage')!='report'
           or r['source'].get('metric_period')!='day' for r in selected):
        missing.append('需要完整日累计报告，不能用可见行、直播本场或筛选后的素材列表证明零活动')
    if any(_context(r).get('report_endpoint')==MATERIAL_GET for r in selected):
        missing.append('素材列表可能只返回有消耗对象，不作为24小时零消耗台账')
    parent_values=[_context(r).get('parent_plan_id') for r in selected]
    parents={p for p in parent_values if isinstance(p,str)}
    if len(parents)!=1 or any(not isinstance(p,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',p) for p in parent_values):
        missing.append('24小时母计划关系缺失或变化，不能拼接不同对象的台账')
    created={_time(_context(r).get('created_at')) for r in selected}
    if len(created)!=1 or None in created or any(t>start for t in created if t):
        missing.append('素材创建身份须一致且已满实际24小时')
    if any(_context(r).get('creative_type')!='custom' for r in selected):
        missing.append('24小时内创意类型须明确且持续为自定义，程序化模式不等于自选素材')
    if any(r['control'].get('running') is not True for r in selected):
        missing.append('24小时每条观测均须明确处于开启状态')
    for r in selected:
        clicks=r['metrics'].get('clicks')
        if clicks is None or clicks!=int(clicks):
            missing.append('点击累计缺失或不是整数');break
    states={tuple(repr(v) for v in (r['control'].get('history_known'),r['control'].get('guarantee_verified'),
             r['control'].get('last_change_at'),r['control'].get('changes_today'),
             _context(r).get('manual_reviewed'))) for r in peers}
    if len(states)!=1:
        missing.append('最新同次观测的历史、保障或人工核验声明冲突')
    by_day=defaultdict(list)
    for r in selected:by_day[r['date']].append(r)
    for own in by_day.values():
        ordered=sorted(own,key=lambda r:r['captured'])
        for a,b in zip(ordered,ordered[1:]):
            if (a['captured']==b['captured'] and a['data']!=b['data']) or (a['captured']<b['captured'] and a['data']>b['data']):
                missing.append('历史窗口内同次时刻矛盾或指标时间回退');break
        by_time=sorted(own,key=lambda r:r['data'])
        if any(a['metrics'].get(k) is not None and b['metrics'].get(k) is not None
               and b['metrics'][k]<a['metrics'][k] for a,b in zip(by_time,by_time[1:]) for k in ('spend','clicks')):
            missing.append('日内消耗或点击累计回退，不能用抵消后的净差额声称零活动')
    # A zero-spend/click assessment needs no ROI basis or configured ROI target.
    # Remove that one irrelevant issue only from private copies for ledger checks.
    ledger=[{**r,'issues':[x for x in r['issues'] if x!='ROI 口径未确认']} for r in selected]
    ledger_missing,boundaries=inspect_daily_ledger(ledger,['spend','clicks'],rules)
    missing.extend('素材观察台账：'+s.replace('恢复','观察') for s in ledger_missing)
    times={r['data'] for r in selected}
    if len(times)<max(3,math.ceil((end-start).total_seconds()/(rules['max_gap_minutes']*60))+1):
        missing.append('24小时独立时刻样本不足，重复记录不补足时长')
    if missing:
        out['missing']=list(dict.fromkeys(missing));return out
    segments=[]
    for day in sorted(by_day):
        own=sorted(by_day[day],key=lambda r:r['data'])
        deltas={k:own[-1]['metrics'][k]-own[0]['metrics'][k] for k in ('spend','clicks')}
        if any(v<0 for v in deltas.values()):
            missing.append('日内累计回退，不能视为零活动');return out
        segments.append({'metric_date':day.isoformat(),'start':own[0]['data'].isoformat(),
                         'end':own[-1]['data'].isoformat(),'delta':deltas})
    spend=math.fsum(s['delta']['spend'] for s in segments)
    clicks=sum(s['delta']['clicks'] for s in segments)
    out['zero_activity']=spend==0 and clicks==0
    out['evidence']={'observed_start':start.isoformat(),'observed_end':end.isoformat(),
        'observed_seconds':(end-start).total_seconds(),'observations':len(times),
        'cross_day_method':'separate_daily_deltas_with_declared_boundaries','daily_segments':segments,
        'daily_boundaries':boundaries,'spend_delta':spend,'clicks_delta':clicks,
        'parent_plan_id':next(iter(parents)),'platform_verified':False}
    return out
