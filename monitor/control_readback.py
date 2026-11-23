"""Task-list configuration readback, default off and disconnected from serving.

Exhausting a mutable filtered list is not proof of a complete inventory. No
comparison authenticates an execution, a performance clock, or releases IO.
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException

from .accounts import registry_accounts
from .actuation import BASE_URL, amount
from .plan_detail import _NoRedirect
from .plan_readback import _business_data, _clock, _id
from .schema import CN, now_cn


LIST_PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/list/'
SOURCE='https://open.oceanengine.com/labels/12/docs/1824940765838411'
SCENES={'material_chase':'MATERIAL_ADD_BUDGET','one_click_boost':'SMART_BOOST'}
STATUSES={'DELETED','DISABLE','OFFLINE_BALANCE','OFFLINE_BUDGET','OFFLINE_TIME','PROCESSING','ROI2_DISABLE','FROZEN'}


def prepare_task_read_request(account_id, parent_id, task_id, control_type, start, end, settings, *, max_pages=5, page_size=100):
    account_id,parent_id,task_id=map(_id,(account_id,parent_id,task_id))
    if not isinstance(control_type,str) or control_type not in SCENES:
        raise ValueError('当前任务列表读取不支持一键控量或未知工具')
    if type(max_pages) is not int or not 1<=max_pages<=20:
        raise ValueError('本地读取页数上限须为1至20')
    if type(page_size) is not int or page_size not in {10,20,50,100}:raise ValueError('任务页大小须为10、20、50或100')
    start,end=_clock(start).astimezone(CN),_clock(end).astimezone(CN)
    if not timedelta(0)<end-start<=timedelta(days=7):
        raise ValueError('本地读取窗口须为正数且不超过7天')
    rows=registry_accounts(settings)
    matches=[r for r in rows if r['id']==account_id and r['observe']]
    if len(matches)!=1 or matches[0]['scene'] not in {'live','product'}:
        raise ValueError('任务读取须指向唯一且启用的配置账户')
    goal='LIVE_PROM_GOODS' if matches[0]['scene']=='live' else 'VIDEO_PROM_GOODS'
    params={'advertiser_id':account_id,'marketing_goal':goal,'ad_id':parent_id,
            'start_time':start.strftime('%Y-%m-%d %H:%M:%S'),'end_time':end.strftime('%Y-%m-%d %H:%M:%S'),
            'scene':SCENES[control_type],'filtering':json.dumps({'search_keyword':task_id},separators=(',',':')),
            'order_type':'ASC','order_field':'create_time','page_size':page_size}
    identity={'params':params,'max_pages':max_pages}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'account_id':account_id,'parent_plan_id':parent_id,'task_id':task_id,'control_type':control_type,
            'marketing_goal':goal,'scene':SCENES[control_type],'window_start':start.isoformat(),'window_end':end.isoformat(),
            'max_pages':max_pages,'page_size':page_size,'params':params,'digest':digest,'official_source':SOURCE,
            'request_submitted':False,'network_enabled':False,'read_only':True}


def _page(raw, expected_page, page_size=100):
    data=_business_data(raw);rows=data.get('task_list');info=data.get('page_info')
    if not isinstance(rows,list) or not isinstance(info,dict):raise ValueError('任务列表或分页结构缺失')
    values=[info.get(k) for k in ('page','page_size','total_number','total_page')]
    if any(type(v) is not int for v in values):raise ValueError('任务分页须为明确整数')
    page,size,total,pages=values
    if (page!=expected_page or size!=page_size or total<0 or pages<0
            or pages not in ({0,1} if total==0 else {(total+size-1)//size})
            or page>max(1,pages) or len(rows)!=min(size,max(0,total-(page-1)*size))):
        raise ValueError('任务分页序号、总数或行数不一致')
    return rows,total,pages


def compare_supplied_task_pages(raw_pages, query_context, attempt, now):
    keys={'account_id','parent_plan_id','marketing_goal','scene','task_id','window_start','window_end',
          'request_started_at','received_at','max_pages','page_size'}
    if not isinstance(query_context,dict) or set(query_context)!=keys:
        raise ValueError('任务核对须提供明确且最小的查询上下文')
    if not isinstance(attempt,dict) or not isinstance(attempt.get('context'),dict):raise ValueError('缺少任务提交证据')
    ctx=attempt['context'];q=query_context
    if ctx.get('scope')!='control':raise ValueError('任务列表不能核对计划提交')
    for field,other in (('account_id','account_id'),('parent_plan_id','parent_plan_id'),('task_id','entity_id')):
        if _id(q[field])!=ctx.get(other):raise ValueError('任务查询与原提交对象不一致')
    if (not isinstance(ctx.get('control_type'),str) or ctx['control_type'] not in SCENES
            or q['scene']!=SCENES[ctx['control_type']] or not isinstance(q['marketing_goal'],str) or q['marketing_goal']!=ctx.get('marketing_goal')
            or q['marketing_goal'] not in {'LIVE_PROM_GOODS','VIDEO_PROM_GOODS'}):
        raise ValueError('任务查询场景或营销目标与原提交不一致')
    now,started,received,submitted=map(_clock,(now,q['request_started_at'],q['received_at'],attempt.get('created_at')))
    start,end=map(_clock,(q['window_start'],q['window_end']))
    if (not submitted<=started<=received<=now or not timedelta(0)<end-start<=timedelta(days=7)
            or end>started or (now-received).total_seconds()>600 or (received-started).total_seconds()>600):
        raise ValueError('任务核对的时间顺序、查询窗口或时效不符合要求')
    if type(q['max_pages']) is not int or not 1<=q['max_pages']<=20:
        raise ValueError('本地页数上限无效')
    if type(q['page_size']) is not int or q['page_size'] not in {10,20,50,100}:raise ValueError('任务查询页大小无效')
    if not isinstance(raw_pages,list) or not 1<=len(raw_pages)<=q['max_pages']:
        raise ValueError('须提供从第一页开始的有界连续任务响应')
    tasks={};first_total=first_pages=None
    for page,raw in enumerate(raw_pages,1):
        rows,total,pages=_page(raw,page,q['page_size'])
        if first_total is None:first_total,first_pages=total,pages
        if (total,pages)!=(first_total,first_pages):raise ValueError('读取期间任务总数变化，不能拼接分页')
        for row in rows:
            if not isinstance(row,dict):raise ValueError('任务行结构无效')
            task_id=_id(row.get('id'))
            if task_id in tasks:raise ValueError('分页含重复任务，不能挑选有利值')
            tasks[task_id]=row
    exhausted=len(raw_pages)==max(1,first_pages)
    normalized={**q,'account_id':_id(q['account_id']),'task_id':_id(q['task_id']),'parent_plan_id':_id(q['parent_plan_id']),
                'window_start':start.astimezone(CN).isoformat(),'window_end':end.astimezone(CN).isoformat(),
                'request_started_at':started.isoformat(),'received_at':received.isoformat()}
    result={'account_id':normalized['account_id'],'entity_id':normalized['task_id'],'parent_plan_id':normalized['parent_plan_id'],
            'action':ctx.get('action'),'status':'inconclusive','expected':None,'observed':None,'observed_task_status':None,
            'blockers':[],'status_warnings':[],'query_context':normalized,'source':'supplied_control_task_pages','api_path':LIST_PATH,
            'contract_source':SOURCE,'contract_verified_at':'2026-10-23','checked_at':now.isoformat(),
            'pages_received':len(raw_pages),'reported_total_pages':first_pages,'reported_total_number':first_total,
            'pagination_exhausted':exhausted,'inventory_complete':False,
            'query_timezone_basis':'local_Asia_Shanghai_not_platform_confirmation',
            'provenance_verified':False,'platform_state_observed':False,'execution_confirmed':False,
            'can_release_hold':False,'automatic_retry':False}
    result['response_digest']=hashlib.sha256(json.dumps([hashlib.sha256(r.encode('utf-8')).hexdigest() for r in raw_pages],separators=(',',':')).encode()).hexdigest()
    errors=result['blockers'];row=tasks.get(normalized['task_id'])
    if not exhausted:errors.append('尚未读取全部报告页，不能选择已见记录确认目标值')
    if row is None:
        errors.append('筛选/时段结果未包含准确任务ID，不能推断不存在或提交失败');return result
    state=row.get('task_status')
    if isinstance(state,str) and state in STATUSES:result['observed_task_status']=state
    else:result['status_warnings'].append('返回任务状态不在当前已核验枚举内，未作运行状态推断')
    if ctx.get('action')=='control_budget_up':
        try:
            before,expected,observed=amount(ctx.get('before')),amount(ctx.get('after')),amount(row.get('budget'))
            result.update(expected=format(expected,'.2f'),observed=format(observed,'.2f'))
            if not 0<(expected-before)/before<=Decimal('.5'):
                errors.append('原任务预算追加方向或幅度不一致')
        except (ValueError,DecimalException):errors.append('原记录或返回预算不是明确的两位小数正数')
        if not errors:result['status']='matches_requested_value' if result['expected']==result['observed'] else 'differs_requested_value'
    elif ctx.get('action')=='pause_control':
        result['expected']='PAUSE';result['observed']=result['observed_task_status']
        if ctx.get('before') is not True or ctx.get('after') is not False:errors.append('原暂停记录的状态方向不明确')
        errors.append('当前官方状态表未定义PAUSE的读回映射，不能将其他非投放状态等同为按请求暂停')
    else:errors.append('当前列表核对仅支持已有任务预算追加或暂停记录')
    return result


@dataclass(frozen=True)
class ControlTaskReadPermit:
    account_id:str
    request_digest:str
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str


def _get_task_page(params,page,token,timeout):
    if type(page) is not int or not 1<=page<=20:raise ValueError('本地页码无效')
    allowed={'advertiser_id','marketing_goal','ad_id','start_time','end_time','scene','filtering','order_type','order_field','page_size'}
    if not isinstance(params,dict) or set(params)!=allowed:raise ValueError('任务查询字段不符合最小合同')
    url=BASE_URL+LIST_PATH+'?'+urllib.parse.urlencode({**params,'page':page})
    request=urllib.request.Request(url,headers={'Access-Token':token,'Cache-Control':'no-cache'},method='GET')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    with opener.open(request,timeout=timeout) as response:
        if response.geturl()!=url:raise ValueError('读取响应地址变化')
        payload=response.read(1024*1024+1)
        if len(payload)>1024*1024:raise ValueError('任务响应超过大小限制')
        return payload.decode('utf-8')


class ControlTaskReadClient:
    def __init__(self,*,enable_network=False,transport=None,clock=None):
        if transport is not None and not callable(transport):raise ValueError('读取传输器无效')
        if clock is not None and not callable(clock):raise ValueError('读取时钟无效')
        self.enable_network=enable_network is True
        self.transport=_get_task_page if transport is None else transport
        self.custom_transport=transport is not None;self.clock=now_cn if clock is None else clock

    def review_attempt(self,ledger,attempt_id,settings,permit,token_provider,window_start,window_end,*,max_pages=5,page_size=100):
        if not self.enable_network:raise PermissionError('任务读取适配器未启用，观察服务没有该入口')
        started=_clock(self.clock())
        if (not isinstance(permit,ControlTaskReadPermit) or not isinstance(permit.issued_at,datetime)
                or not isinstance(permit.expires_at,datetime) or permit.issued_at.tzinfo is None
                or permit.expires_at.tzinfo is None or not permit.issued_at<=started<permit.expires_at
                or not timedelta(0)<permit.expires_at-permit.issued_at<=timedelta(minutes=5)
                or not isinstance(permit.authorization_reference,str) or not permit.authorization_reference.strip()):
            raise PermissionError('需要本次有界分页读取的有效授权记录')
        with ledger.connect() as db:row=db.execute('SELECT created_at,context_json FROM submission_attempts WHERE id=?',(attempt_id,)).fetchone()
        if row is None:raise ValueError('找不到任务提交记录')
        try:
            ctx=json.loads(row[1] or '{}')
            if not isinstance(ctx,dict) or ctx.get('scope')!='control' or _clock(row[0])>started:raise ValueError
            request=prepare_task_read_request(ctx['account_id'],ctx['parent_plan_id'],ctx['entity_id'],ctx['control_type'],window_start,window_end,settings,max_pages=max_pages,page_size=page_size)
            if _clock(request['window_end'])>started:raise ValueError
        except (ValueError,TypeError,KeyError):raise ValueError('任务记录、查询窗口或当前账户范围无效') from None
        if permit.account_id!=request['account_id'] or permit.request_digest!=request['digest']:
            raise PermissionError('任务读取账户或查询摘要与授权不符')
        token=token_provider()
        if not isinstance(token,str) or not 1<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('开放平台凭据格式无效')
        pages=[];first_io=None;total_pages=None
        try:
            for page in range(1,max_pages+1):
                at=_clock(self.clock())
                if not started<=at<permit.expires_at:raise ValueError('读取许可过期或时钟回退')
                current=prepare_task_read_request(ctx['account_id'],ctx['parent_plan_id'],ctx['entity_id'],ctx['control_type'],window_start,window_end,settings,max_pages=max_pages,page_size=page_size)
                if current['digest']!=request['digest']:raise ValueError('任务查询范围已改变')
                first_io=at if first_io is None else first_io
                raw=self.transport(dict(request['params']),page,token,10);pages.append(raw)
                _,_,count=_page(raw,page,page_size)
                if total_pages is None:total_pages=count
                if count!=total_pages:raise ValueError('分页总数变化')
                if page>=max(1,total_pages):break
            received=_clock(self.clock())
            current=prepare_task_read_request(ctx['account_id'],ctx['parent_plan_id'],ctx['entity_id'],ctx['control_type'],window_start,window_end,settings,max_pages=max_pages,page_size=page_size)
            if current['digest']!=request['digest']:raise ValueError('读取期间账户配置改变')
            query={k:request[k] for k in ('account_id','parent_plan_id','marketing_goal','scene','task_id','window_start','window_end','max_pages','page_size')}
            query.update(request_started_at=first_io.isoformat(),received_at=received.isoformat())
            comparison=ledger.review_supplied_readback(attempt_id,pages,query,received,response_kind='control_list')
        except Exception:
            return {'ok':False,'read_attempted':True,'comparison_saved':False,'automatic_retry':False,'submission_state_changed':False,
                    'note':'任务分页读取或核对失败，原提交及未知锁定保持不变'}
        return {'ok':True,'read_attempted':True,'comparison_saved':True,'read_only':True,
                'transport_kind':'custom_transport' if self.custom_transport else 'official_https',
                'comparison':comparison,'automatic_retry':False,'submission_state_changed':False}
