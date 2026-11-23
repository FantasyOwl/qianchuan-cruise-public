"""Official plan configuration plus independent effect reports, without writes."""
import copy
import json
from collections import Counter, defaultdict
from .delivery_entry import report_entries, ADLAB_SCENES, ENTRIES
from .schema import now_cn, numeric

MAX_PAGES=50
BID_TYPES=('SMART_BID_CUSTOM','SMART_BID_CONSERVATIVE')


def native(cell):
    if not isinstance(cell,dict):return cell
    if 'Value' not in cell:raise ValueError('报表指标没有原生Value，不解析格式化文本')
    return cell['Value']


def pages(api,name,query,list_key,total_key,token):
    """Read a stable, bounded page sequence; never label a truncated read complete."""
    values=[];total=None
    for page in range(1,MAX_PAGES+1):
        result=api.transport(name,{**query,'page':page,'page_size':100},token)
        rows=result.get(list_key);info=result.get('page_info',{})
        if not isinstance(rows,list) or not isinstance(info,dict):raise ValueError('官方分页结构无效')
        count=info.get(total_key)
        # Observed native control-task empty response (2026-10-28): the
        # first-page task_list is explicitly [] and only page/page_size are
        # returned. This is not permission to infer totals for nonempty rows,
        # later pages, other endpoints, or a null/missing list.
        if name=='control_tasks' and list_key=='task_list' and total_key=='total_number' and page==1 and rows==[] and info=={'page':1,'page_size':100}:return []
        if type(count) is not int or count<0:raise ValueError('官方分页缺少准确总数')
        if total is None:total=count
        elif count!=total:raise ValueError('读取期间计划或报表总数变化，请重新读取')
        values.extend(rows)
        if len(values)==total:return values
        if len(values)>total or not rows:raise ValueError('官方分页记录与总数不一致')
    raise ValueError('本次读取超过5000条上限，未冒充完整范围')


def config(api,aid,topic,entry,token):
    query={'advertiser_id':int(aid),'data_topics':[topic]}
    if entry=='chengfang':query['data_period']='ALL_DATA'
    def load():
        result=api.transport('report_config',query,token)
        found=[c for c in result.get('custom_config_datas',[]) if isinstance(c,dict) and c.get('data_topic')==topic]
        if len(found)!=1:raise ValueError('官方未返回唯一报表配置')
        return found[0]
    return api.metadata(('report_config',aid,topic,entry),token,load) if hasattr(api,'metadata') else load()


def field_mapping(c):
    from .official_api import ACCOUNT_FIELDS
    available={m.get('field') for m in c.get('metrics',[])}
    mapping={k:v for k,v in ACCOUNT_FIELDS.items() if k!='stat_cost_for_roi2' and k in available}
    spend='stat_cost' if 'stat_cost' in available else 'stat_cost_for_roi2'
    if spend in available:mapping[spend]='spend'
    if not mapping:raise ValueError('报表未提供已核验的消耗或成交指标')
    return mapping


def report(api,aid,topic,entry,dimensions,at,token):
    c=config(api,aid,topic,entry,token)
    fields={d.get('field'):d for d in c.get('dimensions',[])}
    if any(d not in fields or fields[d].get('filter_only') for d in dimensions):
        raise ValueError('本入口未提供准确的计划或抖音号标识维度')
    if any(d.get('filter_config',{}).get('filter_required') for d in fields.values()):
        raise ValueError('报表要求额外过滤条件，未猜测计划身份')
    for field,d in fields.items():
        if d.get('is_required') and not d.get('filter_only') and field not in dimensions:dimensions.append(field)
    mapping=field_mapping(c)
    query={'advertiser_id':int(aid),'data_topic':topic,'dimensions':dimensions,'metrics':list(mapping),
        'filters':[],'order_by':[{'type':2,'field':next((k for k,v in mapping.items() if v=='spend'),next(iter(mapping)))}],
        'start_time':at.strftime('%Y-%m-%d 00:00:00'),'end_time':at.strftime('%Y-%m-%d %H:%M:%S')}
    if entry=='chengfang':query['data_period']='ALL_DATA'
    return pages(api,'report_data',query,'rows','total_number',token),mapping


def plan_record(account,info,rooms,entry,at,source='plan_list'):
    from .official_api import exact_id, ACCOUNT_FIELDS
    deep=info.get('deep_external_action')
    basis='net' if deep=='AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI' else 'overall_payment' if deep=='AD_CONVERT_TYPE_LIVE_PAY_ROI' else 'unknown'
    return {'account_id':exact_id(account['id']),'account_name':account.get('name',account['id']),
        'plan_id':exact_id(info.get('id')),'name':str(info.get('name',''))[:300],
        'delivery_entry':entry,'entry_label':ENTRIES[entry],'scene':account['scene'],
        'smart_bid_type':info.get('smart_bid_type'),'budget':numeric(info.get('budget')),
        'budget_mode':info.get('budget_mode'),'target_roi':numeric(info.get('roi2_goal')),'target_roi_basis':basis,
        'deep_external_action':deep,'opt_status':info.get('opt_status'),'status':info.get('status'),
        'marketing_services_mode':info.get('marketing_services_mode'),
        'create_time':info.get('create_time'),'modify_time':info.get('modify_time'),
        'anchors':[{'id':exact_id(r['anchor_id']),'name':str(r.get('anchor_name',''))[:200]} for r in rooms],
        'configuration_source':source,'entry_identity_source':'plan_list' if source=='plan_list' else 'report_topic',
        'metrics':{target:None for target in ACCOUNT_FIELDS.values()},'metric_status':'not_reported',
        'metrics_source':None,'date':at.date().isoformat(),'captured_at':now_cn().isoformat(),'data_at':None}


def plan_rooms(api,account,info,rooms,entry,goal,token):
    """List anchor_id can be a public Douyin handle, not the numeric UID.

    Resolve that case only from the exact advertiser/plan detail. Never cast
    a handle, search by name, or send it as the author report's aweme_id.
    """
    from .official_api import exact_id
    if not isinstance(rooms,list):raise ValueError('计划抖音号列表结构无效')
    try:
        ids=[exact_id(r.get('anchor_id')) for r in rooms]
        if len(ids)!=len(set(ids)):raise ValueError('计划抖音号ID重复')
        return rooms,None
    except (ValueError,AttributeError):pass
    pid=exact_id(info.get('id'))
    detail=api.transport('plan_detail',{'advertiser_id':int(account['id']),'ad_id':int(pid)},token)
    if exact_id(detail.get('ad_id'))!=pid or detail.get('marketing_goal')!=goal:
        raise ValueError('抖音号UID核验返回的计划或场景不一致')
    if detail.get('advertiser_id') is not None and exact_id(detail['advertiser_id'])!=account['id']:
        raise ValueError('抖音号UID核验返回其他账户')
    family=detail.get('adlab_scene')
    if not (type(family) is int and family=={'uni_promotion':0,'chengfang':1}[entry] or isinstance(family,str) and family==ADLAB_SCENES[entry]):
        raise ValueError('抖音号UID核验返回的投放入口不一致或未明确')
    resolved=detail.get('room_info')
    try:
        if not isinstance(resolved,list) or not resolved:raise ValueError()
        ids=[exact_id(r.get('anchor_id')) for r in resolved]
        if len(ids)!=len(set(ids)):raise ValueError()
        known={exact_id(r.get('anchor_id')) for r in rooms if isinstance(r,dict) and str(r.get('anchor_id','')).isdigit() and str(r.get('anchor_id',''))!='0'}
        if not known<=set(ids):raise ValueError()
    except (ValueError,AttributeError):raise ValueError('计划列表未返回数字UID，详情也未能核验完整且唯一的抖音号UID') from None
    return resolved,{'source':'exact_plan_detail','plan_id':pid,'captured_at':now_cn().isoformat()}


def report_plan(api,account,pid,entry,goal,at,token):
    """Resolve IDs omitted by the ordinary list (e.g. full-shop managed plans).

    The exact advertiser-scoped detail request verifies the ID and marketing
    goal. Report topic supplies the metric family; an undocumented numeric
    adlab_scene is retained as evidence, never guessed into an execution enum.
    """
    from .official_api import exact_id
    detail=api.transport('plan_detail',{'advertiser_id':int(account['id']),'ad_id':int(pid)},token)
    if exact_id(detail.get('ad_id'))!=pid or detail.get('marketing_goal')!=goal:
        raise ValueError('补充计划详情与本户报表ID或营销目标不一致')
    if detail.get('advertiser_id') is not None and exact_id(detail['advertiser_id'])!=account['id']:
        raise ValueError('补充计划详情属于其他账户')
    settings=detail.get('delivery_setting')
    if not isinstance(settings,dict) or settings.get('smart_bid_type') not in BID_TYPES:
        raise ValueError('补充计划详情缺少已核验的投放设置')
    family=detail.get('adlab_scene')
    if isinstance(family,str) and family!=ADLAB_SCENES[entry]:raise ValueError('补充计划详情与报表入口不一致')
    info={**settings,**{k:detail.get(k) for k in ('name','opt_status','status','create_time','modify_time')},'id':pid}
    plan=plan_record(account,info,detail.get('room_info',[]),entry,at,'report_and_detail')
    plan['native_adlab_scene']=family
    plan['full_shop_managed']=detail.get('uni_prom_shop_id') is not None
    return plan


def plan_scope(api,query,gid,token,day,*,background=False):
    """Reuse only proven empty discovery scopes; never cache metrics or errors.

    The key includes every selection field, excluding legacy metric dates.
    Manual refreshes and all write preflights always bypass this cache.
    """
    key=json.dumps([gid,{k:v for k,v in query.items() if k not in {'start_time','end_time'}}],sort_keys=True,separators=(',',':'))
    cached=getattr(api,'empty_plan_scopes',{}).get(key,{})
    if (background and isinstance(cached,dict) and cached.get('date')==day
            and type(cached.get('at')) in (int,float) and 0<=api.clock()-cached['at']<900):
        api.empty_plan_cache_hits+=1
        return [],{'empty':True,'reused':True,'observed_at':cached['at']}
    rows=pages(api,'plans',query,'ad_list','total_num',token)
    if hasattr(api,'empty_plan_scopes'):
        with api.lock:
            if not rows:api.empty_plan_scopes[key]={'at':api.clock(),'date':day}
            else:api.empty_plan_scopes.pop(key,None)
            if len(api.empty_plan_scopes)>1024:
                api.empty_plan_scopes={k:v for k,v in api.empty_plan_scopes.items() if isinstance(v,dict) and v.get('date')==day and type(v.get('at')) in (int,float) and 0<=api.clock()-v['at']<900}
            api._save_empty_plan_scopes()
    return rows,{'empty':not rows,'reused':False,'observed_at':api.clock()}


def read_plans(api,account,gid,*,configuration=None,background=False):
    from .official_api import exact_id, ACCOUNT_FIELDS
    aid=exact_id(account['id']);at=now_cn();goal={'live':'LIVE_PROM_GOODS','product':'VIDEO_PROM_GOODS'}.get(account['scene'])
    if not goal:raise ValueError('账户场景无效')
    entries=report_entries(account);plans={};entry_results=[];discovery=[]
    if configuration is not None:
        if (not isinstance(configuration,dict) or set(configuration)!={'plan_id','delivery_entry','smart_bid_type'}
                or configuration['delivery_entry'] not in entries or configuration['smart_bid_type'] not in BID_TYPES):
            raise ValueError('实时核验的计划范围无效')
        exact_id(configuration['plan_id'])
        entries=[configuration['delivery_entry']]
    with api.read_lock:
        with api.lock:token=api._token(gid)
        for entry in entries:
            family=ADLAB_SCENES[entry];count=0
            for bid in ([configuration['smart_bid_type']] if configuration else BID_TYPES):
                query={'advertiser_id':int(aid),'marketing_goal':goal,'adlab_scene':family,
                    'start_time':at.strftime('%Y-%m-%d 00:00:00'),'end_time':at.strftime('%Y-%m-%d %H:%M:%S'),
                    'filtering':{'smart_bid_type':bid}}
                # Reports retain spending from deleted product plans. The
                # documented default list excludes them, breaking an exact ID
                # join even when both responses are valid and fully paginated.
                if account['scene']=='product':query['filtering']['status']='ALL_INCLUDE_DELETED'
                rows,evidence=plan_scope(api,query,gid,token,at.date().isoformat(),background=background and configuration is None)
                discovery.append({'delivery_entry':entry,'smart_bid_type':bid,**evidence})
                for raw in rows:
                    if not isinstance(raw,dict) or not isinstance(raw.get('ad_info'),dict):raise ValueError('计划结构无效')
                    info=raw['ad_info'];pid=exact_id(info.get('id'))
                    if info.get('adlab_scene')!=family or info.get('marketing_goal')!=goal or info.get('smart_bid_type')!=bid:
                        raise ValueError('计划身份、入口或出价方式与查询不一致')
                    if pid in plans:raise ValueError('计划列表出现重复身份，未重复累计')
                    rooms,resolution=plan_rooms(api,account,info,raw.get('room_info',[]),entry,goal,token)
                    plans[pid]=plan_record(account,info,rooms,entry,at)
                    if resolution:plans[pid]['anchor_identity']=resolution
                    count+=1
            entry_results.append({'delivery_entry':entry,'entry_label':ENTRIES[entry],'plans':count,'complete':True})

        if configuration is not None:
            selected=[p for p in plans.values() if p['plan_id']==configuration['plan_id']]
            if len(selected)!=1:raise ValueError('实时原生列表未返回本计划的唯一入口与投放方式')
            # This is fresh native CONFIGURATION only. Never replace the full
            # account cache or restamp old metrics as freshly observed effects.
            if hasattr(api,'configuration_readings'):
                with api.lock:
                    api.configuration_readings[aid+':'+configuration['plan_id']]={
                        'grant_id':gid,'configuration':copy.deepcopy(selected[0])}
                    api._save_configurations()
            return {'ok':True,'configuration':copy.deepcopy(selected[0]),'ad_changes':0}

        if plans and hasattr(api,'configuration_readings'):
            # Preserve freshly read native plan configurations even if a later
            # metrics report (e.g. author_report) hits rate limit, without
            # publishing partial metrics into plan_readings.
            with api.lock:
                for pid,cfg in plans.items():
                    if cfg.get('configuration_source')=='plan_list':
                        api.configuration_readings[aid+':'+pid]={'grant_id':gid,'configuration':copy.deepcopy(cfg)}
                api._save_configurations()

        anchor_counts=Counter((p['delivery_entry'],p['smart_bid_type'],a['id']) for p in plans.values() for a in p['anchors'])
        all_anchor_families=defaultdict(set)
        for p in plans.values():
            for anchor in p['anchors']:all_anchor_families[anchor['id']].add(p['delivery_entry'])
        for entry in entries:
            selected=[p for p in plans.values() if p['delivery_entry']==entry]
            if not selected and account['scene']!='product':continue
            if account['scene']=='product':
                topic='OVERALL_ROI_PRODUCT_PROJECT' if entry=='chengfang' else 'SITE_PROMOTION_PRODUCT_AD'
                rows,mapping=report(api,aid,topic,entry,['ad_id'],at,token);seen=set()
                for row in rows:
                    pid=exact_id(native(row.get('dimensions',{}).get('ad_id')))
                    if pid in seen:raise ValueError('同一计划出现多行报表，未求和或平均ROI')
                    seen.add(pid)
                    if pid not in plans:plans[pid]=report_plan(api,account,pid,entry,goal,at,token)
                    if plans[pid]['delivery_entry']!=entry:raise ValueError('同一计划出现在不同入口报表，未混合指标')
                    plans[pid]['metrics']=api._account_metrics({k:native(row.get('metrics',{}).get(k)) for k in mapping},mapping)
                    plans[pid].update(metric_status='observed',metrics_source={'kind':'plan_report','data_topic':topic})
            elif entry=='chengfang':
                topic='OVERALL_ROI_LIVE_AWEME';rows,mapping=report(api,aid,topic,entry,['anchor_show_id'],at,token);by_anchor={}
                for row in rows:
                    anchor=exact_id(native(row.get('dimensions',{}).get('anchor_show_id')))
                    if anchor in by_anchor:raise ValueError('同一抖音号出现多行报表，未平均ROI')
                    by_anchor[anchor]=api._account_metrics({k:native(row.get('metrics',{}).get(k)) for k in mapping},mapping)
                for p in selected:
                    if len(p['anchors'])!=1:continue
                    anchor=p['anchors'][0]['id']
                    if anchor_counts[(entry,p['smart_bid_type'],anchor)]!=1 or sum(v for (e,b,a),v in anchor_counts.items() if e==entry and a==anchor)!=1:
                        p['metric_status']='ambiguous_anchor';continue
                    if anchor in by_anchor:
                        p['metrics']=by_anchor[anchor]
                        p.update(metric_status='observed',metrics_source={'kind':'unique_plan_anchor_report','data_topic':topic,'anchor_id':anchor})
            else:
                mapping={('stat_cost' if k=='stat_cost_for_roi2' else k):v for k,v in ACCOUNT_FIELDS.items()
                    if k not in {'stat_cost_for_overall_roi2','total_prepay_and_pay_settle_overall_roi2_1h'}}
                for p in selected:
                    if len(p['anchors'])!=1:continue
                    anchor=p['anchors'][0]['id']
                    if len(all_anchor_families[anchor])!=1 or anchor_counts[(entry,p['smart_bid_type'],anchor)]!=1:
                        p['metric_status']='ambiguous_anchor';continue
                    query={'advertiser_id':int(aid),'aweme_id':int(anchor),'marketing_goal':goal,
                        'metrics':list(mapping),'start_time':at.strftime('%Y-%m-%d 00:00:00'),
                        'end_time':at.strftime('%Y-%m-%d %H:%M:%S'),
                        'filtering':{'order_platform':'QIANCHUAN','smart_bid_type':p['smart_bid_type']}}
                    values={};empty=None
                    for offset in range(0,len(mapping),6):
                        fields=list(mapping)[offset:offset+6]
                        rows=pages(api,'author_report',{**query,'metrics':fields},'list','total_number',token)
                        if empty is not None and empty!=bool(not rows):raise ValueError('直播指标分组的报表范围不一致')
                        empty=not rows
                        if not rows:continue
                        if len(rows)!=1 or exact_id(rows[0].get('advertiser_id'))!=aid or exact_id(rows[0].get('aweme_id'))!=anchor:
                            raise ValueError('直播报表与本户、本抖音号不一致')
                        values.update({field:rows[0].get(field) for field in fields})
                    if empty:continue
                    p['metrics']=api._account_metrics(values,mapping)
                    p.update(metric_status='observed',metrics_source={'kind':'unique_plan_anchor_report','anchor_id':anchor,'smart_bid_type':p['smart_bid_type']})
        for result in entry_results:
            result['report_discovered_plans']=sum(p['delivery_entry']==result['delivery_entry'] and p['configuration_source']=='report_and_detail' for p in plans.values())
        value={'account_id':aid,'account_name':account.get('name',aid),'date':at.date().isoformat(),
            'captured_at':now_cn().isoformat(),'data_at':None,'grant_id':gid,'entries':entry_results,
            'plans':list(plans.values()),'plan_discovery':discovery,'empty_discovery_ttl_seconds':900,
            'complete':True,'ad_changes':0}
        with api.lock:
            api.plan_readings[aid]=value;api._save_plans()
        return {'ok':True,'result':copy.deepcopy(value),'ad_changes':0}
