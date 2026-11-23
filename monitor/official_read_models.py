"""API display models: report freshness and execution readiness stay separate."""
import copy
from .schema import parse_time
from .delivery_entry import report_entries


def official_account_active(api,aid):
    if api is None:return False
    with api.lock:
        return (aid in api.plan_readings or aid in api.polling or
            any(r.get('account_id')==aid for r in api.readings.values()))


def monitoring_model(account, plan_reading, readings, now):
    entries=set(report_entries(account))
    def timing(row):
        try:age=(now-parse_time(row['captured_at'])).total_seconds()
        except (ValueError,KeyError,TypeError):age=float('inf')
        return {'captured_at':row.get('captured_at'),'data_at':None,'metric_date':row.get('date'),
            'metric_period':'day','metric_basis':'overall_payment','segment':'overall',
            'read_fresh':0<=age<=600,'is_today':row.get('date')==now.date().isoformat(),
            'source_kind':'official_api','coverage':'queried_scope'}
    plans=[]
    for p in plan_reading.get('plans',[]):
        if p['delivery_entry'] not in entries:continue
        m=copy.deepcopy(p['metrics']);m.update(budget=p['budget'],target_roi=p['target_roi'])
        m['payment_orders']=m.get('orders');m['payment_conversion_cost']=m.get('conversion_cost')
        m['official_actual_paid']=m.get('actual_payment_gmv')
        plans.append({**timing(p),'plan_id':p['plan_id'],'name':p['name'],'metrics':m,
            'delivery_entry':p['delivery_entry'],'entry_label':p['entry_label'],
            'plan_status':p.get('status'),'configuration_source':p.get('configuration_source','plan_list'),
            'full_shop_managed':p.get('full_shop_managed',False),
            'metric_status':p['metric_status'],'metrics_source':p['metrics_source'],
            'running':False if p.get('status') in {'DELETE','DELETED','FROZEN'} else p['opt_status']=='ENABLE' if p['opt_status'] in {'ENABLE','DISABLE'} else None,
            'execution_ready':False,'configuration':{'budget':p['budget'],'target_roi':p['target_roi'],
                'roi_basis':p['target_roi_basis'],'captured_at':p['captured_at']},
            'issues':[] if p['metric_status']=='observed' else ['未取得可唯一对应本计划的效果报表；缺值未填零']})
    summaries=[{**timing(r),'metrics':copy.deepcopy(r['metrics']),'entry_label':r['entry_label'],
        'delivery_entry':r['delivery_entry'],'data_status':r['data_status']}
        for r in readings.values() if r.get('account_id')==account['id'] and r.get('delivery_entry') in entries]
    return {'account_id':account['id'],'backend':'official_api','summary':None,'summaries':summaries,
        'summary_by_entry':summaries,'plans':plans,'dates':sorted({p['metric_date'] for p in plans}|{s['metric_date'] for s in summaries}),
        'data_connected':any(p['read_fresh'] for p in plans) or any(s['read_fresh'] for s in summaries),
        'execution_plan_count':0,'coverage_note':'官方API按入口分页读取普通计划，并按报表ID补充计划详情（含全店托管）。无报表记录的托管计划覆盖尚未核验；直播指标按唯一计划与抖音号ID关联。API追投与暂停操作在独立确认区准备；预算、ROI与下播排程均在独立API调控区逐单确认；自动条件仍以实际可用数据为准。'}
