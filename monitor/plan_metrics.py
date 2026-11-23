"""Read models for the trading console, separate from execution capabilities."""
from .schema import parse_time

DEFINITIONS = [
    ('spend','整体消耗','元'),('roi','整体支付ROI','倍'),('net_roi','净成交ROI','倍'),
    ('marketing_roi','综合营销ROI','倍'),('comprehensive_cost','综合成本','元'),
    ('gmv','整体成交金额','元'),('net_gmv','净成交金额','元'),('settled_gmv','7日结算金额','元'),
    ('settled_roi','7日结算ROI','倍'),('payment_orders','整体成交订单数','单'),
    ('net_orders','净成交订单数','单'),('orders','当前口径订单数','单'),
    ('official_actual_paid','用户实际支付金额','元'),('official_actual_net_paid','用户实际支付净成交金额','元'),
    ('net_settlement_rate','净成交金额结算率','%'),
    ('payment_conversion_cost','整体成交订单成本','元'),('net_conversion_cost','净成交订单成本','元'),
    ('conversion_cost','当前口径订单成本','元'),('refund_rate_1h','1小时退款率','%'),
    ('refund_rate_pre_ship','发货前退款率','%'),('impressions','展示次数','次'),('clicks','点击次数','次'),
    ('ctr','点击率','%'),('cvr','转化率','%'),('cpm','千次展示成本','元'),('ecpm','eCPM','元'),
    ('gpm','广告GPM','元'),('deep_conversion_cost','深度转化成本','元'),
    ('balance','账户余额','元'),('budget','日预算','元'),('target_roi','计划ROI目标','倍'),
    ('net_roi_goal','净成交ROI目标','倍'),('payment_roi_goal','支付ROI目标','倍'),
    ('base_spend','基础消耗','元'),('base_gmv','基础成交金额','元'),('base_net_gmv','基础净成交金额','元'),
    ('base_roi','基础支付ROI','倍'),('base_net_roi','基础净成交ROI','倍'),
    ('base_marketing_roi','基础综合营销ROI','倍'),('base_comprehensive_cost','基础综合成本','元'),
    ('base_orders','基础当前口径订单数','单'),('online_users','在线人数','人'),('viewers','观看人数','人'),
]
METRIC_CATALOG = [{'key':k,'label':label,'unit':unit} for k,label,unit in DEFINITIONS]
PERFORMANCE = {k for k,_,_ in DEFINITIONS} - {'budget','target_roi','net_roi_goal','payment_roi_goal','balance'}


def _at(row):
    try:return parse_time(row['source']['captured_at']).timestamp()
    except (ValueError,KeyError,TypeError):return float('-inf')


def _read_model(s, now):
    src=s.get('source',{});age=now.timestamp()-_at(s)
    return {'metrics':dict(s.get('metrics',{})), 'metric_basis':s.get('metric_basis','unknown'),
        'metric_date':src.get('metric_date'), 'metric_period':src.get('metric_period','unknown'),
        'period_start':src.get('period_start'),'segment':src.get('segment','overall'),
        'captured_at':src.get('captured_at'),'data_at':src.get('data_at'),
        'source_snapshot_id':s.get('id'),'source_kind':src.get('kind'),'read_fresh':0<=age<=180,
        'is_today':src.get('metric_date')==now.date().isoformat(),
        'coverage':src.get('coverage','visible_only'),
        'plan_view':s.get('context',{}).get('observed_plan_view'),
        'issues':list(s.get('quality',{}).get('issues',[])),
        'running':s.get('control',{}).get('running')}


def account_metrics(account, snapshots, surfaces, now):
    aid=account['id']
    rows=[s for s in snapshots if s.get('account_id')==aid
          and s.get('source',{}).get('kind')!='demo']
    by_query={}
    # Never combine across date, period, basis, overall/base, or live session.
    for s in rows:
        if s.get('scope') not in {'plan','account'}:continue
        src=s.get('source',{});segment=src.get('segment','overall')
        if segment=='plan_operation_log':continue
        performance=any(k in PERFORMANCE and v is not None for k,v in s.get('metrics',{}).items())
        key=(s['scope'],s['entity_id'],src.get('metric_date'),src.get('metric_period'),
             src.get('period_start'),s.get('metric_basis') if s['scope']=='account' else None,
             s.get('context',{}).get('observed_plan_view') if s['scope']=='account' else None,
             segment,'performance' if performance else 'configuration')
        old=by_query.get(key)
        if old is None or _at(s)>_at(old):by_query[key]=s
        elif _at(s)==_at(old) and s.get('metrics')!=old.get('metrics'):
            # Retain the conflict visibly rather than resolving by row order.
            by_query[key]={**old,'metrics':{},'quality':{'issues':['同一时刻的指标观察存在冲突，未选取数值']}}
    plan_rows=[];summaries=[]
    for s in by_query.values():
        model=_read_model(s,now)
        if s['scope']=='account':
            summaries.append(model);continue
        if not any(k in PERFORMANCE and v is not None for k,v in s.get('metrics',{}).items()):continue
        plan_rows.append({**model,'plan_id':s['entity_id'],
            'name':s.get('display_name') or s.get('name') or '计划 '+s['entity_id']})
    fresh_surfaces={s['plan_id']:s for s in surfaces if s.get('account_id')==aid}
    # Config-only observations have their own source and do not erase a metric
    # row. Native configuration remains the sole source of executable controls.
    for pid,s in fresh_surfaces.items():
        existing=[p for p in plan_rows if p['plan_id']==pid]
        for p in existing:
            p['execution_ready']=True
        if not existing:
            plan_rows.append({'plan_id':pid,'name':s['name'],'metrics':{},'metric_basis':'unknown',
                'metric_date':None,'metric_period':'unknown','captured_at':s['captured_at'],
                'data_at':None,'read_fresh':True,'is_today':False,'segment':'configuration',
                'running':s['running'],'execution_ready':True,'issues':['目前只有计划配置，尚无明确统计范围的业绩数据']})
    for p in plan_rows:
        native=fresh_surfaces.get(p['plan_id'])
        p['configuration']={'budget':native.get('budget'),'target_roi':native.get('target_roi'),
            'roi_basis':native.get('roi_basis'),'captured_at':native.get('captured_at')} if native else None
        p.setdefault('execution_ready',False)
    summaries.sort(key=lambda s:(s['is_today'],bool(PERFORMANCE&set(s['metrics'])),parse_time(s['captured_at']).timestamp()),reverse=True)
    summary=summaries[0] if summaries else None
    dates=sorted({p['metric_date'] for p in plan_rows if p.get('metric_date')},reverse=True)
    return {'account_id':aid,'summary':summary,'summaries':summaries,'plans':plan_rows,'dates':dates,
        'data_connected':bool(summary and summary['read_fresh'] or any(p['read_fresh'] and p['segment']!='configuration' for p in plan_rows)),
        'execution_plan_count':len(fresh_surfaces),
        'coverage_note':'仅已读取的渲染行；隐藏列、未加载分页或平台未提供的指标保持空白。'}
