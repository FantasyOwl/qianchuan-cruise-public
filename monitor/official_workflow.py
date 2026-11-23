"""Account/parent-scoped API observations and immutable batch candidates."""
import copy
import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal
from .official_api import exact_id, ACCOUNT_FIELDS
from .official_monitoring import pages, native, config, field_mapping
from .schema import now_cn, parse_time, numeric
from .batch_chase import date_range
from .cruise_rules import _in_window
from .official_contracts import chase_contract

TASK_FIELDS = {
    'stat_cost_for_roi2_assist':'spend',
    'total_prepay_and_pay_order_roi2_assist':'roi',
    'total_pay_order_count_for_roi2_assist':'orders',
    'total_pay_order_gmv_include_coupon_for_roi2_assist':'gmv',
    'total_prepay_and_pay_settle_roi2_1h_assist':'net_roi',
}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def task_snapshots(workflow):
    result=[]
    for t in workflow['tasks']:
        result.append({'id':'api:'+workflow['captured_at']+':'+t['id'],'account_id':workflow['account_id'],
            'entity_id':t['id'],'scope':'control','scene':'live','metric_basis':'control_payment',
            'source':{'kind':'official_api','captured_at':workflow['captured_at'],'metric_date':workflow['date'],
                'parent_entity_id':workflow['plan_id'],'data_at':t.get('metrics_window',{}).get('end_at')},
            'context':{'parent_plan_id':workflow['plan_id'],'control_type':'material_chase' if t['scene']=='MATERIAL_ADD_BUDGET' else 'one_click_boost',
                'control_delivery_mode':'cost_control' if t['smart_bid_type']=='SMART_BID_CUSTOM' else 'volume' if t['smart_bid_type']=='SMART_BID_CONSERVATIVE' else 'unknown',
                'observed_duration_mode':'fixed' if t['duration'] is not None and 0.5<=t['duration']<=24 else 'unknown',
                'observed_duration_hours':t['duration'],'created_at':t['create_time'],
                'lifetime_spend_verified':bool(t.get('lifetime_spend_window')),
                'lifetime_spend_start':t.get('lifetime_spend_window',{}).get('start_at'),
                'lifetime_spend_end':t.get('lifetime_spend_window',{}).get('end_at')},
            'control':{'running':t['status']=='PROCESSING'},'metrics':{**t['metrics'],'budget':t['budget']}})
    return result


def parent(api, account, pid, *, require_fresh=True):
    pid=exact_id(pid);reading=api.plan_readings.get(account['id'],{})
    rows=[p for p in reading.get('plans',[]) if p['plan_id']==pid]
    if len(rows)!=1:raise ValueError('该计划不在本户唯一API计划清单中，请先刷新计划')
    p=copy.deepcopy(rows[0]);at=now_cn()
    if require_fresh and (reading.get('date')!=at.date().isoformat() or not 0<=(at-parse_time(p['captured_at'])).total_seconds()<=600):
        raise ValueError('本户API计划清单已过期，请先刷新')
    from .delivery_entry import report_entries
    if p['delivery_entry'] not in report_entries(account):raise ValueError('计划入口不在本户当前选择范围')
    if p.get('status') in {'DELETE','DELETED','FROZEN'}:raise ValueError('不能对已删除或冻结计划准备调控')
    return p


def cached_parent(api,account,pid):
    """Local review only; no reader lock or network. Never authorizes a write."""
    if not api.lock.acquire(blocking=False):raise ValueError('正在更新本地API连接状态，本次未生成新单；请稍后重试。')
    try:p=parent(api,account,pid,require_fresh=False)
    finally:api.lock.release()
    captured=parse_time(p['captured_at'])
    if captured>now_cn():raise ValueError('计划缓存时间异常，未生成核对单')
    if p.get('configuration_source')!='plan_list':raise ValueError('尚无本计划的原生配置，请先读取账户计划')
    return p


def fresh_parent(api,account,pid):
    """Refresh stale native configuration rather than asking the user to click."""
    at=now_cn()
    with api.read_lock:
        reading=api.plan_readings.get(account['id'],{})
        candidates=[p for p in reading.get('plans',[]) if p['plan_id']==pid]
        saved=getattr(api,'configuration_readings',{}).get(account['id']+':'+pid,{})
        observed=saved.get('configuration',{})
        if (saved and len(candidates)==1 and saved.get('grant_id')==api.grant_for_account(account['id'])
                and observed.get('account_id')==account['id'] and observed.get('plan_id')==pid
                and observed.get('configuration_source')=='plan_list'
                and observed.get('delivery_entry')==candidates[0].get('delivery_entry')
                and observed.get('smart_bid_type')==candidates[0].get('smart_bid_type')):
            try:recent=observed.get('date')==at.date().isoformat() and 0<=(at-parse_time(observed['captured_at'])).total_seconds()<=600
            except (ValueError,TypeError,KeyError):recent=False
            if recent:
                from .delivery_entry import report_entries
                if observed['delivery_entry'] not in report_entries(account) or observed.get('status') in {'DELETE','DELETED','FROZEN'}:
                    raise ValueError('本户当前入口或计划状态不支持调控')
                api.configuration_cache_hits+=1
                return copy.deepcopy(observed)
        try:fresh=reading.get('date')==at.date().isoformat() and len(candidates)==1 and 0<=(at-parse_time(candidates[0]['captured_at'])).total_seconds()<=600
        except (ValueError,TypeError,KeyError):fresh=False
        if not fresh:
            known=candidates[0] if len(candidates)==1 else None
            if known and known.get('configuration_source')=='plan_list':
                # A registered parent needs a fresh native family/goal/budget,
                # not four family lists plus all account performance reports.
                # Do not publish this as a fresh full account/effect reading.
                result=api.read_plans(account,api.grant_for_account(account['id']),configuration={
                    'plan_id':pid,'delivery_entry':known['delivery_entry'],'smart_bid_type':known['smart_bid_type']})
                if result.get('configuration'):
                    p=result['configuration']
                    from .delivery_entry import report_entries
                    if p['delivery_entry'] not in report_entries(account) or p.get('status') in {'DELETE','DELETED','FROZEN'}:
                        raise ValueError('本户当前入口或计划状态不支持调控')
                    return p
            else:api.read_plans(account,api.grant_for_account(account['id']))
        return parent(api,account,pid)


def active_tasks(api, account, p, token, at, *, task_id=None,task_scene=None,task_status='PROCESSING'):
    values=[];seen=set()
    statuses={'DELETED','DISABLE','OFFLINE_BALANCE','OFFLINE_BUDGET','OFFLINE_TIME','PROCESSING','ROI2_DISABLE','FROZEN'}
    if task_status is not None and task_status not in statuses:raise ValueError('任务状态读取范围无效')
    if task_scene is not None and task_scene not in {'SMART_BOOST','MATERIAL_ADD_BUDGET'} or task_id is not None and task_scene is None:raise ValueError('任务读取缺少合法工具类型')
    for scene in ([task_scene] if task_scene is not None else ('SMART_BOOST','MATERIAL_ADD_BUDGET')):
        query={'advertiser_id':int(account['id']),'ad_id':int(p['plan_id']),
            'marketing_goal':'LIVE_PROM_GOODS' if account['scene']=='live' else 'VIDEO_PROM_GOODS',
            'scene':scene,'fields':list(TASK_FIELDS),'filtering':{} if task_status is None else {'task_status':task_status},
            'start_time':at.strftime('%Y-%m-%d 00:00:00'),'end_time':at.strftime('%Y-%m-%d %H:%M:%S'),
            'order_type':'DESC','order_field':'stat_cost_for_roi2_assist'}
        if task_id is not None:query['filtering']['search_keyword']=exact_id(task_id)
        for raw in pages(api,'control_tasks',query,'task_list','total_number',token):
            tid=exact_id(raw.get('id'))
            if tid in seen or raw.get('task_status') not in statuses or task_status is not None and raw.get('task_status')!=task_status:raise ValueError('任务ID重复或状态与读取范围不一致')
            for key,expected in (('advertiser_id',account['id']),('ad_id',p['plan_id'])):
                if raw.get(key) is not None and exact_id(raw[key])!=expected:raise ValueError('任务所属账户或母计划不一致')
            seen.add(tid)
            values.append({'id':tid,'name':str(raw.get('name',''))[:300],'scene':scene,
                'account_id':account['id'],'plan_id':p['plan_id'],'status':raw['task_status'],
                'smart_bid_type':raw.get('smart_bid_type'),'budget':numeric(raw.get('budget')),
                'duration':numeric(raw.get('duration')),'bid':numeric(raw.get('bid')),
                'roi2_goal':numeric(raw.get('roi2_goal')),'create_time':task_created_at(raw.get('create_time')),
                'external_action_name':raw.get('external_action_name'),'deep_external_action_name':raw.get('deep_external_action_name'),
                'material_type':raw.get('material_type'),'material_list_present':'material_list' in raw,
                'material_ids':[exact_id(m.get('material_id')) for m in (raw.get('material_list') or [])],
                'metrics_window':{'start_at':at.replace(hour=0,minute=0,second=0,microsecond=0).isoformat(),'end_at':at.replace(microsecond=0).isoformat()},
                'metrics':api._account_metrics(raw.get('metrics') or {},TASK_FIELDS)})
    return values


def task_created_at(value):
    # Native control-task timestamps use Beijing wall time without an offset.
    # Normalize only a full timestamp, never a date alone or a guessed clock.
    if not isinstance(value,str) or not re.match(r'^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}',value):return None
    try:return parse_time(value).isoformat()
    except ValueError:return None


def low_spend_tasks(api,account,plan,token,at,tasks,profile):
    cfg=profile['features']['f4']
    if not cfg['enabled'] or cfg.get('pause_rule','cost_equals') not in {'low_spend','either'} or cfg.get('low_spend_hours') is None or cfg.get('low_spend_max') is None:return tasks
    eligible=[t for t in tasks if t['scene']=='MATERIAL_ADD_BUDGET' and t.get('create_time') and parse_time(t['create_time'])<=at-timedelta(hours=cfg['low_spend_hours'])]
    if not eligible:return tasks
    end=at.replace(microsecond=0);day_start=at.replace(hour=0,minute=0,second=0,microsecond=0);pending=[]
    for t in eligible:
        w=t.get('metrics_window',{})
        # A native daily window contains the entire life of a task created
        # today. This is not valid for any task created before midnight.
        covered=(parse_time(t['create_time'])>=day_start and w.get('start_at')==day_start.isoformat() and w.get('end_at')==end.isoformat())
        if covered and t['metrics'].get('spend') is not None:
            t['metrics']['lifetime_spend']=t['metrics']['spend']
            t['lifetime_spend_window']={'start_at':day_start.isoformat(),'end_at':end.isoformat(),'source':'native_daily_window_covers_creation'}
        else:pending.append(t)
    if not pending:return tasks
    eligible=pending
    start=min(parse_time(t['create_time']) for t in eligible).replace(hour=0,minute=0,second=0,microsecond=0)
    query={'advertiser_id':int(account['id']),'ad_id':int(plan['plan_id']),'marketing_goal':'LIVE_PROM_GOODS',
        'scene':'MATERIAL_ADD_BUDGET','fields':['stat_cost_for_roi2_assist'],'filtering':{'task_status':'PROCESSING'},
        'start_time':start.strftime('%Y-%m-%d %H:%M:%S'),'end_time':end.strftime('%Y-%m-%d %H:%M:%S')}
    try:
        rows=pages(api,'control_tasks',query,'task_list','total_number',token);observed={}
        for row in rows:
            tid=exact_id(row.get('id'))
            if tid in observed:raise ValueError('累计消耗读取返回重复任务ID')
            observed[tid]=row
        for t in eligible:
            row=observed.get(t['id'])
            if not row or row.get('task_status')!='PROCESSING' or task_created_at(row.get('create_time'))!=t['create_time']:continue
            spend=api._account_metrics(row.get('metrics') or {},{'stat_cost_for_roi2_assist':'lifetime_spend'})['lifetime_spend']
            if spend is None:continue
            t['metrics']['lifetime_spend']=spend
            t['lifetime_spend_window']={'start_at':start.isoformat(),'end_at':end.isoformat()}
    except ValueError as exc:
        if getattr(exc,'reason',None) in {'rate_limited','service_busy'}:raise
        for t in eligible:t['lifetime_spend_error']=str(exc)[:200]
    return tasks


def material_identity(api, account, p, mid, start, end, token, *, cached=False):
    if cached and hasattr(api,'metadata'):
        return api.metadata(('material_identity',account['id'],p['plan_id'],p['delivery_entry'],mid,start,end),
            token,lambda:material_identity(api,account,p,mid,start,end,token),ttl=900)
    query={'advertiser_id':int(account['id']),'ad_id':int(p['plan_id']),
        'filtering':{'material_type':'VIDEO','material_select_type':'ALL','material_status':'ALL',
            'start_date':start,'end_date':end,'search_keyword':exact_id(mid)},
        'fields':['stat_cost_for_roi2'],'order_field':'stat_cost_for_roi2','order_type':'DESC'}
    rows=pages(api,'plan_materials',query,'ad_material_infos','total_number',token)
    matches=[r for r in rows if exact_id(r.get('material_info',{}).get('video_material',{}).get('material_id'))==mid]
    if len(matches)!=1:raise ValueError('素材不在本计划唯一素材配置中')
    row=matches[0]
    return {'audit_status':row.get('audit_status'),'material_status':row.get('material_status'),
        'identity_checked_at':now_cn().isoformat(),
        'approved':row.get('audit_status')=='PASS','selectable':row.get('material_status')=='DELIVERY_OK'}


def material_query(api, account, p, start, end, at, token):
    aid=account['id'];entry=p['delivery_entry']
    if entry=='uni_promotion':
        return 'plan_materials',{'advertiser_id':int(aid),'ad_id':int(p['plan_id']),
            'filtering':{'material_type':'VIDEO','material_select_type':'ALL','material_status':'ALL','start_date':start,'end_date':end},
            'fields':['stat_cost_for_roi2','total_prepay_and_pay_order_roi2','total_pay_order_count_for_roi2'],
            'order_field':'stat_cost_for_roi2','order_type':'DESC'},None
    if account['scene']!='live':raise ValueError('商品乘方素材报表与计划的唯一关联尚未核验')
    if len(p['anchors'])!=1:raise ValueError('需有唯一抖音号关联直播计划')
    uid=p['anchors'][0]['id']
    related=[v for v in api.plan_readings[aid]['plans'] if v['delivery_entry']==entry and any(a['id']==uid for a in v['anchors'])]
    if len(related)!=1:raise ValueError('同一抖音号对应多个计划，素材指标不分摊到计划')
    # The report's internal anchor ID differs from the public UID. Resolve it
    # from the very same advertiser-scoped native row, never names or row order.
    def load_anchor():
        report=api.transport('report_data',{'advertiser_id':int(aid),'data_topic':'OVERALL_ROI_LIVE_AWEME',
            'data_period':'ALL_DATA','dimensions':['anchor_show_id'],'metrics':['stat_cost'],'filters':[],
            'order_by':[{'type':2,'field':'stat_cost'}],'start_time':at.strftime('%Y-%m-%d 00:00:00'),
            'end_time':at.strftime('%Y-%m-%d %H:%M:%S'),'page':1,'page_size':100},token)
        if report.get('page_info',{}).get('total_number')!=len(report.get('rows',[])):raise ValueError('抖音号映射超过本次唯一读取范围')
        rows=[r for r in report['rows'] if exact_id(native(r.get('dimensions',{}).get('anchor_show_id')))==uid]
        if len(rows)!=1:raise ValueError('未取得本计划唯一的抖音号内部ID')
        return exact_id(native(rows[0]['dimensions'].get('anchor_id')))
    anchor=api.metadata(('anchor_mapping',aid,p['plan_id'],entry,uid,at.date().isoformat()),token,load_anchor,ttl=300) if hasattr(api,'metadata') else load_anchor()
    topic='OVERALL_ROI_LIVE_MATERIAL_VIDEO';c=config(api,aid,topic,entry,token)
    dims={d['field']:d for d in c.get('dimensions',[])}
    required={'material_id','anchor_id','roi2_material_video_name','roi2_material_type_v3'}
    if not required<=dims.keys():raise ValueError('官方素材报表身份维度不完整')
    for key,d in dims.items():
        if d.get('filter_config',{}).get('filter_required') and key!='roi2_material_type_v3':raise ValueError('素材报表需要未核验的额外过滤条件')
    if dims['roi2_material_type_v3'].get('filter_config',{}).get('operator')!=7 or not any(v.get('label')=='3' and v.get('value')=='视频' for v in dims['roi2_material_type_v3'].get('filter_config',{}).get('range_values',[])):
        raise ValueError('官方视频类型过滤枚举发生变化')
    mapping=field_mapping(c)
    mapping.update({k:v for k,v in {'basic_stat_cost_for_roi2_v2':'base_spend',
        'additional_delivery_stat_cost_for_roi2_assist':'chase_spend'}.items() if k in {m['field'] for m in c.get('metrics',[])}})
    if not {'spend','roi'}<=set(mapping.values()):raise ValueError('缺少官方整体消耗或整体支付ROI')
    query={'advertiser_id':int(aid),'data_topic':topic,'data_period':'ALL_DATA',
        'dimensions':['material_id','anchor_id','roi2_material_video_name'],'metrics':list(mapping),
        'filters':[{'field':'roi2_material_type_v3','operator':7,'values':['3']},{'field':'anchor_id','operator':7,'values':[anchor]}],
        'start_time':start+' 00:00:00','end_time':at.strftime('%Y-%m-%d %H:%M:%S'),
        'order_by':[{'type':2,'field':next(k for k,v in mapping.items() if v=='spend')}]}
    return 'report_data',query,(mapping,anchor)


def scan(api, account, profile, pid, *, materials=False, progress=None, rule_metrics=False, browser_candidates=False):
    if browser_candidates and not materials:raise ValueError('网页候选模式只用于素材读取')
    at=now_cn();cfg=profile['features']['f5'];start,end=date_range(at,cfg.get('lookback_days',5))
    with api.read_lock:
        if browser_candidates:
            # Candidate reads need the known native account/parent routing,
            # not fresh budgets or bids. The browser independently reads all
            # current configuration before any action can obtain a permit.
            try:p=parent(api,account,pid,require_fresh=False)
            except ValueError:p=fresh_parent(api,account,pid)
            if p.get('configuration_source')!='plan_list':p=fresh_parent(api,account,pid)
        else:p=fresh_parent(api,account,pid)
        with api.lock:token=api._token(api.grant_for_account(account['id']))
        # Candidate metrics must remain available when the task-list endpoint
        # is throttled. The browser independently checks inventory and selected
        # material eligibility before a browser write; this is not API evidence.
        tasks=[] if browser_candidates else active_tasks(api,account,p,token,at)
        if rule_metrics:tasks=low_spend_tasks(api,account,p,token,at,tasks,profile)
        previous=copy.deepcopy(api.workflows.get(account['id']+':'+pid,{}))
        value={**previous,'account_id':account['id'],'plan_id':pid,'delivery_entry':p['delivery_entry'],
            'captured_at':now_cn().isoformat(),'date':at.date().isoformat(),'tasks':tasks,'tasks_complete':not browser_candidates,
            'ad_changes':0}
        if browser_candidates:value['candidate_parent_source']='known_native_routing_browser_rechecks_configuration'
        if not previous or materials:value.update(materials=[],batches=[],notes=[],materials_read=materials)
        if materials:
            if account['scene']!='live':raise ValueError('本轮自动追投只适配直播视频，图文主计划仍可通过API监测')
            method,query,mapped=material_query(api,account,p,start,end,at,token)
            scan_page_size=200 if browser_candidates and method=='report_data' else 100
            active={mid for t in tasks if t['scene']=='MATERIAL_ADD_BUDGET' for mid in t['material_ids']}
            rules=cfg['rules'];selected={i:[] for i in range(len(rules))};seen=set();last_spend=None;total=None;processed=0;unresolved=[]
            excluded=set(getattr(api,'excluded_materials_provider',lambda aid:[])(account['id']))
            if not cfg.get('allow_running_materials',False):
                excluded.update(getattr(api,'recent_chase_materials_provider',lambda aid,pid:[])(account['id'],pid))
            excluded.update(getattr(api,'material_deferrals_provider',lambda aid,pid:[])(account['id'],pid))
            value['excluded_material_count']=len(excluded)
            allowed_time=profile['enabled'] and _in_window(at.strftime('%H:%M'),profile['operation_start'],profile['operation_end'])
            b=profile['broadcast_protection'];allowed_time=allowed_time and not (b['enabled'] and _in_window(at.strftime('%H:%M'),b['start'],b['end']))
            value.update(date_start=start,date_end=end,page_size=scan_page_size,sort='spend_desc',batch_size=cfg.get('batch_size',3),allow_running_materials=cfg.get('allow_running_materials',False))
            thresholds=[r['material_spend_min'] for r in rules if r.get('enabled',True) is not False and r.get('material_spend_min') is not None and r.get('material_roi_min') is not None]
            floor=min(thresholds) if thresholds else None
            page=0
            checked=0
            while True:
                page+=1
                if hasattr(api,'workflow_progress'):
                    with api.lock:api.workflow_progress.setdefault(account['id']+':'+pid,{}).update(page=page,scanned_rows=processed,matched_materials=len({m['id'] for g in selected.values() for m in g}),identities_checked=checked)
                if progress:progress()
                raw=api.transport(method,{**query,'page':page,'page_size':scan_page_size},token)
                count=raw.get('page_info',{}).get('total_number');rows=raw.get('rows' if mapped else 'ad_material_infos')
                if type(count) is not int or count<0 or not isinstance(rows,list):raise ValueError('素材分页结构无效，请重新读取')
                if total is not None and total!=count:raise ValueError(f'素材分页总数变化：上一页{total}条，本页{count}条，请重新扫描')
                total=count
                for row in rows:
                    processed+=1
                    if mapped:
                        mapping,anchor=mapped;dims=row.get('dimensions',{})
                        if exact_id(native(dims.get('anchor_id')))!=anchor:raise ValueError('素材报表混入其他抖音号')
                        raw_mid=native(dims.get('material_id'));name=str(native(dims.get('roi2_material_video_name')) or '')
                        metrics=api._account_metrics({k:native(row.get('metrics',{}).get(k)) for k in mapping},mapping)
                        identity={'approved':False,'selectable':False,'audit_status':None,'material_status':None}
                    else:
                        info=row.get('material_info',{}).get('video_material',{});raw_mid=info.get('material_id');name=str(info.get('title',''))
                        metrics=api._account_metrics(row.get('stats_info',{}),ACCOUNT_FIELDS)
                        identity={'approved':row.get('audit_status')=='PASS','selectable':row.get('material_status')=='DELIVERY_OK',
                            'audit_status':row.get('audit_status'),'material_status':row.get('material_status')}
                    spend=metrics.get('spend');roi=metrics.get('roi')
                    if spend is None or last_spend is not None and spend>last_spend+0.001:raise ValueError('素材消耗缺失或官方排序未生效')
                    last_spend=spend
                    try:mid=exact_id(raw_mid)
                    except ValueError:
                        unresolved.append({'name':name[:300],'reason':'官方报表未提供有效的十进制素材ID，已排除追投','metrics':metrics});continue
                    if mid in seen:raise ValueError('跨页素材ID重复，未准备不完整批次')
                    seen.add(mid)
                    item={'id':mid,'name':name[:300],'metrics':metrics,'running_chase':mid in active,**identity}
                    eligible=[]
                    for i,rule in enumerate(rules):
                        if rule.get('enabled',True) is False or rule.get('material_spend_min') is None or rule.get('material_roi_min') is None:continue
                        if spend>rule['material_spend_min'] and roi is not None and roi>rule['material_roi_min'] and (rule.get('material_spend_max') is None or spend<rule['material_spend_max']):eligible.append(i)
                    if mid not in excluded and eligible and (mid not in active or cfg.get('allow_running_materials',False)):
                        if mapped and not browser_candidates:
                            if progress:progress()
                            item.update(material_identity(api,account,p,mid,start,end,token,cached=True))
                            checked+=1
                            if hasattr(api,'workflow_progress'):
                                with api.lock:api.workflow_progress[account['id']+':'+pid].update(identities_checked=checked,scanned_rows=processed)
                        if item['approved'] and item['selectable'] or mapped and browser_candidates:
                            for i in eligible:
                                selected[i].append(item)
                    value['materials'].append(item)
                value.update(material_total=total,pages_read=page,materials_complete=processed==total,scanned_rows=processed,
                    unresolved_material_rows=unresolved,material_identities_complete=not unresolved)
                if processed>total or not rows and processed!=total:raise ValueError('素材分页记录与总数不一致')
                if processed==total or floor is None or last_spend is not None and last_spend<=floor:break
            value['stop_reason']='complete' if value['materials_complete'] else 'no_threshold' if floor is None else 'spend_threshold'
            value['eligible_range_complete']=True;value['spend_scan_floor']=floor
            from .chase_batching import groups,batch_limits
            assigned=set();remainders=[];low,size=batch_limits(cfg)
            for i,group in selected.items():
                group=[m for m in group if m['id'] not in assigned]
                # Earlier matching rule wins. Never spend twice on the same
                # material in one sweep, even when repeat-running is enabled.
                assigned.update(m['id'] for m in group)
                chunks=list(groups(group,cfg));full=sum(map(len,chunks))
                remainders.extend({'rule_index':i,'material_id':m['id']} for m in group[full:])
                if not full:continue
                rule=rules[i];notes=[]
                if not cfg['enabled'] or not allowed_time:notes.append('本户F5未启用或当前操作时段受限')
                contract=None
                if browser_candidates:notes.append('仅为API素材候选，须由网页按ID核对审核、投放及追投状态后提交')
                native_roi_goals={t['deep_external_action_name'] for t in tasks if t['scene']=='MATERIAL_ADD_BUDGET' and t['smart_bid_type']=='SMART_BID_CUSTOM' and t['roi2_goal'] is not None and t['deep_external_action_name'] in {'支付ROI','净成交ROI'}}
                try:contract=chase_contract(rule,next(iter(native_roi_goals)) if len(native_roi_goals)==1 else None,
                    delivery_entry=p['delivery_entry'],marketing_services_mode=p.get('marketing_services_mode'))
                except ValueError as exc:notes.append(str(exc))
                for chunk in chunks:
                    batch={'account_id':account['id'],'plan_id':pid,'delivery_entry':p['delivery_entry'],'material_ids':[m['id'] for m in chunk],
                        'materials':chunk,'rule_index':i,'rule':copy.deepcopy(rule),'allow_running_materials':cfg.get('allow_running_materials',False),
                        'date_start':start,'date_end':end,'profile_digest':digest(profile),'ready':not notes,'blocked_reasons':notes,
                        'native_contract':contract,'scanned_at':at.isoformat(),'browser_candidate':browser_candidates}
                    batch['key']=digest(batch)[:24];value['batches'].append(batch)
            value['unbatched_materials']=remainders
            value['matched_materials']=len(assigned)
            value['spend_threshold_materials']=sum(m['metrics']['spend']>floor for m in value['materials']) if floor is not None else 0
            value['material_source']='official_api'
            value['browser_candidates']=browser_candidates
            if hasattr(api,'workflow_progress'):
                with api.lock:api.workflow_progress[account['id']+':'+pid].update(scanned_rows=processed,matched_materials=len(assigned))
            value['notes'].append('本轮按消耗降序翻页，扫完消耗门槛以上的范围再按设置数量分批；多条规则重叠时优先前一条，同轮不重复使用素材。配置接口仅核对审核和投放状态。')
            value.update(batch_size_min=low,batch_size_max=size)
            if remainders:value['notes'].append(f'另有{len(remainders)}条符合条件，但不足最少{low}条，暂不创建；每批{low}–{size}条，整批预算保持不变。')
            if unresolved:value['notes'].append(f'另有{len(unresolved)}行官方报表未提供有效素材ID，已明确排除追投；未按名称或行序补ID。')
        value['captured_at']=now_cn().isoformat()
        if materials:value['materials_captured_at']=value['captured_at']
        with api.lock:
            api.workflows[account['id']+':'+pid]=value
            target=api.data_dir/'official-api-workflows.json';temp=target.with_suffix('.tmp')
            temp.write_text(json.dumps(api.workflows,ensure_ascii=False),encoding='utf-8');temp.replace(target)
        return copy.deepcopy(value)
